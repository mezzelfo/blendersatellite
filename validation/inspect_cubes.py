"""Inspect and compare the cube modes (none, linear, bilinear) of a run (step 25).

Reads <run>/cubes/raw_bands_<mode>/cube_satSS.npz (build_cubes.py) plus <run>/raw/bands.json and
<run>/raw/sat_SS.json, and writes figures and tables to <run>/cubes/inspection/ only. CPU only,
parallel over satellites.

Per satellite and mode:
  mosaic_satSS_<mode>.png     all bands, one panel each (8 rows x 4 columns), luminance, one stretch
                              per satellite and mode (2-98 percentile of the opaque cube)
  composite_satSS_<mode>.png  false colour R, G, B = luminance of the last, middle and first band,
                              plus the rendered RGB of the middle band (bands.json has no wavelengths,
                              so no wavelength-based pseudo-RGB can be made)
Per satellite:
  modes_satSS.png             first, middle and last band for none / linear / bilinear and the
                              differences linear - none, bilinear - linear (DN, symmetric scale),
                              on a crop at the left edge and one at the centre of the scene
  spectra_satSS.png           mean luminance per band of the region and of fixed points, one line per mode
  shifts_satSS.png            band-to-band registration: displacement of every band against the
                              middle band, the three modes overlaid, with the geometric prediction
Per mode:
  overview_<mode>.png         false-colour composite of every satellite, one strip per satellite
Tables: registration.csv, registration.json (with per-satellite details and cube inventory), points.json.

Registration: luminance (Rec.709 of the RGB DN), gradient magnitude, S11 estimator (Hann window
and Gaussian low-pass sigma 0.05 on the cross-power spectrum, phase_correlation.phase_shift).
The displacement reported is the scene motion from the reference band to band b (rows +down,
cols +right), i.e. -phase_shift(ref, band). Every mode of a satellite is measured on the same
region: the cube lines common to the three modes and the longest run of columns that are
opaque (alpha 255) in at least 90 % of the lines x bands of every mode; luminance is
multiplied by alpha (as phase_correlation.masked_gray). Besides the whole region, three column blocks
(left, centre, right) are measured, because the column drift of oblique views is a keystone
(opposite signs at the two edges) that a single global shift averages out.

Predicted displacement (from the stored frame indices, no geometry recomputed):
  rows, none: band b at line g samples frame r_b - g instead of k*(g, c, b) (linear index), so it
              shows ground line g + s (k* - (r_b - g)), s = rows per frame; displacement
              -(d_b - d_ref) with d the mean of s (k* - k_int) over the region.
  cols, none and linear: the ground point of frame-0 pixel (g, c) crosses band row r_b at column
              c* (bilinear index), the sample is taken at column c, so the band shows the point of
              frame-0 column c - (c* - c); displacement +(delta_b - delta_ref), delta = mean(c* - c).
  bilinear: zero by construction; linear rows: zero.

    python validation/inspect_cubes.py --run output/orbits_melbourne/<run> --workers 7
"""
import argparse
import csv
import json
import os
from multiprocessing import Pool
from pathlib import Path

import numpy as np

os.environ.setdefault('MPLBACKEND', 'Agg')
import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from scipy.ndimage import uniform_filter  # noqa: E402

from phase_correlation import phase_shift  # noqa: E402

MODES = ('none', 'linear', 'bilinear')
MODE_COLOR = {'none': 'tab:red', 'linear': 'tab:blue', 'bilinear': 'tab:green'}
SIGMA = 0.05              # S11 low-pass, as validate_async_run.py
REC709 = np.array([0.2126, 0.7152, 0.0722])
OPAQUE_FRACTION = 0.9     # column criterion of the common region
CROP = 400                # columns per crop in the mode comparison
BLOCKS = ('left', 'centre', 'right')


# ---------------------------------------------------------------------------------------- loading
def load_cube(run, mode, sat):
    with np.load(Path(run) / 'cubes' / f'raw_bands_{mode}' / f'cube_sat{sat:02d}.npz') as d:
        return dict(cube=d['cube'], g=d['g'], cols=d['cols'], band_rows=d['band_rows'],
                    index=d['frame_index'], meta=json.loads(str(d['metadata'])))


def luminance(rgba):
    """float64 luminance in DN (0-255) of uint8 RGBA [..., 4]."""
    return rgba[..., :3].astype(np.float64) @ REC709


