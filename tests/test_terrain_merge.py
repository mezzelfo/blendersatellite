"""Composite terrain (terrain_merge.py + terrain_to_render.py) on two synthetic members.

Member A (reference) and member B overlap by 50 m. B is 1.5 m lower than A and its colours obey
A = 0.8 B + 20, so the fitted correction must give dh = +1.5 m, gain 0.8 and offset 20; after the
correction B matches A, and B's faces are dropped where A has data.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import terrain_merge as tm  # noqa: E402

X0, Y0 = 25490000.0, 6668000.0
GRID = dict(type='grid', tile_size_m=100, id_regex=r'^(?P<x>\d{3})(?P<y>\d{3})$', x_origin_m=X0, y_origin_m=Y0, id_unit_m=50)


def member(tiles):
    return dict(format='obj_zip', url='https://example.invalid/{tile}.zip', projected_epsg=3879, geographic_epsg=4258,
                raster_bounds='tile', index=dict(GRID, tiles=tiles))


def colour_a(rows, cols):
    """A smooth colour pattern of the reference (60..200 DN), varying in x and y."""
    y, x = np.mgrid[0:rows, 0:cols]
    return np.stack([60 + x, 80 + y, 60 + (x + y) // 2], -1).astype(np.uint8)


def write_cache(path, height, rgb, x0, y0):
    rows, cols = height.shape
    np.savez_compressed(path, height=height.astype(np.float32), rgb=rgb, coverage=np.full((rows, cols), 16, np.uint8),
                        x0=x0, y0=y0, spacing=1.0, supersample=4, epsg=3879, dataset='test', tile='t',
                        facade_ijk=np.zeros((0, 3), np.int32), facade_rgb=np.zeros((0, 3), np.uint8),
                        facade_normal=np.zeros((0, 2), np.int8), facade_count=np.zeros(0, np.uint32),
                        facade_voxel=1.0, facade_faces=0, facade_area_m2=0., facade_samples=0)


@pytest.fixture(scope='module')
def composite(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('composite')
    datasets = dict(A=member(['000000']), B=member(['001000']),
                    AB=dict(members=['A', 'B'], projected_epsg=3879, geographic_epsg=4258))
    datasets_path = tmp / 'datasets.json'
    datasets_path.write_text(json.dumps(datasets))
    root = tmp / 'terrain'
    (root / 'A' / '1m').mkdir(parents=True)
    (root / 'B' / '1m').mkdir(parents=True)
    a_rgb = colour_a(100, 150)                                  # A's colours over x 0..150 (shared with B)
    write_cache(root / 'A' / '1m' / '000000.npz', np.full((100, 100), 10.), a_rgb[:, :100], X0, Y0)
    b_rgb = np.rint((a_rgb[:, 50:150].astype(float) - 20) / 0.8).astype(np.uint8)
    write_cache(root / 'B' / '1m' / '001000.npz', np.full((100, 100), 8.5), b_rgb, X0 + 50, Y0)
    run = [sys.executable, str(ROOT / 'pipeline' / 'terrain_merge.py'), '--dataset', 'AB', '--spacing', '1',
           '--output', str(root), '--datasets', str(datasets_path), '--min-cells', '1000']
    subprocess.run(run, check=True, capture_output=True, text=True)
    model = json.loads((root / 'AB' / '1m' / 'correction.json').read_text())
    # A run whose local frame is centred on the shared strip, terrain bounds over both tiles.
    lon, lat = Transformer.from_crs(3879, 4258, always_xy=True).transform(X0 + 75, Y0 + 50)
    origin = np.array(Transformer.from_crs(4979, 4978, always_xy=True).transform(lon, lat, 0.))
    lo, la = np.deg2rad([lon, lat])
    east = np.array([-np.sin(lo), np.cos(lo), 0.])
    north = np.array([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)])
    cameras = tmp / 'cameras.json'
    cameras.write_text(json.dumps(dict(origin_ecef_m=origin.tolist(), local_from_ecef=[east.tolist(), north.tolist(),
                       np.cross(east, north).tolist()], terrain_bounds_local_m=[[-60., -40.], [60., 40.]],
                       mesh_grid_spacing_m=2.5)))
    out = tmp / 'render'
    subprocess.run([sys.executable, str(ROOT / 'pipeline' / 'terrain_to_render.py'), '--cameras', str(cameras),
                    '--cache', str(root / 'AB' / '1m'), '--dataset', 'AB', '--output', str(out),
                    '--datasets', str(datasets_path), '--walls'], check=True, capture_output=True, text=True)
    textured = tmp / 'render_textured'
    subprocess.run([sys.executable, str(ROOT / 'pipeline' / 'terrain_to_render.py'), '--cameras', str(cameras),
                    '--cache', str(root / 'AB' / '1m'), '--dataset', 'AB', '--output', str(textured),
                    '--datasets', str(datasets_path), '--walls', '--texture-spacing', '1', '--simplify-tolerance', '0.25',
                    '--decimate', '0.5'],
                   check=True, capture_output=True, text=True)
    return dict(model=model, out=out, textured=textured, a_rgb=a_rgb)


def test_correction_model(composite):
    entry = composite['model']['members']['B']
    assert composite['model']['reference'] == 'A' and entry['pairs_used'] == 1
    blocks = np.asarray(entry['height']['blocks'])
    assert len(blocks) == 1 and abs(blocks[0, 2] - 1.5) < 1e-6
    assert np.allclose(entry['colour']['gain'], 0.8, atol=0.01) and np.allclose(entry['colour']['offset'], 20, atol=1.)


def test_outputs_and_tiles_json(composite):
    tiles = json.loads((composite['out'] / 'tiles.json').read_text())
    assert tiles == dict(dataset='AB', tiles=['A__000000', 'B__001000'])
    assert (composite['out'] / 'A__000000.npz').exists() and (composite['out'] / 'B__001000.npz').exists()


def test_priority_mask(composite):
    """A is drawn everywhere; B only east of A's tile (x >= 100 m), plus one quad of overlap."""
    with np.load(composite['out'] / 'A__000000.npz') as a, np.load(composite['out'] / 'B__001000.npz') as b:
        assert a['face_keep'].all()
        keep = b['face_keep']
        rows, cols = (int(n) for n in b['shape'])
        assert keep.shape == (rows - 1, cols - 1)
        # B's render cells: x from 50 m in 2.5 m cells; A has data up to x = 100 m (cell 20)
        assert not keep[:, :19].any() and keep[:, 20:].all()


