"""Synthetic end-to-end test of terrain_cache.py and terrain_to_render.py (no network).

Two OBJ ZIPs (300 x 300 m at x0 = 320000, y0 = 5810000, EPSG:7855) are built in a
temporary directory, with a 1024 x 1024 JPEG texture whose R = u, G = v (row 0 is v = 1)
and B = 50, all files inside a subfolder of the archive:
  SMALL: ground of 3 m triangles on the slope z = 0.01 x, plus a 20 x 20 m building
         (x, y in 100..120) with a flat roof at 30 m and four vertical walls;
  BIG:   the ground as only two triangles (flat, z = 0), plus the same building.
Both are cached at 1 m and 2.5 m with --archives (default: with facade voxels), then converted
to a render frame centred on the tile; the two caches must give the same render grid.
The step-22 tests below (facades and walls) add tiles WALLS, SEAM_W and SEAM_E.

  conda run -n orbits python -m pytest -q tests/test_terrain_cache.py
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
sys.path.insert(0, str(ROOT / 'pipeline'))
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
        run(ROOT / 'pipeline' / 'terrain_cache.py', '--dataset', 'melbourne_2020', '--tiles', 'SMALL', 'BIG',
            '--archives', archives, '--output', cache, '--spacing', spacing, '--workers', 1)
    cameras = tmp / 'cameras.json'
    enu_cameras(cameras, ['SMALL', 'BIG'])
    render = {}
    for spacing in SPACINGS:
        render[spacing] = tmp / f'render_{spacing:g}m'
        run(ROOT / 'pipeline' / 'terrain_to_render.py', '--cameras', cameras, '--cache',
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


# ----------------------------------------------------------------------------- facades and walls (step 22)
#
# WALLS: flat ground (z = 0) with a 20 x 20 m building, 30 m high, x, y in [100, 120]
#        (on the 1 m cache grid and the 2.5 m render grid). A facade sample on the plane x = 100
#        falls in voxel ix = 100 (the side of increasing x), so the west and south walls are in
#        the building's outer voxel ring and the east and north walls just outside it. Wall texture walls.png: three solid bands, red | green | blue. North wall
#        red below z = 15 m and green above, the other three walls blue. Placed 1 km north of the
#        other tiles so it has no neighbours.
# SEAM_W, SEAM_E: x in [0, 150] and [150, 300]; flat ground, and in SEAM_E a blue building at
#        x in [150, 170], y in [100, 120], whose west wall stands on the tile seam x = 150.

B0, B1, ROOF, SPLIT = 100.0, 120.0, 30.0, 15.0
WALLS_DY = 1000.0
BAND_U = dict(red=1 / 6, green=1 / 2, blue=5 / 6)
COLOURS = dict(red=(255, 0, 0), green=(0, 255, 0), blue=(0, 0, 255))


def facade_obj(x_range, y_range, building, north_split):
    """OBJ text: two ground triangles over x_range x y_range, an optional building (x0, x1, y0, y1)
    with roof (material ground) and outward-wound walls (material walls)."""
    vertices, uvs, ground, walls = [], [], [], []

    def vertex(x, y, z, uv):
        vertices.append((x, y, z)); uvs.append(uv)
        return len(vertices)

    (xa, xb), (ya, yb) = x_range, y_range
    g = [vertex(x, y, 0., (x / SIZE % 1, y / SIZE % 1)) for x, y in ((xa, ya), (xb, ya), (xb, yb), (xa, yb))]
    ground += [(g[0], g[1], g[2]), (g[0], g[2], g[3])]
    if building:
        x0, x1, y0, y1 = building
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]  # counter-clockwise: walls wound outward
        roof = [vertex(x, y, ROOF, (.5, .5)) for x, y in corners]
        ground += [(roof[0], roof[1], roof[2]), (roof[0], roof[2], roof[3])]
        for k in range(4):
            (ax, ay), (bx, by) = corners[k], corners[(k + 1) % 4]
            north = k == 2
            bands = [(0., SPLIT, 'red'), (SPLIT, ROOF, 'green')] if north and north_split else [(0., ROOF, 'blue')]
            for z0, z1, band in bands:
                uv = (BAND_U[band], .5)
                q = [vertex(ax, ay, z0, uv), vertex(bx, by, z0, uv), vertex(bx, by, z1, uv), vertex(ax, ay, z1, uv)]
                walls += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    lines = ['mtllib tile.mtl']
    lines += [f'v {X0 + x:.6f} {Y0 + y:.6f} {z:.6f}' for x, y, z in vertices]
    lines += [f'vt {u:.9f} {v:.9f}' for u, v in uvs]
    for material, faces in (('ground', ground), ('walls', walls)):
        lines.append(f'usemtl {material}')
        lines += ['f ' + ' '.join(f'{i}/{i}' for i in face) for face in faces]
    return '\n'.join(lines) + '\n'


def make_facade_zip(path, obj):
    folder = path.stem + '/'
    jpeg, png = path.parent / 'texture.jpg', path.parent / 'walls.png'
    Image.fromarray(texture_jpeg()).save(jpeg, quality=95, subsampling=0)
    bands = np.zeros((32, 96, 3), dtype=np.uint8)
    for k, name in enumerate(('red', 'green', 'blue')):
        bands[:, 32 * k:32 * (k + 1)] = COLOURS[name]
    Image.fromarray(bands).save(png)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(folder + 'tile.obj', obj)
        archive.writestr(folder + 'tile.mtl', 'newmtl ground\nmap_Kd texture.jpg\nnewmtl walls\nmap_Kd walls.png\n')
        archive.write(jpeg, folder + 'texture.jpg')
        archive.write(png, folder + 'walls.png')


def shift_obj(text, dy):
    """Move every vertex of an OBJ by dy in y."""
    out = []
    for line in text.splitlines():
        if line.startswith('v '):
            x, y, z = map(float, line.split()[1:])
            line = f'v {x:.6f} {y + dy:.6f} {z:.6f}'
        out.append(line)
    return '\n'.join(out) + '\n'


@pytest.fixture(scope='module')
def walls_built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('walls')
    archives = tmp / 'archives'
    archives.mkdir()
    make_facade_zip(archives / 'WALLS.zip', shift_obj(
        facade_obj((0., SIZE), (0., SIZE), (B0, B1, B0, B1), north_split=True), WALLS_DY))
    make_facade_zip(archives / 'SEAM_W.zip', facade_obj((0., 150.), (0., SIZE), None, False))
    make_facade_zip(archives / 'SEAM_E.zip', facade_obj((150., SIZE), (0., SIZE), (150., 170., B0, B1), False))
    make_zip(archives / 'SMALL.zip', small=True)
    tiles = ['WALLS', 'SEAM_W', 'SEAM_E']
    cache = tmp / 'cache'
    # Default (facades) into 1m/, as the pipeline; grid only (--no-facades) into 1m_grid/.
    run(ROOT / 'pipeline' / 'terrain_cache.py', '--dataset', 'melbourne_2020', '--tiles', *tiles, 'SMALL',
        '--archives', archives, '--output', cache, '--spacing', 1, '--workers', 1)
    run(ROOT / 'pipeline' / 'terrain_cache.py', '--dataset', 'melbourne_2020', '--tiles', *tiles, 'SMALL',
        '--archives', archives, '--output', cache, '--spacing', 1, '--workers', 1, '--no-facades', '--tag', 'grid')
    cameras = tmp / 'cameras.json'
    enu_cameras(cameras, tiles)
    render = tmp / 'render_walls'
    run(ROOT / 'pipeline' / 'terrain_to_render.py', '--cameras', cameras, '--cache', cache / 'melbourne_2020' / '1m',
        '--dataset', 'melbourne_2020', '--output', render, '--walls')
    plain = tmp / 'render_plain'
    run(ROOT / 'pipeline' / 'terrain_to_render.py', '--cameras', cameras, '--cache', cache / 'melbourne_2020' / '1m',
        '--dataset', 'melbourne_2020', '--output', plain)
    return dict(cache=cache / 'melbourne_2020', render=render, plain=plain, cameras=cameras, tiles=tiles,
                archives=archives, root=cache)


def facade_voxels(built, tile):
    with np.load(built['cache'] / '1m' / f'{tile}.npz') as data:
        return {k: data[k] for k in data.files}


def expected_voxels():
    """Voxel (ix, iy, iz) relative to (X0, Y0 + WALLS_DY) -> expected (rgb, normal x 127) for WALLS.

    A voxel holds the samples of every wall crossing it: only the south-west corner voxel
    (100, 100) holds two walls (west and south, 1 m^2 each), so its normal is their mean."""
    walls = {}
    i0, i1 = int(B0), int(B1)
    for iz in range(int(ROOF)):
        north = COLOURS['red'] if iz < SPLIT else COLOURS['green']
        for i in range(i0, i1):
            walls.setdefault((i0, i, iz), []).append((COLOURS['blue'], (-1, 0)))   # west, x = B0
            walls.setdefault((i1, i, iz), []).append((COLOURS['blue'], (1, 0)))    # east, x = B1
            walls.setdefault((i, i0, iz), []).append((COLOURS['blue'], (0, -1)))   # south, y = B0
            walls.setdefault((i, i1, iz), []).append((north, (0, 1)))              # north, y = B1
    return {k: (np.mean([c for c, _ in v], 0), 127 * np.mean([n for _, n in v], 0)) for k, v in walls.items()}


def test_facade_voxels_only_on_walls(walls_built):
    data = facade_voxels(walls_built, 'WALLS')
    ijk = data['facade_ijk'].astype(np.int64) - [int(X0), int(Y0 + WALLS_DY), 0]
    expected = expected_voxels()
    assert float(data['facade_voxel']) == 1.0
    assert {tuple(v) for v in ijk} == set(expected)
    assert data['facade_count'].min() >= 1
    # >= 16 samples per m^2: 4 walls of 20 x 30 m.
    assert int(data['facade_samples']) >= 16 * 4 * 20 * 30
    assert abs(float(data['facade_area_m2']) - 4 * 20 * 30) < 1e-6


def test_facade_voxel_colours_and_normals(walls_built):
    data = facade_voxels(walls_built, 'WALLS')
    ijk = data['facade_ijk'].astype(np.int64) - [int(X0), int(Y0 + WALLS_DY), 0]
    expected = expected_voxels()
    for key, rgb, normal in zip(map(tuple, ijk), data['facade_rgb'].astype(int), data['facade_normal'].astype(int)):
        colour, direction = expected[key]
        assert np.abs(rgb - colour).max() <= 1, (key, rgb, colour)
        if np.count_nonzero(np.abs(direction) > 1) == 2:
            # Corner voxel: equal areas of two walls, sampled by two different lattices, so the
            # shares differ a little. Its normal must stay within 60 deg of both walls' normals.
            unit = normal / np.linalg.norm(normal)
            for wall in (np.sign(direction) * [1, 0], np.sign(direction) * [0, 1]):
                assert unit @ wall > np.cos(np.radians(60)), (key, normal)
        else:
            assert np.abs(normal - direction).max() <= 1, (key, normal, direction)


def test_facades_keep_grid_bit_for_bit(walls_built):
    for tile in walls_built['tiles'] + ['SMALL']:
        with np.load(walls_built['cache'] / '1m' / f'{tile}.npz') as a, \
                np.load(walls_built['cache'] / '1m_grid' / f'{tile}.npz') as b:
            assert 'facade_ijk' in a.files and 'facade_ijk' not in b.files
            for key in ('height', 'rgb', 'coverage', 'x0', 'y0', 'spacing', 'supersample'):
                assert np.array_equal(a[key], b[key], equal_nan=True), (tile, key)


def native_walls(walls_built):
    """build_walls on the native grids of the three tiles (same code as terrain_to_render.py --walls)."""
    from terrain_to_render import build_walls, facade_loader, render_grid
    cache = walls_built['cache'] / '1m'
    grids = {t: render_grid(cache / f'{t}.npz', 2.5) for t in walls_built['tiles']}
    load = facade_loader(cache)
    return {t: build_walls(t, grids, load, 2.5, 2.0) for t in walls_built['tiles']}


def segment_frame(walls, dy=0.):
    """Per segment: edge line in tile coordinates and the expected wall name."""
    q = walls['quads'] - [X0, Y0 + dy, 0]
    return q, walls['normal']


def test_walls_colour_from_voxels(walls_built):
    walls = native_walls(walls_built)['WALLS']
    q, normal = segment_frame(walls, WALLS_DY)
    assert walls['edges'] == 4 * 8 and len(q) == 4 * 8 * 12
    assert (walls['source'] == 0).all(), np.bincount(walls['source'])
    z0, z1 = q[:, 0, 2], q[:, 2, 2]
    assert np.all(z1 - z0 <= 2.5 + 1e-9)
    is_north = normal[:, 1] > .5
    expected = np.where(is_north[:, None], np.where((z1 <= SPLIT + 1e-9)[:, None], COLOURS['red'], COLOURS['green']),
                        COLOURS['blue'])
    mixed = is_north & (z0 < SPLIT - 1e-9) & (z1 > SPLIT + 1e-9)
    assert mixed.sum() <= 1
    error = np.abs(walls['rgb'].astype(int) - expected).max(1)
    assert error[~mixed].max() <= 1, error[~mixed].max()


def test_no_walls_on_flat_ground(walls_built):
    walls = native_walls(walls_built)['WALLS']
    q, _ = segment_frame(walls, WALLS_DY)
    xs, ys = q[:, :2, 0], q[:, :2, 1]
    on_x = (np.abs(xs - B0) < 1e-6).all(1) | (np.abs(xs - B1) < 1e-6).all(1)
    on_y = (np.abs(ys - B0) < 1e-6).all(1) | (np.abs(ys - B1) < 1e-6).all(1)
    inside = (xs >= B0 - 1e-6).all(1) & (xs <= B1 + 1e-6).all(1) & (ys >= B0 - 1e-6).all(1) & (ys <= B1 + 1e-6).all(1)
    assert np.all((on_x | on_y) & inside)
    assert np.allclose(q[:, 0, 2].min(), 0) and np.allclose(q[:, 2, 2].max(), ROOF)
    flat = native_walls(walls_built)['SEAM_W']
    q, _ = segment_frame(flat)
    assert (np.abs(q[:, :, 0] - 150.) < 1e-6).all()  # its only walls are the seam wall


def test_seam_wall_built_once(walls_built):
    walls = native_walls(walls_built)
    on_seam = {t: (np.abs(segment_frame(w)[0][:, :2, 0] - 150.) < 1e-6).all(1) for t, w in walls.items() if t != 'WALLS'}
    # West wall of the SEAM_E building, x = 150: owned by SEAM_W (west cell), 8 edges x 12 segments.
    assert on_seam['SEAM_W'].sum() == 8 * 12 and on_seam['SEAM_W'].all()
    assert on_seam['SEAM_E'].sum() == 0
    assert walls['SEAM_E']['edges'] == 3 * 8 and walls['SEAM_W']['edges'] == 8
    for tile in ('SEAM_W', 'SEAM_E'):  # coloured from voxels, including SEAM_E's voxels seen from SEAM_W
        w = walls[tile]
        assert (w['source'] == 0).all()
        assert np.abs(w['rgb'].astype(int) - COLOURS['blue']).max() <= 2


def test_render_npz_walls(walls_built):
    native = native_walls(walls_built)
    for tile in walls_built['tiles']:
        with np.load(walls_built['render'] / f'{tile}.npz') as a, np.load(walls_built['plain'] / f'{tile}.npz') as b:
            assert 'wall_vertices' not in b.files
            for key in ('vertices', 'rgb', 'shape'):  # grid keys unchanged by --walls
                assert np.array_equal(a[key], b[key])
            n = len(native[tile]['quads'])
            assert int(a['wall_segments']) == n
            assert a['wall_vertices'].shape == (4 * n, 3) and a['wall_vertices'].dtype == np.float32
            assert np.array_equal(a['wall_rgb'], np.repeat(native[tile]['rgb'], 4, axis=0))
            assert int(a['wall_source_counts'].sum()) == n
    frame = json.loads((walls_built['render'] / 'cache_frame.json').read_text())
    assert frame['walls']['threshold_m'] == 2.0


def test_walls_refuse_mixing(walls_built, tmp_path):
    # --walls into a folder made without walls, and --walls on a cache without facades.
    for cache, output in ((walls_built['cache'] / '1m', walls_built['plain']),
                          (walls_built['cache'] / '1m_grid', tmp_path / 'out')):
        result = subprocess.run([sys.executable, ROOT / 'pipeline' / 'terrain_to_render.py', '--cameras', walls_built['cameras'],
                                 '--cache', cache, '--dataset', 'melbourne_2020', '--output', output, '--walls'],
                                cwd=ROOT, capture_output=True, text=True)
        assert result.returncode != 0


def test_cache_refuses_mixed_tiles(walls_built):
    """terrain_cache.py refuses a folder whose tiles differ from the request, in both directions
    (facades vs grid only, facade voxel size, supersample), even for tiles not yet cached there."""
    base = [sys.executable, ROOT / 'pipeline' / 'terrain_cache.py', '--dataset', 'melbourne_2020', '--tiles', 'BIGNEW',
            '--archives', walls_built['archives'], '--output', walls_built['root'], '--spacing', 1, '--workers', 1]
    cases = [['--no-facades'],                                     # grid-only request, facade folder 1m/
             ['--tag', 'grid'],                                    # facade request, grid-only folder 1m_grid/
             ['--facade-voxel', '0.5'],                            # other voxel size
             ['--supersample', '2']]                               # other supersampling
    for extra in cases:
        result = subprocess.run(list(map(str, base + extra)), cwd=ROOT, capture_output=True, text=True)
        assert result.returncode != 0 and 'built differently' in result.stderr, (extra, result.stderr[-500:])
    # Matching requests still pass (dry run, nothing written).
    for extra in ([], ['--no-facades', '--tag', 'grid']):
        result = subprocess.run(list(map(str, base + extra + ['--dry-run'])), cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, (extra, result.stderr[-500:])