def common_region(cubes):
    """Lines and columns common to every mode, and the longest run of columns that are opaque
    (alpha 255) in at least OPAQUE_FRACTION of the lines x bands of every mode (the scene has a few
    scattered transparent pixels, more in fore/aft views). Returns (g array, cols array,
    {mode: (line idx, col idx)})."""
    g = cubes[MODES[0]]['g']
    cols = cubes[MODES[0]]['cols']
    for m in MODES[1:]:
        g = np.intersect1d(g, cubes[m]['g'])
        cols = np.intersect1d(cols, cubes[m]['cols'])
    opaque = np.ones(cols.size, bool)
    idx = {}
    for m in MODES:
        gi = np.searchsorted(cubes[m]['g'], g)
        ci = np.searchsorted(cubes[m]['cols'], cols)
        idx[m] = (gi, ci)
        a = cubes[m]['cube'][gi][:, ci][..., 3]
        opaque &= (a == 255).mean(axis=(0, 2)) >= OPAQUE_FRACTION
    # longest contiguous run of opaque columns
    best, start, run_start = (0, 0), None, None
    for i, ok in enumerate(np.append(opaque, False)):
        if ok and run_start is None: run_start = i
        if not ok and run_start is not None:
            if i - run_start > best[1] - best[0]: best = (run_start, i)
            run_start = None
    sel = np.arange(best[0], best[1])
    cols = cols[sel]
    idx = {m: (gi, ci[sel]) for m, (gi, ci) in idx.items()}
    return g, cols, idx


# ------------------------------------------------------------------------------------ registration
def gradient_magnitude(img):
    gr, gc = np.gradient(img)
    return np.hypot(gr, gc)


def displacement(ref, mov):
    """Scene motion (rows, cols) from ref to mov (S11 estimator)."""
    return -phase_shift(ref, mov, 1000, sigma=SIGMA, hann=True)


def block_slices(n):
    e = np.linspace(0, n, 4).round().astype(int)
    return {'all': slice(0, n), **{b: slice(e[i], e[i + 1]) for i, b in enumerate(BLOCKS)}}


def registration(lum, ref_band):
    """lum [G, C, B] of the region -> {block: float [B, 2]} displacement of every band."""
    out = {}
    for name, sl in block_slices(lum.shape[1]).items():
        grads = [gradient_magnitude(lum[:, sl, b]) for b in range(lum.shape[2])]
        out[name] = np.array([displacement(grads[ref_band], gb) for gb in grads])
    return out


def predicted(cubes, g, cols, ref_band):
    """{mode: {block: float [B, 2]}} geometric prediction (see module doc)."""
    lin, bil = cubes['linear'], cubes['bilinear']
    gi_l = np.searchsorted(lin['g'], g); ci_l = np.searchsorted(lin['cols'], cols)
    gi_b = np.searchsorted(bil['g'], g); ci_b = np.searchsorted(bil['cols'], cols)
    k_star = lin['index'][gi_l][:, ci_l].astype(np.float64)                     # [G, C, B]
    rows = lin['band_rows'].astype(np.float64)
    k_int = rows[None, None, :] - g[:, None, None]
    s = -1.0 / np.mean(np.diff(k_star, axis=0))                                 # rows per frame
    d_row = s * (k_star - k_int)                                                # [G, C, B]
    c_star = bil['index'][1][gi_b][:, ci_b].astype(np.float64)
    delta = c_star - cols[None, :, None].astype(np.float64)                     # [G, C, B]
    out = {m: {} for m in MODES}
    for name, sl in block_slices(cols.size).items():
        dr = d_row[:, sl].mean(axis=(0, 1)); dr = -(dr - dr[ref_band])
        dc = delta[:, sl].mean(axis=(0, 1)); dc = dc - dc[ref_band]
        z = np.zeros_like(dr)
        out['none'][name] = np.stack([dr, dc], 1)
        out['linear'][name] = np.stack([z, dc], 1)
        out['bilinear'][name] = np.stack([z, z], 1)
    return out, float(s)


