"""Synthetic end-to-end test of terrain_cache.py and terrain_to_render.py (no network).

Two OBJ ZIPs (300 x 300 m at x0 = 320000, y0 = 5810000, EPSG:7855) are built in a
temporary directory, with a 1024 x 1024 JPEG texture whose R = u, G = v (row 0 is v = 1)
and B = 50, all files inside a subfolder of the archive:
  SMALL: ground of 3 m triangles on the slope z = 0.01 x, plus a 20 x 20 m building
         (x, y in 100..120) with a flat roof at 30 m and four vertical walls;
  BIG:   the ground as only two triangles (flat, z = 0), plus the same building.
Both are cached at 1 m and 2.5 m with --archives, then converted to a render frame
centred on the tile; the two caches must give the same render grid.

  conda run -n orbits python -m pytest -q scripts/test_terrain_cache.py
"""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from terrain_to_render import bin_to_spacing  # noqa: E402

X0, Y0, SIZE, SLOPE = 320000.0, 5810000.0, 300.0, 0.01
SPACINGS = (1.0, 2.5)


def texture_jpeg():
    """1024 x 1024 RGB: R = u, G = v with image row 0 at v = 1, B = 50."""
    n = 1024
    u = (np.arange(n) + .5) / n
    v = 1 - (np.arange(n) + .5) / n
    image = np.empty((n, n, 3), dtype=np.uint8)
    image[..., 0] = np.rint(255 * u)[None, :]
    image[..., 1] = np.rint(255 * v)[:, None]
    image[..., 2] = 50
    return image


def obj_text(small):
    """OBJ with absolute EPSG:7855 coordinates and UV = (x, y) / SIZE."""
    vertices, faces = [], []

    def vertex(x, y, z):
        vertices.append((x, y, z))
        return len(vertices)  # OBJ indices are 1-based

    def ground_z(x):
        return SLOPE * x if small else 0.

    step = 3.0 if small else SIZE
    n = int(round(SIZE / step))
    ids = [[vertex(i * step, j * step, ground_z(i * step)) for i in range(n + 1)] for j in range(n + 1)]
    for j in range(n):
        for i in range(n):
            a, b, c, d = ids[j][i], ids[j][i + 1], ids[j + 1][i], ids[j + 1][i + 1]
            faces += [(a, b, d), (a, d, c)]
    corners = [(100., 100.), (120., 100.), (120., 120.), (100., 120.)]
    roof = [vertex(x, y, 30.) for x, y in corners]
    base = [vertex(x, y, ground_z(x)) for x, y in corners]
    faces += [(roof[0], roof[1], roof[2]), (roof[0], roof[2], roof[3])]
    for k in range(4):  # vertical walls: zero area in top view
        m = (k + 1) % 4
        faces += [(base[k], base[m], roof[m]), (base[k], roof[m], roof[k])]
    lines = ['mtllib tile.mtl']
    lines += [f'v {X0 + x:.6f} {Y0 + y:.6f} {z:.6f}' for x, y, z in vertices]
    lines += [f'vt {x / SIZE:.9f} {y / SIZE:.9f}' for x, y, _ in vertices]
    lines.append('usemtl mat0')
    lines += ['f ' + ' '.join(f'{i}/{i}' for i in face) for face in faces]
    return '\n'.join(lines) + '\n'


def make_zip(path, small):
    folder = path.stem + '/'
    jpeg = path.parent / 'texture.jpg'
    Image.fromarray(texture_jpeg()).save(jpeg, quality=95, subsampling=0)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(folder + 'tile.obj', obj_text(small))
        archive.writestr(folder + 'tile.mtl', 'newmtl mat0\nKd 1 1 1\nmap_Kd texture.jpg\n')
        archive.write(jpeg, folder + 'texture.jpg')


def enu_cameras(path, tiles, spacing=2.5):
    """Minimal cameras.json: local ENU frame at the tile centre (z = 0)."""
    lon, lat = Transformer.from_crs(7855, 7844, always_xy=True).transform(X0 + SIZE / 2, Y0 + SIZE / 2)
    origin = Transformer.from_crs(4979, 4978, always_xy=True).transform(lon, lat, 0.)
    lo, la = np.radians(lon), np.radians(lat)
    rotation = [[-np.sin(lo), np.cos(lo), 0.],
                [-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)],
                [np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]]
    path.write_text(json.dumps(dict(origin_ecef_m=list(origin), local_from_ecef=rotation,
                                    mesh_grid_spacing_m=spacing, selected_tiles=tiles)))


