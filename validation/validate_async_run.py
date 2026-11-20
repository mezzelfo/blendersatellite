"""Step 23 validation of an async (fore / nadir / aft) raw run with every PNG kept.

Read-only on RUN; every output goes to RUN/validation/. Sections (run separately):
  completeness  A: pipeline.log, every PNG (validate_run.check_png), render report and GPU memory
                per job, raw/ files and 10000 random raw samples per satellite against the PNGs,
                terrain_render; also the alpha-255 fraction of every PNG (C.1)
  poses         B.2: poses.jsonl written by Blender against cameras.json
  projection    B.3: terrain (grid and wall) vertices projected with the poses, PNG read there
                (check_stacking_geometry.sample_rgb / stats), best bilinear sub-pixel offset
  motion        B.4: ground motion between frames k and k+50 (integer SSD + S11 phase correlation)
  compare       C.2 same-role co-registration, C.3 fore/aft parallax vs terrain height,
                C.4 walls vs grid-only render, C.5 contact sheet
Run in the conda environment orbits, from the repository root:
    python validation/validate_async_run.py RUN SECTION [--workers 20]
"""
import argparse
import json
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))  # pipeline modules
import stacking_geometry as g  # noqa: E402
from validate_run import check_png  # noqa: E402

N_SAT, N_FRAMES = 7, 1000
ROLE = {0: 'nadir', 1: 'fore', 2: 'nadir', 3: 'aft', 4: 'fore', 5: 'nadir', 6: 'aft'}
SIGMA = 0.05          # S11 estimator: Hann + Gaussian low-pass (check_acquisition_cube.py default)


def png_path(run, s, k):
    return run / 'renders' / f'satellite_{s:02d}' / f'frame_{k:04d}.png'


def read_png(path):
    return np.asarray(Image.open(path).convert('RGBA'))


def dump(run, name, obj):
    out = run / 'validation' / name
    out.write_text(json.dumps(obj, indent=1, default=float) + '\n')
    print('wrote', out)


# ----------------------------------------------------------------------------- A
def _png_task(task):
    path, samples = task
    res = check_png(Path(path))
    res.pop('md5', None)
    if res['ok']:
        img = read_png(path)
        res['alpha255'] = float(np.mean(img[..., 3] == 255))
        if samples is not None:
            rows, cols = samples
            res['samples'] = img[rows, cols].tolist()
    return res


