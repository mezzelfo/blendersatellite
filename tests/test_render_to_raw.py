"""render_to_raw.py with a fake worker pool (synthetic PNGs, no Blender)."""
import json
import os
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
             satellites=None, n_bands=3, tdi_rows=2, chunk=10, ref_every=10, workers=2, render_args=[],
             queue=None, workers_per_gpu=None, max_workers_per_gpu=4)
    a.update(kw)
    return Namespace(**a)


class FakePool:
    """Stands in for render_pool.WorkerPool: each poll() claims one pending task like a worker
    (rename to claimed/), writes its synthetic PNGs and its done marker."""

    def __init__(self, fail_at=None):
        self.calls, self.fail_at = [], fail_at
        self.devices, self.decisions = [dict(kind='gpu', label='0')], []

    def __call__(self, args, queue, n_tasks):                      # make_pool
        self.args, self.queue = args, queue
        return self

    def start(self):
        pass

    def alive(self, device=None):
        return []

    def stop(self):
        self.queue.requeue()

    def summary(self):
        return []

    def poll(self):
        names = sorted(n for n in os.listdir(self.queue.pending) if n.endswith('.json'))
        if not names:
            return 1
        claimed = self.queue.claimed / f'{names[0][:-5]}@fake.json'
        os.rename(self.queue.pending / names[0], claimed)
        task = json.loads(claimed.read_text())
        s, (start, stop) = task['satellite'], task['frames']
        self.calls.append((s, start, stop))
        if self.fail_at is not None and start == self.fail_at:
            self.fail_at = None
            raise RuntimeError('simulated crash')
        for k in range(start, stop):
            p = rr.frame_png(self.args.output, s, k)
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(truth(s, k), 'RGBA').save(p)
        (self.queue.done / f'{names[0]}').write_text(json.dumps(dict(
            satellite=s, frames=[start, stop], poses=[dict(satellite_id=s, frame=k) for k in range(start, stop)])))
        claimed.unlink()
        return 1


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
    pool = FakePool(fail_at=10)
    with pytest.raises(RuntimeError):
        rr.run_main(args, pool)                         # crashes on the first task 10..19
    rr.run_main(args, pool)                             # resumes
    # the chunks holding frames 0 and F-1 (band rows) come first, for every satellite
    assert sorted(pool.calls[:6]) == sorted((s, a, b) for s in (0, 1, 2) for a, b in ((0, 10), (20, 25)))
    assert pool.calls.count((0, 10, 20)) == 2 and pool.calls.count((0, 0, 10)) == 1
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
    progress = json.loads((raw / 'progress.json').read_text())
    assert progress['frames'] == list(range(F)) and progress['done']['2'] == list(range(F))
    poses = [json.loads(l)['frame'] for l in (args.output / 'satellite_01' / 'poses.jsonl').read_text().splitlines()]
    assert poses == list(range(F))
    n = len(pool.calls)
    rr.run_main(args, pool)                             # nothing left to do
    assert len(pool.calls) == n


def test_keep_frames(tmp_path):
    args = make_args(tmp_path, keep_frames=True)
    rr.run_main(args, FakePool())
    raw = tmp_path / 'run' / 'raw'
    rows = np.array(json.loads((raw / 'bands.json').read_text())['rows'])
    for s in (0, 1, 2):
        assert len(list((args.output / f'satellite_{s:02d}').glob('*.png'))) == F
        D = np.load(raw / f'sat_{s:02d}.npy')
        for k in range(F):
            np.testing.assert_array_equal(D[:, k], truth(s, k)[rows])
    assert not (tmp_path / 'run' / 'reference_frames').exists()
