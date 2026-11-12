"""Step 17 A + B: full acquisition cube of satellites 0-4, no correction, and the 9-band export.

Plan: PLAN_CONTEXT.md, "Acquisition cube", decisions F1-F7.

A. For each satellite S, D = cube_satSS.npy (np.lib.format.open_memmap), uint8, shape
   (958, 1000, 1908, 4), axes (band b, frame k, column c, RGBA):
       D[b, k, c] = frame_k[1000 + b, 662 + c]       (RGBA exactly as stored in the PNG)
   No shift, no resampling, no interpolation: the deputy drift stays in the data.
   Before building: rows 1000..1957 and columns 662..2569 must have alpha > 0 in EVERY frame of
   EVERY satellite (stacking_inputs.npz alpha_row_any / alpha_col_any), and the output volume must
   have the needed space + 10 GB free. Each PNG is read once (multiprocessing pool); frames are
   written in blocks to a .tmp file that is renamed at the end. Metadata in cube_satSS.json.

B. From the cube (not from the PNGs): band_samples/satellite_SS/band_RRRR.png for the 9 rows
   round(linspace(1000, 1957, 9)), RGBA, 1908 x 1000 px, image row = frame k, column = PNG
   column 662 + c; plus band_samples/README.md.

Run in the conda environment orbits:
    python scripts/build_acquisition_cube.py [--satellites 0 1 2 3 4] [--workers 20] [--overwrite]
"""
import argparse
import datetime as dt
import json
import os
import resource
import shutil
import subprocess
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

import stacking_geometry as sg

SATS = (0, 1, 2, 3, 4)
ROW0, ROW1 = 1000, 1957          # inclusive detector rows (bands)
COL0, COL1 = 662, 2569           # inclusive PNG columns
N_FRAMES = sg.N_FRAMES
N_BANDS, N_COLS = ROW1 - ROW0 + 1, COL1 - COL0 + 1
SHAPE = (N_BANDS, N_FRAMES, N_COLS, 4)
EXPORT_ROWS = (1000, 1120, 1239, 1359, 1478, 1598, 1718, 1837, 1957)
MARGIN_BYTES = 10e9
CONVENTION = ('D[b, k] is detector row r_b = 1000 + b at frame k; for the chief it sees the ground line '
              'g = r_b - k (row in frame 0); for a deputy g is approximately r_b - s k with s from step 13 '
              'C.1 (no correction applied)')


def export_rows():
    rows = np.round(np.linspace(ROW0, ROW1, 9)).astype(int)
    assert tuple(rows.tolist()) == EXPORT_ROWS, rows
    return rows


def cube_dir(run):
    return Path(run) / 'cubes' / 'acquisition'


def band_dir(run):
    return Path(run) / 'band_samples'


def check_common_window(run):
    """A.1: rows ROW0..ROW1 and cols COL0..COL1 have alpha > 0 in every frame of every satellite."""
    with np.load(Path(run) / 'stacking_inputs.npz') as d:
        sats, row_any, col_any = d['alpha_satellites'], d['alpha_row_any'], d['alpha_col_any']
    out, ok = [], True
    for s in SATS:
        i = int(np.flatnonzero(sats == s)[0])
        rows_all = np.flatnonzero(row_any[i].all(0))       # rows with alpha > 0 in every frame
        cols_all = np.flatnonzero(col_any[i].all(0))
        r_ok = bool(row_any[i][:, ROW0:ROW1 + 1].all())
        c_ok = bool(col_any[i][:, COL0:COL1 + 1].all())
        ok &= r_ok and c_ok
        out.append(dict(sat=s, rows=(int(rows_all[0]), int(rows_all[-1]), rows_all.size),
                        cols=(int(cols_all[0]), int(cols_all[-1]), cols_all.size), rows_ok=r_ok, cols_ok=c_ok))
    return ok, out


