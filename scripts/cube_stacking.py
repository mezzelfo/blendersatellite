"""Stacking of the rendered frames of one satellite into a cube cube[g, c, b] (step 13).

Plan: PLAN_CONTEXT.md, "Stacking into per-satellite cubes", decisions S1-S7. NumPy only;
the core functions do no file I/O (the frames come from a caller-supplied reader, the
geometry from stacking_geometry, which reads the poses).

Conventions
-----------
- Band b is the detector row r_b (PNG row, integer = pixel centre). Ground features move
  towards INCREASING row as the frame index k grows (about +1 row per frame).
- Cube line g = row of the ground line in the satellite's OWN frame 0 (integer grid).
- Integer stepping (mode 'none'): sample (g, b) = PNG pixel (r_b, c) of frame k = r_b - g.
  This is exact for the chief only with frame_period_mode ground (one row per frame); with
  the fixed nominal period (about 1.008 rows per frame) and for the deputies a band-to-band
  drift span x (s - 1) remains. Mode 'linear' uses the geometry and is exact in both cases.
- Geometric map (S6): x(k, r, c) = PNG row in frame 0 of the z = height_m ground point seen
  at (r, c) in frame k. x(0, r, c) = r; x decreases with k (x ~ r - s k).
- Fractional stepping (mode 'linear'): k*(g, c, b) solves x(k*, r_b, c) = g (linear
  interpolation of x between integer frames); the RGBA value is interpolated linearly
  between frames floor(k*) and floor(k*) + 1 at row r_b and rounded with np.rint.
  The column stays c: right for nadir views, where a ground point keeps its column.
- Crossing (mode 'bilinear'): the ground point P(g, c) seen by frame-0 pixel (g, c) crosses
  band row r_b at frame k* and column c* (crossing_map); the RGBA value is interpolated
  bilinearly in (frame, column) around (k*, r_b, c*), as reconstruct() in swarm_simulator.
  Needed for oblique views (fore/aft of an async acquisition): with about 9.5 deg of pitch
  a ground point drifts by up to about 3 columns at the image edges between rows 1000
  and 1956 (keystone), which mode 'linear' ignores.

frames_reader(k) contract: returns uint8 [B, C, 4], the RGBA of frame k at rows band_rows
(in band order) and at the cube columns (in column order). build_cube calls it once per
needed frame, in increasing k. raw_reader builds it from the raw rows of render_to_raw.py.
"""
import numpy as np

import stacking_geometry as sg

N_FRAMES = sg.N_FRAMES
HEIGHT = sg.HEIGHT

LAYOUTS = {
    'compact': tuple(range(1520, 1552)),                  # 32 bands, step 1, span 31
    'diagnostic': tuple(1091 + 127 * i for i in range(8)),  # 8 bands, step 127, span 889
}


def band_rows(spec):
    """Band rows (int64 [B]) from a layout name or an explicit list of rows.

    An explicit list must be strictly increasing integers inside 0..HEIGHT-1.
    """
    if isinstance(spec, str):
        if spec not in LAYOUTS: raise KeyError(f'unknown layout {spec!r}; known: {sorted(LAYOUTS)}')
        spec = LAYOUTS[spec]
    rows = np.asarray(spec)
    if rows.ndim != 1 or rows.size == 0: raise ValueError('band rows must be a non-empty 1-D list')
    if not np.issubdtype(rows.dtype, np.integer):
        if not np.all(rows == np.round(rows)): raise ValueError('band rows must be integers')
    rows = rows.astype(np.int64)
    if np.any(np.diff(rows) <= 0): raise ValueError('band rows must be sorted and unique')
    if rows[0] < 0 or rows[-1] > HEIGHT - 1: raise ValueError(f'band rows must lie in 0..{HEIGHT - 1}')
    return rows


def raw_reader(raw, raw_rows, rows, cols):
    """frames_reader over raw data: raw uint8 [R, F, W, 4] (render_to_raw.py, raw[i, k, c] =
    PNG pixel (raw_rows[i], c) of frame k), raw_rows [R] detector rows stored, rows the band
    rows of the cube (each must be stored), cols the contiguous cube columns."""
    raw_rows = np.asarray(raw_rows, dtype=np.int64)
    rows = band_rows(rows)
    where = {int(r): i for i, r in enumerate(raw_rows)}
    missing = [int(r) for r in rows if int(r) not in where]
    if missing: raise ValueError(f'band rows {missing} are not in the raw data')
    idx = np.array([where[int(r)] for r in rows])
    cols = np.asarray(cols, dtype=np.int64)
    c0, c1 = int(cols[0]), int(cols[-1])
    if cols.size != c1 - c0 + 1: raise ValueError('cube columns must be contiguous')

    def read(k):
        return np.ascontiguousarray(raw[idx, k, c0:c1 + 1])
    return read


