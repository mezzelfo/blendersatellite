"""Render inputs of the step-20 study: block frame, camera views, and variants in local ENU.

  python -I prepare_render.py --study STUDY --block CBD frame
  python -I prepare_render.py --study STUDY --block CBD variant --name REF --ref-factor 4
  python -I prepare_render.py --study STUDY --block CBD variant --name DEC_16
  python -I prepare_render.py --study STUDY --block CBD variant --name GRID   (or WALL)

Frame (same chain as terrain_to_render.py / melbourne_overpass.py): origin = block centre in
EPSG:7855 at height 0 -> EPSG:7844 -> ECEF (EPSG:4979 -> 4978, height used as ellipsoidal);
local axes = east, north, up at the origin's longitude and latitude.

Views: the camera of the pipeline (lens 580 mm, sensor 22.528 x 16.896 mm, 4096 x 3072 px) on a
500 km circular orbit over a spherical Earth of radius R = WGS84 mean radius (2a + b) / 3, seen from
the target at zenith angle theta: slant range d solves (R + 500 km)^2 = R^2 + d^2 + 2 R d cos(theta).
Target = block centre at the median GRID height of the block. The camera lies at target +
d (sin(theta) a + cos(theta) up), a = unit vector towards the azimuth (N = +north: camera north of
the block looking south; E = +east), looks at the target, image up = projection of north
(image columns ~ east). Views: theta 0, then 5, 15, 30 deg at azimuth N and E; plus t15_N_hr (lens
1160 mm, half the GSD). Only a centred window is rendered (Blender border, intrinsics unchanged):
512 x 512 px at 580 mm, 1024 x 1024 px at 1160 mm.

Variant files: STUDY/render_inputs/<block>/<name>/<tile>.npz (float32 local vertices, int32 faces,
per-corner UVs + per-face material + texture paths for textured variants, per-vertex sRGB colours
for GRID/WALL, near-vertical face flag |n_z| < 0.3 for REF) and manifest.json.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objtile  # noqa: E402

A, B = 6378137.0, 6356752.314245
R_MEAN = (2 * A + B) / 3
ALTITUDE = 500e3
SENSOR = dict(width_mm=22.528, height_mm=16.896, resolution=[4096, 3072], pixel_m=5.5e-6)
GEO = Transformer.from_crs(7855, 7844, always_xy=True)
ECEF = Transformer.from_crs(4979, 4978, always_xy=True)


def load_frame(study, block):
    return json.loads((study / 'render_inputs' / block / 'frame.json').read_text())


def to_local(xyz, frame):
    lon, lat = GEO.transform(xyz[:, 0], xyz[:, 1])
    earth = np.array(ECEF.transform(lon, lat, xyz[:, 2])).T
    return (earth - np.asarray(frame['origin_ecef_m'])) @ np.asarray(frame['local_from_ecef']).T


def slant_range(theta_deg):
    c = np.cos(np.radians(theta_deg))
    r = R_MEAN + ALTITUDE
    return float(-R_MEAN * c + np.sqrt((R_MEAN * c) ** 2 - R_MEAN ** 2 + r ** 2))


def camera(target, theta, azimuth, lens_mm, window):
    up = np.array([0., 0., 1.])
    a = {'N': np.array([0., 1., 0.]), 'E': np.array([1., 0., 0.]), None: np.zeros(3)}[azimuth]
    d = slant_range(theta)
    t = np.radians(theta)
    position = target + d * (np.sin(t) * a + np.cos(t) * up)
    forward = (target - position) / d
    north = np.array([0., 1., 0.])
    image_up = north - forward * (north @ forward)
    image_up /= np.linalg.norm(image_up)
    right = np.cross(forward, image_up)
    width, height = SENSOR['resolution']
    x0, y0 = (width - window) // 2, (height - window) // 2
    gsd = d * SENSOR['pixel_m'] / (lens_mm * 1e-3)
    return dict(theta_deg=theta, azimuth=azimuth, lens_mm=lens_mm, slant_range_m=d,
                position_local_m=position.tolist(), target_local_m=target.tolist(),
                camera_to_local=np.stack([right, image_up, -forward], 1).tolist(),
                sensor_width_mm=SENSOR['width_mm'], sensor_height_mm=SENSOR['height_mm'],
                resolution=[width, height], crop=dict(x0=x0, x1=x0 + window, y0=y0, y1=y0 + window),
                gsd_across_m=gsd, gsd_tilt_direction_m=gsd / np.cos(t))


def make_frame(args):
    study, name = args.study, args.block
    block = json.loads((study / 'blocks.json').read_text())['blocks'][name]
    x0, y0, x1, y1 = block['bbox_7855']
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    lon, lat = GEO.transform(cx, cy)
    origin = np.array(ECEF.transform(lon, lat, 0.))
    lo, la = np.radians([lon, lat])
    east = np.array([-np.sin(lo), np.cos(lo), 0])
    north = np.array([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)])
    frame = dict(block=name, centre_7855=[cx, cy], lon_lat=[lon, lat], origin_ecef_m=origin.tolist(),
                 local_from_ecef=np.stack([east, north, np.cross(east, north)]).tolist(),
                 R_mean_m=R_MEAN, altitude_m=ALTITUDE)
    heights = []
    for tile in block['tiles']:
        with np.load(study / 'variants' / name / 'GRID' / f'{tile}.npz') as g:
            heights.append(g['height'].ravel())
    h_target = float(np.median(np.concatenate(heights)))
    target = to_local(np.array([[cx, cy, h_target]]), frame)[0]
    frame['target_height_m'] = h_target
    frame['target_local_m'] = target.tolist()
    views = {'t00': camera(target, 0, None, 580, 512)}
    for theta in (5, 15, 30):
        for az in ('N', 'E'):
            views[f't{theta:02d}_{az}'] = camera(target, theta, az, 580, 512)
    views['t15_N_hr'] = camera(target, 15, 'N', 1160, 1024)
    frame['views'] = views
    out = study / 'render_inputs' / name
    out.mkdir(parents=True, exist_ok=True)
    (out / 'frame.json').write_text(json.dumps(frame, indent=1) + '\n')
    print(json.dumps({k: v for k, v in frame.items() if k != 'views'}))
    for k, v in views.items():
        print(k, f"d={v['slant_range_m']:.1f} m gsd={v['gsd_across_m']:.4f}/{v['gsd_tilt_direction_m']:.4f} m")


def grid_mesh(path, frame):
    with np.load(path) as g:
        h, rgb, x0, y0, s = g['height'], g['rgb'], float(g['x0']), float(g['y0']), float(g['spacing'])
    rows, cols = h.shape
    xx, yy = np.meshgrid(x0 + (np.arange(cols) + .5) * s, y0 + (np.arange(rows) + .5) * s)
    vertices = to_local(np.c_[xx.ravel(), yy.ravel(), h.ravel().astype(np.float64)], frame)
    ids = np.arange(rows * cols).reshape(rows, cols)
    a = ids[:-1, :-1].ravel(); b = a + 1; c = a + cols; d = c + 1
    faces = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])  # as render_swarm.py
    return vertices, faces, rgb.reshape(-1, 3)


def make_variant(args):
    study, block_name, name = args.study, args.block, args.name
    frame = load_frame(study, block_name)
    block = json.loads((study / 'blocks.json').read_text())['blocks'][block_name]
    out = study / 'render_inputs' / block_name / (name if name != 'REF' else f'REF_x{args.ref_factor}')
    out.mkdir(parents=True, exist_ok=True)
    manifest = dict(block=block_name, variant=name, tiles=[], triangles=0, texture_megapixels=0.0)
    for tile in block['tiles']:
        if name in ('GRID', 'WALL'):
            vertices, faces, colors = grid_mesh(study / 'variants' / block_name / 'GRID' / f'{tile}.npz', frame)
            if name == 'WALL':
                with np.load(study / 'variants' / block_name / 'WALL' / f'{tile}.npz') as w:
                    quads, qcol = w['quads'], w['rgb']  # (Q, 4, 3) EPSG:7855, (Q, 3) uint8
                base = len(vertices)
                qv = to_local(quads.reshape(-1, 3), frame)
                idx = base + np.arange(len(quads) * 4).reshape(-1, 4)
                qf = np.concatenate([idx[:, [0, 1, 2]], idx[:, [0, 2, 3]]])
                vertices = np.concatenate([vertices, qv])
                faces = np.concatenate([faces, qf])
                colors = np.concatenate([colors, np.repeat(qcol, 4, axis=0)])
            np.savez(out / f'{tile}.npz', kind='vcolor', vertices=vertices.astype(np.float32),
                     faces=faces.astype(np.int32), colors=colors.astype(np.uint8))
            manifest['triangles'] += len(faces)
        else:
            if name == 'REF':
                mesh = objtile.load(next((study / 'extracted' / tile).glob('*.obj')), study / 'work' / 'parsed')
                tex_root = study / 'variants' / block_name / 'REF' / f'tex_x{args.ref_factor}' / tile
            else:
                folder = study / 'variants' / block_name / name / tile
                mesh = objtile.parse_obj(folder / f'{tile}.obj')
                tex_root = folder
            v, vt, fv, ft, fmat, maps = mesh['v'], mesh['vt'], mesh['fv'], mesh['ft'], mesh['fmat'], mesh['maps']
            _, normal = objtile.face_geometry(v, fv)
            vertical = np.abs(np.nan_to_num(normal[:, 2], nan=1.0)) < 0.3
            textures = [str((tex_root / m).resolve()) if m else '' for m in maps]
            np.savez(out / f'{tile}.npz', kind='texture', vertices=to_local(v, frame).astype(np.float32),
                     faces=fv.astype(np.int32), uv=vt[ft.ravel()].astype(np.float32),
                     material=fmat.astype(np.int32), textures=np.array(textures), vertical=vertical)
            manifest['triangles'] += len(fv)
            from PIL import Image
            for m in set(maps[np.unique(fmat)]):
                if m:
                    with Image.open(tex_root / m) as im:
                        manifest['texture_megapixels'] += im.size[0] * im.size[1] / 1e6
        manifest['tiles'].append(tile)
    manifest['ref_factor'] = args.ref_factor if name == 'REF' else None
    manifest['npz_bytes'] = sum(p.stat().st_size for p in out.glob('*.npz'))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps(manifest))


def make_pipeline_grid(args):
    """Memory probe only: the pipeline's own render grids (terrain_to_render.py output of a run, already
    in that run's local frame) as one vcolor variant, triangulated as render_swarm.py does. The run frame
    differs from the block frame by up to ~1 km, which does not matter for a GPU-memory measurement
    (all tiles stay inside the full camera frustum)."""
    out = args.study / 'render_inputs' / args.block / 'GRID_PIPELINE570'
    out.mkdir(parents=True, exist_ok=True)
    manifest = dict(block=args.block, variant='GRID_PIPELINE570', tiles=[], triangles=0, texture_megapixels=0.0,
                    source=str(args.pipeline_terrain))
    for path in sorted(args.pipeline_terrain.glob('*.npz')):
        with np.load(path) as g:
            vertices, rgb, (rows, cols) = g['vertices'], g['rgb'], g['shape']
        ids = np.arange(rows * cols).reshape(rows, cols)
        a = ids[:-1, :-1].ravel(); b = a + 1; c = a + cols; d = c + 1
        faces = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
        np.savez(out / path.name, kind='vcolor', vertices=vertices.astype(np.float32), faces=faces.astype(np.int32),
                 colors=rgb.astype(np.uint8))
        manifest['tiles'].append(path.stem); manifest['triangles'] += len(faces)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(manifest['triangles'], len(manifest['tiles']))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('frame')
    variant = sub.add_parser('variant')
    variant.add_argument('--name', required=True)
    variant.add_argument('--ref-factor', type=int, default=1)
    probe = sub.add_parser('pipeline-grid')
    probe.add_argument('--pipeline-terrain', type=Path, required=True)
    args = parser.parse_args()
    {'frame': make_frame, 'variant': make_variant, 'pipeline-grid': make_pipeline_grid}[args.command](args)


if __name__ == '__main__':
    main()
