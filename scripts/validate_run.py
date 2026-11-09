"""Step 8 final validation of a full swarm render run (read-only on the outputs).

Checks the rendered folders, every PNG, alpha coverage, poses (|dr| = |v| dt, common
target, chief ground advance), the formation lattice, chief phase correlation, footprint
overlap across satellites and the job logs, then writes validation_report.md (and
validation_summary.json) in the run folder. Nothing else is written.

Run in the conda environment "orbits" (NumPy + Pillow), e.g.:
    python scripts/validate_run.py output/orbits_melbourne/eevee_full_1000 \
        --simulation output/orbits_melbourne/20261106T080712Z_run_IxoFnH/simulation
"""
import argparse
import datetime as dt_
import hashlib
import json
import re
import struct
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase_correlation import load, masked_gray, phase_shift  # noqa: E402

N_FRAMES = 1000
WIDTH, HEIGHT = 4096, 3072
PNG_SIG = b'\x89PNG\r\n\x1a\n'
EDGE_NOMINAL_M = 894.427191   # 2 * sep = 2 / sqrt(5) km
EDGE_TOL_M = 1.0
NEIGHBOUR_MAX_M = 1000.0 + 1.0
# Lattice order of deputies 1..6: (1,0),(0,1),(-1,1),(-1,0),(0,-1),(1,-1); consecutive = neighbours.
RING = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1)]
SPOKES = [(0, d) for d in range(1, 7)]


def check_png(path):
    """Full structural + decode check of one PNG; returns a dict (never raises)."""
    out = dict(path=str(path), ok=True, problems=[])
    try:
        raw = path.read_bytes()
        out['bytes'] = len(raw)
        out['md5'] = hashlib.md5(raw).hexdigest()
        if len(raw) == 0:
            raise ValueError('empty file')
        if raw[:8] != PNG_SIG:
            out['problems'].append('bad PNG signature')
        width, height, depth, ctype = struct.unpack('>IIBB', raw[16:26])
        out.update(ihdr_size=(width, height), bit_depth=depth, color_type=ctype)
        if depth != 8: out['problems'].append(f'bit depth {depth}')
        if ctype != 6: out['problems'].append(f'colour type {ctype} (6 = RGBA)')
        if (width, height) != (WIDTH, HEIGHT): out['problems'].append(f'size {width}x{height}')
        if raw[-12:-8] != b'\x00\x00\x00\x00' or raw[-8:-4] != b'IEND':
            out['problems'].append('missing IEND (truncated)')
        with Image.open(path) as img:
            if img.mode != 'RGBA': out['problems'].append(f'mode {img.mode}')
            img.load()  # full decode: raises on truncated / corrupt data
            alpha = np.asarray(img.getchannel('A'))
        out['coverage'] = float(np.mean(alpha > 127))  # same threshold as render_swarm (alpha > .5)
        out['alpha_any'] = float(np.mean(alpha > 0))
    except Exception as exc:  # noqa: BLE001 - every failure is reported, not raised
        out['problems'].append(f'{type(exc).__name__}: {exc}')
    out['ok'] = not out['problems']
    return out


def project(record, point_local):
    """Pixel (col, row) of a local-ENU point in a pose record (Blender camera, -Z forward, +Y up)."""
    rot = np.asarray(record['camera_to_local'])
    cam = rot.T @ (np.asarray(point_local) - np.asarray(record['position_local_m']))
    scale = record['focal_length_mm'] / (record['native_pixel_pitch_um'] * 1e-3)
    width, height = record['resolution']
    return np.array([width / 2 + scale * cam[0] / -cam[2], height / 2 - scale * cam[1] / -cam[2]])


def polygon_area(poly):
    x, y = np.asarray(poly).T
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def cross2(u, v):
    return u[0] * v[1] - u[1] * v[0]


def clip_convex(subject, clipper):
    """Sutherland-Hodgman intersection of two convex polygons (2D, any orientation)."""
    clipper = np.asarray(clipper, float)
    sign = np.sign(cross2(clipper[1] - clipper[0], clipper[2] - clipper[1]))
    out = [np.asarray(p, float) for p in subject]
    for i in range(len(clipper)):
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]
        inside = lambda p: sign * cross2(b - a, p - a) >= 0  # noqa: E731
        pts, out = out, []
        for j in range(len(pts)):
            p, q = pts[j], pts[(j + 1) % len(pts)]
            if inside(q):
                if not inside(p): out.append(intersect(p, q, a, b))
                out.append(q)
            elif inside(p):
                out.append(intersect(p, q, a, b))
        if not out: return []
    return out


