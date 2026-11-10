"""Analysis of stacking_inputs.npz (step 9 B2 export). Read-only on the input.

Needs only NumPy and Matplotlib; it does not need cameras.json, poses.jsonl or the PNGs.

Sections of the report
  A. inventory: every array with shape, dtype, range, NaN count
  B. internal consistency: time grid, rotation matrices, boresight vs centre ground point,
     distance_m and native_gsd_m, step decomposition, ratio to chief, alpha bounds
  C. per-satellite motion: image advance per frame, cumulative position of a ground point
     versus integer-row stepping, drift relative to the chief
  D. scene extent: union / intersection rows and columns, ground lines seen by the chief
  E. band layouts: number of ground lines that cross every band row within the window,
     for a few linear-variable-filter layouts
  F. figures (PNG) next to the report

Usage:
    python3 scripts/analyze_stacking_inputs.py [--npz PATH] [--out DIR]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
DEFAULT_NPZ = REPO / 'output/orbits_melbourne/eevee_full_1000/stacking_inputs.npz'
PIXEL_PITCH_M = 5.5e-6


def fmt(x, digits=6):
    """Compact float formatting for tables."""
    return f'{x:.{digits}g}'


def table(header, rows):
    """Markdown table from a header list and a list of row lists."""
    lines = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    lines += ['| ' + ' | '.join(str(c) for c in r) + ' |' for r in rows]
    return '\n'.join(lines)


def inventory(d):
    rows = []
    for k in d.files:
        a = d[k]
        if a.dtype.kind in 'fiub' and a.size:
            v = a.astype(float)
            rng = f'{fmt(np.nanmin(v))} .. {fmt(np.nanmax(v))}'
            nan = int(np.isnan(v).sum())
        else:
            rng, nan = (str(a)[:60] if a.ndim == 0 else ', '.join(map(str, a.ravel()[:7]))), '-'
        rows.append([f'`{k}`', str(a.shape), str(a.dtype), rng, nan])
    return table(['key', 'shape', 'dtype', 'range', 'NaN'], rows)


def consistency(d):
    """Return a list of (check, measured, ok) tuples."""
    out = []
    t = d['t_s']; dt = np.diff(t)
    out.append(('t_s uniform grid', f'dt = {dt.mean()*1e3:.10f} ms, max |diff - dt| = {np.abs(dt-dt.mean()).max():.1e} s',
                np.abs(dt - dt.mean()).max() < 1e-12))
    R = d['camera_to_local']
    orth = np.abs(np.einsum('snji,snjk->snik', R, R) - np.eye(3)).max()
    det = np.linalg.det(R)
    out.append(('camera_to_local orthonormal, det +1', f'max |R^T R - I| = {orth:.1e}, det {det.min():.12f} .. {det.max():.12f}',
                orth < 1e-9 and np.abs(det - 1).max() < 1e-9))
    pos, cg = d['position_local_m'], d['centre_ground_local_m']
    fwd = -R[..., :, 2]
    ray = cg - pos
    cosang = np.einsum('snx,snx->sn', ray / np.linalg.norm(ray, axis=-1, keepdims=True), fwd)
    out.append(('centre ground point on the boresight', f'max angle {np.degrees(np.arccos(np.clip(cosang.min(), -1, 1)))*3600:.2e} arcsec, '
                f'max |z| {np.abs(cg[..., 2]).max():.1e} m', cosang.min() > 1 - 1e-12 and np.abs(cg[..., 2]).max() < 1e-6))
    dist = np.linalg.norm(ray, axis=-1)
    out.append(('distance_m = |centre ground - camera|', f'max abs diff {np.abs(dist - d["distance_m"]).max():.1e} m',
                np.abs(dist - d['distance_m']).max() < 1e-4))
    f = float(d['focal_length_mm']) * 1e-3
    gsd = d['distance_m'] * PIXEL_PITCH_M / f
    out.append(('native_gsd_m = distance p / f', f'max rel diff {np.abs(gsd/d["native_gsd_m"]-1).max():.1e}',
                np.abs(gsd / d['native_gsd_m'] - 1).max() < 1e-12))
    step = np.hypot(d['centre_step_along_m'], d['centre_step_across_m'])
    out.append(('centre_step_m = hypot(along, across)', f'max abs diff {np.abs(step - d["centre_step_m"]).max():.1e} m '
                f'(residual = vertical part of the step, 0 on z = 0)', np.abs(step - d['centre_step_m']).max() < 1e-6))
    ratio = d['image_motion_rows_px'] / d['image_motion_rows_px'][0]
    out.append(('image_motion_ratio_to_chief = rows / rows[chief]',
                f'max abs diff {np.abs(ratio - d["image_motion_ratio_to_chief"]).max():.1e}',
                np.abs(ratio - d['image_motion_ratio_to_chief']).max() < 1e-12))
    pred = d['centre_step_along_m'] / d['ground_pixel_along_m'][:, :-1]
    out.append(('advance from ground step / ground pixel along vs image_motion_rows_px',
                f'max abs diff {np.abs(pred - d["image_motion_rows_px"]).max():.1e} px (first-order estimate)',
                np.abs(pred - d['image_motion_rows_px']).max() < 1e-3))
    b, ra, ca = d['alpha_bounds'], d['alpha_row_any'], d['alpha_col_any']
    first = lambda m: np.where(m.any(-1), m.argmax(-1), -1)
    last = lambda m: np.where(m.any(-1), m.shape[-1] - 1 - m[..., ::-1].argmax(-1), -1)
    ok = (np.array_equal(b[..., 0], first(ra)) and np.array_equal(b[..., 1], last(ra))
          and np.array_equal(b[..., 2], first(ca)) and np.array_equal(b[..., 3], last(ca)))
    out.append(('alpha_bounds match alpha_row_any / alpha_col_any', 'identical' if ok else 'MISMATCH', ok))
    contiguous = all(np.all(np.diff(np.flatnonzero(m)) == 1) for m in ra.reshape(-1, ra.shape[-1]) if m.any())
    out.append(('rows with alpha > 0 contiguous in every image', str(contiguous), contiguous))
    tgt = d['common_target_local_m']
    off = np.linalg.norm(tgt[:, :2] - cg[0, :, :2], axis=-1)
    out.append(('common target (ellipsoid) vs chief centre ground (z = 0), horizontal',
                f'{off.min():.4f} .. {off.max():.4f} m; target z {tgt[:, 2].min():.3f} .. {tgt[:, 2].max():.3f} m', True))
    return out


def motion(d):
    """Per-satellite advance statistics and cumulative drift against integer stepping."""
    mr, mc = d['image_motion_rows_px'], d['image_motion_cols_px']
    cum_r = np.concatenate([np.zeros((mr.shape[0], 1)), np.cumsum(mr, 1)], 1)   # [S, N] row of a ground point
    cum_c = np.concatenate([np.zeros((mc.shape[0], 1)), np.cumsum(mc, 1)], 1)
    k = np.arange(cum_r.shape[1])
    drift_int = cum_r - k                     # versus +1 row per frame
    drift_chief = cum_r - cum_r[0]            # versus the chief
    rows = []
    for s in range(mr.shape[0]):
        rows.append([s, d['pose_source'][s], f'{mr[s].min():.6f} .. {mr[s].max():.6f}', f'{mr[s].mean():.6f}',
                     f'{mc[s].min():+.1e} .. {mc[s].max():+.1e}', f'{cum_r[s, -1]:.4f}', f'{drift_int[s, -1]:+.4f}',
                     f'{np.abs(drift_int[s]).max():.4f}', f'{drift_chief[s, -1]:+.4f}', f'{cum_c[s, -1]:+.1e}',
                     f'{d["distance_m"][s].mean()-d["distance_m"][0].mean():+.1f}'])
    head = ['sat', 'pose source', 'advance rows/frame', 'mean', 'advance cols/frame', 'total rows f0->f999',
            'minus 999 (integer stepping)', 'max |drift| vs k', 'minus chief', 'total cols', 'mean range minus chief (m)']
    return table(head, rows), cum_r, drift_int, drift_chief


def scene(d):
    """Scene extent per rendered satellite and the ground lines seen by the chief."""
    ra, ca = d['alpha_row_any'], d['alpha_col_any']
    rows = []
    for i, s in enumerate(d['alpha_satellites']):
        u_r, i_r = np.flatnonzero(ra[i].any(0)), np.flatnonzero(ra[i].all(0))
        u_c, i_c = np.flatnonzero(ca[i].any(0)), np.flatnonzero(ca[i].all(0))
        rng = lambda a: f'{a[0]}..{a[-1]} ({a.size})' if a.size else 'none'
        cov = d['alpha_opaque_pixels'][i] / (int(d['width']) * int(d['height']))
        rows.append([s, rng(u_r), rng(u_c), rng(i_r), rng(i_c), f'{cov.min()*100:.4f} .. {cov.max()*100:.4f}',
                     f'{d["alpha_partial_pixels"][i].min()} .. {d["alpha_partial_pixels"][i].max()}'])
    head = ['sat', 'union rows', 'union cols', 'intersection rows', 'intersection cols', 'opaque coverage %', 'partial-alpha pixels']
    # Chief: a ground line g sits at row g + k in frame k (integer stepping, drift < 0.004 rows).
    k_idx, r_idx = np.nonzero(ra[0])
    g = r_idx - k_idx
    lines = (g.min(), g.max())
    return table(head, rows), lines


def band_layouts(n_frames, scene_lines, height):
    """Ground lines that cross every band row within the window (chief, +1 row per frame).

    A ground line g (row in frame 0) is at row g + k in frame k. Band row r_b sees it at
    frame k = r_b - g, which must lie in 0..n_frames-1. All bands are seen iff
    max(r_b) - (n_frames-1) <= g <= min(r_b), i.e. n_frames - span lines, span = max - min.
    """
    layouts = {
        '31 bands spread over the 3072 rows (step 102)': np.round(np.linspace(0, height - 1, 31)).astype(int),
        '31 bands, one every 32 rows, centred': 1536 - 15 * 32 + 32 * np.arange(31),
        '32 bands, one every 28 rows (Y_START multiple of 4), centred': 1536 - 434 + 28 * np.arange(32),
        '32 bands, one every 4 rows (adjacent 4-row windows), centred': 1536 - 62 + 4 * np.arange(32),
    }
    rows = []
    for name, rb in layouts.items():
        lo, hi = rb.max() - (n_frames - 1), rb.min()
        n = max(0, hi - lo + 1)
        inter = max(0, min(hi, scene_lines[1]) - max(lo, scene_lines[0]) + 1)
        need = rb.max() - rb.min() + 1 + (scene_lines[1] - scene_lines[0])  # frames for the whole scene
        rows.append([name, f'{rb.min()}..{rb.max()}', rb.max() - rb.min(), n, inter,
                     f'{inter*4.7496/1e3:.2f}', need])
    head = ['layout', 'band rows', 'span (rows)', 'complete lines in window', 'complete lines inside the scene',
            'along-track length (km)', 'frames for the whole scene']
    return table(head, rows)


def figures(d, cum_r, drift_int, drift_chief, out):
    names = []
    k = np.arange(d['t_s'].size)
    S = d['image_motion_rows_px'].shape[0]
    fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
    for s in range(S):
        ax[0].plot(k[1:], d['image_motion_rows_px'][s] - 1, label=f'sat {s}')
        ax[1].plot(k, drift_int[s], label=f'sat {s}')
    ax[0].set(xlabel='frame', ylabel='advance - 1 (rows/frame)', title='Image advance per frame, own pixels')
    ax[1].set(xlabel='frame', ylabel='rows', title='Ground point row minus k (integer stepping error)')
    for a in ax: a.grid(alpha=.3); a.legend(fontsize=8, ncol=2)
    fig.tight_layout(); p = out / 'motion_and_drift.png'; fig.savefig(p, dpi=120); plt.close(fig); names.append(p.name)

    fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
    for s in range(S):
        ax[0].plot(k, d['distance_m'][s] - d['distance_m'][0], label=f'sat {s}')
        ax[1].plot(k, d['native_gsd_m'][s], label=f'sat {s}')
    ax[0].set(xlabel='frame', ylabel='m', title='Slant range minus chief')
    ax[1].set(xlabel='frame', ylabel='m', title='Native GSD at the boresight')
    for a in ax: a.grid(alpha=.3); a.legend(fontsize=8, ncol=2)
    fig.tight_layout(); p = out / 'range_and_gsd.png'; fig.savefig(p, dpi=120); plt.close(fig); names.append(p.name)

    ra = d['alpha_row_any'][0]
    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ax[0].imshow(ra.T, aspect='auto', origin='upper', cmap='Greys', interpolation='nearest')
    ax[0].set(xlabel='frame k', ylabel='PNG row', title='Chief: rows with alpha > 0')
    b = d['alpha_bounds'][0]
    ax[1].plot(k, b[:, 0], label='row min'); ax[1].plot(k, b[:, 1], label='row max')
    ax[1].plot(k, b[:, 2], label='col min'); ax[1].plot(k, b[:, 3], label='col max')
    ax[1].invert_yaxis(); ax[1].set(xlabel='frame', ylabel='PNG index', title='Chief alpha bounds'); ax[1].legend(); ax[1].grid(alpha=.3)
    fig.tight_layout(); p = out / 'scene_extent_chief.png'; fig.savefig(p, dpi=120); plt.close(fig); names.append(p.name)

    pos, cg = d['position_local_m'], d['centre_ground_local_m']
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.5))
    for s in range(S):
        ax[0].plot(pos[s, :, 0] - pos[0, :, 0].mean(), pos[s, :, 1] - pos[0, :, 1].mean(), label=f'sat {s}')
        ax[0].plot(*(pos[s, 0, :2] - pos[0, :, :2].mean(0)), 'k.', ms=4)
    ax[0].set(xlabel='East (m)', ylabel='North (m)', title='Camera centres, ENU, relative to mean chief position (dot = frame 0)')
    ax[0].set_aspect('equal'); ax[0].legend(fontsize=8); ax[0].grid(alpha=.3)
    ax[1].plot(cg[0, :, 0], cg[0, :, 1], label='chief centre ground (z = 0)')
    ax[1].plot(d['common_target_local_m'][:, 0], d['common_target_local_m'][:, 1], '--', label='common target (ellipsoid)')
    ax[1].set(xlabel='East (m)', ylabel='North (m)', title='Ground track of the image centre'); ax[1].legend(); ax[1].grid(alpha=.3)
    ax[1].set_aspect('equal', adjustable='datalim')
    fig.tight_layout(); p = out / 'positions_enu.png'; fig.savefig(p, dpi=120); plt.close(fig); names.append(p.name)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--npz', type=Path, default=DEFAULT_NPZ)
    parser.add_argument('--out', type=Path, help='default <npz folder>/stacking_analysis')
    args = parser.parse_args()
    out = args.out or args.npz.parent / 'stacking_analysis'
    out.mkdir(parents=True, exist_ok=True)
    d = np.load(args.npz)

    checks = consistency(d)
    motion_table, cum_r, drift_int, drift_chief = motion(d)
    scene_table, lines = scene(d)
    bands = band_layouts(d['t_s'].size, lines, int(d['height']))
    figs = figures(d, cum_r, drift_int, drift_chief, out)

    centre = d['image_centre_row_col']
    gp = [[s, f'{d["native_gsd_m"][s].min():.6f} .. {d["native_gsd_m"][s].max():.6f}',
           f'{d["ground_pixel_across_m"][s].mean():.6f}', f'{d["ground_pixel_along_m"][s].mean():.6f}',
           f'{d["centre_step_m"][s].mean():.6f}', f'{d["centre_step_across_m"][s].min():+.1e} .. {d["centre_step_across_m"][s].max():+.1e}']
          for s in range(d['native_gsd_m'].shape[0])]
    report = [
        '# Analysis of stacking_inputs.npz', '',
        f'Input: `{args.npz}`  ', f'Script: `scripts/analyze_stacking_inputs.py` (read-only on the input).', '',
        f'Sources recorded in the file: `{json.loads(str(d["sources"]))}`', '',
        f'Conventions recorded in the file: `{json.loads(str(d["conventions"]))}`', '',
        f'Intrinsics: {int(d["width"])} x {int(d["height"])} px, f = {float(d["focal_length_mm"])} mm, '
        f'sensor {float(d["sensor_width_mm"])} x {float(d["sensor_height_mm"])} mm, image centre (row, col) = '
        f'({centre[0]}, {centre[1]}).', '',
        '## A. Inventory', '', inventory(d), '',
        '## B. Internal consistency', '',
        table(['check', 'measured', 'ok'], [[c, m, 'yes' if ok else '**NO**'] for c, m, ok in checks]), '',
        '## C. Ground pixel size and motion per satellite', '',
        table(['sat', 'native GSD (m)', 'ground px across mean (m)', 'ground px along mean (m)', 'centre step mean (m)',
               'centre step across (m)'], gp), '',
        motion_table, '',
        'Cumulative row = sum of image_motion_rows_px, i.e. the row of a ground point that started at the image centre, '
        'measured at the image centre only (the scale term away from the centre is not included).', '',
        '## D. Scene extent (rendered satellites)', '', scene_table, '',
        f'Ground lines seen by the chief (row in frame 0, g = row - k): {lines[0]} .. {lines[1]} '
        f'({lines[1]-lines[0]+1} lines, {(lines[1]-lines[0]+1)*4.7496/1e3:.2f} km along track).', '',
        '## E. Band layouts (chief, +1 row per frame)', '',
        'A ground line g is complete when every band row sees it within frames 0..N-1: '
        'complete lines = N - span, with span = max(band row) - min(band row).', '', bands, '',
        '## F. Figures', '', *[f'![{n}]({n})' for n in figs], '']
    (out / 'stacking_inputs_analysis.md').write_text('\n'.join(report))
    print('\n'.join(report))
    print(f'\nwrote {out / "stacking_inputs_analysis.md"} and {len(figs)} figures')


if __name__ == '__main__':
    main()
