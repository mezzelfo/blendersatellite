"""Step 17 C: validation of the acquisition cubes written by build_acquisition_cube.py.

Read-only on the PNGs, the cubes and the exported bands. Writes only the visual products in
<run>/band_samples/comparison/. Prints a markdown report to stdout.

Checks (satellites 0-4; D[b, k, c] = frame_k[1000 + b, 662 + c], no correction)
  C.1 10000 random (b, k, c) per satellite vs the PNG pixel; 20 random frames compared in full.
  C.2 every band_samples/satellite_SS/band_RRRR.png equals D[RRRR - 1000] bit for bit.
  C.3 alpha per satellite and band: fractions 255 / partial / 0 over the 1000 frames, and where
      the alpha-0 pixels are (per corner quadrant of the band image: count and extent).
  C.4 same ground in two bands (b1, b2), delta = r_b2 - r_b1: mean |dRGB| on alpha 255 between
      D[b2, k + o] and D[b1, k], o = delta - 2 .. delta + 2; the chief minimum must be at o = delta.
  C.5 residual sub-pixel shift between D[b2, k + delta] and D[b1, k] (Hann + Gaussian low-pass phase
      correlation, luminance x alpha) vs the S9 prediction (alpha-weighted per-pixel z = 0 drift),
      parallax term P = measured - predicted of the chief on the same pair (S10 logic).
  C.6 comparison crops (x8) and a 9-band GIF on the ground common to bands 1000 and 1957.

Sign convention of the shifts (same as check_cubes.py B.4 / PLAN_CONTEXT F3): "shift rows" is the
apparent displacement of the scene from D[b1, k] to D[b2, k + delta] measured along the ground-line
axis g of frame 0 (g decreases with k), i.e. shift = -(motion along k). For a deputy it is about
delta x (s - 1); the fractional frame offset that best aligns the two bands is delta - shift.
"Shift cols" = motion along the columns (+right).

Run in the conda environment orbits:
    python scripts/check_acquisition_cube.py [--workers 20] [--sigma 0.05] > report.md
"""
import argparse
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import build_acquisition_cube as bac
import stacking_geometry as sg
from phase_correlation import phase_shift

SATS = bac.SATS
R0, C0 = bac.ROW0, bac.COL0
LUMA = np.array([0.2126, 0.7152, 0.0722])
OFFS = (-2, -1, 0, 1, 2)
CROP_W, UPS = 256, 8


