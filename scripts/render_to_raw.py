"""Render a run block by block and keep only the raw pushbroom data of selected detector rows.

The rendered frames are full 4096 x 3072 RGBA snapshots (about 8 MB each). A linear-variable-filter
pushbroom only reads a few detector rows per band, so this driver keeps, for every satellite,

    raw/sat_SS.npy   uint8 [R, F, W, 4]   raw[i, k, c] = frame_k[rows[i], c]   (RGBA as in the PNG)

with R = n_bands * tdi_rows detector rows (tdi_rows adjacent rows per band, for a later TDI model),
F frames and all W columns (alpha = scene coverage). It is the raw data of swarm_simulator's
Acquisition.raw (band, frame, column) with the TDI rows added. Everything else in the frames is
deleted, except one reference frame every --ref-every frames (and the last one), moved to
reference_frames/satellite_SS/. With --keep-frames every PNG stays in renders/ (no deletion,
no move): the raw data are extracted and verified all the same.

Steps
1. Band rows (once, raw/bands.json): render frames 0 and F-1 of every satellite and keep the rows
   that see the scene (alpha > 0 in at least one column) in BOTH frames for EVERY satellite; the
   ground moves monotonically along the rows, so these rows see the scene over the whole window.
   n_bands bands of tdi_rows adjacent rows are spread evenly over that interval, start rows
   multiples of 4 (HyperScape100 band start rows, parameter 0x33).
2. Blocks of --block frames: render_swarm.py --frames a:b --skip-existing for the satellites, read
   the PNGs, write the rows into the memory-mapped raw files, read them back and compare with the
   PNGs, then delete (or keep as reference) each PNG. raw/progress.json lists the extracted frames,
   so a run that stops is resumed by launching the same command again.
3. raw/sat_SS.json: shape, rows, band layout, frame times and coverage of every stored row.

Run in the conda environment orbits (Pillow), from the repository root, e.g.
    python scripts/render_to_raw.py --cameras RUN/simulation/cameras.json --terrain RUN/terrain_render \\
        --output RUN/renders --blender /snap/blender/current/blender -- --engine EEVEE --samples 8
Arguments after '--' go to render_swarm.py unchanged.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
RENDER_SWARM = REPO / 'render_swarm.py'
PNG_BYTES = 8.5e6            # upper estimate of one rendered frame
MARGIN_BYTES = 5e9


def write_json(path, data):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(data, indent=1) + '\n')
    os.replace(tmp, path)


def frame_png(renders, sat, k):
    return Path(renders) / f'satellite_{sat:02d}' / f'frame_{k:04d}.png'


def read_rgba(path, size=None):
    with Image.open(path) as im:
        if im.mode != 'RGBA':
            raise ValueError(f'{path}: mode {im.mode}, expected RGBA')
        a = np.asarray(im)
    if size is not None and a.shape[:2] != (size[1], size[0]):
        raise ValueError(f'{path}: {a.shape[1]} x {a.shape[0]} px, expected {size[0]} x {size[1]}')
    return a


def _read_rows(task):
    path, rows, size = task
    return read_rgba(path, size)[rows]


def blender_render(args, start, stop, sats):
    """render_swarm.py for frames start:stop of the satellites, resuming existing PNGs."""
    cmd = [args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
           '--python', str(RENDER_SWARM), '--', '--cameras', str(args.cameras), '--terrain', str(args.terrain),
           '--output', str(args.output), '--frames', f'{start}:{stop}',
           '--satellites', ','.join(map(str, sats)), '--skip-existing', *args.render_args]
    print('$ ' + ' '.join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def select_band_rows(row_ok, n_bands, tdi_rows, align=4):
    """Band start rows and stored rows from a boolean mask of usable detector rows.

    The longest run of usable rows [r0, r1] hosts n_bands bands of tdi_rows adjacent rows, start
    rows multiples of `align`, spread evenly from the first to the last possible start."""
    idx = np.flatnonzero(row_ok)
    if idx.size == 0:
        raise ValueError('no detector row sees the scene in every checked frame')
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
    run = max(runs, key=len)
    r0, r1 = int(run[0]), int(run[-1])
    lo = -(-r0 // align) * align
    hi = (r1 - tdi_rows + 1) // align * align
    if hi < lo:
        raise ValueError(f'usable rows {r0}..{r1} too few for one band of {tdi_rows} rows')
    starts = (np.round(np.linspace(lo, hi, n_bands) / align) * align).astype(int)
    if n_bands > 1 and np.min(np.diff(starts)) < tdi_rows:
        raise ValueError(f'usable rows {r0}..{r1} too few for {n_bands} bands of {tdi_rows} rows')
    rows = (starts[:, None] + np.arange(tdi_rows)[None]).ravel()
    return dict(usable_rows=[r0, r1], band_start_rows=starts.tolist(), rows=rows.tolist(),
                n_bands=n_bands, tdi_rows=tdi_rows, start_row_alignment=align)


def frame_times(manifest, sat):
    """t_s [s] and acquisition role of every frame of a satellite (its own record time)."""
    t, role = [], None
    for epoch in manifest['epochs']:
        c = next(c for c in epoch['cameras'] if c['satellite_id'] == sat)
        t.append(c.get('t_s', epoch['t_s']))
        role = c.get('acquisition_role', role)
    return t, role


def main():
    argv = sys.argv[1:]
    render_args = argv[argv.index('--') + 1:] if '--' in argv else []
    argv = argv[:argv.index('--')] if '--' in argv else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cameras', type=Path, required=True)
    ap.add_argument('--terrain', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True, help='renders folder (temporary PNGs)')
    ap.add_argument('--raw', type=Path, help='default: OUTPUT/../raw')
    ap.add_argument('--references', type=Path, help='default: OUTPUT/../reference_frames')
    ap.add_argument('--blender', default=os.environ.get('BLENDER_BIN', '/snap/blender/current/blender'))
    ap.add_argument('--satellites', default=None, help='comma-separated ids; default all in cameras.json')
    ap.add_argument('--n-bands', type=int, default=32)
    ap.add_argument('--tdi-rows', type=int, default=8)
    ap.add_argument('--block', type=int, default=100)
    ap.add_argument('--ref-every', type=int, default=100)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--keep-frames', action='store_true', help='extract the raw rows but keep every PNG')
    args = ap.parse_args(argv)
    args.render_args = render_args
    reserved = {'--frames', '--satellites', '--output', '--cameras', '--terrain', '--crop', '--skip-existing'}
    clash = sorted(a.split('=')[0] for a in render_args if a.split('=')[0] in reserved)
    if clash:
        ap.error(f'{clash} are set by this driver; use its own options (--satellites) instead')
    run_main(args)


def run_main(args, render=blender_render):
    manifest = json.loads(args.cameras.read_text())
    n_frames = len(manifest['epochs'])
    cams0 = manifest['epochs'][0]['cameras']
    sats = sorted(c['satellite_id'] for c in cams0) if args.satellites is None else \
        [int(s) for s in str(args.satellites).split(',')]
    size = tuple(cams0[0]['resolution'])                                   # (W, H)
    raw_dir = args.raw or args.output.parent / 'raw'
    ref_dir = args.references or args.output.parent / 'reference_frames'
    raw_dir.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    last = n_frames - 1
    is_reference = lambda k: k % args.ref_every == 0 or k == last

    # 1. band rows
    bands_path = raw_dir / 'bands.json'
    if bands_path.exists():
        bands = json.loads(bands_path.read_text())
        if bands['satellites'] != sats or bands['n_frames'] != n_frames:
            raise SystemExit(f'{bands_path} was made for satellites {bands["satellites"]}, '
                             f'{bands["n_frames"]} frames; use a new --raw folder')
    else:
        for k in (0, last):
            if not all(frame_png(args.output, s, k).exists() or (ref_dir / f'satellite_{s:02d}' / f'frame_{k:04d}.png').exists()
                       for s in sats):
                render(args, k, k + 1, sats)
        ok, per_sat = np.ones(size[1], bool), {}
        for s in sats:
            sat_ok = np.ones(size[1], bool)
            for k in (0, last):
                p = frame_png(args.output, s, k)
                p = p if p.exists() else ref_dir / f'satellite_{s:02d}' / f'frame_{k:04d}.png'
                sat_ok &= read_rgba(p, size)[..., 3].any(axis=1)
            idx = np.flatnonzero(sat_ok)
            per_sat[str(s)] = [int(idx[0]), int(idx[-1])] if idx.size else None
            ok &= sat_ok
        bands = select_band_rows(ok, args.n_bands, args.tdi_rows)
        bands.update(satellites=sats, n_frames=n_frames, rows_per_satellite=per_sat,
                     criterion='alpha > 0 in at least one column, in frames 0 and F-1, for every satellite; '
                               'longest run of such rows',
                     cameras=str(args.cameras.resolve()))
        write_json(bands_path, bands)
    rows = np.asarray(bands['rows'])
    print(f"Bands: {bands['n_bands']} x {bands['tdi_rows']} rows, starts {bands['band_start_rows']} "
          f"(usable rows {bands['usable_rows']})", flush=True)

    # raw files and disk check
    shape = (len(rows), n_frames, size[0], 4)
    nbytes = int(np.prod(shape))
    missing = [s for s in sats if not (raw_dir / f'sat_{s:02d}.npy').exists()]
    free = shutil.disk_usage(raw_dir).free
    keep = getattr(args, 'keep_frames', False)
    frames_left = n_frames if keep else args.block
    need = nbytes * len(missing) + frames_left * len(sats) * PNG_BYTES + MARGIN_BYTES
    print(f'Raw files: {len(sats)} x {nbytes/1e9:.2f} GB; free {free/1e9:.1f} GB, needed {need/1e9:.1f} GB', flush=True)
    if free < need:
        raise SystemExit('not enough free space')
    for s in missing:
        tmp = raw_dir / f'sat_{s:02d}.npy.tmp'
        np.lib.format.open_memmap(tmp, mode='w+', dtype=np.uint8, shape=shape).flush()
        os.replace(tmp, raw_dir / f'sat_{s:02d}.npy')

    # 2. blocks
    progress_path = raw_dir / 'progress.json'
    done = set(json.loads(progress_path.read_text())['frames']) if progress_path.exists() else set()
    with Pool(args.workers) as pool:
        for start in range(0, n_frames, args.block):
            stop = min(n_frames, start + args.block)
            frames = [k for k in range(start, stop) if k not in done]
            if not frames:
                continue
            t0 = time.time()
            render(args, frames[0], frames[-1] + 1, sats)
            for s in sats:
                pngs = [frame_png(args.output, s, k) for k in frames]
                for p in pngs:
                    if not p.exists():
                        raise SystemExit(f'missing {p} after rendering')
                strips = pool.map(_read_rows, [(p, rows, size) for p in pngs], chunksize=1)
                D = np.lib.format.open_memmap(raw_dir / f'sat_{s:02d}.npy', mode='r+')
                D[:, frames[0]:frames[-1] + 1] = np.stack(strips, axis=1)
                D.flush()
                del D
                D = np.load(raw_dir / f'sat_{s:02d}.npy', mmap_mode='r')
                for j, (k, p) in enumerate(zip(frames, pngs)):
                    if not np.array_equal(D[:, k], strips[j]):
                        raise SystemExit(f'raw file of satellite {s} differs from {p}: PNG kept')
                del D, strips
                for k, p in zip(frames, pngs):
                    if keep:
                        continue
                    if is_reference(k):
                        dest = ref_dir / f'satellite_{s:02d}' / p.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(p, dest)
                    else:
                        p.unlink()
            done.update(frames)
            write_json(progress_path, dict(frames=sorted(done)))
            print(f'Frames {frames[0]}..{frames[-1]}: extracted{"" if keep else " and removed"} in {time.time()-t0:.0f} s '
                  f'({len(done)}/{n_frames})', flush=True)

    # 3. metadata
    for s in sats:
        D = np.load(raw_dir / f'sat_{s:02d}.npy', mmap_mode='r')
        seen = (D[..., 3] > 0).any(axis=2)                                    # [R, F]
        t, role = frame_times(manifest, s)
        write_json(raw_dir / f'sat_{s:02d}.json', dict(
            satellite=s, acquisition_role=role, shape=list(shape), dtype='uint8',
            axes=['stored row i', 'frame k', 'column c', 'RGBA'],
            convention='raw[i, k, c] = PNG pixel (rows[i], c) of frame k; band b = rows '
                       'b*tdi_rows .. b*tdi_rows + tdi_rows - 1 (adjacent detector rows, TDI stages)',
            rows=rows.tolist(), band_start_rows=bands['band_start_rows'], tdi_rows=bands['tdi_rows'],
            frame_t_s=t, frame_period_s=manifest.get('frame_period_s'),
            rows_seeing_scene_in_every_frame=int(seen.all(axis=1).sum()),
            min_frames_with_scene_per_row=int(seen.sum(axis=1).min()),
            reference_frames='all (renders/)' if keep else [k for k in range(n_frames) if is_reference(k)],
            cameras=str(args.cameras.resolve()), extracted_frames=len(done)))
        print(f'Satellite {s}: {int(seen.all(axis=1).sum())}/{len(rows)} rows see the scene in all {n_frames} frames',
              flush=True)


if __name__ == '__main__':
    main()
