"""Quantify the geometry of the cubes of one run (build_cubes.py): band alignment, ground
sampling distance (model and measured) and the relative geometry of the satellites.

For every mode (none, linear, bilinear) and satellite the script reads
<run>/cubes/raw_bands_<mode>/cube_satSS.npz and measures:

1. Band alignment. Displacement of every band against the reference band (default the
   central one) in three column windows (left, centre, right) at mid cube, with the S11 phase
   correlation (Hann window, Gaussian low-pass; CLAUDE.md section 9). Per mode, satellite and
   window: linear fit of the displacement against the detector row of the band, over the inner
   bands (slope in pixels per 1000 rows: terrain parallax with respect to the z = 0 map of the
   cube), residual of the two outer bands from that fit (edge of the sensor), largest
   displacement and column drift.
2. GSD. In a 3 x 3 grid of windows of the reference band, the model ground position of every
   cube sample is computed from the poses and the cube's own frame index (none: frame
   k = r_b - g; linear: fractional frame k*; bilinear: k* and column c*), on a plane at the
   median terrain height of the window. The terrain orthophoto of the run's dataset (the
   terrain caches, data/terrain/<member>/1m, priority order of a composite) is resampled at
   those positions and registered against the cube on overlapping sub-windows; an affine fit
   (d = t + A x, in cube pixels) gives the scale, rotation and shear of the rendered image with
   respect to the model. Model GSD = metres per cube line (along) and column (across) of the
   model mapping; measured GSD = model Jacobian J times (I + A)^-1.
3. Satellite geometry. Per mode and satellite, the window means of the model and measured GSD,
   the azimuth of the cube's line axis on the ground and the residual translation (geolocation
   offset of the rendered image from the model at the median terrain height, metres), and the
   ratios and rotations with respect to the chief.

Displacements are scene motion in pixels (lines = cube rows, +down; cols = cube columns,
+right), i.e. minus the shift returned by phase_correlation.phase_shift.

Run on osiride in the conda environment orbits, from the repository root, e.g.
    python validation/analyze_cube_run.py --run $OUTPUT_ROOT/helsinki_espoo_2017/<run>
Writes <run>/analysis/: band_alignment.csv, band_alignment_summary.csv, gsd_windows.csv,
satellites.csv, report.md and, with matplotlib, band_alignment.png.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.ndimage import gaussian_filter, map_coordinates

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / 'pipeline'))
sys.path.insert(0, str(HERE))
import stacking_geometry as sg  # noqa: E402
from phase_correlation import phase_shift  # noqa: E402

LUMA = np.array([0.2126, 0.7152, 0.0722])
MODES = ('none', 'linear', 'bilinear')


# ----------------------------------------------------------------------------- generic helpers

def gray(rgba):
    """Rec.709 luminance of an RGBA uint8 array, 0 where alpha is 0."""
    rgba = np.asarray(rgba)
    return (rgba[..., :3].astype(np.float64) @ LUMA) * (rgba[..., 3] > 0)


def displacement(ref, mov, sigma, factor=200):
    """Scene motion (lines, cols) from ref to mov, S11 estimator on mean-removed images."""
    s = phase_shift(ref - ref.mean(), mov - mov.mean(), factor=factor, sigma=sigma, hann=True)
    return -float(s[0]), -float(s[1])


def fit_affine(points, disps, clip=3.0, iterations=3):
    """Least-squares d = t + A p over sub-window centres p (n, 2) and displacements d (n, 2),
    dropping points whose residual exceeds clip times the median absolute residual.
    Returns A (2, 2), t (2,), rms residual (px) and the number of points used."""
    points, disps = np.asarray(points, float), np.asarray(disps, float)
    keep = np.ones(len(points), bool)
    for _ in range(iterations):
        X = np.c_[np.ones(keep.sum()), points[keep]]
        coef, *_ = np.linalg.lstsq(X, disps[keep], rcond=None)
        res = np.linalg.norm(disps - np.c_[np.ones(len(points)), points] @ coef, axis=1)
        mad = np.median(res[keep]) + 1e-6
        new = res <= clip * mad * 1.4826 + 0.02
        if (new == keep).all():
            break
        keep = new
    t, A = coef[0], coef[1:].T
    rms = float(np.sqrt(np.mean(res[keep] ** 2)))
    return A, t, rms, int(keep.sum())


def register(pred, obs, valid, sub, step, sigma, min_valid=0.95):
    """Affine fit of the scene motion from pred to obs over overlapping sub-windows.
    Coordinates are relative to the window centre (pixels, (line, col))."""
    H, W = pred.shape
    pts, ds = [], []
    for r in range(0, H - sub + 1, step):
        for c in range(0, W - sub + 1, step):
            v = valid[r:r + sub, c:c + sub]
            if v.mean() < min_valid:
                continue
            a = np.where(v, pred[r:r + sub, c:c + sub], 0.)
            b = np.where(v, obs[r:r + sub, c:c + sub], 0.)
            if a.std() < 1e-6 or b.std() < 1e-6:
                continue
            pts.append((r + sub / 2 - H / 2, c + sub / 2 - W / 2))
            ds.append(displacement(a, b, sigma))
    if len(pts) < 6:
        return None
    return fit_affine(pts, ds)


def band_fit(rows, d, inner):
    """Linear fit of displacements d against detector rows over the inner bands: slope per 1000
    rows, intercept at the reference row (rows are given relative to it) and residuals."""
    rows, d = np.asarray(rows, float), np.asarray(d, float)
    p = np.polyfit(rows[inner] / 1000., d[inner], 1)
    res = d - np.polyval(p, rows / 1000.)
    return p[0], p[1], res


# ----------------------------------------------------------------------------- frames and orthophoto

class Frame:
    """Local ENU frame of the run <-> projected CRS of the dataset (same chain as terrain_to_render)."""

    def __init__(self, manifest, dataset):
        self.origin = np.asarray(manifest['origin_ecef_m'], float)
        self.R = np.asarray(manifest['local_from_ecef'], float)
        self.ecef_to_geo = Transformer.from_crs(4978, 4979, always_xy=True)
        self.geo_to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
        self.geo_to_proj = Transformer.from_crs(dataset['geographic_epsg'], dataset['projected_epsg'], always_xy=True)
        self.proj_to_geo = Transformer.from_crs(dataset['projected_epsg'], dataset['geographic_epsg'], always_xy=True)

    def local_to_proj(self, xyz):
        ecef = self.origin + np.asarray(xyz, float) @ self.R
        lon, lat, h = self.ecef_to_geo.transform(ecef[..., 0], ecef[..., 1], ecef[..., 2])
        x, y = self.geo_to_proj.transform(lon, lat)
        return np.asarray(x), np.asarray(y), np.asarray(h)

    def proj_to_local(self, x, y, h):
        lon, lat = self.proj_to_geo.transform(x, y)
        ecef = np.stack(self.geo_to_ecef.transform(lon, lat, h), -1)
        return (ecef - self.origin) @ self.R.T


class Ortho:
    """Luminance and height of the terrain caches of a dataset (members in priority order),
    sampled bilinearly at projected coordinates; luminance low-passed by a Gaussian of blur_m."""

    def __init__(self, cache_dirs, blur_m=2.0):
        self.dirs = [Path(d) for d in cache_dirs]
        self.blur_m = blur_m
        self.tiles = {}
        self.bounds = []
        for d in self.dirs:
            for path in sorted(d.glob('*.npz')):
                with np.load(path) as data:
                    x0, y0, s = float(data['x0']), float(data['y0']), float(data['spacing'])
                    rows, cols = data['height'].shape
                self.bounds.append((path, x0, y0, x0 + cols * s, y0 + rows * s))

    def _tile(self, path):
        if path not in self.tiles:
            if len(self.tiles) > 24:
                self.tiles.pop(next(iter(self.tiles)))
            with np.load(path) as data:
                h = data['height'].astype(np.float64)
                rgb = data['rgb'].astype(np.float64)
                x0, y0, s = float(data['x0']), float(data['y0']), float(data['spacing'])
            valid = np.isfinite(h)
            lum = np.where(valid, rgb @ LUMA, 0.)
            sig = self.blur_m / s
            w = gaussian_filter(valid.astype(float), sig)
            lum = gaussian_filter(lum, sig) / np.maximum(w, 1e-6)
            lum[w < 0.5] = np.nan
            self.tiles[path] = (lum, np.where(valid, h, np.nan), x0, y0, s)
        return self.tiles[path]

    def sample(self, x, y):
        x, y = np.asarray(x, float).ravel(), np.asarray(y, float).ravel()
        lum = np.full(x.size, np.nan)
        hgt = np.full(x.size, np.nan)
        for path, xa, ya, xb, yb in self.bounds:   # bounds are in priority order of the members
            todo = np.isnan(lum) & (x >= xa) & (x < xb) & (y >= ya) & (y < yb)
            if not todo.any():
                continue
            L, Hh, x0, y0, s = self._tile(path)
            rr, cc = (y[todo] - y0) / s - .5, (x[todo] - x0) / s - .5
            lv = map_coordinates(L, [rr, cc], order=1, mode='nearest', cval=np.nan)
            hv = map_coordinates(np.nan_to_num(Hh, nan=np.nanmedian(Hh)), [rr, cc], order=1, mode='nearest')
            idx = np.flatnonzero(todo)
            lum[idx] = lv
            hgt[idx] = np.where(np.isfinite(lv), hv, np.nan)
        return lum, hgt


# ----------------------------------------------------------------------------- cube geometry

def cube_ground(sat, mode, cube, idx_lines, idx_cols, band, height):
    """Local ENU position [n, 3] of cube samples (line index, column index) of a band, on the
    plane z = height, from the cube's own frame index (fractional frames: linear interpolation
    of the ground positions of the two neighbouring frames)."""
    r_b = float(cube['band_rows'][band])
    n_frames = len(sg.load_poses(sat)['t_s'])
    index = cube['frame_index']
    if mode == 'none':
        k = index[idx_lines, band].astype(float)
        c = cube['cols'][idx_cols].astype(float)
    elif mode == 'linear':
        k = index[idx_lines, idx_cols, band].astype(float)
        c = cube['cols'][idx_cols].astype(float)
    else:
        k = index[0, idx_lines, idx_cols, band].astype(float)
        c = index[1, idx_lines, idx_cols, band].astype(float)
    k0 = np.clip(np.floor(k).astype(int), 0, n_frames - 2)
    f = (k - k0)[..., None]
    p0 = sg.ground_from_pixel(sat, k0, r_b, c, height)
    p1 = sg.ground_from_pixel(sat, k0 + 1, r_b, c, height)
    return p0 + f * (p1 - p0)


def jacobian(sat, mode, cube, i, j, band, height):
    """2 x 2 ground metres (local East, North) per cube line (column 0) and column (column 1)."""
    p = lambda di, dj: cube_ground(sat, mode, cube, np.array([i + di]), np.array([j + dj]), band, height)[0, :2]  # noqa: E731
    return np.c_[(p(1, 0) - p(-1, 0)) / 2, (p(0, 1) - p(0, -1)) / 2]


def azimuth_deg(v):
    """Azimuth from north, clockwise, of a local (East, North) vector."""
    return float(np.degrees(np.arctan2(v[0], v[1])) % 360)


def measure_window(sat, mode, cube, band, i0, j0, win, frame, ortho, args):
    """GSD and residual geometry of one cube window against the orthophoto (see module doc)."""
    rgba = cube['cube'][i0:i0 + win, j0:j0 + win, band]
    obs = gray(rgba)
    I, J = np.meshgrid(np.arange(i0, i0 + win), np.arange(j0, j0 + win), indexing='ij')
    sub = np.s_[::8, ::8]
    p = cube_ground(sat, mode, cube, I[sub].ravel(), J[sub].ravel(), band, 0.)
    x, y, _ = frame.local_to_proj(p)
    _, h = ortho.sample(x, y)
    if np.isfinite(h).mean() < 0.5:
        return None
    ok = np.isfinite(h)
    href = float(np.median(frame.proj_to_local(x[ok], y[ok], h[ok])[:, 2]))
    p = cube_ground(sat, mode, cube, I.ravel(), J.ravel(), band, href)
    x, y, _ = frame.local_to_proj(p)
    lum, _ = ortho.sample(x, y)
    pred = lum.reshape(win, win)
    valid = np.isfinite(pred) & (rgba[..., 3] > 0)
    if valid.mean() < 0.9:
        return None
    fit = register(np.nan_to_num(pred), obs, valid, args.sub_window, args.sub_step, args.sigma_ortho)
    if fit is None:
        return None
    A, t, rms, n = fit
    Jm = jacobian(sat, mode, cube, i0 + win // 2, j0 + win // 2, band, href)
    Jmeas = Jm @ np.linalg.inv(np.eye(2) + A)
    rot = np.degrees((A[1, 0] - A[0, 1]) / 2)
    return dict(height_ref_m=round(href, 2), valid_fraction=round(float(valid.mean()), 3),
                gsd_along_model_m=float(np.linalg.norm(Jm[:, 0])), gsd_across_model_m=float(np.linalg.norm(Jm[:, 1])),
                gsd_along_meas_m=float(np.linalg.norm(Jmeas[:, 0])), gsd_across_meas_m=float(np.linalg.norm(Jmeas[:, 1])),
                scale_along_ppm=float(A[0, 0] * 1e6), scale_across_ppm=float(A[1, 1] * 1e6),
                rotation_mdeg=float(rot * 1e3), shear_ppm=float((A[0, 1] + A[1, 0]) / 2 * 1e6),
                t_lines_px=float(t[0]), t_cols_px=float(t[1]), t_ground_m=float(np.linalg.norm(Jm @ t)),
                azimuth_line_model_deg=azimuth_deg(Jm[:, 0]), azimuth_line_meas_deg=azimuth_deg(Jmeas[:, 0]),
                fit_rms_px=rms, fit_points=n)


# ----------------------------------------------------------------------------- driver

def load_cube(run, mode, sat):
    path = Path(run) / 'cubes' / f'raw_bands_{mode}' / f'cube_sat{sat:02d}.npz'
    if not path.exists():
        return None
    with np.load(path) as d:
        out = {k: d[k] for k in ('cube', 'g', 'cols', 'band_rows', 'frame_index')}
    return out


def window_cols(C, win):
    return {'left': int(C * .125 - win / 2), 'centre': int(C * .5 - win / 2), 'right': int(C * .875 - win / 2)}


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--modes', nargs='+', default=list(MODES), choices=MODES)
    ap.add_argument('--satellites', type=int, nargs='+', default=list(range(7)))
    ap.add_argument('--ref-band', type=int, help='reference band (default: the central one)')
    ap.add_argument('--window', type=int, default=512, help='window size in cube pixels')
    ap.add_argument('--sigma', type=float, default=0.05, help='S11 low-pass (cycles/px), band alignment')
    ap.add_argument('--sigma-ortho', type=float, default=0.1, help='low-pass (cycles/px), cube vs orthophoto')
    ap.add_argument('--sub-window', type=int, default=128)
    ap.add_argument('--sub-step', type=int, default=64)
    ap.add_argument('--blur-m', type=float, default=2.0, help='Gaussian blur of the orthophoto (m)')
    ap.add_argument('--terrain-root', type=Path, default=REPO / 'data' / 'terrain')
    ap.add_argument('--out', type=Path, help='default <run>/analysis')
    args = ap.parse_args()

    run = args.run
    out = args.out or run / 'analysis'
    out.mkdir(parents=True, exist_ok=True)
    cameras = run / 'simulation' / 'cameras.json'
    sg.set_paths(run_dir=run, cameras_json=cameras)
    manifest = json.loads(cameras.read_text())
    import terrain_cache as tc
    from terrain_merge import composite_members
    tiles_json = json.loads((run / 'terrain_render' / 'tiles.json').read_text())
    datasets = tc.load_datasets()
    name = tiles_json['dataset']
    members = composite_members(datasets, name) or [name]
    spacing = '1m'
    frame = Frame(manifest, datasets[members[0]])
    ortho = Ortho([args.terrain_root / m / spacing for m in members], args.blur_m)
    print(f'run {run.name}: dataset {name} (members {members}), {len(ortho.bounds)} cache tiles', flush=True)

    win = args.window
    align, align_sum, gsd = [], [], []
    for mode in args.modes:
        for sat in args.satellites:
            start = time.monotonic()
            cube = load_cube(run, mode, sat)
            if cube is None:
                print(f'{mode} sat {sat}: no cube, skipped', flush=True)
                continue
            G, C, B = cube['cube'].shape[:3]
            ref = B // 2 if args.ref_band is None else args.ref_band
            rows_rel = cube['band_rows'] - cube['band_rows'][ref]
            # 1. band alignment
            r0 = G // 2 - win // 2
            for wname, c0 in window_cols(C, win).items():
                base = gray(cube['cube'][r0:r0 + win, c0:c0 + win, ref])
                d = np.array([displacement(base, gray(cube['cube'][r0:r0 + win, c0:c0 + win, b]), args.sigma)
                              for b in range(B)])
                for b in range(B):
                    align.append(dict(mode=mode, sat=sat, window=wname, band=b, row=int(cube['band_rows'][b]),
                                      d_lines=d[b, 0], d_cols=d[b, 1]))
                inner = np.r_[1:B - 1]
                sl, il, rl = band_fit(rows_rel, d[:, 0], inner)
                sc, ic, rc = band_fit(rows_rel, d[:, 1], inner)
                align_sum.append(dict(mode=mode, sat=sat, window=wname, ref_band=ref, lines=G,
                                      slope_lines_px_per_1000_rows=sl, slope_cols_px_per_1000_rows=sc,
                                      rms_inner_lines_px=float(np.sqrt(np.mean(rl[inner] ** 2))),
                                      rms_inner_cols_px=float(np.sqrt(np.mean(rc[inner] ** 2))),
                                      edge_first_lines_px=float(rl[0]), edge_last_lines_px=float(rl[-1]),
                                      edge_first_cols_px=float(rc[0]), edge_last_cols_px=float(rc[-1]),
                                      max_abs_lines_px=float(np.abs(d[:, 0]).max()),
                                      max_abs_cols_px=float(np.abs(d[:, 1]).max())))
            # 2. GSD windows
            for fi in (.25, .5, .75):
                for fj in (.2, .5, .8):
                    i0, j0 = int(G * fi - win / 2), int(C * fj - win / 2)
                    i0, j0 = min(max(i0, 2), G - win - 2), min(max(j0, 2), C - win - 2)
                    m = measure_window(sat, mode, cube, ref, i0, j0, win, frame, ortho, args)
                    row = dict(mode=mode, sat=sat, line_frac=fi, col_frac=fj, i0=i0, j0=j0)
                    gsd.append({**row, **(m or dict(skipped=True))})
            ok = [r for r in gsd if r['mode'] == mode and r['sat'] == sat and 'gsd_along_meas_m' in r]
            s = align_sum[-2]
            print(f'{mode:8s} sat {sat}: {G} lines; bands centre: slope {s["slope_lines_px_per_1000_rows"]:+.3f} '
                  f'lines/1000 rows, edge {s["edge_first_lines_px"]:+.3f}/{s["edge_last_lines_px"]:+.3f}, max '
                  f'({s["max_abs_lines_px"]:.2f}, {s["max_abs_cols_px"]:.2f}) px; GSD {len(ok)}/9 windows'
                  + (f': along {np.mean([r["gsd_along_model_m"] for r in ok]):.3f} -> '
                     f'{np.mean([r["gsd_along_meas_m"] for r in ok]):.3f} m, across '
                     f'{np.mean([r["gsd_across_model_m"] for r in ok]):.3f} -> '
                     f'{np.mean([r["gsd_across_meas_m"] for r in ok]):.3f} m' if ok else '')
                  + f' ({time.monotonic() - start:.0f} s)', flush=True)
            del cube

    # 3. satellites: window means, relative to the chief
    sats = []
    for mode in args.modes:
        chief = None
        for sat in args.satellites:
            ok = [r for r in gsd if r['mode'] == mode and r['sat'] == sat and 'gsd_along_meas_m' in r]
            if not ok:
                continue
            mean = lambda k: float(np.mean([r[k] for r in ok]))  # noqa: E731
            circ = lambda k: float(np.degrees(np.angle(np.mean(np.exp(1j * np.radians([r[k] for r in ok]))))) % 360)  # noqa: E731
            row = dict(mode=mode, sat=sat, windows=len(ok),
                       gsd_along_model_m=mean('gsd_along_model_m'), gsd_along_meas_m=mean('gsd_along_meas_m'),
                       gsd_across_model_m=mean('gsd_across_model_m'), gsd_across_meas_m=mean('gsd_across_meas_m'),
                       azimuth_line_model_deg=circ('azimuth_line_model_deg'),
                       azimuth_line_meas_deg=circ('azimuth_line_meas_deg'),
                       t_ground_mean_m=mean('t_ground_m'), fit_rms_px=mean('fit_rms_px'))
            if sat == 0:
                chief = row
            if chief is not None:
                for k in ('along', 'across'):
                    row[f'ratio_{k}_model'] = row[f'gsd_{k}_model_m'] / chief[f'gsd_{k}_model_m']
                    row[f'ratio_{k}_meas'] = row[f'gsd_{k}_meas_m'] / chief[f'gsd_{k}_meas_m']
                for k in ('model', 'meas'):
                    row[f'rotation_{k}_deg'] = (row[f'azimuth_line_{k}_deg'] - chief[f'azimuth_line_{k}_deg'] + 180) % 360 - 180
            sats.append(row)

    write_csv(out / 'band_alignment.csv', align)
    write_csv(out / 'band_alignment_summary.csv', align_sum)
    write_csv(out / 'gsd_windows.csv', gsd)
    write_csv(out / 'satellites.csv', sats)
    write_report(out / 'report.md', run, name, args, align_sum, gsd, sats)
    plot(out / 'band_alignment.png', align)
    print(f'-> {out}', flush=True)


def write_report(path, run, name, args, align_sum, gsd, sats):
    L = [f'# Cube geometry of {run.name}', '',
         f'Dataset {name}; window {args.window} px; band alignment S11 sigma {args.sigma}; orthophoto '
         f'registration sigma {args.sigma_ortho}, sub-windows {args.sub_window}/{args.sub_step} px, blur {args.blur_m} m.',
         'Displacements are scene motion (lines +down, cols +right). Full tables in the CSV files.', '',
         '## 1. Band alignment (centre window)', '',
         '| mode | sat | lines | slope lines/1000 rows | slope cols/1000 rows | rms inner (lines, cols) | '
         'edge first / last (lines) | max abs (lines, cols) |', '|---|---|---|---|---|---|---|---|']
    for r in align_sum:
        if r['window'] != 'centre':
            continue
        L.append(f"| {r['mode']} | {r['sat']} | {r['lines']} | {r['slope_lines_px_per_1000_rows']:+.3f} | "
                 f"{r['slope_cols_px_per_1000_rows']:+.3f} | {r['rms_inner_lines_px']:.3f}, {r['rms_inner_cols_px']:.3f} | "
                 f"{r['edge_first_lines_px']:+.3f} / {r['edge_last_lines_px']:+.3f} | "
                 f"{r['max_abs_lines_px']:.2f}, {r['max_abs_cols_px']:.2f} |")
    spread = {}
    for r in align_sum:
        spread.setdefault((r['mode'], r['sat']), []).append(r['slope_lines_px_per_1000_rows'])
    L += ['', 'Spread of the slope between the left, centre and right windows (lines/1000 rows): '
          + ', '.join(f'{m} {s}: {np.ptp(v):.3f}' for (m, s), v in spread.items()), '',
          '## 2-3. GSD and satellite geometry (means over the valid windows)', '',
          '| mode | sat | windows | GSD along model / meas (m) | GSD across model / meas (m) | ratio to chief '
          'along, across (meas) | line azimuth model / meas (deg) | rotation vs chief (meas, deg) | '
          'geolocation offset (m) | fit rms (px) |', '|---|---|---|---|---|---|---|---|---|---|']
    for r in sats:
        L.append(f"| {r['mode']} | {r['sat']} | {r['windows']} | {r['gsd_along_model_m']:.3f} / {r['gsd_along_meas_m']:.3f} | "
                 f"{r['gsd_across_model_m']:.3f} / {r['gsd_across_meas_m']:.3f} | "
                 f"{r.get('ratio_along_meas', float('nan')):.4f}, {r.get('ratio_across_meas', float('nan')):.4f} | "
                 f"{r['azimuth_line_model_deg']:.2f} / {r['azimuth_line_meas_deg']:.2f} | "
                 f"{r.get('rotation_meas_deg', float('nan')):+.3f} | {r['t_ground_mean_m']:.2f} | {r['fit_rms_px']:.3f} |")
    skipped = sum(1 for r in gsd if r.get('skipped'))
    L += ['', f'{skipped} of {len(gsd)} GSD windows skipped (less than 90% valid samples or too few sub-windows).', '']
    path.write_text('\n'.join(L) + '\n')


def plot(path, align):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return
    modes = sorted({r['mode'] for r in align}, key=MODES.index)
    fig, axes = plt.subplots(2, len(modes), figsize=(5 * len(modes), 7), squeeze=False)
    for k, mode in enumerate(modes):
        for sat in sorted({r['sat'] for r in align}):
            sel = [r for r in align if r['mode'] == mode and r['sat'] == sat and r['window'] == 'centre']
            rows = [r['row'] for r in sel]
            axes[0, k].plot(rows, [r['d_lines'] for r in sel], '.-', label=f'sat {sat}')
            axes[1, k].plot(rows, [r['d_cols'] for r in sel], '.-', label=f'sat {sat}')
        axes[0, k].set_title(f'{mode}: lines'); axes[1, k].set_title(f'{mode}: columns')
        for a in axes[:, k]:
            a.set_xlabel('detector row of the band'); a.set_ylabel('displacement vs reference band (px)')
            a.grid(alpha=.3)
    axes[0, 0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=110)


if __name__ == '__main__':
    main()