def completeness(run, workers):
    out = {}
    # A.1 pipeline.log
    log = (run / 'pipeline.log').read_text(errors='replace').splitlines()
    issue = re.compile(r'error|traceback|exception|fail|killed|out of memory|segmentation', re.I)
    noise = re.compile(r'^Frame \d+ satellite \d+: corner error')
    out['log'] = dict(lines=len(log), last=[l for l in log if l.strip()][-2:],
                      completed=any(l.startswith('Completed run:') for l in log[-5:]),
                      issues=[l for l in log if issue.search(l) and not noise.match(l)],
                      terrain=[l for l in log if l.startswith('Terrain:')],
                      acquisition=[l for l in log if l.startswith('Formation:')],
                      engine=sorted({m.group(1) for l in log for m in [re.search(r'--engine (\S+)', l)] if m}),
                      rendered=[l for l in log if l.startswith('Rendered ')])
    # A.3 raw metadata (needed for the samples of the PNG pass)
    raw = run / 'raw'
    bands = json.loads((raw / 'bands.json').read_text())
    rows_all = np.array(bands['rows'])
    rng = np.random.default_rng(23)
    samp = {s: dict(i=rng.integers(0, len(rows_all), 10000), k=rng.integers(0, N_FRAMES, 10000),
                    c=rng.integers(0, g.WIDTH, 10000)) for s in range(N_SAT)}
    # A.2 + samples + C.1: one decode of every PNG
    tasks, keys = [], []
    for s in range(N_SAT):
        for k in range(N_FRAMES):
            sel = np.flatnonzero(samp[s]['k'] == k)
            tasks.append((str(png_path(run, s, k)), (rows_all[samp[s]['i'][sel]].tolist(), samp[s]['c'][sel].tolist())))
            keys.append((s, k, sel))
    t0 = time.monotonic()
    with Pool(workers) as pool:
        results = pool.map(_png_task, tasks, chunksize=4)
    out['png_seconds'] = time.monotonic() - t0
    files = {s: sorted(p.name for p in (run / 'renders' / f'satellite_{s:02d}').iterdir()) for s in range(N_SAT)}
    png = {}
    alpha255 = {s: np.full(N_FRAMES, np.nan) for s in range(N_SAT)}
    coverage = {s: np.full(N_FRAMES, np.nan) for s in range(N_SAT)}
    png_vals = {s: np.zeros((10000, 4), np.int16) - 1 for s in range(N_SAT)}
    for (s, k, sel), res in zip(keys, results):
        p = png.setdefault(s, dict(checked=0, ok=0, problems=[], bytes=0))
        p['checked'] += 1; p['ok'] += res['ok']; p['bytes'] += res.get('bytes', 0)
        p['problems'] += [f'frame {k}: {x}' for x in res['problems']]
        if res['ok']:
            alpha255[s][k] = res['alpha255']; coverage[s][k] = res['coverage']
            png_vals[s][sel] = np.asarray(res['samples']).reshape(-1, 4)
    for s in range(N_SAT):
        names = files[s]
        png[s].update(files_png=sum(n.endswith('.png') for n in names), other_files=[n for n in names if not n.endswith('.png')],
                      missing=[k for k in range(N_FRAMES) if f'frame_{k:04d}.png' not in names])
    out['png'] = png
    out['alpha255'] = {s: dict(f0=alpha255[s][0], f500=alpha255[s][500], f999=alpha255[s][999],
                               min=float(np.nanmin(alpha255[s])), max=float(np.nanmax(alpha255[s])),
                               coverage_f0=coverage[s][0], coverage_f500=coverage[s][500], coverage_f999=coverage[s][999])
                       for s in range(N_SAT)}
    # render report: completeness, coverage cross-check, GPU memory per job
    rep = [json.loads(l) for l in (run / 'renders' / 'render_report.jsonl').read_text().splitlines() if l.strip()]
    jobs, cur = [], None
    seen = {}
    cov_dev = 0.
    mib = lambda snap: max((int(m.group(1)) for x in snap or [] for m in [re.search(r'(\d+) MiB', x)] if m), default=None)
    for r in rep:
        if r['type'] == 'run':
            cur = dict(frames=r['frames'], setup_s=r['scene_setup_seconds'], after_setup_mib=mib(r.get('eevee_gpu_processes')),
                       engine=r['engine'], samples=r.get('eevee_taa_render_samples'), triangles=r['triangles'],
                       gpu=r.get('eevee_gpu', {}).get('renderer'), backend=r.get('eevee_gpu', {}).get('backend'),
                       n_frames=0, n_renders=0, peak_mib=0, render_s=[])
            jobs.append(cur)
            continue
        cur['n_frames'] += 1
        for x in r['renders']:
            cur['n_renders'] += 1
            m = mib(x.get('gpu_process_snapshot'))
            if m: cur['peak_mib'] = max(cur['peak_mib'], m)
            cur['render_s'].append(x['render_seconds'])
            seen.setdefault(x['satellite_id'], set()).add(r['frame'])
            c = x.get('geometry_coverage_fraction')
            if c is not None and not np.isnan(coverage[x['satellite_id']][r['frame']]):
                cov_dev = max(cov_dev, abs(c - coverage[x['satellite_id']][r['frame']]))
    for j in jobs:
        rs = j.pop('render_s')
        j['render_s_mean'] = float(np.mean(rs)) if rs else None
    out['render_report'] = dict(jobs=jobs, frames_per_sat={s: len(v) for s, v in seen.items()},
                                complete=all(seen.get(s) == set(range(N_FRAMES)) for s in range(N_SAT)),
                                coverage_logged_vs_png_max_dev=cov_dev)
    # A.3 raw files
    progress = json.loads((raw / 'progress.json').read_text())
    rawres = dict(bands={k: bands[k] for k in ('usable_rows', 'n_bands', 'tdi_rows', 'start_row_alignment', 'satellites', 'n_frames')},
                  progress_complete=sorted(progress['frames']) == list(range(N_FRAMES)), sats={})
    expect_rows = [b + t for b in bands['band_start_rows'] for t in range(bands['tdi_rows'])]
    rawres['rows_consistent'] = expect_rows == bands['rows']
    for s in range(N_SAT):
        meta = json.loads((raw / f'sat_{s:02d}.json').read_text())
        arr = np.load(raw / f'sat_{s:02d}.npy', mmap_mode='r')
        sm = samp[s]
        rv = np.asarray(arr[sm['i'], sm['k'], sm['c']]).astype(np.int16)
        valid = png_vals[s][:, 0] >= 0
        eq = np.all(rv[valid] == png_vals[s][valid], axis=1)
        rawres['sats'][s] = dict(shape=list(arr.shape), dtype=str(arr.dtype), json_shape=meta['shape'], json_dtype=meta['dtype'],
                                 role=meta.get('acquisition_role'), rows_match_bands=meta['rows'] == bands['rows'],
                                 band_starts_match=meta['band_start_rows'] == bands['band_start_rows'],
                                 n_frame_t=len(meta['frame_t_s']), extracted_frames=meta.get('extracted_frames'),
                                 rows_seeing_scene=meta.get('rows_seeing_scene_in_every_frame'),
                                 samples=int(valid.sum()), equal=int(eq.sum()),
                                 samples_alpha_gt0=int(np.sum(rv[valid][:, 3] > 0)))
    out['raw'] = rawres
    # A.4 terrain_render
    tr = run / 'terrain_render'
    frame = json.loads((tr / 'cache_frame.json').read_text())
    tiles = sorted(tr.glob('*.npz'))
    with_walls, segs = 0, 0
    for t in tiles:
        with np.load(t) as d:
            if 'wall_vertices' in d.files:
                with_walls += 1; segs += int(d['wall_segments'])
    cams = json.loads((run / 'simulation' / 'cameras.json').read_text())
    out['terrain'] = dict(tiles=len(tiles), selected_tiles=len(cams['selected_tiles']),
                          missing=sorted(set(cams['selected_tiles']) - {t.stem for t in tiles}),
                          tiles_with_wall_keys=with_walls, wall_segments=segs,
                          walls=frame.get('walls'), spacing=frame.get('mesh_grid_spacing_m'),
                          frame_matches_cameras=all(frame[k] == cams[k] for k in ('origin_ecef_m', 'local_from_ecef')))
    np.savez_compressed(run / 'validation' / 'alpha_per_frame.npz', alpha255=np.array([alpha255[s] for s in range(N_SAT)]),
                        coverage=np.array([coverage[s] for s in range(N_SAT)]))
    dump(run, 'A_completeness.json', out)


