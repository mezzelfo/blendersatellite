"""Build per-satellite cubes cube[g, c, b, :] from the rendered PNGs (steps 14-16).

Plan: PLAN_CONTEXT.md S1-S9. Geometry and assembly come from cube_stacking.py; this script
only reads the PNGs (Pillow, RGBA) and writes
    <run>/cubes/<layout>_<mode>/cube_satSS.npz
with keys
    cube        uint8 [G, C, B, 4]  (RGBA; alpha = validity)
    g           int64 [G]           cube lines = row of the ground line in the satellite's frame 0
    cols        int64 [C]           PNG columns (scene columns of the satellite, S3)
    band_rows   int64 [B]           detector row of each band
    frame_index none:   int64 [G, B]      k = r_b - g
                linear: float32 [G, C, B] k* per column (S8); the interpolation uses exactly
                        these float32 values
    metadata    str (JSON)
Uncompressed npz (np.savez). Refuses to overwrite without --overwrite.

Run in the conda environment orbits, e.g.
    python scripts/build_cubes.py --satellites 0 --layout compact --mode none --workers 20
"""
import argparse
import json
import os
import resource
import subprocess
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

import cube_stacking as cs
import stacking_geometry as sg

NPZ = sg.RUN_DIR / 'stacking_inputs.npz'


def scene_cols(sat, npz=NPZ):
    """S3: every column with alpha > 0 in at least one frame of the satellite (contiguous)."""
    with np.load(npz) as d:
        i = np.flatnonzero(d['alpha_satellites'] == sat)
        if i.size != 1: raise ValueError(f'satellite {sat} not in {npz}')
        any_col = d['alpha_col_any'][int(i[0])].any(0)
    cols = np.flatnonzero(any_col)
    if cols.size != cols[-1] - cols[0] + 1: raise ValueError('scene columns not contiguous')
    return cols


def png_path(run_dir, sat, k):
    return Path(run_dir) / 'renders' / f'satellite_{sat:02d}' / f'frame_{k:04d}.png'


def read_rows(args):
    """uint8 [R, C, 4]: rows x the contiguous column range cols of one PNG."""
    path, rows, c0, c1 = args
    with Image.open(path) as im:
        if im.mode != 'RGBA': im = im.convert('RGBA')
        if im.size != (sg.WIDTH, sg.HEIGHT): raise ValueError(f'{path}: size {im.size}')
        a = np.asarray(im)
    return np.ascontiguousarray(a[rows, c0:c1 + 1])


def read_stack(run_dir, sat, frames, rows, cols, workers):
    """{k: uint8 [B, C, 4]} for the given frames, read once each, in parallel."""
    jobs = [(png_path(run_dir, sat, k), np.asarray(rows), int(cols[0]), int(cols[-1])) for k in frames]
    with Pool(workers) as pool:
        data = pool.map(read_rows, jobs, chunksize=4)
    return dict(zip((int(k) for k in frames), data))


def layout_name(spec):
    return spec if spec in cs.LAYOUTS else 'rows_' + '-'.join(map(str, cs.band_rows(spec)))


def parse_layout(text):
    return text if text in cs.LAYOUTS else [int(v) for v in text.replace(',', ' ').split()]


def peak_rss_mb():
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    return own, children


