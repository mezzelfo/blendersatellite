"""Headless Blender perspective rendering of exported orbits spacecraft poses."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import subprocess
import importlib.util

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector
import numpy as np

# Load the pinned, NumPy-only helper directly: importing orbits.__init__ inside
# Blender would also import PyTorch, which belongs in the conda environment.
_geometry_path=Path(__file__).resolve().parent/'orbits/src/orbits/camera_geometry.py'
_spec=importlib.util.spec_from_file_location('orbits_camera_geometry',_geometry_path)
camera_geometry=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(camera_geometry)


def configure_gpu(scene,backend,indices):
    preferences=bpy.context.preferences.addons['cycles'].preferences
    preferences.compute_device_type=backend
    preferences.get_devices()
    available=[device for device in preferences.devices if device.type==backend]
    if not available:
        raise RuntimeError(f'No {backend} GPUs found; GPU rendering is required (check driver and sandbox access)')
    selected=list(range(len(available))) if indices=='all' else [int(x) for x in indices.split(',')]
    if not selected or any(i<0 or i>=len(available) for i in selected):
        raise ValueError(f'Invalid GPU indices {indices}; {len(available)} {backend} devices available')
    for device in preferences.devices:device.use=False
    records=[]
    for i in selected:
        device=available[i];device.use=True
        records.append(dict(index=i,name=device.name,id=device.id,backend=device.type))
    scene.cycles.device='GPU'
    # GPU denoising is not supported on every CUDA device (including some
    # Pascal builds). Disable it so this small preview does not add a CPU pass.
    scene.cycles.use_denoising=False
    print(f'Cycles GPU devices: {records}; CPU rendering disabled',flush=True)
    return records


def gpu_process_snapshot():
    try:
        result=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory',
                               '--format=csv,noheader'],capture_output=True,text=True,timeout=10,check=True)
        # Only record this Blender process, rather than unrelated desktop apps.
        return [line for line in result.stdout.splitlines() if f', {os.getpid()},' in line]
    except (OSError,subprocess.SubprocessError) as error:
        return [f'nvidia-smi monitoring unavailable: {error}']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cameras',type=Path,required=True)
    parser.add_argument('--terrain',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--satellites',default='0,1,2,3,4')
    parser.add_argument('--samples',type=int,default=8)
    parser.add_argument('--check-only',action='store_true',help='validate Blender projections without loading terrain or rendering')
    parser.add_argument('--gpu-backend',choices=['CUDA','OPTIX','HIP','ONEAPI','METAL'],default='CUDA')
    parser.add_argument('--gpu-devices',default='all',help='all, or comma-separated indices within the chosen backend')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    manifest=json.loads(args.cameras.read_text())
    if not args.check_only and (any(args.output.glob('satellite_*.png')) or
                               (args.output/'render_report.json').exists()):
        parser.error('--output already contains render artifacts; select a new run directory')
    args.output.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    scene.render.engine='CYCLES'
    gpu_devices=configure_gpu(scene,args.gpu_backend,args.gpu_devices)
    scene.cycles.samples=args.samples
    scene.cycles.use_denoising=False
    scene.render.use_persistent_data=True
    scene.render.resolution_x=512;scene.render.resolution_y=512
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGBA'
    scene.render.film_transparent=True
    scene.view_settings.view_transform='Standard'
    scene.view_settings.look='None'
    material=bpy.data.materials.new('Baked aerial texture colors')
    material.use_nodes=True
    nodes=material.node_tree.nodes;nodes.clear()
    output=nodes.new('ShaderNodeOutputMaterial')
    emission=nodes.new('ShaderNodeEmission')
    color=nodes.new('ShaderNodeVertexColor');color.layer_name='Color'
    material.node_tree.links.new(color.outputs['Color'],emission.inputs['Color'])
    material.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
    # Emission preserves the captured aerial texture illumination. This MVP
    # validates geometry/poses and does not infer illumination from orbit time.
    missing=[];triangles=0
    for tile in ([] if args.check_only else manifest['selected_tiles']):
        path=args.terrain/(tile+'.npz')
        if not path.exists(): missing.append(tile);continue
        with np.load(path) as data:
            vertices=data['vertices'];rgb=data['rgb'].astype(float)/255
            rows,cols=data['shape']
        ids=np.arange(rows*cols).reshape(rows,cols)
        a=ids[:-1,:-1].ravel();b=a+1;c=a+cols;d=c+1
        faces=np.concatenate([np.stack([a,b,d],1),np.stack([a,d,c],1)])
        mesh=bpy.data.meshes.new(tile)
        mesh.from_pydata(vertices.tolist(),[],faces.tolist());mesh.update()
        attribute=mesh.color_attributes.new(name='Color',type='FLOAT_COLOR',domain='POINT')
        # PNG textures are sRGB; Blender FLOAT_COLOR attributes are scene linear.
        linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
        rgba=np.c_[linear,np.ones(len(rgb))].astype(np.float32)
        attribute.data.foreach_set('color',rgba.ravel())
        obj=bpy.data.objects.new(tile,mesh);scene.collection.objects.link(obj)
        obj.data.materials.append(material);triangles+=len(faces)
    if missing: raise RuntimeError(f'Missing {len(missing)} selected terrain caches: {missing}')
    if manifest.get('pointing_mode')=='chief-target':
        chief=next(c for c in manifest['cameras'] if c['role']=='chief')
        target_local,target_ecef=camera_geometry.chief_wgs84_target(chief,
            manifest['origin_ecef_m'],manifest['local_from_ecef'])
        records,target,across=camera_geometry.retarget_cameras(manifest['cameras'],'chief-target',target_local)
        manifest.update(cameras=records,common_target_local_m=target,chief_ground_across_local=across,
            common_target_ecef_m=target_ecef.tolist(),
            target_source='chief boresight intersection with WGS84 ellipsoid at zero ellipsoidal height',
            target_ellipsoid=dict(name='WGS84',semi_major_axis_m=camera_geometry.WGS84_A,
                                  semi_minor_axis_m=camera_geometry.WGS84_B))
        print(f'Common WGS84 target: {target}; chief attitude preserved',flush=True)
    resolved_path=args.output/'camera_poses.json'
    if not args.check_only:
        resolved_path.write_text(json.dumps(manifest,indent=2)+'\n')
    camera_data=bpy.data.cameras.new('HyperScape100 cropped RGB approximation')
    camera=bpy.data.objects.new('SatelliteCamera',camera_data)
    scene.collection.objects.link(camera);scene.camera=camera
    camera_data.type='PERSP';camera_data.lens=580
    camera_data.sensor_fit='HORIZONTAL'
    camera_data.clip_start=1;camera_data.clip_end=1000000
    camera_data.dof.use_dof=False
    camera_data.dof.aperture_fstop=580/95
    results=[]
    selected={int(x) for x in args.satellites.split(',')}
    for record in manifest['cameras']:
        index=record['satellite_id']
        if index not in selected:continue
        basis=Matrix(record['camera_to_local']).to_4x4()
        basis.translation=Vector(record['position_local_m'])
        camera.matrix_world=basis
        camera_data.sensor_width=record['sensor_crop_mm']
        camera_data.sensor_height=record['sensor_crop_mm']
        camera_data.dof.focus_distance=float(np.linalg.norm(np.asarray(record['position_local_m'])-record['ground_center_local_m']))
        bpy.context.view_layer.update()
        # Independently check Blender's own projection against exported corners.
        projected=[world_to_camera_view(scene,camera,Vector(p)) for p in record['footprint_local_m']]
        expected=np.array([[0,0],[1,0],[1,1],[0,1]])
        error=float(np.max(np.abs(np.array([[p.x,p.y] for p in projected])-expected)))
        if error>2e-4:raise RuntimeError(f'Camera {index} footprint reprojection failed: {error}')
        center=Vector(record['ground_center_local_m'])
        # One pixel on the reference plane in image-horizontal direction.
        forward=-np.asarray(record['camera_to_local'])[:,2]
        right=np.asarray(record['camera_to_local'])[:,0]
        projected_across=camera_geometry.unit(camera_geometry.ground_direction(forward,right))
        direction=Vector(projected_across)
        p0=world_to_camera_view(scene,camera,center)
        p1=world_to_camera_view(scene,camera,center+direction*5)
        pixel_step=512*float(np.hypot(p1.x-p0.x,p1.y-p0.y))
        if abs(pixel_step-1)>.002:raise RuntimeError(f'Camera {index} 5 m projection={pixel_step} pixels')
        target_error=None;alignment=None
        if manifest.get('pointing_mode')=='chief-target':
            target_pixel=world_to_camera_view(scene,camera,Vector(manifest['common_target_local_m']))
            target_error=512*float(np.hypot(target_pixel.x-.5,target_pixel.y-.5))
            alignment=float(np.dot(projected_across,manifest['chief_ground_across_local']))
            if target_error>.01 or alignment<1-1e-10:
                raise RuntimeError(f'Camera {index}: target error {target_error}px, across-track alignment {alignment}')
            print(f'Camera {index}: common target error={target_error:.6f}px, ground-across dot={alignment:.12f}',flush=True)
        if args.check_only:
            print(f'Camera {index}: corner error {error:.3g}; 5m={pixel_step:.6f}px',flush=True)
            continue
        path=args.output/f'satellite_{index:02d}.png'
        scene.render.filepath=str(path)
        print(f'Rendering satellite {index}: {triangles:,} terrain triangles; corner error {error:.2g}; 5m={pixel_step:.6f}px',flush=True)
        start=time.monotonic();bpy.ops.render.render(write_still=True)
        saved=bpy.data.images.load(str(path.resolve()),check_existing=False)
        pixels=np.empty(512*512*4,dtype=np.float32)
        saved.pixels.foreach_get(pixels);pixels=pixels.reshape(-1,4)
        coverage=float(np.mean(pixels[:,3]>.5))
        bpy.data.images.remove(saved)
        print(f'Satellite {index}: geometry coverage {coverage:.2%}',flush=True)
        results.append(dict(satellite_id=index,image=str(path.resolve()),render_seconds=time.monotonic()-start,
                            corner_reprojection_error=error,pixels_per_5m=pixel_step,
                            common_target_error_pixels=target_error,ground_across_alignment_with_chief=alignment,
                            gpu_process_snapshot=gpu_process_snapshot(),
                            geometry_coverage_fraction=coverage))
    if args.check_only:
        print('All Blender camera projection checks passed',flush=True)
        sys.stdout.flush();os._exit(0)
    (args.output/'render_report.json').write_text(json.dumps(dict(cameras=str(args.cameras.resolve()),
        resolved_cameras=str(resolved_path.resolve()),pointing_mode=manifest.get('pointing_mode','nadir'),
        common_target_local_m=manifest.get('common_target_local_m'),target_source=manifest.get('target_source'),
        terrain_tiles=len(manifest['selected_tiles']),triangles=triangles,samples=args.samples,
        compute_device=scene.cycles.device,gpu_backend=args.gpu_backend,gpu_devices=gpu_devices,
        denoising=False,
        illumination='Source baked aerial texture colors, emission shader',renders=results),indent=2)+'\n')
    print('Rendering complete',flush=True)
    # Avoid the installed snap build's headless shutdown hang after flushing files.
    sys.stdout.flush();sys.stderr.flush();os._exit(0)


if __name__=='__main__':main()