def _read(path):
    with Image.open(path) as im:
        if im.mode != 'RGBA': raise ValueError(f'{path}: mode {im.mode}, expected RGBA')
        a = np.asarray(im)
    return a[ROW0:ROW1 + 1, COL0:COL1 + 1].copy()


def frame_path(run, sat, k):
    return Path(run) / 'renders' / f'satellite_{sat:02d}' / f'frame_{k:04d}.png'


def git_commit():
    try:
        sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=sg.REPO, capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=sg.REPO, capture_output=True, text=True).stdout
        return sha + (' (working tree modified)' if dirty.strip() else '')
    except OSError:
        return 'unknown'


def peak_rss_mb():
    """ru_maxrss of this process and of its (waited-for) children, in MB (Linux: kB)."""
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024)


def build(run, sat, pool, block):
    out = cube_dir(run) / f'cube_sat{sat:02d}.npy'
    tmp = out.with_name(out.name + '.tmp')
    t0 = time.time()
    D = np.lib.format.open_memmap(tmp, mode='w+', dtype=np.uint8, shape=SHAPE)
    del D
    paths = [frame_path(run, sat, k) for k in range(N_FRAMES)]
    for k0 in range(0, N_FRAMES, block):
        k1 = min(N_FRAMES, k0 + block)
        crops = pool.map(_read, paths[k0:k1], chunksize=1)
        # re-map per block so the written pages leave this process's RSS when unmapped
        D = np.lib.format.open_memmap(tmp, mode='r+')
        D[:, k0:k1] = np.stack(crops, axis=1)          # [B, K, C, 4]
        D.flush()
        del D, crops
    os.replace(tmp, out)
    wall = time.time() - t0
    meta = dict(shape=list(SHAPE), axes=['band b', 'frame k', 'column c', 'RGBA'], dtype='uint8',
                rows=dict(first=ROW0, last=ROW1, rule='band b = PNG row 1000 + b'),
                cols=dict(first=COL0, last=COL1, rule='column c = PNG column 662 + c'),
                satellite=sat, run_folder=str(Path(run).resolve()), source=f'renders/satellite_{sat:02d}/frame_KKKK.png',
                git_commit=git_commit(), created_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                correction='none (no shift, no resampling, no interpolation)', convention=CONVENTION)
    out.with_suffix('.json').write_text(json.dumps(meta, indent=1))
    return out, wall


def export_bands(run, sat):
    D = np.load(cube_dir(run) / f'cube_sat{sat:02d}.npy', mmap_mode='r')
    assert D.shape == SHAPE and D.dtype == np.uint8
    d = band_dir(run) / f'satellite_{sat:02d}'
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for r in export_rows():
        p = d / f'band_{r:04d}.png'
        Image.fromarray(np.ascontiguousarray(D[r - ROW0]), 'RGBA').save(p, optimize=False)
        files.append(p)
    return files


