"""Validation of the cubes written by build_cubes.py (step 14 B; reused in steps 15-16).

Read-only on the PNGs and the cubes. Writes only under <run>/cubes/: the previews
(<layout>_<mode>/preview/*.png) and the per-band chief shifts (cubes/chief_shifts.json,
with --save-shifts). Prints a markdown report to stdout.

Checks (satellite S, layouts compact + diagnostic, modes none + linear)
  B.1 1000 random (g, c, b) samples per cube vs the PNG pixel (none) or the linear interpolation
      recomputed from the two PNG frames around the stored float32 k* (linear); plus the full cube.
  B.2 band b vs band 0 (mode none) on pixels with alpha 255 in both: mean |dRGB|, p99 of the
      per-pixel mean |dRGB|, fraction of identical pixels.
  B.3 the same with band b read at k = r_b - g + o, o in -2..+2 (band 0 kept at o = 0).
  B.4 phase correlation (phase_correlation.py: luminance x alpha) of slice b vs slice 0, both
      modes, against the S9 prediction (alpha-weighted mean over the slice of the per-pixel
      z = 0 drift of band b minus band 0).
  B.5 none vs linear on the common lines.
  B.6 alpha of band b vs band 0.
  B.7 previews.

Run in the conda environment orbits:
    python scripts/check_cubes.py --satellite 0 --workers 20 --save-shifts > report.md
"""
import argparse
import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

import stacking_geometry as sg
from phase_correlation import phase_shift

LAYOUTS = ('compact', 'diagnostic')
MODES = ('none', 'linear')
OFFSETS = (-2, -1, 0, 1, 2)
LUMA = np.array([0.2126, 0.7152, 0.0722])


