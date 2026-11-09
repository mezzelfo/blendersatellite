"""Step 10 validation: true frame period (one chief row per frame) on new poses.

Read-only on every existing output. Writes <new run>/validation_step10.json.
Old-run timing geometry (rho, v_t, rate_rows, ...) is recomputed by running
melbourne_overpass.py with --altitude-reference local --frame-period nominal (the old
behaviour) for 3 frames into a temporary folder.

Run in the conda environment "orbits":
    python scripts/validate_step10.py [--new output/orbits_melbourne/step10_validation]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stacking_geometry as g  # noqa: E402
from phase_correlation import load, masked_gray, phase_shift  # noqa: E402
from validate_run import EDGE_NOMINAL_M, RING, SPOKES, rtn_tn  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OLD_RUN = REPO / 'output/orbits_melbourne/eevee_full_1000'
# Old simulation (local altitude reference, nominal dt); regression_old reproduces 20261106T080712Z_run_IxoFnH bit for bit
OLD_SIM = REPO / 'output/orbits_melbourne/step10_validation/regression_old/simulation'
NEW_RUN = REPO / 'output/orbits_melbourne/step10_validation'


def mm(v):
    v = np.asarray(v, dtype=float)
    return dict(min=float(v.min()), max=float(v.max()), mean=float(v.mean()))


def old_timing():
    """Timing geometry of the old configuration (local altitude reference, nominal dt)."""
    with tempfile.TemporaryDirectory() as tmp:
        grid = Path(tmp) / 'grid.json'; grid.write_text('{"features": []}')
        env = dict(os.environ, PYTHONPATH=str(REPO / 'orbits/src'))
        subprocess.run([sys.executable, 'scripts/melbourne_overpass.py', '--grid', str(grid),
                        '--output', str(Path(tmp) / 'run'), '--device', 'cpu', '--formation', 'roe',
                        '--n-frames', '3', '--altitude-reference', 'local', '--frame-period', 'nominal'],
                       cwd=REPO / 'orbits', env=env, check=True, capture_output=True)
        m = json.loads((Path(tmp) / 'run/cameras.json').read_text())
        f = json.loads((Path(tmp) / 'run/formation_distances.json').read_text())
    return dict({k: m[k] for k in ('a_chief_m', 'height_above_target_m', 'frame_period_s', 'dt_nominal_s',
                                   'dt_pixel_rate_s', 'dt_closed_form_s', 'rate_rows_per_s', 'rate_cols_per_s',
                                   'v_target_m_s', 'rho_m', 'gsd_true_m')}, inclination_deg=f['inclination_deg'])


def geometry(run_dir, cameras_json):
    """Image-motion checks of all satellites on the poses of one run."""
    g.set_paths(run_dir=run_dir, cameras_json=cameras_json)
    N = len(g._cameras()['epochs'])
    frames = np.arange(N)
    c0 = np.array(g.IMAGE_CENTRE)
    out = dict(n_frames=N, sats={})
    target = g.common_target_local_m()
    for s in range(g.N_SATELLITES):
        centre = g.ground_from_pixel(s, frames, *g.IMAGE_CENTRE)               # z = 0, as step 9
        r, c = g.pixel_from_ground(s, frames[1:], centre[:-1])
        tr, tc = g.pixel_from_ground(s, frames[1:], target[:-1])               # common target (WGS84)
        cr, cc = g.pixel_from_ground(s, frames, target)
        r_last, c_last = g.pixel_from_ground(s, N - 1, centre[0])
        poses = g.load_poses(s)
        out['sats'][s] = dict(
            advance_rows=mm(r - c0[0]), advance_cols=mm(c - c0[1]),
            advance_rows_target=mm(tr - c0[0]), advance_cols_target=mm(tc - c0[1]),
            cumulative_rows_sum=float(np.sum(r - c0[0])),
            cumulative_rows_direct=float(r_last - c0[0]), cumulative_cols_direct=float(c_last - c0[1]),
            target_to_centre_max_px=float(np.max(np.hypot(cr - c0[0], cc - c0[1]))),
            native_gsd_m=mm(poses['native_gsd_m']), native_gsd_mid_m=float(poses['native_gsd_m'][N // 2]),
            source=poses['source'])
    return out


def formation(sim_dir):
    npz = np.load(sim_dir / 'results.npz')
    pos, vel = npz['positions_eci'], npz['velocities_eci']
    tn_edges, d3 = [], []
    for k in range(len(pos)):
        cr, cv = pos[k, 0], vel[k, 0]
        tn = {s: rtn_tn(cr, cv, pos[k, s] - cr) for s in range(pos.shape[1])}
        for a, b in SPOKES + RING:
            tn_edges.append(np.linalg.norm(tn[a] - tn[b])); d3.append(np.linalg.norm(pos[k, a] - pos[k, b]))
    dt = np.diff(npz['times'])
    return dict(tn_edges_m=mm(tn_edges), tn_max_dev_m=float(np.max(np.abs(np.array(tn_edges) - EDGE_NOMINAL_M))),
                d3_neighbour_max_m=float(np.max(d3)), dt_s=mm(dt))


def phase(renders, a, b, run_dir, cameras_json):
    pa, pb = renders / 'satellite_00' / f'frame_{a:04d}.png', renders / 'satellite_00' / f'frame_{b:04d}.png'
    if not (pa.exists() and pb.exists()): return None
    motion = -phase_shift(masked_gray(load(pa)), masked_gray(load(pb)), 1000)
    g.set_paths(run_dir=run_dir, cameras_json=cameras_json)
    centre = g.ground_from_pixel(0, a, *g.IMAGE_CENTRE)
    pr, pc = g.pixel_from_ground(0, b, centre)
    pred = (float(pr - g.IMAGE_CENTRE[0]), float(pc - g.IMAGE_CENTRE[1]))
    n = b - a
    return dict(pair=[a, b], vertical=float(motion[0]), across=float(motion[1]), predicted_rows=pred[0],
                predicted_cols=pred[1], residual_rows=float(motion[0] - pred[0]),
                residual_rows_per_frame=float((motion[0] - pred[0]) / n))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--new', type=Path, default=NEW_RUN, help='step-10 run folder (simulation/, renders_check/)')
    parser.add_argument('--simulation', default='simulation', help='simulation subfolder of --new')
    parser.add_argument('--old-sim', type=Path, default=OLD_SIM, help='simulation folder of the old (pre-step-10) run')
    args = parser.parse_args()
    new_sim = args.new / args.simulation
    new_cams = json.loads((new_sim / 'cameras.json').read_text())
    new_form = json.loads((new_sim / 'formation_distances.json').read_text())
    old_cams = json.loads((args.old_sim / 'cameras.json').read_text())
    report = dict(
        old_timing=old_timing(),
        new_timing={k: new_cams[k] for k in ('altitude_reference', 'frame_period_mode', 'mean_radius_m', 'a_chief_m',
                    'height_above_target_m', 'frame_period_s', 'dt_nominal_s', 'dt_pixel_rate_s', 'dt_closed_form_s',
                    'rate_rows_per_s', 'rate_cols_per_s', 'v_target_m_s', 'rho_m', 'gsd_true_m')},
        new_inclination_deg=new_form['inclination_deg'],
        old_run_inclination_deg=old_cams['formation_geometry']['inclination_deg'],
        old_run_frame_period_s=old_cams['formation_geometry']['frame_period_s'],
        tiles=dict(old=len(old_cams['selected_tiles']), new=len(new_cams['selected_tiles']),
                   identical=old_cams['selected_tiles'] == new_cams['selected_tiles']),
        new_formation=formation(new_sim), old_formation=formation(args.old_sim))
    t = report['new_timing']
    report['dt_rel_diff_closed_form'] = abs(t['dt_pixel_rate_s'] - t['dt_closed_form_s']) / t['dt_pixel_rate_s']
    report['rate_cols_times_dt_px'] = abs(t['rate_cols_per_s']) * t['frame_period_s']
    report['old_geometry'] = geometry(OLD_RUN, args.old_sim / 'cameras.json')
    report['new_geometry'] = geometry(args.new, new_sim / 'cameras.json')
    report['old_phase_0_50'] = phase(OLD_RUN / 'renders', 0, 50, OLD_RUN, args.old_sim / 'cameras.json')
    report['new_phase_0_50'] = phase(args.new / 'renders_check', 0, 50, args.new, new_sim / 'cameras.json')
    ch = report['new_geometry']['sats'][0]
    checks = dict(
        chief_advance_rows=max(abs(ch['advance_rows']['min'] - 1), abs(ch['advance_rows']['max'] - 1)) < 1e-5,
        chief_advance_cols=max(abs(ch['advance_cols']['min']), abs(ch['advance_cols']['max'])) < 1e-5,
        chief_cumulative=abs(ch['cumulative_rows_direct'] - 999) < 0.01 and abs(ch['cumulative_rows_sum'] - 999) < 0.01,
        target_centre=max(s['target_to_centre_max_px'] for s in report['new_geometry']['sats'].values()) < 0.01,
        formation_edges=report['new_formation']['tn_max_dev_m'] < 1.0,
        tiles=report['tiles']['new'] == 570 and report['tiles']['identical'],
        closed_form=report['dt_rel_diff_closed_form'] < 1e-5,
        rate_cols=report['rate_cols_times_dt_px'] < 1e-6)
    report['checks'] = checks
    report['chief_mean_advance_rows'] = ch['advance_rows']['mean']
    path = args.new / f'validation_step10_{args.simulation}.json'
    path.write_text(json.dumps(report, indent=2, default=float) + '\n')
    print(json.dumps(report, indent=1, default=float))
    print('ALL CHECKS PASS' if all(checks.values()) else f'FAILED: {[k for k, v in checks.items() if not v]}')


if __name__ == '__main__':
    main()
