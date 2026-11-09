"""Write SIMULATION_DESCRIPTION.md in the run folder (step 9 A). Read-only on every input.

Every number in the description is measured here (or by check_stacking_geometry.run_checks,
which this script calls), read from validation_summary.json / stacking_inputs.npz, or quoted
with a file:line reference. Inputs: cameras.json, results.npz, config.yaml and
formation_distances.json of the simulation, poses.jsonl, the PNGs (through the checks), the
terrain caches, render_report.jsonl, pipeline.log and validation_summary.json.

Run in the conda environment "orbits" (NumPy, PyTorch, pyproj, Pillow) after
export_stacking_inputs.py:
    python scripts/describe_simulation.py [--output PATH] [--json PATH]
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import stacking_geometry as g
import check_stacking_geometry as checks

ORBITS = g.REPO / 'orbits'
sys.path[:0] = [str(ORBITS / 'src'), str(ORBITS / 'scripts')]
import torch  # noqa: E402
from pyproj import Transformer  # noqa: E402
from orbits.elements import eci_to_elements, sso_inclination  # noqa: E402
from orbits.frames import eci_to_lvlh  # noqa: E402
from orbits.physics import Earth, Gravity  # noqa: E402
from orbits.roe import elements_to_roe  # noqa: E402
from orbits.sensor_timing import HyperScape100, frame_period, ground_speed, gsd, footprint_m  # noqa: E402
from orbits.camera_geometry import ray_wgs84_intersection  # noqa: E402
import melbourne_overpass as mo  # noqa: E402

SIM = g.CAMERAS_JSON.parent
EARTH = Earth()
FRAMES = (0, 499, 999)
EDGES = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 1)]
PLAN_ROE_M = {1: (447.2136, 0, 0, 894.4272), 2: (223.6068, 387.2983, -774.5967, 447.2136),
              3: (-223.6068, 387.2983, -774.5967, -447.2136), 4: (-447.2136, 0, 0, -894.4272),
              5: (-223.6068, -387.2983, 774.5967, -447.2136), 6: (223.6068, -387.2983, 774.5967, 447.2136)}
BBOX_7855 = [(314857.49072265625, 5808749.471992493), (323257.49072265625, 5808749.471992493),
             (323257.49072265625, 5817149.471992493), (314857.49072265625, 5817149.471992493)]
TO_GEODETIC = Transformer.from_crs(4978, 4979, always_xy=True)
TO_ECEF = Transformer.from_crs(4979, 4978, always_xy=True)
PROJ_TO_GEO = Transformer.from_crs(7855, 7844, always_xy=True)


# ---------------------------------------------------------------- numerics (numpy, float64)
def j2_acc(r):
    """Same model as orbits/src/orbits/physics.py:23-30."""
    r2 = np.sum(r * r, -1, keepdims=True)
    a = -EARTH.mu * r / r2 ** 1.5
    z2 = r[..., 2:3] ** 2 / r2
    f = np.concatenate([5 * z2 - 1, 5 * z2 - 1, 5 * z2 - 3], -1)
    return a + 1.5 * EARTH.j2 * EARTH.mu * EARTH.radius ** 2 * r * f / r2 ** 2.5


def rk4(y, h, n):
    f = lambda s: np.concatenate([s[..., 3:], j2_acc(s[..., :3])], -1)
    for _ in range(n):
        k1 = f(y); k2 = f(y + h / 2 * k1); k3 = f(y + h / 2 * k2); k4 = f(y + h * k3)
        y = y + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return y


def elements(state):
    e = eci_to_elements(torch.tensor(state, dtype=torch.float64)).numpy()
    a, ecc, inc, raan, argp, nu = e
    return dict(a=a, e=ecc, i=math.degrees(inc), raan=math.degrees(raan), argp=math.degrees(argp),
                nu=math.degrees(nu), u=math.degrees((argp + nu) % (2 * math.pi)))


def enu_axes(lon, lat):
    lo, la = np.radians(lon), np.radians(lat)
    return (np.array([-np.sin(lo), np.cos(lo), 0]),
            np.array([-np.sin(la) * np.cos(lo), -np.sin(la) * np.sin(lo), np.cos(la)]),
            np.array([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]))


def chief_capture_recomputed(manifest):
    """Chief ECI state at t_c, recomputed with the formulas of melbourne_overpass.py:107-141."""
    origin = np.asarray(manifest['origin_ecef_m'])
    r_ecef = mo.unit(origin) * (np.linalg.norm(origin) + 500000)
    radial = mo.unit(r_ecef)
    east_r = mo.unit(np.cross([0, 0, 1], radial)); north_r = np.cross(radial, east_r)
    inc = sso_inclination(float(np.linalg.norm(r_ecef)))
    east_fraction = np.cos(inc) / np.linalg.norm(np.cross([0, 0, 1], radial))
    tangent = east_fraction * east_r + np.sqrt(1 - east_fraction ** 2) * north_r
    capture = datetime.fromisoformat(manifest['capture_timestamp'])
    rot = mo.rotation_z(mo.gmst(capture))
    return np.r_[rot @ r_ecef, rot @ tangent * np.sqrt(EARTH.mu / np.linalg.norm(r_ecef))], r_ecef, inc


def fmt(x, n=3):
    return f'{x:.{n}f}'


def vec(v, n=6):
    return '(' + ', '.join(f'{x:+.{n}f}' for x in v) + ')'


# ---------------------------------------------------------------- measurements
def measure():
    t0 = time.monotonic()
    M = {}
    manifest = g._cameras()
    epochs = manifest['epochs']
    with np.load(SIM / 'results.npz') as d:
        times, states = d['times'].astype(float), d['states_eci'].astype(float)
    M['states_shape'] = list(states.shape)
    dt = float(manifest['formation_geometry']['frame_period_s'])
    tc = float(manifest['capture_time_s'])
    capture = datetime.fromisoformat(manifest['capture_timestamp'])
    theta_c = mo.gmst(capture)
    ecef_from_eci = lambda t: mo.rotation_z(theta_c + mo.GMST_RATE * (t - tc)).T   # melbourne_overpass.py:218
    M['time'] = dict(dt=dt, tc=tc, tc_over_dt=tc / dt, n=len(times), t_last=float(times[-1]),
                     dt_dev=float(np.max(np.abs(np.diff(times) - dt))),
                     start=epochs[0]['timestamp'], end=epochs[-1]['timestamp'], capture=manifest['capture_timestamp'],
                     dt_sensor=frame_period(500e3), gsd=gsd(500e3), vg=ground_speed(500e3),
                     footprint=footprint_m(500e3), window=float(times[-1] - times[0]))
    # Chief at t_c: RK4 half step from the samples either side vs the recomputed chief_capture.
    cap, r_ecef_cap, inc_code = chief_capture_recomputed(manifest)
    fwd = rk4(states[499, 0], dt / 16, 8); back = rk4(states[500, 0], -dt / 16, 8)
    M['tc_state'] = dict(fwd_vs_capture_m=float(np.linalg.norm(fwd[:3] - cap[:3])),
                         back_vs_capture_m=float(np.linalg.norm(back[:3] - cap[:3])),
                         fwd_vs_capture_v=float(np.linalg.norm(fwd[3:] - cap[3:])))
    chief_tc = fwd
    M['el0'] = elements(states[0, 0]); M['elc'] = elements(chief_tc)
    M['inc_code'] = math.degrees(inc_code); M['a_chief'] = float(manifest['formation_geometry']['a_chief_m'])
    M['inc_manifest'] = float(manifest['formation_geometry']['inclination_deg'])
    # Sub-satellite point, ground track, local time.
    r_e = ecef_from_eci(tc) @ chief_tc[:3]
    v_e = ecef_from_eci(tc) @ chief_tc[3:] - np.cross([0, 0, mo.GMST_RATE], r_e)
    lon, lat, h = TO_GEODETIC.transform(*r_e)
    east, north, up = enu_axes(lon, lat)
    nadir_hit = ray_wgs84_intersection(r_e, -r_e)
    nlon, nlat, nh = TO_GEODETIC.transform(*nadir_hit)
    origin = np.asarray(manifest['origin_ecef_m'])
    M['sub'] = dict(lon=lon, lat=lat, h=h, az=math.degrees(math.atan2(v_e @ east, v_e @ north)) % 360,
                    v_up=float(v_e @ up), v_horizontal=float(np.hypot(v_e @ east, v_e @ north)),
                    ascending=bool(chief_tc[5] > 0), vz_eci=float(chief_tc[5]),
                    geocentric_r=float(np.linalg.norm(r_e)), r_ecef_vs_code_m=float(np.linalg.norm(r_e - r_ecef_cap)),
                    nadir_lon=nlon, nadir_lat=nlat, nadir_to_origin_m=float(np.linalg.norm(nadir_hit - origin)),
                    origin_lon_lat=manifest['origin_lon_lat'])
    lmst_h = (capture.hour + capture.minute / 60 + capture.second / 3600 + lon / 15) % 24
    M['sub']['lmst'] = f'{int(lmst_h):02d}:{int(lmst_h * 60 % 60):02d}:{int(round(lmst_h * 3600 % 60)):02d}'
    # Ground speed of the common target (= chief geocentric nadir on WGS84).
    tgt = np.array([e['common_target_ecef_m'] for e in epochs])
    speed = np.linalg.norm(np.diff(tgt, axis=0), axis=1) / dt
    M['ground_speed'] = dict(min=float(speed.min()), mean=float(speed.mean()), max=float(speed.max()))
    # RAAN rate: 1 day of J2 RK4 (10 s step) from the chief state at t = 0, linear fit.
    y = states[0, 0].copy(); raan = []; tt = np.arange(0, 86400.1, 60.)
    for _ in tt:
        hv = np.cross(y[:3], y[3:]); raan.append(math.atan2(hv[0], -hv[1])); y = rk4(y, 10., 6)
    M['raan_rate_deg_day'] = float(np.polyfit(tt / 86400, np.degrees(np.unwrap(raan)), 1)[0])
    # Dense output: dopri5 samples vs fixed-step RK4 (step dt/4) from states[0], all 7 satellites.
    y = states[0].copy(); err = 0.
    for k in range(1, len(times)):
        y = rk4(y, dt / 4, 4); err = max(err, float(np.max(np.linalg.norm(y[:, :3] - states[k, :, :3], axis=1))))
    M['dense_err_m'] = err
    # Integrator settings from the run config.
    cfg = (SIM / 'config.yaml').read_text().splitlines()
    i0 = next(i for i, l in enumerate(cfg) if l.startswith('integrator:'))
    M['integrator'] = {l.split(':')[0].strip(): l.split(':', 1)[1].strip() for l in cfg[i0 + 1:i0 + 6]}
    M['integrator_line'] = i0 + 1
    # ---- formation
    roe_file = json.loads(Path(manifest['formation_geometry']['roe_file']).read_text())
    st = torch.tensor(states, dtype=torch.float64)
    el0 = eci_to_elements(st[0])
    realised = (elements_to_roe(el0[0], el0[1:]) * M['a_chief']).numpy()
    M['roe'] = [dict(sat=d['satellite'], WX=d['lattice_WX'], roe_m=d['roe_m'], roe_file=d['roe'],
                     roe_used=[x / M['a_chief'] for x in d['roe_m']], realised_m=realised[i].tolist(),
                     plan_dev=float(np.max(np.abs(np.array(d['roe_m'])[2:] - PLAN_ROE_M[d['satellite']]))))
                for i, d in enumerate(roe_file['deputies'])]
    M['roe_a_ref'] = roe_file['a_ref_m']
    rtn = {}
    for k in FRAMES:
        chief = st[k, 0]; acc = Gravity().acceleration(chief[:3])
        rel = eci_to_lvlh(chief, st[k], acc).numpy()
        rtn[k] = rel
    M['rtn'] = {k: v.tolist() for k, v in rtn.items()}
    tn = []
    for k in range(len(times)):
        chief = st[k, 0]; rel = eci_to_lvlh(chief, st[k], Gravity().acceleration(chief[:3])).numpy()
        tn.append([np.hypot(*(rel[a, 1:3] - rel[b, 1:3])) for a, b in EDGES])
    tn = np.array(tn)
    M['tn_edges'] = dict(min=float(tn.min()), max=float(tn.max()), max_dev=float(np.max(np.abs(tn - 2000 / math.sqrt(5)))))
    pos = states[:, :, :3]
    iu = np.triu_indices(7, 1)
    pd = np.linalg.norm(pos[:, :, None] - pos[:, None, :], axis=-1)[:, iu[0], iu[1]]
    kmin, pmin = np.unravel_index(pd.argmin(), pd.shape); kmax, pmax = np.unravel_index(pd.argmax(), pd.shape)
    M['pairs'] = dict(min=float(pd.min()), min_pair=[int(iu[0][pmin]), int(iu[1][pmin])], min_frame=int(kmin),
                      max=float(pd.max()), max_pair=[int(iu[0][pmax]), int(iu[1][pmax])], max_frame=int(kmax),
                      max_change=float(np.max(pd.max(0) - pd.min(0))),
                      neighbour_max=float(max(pd[:, list(zip(*iu)).index(tuple(sorted(e)))].max() for e in EDGES)))
    poses = [g.load_poses(s) for s in range(7)]
    M['distance'] = [dict(sat=s, at={k: float(p['distance_m'][k]) for k in FRAMES}, min=float(p['distance_m'].min()),
                          max=float(p['distance_m'].max()), source=p['source'],
                          dchief={k: float(p['distance_m'][k] - poses[0]['distance_m'][k]) for k in FRAMES})
                     for s, p in enumerate(poses)]
    # ---- pointing
    R = np.stack([p['camera_to_local'] for p in poses])           # [7,N,3,3]
    fwd_all = -R[..., 2]
    M['chief_axes'] = {k: dict(right=R[0, k, :, 0].tolist(), up=R[0, k, :, 1].tolist(), forward=fwd_all[0, k].tolist(),
                               az_up=math.degrees(math.atan2(R[0, k, 0, 1], R[0, k, 1, 1])) % 360) for k in FRAMES}
    off = []
    for s, p in enumerate(poses):
        ang = np.degrees(np.arccos(np.clip(np.sum(fwd_all[s] * p['nadir_forward_local'], -1), -1, 1)))
        vs = np.degrees(np.arccos(np.clip(np.sum(fwd_all[s] * fwd_all[0], -1), -1, 1)))
        off.append(dict(sat=s, min=float(ang.min()), max=float(ang.max()), field_dev=float(np.max(np.abs(ang - p['off_nadir_deg']))),
                        vs_chief_min=float(vs.min()), vs_chief_max=float(vs.max())))
    M['off_nadir'] = off
    align = np.array([[c['ground_across_alignment_with_chief'] for c in e['cameras']] for e in epochs])
    M['alignment_min'] = float(align.min())
    # Chief boresight vs the WGS84 normal at the common target (geocentric vs geodetic nadir).
    rot = np.asarray(manifest['local_from_ecef'])
    ang = []
    for k in FRAMES:
        tlon, tlat, _ = TO_GEODETIC.transform(*epochs[k]['common_target_ecef_m'])
        ang.append(math.degrees(math.acos(-(rot.T @ fwd_all[0, k]) @ enu_axes(tlon, tlat)[2])))
    M['geodetic_offnadir_deg'] = ang
    # ---- terrain, z = 0 plane, bbox margins
    shapes = []
    for tile in manifest['selected_tiles']:
        with np.load(checks.TERRAIN / f'{tile}.npz') as d:
            shapes.append((*d['shape'], float(d['sampled_fraction']), float(d['vertices'][:, 2].min()), float(d['vertices'][:, 2].max())))
    shapes = np.array(shapes)
    M['terrain'] = dict(tiles=len(shapes), files=len(list(checks.TERRAIN.glob('*.npz'))),
                        vertices=int(np.sum(shapes[:, 0] * shapes[:, 1])),
                        triangles=int(np.sum(2 * (shapes[:, 0] - 1) * (shapes[:, 1] - 1))),
                        sampled_mean=float(np.mean(shapes[:, 2])), z_min=float(shapes[:, 3].min()), z_max=float(shapes[:, 4].max()))
    header = json.loads((g._paths['run_dir'] / 'renders/render_report.jsonl').read_text().splitlines()[0])
    M['render_header'] = header
    corners_local = []
    for x, y in BBOX_7855:
        lo, la = PROJ_TO_GEO.transform(x, y)
        corners_local.append(rot @ (np.array(TO_ECEF.transform(lo, la, 0)) - origin))
    corners_local = np.array(corners_local)
    M['bbox_local'] = corners_local.tolist()
    M['plane_height'] = []
    for c in corners_local:
        e = origin + rot.T @ np.array([c[0], c[1], 0.])
        M['plane_height'].append(TO_GEODETIC.transform(*e)[2])
    margins = {}
    for k in (0, 500, 999):
        r, c = g.pixel_from_ground(0, k, corners_local)
        px = np.minimum.reduce([r + .5, g.HEIGHT - .5 - r, c + .5, g.WIDTH - .5 - c])
        margins[k] = float(px.min() * poses[0]['native_gsd_m'][k])
    M['bbox_margin_m'] = margins
    # Footprint size of the chief (cameras.json corners on z = 0).
    fp = {}
    for k in (0, 999):
        q = np.array(epochs[k]['cameras'][0]['footprint_local_m'])
        fp[k] = ((np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2,
                 (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2)
    M['footprint'] = fp
    # ---- parallax table (step 9 B3)
    locs = {'top-left (0, 0)': (0, 0), 'top-right (0, 4095)': (0, g.WIDTH - 1),
            'bottom-left (3071, 0)': (g.HEIGHT - 1, 0), 'bottom-right (3071, 4095)': (g.HEIGHT - 1, g.WIDTH - 1),
            'centre (1535.5, 2047.5)': g.IMAGE_CENTRE}
    par = []
    for s in range(1, 7):
        for name, (r0, c0) in locs.items():
            row = dict(sat=s, loc=name)
            for h in (0., 30., 100.):
                best = None
                for k in FRAMES:
                    r, c = g.chief_to_satellite_pixel(s, k, r0, c0, h)
                    r_ref, c_ref = g.chief_to_satellite_pixel(s, k, r0, c0, 0.)
                    tot = (float(r - r0), float(c - c0)); dif = (float(r - r_ref), float(c - c_ref))
                    if best is None or np.hypot(*tot) > np.hypot(*best[0]): best = (tot, dif, k)
                    row[f'diff_{h:.0f}'] = max(row.get(f'diff_{h:.0f}', 0.), float(np.hypot(*dif)))
                row[f'tot_{h:.0f}'] = best
            par.append(row)
    M['parallax'] = par
    log = (SIM.parent / 'pipeline.log').read_text().splitlines()
    i = next(i for i, l in enumerate(log) if l.startswith('Checked '))
    worst = eval(log[i].split('worst: ', 1)[1], {'__builtins__': {}})   # dict literal printed by render_swarm.py:349
    M['pipeline_line'] = i + 1
    M['pipeline_worst'] = (f'corner {worst["corner_px"]:.6f} px, pixel step dev {worst["pixel_step_dev"]:.6f}, '
                           f'target {worst["target_px"]:.6f} px, alignment 1 - {1-worst["alignment"]:.1e}')
    import subprocess
    M['commits'] = {name: subprocess.run(['git', '-C', str(path), 'rev-parse', '--short', 'HEAD'], capture_output=True,
                                         text=True).stdout.strip() for name, path in (('repo', g.REPO), ('orbits', ORBITS))}
    M['measure_seconds'] = time.monotonic() - t0
    return M


# ---------------------------------------------------------------- markdown
def write(M, C, S, out):
    L = []
    w = L.append
    T = M['time']
    st = S['stacking']
    w('# Simulation description: 7-satellite swarm burst over Melbourne (run eevee_full_1000)\n')
    w(f'Generated {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")} by `scripts/describe_simulation.py` '
      '(numbers measured by that script, by `scripts/check_stacking_geometry.py`, or read from '
      '`validation_summary.json` / `stacking_inputs.npz`; code references are `file:line` in this repository, '
      f'`orbits/` being the submodule; line numbers refer to repository HEAD {M["commits"]["repo"]} and submodule '
      f'{M["commits"]["orbits"]}, plus the uncommitted step 8/9 scripts). Read-only on every existing output.\n')
    w(f'- Run folder: `{g._paths["run_dir"]}`\n- Simulation: `{SIM}` (cameras.json, results.npz, config.yaml)\n'
      f'- Geometry module: `scripts/stacking_geometry.py`; stacking inputs: `stacking_inputs.npz` in the run folder.\n')

    w('## 1. Time and orbit\n')
    w('| quantity | value | source |\n|---|---|---|')
    w(f'| epoch of sample 0 (t = 0) | {T["start"]} | cameras.json epochs[0].timestamp; melbourne_overpass.py:215-219 |')
    w(f'| capture instant t_c | {T["capture"]}, t_c = {T["tc"]:.12f} s = {T["tc_over_dt"]:.6f} dt | cameras.json capture_time_s; melbourne_overpass.py:132 |')
    w(f'| last sample | {T["end"]}, t = {T["t_last"]:.12f} s | cameras.json epochs[999] |')
    w(f'| time grid | t_k = k dt, k = 0..{T["n"]-1}; max abs(diff(t) - dt) = {T["dt_dev"]:.2e} s | results.npz times; sensor_timing.py:38-41 |')
    w(f'| dt | {T["dt"]*1e3:.13f} ms (sensor_timing.frame_period(500 km) = {T["dt_sensor"]*1e3:.13f} ms) | formation_distances.json; sensor_timing.py:32-35 |')
    w(f'| dt definition | GSD / v_g = {T["gsd"]:.12f} m / {T["vg"]:.10f} m/s (nominal h = 500 km, Re = 6378137 m) | sensor_timing.py:20-35; melbourne_overpass.py:130 |')
    w(f'| window | {T["window"]*1e3:.6f} ms ({T["n"]} samples) | results.npz |\n')
    w('Chief osculating elements (orbits.elements.eci_to_elements, elements.py:32-59; u = argp + nu). The state at t_c '
      'is integrated from sample 499 by dt/2 (RK4, 8 steps, same J2 model) and agrees with the chief_capture state rebuilt '
      f'from melbourne_overpass.py:107-141 within {M["tc_state"]["fwd_vs_capture_m"]*1e3:.3g} mm '
      f'(from sample 500 backwards: {M["tc_state"]["back_vs_capture_m"]*1e3:.3g} mm).\n')
    w('| element | t = 0 | t = t_c |\n|---|---|---|')
    for key, unit, n in (('a', 'm', 4), ('e', '', 10), ('i', 'deg', 9), ('raan', 'deg', 9), ('argp', 'deg', 6),
                         ('nu', 'deg', 6), ('u', 'deg', 9)):
        a, b = M['el0'][key], M['elc'][key]
        w(f'| {key} {unit} | {a:.{n}f} | {b:.{n}f} |' if key != 'e' else f'| e | {a:.3e} | {b:.3e} |')
    w('')
    w(f'- At t_c the chief state is built exactly circular in the two-body sense (r perpendicular to v, |v| = sqrt(mu/r); '
      'melbourne_overpass.py:137-141), hence a = r and e = 0 (argp = 0 by the circular convention, elements.py:55). '
      'The elements are two-body osculating elements of a J2 trajectory, so they carry the short-period J2 variation: '
      f'between t = 0 and t_c a changes by {M["elc"]["a"]-M["el0"]["a"]:+.3f} m and e by {M["elc"]["e"]-M["el0"]["e"]:+.2e}. '
      'With e ~ 1e-6, argp and nu are individually ill-defined; u = argp + nu is the meaningful angle.')
    w(f'- SSO inclination actually used: sso_inclination(a_chief) = {M["inc_code"]:.9f} deg for a_chief = '
      f'{M["a_chief"]:.6f} m (melbourne_overpass.py:126-128, elements.py:8-17; pipeline.log prints the same value; '
      f'cameras.json formation_geometry.inclination_deg = {M["inc_manifest"]:.9f}). The osculating i at t = 0 '
      f'is {M["el0"]["i"]:.9f} deg (back-propagated state, config.yaml chief.inclination_deg).')
    w(f'- RAAN drift of the chief over 1 day of J2 propagation from the t = 0 state (RK4 10 s, linear fit of the '
      f'osculating RAAN): {M["raan_rate_deg_day"]:.5f} deg/day (target 0.9856 deg/day).')
    s = M['sub']
    w(f'- Satellite geodetic position at t_c (WGS84, pyproj EPSG:4978 -> 4979): lat {s["lat"]:.6f} deg, lon {s["lon"]:.6f} deg, '
      f'h {s["h"]:.3f} m; geocentric radius {s["geocentric_r"]:.3f} m. The chief is placed 500 km along the GEOCENTRIC '
      f'radial of the Melbourne point (melbourne_overpass.py:117): its geocentric nadir hits WGS84 at lat {s["nadir_lat"]:.6f}, '
      f'lon {s["nadir_lon"]:.6f}, {s["nadir_to_origin_m"]:.3f} m from the Melbourne origin '
      f'(lat/lon {s["origin_lon_lat"][1]:.6f}, {s["origin_lon_lat"][0]:.6f}); the geodetic sub-satellite point is the '
      'satellite lat/lon above.')
    w(f'- Ground-track azimuth at t_c (ECEF-relative velocity, ENU at the sub-satellite point): {s["az"]:.4f} deg from north '
      f'({s["az"]-360:.4f} deg, i.e. towards north-north-west); horizontal ECEF-relative speed {s["v_horizontal"]:.3f} m/s, '
      f'vertical {s["v_up"]:+.3f} m/s.')
    w(f'- Pass: {"ascending" if s["ascending"] else "descending"} (ECI v_z = {s["vz_eci"]:+.3f} m/s at t_c).')
    w(f'- Local mean solar time at the pass: {s["lmst"]} (UTC 00:00:00 + lon/15 h).')
    gs = M['ground_speed']
    w(f'- Ground speed of the chief nadir point (= common target) on WGS84: {gs["mean"]:.4f} m/s '
      f'(min {gs["min"]:.4f}, max {gs["max"]:.4f}), versus v_g = {T["vg"]:.4f} m/s used for dt '
      f'(ratio {gs["mean"]/T["vg"]:.6f}).')
    ig = M['integrator']
    w(f'- Propagator: point mass + J2 (orbits/src/orbits/physics.py:15-36; mu = 3.986004418e14, Re = 6378137 m, '
      f'J2 = 1.08262668e-3), fixed-pole ECI, torchdiffeq odeint {ig["method"]} with rtol {ig["rtol"]}, atol {ig["atol"]} '
      f'(simulation config.yaml:{M["integrator_line"]}-{M["integrator_line"]+4}; cli.py:39-40, 62; propagation.py:21), '
      f'float64 on cuda:0, {T["n"]} output samples (dense output of the adaptive steps). The back-propagation of the chief '
      'from t_c to t = 0 uses dopri5 with rtol 1e-11, atol 1e-10 (melbourne_overpass.py:145-146). Deputies are created at '
      't = 0 with roe_to_eci (melbourne_overpass.py:190-192, roe.py:86-89).')
    w(f'- Dense-output error (recomputed in this step): all 7 satellites, every sample versus an independent fixed-step '
      f'RK4 (step dt/4, numpy, same J2 model) started from sample 0: max position error {M["dense_err_m"]:.3e} m.\n')

    w('## 2. Formation\n')
    w(f'ROE (quasi-nonsingular, osculating, applied at t = 0 without mean/osculating conversion; roe.py:1-14). '
      f'The metre values are the specification (roe_hex7_dmax1km.json, generator roe.py:101-124). The dimensionless ROE '
      f'used by the run are roe_m / a_chief = roe_m / {M["a_chief"]:.6f} (melbourne_overpass.py:190-192); the JSON '
      f'"roe" field uses a_ref = {M["roe_a_ref"]} m (difference 7e-9 relative, not used). "Realised" = elements_to_roe of '
      'the propagated states at t = 0 times a_chief. da = dlam = 0 for every deputy.\n')
    w('| sat | (W, X) | a dex (m) | a dey (m) | a dix (m) | a diy (m) | dex | dey | dix | diy | realised max abs diff (m) |')
    w('|---|---|---|---|---|---|---|---|---|---|---|')
    for r in M['roe']:
        m, u = r['roe_m'], r['roe_used']
        dev = max(abs(a - b) for a, b in zip(r['realised_m'], m))
        w(f'| {r["sat"]} | {tuple(r["WX"])} | {m[2]:.4f} | {m[3]:.4f} | {m[4]:.4f} | {m[5]:.4f} | {u[2]:.6e} | {u[3]:.6e} | '
          f'{u[4]:.6e} | {u[5]:.6e} | {dev:.1e} |')
    w(f'\nMax deviation of the metre values from the PLAN_CONTEXT table: {max(r["plan_dev"] for r in M["roe"]):.1e} m.\n')
    w('Chief RTN frame (frames.py:6-39, eci_to_lvlh with the chief J2 acceleration): position (r, t, n) in metres and '
      'rotating-frame relative velocity in m/s.\n')
    w('| sat | (r, t, n) m, frame 0 | frame 499 | frame 999 | (vr, vt, vn) m/s, frame 0 | frame 999 |')
    w('|---|---|---|---|---|---|')
    for s_ in range(7):
        p = [M['rtn'][k][s_] for k in FRAMES]
        w(f'| {s_} | {vec(p[0][:3], 3)} | {vec(p[1][:3], 3)} | {vec(p[2][:3], 3)} | {vec(p[0][3:], 4)} | {vec(p[2][3:], 4)} |')
    P = M['pairs']; E = M['tn_edges']
    w(f'\n- T-N lattice edges (12 edges, every frame): {E["min"]:.3f} .. {E["max"]:.3f} m, max deviation from '
      f'894.427 m = {E["max_dev"]:.3f} m.')
    w(f'- Pairwise 3D distance over the whole window (21 pairs x 1000 frames): min {P["min"]:.3f} m (pair {P["min_pair"]}, '
      f'frame {P["min_frame"]}), max {P["max"]:.3f} m (pair {P["max_pair"]}, frame {P["max_frame"]}); lattice neighbours '
      f'max {P["neighbour_max"]:.3f} m; largest change of any pair distance across the window {P["max_change"]:.4f} m.\n')
    w('Slant distance `distance_m` (camera centre to the z = 0 point of its boresight; camera_geometry.py:97-99), all 7 '
      'satellites (5 and 6 from cameras.json, not rendered):\n')
    w('| sat | frame 0 (m) | frame 499 (m) | frame 999 (m) | min (m) | max (m) | minus chief f0 / f499 / f999 (m) | source |')
    w('|---|---|---|---|---|---|---|---|')
    for d in M['distance']:
        w(f'| {d["sat"]} | {d["at"][0]:.3f} | {d["at"][499]:.3f} | {d["at"][999]:.3f} | {d["min"]:.3f} | {d["max"]:.3f} | '
          f'{d["dchief"][0]:+.3f} / {d["dchief"][499]:+.3f} / {d["dchief"][999]:+.3f} | {d["source"]} |')
    w('')

    w('## 3. Pointing law and frames\n')
    w('Frames and conventions:')
    w('- ECI: J2000-aligned, fixed Earth pole (results.json "frame"; physics.py:1). ECEF = R_z(theta)^T ECI with '
      'theta(t) = GMST(capture) + GMST_RATE (t - t_c), GMST from the linear IAU formula with UTC ~ UT1 '
      '(melbourne_overpass.py:34-38, 45-46, 214-218). No polar motion, precession or nutation.')
    w('- Local frame: ENU at the Melbourne origin (EPSG:7855 320877.4907, 5812809.4720 -> GDA2020 lon/lat -> WGS84 '
      'ellipsoid, h = 0), x = East, y = North, z = Up; local = local_from_ecef (ECEF - origin) '
      '(melbourne_overpass.py:107-115, 62-63). z = 0 is the tangent plane at the origin.')
    w('- camera_to_local: 3x3 with columns (image-right, image-up, backward); boresight = -column 2 '
      '(camera_geometry.py:3, 72; melbourne_overpass.py:63). Blender places the camera with matrix_world = '
      '[camera_to_local | position_local_m] (render_swarm.py:248-250); Blender cameras look along their local -Z with +Y up, '
      'so this matrix is used as is.\n')
    w('Chief (satellite 0), per frame:')
    w('1. ECEF position r and ECEF-relative velocity v_rel = R v_ECI - omega x r (melbourne_overpass.py:56-57).')
    w('2. Boresight = geocentric nadir, forward = -r/|r| (melbourne_overpass.py:58).')
    w('3. Image-up = component of v_rel perpendicular to forward; right = forward x up; up re-orthogonalised '
      '(melbourne_overpass.py:59-61); basis rotated to local (melbourne_overpass.py:62-63).')
    w('4. Common target = chief boresight intersected with WGS84 at h = 0 (camera_geometry.py:13-42, '
      'melbourne_overpass.py:71); recomputed every frame (melbourne_overpass.py:217-224) and re-checked by the renderer '
      '(render_swarm.py:229-241).\n')
    w('Deputies (1..6), per frame (camera_geometry.py:75-96):')
    w('1. across = unit ground direction of the chief image-right on z = 0, i.e. the derivative of the ray/plane '
      'intersection (camera_geometry.py:53-61, 90).')
    w('2. aimed_basis: forward = unit(target - position); right = across made perpendicular to forward; up = right x '
      'forward (camera_geometry.py:64-72, 96). This is the roll that makes the deputy ground across-track direction '
      'parallel to the chief\'s (alignment = dot of the unit ground directions, camera_geometry.py:114-115).')
    w(f'3. Measured: min alignment over all deputies and frames = 1 - {1-M["alignment_min"]:.1e} (cameras.json '
      'ground_across_alignment_with_chief).\n')
    w('Chief image axes in ENU (unit vectors, columns of camera_to_local) and azimuth of image-up from north '
      '(atan2(E, N) of the up column):\n')
    w('| frame | image-right (E, N, U) | image-up (E, N, U) | boresight (E, N, U) | azimuth of image-up (deg) |')
    w('|---|---|---|---|---|')
    for k in FRAMES:
        a = M['chief_axes'][k]
        w(f'| {k} | {vec(a["right"])} | {vec(a["up"])} | {vec(a["forward"])} | {a["az_up"]:.4f} ({a["az_up"]-360:.4f}) |')
    w(f'\nChief boresight versus the WGS84 ellipsoid normal at the common target (geocentric vs geodetic nadir): '
      + ', '.join(f'{x:.4f} deg' for x in M['geodetic_offnadir_deg']) + ' at frames 0, 499, 999.\n')
    w('Off-nadir angle (boresight vs the satellite\'s own geocentric nadir, recomputed; matches cameras.json off_nadir_deg) '
      'and angle between each boresight and the chief boresight, min / max over the 1000 frames:\n')
    w('| sat | off-nadir min (deg) | off-nadir max (deg) | max diff vs off_nadir_deg | vs chief min (deg) | vs chief max (deg) |')
    w('|---|---|---|---|---|---|')
    for o in M['off_nadir']:
        w(f'| {o["sat"]} | {o["min"]:.6f} | {o["max"]:.6f} | {o["field_dev"]:.1e} | {o["vs_chief_min"]:.6f} | {o["vs_chief_max"]:.6f} |')
    w('')

    w('## 4. Image conventions\n')
    w('Mapping from a PNG pixel to a ray (implemented in `scripts/stacking_geometry.py`):\n')
    w('1. PNG index (r, c): row r from the TOP (0..3071), column c from the LEFT (0..4095). The integer index is the '
      'pixel CENTRE; pixel (r, c) covers [r - 1/2, r + 1/2) x [c - 1/2, c + 1/2).')
    w('2. Blender view coordinates (world_to_camera_view, origin at the BOTTOM-LEFT, [0, 1]^2): '
      'u = (c + 1/2) / 4096, v = 1 - (r + 1/2) / 3072. The renderer writes the image top row first, so row 0 is v ~ 1.')
    w('3. Camera ray: sensor_fit HORIZONTAL, f = 580 mm, Sw = 22.528 mm, Sh = Sw * 3072/4096 = 16.896 mm '
      '(render_swarm.py:190-192, 251-253, 259): d_cam = ((u - 1/2) Sw/f, (v - 1/2) Sh/f, -1).')
    w('4. ENU: d_local = camera_to_local d_cam (normalised), origin position_local_m. Inverse: '
      'p = camera_to_local^T (X - C), u = 1/2 + (f/Sw) p_x/(-p_z), v = 1/2 + (f/Sh) p_y/(-p_z).')
    w(f'5. Optical axis / image centre: (r, c) = ({g.IMAGE_CENTRE[0]}, {g.IMAGE_CENTRE[1]}), between the 4 central pixels. '
      'The footprint corners in cameras.json are the outer pixel EDGES, (r, c) = (-1/2, -1/2) etc. '
      '(camera_geometry.py:105-109; checked by render_swarm.py:262-265).\n')
    ms = C['motion_sign']
    adv = st['advance']
    w(f'Direction of motion: image-up (decreasing row) is the chief\'s ECEF-relative velocity, so ground features move '
      f'towards INCREASING PNG row as the frame index grows. Advance of the ground in the chief\'s own pixels '
      f'(stacking_inputs.npz image_motion_rows_px[0]): {adv["rows_min"]:+.5f} .. {adv["rows_max"]:+.5f} rows per frame '
      f'(sign +, i.e. downward), columns {adv["cols_min"]:+.1e} .. {adv["cols_max"]:+.1e}. Checked on the PNGs without '
      f'the poses: the integer row shift that best maps chief frame 0 onto frame 50 is {ms["best_row_shift_frame0_to_50"]:+d} '
      f'rows (masked SSD {ms["cost_best"]:.1f} vs {ms["cost_minus"]:.1f} for the opposite sign; poses predict '
      f'{ms["predicted_rows"]:+.3f}).\n')
    w('Real-image check of the convention (`check_stacking_geometry.py`): 200000 random vertices of the 570 terrain caches '
      f'(of {C["terrain_vertices_total"]}; seed 0), projected with pixel_from_ground, PNG read at the nearest pixel '
      'where alpha > 0, |dRGB| = mean over R, G, B of |PNG - vertex| (8-bit DN). n = samples with alpha > 0.\n')
    w('| sat / frame | variant | n | median | p95 | within 25 DN |\n|---|---|---|---|---|---|')
    for key, res in C['conventions'].items():
        for name in ('correct', 'row_flipped', 'col_flipped', 'half_px_plus', 'half_px_minus'):
            v = res[name]
            w(f'| {key} | {name} | {v["n"]} | {v["median"]:.2f} | {v["p95"]:.2f} | {v["within_25"]*100:.2f}% |')
    w('\nhalf_px_plus / minus shift both axes by +1/2 / -1/2 px (pixel-corner instead of pixel-centre convention, either '
      'sign). Bilinear sweep of a sub-pixel offset (dr, dc) in {-0.5, -0.25, 0, 0.25, 0.5}^2: the minimum mean |dRGB| is at '
      + ', '.join(sorted({v['bilinear_best_offset'] for v in C['conventions'].values()})) + ' for every satellite/frame.\n')
    rt = C['round_trip_max_px']; tc_ = C['target_centre']
    w(f'Round trip pixel -> ground (z = 0, 30, 100 m) -> pixel on a 61 x 81 grid incl. the outer edges, frames 0/499/999: '
      f'max error {max(rt.values()):.2e} px over all 7 satellites (per satellite: '
      + ', '.join(f'{k}: {v:.1e}' for k, v in rt.items()) + ').\n')
    w('Common target -> image centre, all 1000 frames (this module vs step 8, validation_summary.json target.reproj_max):\n')
    w('| sat | max px (this module) | frame | step 8 max px |\n|---|---|---|---|')
    for k, v in tc_.items():
        s8 = 'not rendered' if v['step8_max_px'] is None else f'{v["step8_max_px"]:.2e}'
        w(f'| {k} | {v["max_px"]:.2e} | {v["argmax"]} | {s8} |')
    w('')

    w('## 5. Render pipeline\n')
    h = M['render_header']
    w(f'- Blender {h["blender_version"]}, engine {h["engine"]}, GPU {h["eevee_gpu"]["renderer"]} via {h["eevee_gpu"]["backend"]} '
      '(renders/render_report.jsonl run header).')
    w('- Settings that differ from the factory defaults (scene from read_factory_settings(use_empty=True), '
      'render_swarm.py:132): engine BLENDER_EEVEE (136); taa_render_samples 8 (137, --samples 8); use_shadows, '
      'use_raytracing, use_fast_gi, use_volumetric_shadows, use_overscan = False (139-141); motion blur off (142); '
      'resolution 4096 x 3072 at 100% (151, 259); PNG RGBA (152-153), colour depth left at 8 bit; film_transparent True '
      '(154); view transform Standard, look None (155-156); camera PERSP, sensor_fit HORIZONTAL, DOF off (190-193); '
      'clip range distance_m -/+ 10 km per camera (257-258); lens 580 mm, sensor 22.528 x 16.896 mm (251-253). '
      'The pixel filter, colour space of the PNG (sRGB) and everything else are factory defaults. No lights, no world.')
    w('- Material: one emission shader fed by the "Color" point attribute, strength 1 (render_swarm.py:157-164). Vertex '
      'colours are the 8-bit sRGB texture samples of the cache converted to scene-linear (render_swarm.py:179-183); the '
      'Standard view transform converts back to sRGB on output. A pixel therefore holds the baked aerial texture colour '
      '(including the sun/shadows of the original aerial survey) interpolated over the 2.5 m triangles and averaged by the '
      '8-sample anti-aliasing. No illumination model, no atmosphere, no spectral content beyond R, G, B.')
    t = M['terrain']
    w(f'- Terrain: {t["tiles"]} tiles (cameras.json selected_tiles; {t["files"]} .npz in data/swarm_terrain), each a '
      f'regular 2.5 m grid of the photomesh UPPER surface (highest face per cell, empty cells filled from the nearest '
      f'sampled cell; prepare_swarm_terrain.py:62-85, 117-121), so roofs are kept and facades/overhangs are lost. '
      f'Vertices {t["vertices"]:,}, triangles {t["triangles"]:,} (2 per grid cell, render_swarm.py:174-176; run header '
      f'reports {h["triangles"]:,}); mean sampled fraction of cells {t["sampled_mean"]*100:.1f}%; local z range '
      f'{t["z_min"]:.2f} .. {t["z_max"]:.2f} m. Heights are AHD used as ellipsoidal height (cameras.json terrain_vertical), '
      'converted with the full ECEF -> ENU transform, so the z = 0 tangent plane is not the ellipsoid: the ellipsoid '
      'height of z = 0 at the 4 dataset bbox corners is ' + ', '.join(f'{x:.2f}' for x in M['plane_height']) + ' m.')
    al = st['alpha']
    w(f'- Alpha: film_transparent, so alpha = geometry coverage of the pixel (0 outside the dataset, fractional on the '
      f'anti-aliased boundary). Pixels with 0 < alpha < 255: {al["partial_min"]} .. {al["partial_max"]} per image. '
      f'Alpha is straight (not premultiplied): median luminance of pixels with alpha 1-127 / 128-254 / 255 at chief '
      f'frame 0 = {C["images"]["alpha_semantics"]["median_lum_alpha_1_127"]:.1f} / '
      f'{C["images"]["alpha_semantics"]["median_lum_alpha_128_254"]:.1f} / {C["images"]["alpha_semantics"]["median_lum_opaque"]:.1f} DN.')
    w('- Format: PNG, 8-bit RGBA, 4096 x 3072, sRGB (validation_summary.json png: 5000/5000 pass).\n')
    w('Scene extent in the image, computed from the PNGs (alpha > 0, read-only; stacking_inputs.npz alpha_row_any / '
      'alpha_col_any / alpha_bounds). Union = rows/columns with alpha > 0 in at least one frame; intersection = in every '
      'frame. All ranges are contiguous and inclusive (PNG indices).\n')
    w('| sat | union rows | union cols | intersection rows | intersection cols | frames touching row 0 (last) | bottom/left/right edge touched |')
    w('|---|---|---|---|---|---|---|')
    for e in al['extent']:
        w(f'| {e["sat"]} | {e["ru"]} | {e["cu"]} | {e["ri"]} | {e["ci"]} | {e["top_frames"]} ({e["top_last"]}) | {e["other_edges"]} |')
    w(f'\nThe imaged tiles touch the top image row (forward, north-north-west) only in the first '
      f'{min(e["top_frames"] for e in al["extent"])}-{max(e["top_frames"] for e in al["extent"])} frames; no other '
      'image edge is ever reached. The bbox-corner margin is more pessimistic because the 570 tiles do not fill the '
      '8.4 x 8.4 km bbox: chief margin of the 4 EPSG:7855 bbox corners (on the ellipsoid; metres = pixels x native GSD; '
      'negative = outside) '
      + ', '.join(f'frame {k}: {v:+.1f} m' for k, v in M['bbox_margin_m'].items()) + '.\n')
    w('Stacking geometry (stacking_inputs.npz; ground sizes at the image centre on z = 0; advance = image motion of the '
      'ground in the satellite\'s own pixels between consecutive frames):\n')
    w('| sat | native GSD f0 / f999 (m) | ground px across f0 / f999 (m) | ground px along f0 / f999 (m) | centre step (m) | advance rows (px/frame) | advance cols (px/frame) | ratio to chief |')
    w('|---|---|---|---|---|---|---|---|')
    for r in st['per_sat']:
        w(f'| {r["sat"]} | {r["gsd"][0]:.6f} / {r["gsd"][1]:.6f} | {r["across"][0]:.6f} / {r["across"][1]:.6f} | '
          f'{r["along"][0]:.6f} / {r["along"][1]:.6f} | {r["step"]:.6f} | {r["rows"][0]:.6f} .. {r["rows"][1]:.6f} | '
          f'{r["cols"][0]:+.1e} .. {r["cols"][1]:+.1e} | {r["ratio"][0]:.6f} .. {r["ratio"][1]:.6f} |')
    w('\nParallax (step 9 B3): chief pixel (r, c) -> ground point at height h -> pixel of the deputy at the same frame '
      '(chief_to_satellite_pixel). "Offset" = deputy pixel - chief pixel (rows, cols), largest norm over frames 0, 499, 999 '
      '(frame in brackets). "Parallax" = largest norm of offset(h) - offset(0) over the same frames, i.e. the part caused '
      'by the height alone; offset(0) is the scale/position difference on the z = 0 plane.\n')
    w('| sat | chief pixel | offset h=0 (rows, cols) | offset h=30 m | offset h=100 m | parallax 30 m (px) | parallax 100 m (px) |')
    w('|---|---|---|---|---|---|---|')
    for p in M['parallax']:
        cell = lambda h: f'({p[f"tot_{h}"][0][0]:+.3f}, {p[f"tot_{h}"][0][1]:+.3f}) [{p[f"tot_{h}"][2]}]'
        w(f'| {p["sat"]} | {p["loc"]} | {cell(0)} | {cell(30)} | {cell(100)} | {p["diff_30"]:.4f} | {p["diff_100"]:.4f} |')
    w('')

    w('## 6. Results of steps 1-8 (expected versus measured)\n')
    w('The step 1-7 verification reports were given in the chat sessions and are NOT on disk; every line below was '
      're-measured in this step (source "step 9": describe_simulation.py / check_stacking_geometry.py) or read from '
      'validation_summary.json (step 8), pipeline.log or render_report.jsonl. Values quoted from an earlier step without '
      're-measurement are marked "from step N".\n')
    w('| step | check | expected | measured | source |\n|---|---|---|---|---|')
    for row in S['results']:
        w('| ' + ' | '.join(str(x) for x in row) + ' |')
    w('')

    w('## 7. Known limitations and deviations\n')
    for item in S['limitations']:
        w(f'- {item}')
    w('')
    out.write_text('\n'.join(L) + '\n')


def stacking_summary(npz_path):
    with np.load(npz_path) as d:
        D = {k: d[k] for k in d.files}
    per = []
    for s in range(7):
        per.append(dict(sat=s, gsd=D['native_gsd_m'][s, [0, 999]], across=D['ground_pixel_across_m'][s, [0, 999]],
                        along=D['ground_pixel_along_m'][s, [0, 999]], step=float(D['centre_step_m'][s].mean()),
                        rows=(float(D['image_motion_rows_px'][s].min()), float(D['image_motion_rows_px'][s].max())),
                        cols=(float(D['image_motion_cols_px'][s].min()), float(D['image_motion_cols_px'][s].max())),
                        ratio=(float(D['image_motion_ratio_to_chief'][s].min()), float(D['image_motion_ratio_to_chief'][s].max()))))
    rng = lambda m: (lambda i: f'{i[0]}..{i[-1]} ({len(i)})' + ('' if np.all(np.diff(i) == 1) else ' NOT contiguous'))(np.flatnonzero(m))
    ext = []
    for j, s in enumerate(D['alpha_satellites']):
        b = D['alpha_bounds'][j]
        top = np.flatnonzero(b[:, 0] == 0)
        edges = [n for n, hit in (('bottom', np.any(b[:, 1] == g.HEIGHT - 1)), ('left', np.any(b[:, 2] == 0)),
                                  ('right', np.any(b[:, 3] == g.WIDTH - 1))) if hit]
        ext.append(dict(sat=int(s), ru=rng(D['alpha_row_any'][j].any(0)), cu=rng(D['alpha_col_any'][j].any(0)),
                        ri=rng(D['alpha_row_any'][j].all(0)), ci=rng(D['alpha_col_any'][j].all(0)),
                        top_frames=len(top), top_last=int(top[-1]) if len(top) else '-', other_edges=', '.join(edges) or 'none'))
    return dict(per_sat=per, D=D,
                advance=dict(rows_min=float(D['image_motion_rows_px'][0].min()), rows_max=float(D['image_motion_rows_px'][0].max()),
                             cols_min=float(D['image_motion_cols_px'][0].min()), cols_max=float(D['image_motion_cols_px'][0].max())),
                alpha=dict(extent=ext, partial_min=int(D['alpha_partial_pixels'].min()), partial_max=int(D['alpha_partial_pixels'].max())))


def results_and_limitations(M, C, st, V):
    T = M['time']; s8 = V
    adv = s8['advance']; ph = s8['phase']
    shifts = [np.hypot(v['shift_rows'], v['shift_cols']) for row in s8['overlap'] for v in row['image'].values()]
    dep = C['deputy_offsets']
    pred_err = max(np.hypot(x['predicted_rows'] - x['step8_rows'], x['predicted_cols'] - x['step8_cols'])
                   for v in dep.values() for x in v)
    ec = C['images']['eevee_minus_cycles']
    D = st['D']
    fp = M['footprint']
    hdr = M['render_header']
    R = [
        (1, 'GSD = h p / f', '4.741379310344828 m', f'{T["gsd"]:.15f} m', 'step 9 (sensor_timing.gsd)'),
        (1, 'v_g', '7059.2164500563995 m/s', f'{T["vg"]:.10f} m/s', 'step 9 (sensor_timing.ground_speed)'),
        (1, 'dt = GSD / v_g', '0.6716580152896355 ms', f'{T["dt"]*1e3:.16f} ms', 'step 9 (formation_distances.json, results.npz)'),
        (1, 'nominal footprint across x along', '19420.7 x 14565.5 m', f'{T["footprint"][0]:.1f} x {T["footprint"][1]:.1f} m', 'step 9 (sensor_timing.footprint_m)'),
        (2, 'ROE metre table', 'PLAN_CONTEXT values', f'max dev {max(r["plan_dev"] for r in M["roe"]):.1e} m', 'step 9 (roe_hex7_dmax1km.json)'),
        (2, 'ROE realised at t = 0', 'equal to roe_m', f'max dev {max(max(abs(a-b) for a, b in zip(r["realised_m"], r["roe_m"])) for r in M["roe"]):.1e} m', 'step 9 (elements_to_roe of results.npz)'),
        (3, 'states_eci shape', '[1000, 7, 6]', str(M['states_shape']), 'step 9 (results.npz)'),
        (3, 'diff(times) = dt', 'rel 1e-12', f'max abs dev {s8["time_grid"]["dt_dev_max"]:.1e} s', 'validation_summary.json time_grid'),
        (3, 'chief at t_c vs chief_capture', '< 1 mm', f'{M["tc_state"]["fwd_vs_capture_m"]*1e3:.3g} mm', 'step 9 (RK4 half step from sample 499)'),
        (3, 'chief geocentric nadir at t_c hits the Melbourne point', '< 1 m', f'{M["sub"]["nadir_to_origin_m"]:.3f} m', 'step 9'),
        (3, 'dense output vs RK4 dt/4', '< 1e-6 m', f'{M["dense_err_m"]:.2e} m', 'step 9'),
        (3, 'T-N lattice edges', '894.427 +/- 1 m', f'{M["tn_edges"]["min"]:.3f} .. {M["tn_edges"]["max"]:.3f} m', 'step 9; validation_summary.json formation'),
        (3, '3D neighbour distance', '<= 1001 m', f'max {M["pairs"]["neighbour_max"]:.3f} m', 'step 9'),
        (3, 'change of any pair distance over the window', '< 1 m', f'{M["pairs"]["max_change"]:.4f} m', 'step 9'),
        (3, '|dr| = |v| dt', 'rel 1e-6', f'max rel err {max(p["rel_err_max"] for p in s8["poses"]):.1e}', 'validation_summary.json poses'),
        (3, 'SSO RAAN drift', '0.9856 deg/day +/- 0.5%', f'{M["raan_rate_deg_day"]:.5f} deg/day ({(M["raan_rate_deg_day"]/0.9856-1)*100:+.2f}%)', 'step 9 (1-day J2 RK4)'),
        (3, 'chief nadir ground speed', 'report vs v_g = 7059.216 m/s', f'{M["ground_speed"]["mean"]:.3f} m/s', 'step 9 (common_target_ecef_m)'),
        (3, 'bbox corner margin in the chief frame f0 / f999', '-734 / +387 m (from step 3, prompt_4_per_frame_poses.md:32)',
         f'{M["bbox_margin_m"][0]:+.1f} / {M["bbox_margin_m"][999]:+.1f} m', 'step 9'),
        (4, 'chief footprint across x along, f0 / f999', '19420.7 x 14565.5 m +/- 0.5%',
         f'{fp[0][0]:.1f} x {fp[0][1]:.1f} / {fp[999][0]:.1f} x {fp[999][1]:.1f} m', 'step 9 (cameras.json footprint_local_m)'),
        (4, 'chief native GSD', '4.741 m +/- 0.1%', f'{D["native_gsd_m"][0].min():.6f} .. {D["native_gsd_m"][0].max():.6f} m', 'step 9 (poses.jsonl)'),
        (4, 'chief ground advance per frame', '1.00 .. 1.02 px', f'{adv["ground_px"]["min"]:.5f} .. {adv["ground_px"]["max"]:.5f} px; image rows {st["advance"]["rows_min"]:.5f} .. {st["advance"]["rows_max"]:.5f}', 'validation_summary.json advance; step 9 (stacking_inputs.npz)'),
        (4, 'common target -> image centre', '< 0.01 px', f'max {max(v["max_px"] for v in C["target_centre"].values()):.1e} px (7 sats)', 'step 9; validation_summary.json target'),
        (4, 'deputy ground across alignment', '>= 1 - 1e-10', f'1 - {1-M["alignment_min"]:.1e}', 'step 9 (cameras.json)'),
        (4, 'selected tiles', '570', str(M['terrain']['tiles']), 'step 9 (cameras.json)'),
        (5, 'terrain caches', '570 valid .npz', f'{M["terrain"]["files"]} files, {M["terrain"]["tiles"]} selected present', 'step 9 (data/swarm_terrain)'),
        (5, 'triangle count', 'as loaded by the renderer', f'{M["terrain"]["triangles"]:,} (run header {hdr["triangles"]:,})', 'step 9; render_report.jsonl'),
        (6, 'Blender projection checks (7000 poses)', 'corner < 0.1 px, GSD step < 0.002, target < 0.01 px',
         M['pipeline_worst'], f'pipeline.log:{M["pipeline_line"]} (simulation run)'),
        (6, 'render-time target error', '< 0.01 px', f'max {max(t["logged_max"] for t in s8["target"]):.2e} px', 'validation_summary.json target.logged_max'),
        (7, 'EEVEE render time per frame', 'n/a', f'{min(v["mean"] for v in s8["time"]["render_s"].values()):.2f} .. {max(v["mean"] for v in s8["time"]["render_s"].values()):.2f} s mean per satellite', 'validation_summary.json time.render_s'),
        (7, 'EEVEE minus Cycles brightness', 'about 0 (from step 7: about 0.4 DN)',
         ', '.join(f'sat {r["sat"]} f{r["frame"]}: {r["mean_all"]:+.3f} DN' for r in ec), 'step 9 (Cycles PNGs in the simulation run folder, alpha = 255 in both)'),
        (8, 'PNGs', '5000 valid RGBA 8-bit 4096x3072', f'{s8["png"]["ok"]} / {s8["png"]["checked"]}', 'validation_summary.json png'),
        (8, 'alpha coverage (alpha > 0.5)', 'non-zero every frame', f'{min(v["min"] for v in s8["coverage"].values())*100:.4f} .. {max(v["max"] for v in s8["coverage"].values())*100:.4f}%', 'validation_summary.json coverage'),
        (8, 'chief phase correlation, vertical', 'about 1.01 px', f'{min(p["vertical"] for p in ph):.4f} px vs pose prediction {min(p["predicted_rows"] for p in ph):.4f}', 'validation_summary.json phase'),
        (8, 'deputy vs chief image shift', 'small', f'up to {max(shifts):.2f} px; predicted from poses on terrain vertices within {pred_err:.2f} px', 'validation_summary.json overlap; step 9 (deputy_offsets)'),
        (8, 'verdict', 'PASS', s8['verdict'], 'validation_summary.json verdict'),
    ]
    dmax = max(abs(d['dchief'][k]) for d in M['distance'] for k in FRAMES)
    pmax = max(np.hypot(*p['tot_0'][0]) for p in M['parallax'])
    Lm = [
        'Terrain is a 2.5 m heightfield of the photomesh upper surface: no facades, overhangs, bridges underside or '
        'vertical walls; steep edges become ramps one cell wide. Missing cells are filled from the nearest sampled cell.',
        'RGB only: pixels are the baked aerial texture colours (with the survey\'s own sun and shadows) through an emission '
        'shader; no illumination model, BRDF, atmosphere, noise, MTF or spectral bands. Any hyperspectral cube built from '
        'these frames has 3 channels per satellite.',
        'Full-frame perspective camera, not a pushbroom: each frame is an instantaneous snapshot ("RGB full-frame '
        'perspective approximation of pushbroom imager", cameras.json mode).',
        'Footprints, ground pixel sizes, distance_m and native_gsd_m are on the local z = 0 tangent plane; the common target is '
        'on the WGS84 ellipsoid (z about -0.45 m at the target). The z = 0 plane departs from the ellipsoid by up to '
        f'{max(M["plane_height"]):.1f} m at the dataset corners. Terrain heights are AHD used as ellipsoidal height.',
        f'EEVEE is about {np.mean([r["mean_all"] for r in ec]):.2f} DN brighter than Cycles on average over the opaque '
        'pixels of the frames rendered with both engines (mean abs difference about '
        f'{np.mean([r["mean_abs_DN"] for r in ec]):.1f} DN per channel).',
        f'Chief phase correlation is {min(p["predicted_rows"] - p["vertical"] for p in ph):.4f} .. '
        f'{max(p["predicted_rows"] - p["vertical"] for p in ph):.4f} px below the pose prediction, constant over the window '
        '(estimator bias on this scene, not a timing error; step 8).',
        f'Deputy images are shifted up to {max(shifts):.2f} px relative to the chief (step 8 phase correlation). The poses '
        f'predict the same shifts within {pred_err:.2f} px from the mean deputy-minus-chief position of terrain vertices: the '
        'shift is the scale difference (distance differs by up to '
        f'{dmax:.1f} m from the chief, i.e. up to {pmax:.2f} px at the image corners on z = 0, parallax table) acting on a scene that is '
        'off-centre and moves across the frame. It is geometry, not an error; use chief_to_satellite_pixel to co-register.',
        'Satellites 5 and 6 are propagated and have poses (cameras.json) but were not rendered; stacking_geometry reads '
        'their poses from cameras.json.',
        f'The chief ground advance is {st["advance"]["rows_min"]:.4f} .. {st["advance"]["rows_max"]:.4f} px per frame, not 1: '
        'dt is defined with the nominal 500 km, non-rotating spherical v_g, while the real ground motion includes Earth '
        f'rotation and the actual radius (chief nadir speed {M["ground_speed"]["mean"]:.2f} m/s on WGS84). Over 999 frames '
        f'the scene moves {np.sum(D["image_motion_rows_px"][0]):.1f} rows. The deputies advance at '
        f'{min(r["ratio"][0] for r in st["per_sat"]):.5f} .. {max(r["ratio"][1] for r in st["per_sat"]):.5f} times the '
        'chief rate in their own pixels (own GSD).',
        f'In the first {max(e["top_frames"] for e in st["alpha"]["extent"])} frames the dataset crosses the top image edge '
        '(alpha > 0 in row 0): part of the northern tiles is not imaged in those frames. Only '
        f'{st["alpha"]["extent"][0]["ri"]} rows contain the scene in every frame of the chief.',
        f'The chief boresight is the GEOCENTRIC nadir; it is {np.mean(M["geodetic_offnadir_deg"]):.4f} deg from the ellipsoid '
        'normal at the target. The chief is placed 500 km along the geocentric radial (ellipsoidal height '
        f'{M["sub"]["h"]:.2f} m).',
        'Earth orientation: fixed pole, GMST with UTC ~ UT1, no precession/nutation/polar motion; J2 only, no drag, '
        'no third bodies, no SRP (irrelevant over 0.67 s, but the absolute epoch geometry is approximate).',
        'ROE are osculating and applied directly at t = 0 (no mean/osculating map).',
        'PLAN_CONTEXT disagreements found: ground track azimuth is '
        f'{M["sub"]["az"]-360:.2f} deg from north at t_c (PLAN estimate "about 9 deg"); the run integrator for the '
        f'1000-sample propagation uses rtol {M["integrator"]["rtol"]}, atol {M["integrator"]["atol"]} (config default), '
        'tighter values only for the chief back-propagation; the osculating a at t = 0 is '
        f'{M["el0"]["a"]:.1f} m, not a_chief = {M["a_chief"]:.1f} m (short-period J2 variation of the osculating a; '
        'a_chief is exact at t_c only); the export '
        'sample of formation_distances.json is 500, not t_c = 499.5 dt. Everything else checked here matches.',
    ]
    return R, Lm


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=g.RUN_DIR / 'SIMULATION_DESCRIPTION.md')
    parser.add_argument('--json', type=Path, help='also dump the measurements here')
    args = parser.parse_args()
    start = time.monotonic()
    M = measure()
    C = checks.run_checks()
    V = json.loads((g._paths['run_dir'] / 'validation_summary.json').read_text())
    st = stacking_summary(g._paths['run_dir'] / 'stacking_inputs.npz')
    R, Lm = results_and_limitations(M, C, st, V)
    write(M, C, dict(stacking=st, results=R, limitations=Lm), args.output)
    if args.json:
        args.json.write_text(json.dumps(dict(M=M, C=C), indent=1, default=lambda o: o.tolist() if hasattr(o, 'tolist') else str(o)))
    print(f'wrote {args.output} in {time.monotonic()-start:.1f} s')


if __name__ == '__main__':
    main()
