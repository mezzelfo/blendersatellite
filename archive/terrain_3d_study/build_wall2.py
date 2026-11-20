"""Variants WALL2 and WALL2_BLOCK of the step-22 validation: pipeline walls from facade voxels.

  python -I build_wall2.py --study STUDY --block CBD --cache STUDY/wall2/data/melbourne_2020/1m_facades
  (the step-22 study cache, built with the then-option --facades --tag facades; any cache with facade
  voxels works, e.g. data/terrain/melbourne_2020/1m)
  python -I build_wall2.py ... --all-steps          (variant WALL2_BLOCK)

The walls are made by the pipeline code itself (terrain_to_render.render_grid and build_walls,
imported, unchanged) from the block's facade caches (terrain_cache.py, facade voxels by default), at the render
spacing 2.5 m and the default threshold 2 m, in EPSG:7855. Only the block's 9 tiles are loaded,
so the block's outer border has no halo, like the border of a run's tile selection.

WALL2:       the render grid of each tile (two triangles per cell, as GRID) plus the walls.
WALL2_BLOCK: no grid; each owned render cell is a flat quad at its own height, and every edge with
             any height difference gets a wall (coloured from voxels when the step exceeds the
             threshold, with the lower cell's colour otherwise).

Output variants/<block>/<variant>/<tile>.npz in the format of build_wall.py (quads (Q, 4, 3)
EPSG:7855, rgb (Q, 3) uint8, plus source and no_grid) and <variant>/summary.json, which also
records whether each tile's render grid equals the GRID variant bit for bit, and how many wall
quads appear in two tiles (the seam rule says none).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from terrain_to_render import SOURCES, build_walls, facade_loader, render_grid  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--spacing', type=float, default=2.5)
    parser.add_argument('--threshold', type=float, default=2.0)
    parser.add_argument('--all-steps', action='store_true', help='variant WALL2_BLOCK')
    args = parser.parse_args()
    t0 = time.monotonic()
    name = 'WALL2_BLOCK' if args.all_steps else 'WALL2'
    block = json.loads((args.study / 'blocks.json').read_text())['blocks'][args.block]
    out = args.study / 'variants' / args.block / name
    out.mkdir(parents=True, exist_ok=True)
    grids = {t: render_grid(args.cache / f'{t}.npz', args.spacing) for t in block['tiles']}
    t_grids = time.monotonic() - t0
    load = facade_loader(args.cache)
    tiles, seen = [], {}
    totals = dict(edges=0, segments=0, cells=0, sources=np.zeros(len(SOURCES), dtype=np.int64))
    for tile in block['tiles']:
        t1 = time.monotonic()
        walls = build_walls(tile, grids, load, args.spacing, args.threshold, all_steps=args.all_steps)
        quads, rgb = walls['quads'], walls['rgb']
        if args.all_steps:
            quads = np.concatenate([walls['cell_quads'], quads])
            rgb = np.concatenate([walls['cell_rgb'], rgb])
        np.savez_compressed(out / f'{tile}.npz', quads=quads, rgb=rgb, source=walls['source'],
                            no_grid=args.all_steps, cells=len(walls.get('cell_quads', [])))
        for q in np.round(walls['quads'][:, [0, 1]].reshape(len(walls['quads']), -1), 3):
            key = tuple(q)
            seen[key] = seen.get(key, 0) + 1
        with np.load(args.study / 'variants' / args.block / 'GRID' / f'{tile}.npz') as g:
            same = bool(np.array_equal(g['height'], grids[tile]['height'].astype(np.float32))
                        and np.array_equal(g['rgb'], grids[tile]['rgb']))
        counts = np.bincount(walls['source'], minlength=len(SOURCES))
        tiles.append(dict(tile=tile, edges=int(walls['edges']), segments=int(len(walls['quads'])),
                          cells=int(len(walls.get('cell_quads', []))),
                          sources=dict(zip(SOURCES, map(int, counts))), grid_equals_GRID=same,
                          npz_bytes=(out / f'{tile}.npz').stat().st_size, seconds=time.monotonic() - t1))
        totals['edges'] += int(walls['edges']); totals['segments'] += len(walls['quads'])
        totals['cells'] += tiles[-1]['cells']; totals['sources'] += counts
    grid_triangles = sum(2 * (g['height'].shape[0] - 1) * (g['height'].shape[1] - 1) for g in grids.values())
    summary = dict(block=args.block, variant=name, spacing=args.spacing, threshold_m=args.threshold,
                   cache=str(args.cache), edges=totals['edges'], segments=totals['segments'],
                   sources=dict(zip(SOURCES, map(int, totals['sources']))),
                   wall_triangles=2 * totals['segments'],
                   triangles=(2 * totals['cells'] if args.all_steps else grid_triangles) + 2 * totals['segments'],
                   cells=totals['cells'], duplicate_wall_segments=sum(n - 1 for n in seen.values() if n > 1),
                   all_grids_equal_GRID=all(t['grid_equals_GRID'] for t in tiles), tiles=tiles,
                   seconds=dict(grids=t_grids, total=time.monotonic() - t0))
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'tiles'}))


if __name__ == '__main__':
    main()