README = """# Band samples of the acquisition cube (step 17)

Nine bands of the acquisition cube `cubes/acquisition/cube_satSS.npy` (not versioned, 7.3 GB per
satellite) of run eevee_full_1000, exported by `scripts/build_acquisition_cube.py` directly from the
cube, without any correction (no shift, no resampling, no interpolation).

- `satellite_SS/band_RRRR.png`: RGBA, 8 bit, 1908 x 1000 px. A band is one detector row
  r = RRRR (PNG row of the rendered frames) stacked over the 1000 frames:
  image row k = frame k (top = frame 0), image column c = PNG column 662 + c.
  Bands: rows 1000, 1120, 1239, 1359, 1478, 1598, 1718, 1837, 1957 = round(linspace(1000, 1957, 9)).
  Satellites 00 (chief) and 01-04 (deputies). Alpha = geometry coverage (0 = no scene).
- Orientation: going DOWN a band image (increasing k) the ground goes FORWARD (the chief sees the
  ground line g = r - k of its frame 0, so g decreases), the opposite of a single frame, where
  forward is UP.
- Same ground in two bands: the chief sees in band r2 at frame k + (r2 - r1) the ground that band r1
  sees at frame k. For a deputy g is about r - s k (s = its frame-0 pixel advance per frame), so a
  residual of (r2 - r1)(s - 1) remains after the integer offset (up to about 0.8 px for 1000 vs 1957).
- `comparison/`: visual check of that drift on the ground common to bands 1000 and 1957 (43 frames),
  written by `scripts/check_acquisition_cube.py`.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--satellites', type=int, nargs='+', default=list(SATS))
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--block', type=int, default=50, help='frames per write block')
    ap.add_argument('--run', type=Path, default=sg.RUN_DIR)
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--skip-build', action='store_true', help='only part B (cubes must exist)')
    args = ap.parse_args()
    run = args.run

    print('# Step 17 A/B: acquisition cube\n')
    ok, win = check_common_window(run)
    print('## A.1 Common window (stacking_inputs.npz, alpha > 0 in every frame)\n')
    print('| sat | rows in every frame | cols in every frame | rows 1000..1957 ok | cols 662..2569 ok |\n|---|---|---|---|---|')
    for w in win:
        print(f"| {w['sat']} | {w['rows'][0]}..{w['rows'][1]} ({w['rows'][2]}) | {w['cols'][0]}..{w['cols'][1]} "
              f"({w['cols'][2]}) | {w['rows_ok']} | {w['cols_ok']} |")
    if not ok:
        r0 = max(w['rows'][0] for w in win); r1 = min(w['rows'][1] for w in win)
        c0 = max(w['cols'][0] for w in win); c1 = min(w['cols'][1] for w in win)
        raise SystemExit(f'STOP: common intersection is rows {r0}..{r1}, cols {c0}..{c1}')

    cdir = cube_dir(run)
    cdir.mkdir(parents=True, exist_ok=True)
    per_sat = int(np.prod(SHAPE)) + 128
    todo = [] if args.skip_build else list(args.satellites)
    existing = [s for s in todo if (cdir / f'cube_sat{s:02d}.npy').exists()]
    if existing and not args.overwrite:
        raise SystemExit(f'cubes exist for satellites {existing}; pass --overwrite')
    free = shutil.disk_usage(cdir).free
    need = per_sat * len(todo) - sum((cdir / f'cube_sat{s:02d}.npy').stat().st_size for s in existing)
    print(f'\n## A.2 Disk\n\nfree on {cdir}: {free / 1e9:.1f} GB; needed {need / 1e9:.1f} GB '
          f'({len(todo)} x {per_sat / 1e9:.3f} GB) + 10 GB margin = {(need + MARGIN_BYTES) / 1e9:.1f} GB\n')
    if free < need + MARGIN_BYTES:
        raise SystemExit('STOP: not enough free space')

    if todo:
        print('## A.5 Build\n\n| sat | file | size (bytes) | size (GB) | wall (s) | peak RSS main (MB) | peak RSS workers (MB) |\n|---|---|---|---|---|---|---|')
        for s in todo:
            with Pool(args.workers) as pool:
                out, wall = build(run, s, pool, args.block)
            # RUSAGE_CHILDREN covers the pool workers once they are joined (max over all children so far)
            rs, rc = peak_rss_mb()
            size = out.stat().st_size
            print(f'| {s} | {out.relative_to(run)} | {size} | {size / 1e9:.3f} | {wall:.1f} | {rs:.0f} | {rc:.0f} |', flush=True)

    print('\n## B Export\n')
    export_rows()
    bdir = band_dir(run)
    bdir.mkdir(exist_ok=True)
    if not (bdir / 'README.md').exists():          # keep the hand-written README (band_samples/README.md)
        (bdir / 'README.md').write_text(README)
    total = 0
    print('| sat | band row | file | size (MB) |\n|---|---|---|---|')
    for s in args.satellites:
        for p in export_bands(run, s):
            sz = p.stat().st_size
            total += sz
            print(f'| {s} | {p.stem[5:]} | {p.relative_to(run)} | {sz / 1e6:.2f} |', flush=True)
    print(f'\ntotal {total / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
