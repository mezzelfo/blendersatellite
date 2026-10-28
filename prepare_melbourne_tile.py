#!/usr/bin/env python3
"""Recenter a georeferenced Melbourne OBJ before importing it into Blender."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def vertex_bounds(path: Path) -> tuple[list[float], list[float], int]:
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    count = 0
    with path.open("r", encoding="utf-8", errors="strict") as source:
        for line in source:
            if not line.startswith("v "):
                continue
            fields = line.split()
            xyz = [float(fields[1]), float(fields[2]), float(fields[3])]
            for axis, value in enumerate(xyz):
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
            count += 1
    if count == 0:
        raise ValueError(f"No vertices found in {path}")
    return minimum, maximum, count


def jacksonville_scale(path: Path) -> float:
    cameras = json.loads(path.read_text(encoding="utf-8"))
    try:
        return float(cameras[0]["model"]["scale"])
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"No camera scale found in {path}") from exc


def write_local_obj(
    source_path: Path,
    output_path: Path,
    center: list[float],
    scale_m: float,
    rotation_degrees: float,
) -> None:
    angle = math.radians(rotation_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    temporary_path = output_path.with_suffix(output_path.suffix + ".part")

    with (
        source_path.open("r", encoding="utf-8", errors="strict") as source,
        temporary_path.open("w", encoding="utf-8", newline="\n") as output,
    ):
        output.write("# Local coordinates prepared by prepare_melbourne_tile.py\n")
        output.write(
            f"# center_m {center[0]:.12f} {center[1]:.12f} {center[2]:.12f}\n"
        )
        output.write(f"# scale_m {scale_m:.12f}\n")
        output.write(f"# rotation_degrees {rotation_degrees:.12f}\n")
        for line in source:
            if line.startswith("v "):
                fields = line.split()
                x = float(fields[1]) - center[0]
                y = float(fields[2]) - center[1]
                z = float(fields[3]) - center[2]
                local_x = (cosine * x - sine * y) / scale_m
                local_y = (sine * x + cosine * y) / scale_m
                local_z = z / scale_m
                suffix = " " + " ".join(fields[4:]) if len(fields) > 4 else ""
                output.write(
                    f"v {local_x:.9f} {local_y:.9f} {local_z:.9f}{suffix}\n"
                )
            elif line.startswith("vn ") and rotation_degrees:
                fields = line.split()
                x, y, z = map(float, fields[1:4])
                local_x = cosine * x - sine * y
                local_y = sine * x + cosine * y
                suffix = " " + " ".join(fields[4:]) if len(fields) > 4 else ""
                output.write(f"vn {local_x:.9f} {local_y:.9f} {z:.9f}{suffix}\n")
            else:
                output.write(line.replace("\r\n", "\n"))

    temporary_path.replace(output_path)


def main() -> int:
    project_root = Path(__file__).resolve().parent
    default_source = project_root / "data/melbourne/C4-22/C4-22.obj"
    default_models = project_root / "EOGS_affine_models/JAX_068/affine_models.json"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--affine-models", type=Path, default=default_models)
    parser.add_argument(
        "--scale-m",
        type=float,
        help="metres per local unit (defaults to the Jacksonville camera scale)",
    )
    parser.add_argument(
        "--rotation-degrees",
        type=float,
        default=0.0,
        help="optional counter-clockwise rotation about the local Z axis",
    )
    args = parser.parse_args()

    source_path = args.source.resolve()
    output_path = (
        args.output.resolve()
        if args.output
        else source_path.with_name(f"{source_path.stem}.local.obj")
    )
    if source_path == output_path:
        parser.error("--output must not overwrite the source OBJ")
    if not source_path.is_file():
        parser.error(f"source OBJ does not exist: {source_path}")

    scale_m = (
        args.scale_m
        if args.scale_m is not None
        else jacksonville_scale(args.affine_models.resolve())
    )
    if scale_m <= 0:
        parser.error("--scale-m must be positive")

    minimum, maximum, vertex_count = vertex_bounds(source_path)
    center = [(low + high) / 2.0 for low, high in zip(minimum, maximum)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_local_obj(source_path, output_path, center, scale_m, args.rotation_degrees)

    metadata = {
        "source_obj": str(source_path),
        "local_obj": str(output_path),
        "source_crs": "EPSG:7855 (GDA2020 / MGA zone 55), Z in AHD metres",
        "source_bounds_m": {"minimum": minimum, "maximum": maximum},
        "vertex_count": vertex_count,
        "center_m": center,
        "rotation_degrees_counter_clockwise": args.rotation_degrees,
        "metres_per_local_unit": scale_m,
        "local_from_source": (
            "local_xy = Rz(rotation) * (source_xy - center_xy) / scale; "
            "local_z = (source_z - center_z) / scale"
        ),
    }
    metadata_path = output_path.with_suffix(".transform.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {vertex_count:,} vertices: {output_path}")
    print(f"Transform metadata: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
