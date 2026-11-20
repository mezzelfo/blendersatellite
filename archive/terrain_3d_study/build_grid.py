"""Variant GRID of the step-20 study: the current 2.5D pipeline terrain, kept in EPSG:7855.

  python build_grid.py --study STUDY --block CBD

For every tile of the block, data/terrain/melbourne_2020/1m/<tile>.npz is binned to 2.5 m with
terrain_to_render.bin_to_spacing (imported, unchanged) and empty cells are filled from the nearest
filled cell exactly as terrain_to_render.convert_tile does. The only difference from the pipeline is
that the grid stays in the dataset CRS (the render preparation applies the same projected ->
geographic -> ECEF -> local chain). render_swarm.py triangulates it with two triangles per grid cell.

Output STUDY/variants/<block>/GRID/<tile>.npz (x0, y0, spacing, height float32, rgb uint8,
shape, sampled_fraction) and GRID/summary.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from terrain_to_render import bin_to_spacing  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    parser.add_argument('--spacing', type=float, default=2.5)
    args = parser.parse_args()
    block = json.loads((args.study / 'blocks.json').read_text())['blocks'][args.block]
    out = args.study / 'variants' / args.block / 'GRID'
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for tile in block['tiles']:
        t0 = time.monotonic()
        with np.load(ROOT / 'data/terrain/melbourne_2020/1m' / f'{tile}.npz') as data:
            h, rgb, x0, y0 = bin_to_spacing(data['height'], data['rgb'], data['coverage'], float(data['x0']),
                                           float(data['y0']), float(data['spacing']), args.spacing)
        valid = np.isfinite(h)
        nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
        h = h[tuple(nearest)]; rgb = rgb[tuple(nearest)]
        rows, cols = h.shape
        path = out / f'{tile}.npz'
        np.savez_compressed(path, x0=x0, y0=y0, spacing=args.spacing, height=h.astype(np.float32), rgb=rgb,
                            shape=np.array([rows, cols]), sampled_fraction=float(valid.mean()))
        tiles.append(dict(tile=tile, shape=[rows, cols], vertices=rows * cols, triangles=2 * (rows - 1) * (cols - 1),
                          sampled_fraction=float(valid.mean()), npz_bytes=path.stat().st_size,
                          seconds=time.monotonic() - t0))
    summary = dict(block=args.block, spacing=args.spacing, tiles=tiles,
                   vertices=sum(t['vertices'] for t in tiles), triangles=sum(t['triangles'] for t in tiles),
                   npz_bytes=sum(t['npz_bytes'] for t in tiles), seconds=sum(t['seconds'] for t in tiles))
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'tiles'}))


if __name__ == '__main__':
    main()
