"""Step 13 C: geometric map x_s(k, r_b, c) of satellites 0-4 on the real poses, for the
band layouts of cube_stacking.LAYOUTS. Reads stacking_inputs.npz and poses.jsonl only
(no PNG); prints a markdown report to stdout and writes nothing.

Run in the conda environment orbits:
    python scripts/report_cube_geometry.py [--workers 5] > report.md
"""
import argparse
from multiprocessing import Pool

import numpy as np

import cube_stacking as cs
import stacking_geometry as sg

SATS = (0, 1, 2, 3, 4)
HEIGHTS = (30.0, 100.0, 300.0)
NPZ = sg.RUN_DIR / 'stacking_inputs.npz'
N = cs.N_FRAMES


def scene_cols(sat):
    with np.load(NPZ) as d:
        i = int(np.flatnonzero(d['alpha_satellites'] == sat)[0])
        any_col = d['alpha_col_any'][i].any(0)
    cols = np.flatnonzero(any_col)
    assert cols.size == cols[-1] - cols[0] + 1, 'scene columns not contiguous'
    return cols


def fit(k, x):
    """x = a + slope k; returns s = -slope and the max |residual|."""
    p = np.polyfit(k, x, 1)
    return -p[0], np.abs(x - np.polyval(p, k)).max()


def drift(x_kb, rows, g):
    """x(k = r_b - g, r_b) - g, [G, B, ...] from x[k, b, ...]."""
    k = cs.integer_index(g, rows)
    return x_kb[k, np.arange(rows.size)[None, :]] - g.reshape((-1, 1) + (1,) * (x_kb.ndim - 2))


