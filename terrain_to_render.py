"""Convert native-CRS terrain caches (terrain_cache.py) into render_swarm.py inputs.

For every tile in cameras.json 'selected_tiles' the native grid is binned to the
render spacing (area mean of the cache cells whose centres fall in each render
cell, weighted by their coverage), empty cells are filled from the nearest
filled cell, and the cell centres are transformed into the simulation's local
ENU frame with the same chain as prepare_swarm_terrain.py:
projected CRS -> geographic CRS -> ECEF (height used as ellipsoidal) -> local.

The output files have the keys render_swarm.py expects (vertices, rgb, shape,
sampled_fraction) plus the cache_frame.json guard, so a run can switch to them
with --terrain <output> and no change to the renderer.

  python terrain_to_render.py --cameras .../simulation/cameras.json \\
      --cache data/terrain_cache/melbourne_2020/1m --dataset melbourne_2020 \\
      --output data/swarm_terrain_from_cache
"""
import argparse
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt

from terrain_cache import load_datasets


def bin_to_spacing(height, rgb, coverage, x0, y0, spacing, target):
    """Area-mean of a native grid into a grid of cell size target aligned to multiples of target."""
    rows, cols = height.shape
    xc = x0 + (np.arange(cols) + .5) * spacing
    yc = y0 + (np.arange(rows) + .5) * spacing
    gx0 = np.floor(xc[0] / target) * target
    gy0 = np.floor(yc[0] / target) * target
    ci = np.floor((xc - gx0) / target).astype(np.int64)
    ri = np.floor((yc - gy0) / target).astype(np.int64)
    out_cols, out_rows = int(ci.max()) + 1, int(ri.max()) + 1
    key = (ri[:, None] * out_cols + ci[None, :]).ravel()
    weight = np.where(np.isfinite(height), coverage, 0).astype(np.float64).ravel()
    n = out_rows * out_cols
    wsum = np.bincount(key, weight, n)
    hsum = np.bincount(key, weight * np.nan_to_num(height).ravel(), n)
    csum = np.stack([np.bincount(key, weight * rgb[..., k].ravel(), n) for k in range(3)], 1)
    valid = wsum > 0
    with np.errstate(invalid='ignore', divide='ignore'):
        h = np.where(valid, hsum / wsum, np.nan).reshape(out_rows, out_cols)
        c = np.where(valid[:, None], csum / wsum[:, None], 0).reshape(out_rows, out_cols, 3)
    return h, np.rint(c).astype(np.uint8), gx0, gy0


def convert_tile(cache_path, output, manifest, dataset, target):
    with np.load(cache_path) as data:
        h, rgb, x0, y0 = bin_to_spacing(data['height'], data['rgb'], data['coverage'],
                                       float(data['x0']), float(data['y0']), float(data['spacing']), target)
    valid = np.isfinite(h)
    if not valid.any():
        raise ValueError(f'No covered cells in {cache_path}')
    nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    h = h[tuple(nearest)]
    rgb = rgb[tuple(nearest)]
    rows, cols = h.shape
    xx, yy = np.meshgrid(x0 + (np.arange(cols) + .5) * target, y0 + (np.arange(rows) + .5) * target)
    geo = Transformer.from_crs(dataset['projected_epsg'], dataset['geographic_epsg'], always_xy=True)
    ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    lon, lat = geo.transform(xx.ravel(), yy.ravel())
    earth = np.array(ecef.transform(lon, lat, h.ravel())).T
    local = (earth - np.asarray(manifest['origin_ecef_m'])) @ np.asarray(manifest['local_from_ecef']).T
    temporary = output.with_suffix('.npz.part')
    with temporary.open('wb') as stream:
        np.savez_compressed(stream, vertices=local.astype(np.float32), rgb=rgb.reshape(-1, 3),
                            shape=np.array([rows, cols]), sampled_fraction=float(valid.mean()),
                            compute_device='cpu', compute_device_name='terrain_to_render.py')
    temporary.replace(output)
    return rows * cols


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cameras', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True, help='folder with <tile>.npz from terrain_cache.py')
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--spacing', type=float, help="render grid spacing; default: cameras.json mesh_grid_spacing_m")
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    manifest = json.loads(args.cameras.read_text())
    dataset = load_datasets()[args.dataset]
    target = args.spacing or manifest['mesh_grid_spacing_m']
    args.output.mkdir(parents=True, exist_ok=True)
    frame = {k: manifest[k] for k in ('origin_ecef_m', 'local_from_ecef')}
    frame['mesh_grid_spacing_m'] = target
    frame_path = args.output / 'cache_frame.json'
    if frame_path.exists() and json.loads(frame_path.read_text()) != frame:
        raise ValueError('Frame or spacing differs from the existing output; use a new output directory')
    frame_path.write_text(json.dumps(frame, indent=2) + '\n')
    missing, vertices, done = [], 0, 0
    for tile in manifest['selected_tiles']:
        source, destination = args.cache / f'{tile}.npz', args.output / f'{tile}.npz'
        if not source.exists():
            missing.append(tile)
            continue
        if destination.exists() and not args.force:
            continue
        vertices += convert_tile(source, destination, manifest, dataset, target)
        done += 1
    print(f'{done} tiles converted at {target:g} m ({vertices:,} vertices, '
          f'{2 * vertices:,} triangles approx.) -> {args.output}')
    if missing:
        raise SystemExit(f'{len(missing)} selected tiles missing from the cache: {missing[:10]}...')


if __name__ == '__main__':
    main()