# ------------------------------------------------------------------------------------- points
def pick_points(rgba, margin=4):
    """Fixed points on one band image rgba [G, C, 4] (uint8): homogeneous 5x5 neighbourhoods
    chosen by simple colour rules. Returns {name: (line idx, col idx, note)}."""
    rgb = rgba[..., :3].astype(np.float64)
    sm = np.stack([uniform_filter(rgb[..., i], 5) for i in range(3)], -1)
    lum = sm @ REC709
    sq = uniform_filter((rgb @ REC709) ** 2, 5)
    std = np.sqrt(np.maximum(sq - lum ** 2, 0))
    valid = np.zeros(lum.shape, bool)
    valid[margin:-margin, margin:-margin] = True
    valid &= uniform_filter((rgba[..., 3] == 255).astype(np.float64), 5) > 0.999   # fully opaque 5x5
    valid &= std < np.percentile(std[valid], 30)                                # homogeneous
    r, gr, b = sm[..., 0], sm[..., 1], sm[..., 2]
    sat = sm.max(-1) - sm.min(-1)
    pts = {}

    def take(name, score, mask, note):
        sc = np.where(valid & mask, score, -np.inf)
        if not np.isfinite(sc).any(): return
        i, j = np.unravel_index(np.argmax(sc), sc.shape)
        pts[name] = (int(i), int(j), note)

    take('bright roof', lum, np.ones_like(valid), 'highest 5x5 luminance among homogeneous pixels')
    take('road', -sat, np.abs(lum - np.median(lum[valid])) < 0.15 * np.median(lum[valid]),
         'least saturated homogeneous pixel within 15% of the median luminance (grey surface)')
    take('vegetation', 2 * gr - r - b, (gr > r) & (gr > b), 'largest excess green 2G-R-B, G > R and G > B')
    water = (b > r) & (lum < np.percentile(lum[valid], 5))
    take('water', -lum, water, 'darkest homogeneous pixel with B > R in the darkest 5%')
    return pts


# ------------------------------------------------------------------------------------- figures
def stretch(x, lo, hi):
    return np.clip((x - lo) / max(hi - lo, 1e-9), 0, 1)


def masked(img, alpha):
    return np.ma.masked_where(alpha == 0, img)


def fig_mosaic(path, cube, rows_b, sat, role, mode):
    lum = luminance(cube)
    a = cube[..., 3]
    lo, hi = np.percentile(lum[a > 0], [2, 98])
    B = cube.shape[2]
    G, C = cube.shape[:2]
    fig, axes = plt.subplots(8, 4, figsize=(24, 8 * (6 * G / C + 0.45)), squeeze=False)
    for b, ax in enumerate(axes.ravel()):
        if b >= B: ax.axis('off'); continue
        ax.imshow(masked(lum[:, :, b], a[:, :, b]), cmap='gray', vmin=lo, vmax=hi, interpolation='nearest')
        ax.set_title(f'band {b}, row {rows_b[b]} (no wavelength)', fontsize=9)
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f'sat {sat} ({role}), mode {mode}: luminance of every band, stretch 2-98 % = {lo:.0f}-{hi:.0f} DN'
                 f' ({G} lines x {C} cols)', fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=110); plt.close(fig)


def false_colour(cube, bands):
    """[G, C, 3] float composite R, G, B = luminance of bands, each stretched 2-98 %; alpha [G, C]."""
    a = (cube[:, :, bands, 3] > 0).all(-1)
    chans = []
    for b in bands:
        lum = luminance(cube[:, :, b])
        lo, hi = np.percentile(lum[a], [2, 98])
        chans.append(stretch(lum, lo, hi))
    img = np.stack(chans, -1)
    img[~a] = 1.0
    return img, a


def true_colour(rgba):
    a = rgba[..., 3] > 0
    rgb = rgba[..., :3].astype(np.float64)
    lo, hi = np.percentile(rgb[a], [1, 99])
    img = stretch(rgb, lo, hi)
    img[~a] = 1.0
    return img