def analyse(sat):
    k = np.arange(N)
    cols = scene_cols(sat)
    cc = int((cols[0] + cols[-1]) // 2)
    ic = int(np.flatnonzero(cols == cc)[0])
    out = dict(sat=sat, cols=(int(cols[0]), int(cols[-1])), cc=cc, layouts={})
    with np.load(NPZ) as d:
        motion = d['image_motion_rows_px'][sat]
    out['motion_mean'] = float(motion.mean())
    rc, ccen = sg.IMAGE_CENTRE
    x_axis = cs.ground_line_map(sat, k, [rc], [ccen, cc])
    out['s_centre_row'] = {name: fit(k, x_axis[:, 0, j])[0] for j, name in enumerate(('image centre col', 'scene centre col'))}
    # image_motion_rows_px is in frame-(k+1) pixels, x in frame-0 pixels: per-frame advance
    # dk = x(k) - x(k+1) must equal J(k+1) * motion(k), J = dx/dr of the map frame k+1 -> 0
    dk = x_axis[:-1, 0, 0] - x_axis[1:, 0, 0]
    J = (cs.ground_line_map(sat, k[1:], [rc + 0.5], [ccen]) - cs.ground_line_map(sat, k[1:], [rc - 0.5], [ccen]))[:, 0, 0]
    out['J_range'] = (float(J.min()), float(J.max()))
    out['dk_vs_motion'] = float(np.abs(dk - J * motion).max())
    for layout in cs.LAYOUTS:
        rows = cs.band_rows(layout)
        B = rows.size
        x = cs.ground_line_map(sat, k, rows, cols)                    # [N, B, C]
        res = {}
        # C.1
        fits = [fit(k, x[:, b, ic]) for b in range(B)]
        res['s'] = np.array([f[0] for f in fits]); res['fit_resid'] = np.array([f[1] for f in fits])
        # C.3
        res['col_dep'] = {side: float(np.abs(x[:, :, j] - x[:, :, ic]).max()) for side, j in (('left', 0), ('right', -1))}
        # C.4
        gi0, gi1 = cs.complete_lines_integer(rows, N)
        g_cand = np.arange(rows[-1] - N - 5, rows[0] + 6)
        ks_all = cs.fractional_index_map(x, g_cand)                  # [G, C, B]
        res['int_lines'] = (gi0, gi1)
        res['frac_lines'] = cs.complete_lines_fractional(ks_all, g_cand)
        res['frac_lines_centre'] = cs.complete_lines_fractional(ks_all[:, ic], g_cand)
        g_f0, g_f1 = res['frac_lines']
        sel = (g_cand >= g_f0) & (g_cand <= g_f1)
        ks = ks_all[sel]
        res['kstar_col_dep'] = float(np.nanmax(np.abs(ks - ks[:, ic:ic + 1])))
        del ks_all
        # C.5 (centre column, and all columns)
        g = np.arange(gi0, gi1 + 1)
        dr_c = drift(x[:, :, ic], rows, g)                            # [G, B]
        dr_all = drift(x, rows, g)                                    # [G, B, C]
        res['drift_abs_band'] = np.abs(dr_c).max(0)
        res['drift_b2b_band'] = np.abs(dr_c - dr_c[:, :1]).max(0)
        res['drift_b2b_all_cols'] = float(np.abs(dr_all - dr_all[:, :1]).max())
        res['drift_abs_all_cols'] = float(np.abs(dr_all).max())
        del x, dr_all
        # C.6
        res['parallax'] = {}
        for h in HEIGHTS:
            xh = cs.ground_line_map(sat, k, rows, [cc], height_m=h)[:, :, 0]
            dh = drift(xh, rows, g)
            res['parallax'][h] = dict(abs=float(np.abs(dh - dr_c).max()),
                                      b2b=float(np.abs((dh - dh[:, :1]) - (dr_c - dr_c[:, :1])).max()),
                                      b2b_total=float(np.abs(dh - dh[:, :1]).max()))
        # C.7
        C = cols.size
        res['size_none'] = (gi1 - gi0 + 1) * C * B * 4
        res['size_linear'] = (g_f1 - g_f0 + 1) * C * B * 4
        res['kstar_bytes_f64'] = (g_f1 - g_f0 + 1) * C * B * 8
        res['G_frac'] = g_f1 - g_f0 + 1
        out['layouts'][layout] = res
    return out


def md(header, rows):
    return '\n'.join(['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)] +
                     ['| ' + ' | '.join(map(str, r)) + ' |' for r in rows])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=5)
    args = ap.parse_args()
    with Pool(args.workers) as pool:
        R = pool.map(analyse, SATS)
    L = list(cs.LAYOUTS)
    print('# Step 13 C: geometric map on the real poses\n')
    print('Scene columns (S3) and centre column used:\n')
    print(md(['sat', 'cols', 'C', 'centre col'], [[r['sat'], '%d..%d' % r['cols'], r['cols'][1] - r['cols'][0] + 1, r['cc']] for r in R]))

    print('\n## C.1 Linear fit x = a - s k at the scene centre column (all 1000 frames)\n')
    rows = []
    for r in R:
        for lay in L:
            q = r['layouts'][lay]
            rows.append([r['sat'], lay, f"{q['s'].min():.7f} .. {q['s'].max():.7f}", f"{q['s'].max() - q['s'].min():.2e}",
                         f"{q['fit_resid'].max():.2e}"])
    print(md(['sat', 'layout', 's over bands (min .. max)', 'spread', 'max fit residual (px)'], rows))
    print('\nDiagnostic layout, s per band:\n')
    rows_d = cs.band_rows('diagnostic')
    print(md(['sat'] + [f'r={x}' for x in rows_d],
             [[r['sat']] + [f'{v:.7f}' for v in r['layouts']['diagnostic']['s']] for r in R]))

    print('\n## C.2 s at the centre row (1535.5) vs mean image_motion_rows_px\n')
    rows = []
    for r in R:
        a, b = r['s_centre_row']['image centre col'], r['s_centre_row']['scene centre col']
        rows.append([r['sat'], f'{a:.7f}', f'{b:.7f}', f"{r['motion_mean']:.7f}", f"{a - r['motion_mean']:+.2e}",
                     f"{b - r['motion_mean']:+.2e}", 'yes' if max(abs(a - r['motion_mean']), abs(b - r['motion_mean'])) < 1e-5 else 'NO'])
    print(md(['sat', 's (col 2047.5)', 's (scene centre col)', 'mean image_motion_rows_px', 'diff (2047.5)', 'diff (scene centre)', '< 1e-5'], rows))
    print('\nUnit check: image_motion_rows_px is in frame-(k+1) pixels, x in frame-0 pixels. Per-frame advance '
          'dk = x(k) - x(k+1) at the image centre vs J(k+1) * image_motion_rows_px[k], J = dx/dr of the map frame k+1 -> 0:\n')
    print(md(['sat', 'J min .. max', 'max |dk - J motion| (px)'],
             [[r['sat'], f"{r['J_range'][0]:.6f} .. {r['J_range'][1]:.6f}", f"{r['dk_vs_motion']:.1e}"] for r in R]))

    print('\n## C.3 Column dependence: max_k,b |x(k, r_b, c) - x(k, r_b, c_centre)| at the scene edges\n')
    rows = []
    for r in R:
        for lay in L:
            q = r['layouts'][lay]
            m = max(q['col_dep'].values())
            rows.append([r['sat'], lay, f"{q['col_dep']['left']:.4f}", f"{q['col_dep']['right']:.4f}",
                         f"{q['kstar_col_dep']:.4f}", 'per column' if m > 0.01 else 'one column OK'])
    print(md(['sat', 'layout', 'left edge (px)', 'right edge (px)', 'max |k*(c) - k*(c_centre)| (frames)', 'k*'], rows))

    print('\n## C.4 Complete lines\n')
    rows = []
    for r in R:
        for lay in L:
            q = r['layouts'][lay]
            (a, b), (c, d), (e, f) = q['int_lines'], q['frac_lines'], q['frac_lines_centre']
            rows.append([r['sat'], lay, f'{a}..{b} ({b - a + 1})', f'{c}..{d} ({d - c + 1})', f'{e}..{f} ({f - e + 1})'])
    print(md(['sat', 'layout', 'integer', 'fractional (all columns)', 'fractional (centre column only)'], rows))

    print('\n## C.5 Predicted drift in mode none: x(k = r_b - g, r_b) - g, max over g\n')
    print('"abs" = drift of the band against the frame-0 grid g; "band-to-band" = drift of band b minus band 0 '
          '(the misregistration between bands inside the cube). Centre column; last column = max over all scene columns.\n')
    rows = []
    for r in R:
        for lay in L:
            q = r['layouts'][lay]
            rows.append([r['sat'], lay, f"{q['drift_abs_band'].max():.4f}", f"{q['drift_b2b_band'].max():.4f}",
                         f"{q['drift_abs_all_cols']:.4f}", f"{q['drift_b2b_all_cols']:.4f}"])
    print(md(['sat', 'layout', 'abs max (px)', 'band-to-band max (px)', 'abs, all cols', 'band-to-band, all cols'], rows))
    print('\nDiagnostic layout, band-to-band drift per band (centre column, px):\n')
    print(md(['sat'] + [f'r={x}' for x in rows_d],
             [[r['sat']] + [f'{v:.4f}' for v in r['layouts']['diagnostic']['drift_b2b_band']] for r in R]))
    print('\nDiagnostic layout, abs drift per band (centre column, px):\n')
    print(md(['sat'] + [f'r={x}' for x in rows_d],
             [[r['sat']] + [f'{v:.4f}' for v in r['layouts']['diagnostic']['drift_abs_band']] for r in R]))

    print('\n## C.6 Within-satellite parallax: change of the C.5 drift with the ground at z = h (centre column)\n')
    rows = []
    for r in R:
        for lay in L:
            q = r['layouts'][lay]['parallax']
            rows.append([r['sat'], lay] + [f"{q[h]['abs']:.4f} / {q[h]['b2b']:.4f}" for h in HEIGHTS])
    print(md(['sat', 'layout'] + [f'z = {h:.0f} m: abs / band-to-band (px)' for h in HEIGHTS], rows))

    print('\n## C.7 Expected cube sizes (G x C x B x 4 bytes)\n')
    rows = []
    tot = {}
    for r in R:
        for lay in L:
            q = r['layouts'][lay]
            C = r['cols'][1] - r['cols'][0] + 1
            B = len(cs.LAYOUTS[lay])
            a, b = q['int_lines']
            rows.append([r['sat'], lay, f'{b - a + 1} x {C} x {B} x 4', f"{q['size_none'] / 1e6:.1f}",
                         f"{q['G_frac']} x {C} x {B} x 4", f"{q['size_linear'] / 1e6:.1f}", f"{q['kstar_bytes_f64'] / 1e6:.1f}"])
            tot[lay, 'none'] = tot.get((lay, 'none'), 0) + q['size_none']
            tot[lay, 'linear'] = tot.get((lay, 'linear'), 0) + q['size_linear'] + q['kstar_bytes_f64']
    print(md(['sat', 'layout', 'none shape', 'none MB', 'linear shape', 'linear MB', 'k* [G,C,B] float64 MB'], rows))
    print('\nTotals (uncompressed, cube + k* for linear): ' +
          ', '.join(f'{lay} {mode} {v / 1e9:.2f} GB' for (lay, mode), v in tot.items()))


if __name__ == '__main__':
    main()
