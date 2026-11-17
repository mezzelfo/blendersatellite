"""render_to_raw.py with a fake renderer (synthetic PNGs, no Blender)."""
import json
from argparse import Namespace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import render_to_raw as rr

W, H, F = 64, 96, 25
G0, G1, C0, C1 = 5, 80, 3, 58          # scene in frame-0 rows G0..G1, columns C0..C1


def truth(sat, k):
    """Synthetic frame: the ground line g = r - (1 + 0.01 sat) k, RGBA from (g, c)."""
    r = np.arange(H)[:, None].astype(float)
    c = np.arange(W)[None, :]
    g = np.floor(r - (1 + 0.01*sat) * k)
    inside = (g >= G0 - 0) & (g <= G1) & (c >= C0) & (c <= C1)
    img = np.zeros((H, W, 4), np.uint8)
    img[..., 0] = (g * 7 + c) % 251
    img[..., 1] = (g * 3 + 2 * c + sat) % 253
    img[..., 2] = (k + c) % 255
    img[..., 3] = np.where(inside, 255, 0)
    img[~inside, :3] = 0
    return img


def make_args(tmp, **kw):
    epochs = [dict(t_s=k * 1e-3, cameras=[dict(satellite_id=s, resolution=[W, H], t_s=k * 1e-3 + s,
                                               acquisition_role='nadir') for s in (0, 1, 2)]) for k in range(F)]
    cams = tmp / 'cameras.json'
    cams.write_text(json.dumps(dict(epochs=epochs, frame_period_s=1e-3)))
    a = dict(cameras=cams, terrain=tmp / 'terrain', output=tmp / 'run' / 'renders', raw=None, references=None,
             satellites=None, n_bands=3, tdi_rows=2, block=10, ref_every=10, workers=2, render_args=[])
    a.update(kw)
    return Namespace(**a)


class FakeRender:
    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at

    def __call__(self, args, start, stop, sats):
        self.calls.append((start, stop))
        if self.fail_at is not None and start == self.fail_at:
            self.fail_at = None
            raise RuntimeError('simulated crash')
        for s in sats:
            for k in range(start, stop):
                p = rr.frame_png(args.output, s, k)
                if p.exists():
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(truth(s, k), 'RGBA').save(p)


def test_select_band_rows():
    ok = np.zeros(3072, bool); ok[1001:1958] = True
    b = rr.select_band_rows(ok, 32, 8)
    starts = np.array(b['band_start_rows'])
    assert b['usable_rows'] == [1001, 1957] and len(starts) == 32
    assert np.all(starts % 4 == 0) and starts[0] >= 1001 and starts[-1] + 7 <= 1957
    assert np.min(np.diff(starts)) >= 8 and len(b['rows']) == 256
    with pytest.raises(ValueError):
        rr.select_band_rows(ok, 200, 8)


def test_render_to_raw_with_resume(tmp_path):
    args = make_args(tmp_path)
    render = FakeRender(fail_at=10)
    with pytest.raises(RuntimeError):
        rr.run_main(args, render)                       # crashes on block 10..19
    rr.run_main(args, render)                           # resumes
    assert render.calls[:2] == [(0, 1), (F - 1, F)]    # frames for the band rows
    assert (10, 20) in render.calls and render.calls.count((0, 10)) == 1
    raw = tmp_path / 'run' / 'raw'
    bands = json.loads((raw / 'bands.json').read_text())
    rows = np.array(bands['rows'])
    # rows seeing the scene in frames 0 and F-1 for the slowest-moving.. every satellite
    assert bands['usable_rows'][0] >= G0 + (1 + 0.02) * (F - 1) - 1 and bands['usable_rows'][1] <= G1
    for s in (0, 1, 2):
        D = np.load(raw / f'sat_{s:02d}.npy')
        assert D.shape == (len(rows), F, W, 4)
        for k in range(F):
            np.testing.assert_array_equal(D[:, k], truth(s, k)[rows])
        meta = json.loads((raw / f'sat_{s:02d}.json').read_text())
        assert meta['rows_seeing_scene_in_every_frame'] == len(rows)
        assert meta['frame_t_s'][3] == pytest.approx(3e-3 + s)
        left = sorted(p.name for p in (args.output / f'satellite_{s:02d}').glob('*.png'))
        assert left == []
        refs = sorted(p.name for p in (tmp_path / 'run' / 'reference_frames' / f'satellite_{s:02d}').glob('*.png'))
        assert refs == ['frame_0000.png', 'frame_0010.png', 'frame_0020.png', 'frame_0024.png']
    assert json.loads((raw / 'progress.json').read_text())['frames'] == list(range(F))
    n = len(render.calls)
    rr.run_main(args, render)                           # nothing left to do
    assert len(render.calls) == n


def test_keep_frames(tmp_path):
    args = make_args(tmp_path, keep_frames=True)
    rr.run_main(args, FakeRender())
    raw = tmp_path / 'run' / 'raw'
    rows = np.array(json.loads((raw / 'bands.json').read_text())['rows'])
    for s in (0, 1, 2):
        assert len(list((args.output / f'satellite_{s:02d}').glob('*.png'))) == F
        D = np.load(raw / f'sat_{s:02d}.npy')
        for k in range(F):
            np.testing.assert_array_equal(D[:, k], truth(s, k)[rows])
    assert not (tmp_path / 'run' / 'reference_frames').exists()