def fig_composite(path, cube, fc_bands, rows_b, sat, role, mode):
    fc, _ = false_colour(cube, fc_bands)
    mid = fc_bands[1]
    tc = true_colour(cube[:, :, mid])
    G, C = cube.shape[:2]
    fig, axes = plt.subplots(2, 1, figsize=(22, 2 * (22 * G / C + 0.5)))
    axes[0].imshow(fc, interpolation='nearest')
    axes[0].set_title(f'false colour R/G/B = band {fc_bands[0]} / {fc_bands[1]} / {fc_bands[2]} '
                      f'(rows {rows_b[fc_bands[0]]} / {rows_b[fc_bands[1]]} / {rows_b[fc_bands[2]]}), luminance, '
                      'each 2-98 %; colour fringes = band-to-band misregistration')
    axes[1].imshow(tc, interpolation='nearest')
    axes[1].set_title(f'rendered RGB of band {mid} (row {rows_b[mid]}), 1-99 % '
                      '(no wavelengths in bands.json: no 640/550/470 nm pseudo-RGB possible)')
    for ax in axes: ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f'sat {sat} ({role}), mode {mode}')
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def fig_modes(path, region_lum, fc_bands, rows_b, sat, role, g):
    """region_lum {mode: [G, C, B]} on the common region."""
    C = region_lum['none'].shape[1]
    crops = {'left edge': slice(0, min(CROP, C)),
             'centre': slice(max(0, C // 2 - CROP // 2), min(C, C // 2 + CROP // 2))}
    d1 = region_lum['linear'] - region_lum['none']
    d2 = region_lum['bilinear'] - region_lum['linear']
    # one symmetric scale per difference type: linear - none is ~50x larger than bilinear - linear
    vd1 = max(float(np.percentile(np.abs(d1[:, :, fc_bands]), 99)), 1.0)
    vd2 = max(float(np.percentile(np.abs(d2[:, :, fc_bands]), 99)), 1.0)
    allv = np.concatenate([region_lum[m][:, :, fc_bands].ravel() for m in MODES])
    lo, hi = np.percentile(allv, [2, 98])
    nrow = len(fc_bands) * len(crops)
    G = region_lum['none'].shape[0]
    fig, axes = plt.subplots(nrow, 5, figsize=(26, nrow * (5 * G / CROP + 0.55)), squeeze=False)
    r, ims = 0, {}
    for cname, sl in crops.items():
        for b in fc_bands:
            panels = [(region_lum[m][:, sl, b], m, 'gray', lo, hi) for m in MODES]
            panels += [(d1[:, sl, b], 'linear - none', 'RdBu_r', -vd1, vd1),
                       (d2[:, sl, b], 'bilinear - linear', 'RdBu_r', -vd2, vd2)]
            for j, (img, name, cmap, vmin, vmax) in enumerate(panels):
                ax = axes[r, j]
                im = ax.imshow(img, cmap=cmap, vmin=vmin, vmax=vmax, interpolation='nearest')
                ims[j] = im
                ax.set_title(f'{name} | band {b} (row {rows_b[b]}) | {cname}', fontsize=8)
                ax.set_xticks([]); ax.set_yticks([])
            r += 1
    fig.colorbar(ims[3], ax=axes[:, 3].tolist(), shrink=0.5, location='bottom', label='linear - none [DN]')
    fig.colorbar(ims[4], ax=axes[:, 4].tolist(), shrink=0.5, location='bottom', label='bilinear - linear [DN]')
    fig.suptitle(f'sat {sat} ({role}): modes on the common region, lines {g[0]}-{g[-1]}, crops of {CROP} cols; '
                 f'gray 2-98 % = {lo:.0f}-{hi:.0f} DN; differences (p99, symmetric): linear - none +-{vd1:.1f} DN, '
                 f'bilinear - linear +-{vd2:.1f} DN')
    fig.savefig(path, dpi=100, bbox_inches='tight'); plt.close(fig)
    return dict(diff_scale_linear_minus_none_DN=vd1, diff_scale_bilinear_minus_linear_DN=vd2,
                mean_abs_linear_minus_none_DN=float(np.abs(d1).mean()),
                mean_abs_bilinear_minus_linear_DN=float(np.abs(d2).mean()))


def fig_spectra(path, region_lum, points, sat, role):
    names = ['region mean'] + list(points)
    fig, axes = plt.subplots(1, len(names), figsize=(4.2 * len(names), 3.6), squeeze=False)
    for ax, name in zip(axes[0], names):
        for m in MODES:
            L = region_lum[m]
            if name == 'region mean':
                y = L.mean(axis=(0, 1))
            else:
                i, j, _ = points[name]
                y = L[max(i - 1, 0):i + 2, max(j - 1, 0):j + 2].mean(axis=(0, 1))
            ax.plot(np.arange(L.shape[2]), y, color=MODE_COLOR[m], label=m, lw=1.2)
        sub = '' if name == 'region mean' else f' (line {points[name][0]}, col {points[name][1]})'
        ax.set_title(name + sub, fontsize=9)
        ax.set_xlabel('band'); ax.grid(alpha=.3)
    axes[0, 0].set_ylabel('luminance [DN] (3x3 mean at points)')
    axes[0, 0].legend(fontsize=8)
    fig.suptitle(f'sat {sat} ({role}): luminance per band (same rendered RGB in every band: '
                 'variation = misregistration, not spectrum); region indices')
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def fig_shifts(path, meas, pred, sat, role, ref_band):
    fig, axes = plt.subplots(2, 4, figsize=(20, 7), sharex=True)
    for j, block in enumerate(['all', *BLOCKS]):
        for i, comp in enumerate(['rows', 'cols']):
            ax = axes[i, j]
            for m in MODES:
                bands = np.arange(meas[m][block].shape[0])
                ax.plot(bands, meas[m][block][:, i], 'o-', ms=3, color=MODE_COLOR[m], label=f'{m} measured')
                ax.plot(bands, pred[m][block][:, i], '--', color=MODE_COLOR[m], lw=1, label=f'{m} predicted')
            ax.axvline(ref_band, color='k', lw=.5)
            ax.set_title(f'{comp}, columns: {block}', fontsize=9); ax.grid(alpha=.3)
            if j == 0: ax.set_ylabel(f'displacement {comp} [px]')
            if i == 1: ax.set_xlabel('band')
    axes[0, 0].legend(fontsize=7)
    fig.suptitle(f'sat {sat} ({role}): displacement of every band vs band {ref_band} (gradient, S11 phase correlation)')
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ------------------------------------------------------------------------------------- per sat
def process(args):
    run, sat, out, roles = args
    role = roles[sat]
    cubes = {m: load_cube(run, m, sat) for m in MODES}
    rows_b = cubes['none']['band_rows']
    B = rows_b.size
    ref_band = B // 2
    fc_bands = [0, ref_band, B - 1]
    composites = {}
    for m in MODES:
        c = cubes[m]['cube']
        fig_mosaic(out / f'mosaic_sat{sat:02d}_{m}.png', c, rows_b, sat, role, m)
        fig_composite(out / f'composite_sat{sat:02d}_{m}.png', c, fc_bands, rows_b, sat, role, m)
        composites[m] = false_colour(c, fc_bands)[0]
    g, cols, idx = common_region(cubes)
    region = {m: cubes[m]['cube'][idx[m][0]][:, idx[m][1]] for m in MODES}
    region_lum = {m: luminance(region[m]) for m in MODES}
    region_alpha = {m: region[m][..., 3].astype(np.float64) / 255 for m in MODES}
    diff_stats = fig_modes(out / f'modes_sat{sat:02d}.png', region_lum, fc_bands, rows_b, sat, role, g)
    pts = pick_points(region['linear'][:, :, ref_band])
    fig_spectra(out / f'spectra_sat{sat:02d}.png', region_lum, pts, sat, role)
    meas = {m: registration(region_lum[m] * region_alpha[m], ref_band) for m in MODES}
    pred, s_rate = predicted(cubes, g, cols, ref_band)
    fig_shifts(out / f'shifts_sat{sat:02d}.png', meas, pred, sat, role, ref_band)
    rows_out = []
    for m in MODES:
        rec = dict(satellite=sat, role=role, mode=m)
        for block in ['all', *BLOCKS]:
            d = np.delete(meas[m][block], ref_band, axis=0)
            p = np.delete(pred[m][block], ref_band, axis=0)
            rec[f'{block}_max_abs_row'] = float(np.abs(d[:, 0]).max())
            rec[f'{block}_max_abs_col'] = float(np.abs(d[:, 1]).max())
            rec[f'{block}_rms'] = float(np.sqrt(np.mean(np.sum(d ** 2, 1))))
            rec[f'{block}_pred_max_abs_row'] = float(np.abs(p[:, 0]).max())
            rec[f'{block}_pred_max_abs_col'] = float(np.abs(p[:, 1]).max())
            rec[f'{block}_max_abs_residual_row'] = float(np.abs(d[:, 0] - p[:, 0]).max())
            rec[f'{block}_max_abs_residual_col'] = float(np.abs(d[:, 1] - p[:, 1]).max())
        rows_out.append(rec)
    inv = {m: dict(shape=list(cubes[m]['cube'].shape), g_range=[int(cubes[m]['g'][0]), int(cubes[m]['g'][-1])],
                   cols_range=[int(cubes[m]['cols'][0]), int(cubes[m]['cols'][-1])]) for m in MODES}
    detail = dict(satellite=sat, role=role, ref_band=ref_band, false_colour_bands=fc_bands,
                  false_colour_rows=[int(rows_b[b]) for b in fc_bands], rows_per_frame=s_rate,
                  region=dict(g=[int(g[0]), int(g[-1])], lines=int(g.size), cols=[int(cols[0]), int(cols[-1])],
                              n_cols=int(cols.size)),
                  points={k: dict(line_index=v[0], col_index=v[1], g=int(g[v[0]]), col=int(cols[v[1]]), rule=v[2])
                          for k, v in pts.items()},
                  differences=diff_stats, inventory=inv,
                  measured={m: {b: meas[m][b].round(4).tolist() for b in meas[m]} for m in MODES},
                  predicted={m: {b: pred[m][b].round(4).tolist() for b in pred[m]} for m in MODES})
    print(f'sat {sat} ({role}) done', flush=True)
    return rows_out, detail, composites


def fig_overview(path, composites, roles, mode, sats):
    C = max(composites[s].shape[1] for s in sats)
    hs = [composites[s].shape[0] for s in sats]
    fig, axes = plt.subplots(len(sats), 1, figsize=(22, sum(22 * h / C + 0.45 for h in hs)),
                             gridspec_kw=dict(height_ratios=hs))
    for ax, s in zip(np.atleast_1d(axes), sats):
        ax.imshow(composites[s], interpolation='nearest')
        ax.set_title(f'sat {s} ({roles[s]})', fontsize=10, loc='left')
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f'mode {mode}: false colour R/G/B = luminance of the first / middle / last band '
                 '(rows 1020 / 1492 / 1932 for the raw_bands layout), each 2-98 %', fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.98)); fig.savefig(path, dpi=110); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--modes', nargs='+', default=list(MODES), choices=MODES,
                    help='the comparisons need all three; kept for the interface')
    ap.add_argument('--satellites', type=int, nargs='+', default=list(range(7)))
    ap.add_argument('--out', type=Path, help='default <run>/cubes/inspection')
    ap.add_argument('--workers', type=int, default=7)
    args = ap.parse_args()
    if set(args.modes) != set(MODES): raise SystemExit('the mode comparison needs none, linear and bilinear')
    out = args.out or args.run / 'cubes' / 'inspection'
    out.mkdir(parents=True, exist_ok=True)
    roles = {s: json.loads((args.run / 'raw' / f'sat_{s:02d}.json').read_text())['acquisition_role']
             for s in args.satellites}
    jobs = [(args.run, s, out, roles) for s in args.satellites]
    with Pool(min(args.workers, len(jobs))) as pool:
        results = pool.map(process, jobs)
    table = [r for res in results for r in res[0]]
    details = {res[1]['satellite']: res[1] for res in results}
    for m in MODES:
        fig_overview(out / f'overview_{m}.png', {res[1]['satellite']: res[2][m] for res in results}, roles, m,
                     args.satellites)
    with open(out / 'registration.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(table[0])); w.writeheader(); w.writerows(table)
    (out / 'registration.json').write_text(json.dumps(dict(table=table, details=details), indent=1))
    (out / 'points.json').write_text(json.dumps({s: d['points'] for s, d in details.items()}, indent=1))
    print(f'{"sat":>3} {"role":6} {"mode":9} {"max|row|":>8} {"max|col|":>8} {"rms":>6} | blocks max|col| L/C/R'
          f' | pred max|row| max|col|')
    for r in table:
        print(f'{r["satellite"]:>3} {r["role"]:6} {r["mode"]:9} {r["all_max_abs_row"]:8.3f} {r["all_max_abs_col"]:8.3f}'
              f' {r["all_rms"]:6.3f} | {r["left_max_abs_col"]:.3f}/{r["centre_max_abs_col"]:.3f}/{r["right_max_abs_col"]:.3f}'
              f' | {r["all_pred_max_abs_row"]:.3f} {r["all_pred_max_abs_col"]:.3f}')


if __name__ == '__main__':
    main()
