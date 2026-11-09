"""Verification of stacking_geometry.py (step 9 C). Read-only on renders, poses and caches.

Checks
  1. round trip pixel -> ground (z = 0, 30, 100 m) -> pixel, every satellite, frames 0/499/999
  2. common target -> image centre, every satellite and frame (compared with step 8)
  3. real-image convention test, satellites 0 and 3, frames 0/499/999: 200000 random terrain
     cache vertices projected with pixel_from_ground, PNG read at the nearest pixel (alpha > 0),
     |dRGB| = mean over R,G,B of |PNG - vertex| in 8-bit DN. Variants: correct, PNG row axis
     flipped, column axis flipped, +1/2 px and -1/2 px offset on both axes. A bilinear sub-pixel
     offset sweep locates the best offset.
  4. direction of ground motion in the chief PNGs (integer row shift frame 0 -> 50, no poses)
  5. deputy-vs-chief offset predicted from the poses on the terrain vertices, to compare with
     the step 8 phase-correlation shifts
  6. alpha semantics (straight vs premultiplied) and EEVEE-vs-Cycles brightness on the
     Cycles frames kept in the simulation run folder
Run in the conda environment "orbits":
    python scripts/check_stacking_geometry.py [--json out.json]
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

import stacking_geometry as g

TERRAIN = g.REPO / 'data/swarm_terrain'
CYCLES_RUN = g.REPO / 'output/orbits_melbourne/20261109T180310Z_run_v2/renders'
FRAMES = (0, 499, 999)
H, W = g.HEIGHT, g.WIDTH


def png(sat, frame, run=None):
    path = Path(run or g._paths['run_dir'] / 'renders') / f'satellite_{sat:02d}' / f'frame_{frame:04d}.png'
    return np.asarray(Image.open(path).convert('RGBA'))


def load_terrain(selected):
    verts, rgb = [], []
    for tile in selected:
        with np.load(TERRAIN / f'{tile}.npz') as d:
            verts.append(d['vertices'].astype(float)); rgb.append(d['rgb'])
    return np.concatenate(verts), np.concatenate(rgb)


def round_trip():
    rows, cols = np.meshgrid(np.linspace(-.5, H - .5, 61), np.linspace(-.5, W - .5, 81), indexing='ij')
    out = {}
    for s in range(g.N_SATELLITES):
        worst = 0.
        for k in FRAMES:
            for h in (0., 30., 100.):
                r, c = g.pixel_from_ground(s, k, g.ground_from_pixel(s, k, rows, cols, h))
                worst = max(worst, float(np.max(np.hypot(r - rows, c - cols))))
        out[s] = worst
    return out


def target_centre(summary):
    target = g.common_target_local_m()
    out = {}
    for s in range(g.N_SATELLITES):
        r, c = g.pixel_from_ground(s, np.arange(g.N_FRAMES), target)
        err = np.hypot(r - g.IMAGE_CENTRE[0], c - g.IMAGE_CENTRE[1])
        step8 = next((t['reproj_max'] for t in summary['target'] if t['sat'] == s), None)
        out[s] = dict(max_px=float(err.max()), argmax=int(err.argmax()), step8_max_px=step8)
    return out


def sample_rgb(img, r, c, mode):
    """Nearest-neighbour or bilinear PNG read at continuous (r, c) (integer = pixel centre)."""
    if mode == 'nearest':
        ri, ci = np.rint(r).astype(int), np.rint(c).astype(int)
        ok = (ri >= 0) & (ri < H) & (ci >= 0) & (ci < W)
        px = np.zeros((len(r), 4))
        px[ok] = img[ri[ok], ci[ok]]
        return px, ok & (px[:, 3] > 0)
    r0, c0 = np.floor(r).astype(int), np.floor(c).astype(int)
    ok = (r0 >= 0) & (r0 < H - 1) & (c0 >= 0) & (c0 < W - 1)
    fr, fc = (r - r0)[ok, None], (c - c0)[ok, None]
    a, b = r0[ok], c0[ok]
    q = [img[a, b].astype(float), img[a, b + 1].astype(float), img[a + 1, b].astype(float), img[a + 1, b + 1].astype(float)]
    px = np.zeros((len(r), 4))
    px[ok] = (q[0] * (1 - fc) + q[1] * fc) * (1 - fr) + (q[2] * (1 - fc) + q[3] * fc) * fr
    alpha_all = np.zeros(len(r), bool)
    alpha_all[ok] = np.min([x[:, 3] for x in q], axis=0) > 0
    return px, alpha_all


def stats(px, ok, rgb):
    d = np.abs(px[ok, :3] - rgb[ok]).mean(1)
    return dict(n=int(ok.sum()), median=float(np.median(d)), p95=float(np.percentile(d, 95)),
                within_25=float(np.mean(d <= 25)), mean=float(d.mean()))


def convention_test(verts, rgb, sats=(0, 3)):
    variants = {'correct': lambda r, c: (r, c), 'row_flipped': lambda r, c: (H - 1 - r, c),
                'col_flipped': lambda r, c: (r, W - 1 - c), 'half_px_plus': lambda r, c: (r + .5, c + .5),
                'half_px_minus': lambda r, c: (r - .5, c - .5)}
    sweep_offsets = (-.5, -.25, 0., .25, .5)
    out = {}
    for s in sats:
        for k in FRAMES:
            img = png(s, k)
            r, c = g.pixel_from_ground(s, k, verts)
            res = {name: stats(*sample_rgb(img, *f(r, c), 'nearest'), rgb) for name, f in variants.items()}
            sweep = {}
            for dr in sweep_offsets:
                for dc in sweep_offsets:
                    sweep[f'{dr:+.2f},{dc:+.2f}'] = stats(*sample_rgb(img, r + dr, c + dc, 'bilinear'), rgb)['mean']
            best = min(sweep, key=sweep.get)
            res['bilinear_sweep_mean'] = sweep; res['bilinear_best_offset'] = best
            out[f'{s}/{k}'] = res
    return out


def motion_sign():
    """Integer row shift d minimising the masked SSD between frame 0 and frame 50 of the chief:
    frame50[r + d, c] ~ frame0[r, c]. d > 0 means features move to increasing row."""
    a, b = png(0, 0).astype(float), png(0, 50).astype(float)
    rs, cs = slice(800, 2300), slice(1200, 2900)
    lum = lambda x: x[..., :3] @ [0.2126, 0.7152, 0.0722]
    la, lb = lum(a), lum(b)
    costs = {}
    for d in range(-60, 61):
        ra = la[rs, cs]; rb = lb[rs.start + d:rs.stop + d, cs]
        m = (a[rs, cs, 3] == 255) & (b[rs.start + d:rs.stop + d, cs, 3] == 255)
        costs[d] = float(np.mean((ra[m] - rb[m]) ** 2))
    best = min(costs, key=costs.get)
    return dict(best_row_shift_frame0_to_50=best, cost_best=costs[best], cost_minus=costs[-best],
                predicted_rows=float(sum(g.pixel_from_ground(0, k + 1, g.ground_from_pixel(0, k, *g.IMAGE_CENTRE))[0]
                                         - g.IMAGE_CENTRE[0] for k in range(50))))


def deputy_offsets(verts, summary):
    """Mean (deputy - chief) PNG position of terrain vertices visible to the chief."""
    out = {}
    for row in summary['overlap']:
        k = row['frame']
        rc, cc = g.pixel_from_ground(0, k, verts)
        vis = (rc >= 0) & (rc < H) & (cc >= 0) & (cc < W)
        for s, img in row.get('image', {}).items():
            rd, cd = g.pixel_from_ground(int(s), k, verts[vis])
            out.setdefault(s, []).append(dict(frame=k, predicted_rows=float(np.mean(rd - rc[vis])),
                                              predicted_cols=float(np.mean(cd - cc[vis])),
                                              step8_rows=img['shift_rows'], step8_cols=img['shift_cols']))
    return out


def alpha_and_cycles():
    img = png(0, 0).astype(float)
    lum = img[..., :3] @ [0.2126, 0.7152, 0.0722]
    a = img[..., 3]
    out = dict(alpha_semantics=dict(median_lum_alpha_1_127=float(np.median(lum[(a > 0) & (a < 128)])),
                                    median_lum_alpha_128_254=float(np.median(lum[(a >= 128) & (a < 255)])),
                                    median_lum_opaque=float(np.median(lum[a == 255])),
                                    partial_pixels=int(np.count_nonzero((a > 0) & (a < 255)))))
    header = json.loads((CYCLES_RUN / 'render_report.jsonl').read_text().splitlines()[0])
    out['cycles_header'] = {k: header.get(k) for k in ('engine', 'samples', 'compute_device', 'gpu_backend', 'blender_version')}
    rows = []
    for s, k in ((0, 0), (0, 499), (0, 500), (0, 999), (3, 999)):
        cyc = CYCLES_RUN / f'satellite_{s:02d}' / f'frame_{k:04d}.png'
        if not cyc.exists(): continue
        e, c = png(s, k).astype(float), png(s, k, CYCLES_RUN).astype(float)
        both = (e[..., 3] == 255) & (c[..., 3] == 255)
        d = (e[..., :3] - c[..., :3])[both]
        rows.append(dict(sat=s, frame=k, mean_eevee_minus_cycles_DN=d.mean(0).tolist(), mean_all=float(d.mean()),
                         mean_abs_DN=float(np.abs(d).mean()), pixels=int(both.sum())))
    out['eevee_minus_cycles'] = rows
    return out


def run_checks(seed=0, n_vertices=200000):
    start = time.monotonic()
    summary = json.loads((g._paths['run_dir'] / 'validation_summary.json').read_text())
    verts_all, rgb_all = load_terrain(g._cameras()['selected_tiles'])
    pick = np.random.default_rng(seed).choice(len(verts_all), n_vertices, replace=False)
    verts, rgb = verts_all[pick], rgb_all[pick].astype(float)
    res = dict(round_trip_max_px=round_trip(), target_centre=target_centre(summary),
               terrain_vertices_total=int(len(verts_all)), sample=dict(seed=seed, n=n_vertices),
               conventions=convention_test(verts, rgb), motion_sign=motion_sign(),
               deputy_offsets=deputy_offsets(verts, summary), images=alpha_and_cycles())
    res['seconds'] = time.monotonic() - start
    return res


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--json', type=Path, help='also write the results here')
    args = parser.parse_args()
    res = run_checks()
    print('round trip max px:', res['round_trip_max_px'])
    print('target -> centre max px:', {s: v['max_px'] for s, v in res['target_centre'].items()})
    for key, value in res['conventions'].items():
        print(key, {n: (v['median'], round(v['p95'], 2), round(v['within_25'], 4), v['n'])
                    for n, v in value.items() if isinstance(v, dict) and 'median' in v}, 'best', value['bilinear_best_offset'])
    print('motion:', res['motion_sign'])
    print('images:', json.dumps(res['images'])[:800])
    fails = [s for s, v in res['round_trip_max_px'].items() if v > 1e-6]
    fails += [f'target {s}' for s, v in res['target_centre'].items() if v['max_px'] > 1e-6]
    fails += [k for k, v in res['conventions'].items()
              if min((x['mean'] for n, x in v.items() if isinstance(x, dict) and 'mean' in x), default=0) != v['correct']['mean']]
    print('FAILURES:', fails or 'none')
    if args.json: args.json.write_text(json.dumps(res, indent=1, default=float) + '\n')


if __name__ == '__main__':
    main()
