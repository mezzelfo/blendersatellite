"""Synthetic tests of cube_stacking.py (step 13 B). No PNG, no poses.

Run in the conda environment orbits:
    python -m pytest scripts/test_cube_stacking.py -q
"""
import numpy as np
import pytest
from scipy.interpolate import CubicSpline

import cube_stacking as cs

N = 1000
L = cs.HEIGHT                 # texture rows
C = 5                         # synthetic columns


# ---------------------------------------------------------------- integer stepping
@pytest.fixture(scope='module')
def texture():
    return np.random.default_rng(0).integers(0, 256, size=(L, C, 4), dtype=np.uint8)


def integer_reader(T, rows):
    """frame k, row r = T[r - k] (transparent outside T)."""
    def reader(k):
        src = rows - k
        out = np.zeros((rows.size, C, 4), np.uint8)
        ok = (src >= 0) & (src < L)
        out[ok] = T[src[ok]]
        return out
    return reader


@pytest.mark.parametrize('layout, expected', [('compact', 969), ('diagnostic', 111)])
def test_integer_none(texture, layout, expected):
    rows = cs.band_rows(layout)
    g0, g1 = cs.complete_lines_integer(rows, N)
    g = np.arange(g0, g1 + 1)
    assert g.size == N - (rows[-1] - rows[0]) == expected
    k = cs.integer_index(g, rows)
    assert k.min() == 0 and k.max() == N - 1
    cube, idx = cs.build_cube(integer_reader(texture, rows), rows, np.arange(C), g, 'none', n_frames=N)
    assert cube.shape == (g.size, C, rows.size, 4) and cube.dtype == np.uint8
    assert np.array_equal(idx, k)
    for b in range(rows.size):
        assert np.array_equal(cube[:, :, b], texture[g]), f'band {b}'


@pytest.mark.parametrize('layout', ['compact', 'diagnostic'])
def test_integer_linear_with_integer_kstar_is_exact(texture, layout):
    """linear mode with k* = r_b - g (integer, including k* = N - 1) must equal mode none."""
    rows = cs.band_rows(layout)
    g = np.arange(*np.add(cs.complete_lines_integer(rows, N), (0, 1)))
    k_star = cs.integer_index(g, rows).astype(float)
    cube, _ = cs.build_cube(integer_reader(texture, rows), rows, np.arange(C), g, 'linear', index=k_star, n_frames=N)
    assert np.array_equal(cube, np.broadcast_to(texture[g][:, :, None], cube.shape))


@pytest.mark.parametrize('layout', ['compact', 'diagnostic'])
def test_off_by_one_is_detected(texture, layout):
    rows = cs.band_rows(layout)
    g = np.arange(*np.add(cs.complete_lines_integer(rows, N), (0, 1)))
    wrong = cs.integer_index(g, rows) + 1          # k = r_b - g + 1
    cube, _ = cs.build_cube(integer_reader(texture, rows), rows, np.arange(C), g, 'none', index=wrong, n_frames=N)
    with pytest.raises(AssertionError):
        for b in range(rows.size):
            assert np.array_equal(cube[:, :, b], texture[g])


# ---------------------------------------------------------------- fractional stepping
# Smooth band-limited texture, shortest period 8 rows. Worst-case linear-interpolation
# error sum A (2 pi / P)^2 / 8 = 0.02 + 0.13 + 0.15 + 0.12 = 0.41 DN < 0.5 DN.
WAVES = ((97.0, 40.0), (31.0, 25.0), (13.0, 5.0), (8.0, 1.5))


def smooth(x, c, ch):
    """Float texture value at frame-0 row x, column c, channel ch (broadcasting)."""
    v = 128.0
    for i, (period, amp) in enumerate(WAVES):
        v = v + amp * np.sin(2 * np.pi * x / period + 0.7 * c + 1.3 * ch + 2.1 * i)
    return v


def smooth_reader(rows, s):
    """frame k, row r = rint(f(r - s k)): ground advances s rows per frame."""
    c = np.arange(C)[None, :, None]
    ch = np.arange(4)[None, None, :]
    def reader(k):
        x = (rows - s * k)[:, None, None]
        return np.rint(smooth(x, c, ch)).astype(np.uint8)
    return reader


def k_star_synthetic(rows, s, g):
    """k*[g, b] from the exact map x(k, r_b) = r_b - s k."""
    k = np.arange(N)
    return np.stack([cs.fractional_index(r - s * k, g) for r in rows], axis=1)


def measure_shift(a, b, max_shift=1.5, trim=4):
    """Shift d (rows) with a(g) ~ b(g - d), maximising the normalised cross-correlation
    summed over the columns and channels of a, b [G, C, 4] (b cubic-spline interpolated)."""
    G = a.shape[0]
    g = np.arange(G, dtype=float)
    inner = slice(trim + 2, G - trim - 2)
    spline = CubicSpline(g, b.astype(float).reshape(G, -1), axis=0)
    av = a.astype(float).reshape(G, -1)[inner]
    av = av - av.mean(0)

    def ncc(d):
        bv = spline(g[inner] - d)
        bv = bv - bv.mean(0)
        return np.sum(np.sum(av * bv, 0) / np.sqrt(np.sum(av ** 2, 0) * np.sum(bv ** 2, 0)))

    grid = np.arange(-max_shift, max_shift + 1e-9, 0.01)
    d0 = grid[np.argmax([ncc(d) for d in grid])]
    fine = np.arange(d0 - 0.01, d0 + 0.01 + 1e-9, 0.0002)
    return fine[np.argmax([ncc(d) for d in fine])]