# ----------------------------------------------------------------------------- B.2
def poses(run):
    cams = g._cameras()
    out = {}
    for s in range(N_SAT):
        lines = [json.loads(l) for l in (run / 'renders' / f'satellite_{s:02d}' / 'poses.jsonl').read_text().splitlines() if l.strip()]
        recs = {}
        conflict = 0
        for r in lines:
            k = int(r['frame'])
            if k in recs and (r['position_local_m'] != recs[k]['position_local_m'] or r['camera_to_local'] != recs[k]['camera_to_local']):
                conflict += 1
            recs[k] = r
        dp, dR, ang, dt = 0., 0., 0., 0.
        for k in range(N_FRAMES):
            c = next(x for x in cams['epochs'][k]['cameras'] if x['satellite_id'] == s)
            b = recs[k]
            dp = max(dp, float(np.max(np.abs(np.subtract(b['position_local_m'], c['position_local_m'])))))
            Rb, Rc = np.asarray(b['camera_to_local']), np.asarray(c['camera_to_local'])
            dR = max(dR, float(np.max(np.abs(Rb - Rc))))
            ang = max(ang, float(np.arccos(np.clip((np.trace(Rb.T @ Rc) - 1) / 2, -1, 1))))
            dt = max(dt, abs(b.get('t_s', np.nan) - c.get('t_s', cams['epochs'][k]['t_s'])))
        out[s] = dict(lines=len(lines), frames=len(recs), all_frames=sorted(recs) == list(range(N_FRAMES)),
                      conflicting_duplicates=conflict, max_position_diff_m=dp, max_rotation_element_diff=dR,
                      max_rotation_angle_rad=ang, max_t_s_diff=dt, role=ROLE[s])
    dump(run, 'B2_poses.json', out)


# ----------------------------------------------------------------------------- terrain helpers
def load_terrain(tdir, walls=True):
    v, rgb, wv, wrgb = [], [], [], []
    for t in sorted(Path(tdir).glob('*.npz')):
        with np.load(t) as d:
            v.append(d['vertices']); rgb.append(d['rgb'])
            if walls and 'wall_vertices' in d.files:
                wv.append(d['wall_vertices'].reshape(-1, 4, 3)); wrgb.append(d['wall_rgb'][::4])
    out = dict(v=np.concatenate(v).astype(float), rgb=np.concatenate(rgb).astype(float))
    if walls and wv:
        out['quads'] = np.concatenate(wv).astype(float); out['qrgb'] = np.concatenate(wrgb).astype(float)
    return out


def wall_facing(quads, verts, spacing=2.5):
    """Unit horizontal normal of each wall pointing to its lower side (terrain_to_render: walls face the
    lower cell), from the grid heights half a cell on either side of the segment centre."""
    from scipy.spatial import cKDTree
    a, b = quads[:, 0, :2], quads[:, 1, :2]
    t = (b - a) / np.linalg.norm(b - a, axis=1, keepdims=True)
    n = np.c_[-t[:, 1], t[:, 0]]
    mid = 0.5 * (a + b)
    tree = cKDTree(verts[:, :2])
    _, ip = tree.query(mid + 0.5 * spacing * n)
    _, im = tree.query(mid - 0.5 * spacing * n)
    sign = np.where(verts[ip, 2] < verts[im, 2], 1., -1.)
    return n * sign[:, None]


def sample_bilinear(img, r, c):
    """RGBA float at continuous (r, c); ok = all four neighbours inside and alpha > 0."""
    H, W = img.shape[:2]
    r0, c0 = np.floor(r).astype(int), np.floor(c).astype(int)
    ok = (r0 >= 0) & (r0 < H - 1) & (c0 >= 0) & (c0 < W - 1)
    r0c, c0c = np.clip(r0, 0, H - 2), np.clip(c0, 0, W - 2)
    fr, fc = (r - r0c)[..., None], (c - c0c)[..., None]
    q00, q01 = img[r0c, c0c].astype(float), img[r0c, c0c + 1].astype(float)
    q10, q11 = img[r0c + 1, c0c].astype(float), img[r0c + 1, c0c + 1].astype(float)
    px = (q00 * (1 - fc) + q01 * fc) * (1 - fr) + (q10 * (1 - fc) + q11 * fc) * fr
    ok &= np.minimum.reduce([q00[..., 3], q01[..., 3], q10[..., 3], q11[..., 3]]) > 0
    return px, ok


