"""Render a run with every available GPU and keep only the raw pushbroom data of selected detector rows.

The rendered frames are full 4096 x 3072 RGBA snapshots (about 8 MB each). A linear-variable-filter
pushbroom only reads a few detector rows per band, so this driver keeps, for every satellite,

    raw/sat_SS.npy   uint8 [R, F, W, 4]   raw[i, k, c] = frame_k[rows[i], c]   (RGBA as in the PNG)

with R = n_bands * tdi_rows detector rows (tdi_rows adjacent rows per band, for a later TDI model),
F frames and all W columns (alpha = scene coverage). It is the raw data of swarm_simulator's
Acquisition.raw (band, frame, column) with the TDI rows added. Everything else in the frames is
deleted, except one reference frame every --ref-every frames (and the last one), moved to
reference_frames/satellite_SS/. With --keep-frames every PNG stays in renders/ (no deletion,
no move): the raw data are extracted and verified all the same.

Rendering (scripts/render_pool.py): the frames are split into tasks of --chunk frames of one
satellite, rendered by persistent Blender workers (render_swarm.py --task-queue) on every GPU
selected by --gpus, with as many workers per GPU as its measured memory allows. The results do not
depend on the hardware: every PNG and raw file is the same whatever the number of GPUs and workers.

Steps
1. Band rows (once, raw/bands.json): the chunks holding frames 0 and F-1 are rendered first; the
   rows that see the scene (alpha > 0 in at least one column) in BOTH frames for EVERY satellite
   are kept; the ground moves monotonically along the rows, so these rows see the scene over the
   whole window. n_bands bands of tdi_rows adjacent rows are spread evenly over that interval,
   start rows multiples of 4 (HyperScape100 band start rows, parameter 0x33).
2. As soon as a task is done (and the band rows are known) its frames are extracted while the
   workers keep rendering: the rows are written into the memory-mapped raw file, read back and
   compared with the PNGs, then each PNG is deleted (or kept as reference). raw/progress.json
   lists the extracted (satellite, frame) pairs ('done'; 'frames' = frames extracted for every
   satellite), so a run that stops is resumed by launching the same command again.
3. raw/sat_SS.json: shape, rows, band layout, frame times and coverage of every stored row;
   renders/satellite_SS/poses.jsonl sorted by frame.

Run in the conda environment orbits (Pillow), from the repository root, e.g.
    python scripts/render_to_raw.py --cameras RUN/simulation/cameras.json --terrain RUN/terrain_render \\
        --output RUN/renders --blender /snap/blender/current/blender [--gpus all] -- --engine EEVEE --samples 8
Arguments after '--' go to render_swarm.py unchanged.
"""
import argparse
import json
import os
import shutil
import signal
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_pool as rp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PNG_BYTES = 8.5e6            # upper estimate of one rendered frame (render_swarm --png-compression 15, default)
PNG_BYTES_STORED = 51e6      # the same frame stored without zlib (Blender compression below 10: 48 MiB)
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