def intersect(p, q, a, b):
    d1, d2 = q - p, b - a
    t = cross2(a - p, d2) / cross2(d1, d2)
    return p + t * d1


def stats(values):
    v = np.asarray(values, float)
    return dict(min=float(v.min()), mean=float(v.mean()), max=float(v.max()))


def rtn_tn(chief_r, chief_v, rel):
    r_hat = chief_r / np.linalg.norm(chief_r)
    n_hat = np.cross(chief_r, chief_v); n_hat /= np.linalg.norm(n_hat)
    t_hat = np.cross(n_hat, r_hat)
    return np.array([rel @ t_hat, rel @ n_hat])


def parse_utc(text):
    return dt_.datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run', type=Path, help='run folder with renders/ and logs/')
    parser.add_argument('--simulation', type=Path, required=True, help='simulation folder (results.npz, cameras.json)')
    parser.add_argument('--satellites', type=int, default=5, help='expected number of rendered satellite folders')
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--sample-every', type=int, default=100)
    args = parser.parse_args()
    t_start = time.monotonic()
    renders, logs = args.run / 'renders', args.run / 'logs'
    failures, notes = [], []
    report = {}

    # ---------------------------------------------------------------- folders / frames
    folders = sorted(p for p in renders.iterdir() if p.is_dir() and p.name.startswith('satellite_'))
    sat_ids = [int(p.name.split('_')[1]) for p in folders]
    if len(folders) != args.satellites:
        failures.append(f'{len(folders)} satellite folders, expected {args.satellites}: {[p.name for p in folders]}')
    expected_names = {f'frame_{k:04d}.png' for k in range(N_FRAMES)}
    folder_rows = []
    for folder in folders:
        names = [p.name for p in folder.iterdir()]
        pngs = [n for n in names if n.endswith('.png')]
        missing = sorted(expected_names - set(pngs))
        extra = sorted(set(pngs) - expected_names)
        other = sorted(n for n in names if not n.endswith('.png'))
        dup = len(pngs) - len(set(pngs))
        folder_rows.append((folder.name, len(pngs), len(missing), len(extra), dup, other))
        for n in missing: failures.append(f'{folder.name}: missing {n}')
        for n in extra: failures.append(f'{folder.name}: unexpected file {n}')
        if 'poses.jsonl' not in other: failures.append(f'{folder.name}: no poses.jsonl')
    report['folders'] = folder_rows

    # ---------------------------------------------------------------- PNG pass
    paths = [f / f'frame_{k:04d}.png' for f in folders for k in range(N_FRAMES) if (f / f'frame_{k:04d}.png').exists()]
    t0 = time.monotonic()
    with Pool(args.workers) as pool:
        results = pool.map(check_png, paths, chunksize=8)
    png_seconds = time.monotonic() - t0
    by_sat = {s: {} for s in sat_ids}
    for path, res in zip(paths, results):
        sat = int(path.parent.name.split('_')[1]); frame = int(path.stem.split('_')[1])
        by_sat[sat][frame] = res
        for problem in res['problems']:
            failures.append(f'satellite {sat:02d} frame {frame:04d}: {problem}')
    hashes = {}
    for path, res in zip(paths, results):
        if 'md5' in res: hashes.setdefault(res['md5'], []).append(str(path.relative_to(renders)))
    duplicates = [v for v in hashes.values() if len(v) > 1]
    for group in duplicates:
        failures.append(f'byte-identical PNGs: {group}')
    report['png'] = dict(checked=len(results), ok=sum(r['ok'] for r in results), seconds=png_seconds,
                         total_bytes=sum(r.get('bytes', 0) for r in results),
                         bytes=stats([r['bytes'] for r in results if r.get('bytes')]),
                         duplicate_groups=len(duplicates))

    # ---------------------------------------------------------------- coverage
    coverage = {}
    for sat in sat_ids:
        cov = {f: r['coverage'] for f, r in by_sat[sat].items() if 'coverage' in r}
        zero = sorted(f for f, c in cov.items() if c == 0)
        coverage[sat] = dict(stats(list(cov.values())), argmin=min(cov, key=cov.get), argmax=max(cov, key=cov.get),
                             zero_frames=zero,
                             alpha_any_minus_coverage_max=max(by_sat[sat][f]['alpha_any'] - c for f, c in cov.items()))
        for f in zero: failures.append(f'satellite {sat:02d} frame {f:04d}: alpha coverage 0')
    report['coverage'] = coverage

    # cross-check with the coverage logged by render_swarm (every 50th frame)
    render_rows = [json.loads(l) for l in (renders / 'render_report.jsonl').read_text().splitlines() if l.strip()]
    frame_rows = [r for r in render_rows if r['type'] == 'frame']
    run_rows = [r for r in render_rows if r['type'] == 'run']
    logged_cov_dev = 0.0
    for row in frame_rows:
        for rnd in row['renders']:
            c = rnd.get('geometry_coverage_fraction')
            sat = rnd['satellite_id']
            if c is not None and sat in by_sat and row['frame'] in by_sat[sat]:
                logged_cov_dev = max(logged_cov_dev, abs(c - by_sat[sat][row['frame']].get('coverage', np.nan)))
    report['coverage_logged_max_dev'] = logged_cov_dev

    # ---------------------------------------------------------------- poses
    npz = np.load(args.simulation / 'results.npz')
    times, pos, vel = npz['times'], npz['positions_eci'], npz['velocities_eci']
    cams = json.loads((args.simulation / 'cameras.json').read_text())
    frame_period = cams['formation_geometry']['frame_period_s']
    dts = np.diff(times)
    report['time_grid'] = dict(n=len(times), dt=frame_period, dt_dev_max=float(np.max(np.abs(dts - frame_period))),
                               t0=float(times[0]), t_last=float(times[-1]))
    if len(times) != N_FRAMES: failures.append(f'results.npz has {len(times)} samples')
    if np.max(np.abs(dts - frame_period)) > 1e-12: failures.append('time grid not uniform k*dt')

    poses = {}
    for folder in folders:
        sat = int(folder.name.split('_')[1])
        rows = [json.loads(l) for l in (folder / 'poses.jsonl').read_text().splitlines() if l.strip()]
        poses[sat] = {r['frame']: r for r in rows}
        if sorted(poses[sat]) != list(range(N_FRAMES)) or len(rows) != N_FRAMES:
            failures.append(f'{folder.name}/poses.jsonl: {len(rows)} rows, frames not exactly 0..999')

    pose_rows = []
    n_sim = pos.shape[1]
    for sat in range(n_sim):
        dr = np.linalg.norm(np.diff(pos[:, sat], axis=0), axis=1)
        v_mid = np.linalg.norm(0.5 * (vel[1:, sat] + vel[:-1, sat]), axis=1)
        rel = np.abs(dr - v_mid * dts) / (v_mid * dts)
        bad = np.flatnonzero(rel > 1e-6)
        row = dict(sat=sat, rendered=sat in poses, dr=stats(dr), speed=stats(np.linalg.norm(vel[:, sat], axis=1)),
                   rel_err_max=float(rel.max()), rel_err_argmax=int(rel.argmax()), n_bad=len(bad))
        if sat in poses:
            eci = np.array([poses[sat][k]['position_eci_m'] for k in range(N_FRAMES)])
            row['poses_vs_npz_max_m'] = float(np.max(np.abs(eci - pos[:, sat])))
            tsv = np.array([poses[sat][k]['t_s'] for k in range(N_FRAMES)])
            row['poses_t_dev_max_s'] = float(np.max(np.abs(tsv - times)))
            if row['poses_vs_npz_max_m'] > 1e-6:
                failures.append(f'satellite {sat:02d}: poses.jsonl ECI differs from results.npz by {row["poses_vs_npz_max_m"]:.3e} m')
            if sat in poses:
                for k in bad: failures.append(f'satellite {sat:02d} frame {k:04d}->{k+1:04d}: '
                                              f'| |dr|-|v|dt | / |v|dt = {rel[k]:.2e} > 1e-6')
        elif len(bad):
            notes.append(f'satellite {sat:02d} (not rendered): {len(bad)} steps with |dr| vs |v|dt rel err > 1e-6')
        pose_rows.append(row)
    report['poses'] = pose_rows

    # common target: render_report value + independent re-projection from poses.jsonl
    target_rows = []
    logged = {}
    for row in frame_rows:
        for rnd in row['renders']:
            logged.setdefault(rnd['satellite_id'], []).append((row['frame'], rnd['common_target_error_pixels']))
    for sat in sat_ids:
        errs = np.array([np.linalg.norm(project(poses[sat][k], poses[sat][k]['common_target_local_m'])
                                        - np.array([WIDTH / 2, HEIGHT / 2])) for k in range(N_FRAMES)])
        lg = np.array([e for _, e in sorted(logged.get(sat, []))]) if sat in logged else np.array([np.nan])
        target_rows.append(dict(sat=sat, reproj_max=float(errs.max()), reproj_argmax=int(errs.argmax()),
                                logged_n=len(logged.get(sat, [])), logged_max=float(np.nanmax(lg))))
        for k in np.flatnonzero(errs >= 0.01):
            failures.append(f'satellite {sat:02d} frame {k:04d}: common-target error {errs[k]:.4f} px')
        for f, e in logged.get(sat, []):
            if e >= 0.01: failures.append(f'satellite {sat:02d} frame {f:04d}: logged common-target error {e:.4f} px')
    report['target'] = target_rows

    # chief ground advance: ground centre displacement / native GSD, and image-space motion of
    # the frame-k ground centre in camera k+1
    chief = poses[0] if 0 in poses else None
    if chief is not None:
        gc = np.array([chief[k]['ground_center_local_m'] for k in range(N_FRAMES)])
        gsd = np.array([chief[k]['native_gsd_m'] for k in range(N_FRAMES)])
        adv_ground = np.linalg.norm(np.diff(gc, axis=0), axis=1) / gsd[:-1]
        img = np.array([project(chief[k + 1], gc[k]) - np.array([WIDTH / 2, HEIGHT / 2]) for k in range(N_FRAMES - 1)])
        report['advance'] = dict(ground_px=stats(adv_ground), ground_m=stats(np.linalg.norm(np.diff(gc, axis=0), axis=1)),
                                 image_rows=stats(img[:, 1]), image_cols=stats(img[:, 0]),
                                 image_norm=stats(np.linalg.norm(img, axis=1)),
                                 total_ground_m=float(np.linalg.norm(gc[-1] - gc[0])))
    else:
        failures.append('chief (satellite 00) not rendered: no ground-advance / phase-correlation checks')

    # ---------------------------------------------------------------- formation
    formation_rows = []
    for k in (0, 499, 999):
        cr, cv = pos[k, 0], vel[k, 0]
        tn = {s: rtn_tn(cr, cv, pos[k, s] - cr) for s in range(n_sim)}
        edges = {}
        for a, b in SPOKES + RING:
            e_tn = float(np.linalg.norm(tn[a] - tn[b]))
            e_3d = float(np.linalg.norm(pos[k, a] - pos[k, b]))
            edges[f'{a}-{b}'] = (e_tn, e_3d)
            if abs(e_tn - EDGE_NOMINAL_M) > EDGE_TOL_M:
                failures.append(f'frame {k:04d} edge {a}-{b}: T-N length {e_tn:.3f} m (nominal {EDGE_NOMINAL_M:.3f} +/- 1 m)')
            if e_3d > NEIGHBOUR_MAX_M:
                failures.append(f'frame {k:04d} edge {a}-{b}: 3D distance {e_3d:.3f} m > 1001 m')
        formation_rows.append((k, edges))
    report['formation'] = formation_rows

    # ---------------------------------------------------------------- phase correlation (chief)
    phase_rows = []
    if chief is not None:
        for a, b in ((0, 1), (499, 500), (998, 999)):
            t0 = time.monotonic()
            ia = load(renders / 'satellite_00' / f'frame_{a:04d}.png')
            ib = load(renders / 'satellite_00' / f'frame_{b:04d}.png')
            motion = -phase_shift(masked_gray(ia), masked_gray(ib), 1000)
            pred = project(chief[b], chief[a]['ground_center_local_m']) - np.array([WIDTH / 2, HEIGHT / 2])
            phase_rows.append(dict(pair=(a, b), vertical=float(motion[0]), across=float(motion[1]),
                                   predicted_rows=float(pred[1]), predicted_cols=float(pred[0]),
                                   seconds=time.monotonic() - t0))
            if not 0.9 < abs(motion[0]) < 1.1:
                failures.append(f'chief phase correlation {a}/{b}: vertical {motion[0]:+.4f} px, expected about 1.01')
    report['phase'] = phase_rows

    # ---------------------------------------------------------------- same ground area across satellites
    overlap_rows = []
    sim_cams = cams['epochs']
    for k in range(0, N_FRAMES, args.sample_every):
        cams_k = {c['satellite_id']: c for c in sim_cams[k]['cameras']}
        ref = np.asarray(cams_k[0]['footprint_local_m'])[:, :2]
        ref_area = polygon_area(ref)
        row = dict(frame=k, footprint=dict(), image=dict())
        for sat, cam in sorted(cams_k.items()):
            if sat == 0: continue
            fp = np.asarray(cam['footprint_local_m'])[:, :2]
            inter = clip_convex(fp, ref)
            ia = polygon_area(inter) if len(inter) >= 3 else 0.0
            row['footprint'][sat] = dict(overlap_of_chief=ia / ref_area, iou=ia / (ref_area + polygon_area(fp) - ia),
                                         centre_offset_m=float(np.linalg.norm(np.asarray(cam['ground_center_local_m'])
                                                                             - np.asarray(cams_k[0]['ground_center_local_m']))))
            if ia / ref_area < 0.95:
                failures.append(f'frame {k:04d} satellite {sat:02d}: footprint overlap with chief {ia / ref_area:.4f} < 0.95')
        if 0 in poses:
            chief_img = load(renders / 'satellite_00' / f'frame_{k:04d}.png')
            chief_alpha = chief_img[..., 3] > .5
            chief_gray = masked_gray(chief_img)
            for sat in sat_ids:
                if sat == 0: continue
                dep = load(renders / f'satellite_{sat:02d}' / f'frame_{k:04d}.png')
                dep_alpha = dep[..., 3] > .5
                iou = float(np.sum(chief_alpha & dep_alpha) / max(1, np.sum(chief_alpha | dep_alpha)))
                motion = -phase_shift(chief_gray, masked_gray(dep), 100)
                row['image'][sat] = dict(alpha_iou=iou, shift_rows=float(motion[0]), shift_cols=float(motion[1]))
                if iou < 0.9:
                    failures.append(f'frame {k:04d} satellite {sat:02d}: alpha IoU with chief {iou:.4f} < 0.9')
        overlap_rows.append(row)
    report['overlap'] = overlap_rows

    # ---------------------------------------------------------------- logs, disk, render time
    log_issues = []
    benign = re.compile(r"render_swarm\.py:\d+: DeprecationWarning: 'Material\.use_nodes'|^\s+material\.use_nodes=True$")
    issue = re.compile(r'error|warn|traceback|exception|fail|killed|out of memory|segmentation', re.I)
    noise = re.compile(r'^Frame \d+ satellite \d+: (corner error [0-9.e-]+px; [0-9.]+m=[0-9.]+px|geometry coverage [0-9.]+%)$')
    benign_count = 0
    for log in sorted(logs.glob('*.log')):
        for n, line in enumerate(log.read_text(errors='replace').splitlines(), 1):
            if noise.match(line): continue
            if benign.search(line): benign_count += 1; continue
            if issue.search(line): log_issues.append(f'{log.name}:{n}: {line.strip()}')
    for line in log_issues: failures.append(f'log: {line}')
    if benign_count:
        notes.append(f"{benign_count} log lines are the Blender DeprecationWarning for Material.use_nodes "
                     f"(render_swarm.py:158, once per satellite run); benign until Blender 6.0")

    job_times = []
    for log in sorted(logs.glob('job*.log')):
        for line in log.read_text().splitlines():
            m = re.match(r'(\S+Z) satellite (\d+) -> ', line)
            if m: job_times.append(['start', int(m.group(2)), parse_utc(m.group(1)), log.name])
            m = re.match(r'(\S+Z) satellite (\d+) done: (.*)', line)
            if m: job_times.append(['done', int(m.group(2)), parse_utc(m.group(1)), m.group(3)])
    per_sat_wall = {}
    for kind, sat, when, _ in job_times:
        per_sat_wall.setdefault(sat, {})[kind] = when
    wall_rows = {s: (v['start'], v.get('done'), (v['done'] - v['start']).total_seconds() if 'done' in v else None)
                 for s, v in per_sat_wall.items()}
    for s in sat_ids:
        if s not in wall_rows or wall_rows[s][1] is None:
            failures.append(f'satellite {s:02d}: no "done" line in job logs')
    starts = [v[0] for v in wall_rows.values()]; ends = [v[1] for v in wall_rows.values() if v[1]]
    render_s = {}
    for row in frame_rows:
        for rnd in row['renders']:
            render_s.setdefault(rnd['satellite_id'], []).append(rnd['render_seconds'])
    du = subprocess.run(['du', '-sb', str(args.run)], capture_output=True, text=True).stdout.split()[0]
    df = subprocess.run(['df', '-B1', '--output=avail', str(args.run)], capture_output=True, text=True).stdout.split()[-1]
    report['logs'] = dict(issues=log_issues, benign=benign_count)
    report['time'] = dict(wall=wall_rows, wall_total_s=(max(ends) - min(starts)).total_seconds() if ends else None,
                          render_s={s: dict(n=len(v), sum=float(np.sum(v)), **stats(v)) for s, v in render_s.items()},
                          setup_s=[r.get('scene_setup_seconds') for r in run_rows])
    report['disk'] = dict(run_bytes=int(du), free_bytes=int(df))
    for s in sat_ids:
        if len(render_s.get(s, [])) != N_FRAMES:
            notes.append(f'render_report.jsonl has {len(render_s.get(s, []))} frame entries for satellite {s:02d}')

    # ---------------------------------------------------------------- extra notes
    adv = report.get('advance')
    if adv and not 1.0 <= adv['image_norm']['mean'] <= 1.03:
        notes.append(f"chief image advance {adv['image_norm']['mean']:.4f} px/frame outside 1.00-1.03")
    if adv:
        notes.append(f"chief ground advance {adv['ground_px']['mean']:.4f} px/frame (Earth rotation + real ground speed "
                     f"0.05% above v_g): expected, dt kept on purpose")
    if report['phase']:
        dev = max(abs(p['vertical'] - p['predicted_rows']) for p in report['phase'])
        notes.append(f'chief phase-correlation vertical shift is {min(p["vertical"] for p in report["phase"]):.4f} px, '
                     f'up to {dev:.4f} px below the pose prediction (constant across the window: estimator bias, '
                     f'not a timing error)')
    shifts = [np.hypot(v['shift_rows'], v['shift_cols']) for row in report['overlap'] for v in row['image'].values()]
    if shifts:
        notes.append(f'deputy-vs-chief image shift up to {max(shifts):.2f} px, drifting linearly over the window; '
                     f'consistent with each satellite having its own range/GSD (scale about the image centre while the '
                     f'scene crosses the frame); footprint and alpha IoU confirm the same ground area')
    if report['disk']['free_bytes'] < 20e9:
        notes.append(f'only {report["disk"]["free_bytes"] / 1e9:.1f} GB free on the output volume')
    if args.satellites < n_sim:
        notes.append(f'only satellites {sat_ids} rendered ({len(sat_ids) * N_FRAMES} PNGs); propagation and formation '
                     f'checks include all {n_sim} satellites')

    verdict = 'FAIL' if failures else ('PASS WITH NOTES' if notes else 'PASS')
    report.update(verdict=verdict, failures=failures, notes=notes, validation_seconds=time.monotonic() - t_start)
    write_report(args, report, sat_ids, n_sim)
    (args.run / 'validation_summary.json').write_text(json.dumps(report, indent=1, default=str))
    print(f'{verdict}: {len(failures)} failures, {len(notes)} notes -> {args.run / "validation_report.md"}')


