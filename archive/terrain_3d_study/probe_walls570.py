"""Step 22 C: GPU-memory probe inputs for the 570 run_v2 tiles with walls (no download needed).

  python -I probe_walls570.py --study STUDY --variant WALL2        (or WALL2_BLOCK)

The render grids come from data/terrain/melbourne_2020/1m (bit-for-bit the grids of the facade
cache), the walls from terrain_to_render.build_walls with an empty voxel set: the geometry is
exact, every wall takes the lower cell's colour (colour does not change GPU memory). Vertices are
in the run_v2 local frame, as the GRID_PIPELINE570 probe of step 20; rendered with the CBD frame's
t00 camera (the full scene is loaded either way). Output render_inputs/CBD/<variant>_PIPELINE570/.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from terrain_cache import load_datasets  # noqa: E402
from terrain_to_render import build_walls, render_grid, to_local  # noqa: E402

RUN = ROOT / 'output/orbits_melbourne/20261109T180310Z_run_v2'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--variant', choices=['WALL2', 'WALL2_BLOCK'], required=True)
    args = parser.parse_args()
    manifest = json.loads((RUN / 'simulation/cameras.json').read_text())
    dataset = load_datasets()['melbourne_2020']
    cache = ROOT / 'data/terrain/melbourne_2020/1m'
    tiles = manifest['selected_tiles']
    grids = {t: render_grid(cache / f'{t}.npz', 2.5) for t in tiles}
    empty = dict(ijk=np.zeros((0, 3), np.int64), rgb=np.zeros((0, 3)), normal=np.zeros((0, 2)),
                 count=np.zeros(0), voxel=1.0)
    block = args.variant == 'WALL2_BLOCK'
    out = args.study / 'render_inputs' / 'CBD' / f'{args.variant}_PIPELINE570'
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(block='CBD', variant=f'{args.variant}_PIPELINE570', tiles=[], triangles=0, texture_megapixels=0.0,
                note='memory probe: exact geometry, walls coloured with the lower cell')
    for t in tiles:
        g = grids[t]
        w = build_walls(t, grids, lambda _: empty, 2.5, 2.0, all_steps=block)
        quads, qrgb = w['quads'], w['rgb']
        if block:
            quads, qrgb = np.concatenate([w['cell_quads'], quads]), np.concatenate([w['cell_rgb'], qrgb])
            vertices, faces, colors = np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros((0, 3), np.uint8)
        else:
            rows, cols = g['height'].shape
            xx, yy = np.meshgrid(g['x0'] + (np.arange(cols) + .5) * 2.5, g['y0'] + (np.arange(rows) + .5) * 2.5)
            vertices = to_local(np.c_[xx.ravel(), yy.ravel(), g['height'].ravel()], manifest, dataset)
            ids = np.arange(rows * cols).reshape(rows, cols)
            a = ids[:-1, :-1].ravel(); b = a + 1; c = a + cols; d = c + 1
            faces = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
            colors = g['rgb'].reshape(-1, 3)
        base = len(vertices)
        idx = base + np.arange(len(quads) * 4).reshape(-1, 4)
        vertices = np.concatenate([vertices, to_local(quads.reshape(-1, 3), manifest, dataset)])
        faces = np.concatenate([faces, idx[:, [0, 1, 2]], idx[:, [0, 2, 3]]])
        colors = np.concatenate([colors, np.repeat(qrgb, 4, axis=0)])
        np.savez(out / f'{t}.npz', kind='vcolor', vertices=vertices.astype(np.float32), faces=faces.astype(np.int32),
                 colors=colors.astype(np.uint8))
        meta['tiles'].append(t); meta['triangles'] += len(faces)
    (out / 'manifest.json').write_text(json.dumps(meta, indent=1) + '\n')
    print(meta['triangles'], len(meta['tiles']))


if __name__ == '__main__':
    main()
