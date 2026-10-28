"""Render one prepared Melbourne OBJ with Jacksonville affine cameras in Blender."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


CAMERA_SHADER = r"""
shader affine_camera_simple(
    output point position = point(0.0, 0.0, 0.0),
    output vector direction = vector({DIRECTION}),
    output color throughput = color(1.0)
)
{
    matrix inverse_projection = matrix({INVERSE_PROJECTION});
    position = point(camera_shader_raster_position());
    position[0] = position[0] * 2.0 - 1.0;
    position[1] = position[1] * 2.0 - 1.0;
    position[2] = 0.0;
    position = transform(inverse_projection, position);
}
"""


def blender_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mesh",
        type=Path,
        default=project_root / "data/melbourne/C4-22/C4-22.local.obj",
    )
    parser.add_argument(
        "--affine-models",
        type=Path,
        default=project_root / "EOGS_affine_models/JAX_068/affine_models.json",
    )
    parser.add_argument(
        "--camera-indices",
        default="0,6,13",
        help="comma-separated zero-based indices in affine_models.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=project_root / "output/melbourne_C4-22_poc",
    )
    parser.add_argument("--resolution", type=int, default=192, help="long image edge")
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--inspect-only", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def start_empty_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_obj(path: Path):
    if not path.is_file():
        raise FileNotFoundError(
            f"Prepared OBJ not found: {path}\n"
            "Run: python3 prepare_melbourne_tile.py"
        )
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(
        filepath=str(path),
        forward_axis="NEGATIVE_Y",
        up_axis="Z",
        use_split_objects=False,
        use_split_groups=False,
        validate_meshes=False,
    )
    imported = [obj for obj in bpy.data.objects if obj not in before and obj.type == "MESH"]
    if len(imported) != 1:
        raise RuntimeError(f"Expected one imported mesh, got {len(imported)}")
    imported[0].name = path.stem
    return imported[0]


def force_materials_opaque() -> None:
    # CloudCompare writes `Tr 1`; its meaning is ambiguous across OBJ readers.
    # The source textures are ordinary JPEGs, so force the photomesh opaque.
    for material in bpy.data.materials:
        material.diffuse_color[3] = 1.0
        if not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED" and "Alpha" in node.inputs:
                alpha = node.inputs["Alpha"]
                for link in list(alpha.links):
                    material.node_tree.links.remove(link)
                alpha.default_value = 1.0
                # The aerial textures already contain baked illumination. A
                # small emission component keeps one-digit-sample previews
                # legible, while the diffuse lobe and Sun still add shading.
                base_color = node.inputs.get("Base Color")
                emission_color = node.inputs.get("Emission Color")
                emission_strength = node.inputs.get("Emission Strength")
                if base_color and base_color.links and emission_color:
                    source_socket = base_color.links[0].from_socket
                    material.node_tree.links.new(source_socket, emission_color)
                    emission_strength.default_value = 0.45


def print_material_diagnostics(mesh) -> None:
    material = next((item for item in mesh.data.materials if item is not None), None)
    if material is None:
        print("Material diagnostic: mesh has no material")
        return
    print(f"Material diagnostic: {material.name}, nodes={material.use_nodes}")
    if not material.use_nodes:
        return
    for node in material.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            values = {
                name: node.inputs[name].default_value
                for name in ("Base Color", "Alpha", "Base Weight", "Roughness")
                if name in node.inputs
            }
            links = {
                name: [link.from_node.name for link in node.inputs[name].links]
                for name in ("Base Color", "Alpha")
                if name in node.inputs
            }
            print(f"  Principled values={values} links={links}")
        elif node.type == "TEX_IMAGE":
            print(f"  Image texture: {node.image.filepath if node.image else None}")


def null_direction(matrix: np.ndarray) -> np.ndarray:
    _, singular_values, vh = np.linalg.svd(matrix, full_matrices=True)
    tolerance = singular_values.max() * np.finfo(singular_values.dtype).eps * max(matrix.shape)
    rank = int(np.sum(singular_values > tolerance))
    direction = vh[rank:, :].T.flatten()
    direction /= np.linalg.norm(direction)
    # Affine null spaces have an arbitrary sign. Camera rays must travel down.
    if direction[2] > 0:
        direction *= -1
    return direction


def make_camera_shader(coefficients, intercept, start_z=1.5) -> str:
    matrix = np.asarray(coefficients, dtype=float)
    offset = np.asarray(intercept, dtype=float)
    if matrix.shape == (3, 3) and offset.shape == (3,):
        matrix, offset = matrix[:2, :], offset[:2]
    if matrix.shape != (2, 3) or offset.shape != (2,):
        raise ValueError(f"Unexpected affine camera shapes: {matrix.shape}, {offset.shape}")
    if np.linalg.matrix_rank(matrix) != 2:
        raise ValueError("Affine camera matrix is not full rank")

    direction = null_direction(matrix)
    projection = np.eye(4)
    projection[:2, :3] = matrix
    projection[:2, 3] = offset
    projection[2, 3] = -start_z
    # EOGS image rows increase downward; Blender raster Y increases upward.
    projection[1, :] *= -1
    inverse_values = np.linalg.inv(projection).T.flatten().tolist()

    return CAMERA_SHADER.replace(
        "{DIRECTION}", ", ".join(f"{value:.17g}" for value in direction)
    ).replace(
        "{INVERSE_PROJECTION}",
        ", ".join(f"{value:.17g}" for value in inverse_values),
    )


def create_camera():
    camera_data = bpy.data.cameras.new("AffineCamera")
    camera_object = bpy.data.objects.new("AffineCamera", camera_data)
    bpy.context.collection.objects.link(camera_object)
    camera_object.location = (0.0, 0.0, 0.0)
    camera_object.rotation_euler = (0.0, 0.0, 0.0)
    camera_data.type = "CUSTOM"
    camera_data.custom_mode = "EXTERNAL"
    bpy.context.scene.camera = camera_object
    return camera_object


def sun_direction(camera_record) -> Vector:
    azimuth = math.radians(float(camera_record["sun_azimuth"]))
    elevation = math.radians(float(camera_record["sun_elevation"]))
    to_sun = Vector(
        (
            math.sin(azimuth) * math.cos(elevation),
            math.cos(azimuth) * math.cos(elevation),
            math.sin(elevation),
        )
    )
    return -to_sun


def create_sun(camera_record):
    light_data = bpy.data.lights.new("JacksonvilleSun", type="SUN")
    light_data.energy = 3.0
    light_data.angle = math.radians(0.5)
    light_object = bpy.data.objects.new("JacksonvilleSun", light_data)
    bpy.context.collection.objects.link(light_object)
    light_object.rotation_mode = "QUATERNION"
    light_object.rotation_quaternion = sun_direction(camera_record).to_track_quat("-Z", "Y")
    return light_object


def update_sun(light_object, camera_record) -> None:
    light_object.rotation_quaternion = sun_direction(camera_record).to_track_quat("-Z", "Y")


def configure_render(samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.shading_system = True
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 2
    scene.cycles.diffuse_bounces = 1
    scene.cycles.glossy_bounces = 1
    scene.cycles.transparent_max_bounces = 0
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.use_persistent_data = True
    if scene.world is None:
        scene.world = bpy.data.worlds.new("MelbourneWorld")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.18, 0.20, 0.24, 1.0)
    background.inputs["Strength"].default_value = 0.35
    scene.view_settings.exposure = 0.5
    scene.view_settings.look = "AgX - Medium High Contrast"


def scaled_resolution(camera_record, long_edge: int) -> tuple[int, int]:
    width, height = int(camera_record["width"]), int(camera_record["height"])
    scale = long_edge / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).rsplit(".", 1)[0]


def main() -> None:
    args = blender_arguments()
    if args.resolution < 16 or args.samples < 1:
        raise ValueError("Resolution must be >= 16 and samples must be >= 1")
    indices = [int(value) for value in args.camera_indices.split(",") if value.strip()]
    cameras = json.loads(args.affine_models.resolve().read_text(encoding="utf-8"))
    selected = [cameras[index] for index in indices]
    tile_name = args.mesh.name.split(".", 1)[0]

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    start_empty_scene()
    mesh = import_obj(args.mesh.resolve())
    force_materials_opaque()
    print_material_diagnostics(mesh)
    if args.inspect_only:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    camera_object = create_camera()
    light_object = create_sun(selected[0])
    configure_render(args.samples)
    print(
        f"Imported {mesh.name}: {len(mesh.data.vertices):,} vertices, "
        f"{len(mesh.data.polygons):,} polygons, {len(mesh.data.materials):,} materials"
    )

    shader_paths = []
    rendered = []
    for index, camera_record in zip(indices, selected):
        if camera_record.get("img") == "Nadir":
            continue
        shader = make_camera_shader(
            camera_record["model"]["coef_"], camera_record["model"]["intercept_"]
        )
        descriptor, shader_name = tempfile.mkstemp(
            prefix=f"melbourne_affine_{index:02d}_", suffix=".osl"
        )
        os.close(descriptor)
        shader_path = Path(shader_name)
        shader_path.write_text(shader, encoding="utf-8")
        shader_paths.append(shader_path)
        camera_object.data.custom_filepath = str(shader_path)
        update_sun(light_object, camera_record)

        width, height = scaled_resolution(camera_record, args.resolution)
        bpy.context.scene.render.resolution_x = width
        bpy.context.scene.render.resolution_y = height
        bpy.context.scene.render.resolution_percentage = 100
        image_name = f"melbourne_{tile_name}_{safe_name(camera_record['img'])}.png"
        image_path = output_dir / image_name
        bpy.context.scene.render.filepath = str(image_path)
        print(f"Rendering camera {index} at {width}x{height}: {image_path}")
        bpy.ops.render.render(write_still=True)
        rendered.append(
            {
                "camera_index": index,
                "source_camera": camera_record["img"],
                "resolution": [width, height],
                "sun_azimuth_degrees": float(camera_record["sun_azimuth"]),
                "sun_elevation_degrees": float(camera_record["sun_elevation"]),
                "output": str(image_path),
            }
        )

    run_metadata = {
        "mesh": str(args.mesh.resolve()),
        "affine_models": str(args.affine_models.resolve()),
        "samples": args.samples,
        "renders": rendered,
    }
    (output_dir / "render_metadata.json").write_text(
        json.dumps(run_metadata, indent=2) + "\n", encoding="utf-8"
    )
    for shader_path in shader_paths:
        shader_path.unlink(missing_ok=True)
    print(f"Finished {len(rendered)} proof-of-concept renders in {output_dir}")
    sys.stdout.flush()
    sys.stderr.flush()
    # The directly invoked snap binary can hang while shutting down in a
    # headless sandbox; all render outputs are flushed before this point.
    os._exit(0)


if __name__ == "__main__":
    main()