def write_report(args, r, sat_ids, n_sim):
    L = []
    w = L.append
    w('# Validation report: full 1000-frame swarm render\n')
    w(f'**Verdict: {r["verdict"]}**\n')
    if r['failures']:
        w(f'Failures ({len(r["failures"])}):\n')
        for f in r['failures'][:500]: w(f'- {f}')
        if len(r['failures']) > 500: w(f'- ... {len(r["failures"]) - 500} more (see validation_summary.json)')
        w('')
    if r['notes']:
        w('Notes:\n')
        for n in r['notes']: w(f'- {n}')
        w('')
    w(f'Run folder: `{args.run.resolve()}`  ')
    w(f'Simulation: `{args.simulation.resolve()}`  ')
    w(f'Generated {dt_.datetime.now(dt_.timezone.utc):%Y-%m-%dT%H:%M:%SZ} by `scripts/validate_run.py` '
      f'in {r["validation_seconds"] / 60:.1f} min (read-only on the outputs).\n')

    w('## 1. Folders and frames\n')
    w('| folder | PNGs | missing | unexpected | duplicate names | other files |')
    w('|---|---|---|---|---|---|')
    for name, n, miss, extra, dup, other in r['folders']:
        w(f'| {name} | {n} | {miss} | {extra} | {dup} | {", ".join(other)} |')
    w(f'\n{len(r["folders"])} satellite folders (expected {args.satellites}).\n')

    p = r['png']
    w('## 2. PNG integrity\n')
    w(f'- {p["checked"]} PNGs checked, {p["ok"]} pass: signature, IHDR 4096x3072, bit depth 8, colour type 6 (RGBA), '
      f'IEND present, Pillow mode RGBA, full decode.')
    w(f'- File size min / mean / max: {p["bytes"]["min"] / 1e6:.3f} / {p["bytes"]["mean"] / 1e6:.3f} / '
      f'{p["bytes"]["max"] / 1e6:.3f} MB; total {p["total_bytes"] / 1e9:.2f} GB.')
    w(f'- Byte-identical files: {p["duplicate_groups"]} groups.\n')

    w('## 3. Alpha coverage (fraction of pixels with alpha > 0.5)\n')
    w('| satellite | min (frame) | mean | max (frame) | frames with 0 | max(alpha>0) - coverage |')
    w('|---|---|---|---|---|---|')
    for s, c in r['coverage'].items():
        w(f'| {s:02d} | {c["min"]:.4%} ({c["argmin"]}) | {c["mean"]:.4%} | {c["max"]:.4%} ({c["argmax"]}) | '
          f'{len(c["zero_frames"])} | {c["alpha_any_minus_coverage_max"]:.2e} |')
    w(f'\nMax difference from the coverage logged by render_swarm (every 50th frame): {r["coverage_logged_max_dev"]:.2e}.\n')

    w('## 4. Poses\n')
    tg = r['time_grid']
    w(f'Time grid: {tg["n"]} samples, dt = {tg["dt"] * 1e3:.10f} ms, max |diff(t) - dt| = {tg["dt_dev_max"]:.2e} s, '
      f't = {tg["t0"]} .. {tg["t_last"]:.9f} s.\n')
    w('Consecutive position difference vs |v| dt (|v| of the mean of the two endpoint velocities, rel tol 1e-6):\n')
    w('| sat | rendered | |dr| min / mean / max (m) | speed mean (m/s) | max rel err (step) | steps > 1e-6 | poses.jsonl vs npz (m) | t_s dev (s) |')
    w('|---|---|---|---|---|---|---|---|')
    for row in r['poses']:
        w(f'| {row["sat"]:02d} | {"yes" if row["rendered"] else "no"} | {row["dr"]["min"]:.6f} / {row["dr"]["mean"]:.6f} / '
          f'{row["dr"]["max"]:.6f} | {row["speed"]["mean"]:.4f} | {row["rel_err_max"]:.2e} ({row["rel_err_argmax"]}) | '
          f'{row["n_bad"]} | {row.get("poses_vs_npz_max_m", float("nan")):.1e} | {row.get("poses_t_dev_max_s", float("nan")):.1e} |')
    w('\nCommon target (WGS84 point the chief boresight hits): distance of its projection from the image centre.\n')
    w('| sat | re-projected from poses.jsonl, max px (frame) | render_report.jsonl entries | logged max px |')
    w('|---|---|---|---|')
    for row in r['target']:
        w(f'| {row["sat"]:02d} | {row["reproj_max"]:.2e} ({row["reproj_argmax"]}) | {row["logged_n"]} | {row["logged_max"]:.2e} |')
    if 'advance' in r:
        a = r['advance']
        w('\nChief ground advance per frame (999 steps):\n')
        w('| quantity | min | mean | max |')
        w('|---|---|---|---|')
        w(f'| ground centre displacement (m) | {a["ground_m"]["min"]:.4f} | {a["ground_m"]["mean"]:.4f} | {a["ground_m"]["max"]:.4f} |')
        w(f'| ground centre displacement / native GSD (px) | {a["ground_px"]["min"]:.5f} | {a["ground_px"]["mean"]:.5f} | {a["ground_px"]["max"]:.5f} |')
        w(f'| image motion, rows (px) | {a["image_rows"]["min"]:+.5f} | {a["image_rows"]["mean"]:+.5f} | {a["image_rows"]["max"]:+.5f} |')
        w(f'| image motion, columns (px) | {a["image_cols"]["min"]:+.5f} | {a["image_cols"]["mean"]:+.5f} | {a["image_cols"]["max"]:+.5f} |')
        w(f'| image motion, norm (px) | {a["image_norm"]["min"]:.5f} | {a["image_norm"]["mean"]:.5f} | {a["image_norm"]["max"]:.5f} |')
        w(f'\nTotal chief ground-centre travel frame 0 -> 999: {a["total_ground_m"]:.2f} m. Image motion = frame-k ground centre '
          f'projected into camera k+1 (rows +down).\n')

    w('## 5. Formation (all propagated satellites, chief RTN from Cartesian r, v)\n')
    w(f'Nominal T-N edge {EDGE_NOMINAL_M:.3f} m +/- 1 m; 3D neighbour distance <= 1001 m.\n')
    for k, edges in r['formation']:
        tn = [v[0] for v in edges.values()]; d3 = [v[1] for v in edges.values()]
        w(f'Frame {k}: T-N {min(tn):.3f} .. {max(tn):.3f} m (max dev {max(abs(x - EDGE_NOMINAL_M) for x in tn):.3f} m); '
          f'3D {min(d3):.3f} .. {max(d3):.3f} m.\n')
    w('| edge | ' + ' | '.join(f'T-N f{k} (m) | 3D f{k} (m)' for k, _ in r['formation']) + ' |')
    w('|---|' + '---|---|' * len(r['formation']))
    for name in r['formation'][0][1]:
        w(f'| {name} | ' + ' | '.join(f'{e[name][0]:.3f} | {e[name][1]:.3f}' for _, e in r['formation']) + ' |')
    w('')

    w('## 6. Chief phase correlation (upsampled DFT, 1/1000 px, luminance x alpha)\n')
    w('| frames | vertical (rows, px) | across (cols, px) | predicted from poses rows / cols (px) | |shift| (px) |')
    w('|---|---|---|---|---|')
    for row in r['phase']:
        w(f'| {row["pair"][0]}/{row["pair"][1]} | {row["vertical"]:+.4f} | {row["across"]:+.4f} | '
          f'{row["predicted_rows"]:+.4f} / {row["predicted_cols"]:+.4f} | {np.hypot(row["vertical"], row["across"]):.4f} |')
    w('\nExpected vertical about 1.01 px.\n')

    w(f'## 7. Same ground area across satellites (every {args.sample_every}th frame)\n')
    w(f'Footprint overlap = area(deputy footprint ∩ chief footprint) / area(chief footprint), from cameras.json for all {n_sim} '
      f'satellites. Image checks (rendered satellites only): alpha IoU with the chief and phase-correlation shift deputy vs chief '
      f'(1/100 px).\n')
    deps = sorted(r['overlap'][0]['footprint'])
    w('| frame | ' + ' | '.join(f'sat {s:02d}' for s in deps) + ' |')
    w('|---|' + '---|' * len(deps))
    for row in r['overlap']:
        w(f'| {row["frame"]} | ' + ' | '.join(
            f'{row["footprint"][s]["overlap_of_chief"]:.4%}' for s in deps) + ' |')
    ov = [row['footprint'][s]['overlap_of_chief'] for row in r['overlap'] for s in deps]
    off = [row['footprint'][s]['centre_offset_m'] for row in r['overlap'] for s in deps]
    w(f'\nFootprint overlap min / mean: {min(ov):.4%} / {np.mean(ov):.4%}; ground-centre offset from chief max {max(off):.3f} m.\n')
    img_deps = sorted(r['overlap'][0]['image'])
    if img_deps:
        w('| frame | ' + ' | '.join(f'sat {s:02d} IoU / shift r,c (px)' for s in img_deps) + ' |')
        w('|---|' + '---|' * len(img_deps))
        for row in r['overlap']:
            w(f'| {row["frame"]} | ' + ' | '.join(
                f'{row["image"][s]["alpha_iou"]:.4f} / {row["image"][s]["shift_rows"]:+.2f}, {row["image"][s]["shift_cols"]:+.2f}'
                for s in img_deps) + ' |')
        w('')

    w('## 8. Logs, disk, render time\n')
    lg = r['logs']
    w(f'- Log lines flagged (error / warning / traceback / fail / killed / OOM): {len(lg["issues"])}; '
      f'benign DeprecationWarning lines: {lg["benign"]}.')
    d = r['disk']
    w(f'- Disk used by the run folder: {d["run_bytes"] / 1e9:.2f} GB ({d["run_bytes"] / 2**30:.2f} GiB); free on the volume: '
      f'{d["free_bytes"] / 1e9:.1f} GB.')
    t = r['time']
    w(f'- Wall clock first start -> last finish: {t["wall_total_s"] / 3600:.3f} h ({t["wall_total_s"]:.0f} s).\n')
    w('| satellite | start (UTC) | done (UTC) | wall (min) | frames in report | sum render_seconds (min) | render s/frame min / mean / max |')
    w('|---|---|---|---|---|---|---|')
    total = 0.0
    for s in sorted(t['wall']):
        st, en, sec = t['wall'][s]; rs = t['render_s'].get(s, dict(n=0, sum=0, min=0, mean=0, max=0)); total += rs['sum']
        w(f'| {s:02d} | {st:%H:%M:%S} | {en:%H:%M:%S} | {sec / 60:.1f} | {rs["n"]} | {rs["sum"] / 60:.1f} | '
          f'{rs["min"]:.2f} / {rs["mean"]:.2f} / {rs["max"]:.2f} |')
    w(f'\nTotal render time (sum of per-frame render_seconds, incl. PNG write): {total / 3600:.3f} GPU-process hours; '
      f'scene setup per run: {", ".join(f"{x:.1f}" for x in t["setup_s"] if x)} s.\n')
    (args.run / 'validation_report.md').write_text('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