def complete_lines_integer(rows, n_frames=N_FRAMES):
    """(g_min, g_max) inclusive: lines g with k = r_b - g in 0..n_frames-1 for every band.

    Number of lines = n_frames - span (span = max r_b - min r_b); empty if g_min > g_max.
    """
    rows = band_rows(rows)
    return int(rows.max() - (n_frames - 1)), int(rows.min())


def integer_index(g, rows):
    """Frame index k[g, b] = r_b - g (int64 [G, B])."""
    g = np.asarray(g, dtype=np.int64)
    return band_rows(rows)[None, :] - g[:, None]


def ground_line_map(sat, frames, rows, cols, height_m=0.0, max_points=2_000_000):
    """x[k, r, c] (float64 [K, R, C]): PNG row in frame 0 of satellite sat of the ground
    point at z = height_m seen at PNG (rows[r], cols[c]) in frame frames[k] (S6).

    Vectorised over frames (processed in chunks of about max_points pixels).
    """
    frames = np.asarray(frames, dtype=np.int64).ravel()
    rows = np.asarray(rows, dtype=float).ravel()
    cols = np.asarray(cols, dtype=float).ravel()
    out = np.empty((frames.size, rows.size, cols.size))
    step = max(1, max_points // (rows.size * cols.size))
    for i in range(0, frames.size, step):
        f = frames[i:i + step, None, None]
        xyz = sg.ground_from_pixel(sat, f, rows[None, :, None], cols[None, None, :], height_m)
        out[i:i + step] = sg.pixel_from_ground(sat, 0, xyz)[0]
    # frame 0 maps onto itself at any height: x(0, r, c) = r exactly (the round trip leaves
    # ~1e-11 px, which would push g = max band row just outside the fractional window)
    out[frames == 0] = rows[:, None]
    return out


def _check_monotone(x, axis=0):
    """Return +1 / -1 (per trailing element) if x is strictly monotone along axis; raise otherwise."""
    d = np.diff(x, axis=axis)
    inc, dec = np.all(d > 0, axis=axis), np.all(d < 0, axis=axis)
    if not np.all(inc | dec) or not np.all(np.isfinite(x)):
        raise ValueError('x is not strictly monotone in k')
    return np.where(inc, 1, -1)


def fractional_index(x_of_k, g):
    """k*(g) for one band and column: solves x(k*) = g by linear interpolation between
    integer frames 0..K-1 (x_of_k [K]). x must be strictly monotone (raises otherwise).
    NaN where g is outside [min x, max x], i.e. k* outside 0..K-1. Endpoints are inclusive.
    """
    x = np.asarray(x_of_k, dtype=float)
    if x.ndim != 1 or x.size < 2: raise ValueError('x_of_k must be 1-D with at least 2 frames')
    k = np.arange(x.size, dtype=float)
    if _check_monotone(x) < 0: x, k = x[::-1], k[::-1]
    return np.interp(np.asarray(g, dtype=float), x, k, left=np.nan, right=np.nan)


def fractional_index_map(x, g):
    """k*[g, c, b] (float64 [G, C, B]) from x[k, b, c] (output of ground_line_map with the
    band rows as rows); fractional_index applied to every band and column."""
    x = np.asarray(x, dtype=float)
    _check_monotone(x)
    g = np.asarray(g, dtype=float)
    out = np.empty((g.size, x.shape[2], x.shape[1]))
    for b in range(x.shape[1]):
        for c in range(x.shape[2]):
            out[:, c, b] = fractional_index(x[:, b, c], g)
    return out


def complete_lines_fractional(k_star, g):
    """(g_min, g_max) inclusive of the lines g where k* is finite for every band (and
    column, if k_star is [G, C, B]); (None, None) if there are none. Raises if the finite
    lines are not contiguous (k* is monotone in g, so they must be)."""
    k_star = np.asarray(k_star, dtype=float)
    g = np.asarray(g)
    ok = np.all(np.isfinite(k_star.reshape(k_star.shape[0], -1)), axis=1)
    idx = np.flatnonzero(ok)
    if idx.size == 0: return None, None
    if idx[-1] - idx[0] + 1 != idx.size: raise ValueError('complete lines are not contiguous')
    return g[idx[0]].item(), g[idx[-1]].item()


def crossing_map(sat, rows, g, cols, n_frames=N_FRAMES, height_m=0.0, n_iter=20):
    """(k*, c*), float64 [G, C, B] each: fractional frame and column at which the ground point
    seen by frame-0 PNG pixel (g, c) of satellite sat (at z = height_m) crosses band row r_b.

    The projected pixel of the point is interpolated linearly between integer frames (as the
    x map of mode 'linear'); k* is found by stepping the bracketing frames. NaN where the point
    does not cross the row inside frames 0..n_frames-1.
    """
    rows = band_rows(rows)
    g = np.asarray(g, dtype=float); cols = np.asarray(cols, dtype=float)
    P = sg.ground_from_pixel(sat, 0, g[:, None], cols[None, :], height_m)            # [G, C, 3]
    proj = lambda k: sg.pixel_from_ground(sat, k, P)                                  # rows, cols [G, C]
    r_first, _ = proj(np.zeros(P.shape[:-1], int))
    r_last, _ = proj(np.full(P.shape[:-1], n_frames - 1))
    rate = (r_last - r_first) / (n_frames - 1)                                        # rows per frame
    k_out = np.full((g.size, cols.size, rows.size), np.nan)
    c_out = np.full_like(k_out, np.nan)
    for b, r in enumerate(rows):
        k = np.clip((r - r_first) / rate, 0, n_frames - 1)
        k0 = np.minimum(np.floor(k), n_frames - 2).astype(np.int64)
        for _ in range(n_iter):
            ra, ca = proj(k0); rb, cb = proj(k0 + 1)
            t = (r - ra) / (rb - ra)
            step = np.where(t < 0, -1, np.where(t > 1, 1, 0))
            step = np.where(((k0 == 0) & (step < 0)) | ((k0 == n_frames - 2) & (step > 0)), 0, step)
            if not np.any(step): break
            k0 = k0 + step
        else:
            raise RuntimeError('crossing_map did not converge')
        ok = (t >= 0) & (t <= 1)
        k_out[..., b] = np.where(ok, k0 + t, np.nan)
        c_out[..., b] = np.where(ok, ca + t * (cb - ca), np.nan)
    return k_out, c_out


def build_cube(frames_reader, rows, cols, g, mode, index=None, n_frames=N_FRAMES):
    """Assemble cube[g, c, b, :] (uint8 [G, C, B, 4]; alpha = validity).

    rows: band rows (layout name or list); cols: cube columns [C]; g: cube lines [G].
    mode 'none': index defaults to integer_index(g, rows) (int [G, B]); samples whose
      frame is outside 0..n_frames-1 stay 0 (alpha 0).
    mode 'linear': index = k* is required, float [G, B] (same for every column) or
      [G, C, B]; NaN or outside 0..n_frames-1 stays 0.
    mode 'bilinear': index = (k*, c*) float [2, G, C, B] (crossing_map; c* in PNG columns);
      bilinear in (frame, column); NaN, k* outside 0..n_frames-1 or c* outside
      cols[0]..cols[-1] stays 0.
    Returns (cube, index) with the index actually used.
    """
    rows = band_rows(rows)
    cols, g = np.asarray(cols), np.asarray(g)
    G, C, B = g.size, cols.size, rows.size
    cube = np.zeros((G, C, B, 4), np.uint8)

    def read(k):
        a = np.asarray(frames_reader(int(k)))
        if a.shape != (B, C, 4) or a.dtype != np.uint8:
            raise ValueError(f'frames_reader({k}) returned {a.dtype} {a.shape}, expected uint8 {(B, C, 4)}')
        return a

    if mode == 'none':
        k = integer_index(g, rows) if index is None else np.asarray(index)
        if k.shape != (G, B) or not np.issubdtype(k.dtype, np.integer):
            raise ValueError(f'none mode needs an integer index [G, B] = {(G, B)}')
        gi, bi = np.nonzero((k >= 0) & (k < n_frames))
        kv = k[gi, bi]
        order = np.argsort(kv, kind='stable')
        gi, bi, kv = gi[order], bi[order], kv[order]
        bounds = np.flatnonzero(np.diff(kv)) + 1
        for seg_g, seg_b in zip(np.split(gi, bounds), np.split(bi, bounds)):
            if seg_g.size == 0: continue
            frame = read(k[seg_g[0], seg_b[0]])
            cube[seg_g, :, seg_b] = frame[seg_b]
        return cube, k

    if mode == 'bilinear':
        if index is None: raise ValueError('bilinear mode needs (k*, c*) (index)')
        kc = np.asarray(index, dtype=float)
        if kc.shape != (2, G, C, B): raise ValueError(f'(k*, c*) must be [2, G, C, B], got {kc.shape}')
        if np.any(np.diff(cols) != 1): raise ValueError('bilinear mode needs contiguous columns')
        ks, cs_ = kc[0].reshape(-1), kc[1].reshape(-1) - cols[0]
        valid = np.flatnonzero(np.isfinite(ks) & np.isfinite(cs_) & (ks >= 0) & (ks <= n_frames - 1)
                               & (cs_ >= 0) & (cs_ <= C - 1))
        ks, cs_ = ks[valid], cs_[valid]
        k0 = np.minimum(np.floor(ks), n_frames - 2).astype(np.int64)
        c0 = np.minimum(np.floor(cs_), C - 2).astype(np.int64)
        fk, fc = (ks - k0)[:, None], (cs_ - c0)[:, None]
        order = np.argsort(k0, kind='stable')
        valid, k0, c0, fk, fc = valid[order], k0[order], c0[order], fk[order], fc[order]
        bounds = np.flatnonzero(np.diff(k0)) + 1
        cache = {}
        for seg, kk in zip(np.split(np.arange(valid.size), bounds), k0[np.r_[0, bounds]] if k0.size else []):
            kk = int(kk)
            gi, ci, bi = np.unravel_index(valid[seg], (G, C, B))
            cache = {k: v for k, v in cache.items() if k >= kk}
            for k in (kk, kk + 1):
                if k not in cache: cache[k] = read(k).astype(np.float64)
            cc, wc = c0[seg], fc[seg]
            lo = (1 - wc) * cache[kk][bi, cc] + wc * cache[kk][bi, cc + 1]
            hi = (1 - wc) * cache[kk + 1][bi, cc] + wc * cache[kk + 1][bi, cc + 1]
            cube[gi, ci, bi] = np.clip(np.rint((1 - fk[seg]) * lo + fk[seg] * hi), 0, 255).astype(np.uint8)
        return cube, kc

    if mode != 'linear': raise ValueError(f'unknown mode {mode!r}')
    if index is None: raise ValueError('linear mode needs k* (index)')
    if n_frames < 2: raise ValueError('linear mode needs at least 2 frames')
    k_star = np.asarray(index, dtype=float)
    if k_star.shape == (G, B): full = np.broadcast_to(k_star[:, None, :], (G, C, B))
    elif k_star.shape == (G, C, B): full = k_star
    else: raise ValueError(f'k* must be [G, B] or [G, C, B], got {k_star.shape}')
    flat = full.reshape(-1)
    valid = np.flatnonzero(np.isfinite(flat) & (flat >= 0) & (flat <= n_frames - 1))
    ks = flat[valid]
    k0 = np.minimum(np.floor(ks), n_frames - 2).astype(np.int64)
    frac = ks - k0
    order = np.argsort(k0, kind='stable')
    valid, k0, frac = valid[order], k0[order], frac[order]
    bounds = np.flatnonzero(np.diff(k0)) + 1
    cache = {}                                   # at most frames kk and kk + 1
    for seg, seg_f, kk in zip(np.split(valid, bounds), np.split(frac, bounds),
                              k0[np.r_[0, bounds]] if k0.size else []):
        kk = int(kk)
        gi, ci, bi = np.unravel_index(seg, (G, C, B))
        cache = {k: v for k, v in cache.items() if k >= kk}
        for k in (kk, kk + 1):
            if k not in cache: cache[k] = read(k)
        a = cache[kk][bi, ci].astype(np.float64)
        b = cache[kk + 1][bi, ci].astype(np.float64)
        w = seg_f[:, None]
        cube[gi, ci, bi] = np.clip(np.rint((1.0 - w) * a + w * b), 0, 255).astype(np.uint8)
    return cube, k_star
