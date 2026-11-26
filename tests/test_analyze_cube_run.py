"""validation/analyze_cube_run.py on synthetic data: the affine registration recovers a known
scale, rotation and translation; the outlier rejection and the band fit; the local <-> projected
frame round trip."""
import sys
from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer
from scipy.ndimage import gaussian_filter, map_coordinates

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'validation'))
import analyze_cube_run as acr  # noqa: E402


def texture(n=640, seed=0):
    rng = np.random.default_rng(seed)
    return gaussian_filter(rng.random((n, n)), 1.5) * 255


def warp(img, A, t, size):
    """obs(x') = img(inv(I + A) (x' - t)), coordinates relative to the window centre: a feature at
    x in img appears at (I + A) x + t in obs."""
    n = img.shape[0]
    yy, xx = np.mgrid[0:size, 0:size].astype(float)
    p = np.stack([yy - size / 2, xx - size / 2], -1) - t
    q = p @ np.linalg.inv(np.eye(2) + A).T
    return map_coordinates(img, [q[..., 0] + n / 2, q[..., 1] + n / 2], order=3, mode='reflect')


@pytest.mark.parametrize('A,t', [
    (np.diag([0.004, -0.003]), np.array([0.6, -1.3])),
    (np.array([[0.0, -0.002], [0.002, 0.0]]), np.array([0.0, 0.25])),   # small rotation
])
def test_register_recovers_affine(A, t):
    img = texture()
    size = 512
    pred = warp(img, np.zeros((2, 2)), np.zeros(2), size)
    obs = warp(img, A, t, size)
    fit = acr.register(pred, obs, np.ones((size, size), bool), sub=128, step=64, sigma=0.15)
    assert fit is not None
    A_fit, t_fit, rms, n = fit
    assert n >= 40 and rms < 0.05
    assert np.abs(A_fit - A).max() < 3e-4
    assert np.abs(t_fit - t).max() < 0.05


def test_fit_affine_rejects_outliers():
    rng = np.random.default_rng(1)
    pts = rng.uniform(-200, 200, (60, 2))
    A, t = np.array([[1e-3, 0], [0, -2e-3]]), np.array([0.3, -0.1])
    d = t + pts @ A.T + rng.normal(0, 0.01, (60, 2))
    d[:5] += 3.0                                   # five wrong sub-window matches
    A_fit, t_fit, rms, n = acr.fit_affine(pts, d)
    assert n == 55 and np.abs(A_fit - A).max() < 1e-4 and np.abs(t_fit - t).max() < 0.01 and rms < 0.03


def test_band_fit_slope_and_edge():
    rows = np.arange(-1200, 1201, 76.)
    d = 0.05 * rows / 1000 + 0.01
    d[0] += 0.09                                   # edge of the sensor
    inner = np.r_[1:len(rows) - 1]
    slope, intercept, res = acr.band_fit(rows, d, inner)
    assert abs(slope - 0.05) < 1e-9 and abs(intercept - 0.01) < 1e-9
    assert abs(res[0] - 0.09) < 1e-9 and abs(res[-1]) < 1e-9


def test_frame_round_trip():
    lon, lat = 24.6934, 60.2099
    origin = np.array(Transformer.from_crs(4979, 4978, always_xy=True).transform(lon, lat, 0.))
    lo, la = np.radians([lon, lat])
    east = [-np.sin(lo), np.cos(lo), 0.]
    north = [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)]
    manifest = dict(origin_ecef_m=origin.tolist(), local_from_ecef=[east, north, np.cross(east, north).tolist()])
    frame = acr.Frame(manifest, dict(projected_epsg=3879, geographic_epsg=4258))
    xyz = np.array([[0., 0., 0.], [1500., -2500., 35.], [-12000., 18000., -20.]])
    x, y, h = frame.local_to_proj(xyz)
    back = frame.proj_to_local(x, y, h)
    assert np.abs(back - xyz).max() < 1e-4
    assert abs(x[0] - 25483000) < 2000 and abs(y[0] - 6677500) < 2000   # the composite target
