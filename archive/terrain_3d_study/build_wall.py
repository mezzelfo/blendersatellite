"""Variant WALL of the step-20 study: GRID plus coloured vertical quads at height steps.

  python -I build_wall.py --study STUDY --block CBD

For every tile's GRID (variants/<block>/GRID/<tile>.npz, 2.5 m, EPSG:7855), each pair of 4-adjacent
cells whose heights differ by more than --step (2 m) gets one vertical quad on their shared cell edge
(the line halfway between the two GRID vertices, one cell edge long), from the lower to the upper
height. The quad's outward normal points to the lower cell.

Colour: a 3 x 3 grid of points on the quad (fractions 1/6, 1/2, 5/6 along both sides) casts
horizontal rays in the outward-normal direction into REF (all 9 tiles of the block, open3d
RaycastingScene). Each ray starts --back (1.25 m, half a cell) behind the quad, so a REF facade
anywhere within +-1.25 m of the cell edge is hit, and hits farther than back + 2.5 m are ignored.
The colour of a hit is the full-resolution texel at the interpolated UV of the hit face. The quad
colour is the mean over its hits; quads without hits take the GRID colour of the lower cell.

Output variants/<block>/WALL/<tile>.npz (quads (Q, 4, 3) EPSG:7855, rgb (Q, 3) uint8, hits (Q,))
and WALL/summary.json.
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objtile  # noqa: E402

Image.MAX_IMAGE_PIXELS = None


def quads_of(h, x0, y0, s, step):
    """Vertical quads between 4-adjacent cells; returns corners (Q,4,3), outward normal (Q,3), lower cell (Q,2)."""
    out_q, out_n, out_low = [], [], []
    rows, cols = h.shape
    # East-west neighbours: cells (r, c) and (r, c + 1), shared edge at x = x0 + (c + 1) s.
    r, c = np.nonzero(np.abs(np.diff(h, axis=1)) > step)
    a, b = h[r, c], h[r, c + 1]
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    x = x0 + (c + 1) * s
    ya, yb = y0 + r * s, y0 + (r + 1) * s
    out_q.append(np.stack([np.c_[x, ya, lo], np.c_[x, yb, lo], np.c_[x, yb, hi], np.c_[x, ya, hi]], 1))
    sign = np.where(a < b, -1., 1.)  # outward = towards the lower cell
    out_n.append(np.c_[sign, np.zeros_like(sign), np.zeros_like(sign)])
    out_low.append(np.c_[r, np.where(a < b, c, c + 1)])
    # North-south neighbours: cells (r, c) and (r + 1, c), shared edge at y = y0 + (r + 1) s.
    r, c = np.nonzero(np.abs(np.diff(h, axis=0)) > step)
    a, b = h[r, c], h[r + 1, c]
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    y = y0 + (r + 1) * s
    xa, xb = x0 + c * s, x0 + (c + 1) * s
    out_q.append(np.stack([np.c_[xa, y, lo], np.c_[xb, y, lo], np.c_[xb, y, hi], np.c_[xa, y, hi]], 1))
    sign = np.where(a < b, -1., 1.)
    out_n.append(np.c_[np.zeros_like(sign), sign, np.zeros_like(sign)])
    out_low.append(np.c_[np.where(a < b, r, r + 1), c])
    return np.concatenate(out_q), np.concatenate(out_n), np.concatenate(out_low)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    parser.add_argument('--step', type=float, default=2.0)
    parser.add_argument('--back', type=float, default=1.25)
    parser.add_argument('--reach', type=float, default=2.5)
    args = parser.parse_args()
    t0 = time.monotonic()
    block = json.loads((args.study / 'blocks.json').read_text())['blocks'][args.block]
    centre = np.array([(block['bbox_7855'][0] + block['bbox_7855'][2]) / 2,
                       (block['bbox_7855'][1] + block['bbox_7855'][3]) / 2, 0.])
    # REF of the whole block, relative to the block centre (float32 in the ray caster).
    verts, faces, uvs, mats, tex_paths = [], [], [], [], []
    offset, mat_offset = 0, 0
    for tile in block['tiles']:
        m = objtile.load(next((args.study / 'extracted' / tile).glob('*.obj')), args.study / 'work' / 'parsed')
        verts.append(m['v'] - centre); faces.append(m['fv'] + offset)
        uvs.append(m['vt'][m['ft']].astype(np.float32)); mats.append(m['fmat'] + mat_offset)
        tex_paths += [str(args.study / 'extracted' / tile / p) if p else '' for p in m['maps']]
        offset += len(m['v']); mat_offset += len(m['maps'])
    verts = np.concatenate(verts); faces = np.concatenate(faces)
    uvs = np.concatenate(uvs); mats = np.concatenate(mats)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(verts.astype(np.float32)), o3d.core.Tensor(faces.astype(np.uint32)))
    t_scene = time.monotonic() - t0

    out = args.study / 'variants' / args.block / 'WALL'
    out.mkdir(parents=True, exist_ok=True)
    frac = np.array([1 / 6, 1 / 2, 5 / 6])
    fu, fv_ = np.meshgrid(frac, frac)
    fu, fv_ = fu.ravel(), fv_.ravel()
    tiles, all_hits = [], defaultdict(list)
    per_tile = {}
    for tile in block['tiles']:
        with np.load(args.study / 'variants' / args.block / 'GRID' / f'{tile}.npz') as g:
            h, rgb, x0, y0, s = g['height'].astype(np.float64), g['rgb'], float(g['x0']), float(g['y0']), float(g['spacing'])
        q, n, low = quads_of(h, x0, y0, s, args.step)
        # Sample points: p = q0 + fu (q1 - q0) + fv (q3 - q0).
        pts = (q[:, None, 0] + fu[None, :, None] * (q[:, None, 1] - q[:, None, 0])
               + fv_[None, :, None] * (q[:, None, 3] - q[:, None, 0]))
        dirs = np.repeat(n[:, None], 9, axis=1)
        origins = pts - args.back * dirs - centre
        rays = np.concatenate([origins, dirs], 2).reshape(-1, 6).astype(np.float32)
        res = scene.cast_rays(o3d.core.Tensor(rays))
        t_hit = res['t_hit'].numpy(); prim = res['primitive_ids'].numpy().astype(np.int64)
        buv = res['primitive_uvs'].numpy()
        ok = np.isfinite(t_hit) & (t_hit <= args.back + args.reach)
        per_tile[tile] = dict(q=q, low=low, rgb=rgb, ok=ok, colour=np.zeros((len(ok), 3)))
        idx = np.flatnonzero(ok)
        f = prim[idx]
        w1, w2 = buv[idx, 0], buv[idx, 1]
        uv = (1 - w1 - w2)[:, None] * uvs[f, 0] + w1[:, None] * uvs[f, 1] + w2[:, None] * uvs[f, 2]
        for k, mat, u in zip(idx, mats[f], uv):
            all_hits[int(mat)].append((tile, int(k), float(u[0]), float(u[1])))
    t_rays = time.monotonic() - t0 - t_scene
    # Texture lookups, one decoded texture at a time.
    for mat, hits in all_hits.items():
        path = tex_paths[mat]
        if not path:
            continue
        with Image.open(path) as im:
            img = np.asarray(im.convert('RGB'))
        hh, ww = img.shape[:2]
        for tile, k, u, v in hits:
            x = min(max(int(u * ww), 0), ww - 1); y = min(max(int((1 - v) * hh), 0), hh - 1)
            per_tile[tile]['colour'][k] = img[y, x]
    t_tex = time.monotonic() - t0 - t_scene - t_rays
    total_q = total_hit_q = 0
    for tile in block['tiles']:
        d = per_tile[tile]
        ok = d['ok'].reshape(-1, 9)
        col = d['colour'].reshape(-1, 9, 3)
        nhit = ok.sum(1)
        with np.errstate(invalid='ignore', divide='ignore'):
            mean = (col * ok[..., None]).sum(1) / nhit[:, None]
        fallback = d['rgb'][d['low'][:, 0], d['low'][:, 1]].astype(np.float64)
        colour = np.where(nhit[:, None] > 0, mean, fallback)
        np.savez_compressed(out / f'{tile}.npz', quads=d['q'], rgb=np.rint(colour).astype(np.uint8), hits=nhit)
        tiles.append(dict(tile=tile, quads=int(len(nhit)), quads_with_hits=int((nhit > 0).sum()),
                          ray_hit_fraction=float(ok.mean()) if len(ok) else None,
                          npz_bytes=(out / f'{tile}.npz').stat().st_size))
        total_q += len(nhit); total_hit_q += int((nhit > 0).sum())
    summary = dict(block=args.block, step_m=args.step, back_m=args.back, reach_m=args.reach, quads=total_q,
                   added_triangles=2 * total_q, quads_without_hit=total_q - total_hit_q,
                   textures_read=len(all_hits), tiles=tiles,
                   seconds=dict(scene=t_scene, rays=t_rays, textures=t_tex, total=time.monotonic() - t0))
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'tiles'}))


if __name__ == '__main__':
    main()