def git_commit():
    try:
        return subprocess.run(['git', '-C', str(sg.REPO), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return None


def plan(sat, layout, mode, n_frames):
    """Lines, columns and frame index of one cube (no PNG access)."""
    rows = cs.band_rows(layout)
    cols = scene_cols(sat)
    if mode == 'none':
        g0, g1 = cs.complete_lines_integer(rows, n_frames)
        g = np.arange(g0, g1 + 1)
        return rows, cols, g, cs.integer_index(g, rows)
    x = cs.ground_line_map(sat, np.arange(n_frames), rows, cols)            # [N, B, C]
    g_cand = np.arange(rows[-1] - n_frames - 5, rows[0] + 6)
    k_star = cs.fractional_index_map(x, g_cand)                            # [G, C, B]
    g0, g1 = cs.complete_lines_fractional(k_star, g_cand)
    sel = (g_cand >= g0) & (g_cand <= g1)
    k_star = k_star[sel].astype(np.float32)                                # S8; used as stored
    if not (np.all(np.isfinite(k_star)) and k_star.min() >= 0 and k_star.max() <= n_frames - 1):
        raise ValueError('float32 k* left the frame window')
    return rows, cols, g_cand[sel], k_star


def build_one(run_dir, sat, layout, mode, workers, overwrite, n_frames=cs.N_FRAMES):
    out_dir = Path(run_dir) / 'cubes' / f'{layout_name(layout)}_{mode}'
    target = out_dir / f'cube_sat{sat:02d}.npz'
    if target.exists() and not overwrite:
        raise SystemExit(f'{target} exists; refusing to overwrite (use --overwrite)')
    start = time.monotonic()
    rows, cols, g, index = plan(sat, layout, mode, n_frames)
    G, C, B = g.size, cols.size, rows.size
    size = G * C * B * 4 + index.nbytes + g.nbytes + cols.nbytes + rows.nbytes
    print(f'sat {sat} {layout_name(layout)} {mode}: cube {G} x {C} x {B} x 4 = {G * C * B * 4 / 1e6:.1f} MB, '
          f'frame_index {index.dtype} {index.shape} = {index.nbytes / 1e6:.1f} MB; expected file {size / 1e6:.1f} MB '
          f'-> {target}', flush=True)
    if mode == 'none':
        frames = np.unique(index)
    else:
        k0 = np.minimum(np.floor(index.astype(np.float64)), n_frames - 2).astype(np.int64)
        frames = np.union1d(np.unique(k0), np.unique(k0) + 1)
    t_plan = time.monotonic() - start
    stack = read_stack(run_dir, sat, frames, rows, cols, workers)
    t_read = time.monotonic() - start - t_plan
    cube, used = cs.build_cube(stack.__getitem__, rows, cols, g, mode, index=index, n_frames=n_frames)
    del stack
    if not np.array_equal(used, index): raise RuntimeError('build_cube changed the frame index')
    t_build = time.monotonic() - start - t_plan - t_read
    meta = dict(satellite=sat, layout=layout_name(layout), mode=mode, band_rows=rows.tolist(),
                g_range=[int(g[0]), int(g[-1])], cols_range=[int(cols[0]), int(cols[-1])], n_frames=n_frames,
                frames_read=int(frames.size), run_dir=str(Path(run_dir).resolve()),
                frame_index=('k = r_b - g, int64 [G, B]' if mode == 'none' else
                             'k* per column, float32 [G, C, B]; RGBA = rint((1-f) F[floor k*] + f F[floor k* + 1]) '
                             'at row r_b, floor clipped to n_frames-2'),
                geometry='S6: x(k, r, c) = pixel_from_ground(sat, 0, ground_from_pixel(sat, k, r, c, z=0))[0]',
                png='row from top, col from left, integer = pixel centre; RGBA straight alpha, sRGB',
                plan='Prompts/PLAN_CONTEXT.md S1-S9', script='scripts/build_cubes.py', git_commit=git_commit(),
                created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + '.tmp.npz')
    np.savez(tmp, cube=cube, g=g, cols=cols, band_rows=rows, frame_index=index, metadata=json.dumps(meta))
    os.replace(tmp, target)
    own, children = peak_rss_mb()
    print(f'  wrote {target.stat().st_size / 1e6:.1f} MB; frames read {frames.size}; time plan {t_plan:.1f} s, '
          f'read {t_read:.1f} s, build {t_build:.1f} s, total {time.monotonic() - start:.1f} s; '
          f'peak RSS main {own:.0f} MB, largest worker {children:.0f} MB', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--satellites', type=int, nargs='+', default=[0])
    ap.add_argument('--layout', type=parse_layout, default='compact', help='layout name or comma-separated rows')
    ap.add_argument('--mode', choices=('none', 'linear'), required=True)
    ap.add_argument('--workers', type=int, default=20)
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--run', type=Path, default=sg.RUN_DIR)
    args = ap.parse_args()
    for sat in args.satellites:
        build_one(args.run, sat, args.layout, args.mode, args.workers, args.overwrite)


if __name__ == '__main__':
    main()