# ----------------------------------------------------------------------------- B.3
def _proj_stats(img, r, c, rgb):
    H, W = img.shape[:2]
    ri, ci = np.rint(r).astype(int), np.rint(c).astype(int)
    ok = (ri >= 0) & (ri < H) & (ci >= 0) & (ci < W)
    px = np.zeros((len(r), 4)); px[ok] = img[ri[ok], ci[ok]]
    ok &= px[:, 3] > 0
    d = np.abs(px[ok, :3] - rgb[ok]).mean(1)
    res = dict(n=int(ok.sum()), median=float(np.median(d)), mean=float(d.mean()), p95=float(np.percentile(d, 95)))
    offs = np.round(np.arange(-0.5, 0.501, 0.125), 3)
    grid = np.zeros((len(offs), len(offs)))
    for i, dr in enumerate(offs):
        for j, dc in enumerate(offs):
            p, okb = sample_bilinear(img, r + dr, c + dc)
            grid[i, j] = np.median(np.abs(p[okb, :3] - rgb[okb]).mean(1))
    i, j = np.unravel_index(np.argmin(grid), grid.shape)
    # parabolic refinement of the median-|dRGB| minimum (step 0.125 px)
    def para(m1, m0, p1):
        den = m1 - 2 * m0 + p1
        return 0. if den <= 0 else float(np.clip(0.5 * (m1 - p1) / den, -0.5, 0.5))
    fr = para(grid[i - 1, j], grid[i, j], grid[i + 1, j]) if 0 < i < len(offs) - 1 else 0.
    fc = para(grid[i, j - 1], grid[i, j], grid[i, j + 1]) if 0 < j < len(offs) - 1 else 0.
    res.update(best_offset_rows=float(offs[i] + 0.125 * fr), best_offset_cols=float(offs[j] + 0.125 * fc),
               bilinear_median_at_0=float(grid[len(offs) // 2, len(offs) // 2]), bilinear_median_best=float(grid[i, j]),
               on_grid_edge=bool(i in (0, len(offs) - 1) or j in (0, len(offs) - 1)))
    return res


def projection(run):
    terr = load_terrain(run / 'terrain_render')
    rng = np.random.default_rng(0)
    pick = rng.choice(len(terr['v']), 200000, replace=False)
    verts, rgb = terr['v'][pick], terr['rgb'][pick]
    wp = rng.choice(len(terr['quads']), 200000, replace=False)
    quads = terr['quads'][wp]
    wcen, wrgb = quads.mean(1), terr['qrgb'][wp]
    normal = wall_facing(quads, terr['v'])
    out = dict(n_grid_vertices=len(terr['v']), n_wall_segments=len(terr['quads']), rows=[])
    for s in range(N_SAT):
        for k in (0, 500, 999):
            img = read_png(png_path(run, s, k))
            r, c = g.pixel_from_ground(s, k, verts)
            row = dict(sat=s, role=ROLE[s], frame=k, grid=_proj_stats(img, r, c, rgb))
            pos, _ = g._pose(s, k)
            to_cam = pos[:2] - wcen[:, :2]
            to_cam /= np.linalg.norm(to_cam, axis=1, keepdims=True)
            facing = np.sum(normal * to_cam, axis=1) > 0.5          # within 60 deg of the camera azimuth
            rw, cw = g.pixel_from_ground(s, k, wcen)
            row['walls_all'] = _proj_stats(img, rw, cw, wrgb)
            row['walls_facing'] = _proj_stats(img, rw[facing], cw[facing], wrgb[facing])
            row['walls_facing_fraction'] = float(facing.mean())
            out['rows'].append(row)
            print(s, k, {key: (round(v['median'], 2), v['best_offset_rows'], v['best_offset_cols'])
                         for key, v in row.items() if isinstance(v, dict)}, flush=True)
    dump(run, 'B3_projection.json', out)


# ----------------------------------------------------------------------------- shift helpers
def gray(img):
    f = img.astype(float) / 255
    return (f[..., :3] @ [0.2126, 0.7152, 0.0722]) * f[..., 3]


def scene_box(*imgs, pad=16):
    m = np.zeros(imgs[0].shape[:2], bool)
    for im in imgs: m |= im[..., 3] > 0
    rr, cc = np.flatnonzero(m.any(1)), np.flatnonzero(m.any(0))
    r0, r1 = max(rr[0] - pad, 0), min(rr[-1] + pad, m.shape[0])
    c0, c1 = max(cc[0] - pad, 0), min(cc[-1] + pad, m.shape[1])
    return slice(r0, r1 - (r1 - r0) % 2), slice(c0, c1 - (c1 - c0) % 2)


def s11_motion(a, b, box=None):
    """Scene motion (rows, cols) from a to b (S11 estimator: Hann + low-pass sigma 0.05)."""
    from phase_correlation import phase_shift
    box = box or scene_box(a, b)
    return -phase_shift(gray(a[box]), gray(b[box]), 1000, sigma=SIGMA, hann=True)


def int_ssd(a, b, rows, cols, box):
    """Integer (dr, dc) minimising the masked SSD of luminance: b[r + dr, c + dc] ~ a[r, c]."""
    la, lb = gray(a), gray(b)
    best = None
    rs, cs = box
    for dr in rows:
        for dc in cols:
            ra = la[rs, cs]
            rb = lb[rs.start + dr:rs.stop + dr, cs.start + dc:cs.stop + dc]
            m = (a[rs, cs, 3] == 255) & (b[rs.start + dr:rs.stop + dr, cs.start + dc:cs.stop + dc, 3] == 255)
            cost = float(np.mean((ra[m] - rb[m]) ** 2))
            if best is None or cost < best[0]: best = (cost, dr, dc)
    return best[1], best[2]


def predicted_motion(sa, ka, sb, kb, img, h=0.):
    """Alpha-weighted mean (S9) over opaque pixels (stride 16) of the z = h pixel map a -> b."""
    rr, cc = np.mgrid[0:g.HEIGHT:16, 0:g.WIDTH:16]
    ok = img[rr, cc, 3] == 255
    r, c = rr[ok].astype(float), cc[ok].astype(float)
    r2, c2 = g.pixel_from_ground(sb, kb, g.ground_from_pixel(sa, ka, r, c, h))
    return float(np.mean(r2 - r)), float(np.mean(c2 - c))


# ----------------------------------------------------------------------------- B.4
def motion(run):
    out = []
    for s in range(N_SAT):
        for k in (0, 475, 949):
            a, b = read_png(png_path(run, s, k)), read_png(png_path(run, s, k + 50))
            rs, cs = scene_box(a)
            inner = (slice(rs.start + 80, rs.stop - 80), slice(cs.start + 20, cs.stop - 20))
            dr, dc = int_ssd(a, b, range(40, 61), range(-3, 4), inner)
            m = s11_motion(a, b)
            pr, pc = predicted_motion(s, k, s, k + 50, a)
            out.append(dict(sat=s, role=ROLE[s], k=k, int_rows=dr, int_cols=dc, sub_rows=float(m[0]), sub_cols=float(m[1]),
                            pred_rows_z0=pr, pred_cols_z0=pc))
            print(out[-1], flush=True)
    dump(run, 'B4_motion.json', out)


# ----------------------------------------------------------------------------- C
def cbd_centre(verts, cell=250., win=4):
    """Local (x, y) centre of the win x win cell window (1 km) with the largest mean height relief
    (p95 - p5 of the vertex heights per 250 m cell): the CBD towers, independent of the ground level."""
    ij = np.floor(verts[:, :2] / cell).astype(int)
    lo = ij.min(0)
    ij -= lo
    relief = np.zeros(ij.max(0) + 1)
    key = ij[:, 0] * relief.shape[1] + ij[:, 1]
    order = np.argsort(key, kind='stable')
    uk, start = np.unique(key[order], return_index=True)
    for k, z in zip(uk, np.split(verts[order, 2], start[1:])):
        p5, p95 = np.percentile(z, [5, 95])
        relief.flat[k] = p95 - p5
    from numpy.lib.stride_tricks import sliding_window_view
    s = sliding_window_view(relief, (win, win)).mean((2, 3))
    i, j = np.unravel_index(np.argmax(s), s.shape)
    return np.array([(lo[0] + i + win / 2) * cell, (lo[1] + j + win / 2) * cell]), float(s[i, j])


def mapped_compare(A, a, B, b, K, h=0.):
    """B warped onto A's pixels with the z = h pose map (bilinear), then the residual S11 shift folded
    into the same single resampling; mean |dRGB| over the scene box of A."""
    box = scene_box(A)
    rr, cc = np.mgrid[box[0], box[1]].astype(float)
    r2, c2 = g.pixel_from_ground(b, K, g.ground_from_pixel(a, K, rr, cc, h))
    Bw, ok = sample_bilinear(B, r2, c2)
    Bw[~ok] = 0
    Ab = A[box].astype(float)
    res = s11_motion(Ab, Bw, box=(slice(0, Ab.shape[0] - Ab.shape[0] % 2), slice(0, Ab.shape[1] - Ab.shape[1] % 2)))
    Bw2, ok2 = sample_bilinear(B, *g.pixel_from_ground(b, K, g.ground_from_pixel(a, K, rr + res[0], cc + res[1], h)))
    Bw2[~ok2] = 0
    dr, dc = (r2 - rr)[A[box][..., 3] == 255], (c2 - cc)[A[box][..., 3] == 255]
    return dict(residual_rows=float(res[0]), residual_cols=float(res[1]), mad_mapped=mad_rgb(Ab, Bw2)[0],
                frac_gt2_mapped=mad_rgb(Ab, Bw2)[2],
                map_rows_p5_p95=np.percentile(dr, [5, 95]).tolist(), map_cols_p5_p95=np.percentile(dc, [5, 95]).tolist())


def block_match(a, b, block=32, sr=30, sc=4):
    """Per block of a (luminance x alpha), integer + parabolic sub-pixel (dr, dc) with b[r+dr, c+dc] ~ a[r, c]."""
    la, lb = gray(a), gray(b)
    ma, mb = a[..., 3] == 255, b[..., 3] == 255
    H, W = la.shape
    nb_r, nb_c = (H - 2 * sr) // block, (W - 2 * sc) // block
    cost = np.full((2 * sr + 1, 2 * sc + 1, nb_r, nb_c), np.inf)
    for i, dr in enumerate(range(-sr, sr + 1)):
        for j, dc in enumerate(range(-sc, sc + 1)):
            ra = la[sr:sr + nb_r * block, sc:sc + nb_c * block]
            rb = lb[sr + dr:sr + dr + nb_r * block, sc + dc:sc + dc + nb_c * block]
            m = ma[sr:sr + nb_r * block, sc:sc + nb_c * block] & mb[sr + dr:sr + dr + nb_r * block, sc + dc:sc + dc + nb_c * block]
            e = ((ra - rb) ** 2 * m).reshape(nb_r, block, nb_c, block).sum((1, 3))
            n = m.reshape(nb_r, block, nb_c, block).sum((1, 3))
            cost[i, j] = np.where(n > 0.9 * block * block, e / np.maximum(n, 1), np.inf)
    flat = cost.reshape(-1, nb_r, nb_c)
    idx = np.argmin(flat, 0)
    bi, bj = np.unravel_index(idx, cost.shape[:2])
    valid = np.isfinite(np.min(flat, 0))
    def para(m1, m0, p1):
        den = m1 - 2 * m0 + p1
        with np.errstate(invalid='ignore', divide='ignore'):
            return np.where(np.isfinite(den) & (den > 0), np.clip(0.5 * (m1 - p1) / den, -0.5, 0.5), 0.)
    R, C = np.meshgrid(np.arange(nb_r), np.arange(nb_c), indexing='ij')
    gi = lambda ii, jj: cost[np.clip(ii, 0, 2 * sr), np.clip(jj, 0, 2 * sc), R, C]
    fr = para(gi(bi - 1, bj), gi(bi, bj), gi(bi + 1, bj))
    fc = para(gi(bi, bj - 1), gi(bi, bj), gi(bi, bj + 1))
    edge = (bi == 0) | (bi == 2 * sr) | (bj == 0) | (bj == 2 * sc)
    dr = np.where(valid & ~edge, bi - sr + fr, np.nan)
    dc = np.where(valid & ~edge, bj - sc + fc, np.nan)
    return dr, dc, (sr, sc, block)


def shift_rgba(img, d):
    """out(x) = img(x - d), bilinear (scipy.ndimage.shift order 1), float."""
    from scipy.ndimage import shift
    return np.stack([shift(img[..., i].astype(float), d, order=1, cval=0.) for i in range(4)], -1)


def mad_rgb(a, b):
    m = (a[..., 3] >= 254.5) & (b[..., 3] >= 254.5)
    d = np.abs(a[..., :3].astype(float) - b[..., :3].astype(float))[m]
    return float(d.mean()), int(m.sum()), float(np.mean(d.mean(1) > 2))


def compare(run):
    K = 500
    imgs = {s: read_png(png_path(run, s, K)) for s in range(N_SAT)}
    out = {}
    # C.1 is in A_completeness.json (alpha255 at frames 0, 500, 999)
    # C.2 same role
    rows = []
    for a, b in ((0, 2), (0, 5), (2, 5), (1, 4), (3, 6)):
        A, B = imgs[a], imgs[b]
        m = s11_motion(A, B)
        before = mad_rgb(A, B)
        Bs = shift_rgba(B, -m)      # move b back by the measured scene motion
        after = mad_rgb(A, Bs)
        # symmetric: both images resampled by half the shift (same bilinear blur on both)
        sym = mad_rgb(shift_rgba(A, m / 2), shift_rgba(B, -m / 2))
        pr, pc = predicted_motion(a, K, b, K, A)
        rows.append(dict(a=a, b=b, role=ROLE[a], shift_rows=float(m[0]), shift_cols=float(m[1]),
                         pred_rows_z0=pr, pred_cols_z0=pc, mad_before=before[0], mad_after=after[0],
                         frac_gt2_after=after[2], mad_after_symmetric=sym[0], frac_gt2_symmetric=sym[2], pixels=after[1],
                         **mapped_compare(A, a, B, b, K)))
        print(rows[-1], flush=True)
    out['C2'] = rows
    # ground grid on the CBD: chief frame-500 pixels, z = 0
    terr = load_terrain(run / 'terrain_render', walls=False)
    centre, hmean = cbd_centre(terr['v'])
    r0, c0 = (float(x) for x in g.pixel_from_ground(0, K, np.r_[centre, 0.]))
    half = 256
    rr, cc = np.mgrid[int(r0) - half:int(r0) + half, int(c0) - half:int(c0) + half].astype(float)
    G = g.ground_from_pixel(0, K, rr, cc, 0.)
    ortho = {}
    for s in range(N_SAT):
        r, c = g.pixel_from_ground(s, K, G)
        p, ok = sample_bilinear(imgs[s], r, c)
        p[~ok] = 0
        ortho[s] = p
    # terrain height binned on the same grid (vertex (x, y, 0) -> chief pixel)
    vr, vc = g.pixel_from_ground(0, K, np.c_[terr['v'][:, :2], np.zeros(len(terr['v']))])
    ir, ic = np.rint(vr).astype(int) - (int(r0) - half), np.rint(vc).astype(int) - (int(c0) - half)
    inside = (ir >= 0) & (ir < 2 * half) & (ic >= 0) & (ic < 2 * half)
    hs = np.zeros((2 * half, 2 * half)); hc = np.zeros_like(hs); hmax = np.full_like(hs, -np.inf)
    np.add.at(hs, (ir[inside], ic[inside]), terr['v'][inside, 2]); np.add.at(hc, (ir[inside], ic[inside]), 1)
    np.maximum.at(hmax, (ir[inside], ic[inside]), terr['v'][inside, 2])
    height = hs / np.maximum(hc, 1)
    # C.3 fore (1) vs aft (3)
    F, A3 = ortho[1], ortho[3]
    glob = s11_motion(F, A3, box=(slice(0, 2 * half), slice(0, 2 * half)))
    A3s = shift_rgba(A3, -glob)
    dr, dc, (sr, sc, blk) = block_match(F, A3s)
    nb_r, nb_c = dr.shape
    hb = height[sr:sr + nb_r * blk, sc:sc + nb_c * blk].reshape(nb_r, blk, nb_c, blk).mean((1, 3))
    hbmax = hmax[sr:sr + nb_r * blk, sc:sc + nb_c * blk].reshape(nb_r, blk, nb_c, blk).max((1, 3))
    hb90 = np.percentile(height[sr:sr + nb_r * blk, sc:sc + nb_c * blk].reshape(nb_r, blk, nb_c, blk).transpose(0, 2, 1, 3)
                         .reshape(nb_r, nb_c, -1), 90, axis=2)
    hbstd = height[sr:sr + nb_r * blk, sc:sc + nb_c * blk].reshape(nb_r, blk, nb_c, blk).std((1, 3))
    ok = np.isfinite(dr)
    corr = lambda x, y: float(np.corrcoef(x, y)[0, 1])
    slope = np.polyfit(hb[ok], dr[ok], 1)
    slope90 = np.polyfit(hb90[ok], dr[ok], 1)
    flat = ok & (hbstd < 10)
    slope_flat = np.polyfit(hb[flat], dr[flat], 1) if flat.sum() > 3 else [np.nan, np.nan]
    # DSM orthorectification: same ground (x, y) of the chief grid, at the terrain height of that pixel
    filled = height.copy()
    if np.any(hc == 0):
        from scipy.ndimage import distance_transform_edt
        idx = distance_transform_edt(hc == 0, return_distances=False, return_indices=True)
        filled = height[tuple(idx)]
    Gd = G.copy(); Gd[..., 2] = filled
    dsm = {}
    for s in (1, 3):
        p, okp = sample_bilinear(imgs[s], *g.pixel_from_ground(s, K, Gd))
        p[~okp] = 0
        dsm[s] = p
    dsm_glob = s11_motion(dsm[1], dsm[3], box=(slice(0, 2 * half), slice(0, 2 * half)))
    tall = (filled > 30)
    def fa(a_, b_, mask=None):
        m_ = (a_[..., 3] >= 254.5) & (b_[..., 3] >= 254.5)
        if mask is not None: m_ &= mask
        return float(np.abs(a_[..., :3] - b_[..., :3]).mean(-1)[m_].mean())
    dsm_cmp = dict(z0_mad=fa(F, A3s), z0_mad_tall=fa(F, A3s, tall), dsm_mad=fa(dsm[1], dsm[3]), dsm_mad_tall=fa(dsm[1], dsm[3], tall),
                   dsm_global_shift=[float(dsm_glob[0]), float(dsm_glob[1])], tall_fraction=float(tall.mean()))
    gsd_along = float(g.ground_pixel_size(0, K)[1])
    inc = {}
    for s in (1, 3):
        pos, _ = g._pose(s, K)
        inc[s] = float(np.arctan(np.hypot(*pos[:2]) / pos[2]))
    pred_slope = (np.tan(inc[1]) + np.tan(inc[3])) / gsd_along
    out['C3'] = dict(cbd_centre_local_m=centre.tolist(), cbd_mean_height_1km_m=hmean, crop_chief_px=[int(r0) - half, int(c0) - half, 2 * half],
                     crop_ground_m=float(2 * half * gsd_along), global_shift_rows=float(glob[0]), global_shift_cols=float(glob[1]),
                     blocks=int(dr.size), blocks_valid=int(ok.sum()), block_px=blk,
                     dr_range=[float(np.nanmin(dr)), float(np.nanmax(dr))], dr_p5_p95=np.nanpercentile(dr, [5, 95]).tolist(),
                     dc_range=[float(np.nanmin(dc)), float(np.nanmax(dc))], dc_p5_p95=np.nanpercentile(dc, [5, 95]).tolist(),
                     height_mean_range=[float(hb.min()), float(hb.max())],
                     corr_dr_hmean=corr(dr[ok], hb[ok]), corr_dr_h90=corr(dr[ok], hb90[ok]), corr_dr_hmax=corr(dr[ok], hbmax[ok]),
                     corr_dc_hmean=corr(dc[ok], hb[ok]), fit_dr_per_m=float(slope[0]), fit_intercept_px=float(slope[1]),
                     fit_dr_per_m_h90=float(slope90[0]), fit_dr_per_m_uniform_blocks=float(slope_flat[0]),
                     uniform_blocks=int(flat.sum()), predicted_px_per_m=float(pred_slope),
                     incidence_deg_local_vertical={s: float(np.rad2deg(v)) for s, v in inc.items()},
                     chief_along_gsd_m=gsd_along, ortho_fore_aft=dsm_cmp)
    print(out['C3'], flush=True)
    # C.4 walls vs grid-only, satellite 1 frame 500
    W_ = imgs[1]; Gr = read_png(run / 'validation' / 'render_grid' / 'satellite_01' / f'frame_{K:04d}.png')
    both = (W_[..., 3] == 255) & (Gr[..., 3] == 255)
    d = np.abs(W_[..., :3].astype(float) - Gr[..., :3].astype(float)).mean(-1)
    changed = both & (d > 2)
    m = s11_motion(Gr, W_)
    # CBD box in satellite 1 frame 500 (the ground grid's corners)
    cr, ccol = g.pixel_from_ground(1, K, G[[0, 0, -1, -1], [0, -1, 0, -1]])
    box = (slice(int(cr.min()), int(cr.max())), slice(int(ccol.min()), int(ccol.max())))
    inbox = np.zeros_like(both); inbox[box] = True
    rows_c, cols_c = np.nonzero(changed)
    out['C4'] = dict(mad_all=float(d[both].mean()), pixels=int(both.sum()), frac_gt2=float(changed.sum() / both.sum()),
                     frac_gt10=float(np.sum(both & (d > 10)) / both.sum()),
                     alpha_disagree=int(np.sum((W_[..., 3] == 255) != (Gr[..., 3] == 255))),
                     shift_rows=float(m[0]), shift_cols=float(m[1]), cbd_box_sat1=[box[0].start, box[0].stop, box[1].start, box[1].stop],
                     mad_in_cbd=float(d[both & inbox].mean()), mad_outside_cbd=float(d[both & ~inbox].mean()),
                     changed_in_cbd_fraction=float(np.sum(changed & inbox) / max(changed.sum(), 1)),
                     cbd_area_fraction_of_scene=float(np.sum(both & inbox) / both.sum()),
                     changed_bbox=[int(rows_c.min()), int(rows_c.max()), int(cols_c.min()), int(cols_c.max())] if len(rows_c) else None)
    # walls-vs-grid change vs height on the CBD grid
    rw, cw = g.pixel_from_ground(1, K, G)
    dd, okd = sample_bilinear(np.dstack([np.repeat(d[..., None], 3, -1), both * 255.]), rw, cw)
    hv = height[okd]
    out['C4']['cbd_grid_corr_dRGB_height'] = corr(dd[okd, 0], hv)
    out['C4']['cbd_grid_mad_h_lt10'] = float(dd[okd, 0][hv < 10].mean())
    out['C4']['cbd_grid_mad_h_gt50'] = float(dd[okd, 0][hv > 50].mean()) if np.any(hv > 50) else None
    print(out['C4'], flush=True)
    dump(run, 'C_compare.json', out)
    # C.5 contact sheet
    contact_sheet(run, ortho, F, A3s, dr, (sr, sc, blk), height)


def contact_sheet(run, ortho, F, A3s, dr, geom, height):
    from PIL import ImageDraw, ImageFont
    n = F.shape[0]
    font = ImageFont.load_default(size=22)
    tiles = []
    for s in range(N_SAT):
        rgb = np.clip(ortho[s][..., :3], 0, 255).astype(np.uint8)
        tiles.append((rgb, f'sat {s} {ROLE[s]}'))
    diff = np.abs(F[..., :3] - A3s[..., :3]).mean(-1)
    diff[(F[..., 3] < 255) | (A3s[..., 3] < 255)] = 0
    dimg = np.clip(diff * 4, 0, 255).astype(np.uint8)
    tiles.append((np.dstack([dimg] * 3), 'fore-aft |dRGB| x4 (1 vs 3)'))
    sr, sc, blk = geom
    disp = np.full((n, n), np.nan)
    up = np.kron(dr, np.ones((blk, blk)))
    disp[sr:sr + up.shape[0], sc:sc + up.shape[1]] = up
    lim = np.nanpercentile(np.abs(disp), 99)
    x = np.clip((disp / lim + 1) / 2, 0, 1)
    col = np.dstack([x, 1 - np.abs(2 * x - 1), 1 - x]) * 255
    col[np.isnan(disp)] = 40
    tiles.append((col.astype(np.uint8), f'fore-aft row disp. +/-{lim:.1f} px'))
    pad, lab = 6, 30
    sheet = Image.new('RGB', (3 * (n + pad) + pad, 3 * (n + lab + pad) + pad), (255, 255, 255))
    dr_ = ImageDraw.Draw(sheet)
    for i, (t, label) in enumerate(tiles):
        y, x = divmod(i, 3)
        X, Y = pad + x * (n + pad), pad + y * (n + lab + pad)
        dr_.text((X + 4, Y + 2), label, fill=(0, 0, 0), font=font)
        sheet.paste(Image.fromarray(t), (X, Y + lab))
    path = run / 'validation' / 'contact_sheet.png'
    sheet.save(path, optimize=True)
    print('wrote', path, path.stat().st_size)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run', type=Path)
    ap.add_argument('section', choices=['completeness', 'poses', 'projection', 'motion', 'compare'])
    ap.add_argument('--workers', type=int, default=20)
    args = ap.parse_args()
    run = args.run.resolve()
    (run / 'validation').mkdir(exist_ok=True)
    g.set_paths(run_dir=run, cameras_json=run / 'simulation' / 'cameras.json')
    t0 = time.monotonic()
    if args.section == 'completeness':
        completeness(run, args.workers)
    else:
        globals()[args.section](run)
    print(f'{args.section}: {time.monotonic() - t0:.0f} s')


if __name__ == '__main__':
    main()
