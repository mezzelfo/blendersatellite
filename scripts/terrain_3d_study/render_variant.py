"""Blender script of the step-20 study: render one variant of one block from the study views.

  blender --background --factory-startup --gpu-backend vulkan --gpu-device 1 --gpu-device-no-fallback \\
      --python render_variant.py -- --inputs STUDY/render_inputs/CBD/GRID --output DIR \\
      --views t00,t05_N,... [--vertical-pass]

Same engine settings and material as render_swarm.py with --engine EEVEE: 8 TAA samples, shadows,
ray tracing, fast GI, volumetric shadows, overscan and motion blur off, no lights, one emission
shader, transparent film, Standard view transform (sRGB PNG, RGBA 8 bit). Colour source:
  - vcolor variants (GRID, WALL): per-vertex sRGB colour converted to scene linear, as render_swarm.py;
  - texture variants (REF, DEC_r): one material per photomesh material, Image Texture (sRGB, Linear
    interpolation with mipmaps, Extend) -> Emission.
--vertical-pass renders every view a second time with a material override whose emission is 1 on
faces with |n_z| < 0.3 and 0 elsewhere (<view>_vert.png).
Camera per view from <block>/frame.json (prepare_render.py); only the view's crop window is rendered
(Blender border with crop, like render_swarm.py --crop). Writes <output>/<view>.png and report.json.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector


def srgb_to_linear(c):
    return np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)


def emission_material(name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes; nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    emission = nodes.new('ShaderNodeEmission')
    material.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
    return material, nodes, emission


def build_mesh(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set('co', vertices.ravel())
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set('vertex_index', faces.ravel())
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set('loop_start', np.arange(0, faces.size, 3, dtype=np.int32))
    mesh.update(calc_edges=False)
    return mesh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--views', required=True)
    parser.add_argument('--vertical-pass', action='store_true')
    parser.add_argument('--samples', type=int, default=8)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    t0 = time.monotonic()
    manifest = json.loads((args.inputs / 'manifest.json').read_text())
    frame = json.loads((args.inputs.parent / 'frame.json').read_text())
    views = [frame['views'][v] | dict(name=v) for v in args.views.split(',')]
    args.output.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.taa_render_samples = args.samples
    for key, value in dict(use_shadows=False, use_raytracing=False, use_fast_gi=False,
                           use_volumetric_shadows=False, use_overscan=False).items():
        setattr(scene.eevee, key, value)
    scene.render.use_motion_blur = False
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '8'
    scene.render.film_transparent = True
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'

    vmat, vnodes, vemission = emission_material('Baked aerial texture colors')
    color = vnodes.new('ShaderNodeVertexColor'); color.layer_name = 'Color'
    vmat.node_tree.links.new(color.outputs['Color'], vemission.inputs['Color'])
    mask, mnodes, memission = emission_material('Near-vertical mask')
    attr = mnodes.new('ShaderNodeAttribute'); attr.attribute_name = 'vert'
    mask.node_tree.links.new(attr.outputs['Color'], memission.inputs['Color'])

    tex_template, tnodes, temission = emission_material('Texture template')
    tex = tnodes.new('ShaderNodeTexImage'); tex.name = 'Texture'
    tex.interpolation = 'Linear'; tex.extension = 'EXTEND'
    tex_template.node_tree.links.new(tex.outputs['Color'], temission.inputs['Color'])
    grey, _, gemission = emission_material('No texture')
    gemission.inputs['Color'].default_value = (0.2, 0.2, 0.2, 1)

    triangles = images = materials = 0
    t_images = 0.
    for tile in manifest['tiles']:
        with np.load(args.inputs / f'{tile}.npz') as data:
            d = {k: data[k] for k in data.files}
        mesh = build_mesh(tile, d['vertices'], d['faces'])
        if str(d['kind']) == 'vcolor':
            rgb = srgb_to_linear(d['colors'].astype(np.float64) / 255)
            attribute = mesh.color_attributes.new(name='Color', type='FLOAT_COLOR', domain='POINT')
            attribute.data.foreach_set('color', np.c_[rgb, np.ones(len(rgb))].astype(np.float32).ravel())
            mesh.materials.append(vmat)
        else:
            uv = mesh.uv_layers.new(name='UVMap')
            uv.data.foreach_set('uv', d['uv'].ravel())
            used, slot = np.unique(d['material'], return_inverse=True)
            ti = time.monotonic()
            for m in used:
                path = str(d['textures'][m])
                # Every node-API call scans all node trees of the file, so building tens of thousands
                # of materials node by node is quadratic; copying one template and setting only its
                # image costs one such update per material. check_existing=True would also scan every
                # loaded image; every path here is distinct, so it is not needed.
                if path:
                    mat = tex_template.copy(); mat.name = f'{tile}_{m}'
                    mat.node_tree.nodes['Texture'].image = bpy.data.images.load(path, check_existing=False)
                    images += 1
                else:
                    mat = grey
                mesh.materials.append(mat)
                materials += 1
            t_images += time.monotonic() - ti
            mesh.polygons.foreach_set('material_index', slot.astype(np.int32))
            if 'vertical' in d:
                flag = np.repeat(d['vertical'].astype(np.float32), 3)
                vert = mesh.color_attributes.new(name='vert', type='FLOAT_COLOR', domain='CORNER')
                vert.data.foreach_set('color', np.stack([flag, flag, flag, np.ones_like(flag)], 1).ravel())
        obj = bpy.data.objects.new(tile, mesh)
        scene.collection.objects.link(obj)
        triangles += len(d['faces'])
        print(f'tile {tile}: {len(d["faces"]):,} faces, {time.monotonic() - t0:.1f} s', flush=True)
        del d

    camera_data = bpy.data.cameras.new('HyperScape100')
    camera = bpy.data.objects.new('Camera', camera_data)
    scene.collection.objects.link(camera); scene.camera = camera
    camera_data.type = 'PERSP'; camera_data.sensor_fit = 'HORIZONTAL'; camera_data.dof.use_dof = False
    setup_seconds = time.monotonic() - t0
    print(f'setup {setup_seconds:.1f} s, {triangles:,} triangles, {materials} materials, {images} images', flush=True)

    records = []
    for view in views:
        basis = Matrix(view['camera_to_local']).to_4x4()
        basis.translation = Vector(view['position_local_m'])
        camera.matrix_world = basis
        camera_data.lens = view['lens_mm']
        camera_data.sensor_width = view['sensor_width_mm']; camera_data.sensor_height = view['sensor_height_mm']
        camera_data.clip_start = view['slant_range_m'] - 10e3; camera_data.clip_end = view['slant_range_m'] + 10e3
        width, height = view['resolution']
        scene.render.resolution_x = width; scene.render.resolution_y = height
        c = view['crop']
        scene.render.use_border = True; scene.render.use_crop_to_border = True
        scene.render.border_min_x = c['x0'] / width; scene.render.border_max_x = c['x1'] / width
        scene.render.border_min_y = (height - c['y1']) / height; scene.render.border_max_y = (height - c['y0']) / height
        passes = [('', None)] + ([('_vert', mask)] if args.vertical_pass else [])
        for suffix, override in passes:
            bpy.context.view_layer.material_override = override
            path = args.output / f"{view['name']}{suffix}.png"
            start = time.monotonic()
            bpy.ops.render.render()
            bpy.data.images['Render Result'].save_render(str(path))
            records.append(dict(view=view['name'], image=path.name, seconds=time.monotonic() - start))
            print(f'{path.name}: {records[-1]["seconds"]:.1f} s', flush=True)
        bpy.context.view_layer.material_override = None
    import gpu
    report = dict(inputs=str(args.inputs), variant=manifest['variant'], block=manifest['block'],
                  triangles=triangles, texture_megapixels=manifest['texture_megapixels'], materials=materials,
                  images=images, setup_seconds=setup_seconds, image_setup_seconds=t_images, renders=records,
                  total_seconds=time.monotonic() - t0, blender_version=bpy.app.version_string,
                  gpu=dict(backend=gpu.platform.backend_type_get(), renderer=gpu.platform.renderer_get(),
                           version=gpu.platform.version_get()), samples=args.samples)
    (args.output / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('done', flush=True)
    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)


if __name__ == '__main__':
    main()