def test_member_heights_corrected(composite):
    """After the correction B's surface is at A's height (10 m) where both are drawn."""
    with np.load(composite['out'] / 'A__000000.npz') as a, np.load(composite['out'] / 'B__001000.npz') as b:
        za = a['vertices'].reshape(*a['shape'], 3)[..., 2]
        zb = b['vertices'].reshape(*b['shape'], 3)[..., 2]
        # local z includes the Earth curvature (< 1 mm here): compare the seam columns directly
        assert abs(float(np.median(za[:, -1])) - float(np.median(zb[:, 20]))) < 0.01


def test_height_field_taper():
    field = tm.HeightField(dict(block_m=250., taper_m=2000., blocks=[[0., 0., 2., 5000]]))
    assert np.isclose(field(0., 0.), 2.) and np.isclose(field(250., 0.), 2.)
    assert np.isclose(field(1250., 0.), 1.) and np.isclose(field(2250., 0.), 0.) and field(5000., 0.) == 0.


def test_reject_outliers():
    """On a 5 x 5 grid of blocks with a smooth ramp, a single spike is rejected and the ramp is kept."""
    x, y = np.meshgrid(np.arange(5) * 250., np.arange(5) * 250.)
    dh = 0.002 * x  # +2 m over 1 km: a real, coherent offset
    blocks = np.c_[x.ravel(), y.ravel(), dh.ravel(), np.full(25, 5000.)]
    blocks[12, 2] += 15.  # centre block: a surface that changed between the flights
    kept, rejected = tm.reject_outliers(blocks, outlier_m=2., neighbour_m=600.)
    assert len(rejected) == 1 and rejected[0, 0] == 500. and rejected[0, 1] == 500.
    assert len(kept) == 24
    single, none = tm.reject_outliers(blocks[:1], 2., 600.)
    assert len(single) == 1 and len(none) == 0


def test_textured_composite(composite):
    """Textured and simplified conversion of the composite: flat members merged into few triangles,
    B's drawn area the same as with face_keep, B's texture corrected to A's colours, walls kept."""
    from PIL import Image
    tex, old = composite['textured'], composite['out']
    frame = json.loads((tex / 'cache_frame.json').read_text())
    assert frame['mesh'] == dict(texture_spacing_m=1.0, simplify_tolerance_m=0.25, decimate=0.5)
    with np.load(old / 'B__001000.npz') as b_old, np.load(tex / 'B__001000.npz') as b:
        assert 'rgb' not in b.files and 'face_keep' not in b.files
        t = b['vertices'][b['triangles']]
        area = .5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1])
                           - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))
        assert np.isclose(area.sum(), b_old['face_keep'].sum() * 2.5 ** 2, rtol=1e-3)
        assert len(b["triangles"]) < 0.5 * 2 * b_old['face_keep'].sum()
        assert (b['wall_vertices'].shape == b_old['wall_vertices'].shape)
        image = np.asarray(Image.open(tex / str(b['texture'])))[::-1]  # row 0 south
        assert np.all((b['uv'] >= 0) & (b['uv'] <= 1))
    # B's texture starts at x = 50 m: its first 50 columns are the strip shared with A
    shared = image[:, :50].astype(int) - composite['a_rgb'][:, 50:100].astype(int)
    assert np.abs(shared).max() <= 2
    with np.load(tex / 'A__000000.npz') as a:
        assert len(a["triangles"]) < 0.5 * 2 * 39 * 39