def _ignore_signals():
    """Extraction processes ignore Ctrl-C / SIGTERM (sent to the whole process group): the driver
    stops them itself; a pool child killed by a signal can hang the pool."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def extract_task(job):
    """Write the band rows of the frames of one task into raw/sat_SS.npy, verify, delete/move the PNGs.

    Runs in a process of the extraction pool; tasks write disjoint frames of the memory-mapped file.
    Once the rows are verified, the marker QUEUE/extracted/NAME.json is written before any PNG is
    deleted, so the frames count as extracted even if the driver is killed right after."""
    raw_path, rows, size, sat, frames, renders, ref_dir, keep, refs, marker = job
    t0 = time.time()
    pngs = [frame_png(renders, sat, k) for k in frames]
    for p in pngs:
        if not p.exists():
            raise RuntimeError(f'missing {p} after rendering')
    strips = np.stack([read_rgba(p, size)[rows] for p in pngs], axis=1)
    t_read = time.time()
    D = np.lib.format.open_memmap(raw_path, mode='r+')
    for j, k in enumerate(frames):
        D[:, k] = strips[:, j]
    D.flush()
    del D
    t_write = time.time()
    D = np.load(raw_path, mmap_mode='r')
    for j, (k, p) in enumerate(zip(frames, pngs)):
        if not np.array_equal(D[:, k], strips[:, j]):
            raise RuntimeError(f'raw file of satellite {sat} differs from {p}: PNG kept')
    del D
    t_verify = time.time()
    write_json(marker, dict(satellite=sat, frames=list(frames)))
    for k, p in zip(frames, pngs):
        if keep:
            continue
        if k in refs:
            dest = Path(ref_dir) / f'satellite_{sat:02d}' / p.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(p, dest)
        else:
            p.unlink()
    t_end = time.time()
    return dict(satellite=sat, frames=list(frames), read_s=t_read - t0, write_s=t_write - t_read,
                verify_s=t_verify - t_write, delete_s=t_end - t_verify, total_s=t_end - t0)


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


def load_progress(path, sats):
    """{sat: set(frames)} from raw/progress.json; the old format {'frames': [...]} means every satellite."""
    if not path.exists():
        return {s: set() for s in sats}
    data = json.loads(path.read_text())
    if 'done' in data:
        return {s: set(data['done'].get(str(s), [])) for s in sats}
    return {s: set(data['frames']) for s in sats}


def save_progress(path, progress):
    every = set.intersection(*progress.values()) if progress else set()
    write_json(path, dict(frames=sorted(every), done={str(s): sorted(f) for s, f in progress.items()}))


def compute_bands(args, sats, n_frames, size, ref_dir):
    """Band rows from frames 0 and F-1 of every satellite (see step 1), or None if a PNG is missing."""
    last = n_frames - 1
    paths = {}
    for s in sats:
        for k in (0, last):
            p = frame_png(args.output, s, k)
            p = p if p.exists() else Path(ref_dir) / f'satellite_{s:02d}' / f'frame_{k:04d}.png'
            if not p.exists():
                return None
            paths[s, k] = p
    ok, per_sat = np.ones(size[1], bool), {}
    for s in sats:
        sat_ok = np.ones(size[1], bool)
        for k in (0, last):
            sat_ok &= read_rgba(paths[s, k], size)[..., 3].any(axis=1)
        idx = np.flatnonzero(sat_ok)
        per_sat[str(s)] = [int(idx[0]), int(idx[-1])] if idx.size else None
        ok &= sat_ok
    bands = select_band_rows(ok, args.n_bands, args.tdi_rows)
    bands.update(satellites=sats, n_frames=n_frames, rows_per_satellite=per_sat,
                 criterion='alpha > 0 in at least one column, in frames 0 and F-1, for every satellite; '
                           'longest run of such rows',
                 cameras=str(args.cameras.resolve()))
    return bands


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
    ap.add_argument('--satellites', default=None, help='comma-separated ids; default all in cameras.json')
    ap.add_argument('--n-bands', type=int, default=32)
    ap.add_argument('--tdi-rows', type=int, default=8)
    ap.add_argument('--ref-every', type=int, default=100)
    ap.add_argument('--workers', type=int, default=None,
                    help='extraction processes; default the cores not taken by render workers')
    ap.add_argument('--keep-frames', action='store_true', help='extract the raw rows but keep every PNG')
    rp.add_pool_arguments(ap)
    args = ap.parse_args(argv)
    args.render_args = render_args
    reserved = {'--frames', '--satellites', '--output', '--cameras', '--terrain', '--crop', '--skip-existing'}
    clash = sorted(a.split('=')[0] for a in render_args if a.split('=')[0] in reserved)
    if clash:
        ap.error(f'{clash} are set by this driver; use its own options (--satellites) instead')
    try:
        run_main(args)
    except rp.PoolFailed as error:
        rp.log(f'FAILED: {error}')
        sys.exit(1)
    except KeyboardInterrupt as error:
        rp.log(f'interrupted ({error}); workers stopped; launch the same command again to resume')
        sys.exit(130)


def real_pool(args, queue, n_tasks):
    """Device discovery and the Blender worker pool (replaced by a fake in the tests)."""
    devices = rp.build_pool(args, list(args.render_args), args.output, n_tasks)
    command = rp.worker_command(args.blender, args.cameras.resolve(), args.terrain.resolve(), args.output.resolve(),
                                queue, args.render_args)
    return rp.WorkerPool(queue, devices, command, args.workers_per_gpu, args.max_workers_per_gpu,
                         args.gpu_memory_margin, args.ram_margin, output=args.output,
                         min_gain=args.min_worker_gain)


def run_main(args, make_pool=real_pool):
    t_start = time.time()
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
    refs = {k for k in range(n_frames) if k % args.ref_every == 0 or k == last}
    keep = getattr(args, 'keep_frames', False)
    chunk = getattr(args, 'chunk', 20)

    bands_path = raw_dir / 'bands.json'
    bands = None
    if bands_path.exists():
        bands = json.loads(bands_path.read_text())
        if bands['satellites'] != sats or bands['n_frames'] != n_frames:
            raise SystemExit(f'{bands_path} was made for satellites {bands["satellites"]}, '
                             f'{bands["n_frames"]} frames; use a new --raw folder')

    # raw files and disk check (the raw shape depends only on n_bands x tdi_rows)
    n_rows = args.n_bands * args.tdi_rows
    shape = (n_rows, n_frames, size[0], 4)
    nbytes = int(np.prod(shape))
    missing = [s for s in sats if not (raw_dir / f'sat_{s:02d}.npy').exists()]
    progress_path = raw_dir / 'progress.json'
    progress = load_progress(progress_path, sats)
    extracted_dir = (getattr(args, 'queue', None) or args.output / 'queue') / 'extracted'
    extracted_dir.mkdir(parents=True, exist_ok=True)
    for p in extracted_dir.glob('*.json'):                 # extractions the driver did not record
        m = json.loads(p.read_text())
        if m['satellite'] in progress:
            progress[m['satellite']].update(m['frames'])
    todo = {s: [k for k in range(n_frames) if k not in progress[s]] for s in sats}
    n_todo = sum(map(len, todo.values()))

    # queue: tasks for the frames not extracted yet; the chunks with frames 0 and F-1 first
    queue = rp.TaskQueue(getattr(args, 'queue', None) or args.output / 'queue')
    queue.check_config(dict(cameras=str(args.cameras.resolve()), satellites=sats, n_frames=n_frames, chunk=chunk,
                            render_args=list(getattr(args, 'render_args', []))))
    for name in queue.done_names():
        # A done task whose frames are not all extracted needs its PNGs; if one is gone, render it again.
        m = queue.read_done(name)
        frames = [k for k in range(*m['frames']) if k not in progress[m['satellite']]]
        if frames and not all(frame_png(args.output, m['satellite'], k).exists() for k in frames):
            (queue.done / f'{name}.json').unlink()
    tasks = rp.chunk_tasks(sats, todo, chunk, priority_frames=(0, last))
    for name, task in tasks:
        queue.add(name, task)
    pool = make_pool(args, queue, len(tasks))
    max_render = min(rp.cpu_count(), sum(1 if d['kind'] == 'cpu' else
                                         (getattr(args, 'workers_per_gpu', None) or getattr(args, 'max_workers_per_gpu', 4))
                                         for d in getattr(pool, 'devices', [])) or 1)
    n_extract = getattr(args, 'workers', None) or max(1, rp.cpu_count() - max_render)

    # disk check: raw files plus the PNGs on disk at once (everything left with --keep-frames, else the
    # band chunks, rendered first and extracted once the bands are known, plus two chunks per worker)
    in_flight = n_todo if keep else min(n_todo, (2 * len(sats) + 2 * max_render) * chunk)
    free = shutil.disk_usage(raw_dir).free
    level = rp.take_option(list(args.render_args), '--png-compression')[0]
    png_bytes = PNG_BYTES_STORED if level is not None and int(level) < 10 else PNG_BYTES
    need = nbytes * len(missing) + in_flight * png_bytes + MARGIN_BYTES
    print(f'Raw files: {len(sats)} x {nbytes/1e9:.2f} GB; {n_todo} frames to render; free {free/1e9:.1f} GB, '
          f'needed {need/1e9:.1f} GB', flush=True)
    if free < need:
        raise SystemExit('not enough free space')
    for s in missing:
        tmp = raw_dir / f'sat_{s:02d}.npy.tmp'
        np.lib.format.open_memmap(tmp, mode='w+', dtype=np.uint8, shape=shape).flush()
        os.replace(tmp, raw_dir / f'sat_{s:02d}.npy')
    for s in sats:
        if np.load(raw_dir / f'sat_{s:02d}.npy', mmap_mode='r').shape != shape:
            raise SystemExit(f'{raw_dir}/sat_{s:02d}.npy is not {shape}; use a new --raw folder')
    rp.log(f'{len(tasks)} task(s) for {n_todo} frame(s); {n_extract} extraction process(es)')

    waiting, running, failures = [], [], []
    state = dict(bands=bands, extracted=0)
    rows = None if bands is None else np.asarray(bands['rows'])

    def submit(name, xpool):
        m = queue.read_done(name)
        s = m['satellite']
        frames = [k for k in range(*m['frames']) if k not in progress[s]]
        if not frames:
            return
        job = (raw_dir / f'sat_{s:02d}.npy', state['rows'], size, s, frames, args.output, ref_dir, keep,
               refs & set(frames), extracted_dir / f'{name}.json')
        running.append((name, xpool.apply_async(extract_task, (job,))))

    with Pool(n_extract, initializer=_ignore_signals) as xpool:
        def on_done(name):
            if state['bands'] is None:
                waiting.append(name)
            else:
                submit(name, xpool)

        def tick():
            if state['bands'] is None and waiting:
                b = compute_bands(args, sats, n_frames, size, ref_dir)
                if b is not None:
                    write_json(bands_path, b)
                    state['bands'], state['rows'] = b, np.asarray(b['rows'])
                    print(f"Bands: {b['n_bands']} x {b['tdi_rows']} rows, starts {b['band_start_rows']} "
                          f"(usable rows {b['usable_rows']})", flush=True)
                    for name in waiting:
                        submit(name, xpool)
                    waiting.clear()
            changed = False
            for item in [r for r in running if r[1].ready()]:
                running.remove(item)
                name, res = item
                try:
                    out = res.get()
                except Exception as error:                                # verification failed etc.
                    failures.append(f'{name}: {error}')
                    continue
                progress[out['satellite']].update(out['frames'])
                state['extracted'] += len(out['frames'])
                queue.event('extracted', task=name, **out)
                changed = True
            if changed:
                save_progress(progress_path, progress)
            if failures:
                raise SystemExit('extraction failed: ' + '; '.join(failures))

        if state['bands'] is not None:
            state['rows'] = rows
        rp.drive(queue, pool, on_done=on_done, busy=lambda: bool(running or waiting), tick=tick, total=len(tasks))
    save_progress(progress_path, progress)
    left = {s: n_frames - len(progress[s]) for s in sats if len(progress[s]) < n_frames}
    if left:
        raise SystemExit(f'frames not extracted: {left}')
    rp.merge_poses(args.output, queue, sats)

    # 3. metadata
    bands = state['bands']
    rows = np.asarray(bands['rows'])
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
            reference_frames='all (renders/)' if keep else sorted(refs),
            cameras=str(args.cameras.resolve()), extracted_frames=min(len(progress[s]) for s in sats)))
        print(f'Satellite {s}: {int(seen.all(axis=1).sum())}/{len(rows)} rows see the scene in all {n_frames} frames',
              flush=True)
    for row in pool.summary():
        rp.log(f'worker summary: {row}')
    rp.log(f'done in {time.time() - t_start:.0f} s; sizing: ' + ' | '.join(getattr(pool, 'decisions', [])))


if __name__ == '__main__':
    main()