def md(header, rows):
    return '\n'.join(['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)] +
                     ['| ' + ' | '.join(map(str, r)) + ' |' for r in rows])


def load_cube(run, layout, mode, sat):
    with np.load(Path(run) / 'cubes' / f'{layout}_{mode}' / f'cube_sat{sat:02d}.npz') as d:
        out = {k: d[k] for k in d.files}
    out['metadata'] = json.loads(str(out['metadata']))
    return out


def _read(args):
    path, rows, c0, c1 = args
    with Image.open(path) as im:
        a = np.asarray(im.convert('RGBA'))
    return a[rows, c0:c1 + 1].copy()


def read_png_rows(run, sat, rows, c0, c1, n_frames, workers):
    """uint8 [N, R, C, 4]: the given PNG rows of every frame, columns c0..c1."""
    jobs = [(Path(run) / 'renders' / f'satellite_{sat:02d}' / f'frame_{k:04d}.png', np.asarray(rows), c0, c1)
            for k in range(n_frames)]
    with Pool(workers) as pool:
        return np.stack(pool.map(_read, jobs, chunksize=4))


def lerp_rint(stack, k_star, ri, ci, n_frames):
    """Independent re-implementation of S5 linear: rint((1-f) F[k0] + f F[k0+1])."""
    ks = k_star.astype(np.float64)
    k0 = np.minimum(np.floor(ks), n_frames - 2).astype(np.int64)
    f = (ks - k0)[..., None]
    a = stack[k0, ri, ci].astype(np.float64)
    b = stack[k0 + 1, ri, ci].astype(np.float64)
    return np.rint((1 - f) * a + f * b).astype(np.uint8)


def pair_stats(a, b):
    """a, b uint8 [..., 4]: stats on alpha 255 in both."""
    m = (a[..., 3] == 255) & (b[..., 3] == 255)
    d = np.abs(a[..., :3][m].astype(np.int16) - b[..., :3][m].astype(np.int16))
    per_px = d.mean(1)
    return dict(n=int(m.sum()), mean=float(d.mean()), p99=float(np.percentile(per_px, 99)),
                identical=float(np.mean(np.all(d == 0, 1))))


def gray(slice_rgba):
    s = slice_rgba.astype(np.float64) / 255
    return (s[..., :3] @ LUMA) * s[..., 3]


def drift_maps(sat, rows, cols, g, frame_index, mode, n_frames):
    """Per-pixel z = 0 drift (rows, cols) [G, C, B]: frame-0 position of the ground seen by
    cube sample (g, c, b) minus (g, c). none: frame k = r_b - g; linear: linear interpolation
    of the positions at floor(k*) and floor(k*) + 1 (the same weights as the RGBA)."""
    G, C, B = g.size, cols.size, rows.size
    dr, dc = np.empty((G, C, B)), np.empty((G, C, B))
    for b in range(B):
        if mode == 'none':
            k = np.broadcast_to((rows[b] - g)[:, None], (G, C))
            xr, xc = sg.pixel_from_ground(sat, 0, sg.ground_from_pixel(sat, k, rows[b], cols[None, :]))
        else:
            ks = frame_index[:, :, b].astype(np.float64)
            k0 = np.minimum(np.floor(ks), n_frames - 2).astype(np.int64)
            f = ks - k0
            r0, c0_ = sg.pixel_from_ground(sat, 0, sg.ground_from_pixel(sat, k0, rows[b], cols[None, :]))
            r1, c1_ = sg.pixel_from_ground(sat, 0, sg.ground_from_pixel(sat, k0 + 1, rows[b], cols[None, :]))
            xr, xc = (1 - f) * r0 + f * r1, (1 - f) * c0_ + f * c1_
        dr[:, :, b] = xr - g[:, None]
        dc[:, :, b] = xc - cols[None, :]
    return dr, dc


def save_png(path, arr):
    Image.fromarray(arr).save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--satellite', type=int, default=0)
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--run', type=Path, default=sg.RUN_DIR)
    ap.add_argument('--samples', type=int, default=1000)
    ap.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    ap.add_argument('--save-shifts', action='store_true', help='write cubes/chief_shifts.json (chief only)')
    args = ap.parse_args()
    sat, run, modes = args.satellite, args.run, [m for m in MODES if m in args.modes]
    cubes = {(l, m): load_cube(run, l, m, sat) for l in LAYOUTS for m in modes}
    first = next(iter(cubes.values()))
    n_frames, cols = first['metadata']['n_frames'], first['cols']
    for c in cubes.values():
        assert np.array_equal(c['cols'], cols) and c['metadata']['satellite'] == sat
    all_rows = np.union1d(*[cubes[l, modes[0]]['band_rows'] for l in LAYOUTS])
    stack = read_png_rows(run, sat, all_rows, int(cols[0]), int(cols[-1]), n_frames, args.workers)
    rng = np.random.default_rng(14)
    print(f'# Cube validation, satellite {sat}, modes {", ".join(modes)}\n')
    print(f'PNG rows read: {all_rows.size} rows x {cols.size} cols x {n_frames} frames '
          f'({stack.nbytes / 1e6:.0f} MB)\n')

    # ---------------- B.1
    rows_b1 = []
    for (l, m), c in cubes.items():
        g, rows, cube, idx = c['g'], c['band_rows'], c['cube'], c['frame_index']
        ri = np.searchsorted(all_rows, rows)
        G, C, B = g.size, cols.size, rows.size
        res = []
        for full in (False, True):
            if full: gi, ci, bi = (a.ravel() for a in np.meshgrid(np.arange(G), np.arange(C), np.arange(B), indexing='ij'))
            else: gi, ci, bi = rng.integers(0, G, args.samples), rng.integers(0, C, args.samples), rng.integers(0, B, args.samples)
            if m == 'none':
                k = idx[gi, bi]
                assert np.array_equal(k, rows[bi] - g[gi])
                ref = stack[k, ri[bi], ci]
            else:
                ref = lerp_rint(stack, idx[gi, ci, bi], ri[bi], ci, n_frames)
            res.append(int(np.sum(np.any(cube[gi, ci, bi] != ref, -1))))
        rows_b1.append([l, m, f'{G} x {C} x {B}', f'{args.samples - res[0]}/{args.samples}', res[1],
                        'yes' if res == [0, 0] else 'NO'])
    print('## B.1 Direct read\n')
    print(md(['layout', 'mode', 'G x C x B', 'random samples equal', 'full cube: differing samples', 'all equal'], rows_b1))

    # ---------------- B.2 / B.3
    print('\n## B.2 / B.3 Band b vs band 0 (mode none, alpha 255 in both), band b read at k = r_b - g + o\n')
    print('mean = mean |dRGB| (DN), p99 = 99th percentile of the per-pixel mean |dRGB|, ident = fraction of '
          'pixels with identical RGB. Ratio = mean(o) / mean(0). Lines restricted to those valid for every o.\n')
    stop = []
    b23 = {}
    for l in (LAYOUTS if 'none' in modes else ()):
        c = cubes[l, 'none']
        g, rows = c['g'], c['band_rows']
        ri = np.searchsorted(all_rows, rows)
        gs = g[2:-2]
        ref0 = stack[rows[0] - gs, ri[0]]
        table_rows = []
        for b in range(1, rows.size):
            st = {o: pair_stats(stack[rows[b] - gs + o, ri[b]], ref0) for o in OFFSETS}
            assert np.array_equal(stack[rows[b] - gs, ri[b]], c['cube'][2:-2, :, b])
            best = min(OFFSETS, key=lambda o: st[o]['mean'])
            if best != 0: stop.append((l, b, best))
            b23[l, b] = st
            table_rows.append([b, rows[b], f"{st[0]['mean']:.3f}", f"{st[0]['p99']:.2f}", f"{st[0]['identical']:.4f}"] +
                              [f"{st[o]['mean'] / st[0]['mean']:.2f}" for o in OFFSETS if o != 0] + [best])
        print(f'### {l}\n')
        print(md(['b', 'row', 'mean (o=0)', 'p99 (o=0)', 'ident (o=0)'] + [f'ratio o={o:+d}' for o in OFFSETS if o != 0] +
                 ['argmin o'], table_rows))
        print()
    if stop:
        print(f'**{"STOP: " if sat == 0 else ""}correct index is not the minimum for {stop}**')
        if sat == 0: return

    # ---------------- B.4
    print('## B.4 Phase correlation of slice b vs slice 0 (luminance x alpha)\n')
    print('Motion = apparent displacement of the scene from slice 0 to slice b (rows +down, cols +right), '
          'phase_correlation.phase_shift with upsampling 1000. Prediction (S9): -(alpha-weighted mean over the '
          'slice of drift_b - drift_0), drift = z = 0 frame-0 position of the sample minus (g, c).\n')
    shifts = dict(satellite=sat, method='phase_correlation.phase_shift(ref=slice 0, mov=slice b), luminance x alpha, '
                  'upsample 1000; motion = -shift (rows +down, cols +right)',
                  prediction='S9: -(sum w (drift_b - drift_0)) / sum w, w = alpha_0 alpha_b / 255^2, drift = '
                             'frame-0 position of the z = 0 ground seen by the cube sample minus (g, c)',
                  layouts={})
    # S10: parallax reference P_b = measured_0 - predicted_0 of the chief in mode none
    chief_path = Path(run) / 'cubes' / 'chief_shifts.json'
    chief = json.loads(chief_path.read_text()) if chief_path.exists() else None
    tol = dict(none=0.05, linear=0.02)
    failures = []
    if chief is not None:
        print(f'Parallax term P_b (S10) from {chief_path}. Requirement for deputies: none |meas - pred - P_b| < 0.05 px, '
              'linear |meas - P_b| < 0.02 px (rows and cols).\n')
    for l in LAYOUTS:
        shifts['layouts'][l] = {}
        for m in modes:
            c = cubes[l, m]
            g, rows, cube = c['g'], c['band_rows'], c['cube']
            dr, dc = drift_maps(sat, rows, cols, g, c['frame_index'], m, n_frames)
            ref = gray(cube[:, :, 0])
            out = dict(band_rows=rows.tolist(), measured_rows=[0.0], measured_cols=[0.0],
                       predicted_rows=[0.0], predicted_cols=[0.0])
            trs = []
            if chief is not None:
                cn = chief['layouts'][l]['none']
                assert cn['band_rows'] == rows.tolist()
                P = np.array([np.subtract(cn['measured_rows'], cn['predicted_rows']),
                              np.subtract(cn['measured_cols'], cn['predicted_cols'])])
            else:
                P = np.zeros((2, rows.size))
            out['parallax_rows'], out['parallax_cols'] = P[0].tolist(), P[1].tolist()
            out['residual_rows'], out['residual_cols'] = [0.0], [0.0]
            for b in range(1, rows.size):
                mot = -phase_shift(ref, gray(cube[:, :, b]), 1000)
                w = cube[:, :, 0, 3].astype(float) * cube[:, :, b, 3] / 255 ** 2
                pr = -np.sum(w * (dr[:, :, b] - dr[:, :, 0])) / w.sum()
                pc = -np.sum(w * (dc[:, :, b] - dc[:, :, 0])) / w.sum()
                out['measured_rows'].append(float(mot[0])); out['measured_cols'].append(float(mot[1]))
                out['predicted_rows'].append(float(pr)); out['predicted_cols'].append(float(pc))
                base = (pr, pc) if m == 'none' else (0.0, 0.0)
                res = (mot[0] - base[0] - P[0, b], mot[1] - base[1] - P[1, b])
                ok = max(abs(res[0]), abs(res[1])) < tol[m]
                if sat != 0 and chief is not None and not ok: failures.append((l, m, b, res))
                out['residual_rows'].append(float(res[0])); out['residual_cols'].append(float(res[1]))
                trs.append([b, rows[b], f'{mot[0]:+.4f}', f'{mot[1]:+.4f}', f'{pr:+.4f}', f'{pc:+.4f}',
                            f'{abs(mot[0] - pr):.4f}', f'{abs(mot[1] - pc):.4f}', f'{P[0, b]:+.4f}', f'{P[1, b]:+.4f}',
                            f'{res[0]:+.4f}', f'{res[1]:+.4f}', '-' if sat == 0 else ('yes' if ok else '**NO**')])
            shifts['layouts'][l][m] = out
            sd = Path(run) / 'cubes' / f'{l}_{m}'
            (sd / f'shifts_sat{sat:02d}.json').write_text(json.dumps(dict(shifts, layouts={l: {m: out}}), indent=1))
            mr, mc = np.abs(out['measured_rows']).max(), np.abs(out['measured_cols']).max()
            print(f'### {l} {m}: max |motion| rows {mr:.4f}, cols {mc:.4f}' +
                  (f" ({'< 0.01 px: yes' if max(mr, mc) < 0.01 else '>= 0.01 px'})" if l == 'compact' else '') + '\n')
            if l == 'diagnostic' or b < 40:
                print(md(['b', 'row', 'meas rows', 'meas cols', 'pred rows', 'pred cols', '|meas-pred| rows',
                          '|meas-pred| cols', 'P_b rows', 'P_b cols', 'residual rows', 'residual cols',
                          f'< {tol[m]} px'], trs))
            print()
    if args.save_shifts:
        if sat != 0: raise SystemExit('--save-shifts is for the chief only')
        path = Path(run) / 'cubes' / 'chief_shifts.json'
        path.write_text(json.dumps(shifts, indent=1))
        print(f'saved {path}\n')
    if failures:
        print(f'**STOP: requirement failed for {len(failures)} (layout, mode, band): {failures}**')
        return

    # ---------------- B.5
    print('## B.5 none vs linear (common lines)\n')
    tr = []
    for l in (LAYOUTS if len(modes) == 2 else ()):
        a, b = cubes[l, 'none'], cubes[l, 'linear']
        common = np.intersect1d(a['g'], b['g'])
        A = a['cube'][np.searchsorted(a['g'], common)].astype(np.int16)
        Bc = b['cube'][np.searchsorted(b['g'], common)].astype(np.int16)
        d = np.abs(A - Bc)
        m = (A[..., 3] == 255) & (Bc[..., 3] == 255)
        drgb = d[..., :3][m]
        tr.append([l, f'{common[0]}..{common[-1]} ({common.size})', int(drgb.max()), f'{drgb.mean():.4f}',
                   f'{np.mean(drgb == 0):.4f}', int(d[..., 3].max()), f'{d[..., 3].mean():.5f}', int(np.sum(np.any(d != 0, -1)))])
    print(md(['layout', 'lines', 'max |dRGB| (alpha 255)', 'mean |dRGB|', 'fraction dRGB = 0', 'max |dA|', 'mean |dA|',
              'samples differing (any channel)'], tr))

    # ---------------- B.6
    print('\n## B.6 Alpha of band b vs band 0\n')
    tr = []
    for (l, m), c in cubes.items():
        A = c['cube'][..., 3]
        diff = [int(np.sum(A[:, :, b] != A[:, :, 0])) for b in range(1, A.shape[2])]
        cov = [int(np.sum((A[:, :, b] > 0) != (A[:, :, 0] > 0))) for b in range(1, A.shape[2])]
        tr.append([l, m, A.shape[0] * A.shape[1], f'{min(diff)} .. {max(diff)}', f'{min(cov)} .. {max(cov)}',
                   f'{np.mean(A == 255):.4f}', f'{np.mean((A > 0) & (A < 255)):.5f}'])
    print(md(['layout', 'mode', 'pixels per slice', 'alpha differs (min .. max over b)', 'alpha>0 differs (min .. max)',
              'fraction alpha 255', 'fraction 0 < alpha < 255'], tr))

    # ---------------- B.7
    print('\n## B.7 Previews\n')
    for (l, m), c in cubes.items():
        d = Path(run) / 'cubes' / f'{l}_{m}' / 'preview'
        d.mkdir(exist_ok=True)
        cube = c['cube']
        step = max(1, int(np.ceil(max(cube.shape[:2]) / 2048)))
        first, last = cube[::step, ::step, 0, :3], cube[::step, ::step, -1, :3]
        save_png(d / f'sat{sat:02d}_band00_rgb.png', np.ascontiguousarray(first))
        save_png(d / f'sat{sat:02d}_band{cube.shape[2] - 1:02d}_rgb.png', np.ascontiguousarray(last))
        diff = np.clip(np.abs(last.astype(np.int16) - first.astype(np.int16)) * 10, 0, 255).astype(np.uint8)
        save_png(d / f'sat{sat:02d}_absdiff_last_vs_band00_x10.png', diff)
        print(f'- {d} (downsample {step})')


if __name__ == '__main__':
    main()