def md(header, rows):
    return '\n'.join(['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)] +
                     ['| ' + ' | '.join(map(str, r)) + ' |' for r in rows])


def cube_path(run, sat):
    return bac.cube_dir(run) / f'cube_sat{sat:02d}.npy'


def open_cube(run, sat):
    D = np.load(cube_path(run, sat), mmap_mode='r')
    assert D.shape == bac.SHAPE and D.dtype == np.uint8, (D.shape, D.dtype)
    return D


def gray(rgba):
    a = rgba.astype(np.float64) / 255
    return (a[..., :3] @ LUMA) * a[..., 3]


# ---------------------------------------------------------------- C.1
def _check_frame(job):
    """Worker: (run, sat, k, b, c, full) -> (n samples differing, full-frame differing samples or -1)."""
    run, sat, k, b, c, full = job
    with Image.open(bac.frame_path(run, sat, k)) as im:
        png = np.asarray(im.convert('RGBA'))
    D = open_cube(run, sat)
    bad = int(np.sum(np.any(D[b, k, c] != png[R0 + b, C0 + c], -1)))
    nfull = -1
    if full:
        nfull = int(np.sum(np.any(np.asarray(D[:, k]) != png[R0:bac.ROW1 + 1, C0:bac.COL1 + 1], -1)))
    return bad, nfull


def c1(run, sat, pool, rng, n_samples=10000, n_full=20):
    b = rng.integers(0, bac.N_BANDS, n_samples)
    k = rng.integers(0, bac.N_FRAMES, n_samples)
    c = rng.integers(0, bac.N_COLS, n_samples)
    full = set(rng.choice(bac.N_FRAMES, n_full, replace=False).tolist())
    frames = sorted(set(k.tolist()) | full)
    jobs = [(run, sat, f, b[k == f], c[k == f], f in full) for f in frames]
    res = pool.map(_check_frame, jobs, chunksize=4)
    bad = sum(r[0] for r in res)
    full_bad = [r[1] for r in res if r[1] >= 0]
    return dict(samples=n_samples, frames_read=len(frames), bad=bad, full_frames=sorted(full),
                full_bad=int(sum(full_bad)), full_n=len(full_bad))


# ---------------------------------------------------------------- C.3
def alpha_stats(D, b):
    a = np.asarray(D[b, :, :, 3])
    z = a == 0
    K, C = a.shape
    quad = {}
    for name, ks, cs in (('top-left', slice(0, K // 2), slice(0, C // 2)), ('top-right', slice(0, K // 2), slice(C // 2, C)),
                         ('bottom-left', slice(K // 2, K), slice(0, C // 2)), ('bottom-right', slice(K // 2, K), slice(C // 2, C))):
        kk, cc = np.nonzero(z[ks, cs])
        if kk.size == 0:
            quad[name] = (0, 0, 0)
            continue
        kk = kk + ks.start; cc = cc + cs.start
        # extent from the corner: frames from the top/bottom edge, columns from the left/right edge
        dk = (kk if name.startswith('top') else K - 1 - kk).max() + 1
        dc = (cc if name.endswith('left') else C - 1 - cc).max() + 1
        quad[name] = (int(kk.size), int(dk), int(dc))
    return dict(f255=float(np.mean(a == 255)), fpart=float(np.mean((a > 0) & (a < 255))), f0=float(np.mean(z)),
                n0=int(z.sum()), quad=quad)


# ---------------------------------------------------------------- C.4 / C.5 geometry
def ground_xy(sat, frames, row, cols):
    """(x_row, x_col) [K, C]: frame-0 PNG position of the z = 0 ground seen at (row, cols) in frames."""
    f = np.asarray(frames)[:, None]
    xyz = sg.ground_from_pixel(sat, f, float(row), np.asarray(cols, float)[None, :])
    xr, xc = sg.pixel_from_ground(sat, 0, xyz)
    return np.asarray(xr), np.asarray(xc)


def pair_offsets(D, b1, b2):
    """C.4: mean |dRGB| on alpha 255 for o = delta + OFFS (common frames for every o)."""
    delta = b2 - b1
    n = bac.N_FRAMES - (delta + 2)
    ref = np.asarray(D[b1, 0:n]).astype(np.int16)
    out = {}
    for o in OFFS:
        mov = np.asarray(D[b2, delta + o:delta + o + n]).astype(np.int16)
        m = (ref[..., 3] == 255) & (mov[..., 3] == 255)
        out[o] = float(np.abs(mov[..., :3] - ref[..., :3])[m].mean())
    return out, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--run', type=Path, default=sg.RUN_DIR)
    ap.add_argument('--sigma', type=float, default=0.05,
                    help='Gaussian low-pass (cycles/px) of the S11 estimator, with Hann window')
    args = ap.parse_args()
    run = args.run
    rows9 = bac.export_rows()
    bands9 = rows9 - R0
    cubes = {s: open_cube(run, s) for s in SATS}
    print('# Step 17 C: acquisition cube validation\n')

    # ---------------- C.1
    rng = np.random.default_rng(17)
    tr, stop = [], False
    with Pool(args.workers) as pool:
        for s in SATS:
            r = c1(run, s, pool, rng)
            ok = r['bad'] == 0 and r['full_bad'] == 0 and r['full_n'] == 20
            stop |= not ok
            tr.append([s, f"{r['samples'] - r['bad']}/{r['samples']}", r['frames_read'], r['full_n'],
                       r['full_bad'], ', '.join(map(str, r['full_frames'])), 'yes' if ok else '**NO**'])
            print(f'C.1 sat {s} done', flush=True, file=__import__('sys').stderr)
    print('## C.1 Direct read vs PNG\n')
    print(md(['sat', 'random samples equal', 'PNG frames read', 'full frames', 'full frames: differing samples',
              'full frames (k)', 'all equal'], tr))
    if stop:
        print('\n**STOP: cube differs from the PNGs**'); return

    # ---------------- C.2
    tr, stop = [], False
    for s in SATS:
        for r, b in zip(rows9, bands9):
            p = bac.band_dir(run) / f'satellite_{s:02d}' / f'band_{r:04d}.png'
            with Image.open(p) as im:
                mode, a = im.mode, np.asarray(im)
            eq = mode == 'RGBA' and a.shape == (bac.N_FRAMES, bac.N_COLS, 4) and np.array_equal(a, cubes[s][b])
            stop |= not eq
            tr.append([s, r, mode, f'{a.shape[1]} x {a.shape[0]}', 'yes' if eq else '**NO**'])
    print('\n## C.2 Exported bands vs cube\n')
    print(md(['sat', 'band row', 'mode', 'size (w x h)', 'bit-identical to D[r - 1000]'], tr))
    if stop:
        print('\n**STOP: exported band differs from the cube**'); return

    # ---------------- C.3
    print('\n## C.3 Alpha per band (fractions over 1000 frames x 1908 columns)\n')
    print('Quadrants of the band image (top = frames 0..499, left = columns 662..1615): n alpha-0 pixels / '
          'extent from the corner (frames along k x columns).\n')
    tr, summ = [], []
    for s in SATS:
        all_f0 = []
        for b in range(bac.N_BANDS):
            st = alpha_stats(cubes[s], b)
            all_f0.append((st['f0'], st['fpart'], st['f255']))
            if b in bands9:
                q = st['quad']
                tr.append([s, R0 + b, f"{st['f255']:.5f}", f"{st['fpart']:.5f}", f"{st['f0']:.5f}", st['n0']] +
                          [f'{q[n][0]} / {q[n][1]} x {q[n][2]}' for n in ('top-left', 'top-right', 'bottom-left', 'bottom-right')])
        f = np.array(all_f0)
        summ.append([s, f'{f[:, 2].min():.5f} .. {f[:, 2].max():.5f}', f'{f[:, 1].min():.5f} .. {f[:, 1].max():.5f}',
                     f'{f[:, 0].min():.5f} .. {f[:, 0].max():.5f}'])
    print(md(['sat', 'band row', 'alpha 255', '0 < alpha < 255', 'alpha 0', 'n alpha 0', 'top-left', 'top-right',
              'bottom-left', 'bottom-right'], tr))
    print('\nAll 958 bands (min .. max over bands):\n')
    print(md(['sat', 'alpha 255', '0 < alpha < 255', 'alpha 0'], summ))

    # ---------------- C.4
    pairs = [(int(bands9[i]), int(bands9[i + 1])) for i in range(8)] + [(0, bac.N_BANDS - 1)]
    print('\n## C.4 Same ground in two bands: mean |dRGB| (DN, alpha 255 in both) of D[b2, k + o] vs D[b1, k]\n')
    tr, stop, best_int = [], [], {}
    for s in SATS:
        for b1, b2 in pairs:
            st, n = pair_offsets(cubes[s], b1, b2)
            d = b2 - b1
            best = min(OFFS, key=st.get)
            best_int[s, b1, b2] = d + best
            if s == 0 and best != 0: stop.append((b1, b2, best))
            tr.append([s, f'{R0 + b1} -> {R0 + b2}', d, n] + [f'{st[o]:.3f}' for o in OFFS] + [f'delta {best:+d}'])
    print(md(['sat', 'pair (rows)', 'delta', 'frames'] + [f'o = delta {o:+d}' for o in OFFS] + ['argmin'], tr))
    if stop:
        print(f'\n**STOP: chief minimum not at delta for {stop}**'); return

    # ---------------- C.5
    print(f'\n## C.5 Residual sub-pixel shift between D[b2, k + delta] and D[b1, k]\n')
    print(f'Estimator: phase_correlation.phase_shift(ref = D[b1, k], mov = D[b2, k + delta], upsample 1000, '
          f'hann=True, sigma={args.sigma}) on luminance x alpha. S11 is not settled (no sigma meets 0.005 px in '
          f'cubes/shift_estimator_bias.json); sigma = {args.sigma} has the smallest max |bias| there (0.012 px).\n')
    print('shift rows = scene displacement along g (= -motion along k); pred S9 = -(alpha-weighted mean of the '
          'per-pixel z = 0 drift x(k + delta, r2, c) - x(k, r1, c)) / s_local; pred s = delta (s - 1) with s fitted '
          '(step 13 C.1) at the scene-centre column 1615, mean of rows r1 and r2; P = chief measured - chief pred S9; '
          'residual = measured - pred S9 - P; requirement |residual| < 0.05 px (rows and cols).\n')
    cc_idx = 1615 - C0
    cols = np.arange(C0, bac.COL1 + 1)
    kk = np.arange(bac.N_FRAMES)
    geo = {}
    for s in SATS:
        geo[s] = {}
        for r in sorted(set(R0 + b for p in pairs for b in p)):
            xr, xc = ground_xy(s, kk, r, cols)
            sl = -np.polyfit(kk, xr[:, cc_idx], 1)[0]
            geo[s][r] = (xr, xc, sl)
    meas, pred = {}, {}
    for s in SATS:
        D = cubes[s]
        for b1, b2 in pairs:
            d, n = b2 - b1, bac.N_FRAMES - (b2 - b1)
            A, B = np.asarray(D[b1, 0:n]), np.asarray(D[b2, d:d + n])
            mot = -phase_shift(gray(A), gray(B), 1000, sigma=args.sigma, hann=True)     # (k, c)
            meas[s, b1, b2] = np.array([-mot[0], mot[1]])
            x1r, x1c, s1 = geo[s][R0 + b1]
            x2r, x2c, s2 = geo[s][R0 + b2]
            dr = x2r[d:d + n] - x1r[0:n]
            dc = x2c[d:d + n] - x1c[0:n]
            s_loc = -np.gradient(x1r[0:n], axis=0)                      # frame-0 rows per frame
            w = A[..., 3].astype(float) * B[..., 3] / 255 ** 2
            # along k: motion_k = +drift / s_local, shift = -motion_k; along c: motion = -drift
            pred[s, b1, b2] = np.array([-np.sum(w * dr / s_loc) / w.sum(), -np.sum(w * dc) / w.sum()])
            geo[s][b1, b2] = d * ((s1 + s2) / 2 - 1)
    tr, fails = [], []
    for s in SATS:
        for b1, b2 in pairs:
            m, p = meas[s, b1, b2], pred[s, b1, b2]
            P = meas[0, b1, b2] - pred[0, b1, b2]
            res = m - p - P
            ok = bool(np.all(np.abs(res) < 0.05))
            if s != 0 and not ok: fails.append((s, R0 + b1, R0 + b2))
            tr.append([s, f'{R0 + b1} -> {R0 + b2}', b2 - b1, f'{m[0]:+.4f}', f'{m[1]:+.4f}', f'{geo[s][b1, b2]:+.4f}',
                       f'{p[0]:+.4f}', f'{p[1]:+.4f}', f'{P[0]:+.4f}', f'{P[1]:+.4f}', f'{res[0]:+.4f}', f'{res[1]:+.4f}',
                       f'delta {best_int[s, b1, b2] - (b2 - b1):+d}', '-' if s == 0 else ('yes' if ok else '**NO**')])
    print(md(['sat', 'pair (rows)', 'delta', 'meas rows', 'meas cols', 'pred delta(s-1)', 'pred S9 rows', 'pred S9 cols',
              'P rows', 'P cols', 'residual rows', 'residual cols', 'best integer offset (C.4)', '< 0.05 px'], tr))
    big = [f'sat {s}: {meas[s, 0, bac.N_BANDS - 1][0]:+.3f}' for s in SATS[1:]]
    print(f'\nPair (1000, 1957), measured shift rows: {", ".join(big)} (expected about +0.68, -0.12, -0.78, -0.65).')
    if fails:
        print(f'\n**STOP: requirement failed for {fails}**')

    # ---------------- C.6
    out = bac.band_dir(run) / 'comparison'
    out.mkdir(parents=True, exist_ok=True)
    nb = bac.N_BANDS - 1                        # 957
    ncom = bac.N_FRAMES - nb                    # 43 frames of common ground
    # crop: the 256 columns with the highest luminance std in chief band 1000, frames 0..42, among the
    # windows with alpha 255 in both bands for every satellite
    ok_cols = np.ones(bac.N_COLS, bool)
    for s in SATS:
        a = np.asarray(cubes[s][0, 0:ncom, :, 3]) == 255
        bb = np.asarray(cubes[s][nb, nb:nb + ncom, :, 3]) == 255
        ok_cols &= np.all(a & bb, 0)
        for r, b in zip(rows9, bands9):
            ok_cols &= np.all(np.asarray(cubes[s][b, b:b + ncom, :, 3]) == 255, 0)
    g0 = gray(np.asarray(cubes[0][0, 0:ncom]))
    best_c, best_std = None, -1.0
    for c in range(0, bac.N_COLS - CROP_W + 1, 8):
        if ok_cols[c:c + CROP_W].all():
            sd = g0[:, c:c + CROP_W].std()
            if sd > best_std: best_c, best_std = c, sd
    if best_c is None:
        print('\n**C.6: no fully opaque 256-column window**'); return
    cs_ = slice(best_c, best_c + CROP_W)
    up = lambda im: np.repeat(np.repeat(im, UPS, 0), UPS, 1)
    files = []
    gap = np.full((ncom * UPS, 16, 3), 255, np.uint8)
    for s in SATS:
        a = np.asarray(cubes[s][0, 0:ncom, cs_, :3])
        b = np.asarray(cubes[s][nb, nb:nb + ncom, cs_, :3])
        diff = np.clip(np.abs(b.astype(np.int16) - a.astype(np.int16)) * 10, 0, 255).astype(np.uint8)
        p = out / f'sat{s:02d}_band1000_vs_band1957_offset957_absdiff_x10.png'
        Image.fromarray(np.concatenate([up(a), gap, up(b), gap, up(diff)], 1)).save(p)
        files.append(p)
        frames = []
        for r, bi in zip(rows9, bands9):
            im = Image.fromarray(up(np.asarray(cubes[s][bi, bi:bi + ncom, cs_, :3])))
            ImageDraw.Draw(im).text((6, 4), f'sat {s} band {r} frames {bi}..{bi + ncom - 1}', fill=(255, 255, 0))
            frames.append(im)
        p = out / f'sat{s:02d}_9bands_common_ground.gif'
        frames[0].save(p, save_all=True, append_images=frames[1:], duration=500, loop=0)
        files.append(p)
    print(f'\n## C.6 Visual check ({out.relative_to(run)})\n')
    print(f'Crop: columns {C0 + best_c}..{C0 + best_c + CROP_W - 1} (PNG), {ncom} frames, upscaled x{UPS} nearest; '
          f'luminance std of the chief crop {best_std:.4f}. Panels: band 1000 frames 0..42 | band 1957 frames 957..999 | '
          f'|difference| x10. GIF: band r frames (r - 1000) .. (r - 1000) + 42, 2 frames/s.\n')
    print(md(['file', 'size (MB)', '< 10 MB'], [[f.name, f'{f.stat().st_size / 1e6:.3f}',
                                               'yes' if f.stat().st_size < 10e6 else '**NO**'] for f in files]))


if __name__ == '__main__':
    main()
