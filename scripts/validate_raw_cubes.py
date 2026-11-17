"""Check the cube reconstruction from raw rows (render_to_raw.py -> build_cubes.py --source raw)
on synthetic data with the real poses of a run, without Blender.

A smooth synthetic texture T(x, y) is laid on the local ground plane z = 0. For every satellite
the raw rows are computed as raw[i, k, c] = T(ground_from_pixel(sat, k, rows[i], c)) on a
column window, written in the render_to_raw.py layout in a temporary run folder, and the cube is
built with build_cubes.build_one. The cube line g, column c is the ground seen by the satellite's
frame 0 at (g, c), so every band of a co-registered cube must show T(ground_from_pixel(sat, 0, g, c)).

    python scripts/validate_raw_cubes.py --cameras RUN/simulation/cameras.json [--satellites 0 1 3]
"""
import argparse
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np

import build_cubes as bc
import render_to_raw as rr
import stacking_geometry as sg


def texture(xyz):
    """Smooth RGB pattern on the ground (periods of 60-200 m, much longer than the 4.7 m GSD)."""
    x, y = xyz[..., 0], xyz[..., 1]
    r = 128 + 90 * np.sin(2 * np.pi * x / 97.0) * np.cos(2 * np.pi * y / 131.0)
    g = 128 + 90 * np.sin(2 * np.pi * (x + y) / 173.0)
    b = 128 + 90 * np.cos(2 * np.pi * (x - 0.5 * y) / 61.0)
    return np.stack([r, g, b], -1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cameras', type=Path, required=True)
    ap.add_argument('--satellites', type=int, nargs='+', default=[0, 1, 3])
    ap.add_argument('--n-bands', type=int, default=8)
    ap.add_argument('--tdi-rows', type=int, default=2)
    ap.add_argument('--usable-rows', type=int, nargs=2, default=[1000, 1957])
    ap.add_argument('--cols', type=int, nargs=2, default=[1900, 2199])
    ap.add_argument('--png-check', type=int, default=0, metavar='N',
                    help='also write the first N frames as PNGs (raw rows only, rest transparent) and check that '
                         'build_cubes gives identical cubes from PNGs and from raw data, for the three modes; '
                         'uses N frames and the band rows --png-rows')
    ap.add_argument('--png-rows', type=int, nargs=2, default=[1500, 1539])
    args = ap.parse_args()
    run = Path(tempfile.mkdtemp(prefix='raw_cube_check_'))
    try:
        (run / 'simulation').mkdir()
        shutil.copy(args.cameras, run / 'simulation' / 'cameras.json')
        sg.set_paths(run_dir=run, cameras_json=run / 'simulation' / 'cameras.json')
        n_frames = len(json.loads(args.cameras.read_text())['epochs'])
        ok = np.zeros(sg.HEIGHT, bool); ok[args.usable_rows[0]:args.usable_rows[1] + 1] = True
        bands = rr.select_band_rows(ok, args.n_bands, args.tdi_rows)
        bands.update(satellites=args.satellites, n_frames=n_frames)
        (run / 'raw').mkdir()
        rr.write_json(run / 'raw' / 'bands.json', bands)
        rr.write_json(run / 'raw' / 'progress.json', dict(frames=list(range(n_frames))))
        rows = np.asarray(bands['rows'])
        cols = np.arange(args.cols[0], args.cols[1] + 1)
        print(f'bands {bands["band_start_rows"]}, {args.tdi_rows} rows each; columns {cols[0]}..{cols[-1]}; '
              f'{n_frames} frames')
        print('| sat | role | mode | layout | lines | max |cube - truth| [DN] | rms [DN] | max band-to-band [DN] | samples with a missing band |')
        print('|---|---|---|---|---|---|---|---|---|')
        for s in args.satellites:
            raw = np.lib.format.open_memmap(run / 'raw' / f'sat_{s:02d}.npy', mode='w+', dtype=np.uint8,
                                            shape=(rows.size, n_frames, sg.WIDTH, 4))
            for k0 in range(0, n_frames, 100):
                k = np.arange(k0, min(n_frames, k0 + 100))[:, None, None]
                xyz = sg.ground_from_pixel(s, k, rows[None, :, None], cols[None, None, :])
                block = np.clip(np.rint(texture(xyz)), 0, 255).astype(np.uint8)        # [K, R, C, 3]
                raw[:, k0:k0 + block.shape[0], cols[0]:cols[-1] + 1, :3] = block.transpose(1, 0, 2, 3)
                raw[:, k0:k0 + block.shape[0], cols[0]:cols[-1] + 1, 3] = 255
            raw.flush(); del raw
            role = sg.load_poses(s)
            meta = json.loads(args.cameras.read_text())['epochs'][0]['cameras'][s].get('acquisition_role', '')
            for mode in ('bilinear', 'linear', 'none'):
                bc.build_one(run, s, 'raw_bands', mode, 1, True, source='raw')
                d = np.load(run / 'cubes' / f'raw_bands_{mode}' / f'cube_sat{s:02d}.npz')
                cube, g, cc = d['cube'], d['g'], d['cols']
                truth = texture(sg.ground_from_pixel(s, 0, g[:, None], cc[None, :]))     # [G, C, 3]
                valid = cube[..., 3] > 0
                err = np.abs(cube[..., :3].astype(float) - truth[:, :, None, :])[valid]
                full = valid.all(axis=2)                                                    # every band valid
                rgb = cube[..., :3].astype(int)
                spread = (rgb.max(axis=2) - rgb.min(axis=2))[full].max()
                edge = 1 - full.mean()
                print(f'| {s} | {meta} | {mode} | raw_bands | {g.size} | {err.max():.2f} | '
                      f'{np.sqrt(np.mean(err**2)):.2f} | {spread} | {edge:.2%} |', flush=True)
        if args.png_check:
            png_check(args, run)
    finally:
        shutil.rmtree(run)


def png_check(args, run):
    """Same cubes from PNG frames and from raw rows (first N frames, rows --png-rows)."""
    from PIL import Image
    n = args.png_check
    manifest = json.loads(args.cameras.read_text())
    manifest['epochs'] = manifest['epochs'][:n]
    (run / 'simulation' / 'cameras.json').write_text(json.dumps(manifest))
    sg.set_paths(run_dir=run, cameras_json=run / 'simulation' / 'cameras.json')
    ok = np.zeros(sg.HEIGHT, bool); ok[args.png_rows[0]:args.png_rows[1] + 1] = True
    bands = rr.select_band_rows(ok, 4, 2)
    bands.update(satellites=args.satellites, n_frames=n)
    rr.write_json(run / 'raw' / 'bands.json', bands)
    rr.write_json(run / 'raw' / 'progress.json', dict(frames=list(range(n))))
    rows = np.asarray(bands['rows'])
    cols = np.arange(args.cols[0], args.cols[1] + 1)
    print(f'PNG check: {n} frames, rows {rows.tolist()}')
    for s in args.satellites:
        raw = np.lib.format.open_memmap(run / 'raw' / f'sat_{s:02d}.npy', mode='w+', dtype=np.uint8,
                                        shape=(rows.size, n, sg.WIDTH, 4))
        k = np.arange(n)[:, None, None]
        xyz = sg.ground_from_pixel(s, k, rows[None, :, None], cols[None, None, :])
        raw[:, :, cols[0]:cols[-1] + 1, :3] = np.clip(np.rint(texture(xyz)), 0, 255).astype(np.uint8).transpose(1, 0, 2, 3)
        raw[:, :, cols[0]:cols[-1] + 1, 3] = 255
        raw.flush()
        out = run / 'renders' / f'satellite_{s:02d}'
        out.mkdir(parents=True, exist_ok=True)
        for kk in range(n):
            img = np.zeros((sg.HEIGHT, sg.WIDTH, 4), np.uint8)
            img[rows] = raw[:, kk]
            Image.fromarray(img, 'RGBA').save(out / f'frame_{kk:04d}.png', compress_level=1)
        del raw
        for mode in ('none', 'linear', 'bilinear'):
            bc.build_one(run, s, 'raw_rows', mode, 4, True, source='raw')
            a = dict(np.load(run / 'cubes' / f'raw_rows_{mode}' / f'cube_sat{s:02d}.npz'))
            bc.build_one(run, s, 'raw_rows', mode, 4, True, source='png')
            b = dict(np.load(run / 'cubes' / f'raw_rows_{mode}' / f'cube_sat{s:02d}.npz'))
            same = all(np.array_equal(a[key], b[key]) for key in ('cube', 'g', 'cols', 'band_rows', 'frame_index', 'frame_t_s'))
            print(f'| {s} | {mode} | PNG vs raw identical: {same} | lines {a["g"].size} | '
                  f't_s {a["frame_t_s"][0]:.6f}..{a["frame_t_s"][-1]:.6f} |', flush=True)
            if not same: raise SystemExit('PNG and raw cubes differ')


if __name__ == '__main__':
    main()