@pytest.mark.parametrize('s', [1.0008, 0.9992])
@pytest.mark.parametrize('layout', ['compact', 'diagnostic'])
def test_fractional_linear_within_1dn(layout, s):
    rows = cs.band_rows(layout)
    g_cand = np.arange(rows[-1] - N - 5, rows[0] + 6)
    k_star = k_star_synthetic(rows, s, g_cand)
    g0, g1 = cs.complete_lines_fractional(k_star, g_cand)
    # analytic: x(N-1, r_b) = r_b - s (N-1) <= g <= r_b for every band
    assert (g0, g1) == (int(np.ceil(rows[-1] - s * (N - 1))), int(rows[0]))
    sel = (g_cand >= g0) & (g_cand <= g1)
    g, k_star = g_cand[sel], k_star[sel]
    cube, _ = cs.build_cube(smooth_reader(rows, s), rows, np.arange(C), g, 'linear', index=k_star, n_frames=N)
    truth = np.rint(smooth(g[:, None, None], np.arange(C)[None, :, None], np.arange(4)[None, None, :]))
    err = np.abs(cube.astype(float) - truth[:, :, None, :])
    assert err.max() <= 1, err.max()


@pytest.mark.parametrize('s', [1.0008, 0.9992])
@pytest.mark.parametrize('layout', ['compact', 'diagnostic'])
def test_fractional_none_band_shift(layout, s):
    rows = cs.band_rows(layout)
    g = np.arange(*np.add(cs.complete_lines_integer(rows, N), (0, 1)))
    cube, _ = cs.build_cube(smooth_reader(rows, s), rows, np.arange(C), g, 'none', n_frames=N)
    span = rows[-1] - rows[0]
    measured = measure_shift(cube[:, :, -1], cube[:, :, 0])
    assert abs(measured - span * (s - 1)) < 0.01, (measured, span * (s - 1))


# ---------------------------------------------------------------- fractional_index
def test_fractional_index_monotone_and_nan():
    k = np.arange(10, dtype=float)
    x_dec = 100.0 - 1.25 * k                        # like the real map (decreasing)
    ks = cs.fractional_index(x_dec, [100.0, 88.75, 95.0, 100.0 + 1e-9, 88.75 - 1e-9])
    assert ks[0] == 0.0 and ks[1] == 9.0            # both window ends inclusive
    assert ks[2] == pytest.approx(4.0)
    assert np.isnan(ks[3]) and np.isnan(ks[4])      # just outside 0..N-1
    ks_inc = cs.fractional_index(-x_dec, [-100.0, -88.75, -95.0, -101.0])
    assert ks_inc[0] == 0.0 and ks_inc[1] == 9.0 and ks_inc[2] == pytest.approx(4.0) and np.isnan(ks_inc[3])
    for bad in (np.r_[x_dec[:5], x_dec[4], x_dec[6:]],   # flat step
                np.r_[x_dec[:5], x_dec[5] + 10, x_dec[6:]],  # reversal
                np.r_[x_dec[:5], np.nan, x_dec[6:]]):
        with pytest.raises(ValueError):
            cs.fractional_index(bad, [95.0])
    with pytest.raises(ValueError):
        cs.fractional_index_map(np.stack([x_dec, x_dec[::-1] * 0 + 5], 1)[:, :, None], [95.0])


def test_complete_lines_fractional_and_nan_samples_are_transparent():
    rows = cs.band_rows([10, 12])
    g = np.arange(0, 20)
    k_star = np.stack([cs.fractional_index(r - 0.5 * np.arange(N), g) for r in rows], 1)
    assert cs.complete_lines_fractional(k_star, g) == (0, 10)
    reader = lambda k: np.full((2, 1, 4), 200, np.uint8)
    cube, _ = cs.build_cube(reader, rows, [0], g, 'linear', index=k_star, n_frames=N)
    assert np.all(cube[np.isnan(k_star)[:, None, :].repeat(1, 1)] == 0)
    assert np.all(cube[~np.isnan(k_star)[:, None, :].repeat(1, 1)] == 200)


def test_band_rows_validation():
    assert cs.band_rows('compact').tolist() == list(range(1520, 1552))
    assert cs.band_rows('diagnostic').tolist() == [1091, 1218, 1345, 1472, 1599, 1726, 1853, 1980]
    for bad in ([5, 3], [3, 3], [-1, 4], [0, 3072], [1.5, 2]):
        with pytest.raises(ValueError):
            cs.band_rows(bad)
    with pytest.raises(KeyError):
        cs.band_rows('nope')