def run(*args):
    subprocess.run([sys.executable, *map(str, args)], check=True, cwd=ROOT)


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('terrain')
    archives = tmp / 'archives'
    archives.mkdir()
    make_zip(archives / 'SMALL.zip', small=True)
    make_zip(archives / 'BIG.zip', small=False)
    cache = tmp / 'cache'
    for spacing in SPACINGS:
        run(ROOT / 'terrain_cache.py', '--dataset', 'melbourne_2020', '--tiles', 'SMALL', 'BIG',
            '--archives', archives, '--output', cache, '--spacing', spacing, '--workers', 1)
    cameras = tmp / 'cameras.json'
    enu_cameras(cameras, ['SMALL', 'BIG'])
    render = {}
    for spacing in SPACINGS:
        render[spacing] = tmp / f'render_{spacing:g}m'
        run(ROOT / 'terrain_to_render.py', '--cameras', cameras, '--cache',
            cache / 'melbourne_2020' / f'{spacing:g}m', '--dataset', 'melbourne_2020', '--output', render[spacing])
    return dict(cache=cache / 'melbourne_2020', render=render)


def load(built, tile, spacing):
    with np.load(built['cache'] / f'{spacing:g}m' / f'{tile}.npz') as data:
        return {k: data[k] for k in data.files}


def value_at(grid, key, x, y):
    """Cell of the native grid containing the point (x, y) relative to the tile origin."""
    spacing = float(grid['spacing'])
    col = int(np.floor((X0 + x - float(grid['x0'])) / spacing))
    row = int(np.floor((Y0 + y - float(grid['y0'])) / spacing))
    return grid[key][row, col]


CASES = [(tile, spacing) for tile in ('SMALL', 'BIG') for spacing in SPACINGS]


@pytest.mark.parametrize('tile,spacing', CASES)
def test_full_coverage(built, tile, spacing):
    grid = load(built, tile, spacing)
    assert np.isfinite(grid['height']).all()
    assert grid['height'].shape == (round(SIZE / spacing),) * 2


@pytest.mark.parametrize('tile,spacing', CASES)
def test_roof_height(built, tile, spacing):
    assert abs(value_at(load(built, tile, spacing), 'height', 110.5, 110.5) - 30.) < 1e-3


@pytest.mark.parametrize('tile,spacing', CASES)
def test_ground_height(built, tile, spacing):
    grid = load(built, tile, spacing)
    expected = SLOPE * 50.5 if tile == 'SMALL' else 0.
    if spacing != 1.0:  # the cell containing (50.5, 50.5) is centred at 51.25 on a 2.5 m grid
        expected = SLOPE * 51.25 if tile == 'SMALL' else 0.
    assert abs(value_at(grid, 'height', 50.5, 50.5) - expected) < 1e-3


@pytest.mark.parametrize('tile,spacing', CASES)
def test_colour(built, tile, spacing):
    grid = load(built, tile, spacing)
    rgb = value_at(grid, 'rgb', 200.5, 250.5).astype(int)
    # (170, 213, 50) is the colour at (200.5, 250.5); the 2.5 m cell is centred at
    # (201.25, 251.25), i.e. (171.1, 213.6, 50), still within 2 DN.
    expected = np.array([170, 213, 50])
    assert np.abs(rgb - expected).max() <= 2, (rgb, expected)


@pytest.mark.parametrize('tile,spacing', CASES)
def test_grid_alignment(built, tile, spacing):
    grid = load(built, tile, spacing)
    for key in ('x0', 'y0'):
        ratio = float(grid[key]) / spacing
        assert abs(ratio - round(ratio)) < 1e-9


@pytest.mark.parametrize('tile,spacing', CASES)
def test_row0_is_south(built, tile, spacing):
    """G = v grows northwards, so the first row must be darker in G than the last."""
    rgb = load(built, tile, spacing)['rgb']
    assert rgb[0, :, 1].mean() + 100 < rgb[-1, :, 1].mean()


@pytest.mark.parametrize('tile', ('SMALL', 'BIG'))
def test_render_grids_agree(built, tile):
    grids = {}
    for spacing in SPACINGS:
        with np.load(built['render'][spacing] / f'{tile}.npz') as data:
            grids[spacing] = {k: data[k] for k in ('vertices', 'rgb', 'shape')}
    a, b = grids[1.0], grids[2.5]
    assert (a['shape'] == b['shape']).all()
    assert np.abs(a['vertices'] - b['vertices']).max() < 0.01
    assert np.abs(a['rgb'].astype(int) - b['rgb'].astype(int)).max() <= 1


def test_binning_is_unbiased(built):
    """Extra check: binning the 1 m SMALL cache to 2.5 m must reproduce z = 0.01 x at the
    2.5 m cell centres. A centre-in-bin assignment shifts the bin centroid by -0.25 m
    (bias -0.0025 m on this slope); an area-overlap binning has no bias."""
    grid = load(built, 'SMALL', 1.0)
    h, _, gx0, _ = bin_to_spacing(grid['height'], grid['rgb'], grid['coverage'], float(grid['x0']),
                                  float(grid['y0']), float(grid['spacing']), 2.5)
    xc = gx0 + (np.arange(h.shape[1]) + .5) * 2.5 - X0
    ground = np.ones(h.shape, bool)
    ground[np.ix_(np.abs(np.arange(h.shape[0]) * 2.5 + 1.25 - 110) < 15,
                  np.abs(xc - 110) < 15)] = False  # skip the building and its edges
    error = np.abs(h - SLOPE * xc[None, :])[ground]
    assert error.max() < 1e-3, error.max()
