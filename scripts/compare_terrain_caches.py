"""Compare two render-frame terrain folders (e.g. prepare_swarm_terrain.py vs terrain_to_render.py).

Grids are not aligned (the old caches start at each tile's minimum, the new ones
at multiples of the spacing), so every new vertex is paired with the nearest old
vertex in the horizontal plane and only pairs closer than --max-distance count. Expect differences at
roof edges: a half-cell shift moves the edge by up to one cell.
Reports per-tile and overall percentiles of |dz| and mean |dRGB|.

  python scripts/compare_terrain_caches.py data/swarm_terrain data/swarm_terrain_from_cache --limit 20
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('old', type=Path)
    parser.add_argument('new', type=Path)
    parser.add_argument('--max-distance', type=float, default=1.8,
                        help='metres, horizontal; 1.8 m is half the diagonal of a 2.5 m cell, so every vertex pairs')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    tiles = sorted(p.stem for p in args.new.glob('*.npz') if (args.old / p.name).exists())[:args.limit]
    all_dz, all_drgb, rows = [], [], []
    for tile in tiles:
        with np.load(args.old / f'{tile}.npz') as o, np.load(args.new / f'{tile}.npz') as n:
            ov, orgb, nv, nrgb = o['vertices'], o['rgb'].astype(float), n['vertices'], n['rgb'].astype(float)
        distance, index = cKDTree(ov[:, :2]).query(nv[:, :2], distance_upper_bound=args.max_distance)
        hit = np.isfinite(distance)
        if not hit.any():
            print(json.dumps(dict(tile=tile, pairs=0)))
            continue
        dz = np.abs(nv[hit, 2] - ov[index[hit], 2])
        drgb = np.abs(nrgb[hit] - orgb[index[hit]]).mean(1)
        all_dz.append(dz); all_drgb.append(drgb)
        rows.append(dict(tile=tile, pairs=int(hit.sum()), dz_median_m=float(np.median(dz)),
                         dz_p95_m=float(np.percentile(dz, 95)), drgb_mean=float(drgb.mean())))
        print(json.dumps(rows[-1]))
    dz, drgb = np.concatenate(all_dz), np.concatenate(all_drgb)
    print(json.dumps(dict(tiles=len(tiles), pairs=len(dz), dz_median_m=float(np.median(dz)),
                          dz_p95_m=float(np.percentile(dz, 95)), dz_p99_m=float(np.percentile(dz, 99)),
                          drgb_mean=float(drgb.mean()), drgb_p95=float(np.percentile(drgb, 95)))))


if __name__ == '__main__':
    main()
