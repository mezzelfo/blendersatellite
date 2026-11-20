"""Geometric error of DEC_r against REF for one tile (step-20 study, part B).

  python -I hausdorff.py --study STUDY --block CBD --tile D4-04 --ratio 16 [--samples-per-face 1]

One-sided Hausdorff distance REF -> DEC with pymeshlab get_hausdorff_distance: points sampled on the
REF surface (Monte Carlo, uniform in area; samplenum = REF faces x --samples-per-face, no vertex or
edge samples), distance to the closest point of DEC, maxdist 1000 m (no cut-off). Positions are
taken relative to the tile's lower bbox corner (MeshLab float32). The per-sample distances
(savesample) give mean, p50, p95, p99 and max; pymeshlab's own mean/max/RMS are kept as a check.
Writes variants/<block>/DEC_<r>/<tile>/hausdorff.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pymeshlab

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objtile  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    parser.add_argument('--tile', required=True)
    parser.add_argument('--ratio', type=int, required=True)
    parser.add_argument('--samples-per-face', type=float, default=1.0)
    args = parser.parse_args()
    t0 = time.monotonic()
    ref = objtile.load(next((args.study / 'extracted' / args.tile).glob('*.obj')), args.study / 'work' / 'parsed')
    folder = args.study / 'variants' / args.block / f'DEC_{args.ratio}' / args.tile
    dec = objtile.parse_obj(folder / f'{args.tile}.obj')
    origin = np.floor(ref['v'].min(0))
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=ref['v'] - origin, face_matrix=ref['fv']), 'ref')
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=dec['v'] - origin, face_matrix=dec['fv']), 'dec')
    n = int(len(ref['fv']) * args.samples_per_face)
    result = ms.get_hausdorff_distance(sampledmesh=0, targetmesh=1, savesample=True, samplevert=False,
                                       sampleedge=False, samplefauxedge=False, sampleface=True, samplenum=n,
                                       maxdist=pymeshlab.PureValue(1000.0))
    # savesample appends the sample points (vertex quality = distance) and their closest points.
    distances = None
    for i in range(ms.mesh_number()):
        m = ms.mesh(ms.mesh_id_list()[i]) if hasattr(ms, 'mesh_id_list') else ms.mesh(i)
        if m.vertex_number() == result['n_samples'] and m.has_vertex_scalar():
            distances = m.vertex_scalar_array()
            break
    if distances is None:
        raise RuntimeError('per-sample distances not found')
    record = dict(tile=args.tile, block=args.block, ratio=args.ratio, ref_faces=int(len(ref['fv'])),
                  dec_faces=int(len(dec['fv'])), samples=int(len(distances)),
                  mean_m=float(distances.mean()), p50_m=float(np.percentile(distances, 50)),
                  p95_m=float(np.percentile(distances, 95)), p99_m=float(np.percentile(distances, 99)),
                  max_m=float(distances.max()),
                  pymeshlab=dict((k, float(v)) for k, v in result.items()), seconds=time.monotonic() - t0)
    (folder / 'hausdorff.json').write_text(json.dumps(record, indent=1) + '\n')
    np.save(folder.parent / f'{args.tile}_hausdorff_samples.npy', distances.astype(np.float32))
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
