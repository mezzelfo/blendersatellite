"""Textured, simplified terrain mesh (terrain_to_render.py --texture-spacing, --simplify-tolerance).

simplify_grid on a synthetic city: the merged mesh covers exactly the drawn quads, has no crack
(every edge inside the drawn area is shared by two triangles), keeps every grid vertex within
2 x tolerance, and uses far fewer triangles. render_texture: texels in the right place and
orientation, so the texture read at a vertex gives the colour of the cache there.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import terrain_to_render as ttr  # noqa: E402


def city(rows=97, cols=97, seed=0):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:rows, 0:cols].astype(float)
    h = 0.02 * x - 0.01 * y + rng.normal(0, 0.03, (rows, cols))
    for _ in range(12):
        r, c = rng.integers(0, rows - 12, 2)
        w, d = rng.integers(3, 12, 2)
        h[r:r + d, c:c + w] += rng.uniform(4, 25)
    if rows > 80:
        h[60:80, 5:40] += rng.normal(0, 0.4, (20, 35))  # rough ground (trees): not merged
    return h


def mesh_points(height, triangles, ids, centres):
    """Vertex positions (x = col, y = row, z) of a simplify_grid result."""
    rows, cols = height.shape
    r, c = np.divmod(ids, cols)
    points = np.c_[c, r, height.ravel()[ids]].astype(float)
    if len(centres):
        points = np.concatenate([points, np.c_[centres[:, 1], centres[:, 0], centres[:, 2]]])
    return points[triangles]


def surface_at(tri, x, y):
    """Height of the triangulated surface at grid points (x, y); NaN outside."""
    z = np.full(len(x), np.nan)
    for (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) in tri:
        det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        box = (x >= min(x0, x1, x2) - 1e-9) & (x <= max(x0, x1, x2) + 1e-9) & \
              (y >= min(y0, y1, y2) - 1e-9) & (y <= max(y0, y1, y2) + 1e-9)
        k = np.flatnonzero(box)
        a = ((x1 - x[k]) * (y2 - y[k]) - (x2 - x[k]) * (y1 - y[k])) / det
        b = ((x2 - x[k]) * (y0 - y[k]) - (x0 - x[k]) * (y2 - y[k])) / det
        c = 1 - a - b
        inside = (a >= -1e-9) & (b >= -1e-9) & (c >= -1e-9)
        z[k[inside]] = (a * z0 + b * z1 + c * z2)[inside]
    return z


@pytest.fixture(scope='module')
def simplified():
    h = city()
    drawable = np.ones((h.shape[0] - 1, h.shape[1] - 1), bool)
    drawable[30:45, 50:70] = False  # a hole (e.g. another member's cells)
    tol = 0.25
    triangles, (ids, centres) = ttr.simplify_grid(h, drawable, tol)
    return dict(h=h, drawable=drawable, tol=tol, tri=mesh_points(h, triangles, ids, centres), triangles=triangles)


def test_fewer_triangles(simplified):
    assert len(simplified['triangles']) < 0.5 * 2 * simplified['drawable'].sum()


def test_covers_the_drawn_quads_once(simplified):
    t = simplified['tri']
    area = .5 * ((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))
    assert area.min() > 0  # counter-clockwise seen from above, none degenerate
    assert np.isclose(area.sum(), simplified['drawable'].sum())
    centroid = t[:, :, :2].mean(1)
    assert simplified['drawable'][np.floor(centroid[:, 1]).astype(int), np.floor(centroid[:, 0]).astype(int)].all()


def test_no_crack(simplified):
    """Every edge used once lies on the border of the drawn area (grid border or hole)."""
    tri = simplified['triangles']
    edges = np.sort(np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]), 1)
    unique, count = np.unique(edges, axis=0, return_counts=True)
    assert count.max() == 2
    t = simplified['tri'].reshape(-1, 3)
    pos = np.zeros((tri.max() + 1, 3))
    pos[tri.ravel()] = t
    a, b = pos[unique[count == 1, 0]], pos[unique[count == 1, 1]]
    drawable = np.pad(simplified['drawable'], 1)  # quads, padded with not drawn

    def border(p, q):  # the quads on both sides of the edge differ in drawability
        mid = (p[:, :2] + q[:, :2]) / 2
        normal = np.c_[-(q[:, 1] - p[:, 1]), q[:, 0] - p[:, 0]]
        normal /= np.linalg.norm(normal, axis=1, keepdims=True)
        left, right = mid + .25 * normal, mid - .25 * normal
        cell = lambda m: drawable[np.floor(m[:, 1]).astype(int) + 1, np.floor(m[:, 0]).astype(int) + 1]  # noqa: E731
        return cell(left) != cell(right)
    assert border(a, b).all()


def test_within_twice_the_tolerance(simplified):
    h, drawable = simplified['h'], simplified['drawable']
    rows, cols = h.shape
    y, x = np.mgrid[0:rows, 0:cols]
    z = surface_at(simplified['tri'], x.ravel().astype(float), y.ravel().astype(float))
    drawn = np.zeros((rows, cols), bool)  # vertices of drawn quads
    for dr in (0, 1):
        for dc in (0, 1):
            drawn[dr:rows - 1 + dr, dc:cols - 1 + dc] |= drawable
    err = np.abs(z - h.ravel())[drawn.ravel()]
    assert np.isfinite(err).all() and err.max() <= 2 * simplified['tol'] + 1e-9


def test_no_merging_without_tolerance():
    h = city(33, 33)
    drawable = np.ones((32, 32), bool)
    drawable[5, 7] = False
    triangles, (ids, centres) = ttr.simplify_grid(h, drawable, 0.)
    assert len(centres) == 0 and len(triangles) == 2 * drawable.sum()


def test_texture_orientation_and_placement(tmp_path):
    """A 1 m cache with R = x, G = y (metres from its corner), its render grid at 2.5 m: the
    texture read (bilinear) at each vertex's uv gives the colour of the cache cell there."""
    x0, y0, n = 25490003.0, 6668001.0, 60
    yy, xx = np.mgrid[0:n, 0:n]
    rgb = np.stack([xx * 4, yy * 4, np.full_like(xx, 77)], -1).astype(np.uint8)
    height = np.zeros((n, n), np.float32)
    height[:, :2] = np.nan  # an empty strip: filled from the nearest covered texel
    np.savez(tmp_path / 't.npz', height=height, rgb=rgb, coverage=np.full((n, n), 16, np.uint8),
             x0=x0, y0=y0, spacing=1.0)
    grid = ttr.render_grid(tmp_path / 't.npz', 2.5)
    image, tx0, ty0 = ttr.render_texture(tmp_path / 't.npz', grid, 2.5, 1.0)
    rows, cols = grid['height'].shape
    assert tx0 <= grid['x0'] and ty0 <= grid['y0']
    assert tx0 + image.shape[1] >= grid['x0'] + cols * 2.5 and ty0 + image.shape[0] >= grid['y0'] + rows * 2.5
    # texel of the cache cell (10, 20) (row = y index from the south)
    r, c = int(round(y0 - ty0)) + 10, int(round(x0 - tx0)) + 20
    assert tuple(image[r, c]) == (80, 40, 77)
    # vertices: texture coordinates as convert_tile computes them, sampled bilinearly
    vx = grid['x0'] + (np.arange(cols) + .5) * 2.5
    vy = grid['y0'] + (np.arange(rows) + .5) * 2.5
    u = (vx - tx0) / image.shape[1] * image.shape[1] - .5
    v = (vy - ty0) / image.shape[0] * image.shape[0] - .5
    inside = (vx > x0 + 3) & (vx < x0 + n - 1)
    rr = np.clip(v.astype(int), 0, image.shape[0] - 2)
    cc = np.clip(u.astype(int), 0, image.shape[1] - 2)
    fu, fv = u - cc, v - rr
    sampled = (image[rr[5], cc, 0] * (1 - fu) + image[rr[5], cc + 1, 0] * fu)
    expected = 4 * (vx - x0 - .5)  # R = 4 x (cell index) at the cell centres, linear in x
    assert np.abs(sampled - expected)[inside].max() < 1.01
    assert abs((image[rr, cc[10], 1] * (1 - fv) + image[rr + 1, cc[10], 1] * fv)[5] - 4 * (vy[5] - y0 - .5)) < 1.01
