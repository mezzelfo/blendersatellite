"""Convert native-CRS terrain caches (terrain_cache.py) into render_swarm.py inputs.

For every tile in cameras.json 'selected_tiles' the native grid is binned to the
render spacing (area mean of the cache cells overlapping each render cell,
weighted by overlap area and coverage), empty cells are filled from the nearest
filled cell, and the cell centres are transformed into the simulation's local
ENU frame with the same chain as prepare_swarm_terrain.py:
projected CRS -> geographic CRS -> ECEF (height used as ellipsoidal) -> local.

The output files have the keys render_swarm.py expects (vertices, rgb, shape,
sampled_fraction) plus the cache_frame.json guard, so a run can switch to them
with --terrain <output> and no change to the renderer.

  python terrain_to_render.py --cameras .../simulation/cameras.json \\
      --cache data/terrain/melbourne_2020/1m --dataset melbourne_2020
  (output by default in <run>/terrain_render)
"""
import argparse
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt

from terrain_cache import load_datasets


def _axis_overlap(start, spacing, count, target):
    """Split cells [start + i*spacing, +spacing) between the target bins they overlap.

    Returns the grid origin (multiple of target), and for every source cell the lower bin
    index and the fraction of the cell inside it (the rest goes to the next bin).
    Requires spacing <= target, so a cell overlaps at most two bins.
    """
    origin = np.floor(start / target) * target
    left = (start - origin) / target + np.arange(count) * (spacing / target)  # in bins
    low = np.floor(left + 1e-9).astype(np.int64)
    fraction = np.clip((low + 1 - left) * (target / spacing), 0., 1.)
    fraction[fraction > 1 - 1e-9] = 1.  # cell entirely inside one bin
    return origin, low, fraction


def bin_to_spacing(height, rgb, coverage, x0, y0, spacing, target):
    """Area-mean of a native grid into a grid of cell size target aligned to multiples of target.

    Each native cell contributes to every target cell it overlaps, weighted by the
    overlap area times its coverage, so the binned value is centred on the target
    cell even when target / spacing is not an integer (e.g. 1 m -> 2.5 m).
    """
    if spacing > target + 1e-9:
        raise ValueError(f'cache spacing {spacing} is coarser than the render spacing {target}')
    rows, cols = height.shape
    gx0, ci, fx = _axis_overlap(x0, spacing, cols, target)
    gy0, ri, fy = _axis_overlap(y0, spacing, rows, target)
    out_cols = int(np.max(np.where(fx < 1, ci + 1, ci))) + 1
    out_rows = int(np.max(np.where(fy < 1, ri + 1, ri))) + 1
    n = out_rows * out_cols
    base = np.where(np.isfinite(height), coverage, 0).astype(np.float64)
    hval = np.nan_to_num(height).astype(np.float64)
    wsum, hsum, csum = np.zeros(n), np.zeros(n), np.zeros((n, 3))
    for dr, wy in ((0, fy), (1, 1 - fy)):
        for dc, wx in ((0, fx), (1, 1 - fx)):
            weight = (base * wy[:, None] * wx[None, :]).ravel()
            used = weight > 0
            key = ((ri + dr)[:, None] * out_cols + (ci + dc)[None, :]).ravel()[used]
            weight = weight[used]
            wsum += np.bincount(key, weight, n)
            hsum += np.bincount(key, weight * hval.ravel()[used], n)
            for k in range(3):
                csum[:, k] += np.bincount(key, weight * rgb[..., k].ravel()[used], n)
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
    return rows, cols


def binned_shape(cache_path, target):
    """(rows, cols) that convert_tile would write for this cache, without converting it."""
    with np.load(cache_path) as data:
        rows, cols = data['height'].shape
        x0, y0, spacing = float(data['x0']), float(data['y0']), float(data['spacing'])
    _, ci, fx = _axis_overlap(x0, spacing, cols, target)
    _, ri, fy = _axis_overlap(y0, spacing, rows, target)
    return int(np.max(np.where(fy < 1, ri + 1, ri))) + 1, int(np.max(np.where(fx < 1, ci + 1, ci))) + 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cameras', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True, help='folder with <tile>.npz from terrain_cache.py')
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', type=Path,
                        help='default: <run>/terrain_render, where cameras.json is <run>/simulation/cameras.json')
    parser.add_argument('--spacing', type=float, help="render grid spacing; default: cameras.json mesh_grid_spacing_m")
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='report what would be converted, write nothing')
    args = parser.parse_args()

    if args.output is None:
        args.output = args.cameras.resolve().parent.parent / 'terrain_render'
    manifest = json.loads(args.cameras.read_text())
    dataset = load_datasets()[args.dataset]
    target = args.spacing or manifest['mesh_grid_spacing_m']
    frame = {k: manifest[k] for k in ('origin_ecef_m', 'local_from_ecef')}
    frame['mesh_grid_spacing_m'] = target
    frame_path = args.output / 'cache_frame.json'
    if frame_path.exists() and json.loads(frame_path.read_text()) != frame:
        raise ValueError('Frame or spacing differs from the existing output; use a new output directory')
    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)
        frame_path.write_text(json.dumps(frame, indent=2) + '\n')
    missing, done, kept, vertices, triangles = [], 0, 0, 0, 0
    for tile in manifest['selected_tiles']:
        source, destination = args.cache / f'{tile}.npz', args.output / f'{tile}.npz'
        if not source.exists():
            missing.append(tile)
            continue
        if destination.exists() and not args.force:
            with np.load(destination) as data:  # already converted: count what the renderer will load
                rows, cols = (int(n) for n in data['shape'])
            kept += 1
        elif args.dry_run:
            rows, cols = binned_shape(source, target)
            done += 1
        else:
            rows, cols = convert_tile(source, destination, manifest, dataset, target)
            done += 1
        vertices += rows * cols
        triangles += 2 * (rows - 1) * (cols - 1)  # two per grid cell, as render_swarm.py builds them
    print(f'{"would convert" if args.dry_run else "converted"} {done} tiles at {target:g} m, '
          f'{kept} already present; {done + kept} tiles, {vertices:,} vertices, '
          f'{triangles:,} triangles -> {args.output}')
    if missing:
        raise SystemExit(f'{len(missing)} selected tiles missing from the cache: {missing[:10]}...')


if __name__ == '__main__':
    main()
