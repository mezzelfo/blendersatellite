"""Pixel <-> ground geometry of the rendered swarm frames, for stacking the frames of
each satellite into a hyperspectral cube. NumPy only (no Blender, no PyTorch).

Data sources (read-only)
------------------------
- <run>/renders/satellite_SS/poses.jsonl: one JSON record per rendered frame. Records
  are indexed by their "frame" field, never by line order (resumed jobs may append out
  of order). Satellites without poses.jsonl (5 and 6 in eevee_full_1000) are read from
  the per-epoch cameras.json of the simulation, which is the file the renderer itself
  consumed (render_swarm.py:115-119, 244-259).
- Both give, per satellite and frame, position_local_m (camera centre) and
  camera_to_local (3x3) in the local ENU frame of the Melbourne origin
  (melbourne_overpass.py:107-115): x = East, y = North, z = Up, metres, origin on the
  WGS84 ellipsoid at EPSG:7855 (320877.49, 5812809.47). z = 0 is the tangent plane at the
  origin, NOT the ellipsoid (it lies 1.7 .. 4.3 m above it at the dataset bbox corners,
  measured by describe_simulation.py).

Camera convention (Blender, render_swarm.py:187-259)
----------------------------------------------------
camera_to_local columns are (right, up, backward): the camera looks along -column 2.
The Blender camera uses sensor_fit HORIZONTAL, lens f = 580 mm, sensor width
Sw = 22.528 mm, resolution W x H = 4096 x 3072, no shift, square pixels, so the view
height is Sh = Sw * H / W = 16.896 mm. Blender's world_to_camera_view returns (u, v) in
[0, 1] with the origin at the BOTTOM-LEFT of the image:
    p_cam = R^T (X - C)                     (R = camera_to_local, C = position_local_m)
    u = 1/2 + (f / Sw) * p_cam.x / (-p_cam.z)
    v = 1/2 + (f / Sh) * p_cam.y / (-p_cam.z)

PNG pixel convention (verified on the real images, see check_stacking_geometry.py)
----------------------------------------------------------------------------------
PNG row r counts from the TOP, column c from the LEFT. The integer index (r, c) is the
CENTRE of that pixel; the pixel covers [r - 1/2, r + 1/2) x [c - 1/2, c + 1/2):
    c = u * W - 1/2
    r = (1 - v) * H - 1/2
The optical axis (u, v) = (1/2, 1/2) is at (r, c) = (1535.5, 2047.5) = IMAGE_CENTRE,
between the four central pixels. To read the PNG value at a continuous (r, c), use the
pixel np.rint(r), np.rint(c) (nearest neighbour) or bilinear interpolation on integer
centres. Image-up (decreasing row) points along the chief's ECEF-relative velocity;
ground features therefore move towards INCREASING PNG row as the frame index grows
(+1.0102 rows per frame for the chief).

Inverse (pixel -> ray):
    u = (c + 1/2) / W,  v = 1 - (r + 1/2) / H
    d_cam = ((u - 1/2) Sw / f, (v - 1/2) Sh / f, -1),  d_local = R d_cam / |d_cam|

All functions accept a satellite id (0 = chief, 1..6 = deputies), a frame index (int or
integer array) and rows/cols (scalars or arrays); frame, rows and cols broadcast together.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
RUN_DIR = REPO / 'output/orbits_melbourne/eevee_full_1000'
CAMERAS_JSON = REPO / 'output/orbits_melbourne/20261109T180310Z_run_v2/simulation/cameras.json'
N_SATELLITES = 7
N_FRAMES = 1000

WIDTH, HEIGHT = 4096, 3072                 # PNG columns, rows
FOCAL_MM = 580.0
SENSOR_WIDTH_MM = 22.528
SENSOR_HEIGHT_MM = SENSOR_WIDTH_MM * HEIGHT / WIDTH   # 16.896, sensor_fit HORIZONTAL
PIXEL_PITCH_M = 5.5e-6
IMAGE_CENTRE = ((HEIGHT - 1) / 2, (WIDTH - 1) / 2)    # (row, col) of the optical axis

_paths = dict(run_dir=RUN_DIR, cameras_json=CAMERAS_JSON)


def set_paths(run_dir=None, cameras_json=None):
    """Point the module at another run folder / cameras.json (clears the caches)."""
    if run_dir is not None: _paths['run_dir'] = Path(run_dir)
    if cameras_json is not None: _paths['cameras_json'] = Path(cameras_json)
    _cameras.cache_clear(); load_poses.cache_clear()


@lru_cache(maxsize=1)
def _cameras():
    manifest = json.loads(Path(_paths['cameras_json']).read_text())
    if manifest.get('schema_version') != 2:
        raise ValueError('expected a schema_version 2 (per-epoch) cameras.json')
    for k, epoch in enumerate(manifest['epochs']):
        if epoch['frame'] != k:
            raise ValueError(f'cameras.json epoch {k} is labelled frame {epoch["frame"]}')
    return manifest


_FIELDS = ('position_local_m', 'camera_to_local', 'distance_m', 'native_gsd_m',
           'reference_plane_across_gsd_m', 'reference_plane_along_gsd_m', 'off_nadir_deg',
           'position_ecef_m', 'position_eci_m', 'nadir_forward_local', 't_s')


def _stack(records, n_frames):
    out = {}
    for key in _FIELDS:
        out[key] = np.array([records[k][key] for k in range(n_frames)], dtype=float)
    out['resolution'] = tuple(records[0]['resolution'])
    out['focal_length_mm'] = float(records[0]['focal_length_mm'])
    out['sensor_width_mm'] = float(records[0]['sensor_width_mm'])
    return out


@lru_cache(maxsize=None)
def load_poses(satellite):
    """Pose arrays of one satellite for all frames, indexed by the frame number.

    Returns a dict with position_local_m [N,3], camera_to_local [N,3,3], distance_m [N],
    native_gsd_m [N], reference_plane_{across,along}_gsd_m [N], off_nadir_deg [N],
    position_ecef_m, position_eci_m, nadir_forward_local [N,3], t_s [N], and 'source'
    ('poses.jsonl' or 'cameras.json'). poses.jsonl lines are placed by their 'frame' field;
    a frame listed twice must carry identical poses, and every frame 0..N-1 must exist.
    """
    satellite = int(satellite)
    manifest = _cameras()
    n_frames = len(manifest['epochs'])
    path = Path(_paths['run_dir']) / 'renders' / f'satellite_{satellite:02d}' / 'poses.jsonl'
    if path.exists():
        records = {}
        for line in path.read_text().splitlines():
            if not line.strip(): continue
            rec = json.loads(line)
            if rec.get('crop') is not None: continue          # crop renders are not full frames
            if rec['satellite_id'] != satellite:
                raise ValueError(f'{path}: record of satellite {rec["satellite_id"]}')
            k = int(rec['frame'])
            if k in records:
                same = all(np.allclose(records[k][f], rec[f], rtol=0, atol=0) for f in
                           ('position_local_m', 'camera_to_local'))
                if not same: raise ValueError(f'{path}: frame {k} listed twice with different poses')
            records[k] = rec
        missing = sorted(set(range(n_frames)) - set(records))
        if missing: raise ValueError(f'{path}: {len(missing)} frames missing, first {missing[:5]}')
        out = _stack(records, n_frames); out['source'] = 'poses.jsonl'
    else:
        records = {}
        for epoch in manifest['epochs']:
            rec = next(c for c in epoch['cameras'] if c['satellite_id'] == satellite)
            records[epoch['frame']] = dict(rec, t_s=epoch['t_s'])
        out = _stack(records, n_frames); out['source'] = 'cameras.json'
    if out['resolution'] != (WIDTH, HEIGHT) or out['focal_length_mm'] != FOCAL_MM \
            or out['sensor_width_mm'] != SENSOR_WIDTH_MM:
        raise ValueError(f'satellite {satellite}: unexpected intrinsics')
    for value in out.values():
        if isinstance(value, np.ndarray): value.setflags(write=False)
    return out


def common_target_local_m():
    """[N,3] common target (chief boresight x WGS84, h = 0) per frame, from cameras.json."""
    return np.array([e['common_target_local_m'] for e in _cameras()['epochs']], dtype=float)


def _pose(sat, frame):
    poses = load_poses(sat)
    frame = np.asarray(frame, dtype=int)
    return poses['position_local_m'][frame], poses['camera_to_local'][frame]


def pixel_to_view(rows, cols):
    """PNG (row from top, col from left; integer = pixel centre) -> Blender view (u, v)."""
    rows, cols = np.asarray(rows, dtype=float), np.asarray(cols, dtype=float)
    return (cols + 0.5) / WIDTH, 1.0 - (rows + 0.5) / HEIGHT


def view_to_pixel(u, v):
    """Blender world_to_camera_view (u, v), origin bottom-left -> PNG (row, col)."""
    return (1.0 - np.asarray(v, dtype=float)) * HEIGHT - 0.5, np.asarray(u, dtype=float) * WIDTH - 0.5


def pixel_to_ray(sat, frame, rows, cols):
    """Unit ray directions [...,3] in local ENU through PNG pixels (rows, cols)."""
    frame, rows, cols = np.broadcast_arrays(np.asarray(frame, dtype=int), np.asarray(rows, dtype=float),
                                            np.asarray(cols, dtype=float))
    _, rot = _pose(sat, frame)
    u, v = pixel_to_view(rows, cols)
    d_cam = np.stack([(u - 0.5) * SENSOR_WIDTH_MM / FOCAL_MM, (v - 0.5) * SENSOR_HEIGHT_MM / FOCAL_MM,
                      -np.ones_like(u)], -1)
    d = np.einsum('...ij,...j->...i', rot, d_cam)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def ground_from_pixel(sat, frame, rows, cols, height_m=0.0):
    """Local ENU point [...,3] where the ray of PNG pixel (rows, cols) meets z = height_m."""
    frame, rows, cols, height_m = np.broadcast_arrays(np.asarray(frame, dtype=int), np.asarray(rows, dtype=float),
                                                      np.asarray(cols, dtype=float), np.asarray(height_m, dtype=float))
    pos, _ = _pose(sat, frame)
    d = pixel_to_ray(sat, frame, rows, cols)
    t = (height_m - pos[..., 2]) / d[..., 2]
    if np.any(t <= 0): raise ValueError('plane behind the camera')
    return pos + t[..., None] * d


def pixel_from_ground(sat, frame, xyz_enu):
    """PNG (rows, cols) of local ENU points xyz_enu [...,3] (inverse of ground_from_pixel)."""
    xyz = np.asarray(xyz_enu, dtype=float)
    frame = np.broadcast_to(np.asarray(frame, dtype=int), xyz.shape[:-1])
    pos, rot = _pose(sat, frame)
    p = np.einsum('...ji,...j->...i', rot, xyz - pos)      # R^T (X - C)
    depth = -p[..., 2]
    if np.any(depth <= 0): raise ValueError('point behind the camera')
    u = 0.5 + (FOCAL_MM / SENSOR_WIDTH_MM) * p[..., 0] / depth
    v = 0.5 + (FOCAL_MM / SENSOR_HEIGHT_MM) * p[..., 1] / depth
    return view_to_pixel(u, v)


def chief_to_satellite_pixel(sat, frame, rows, cols, height_m=0.0):
    """Pixel (rows, cols) of satellite sat seeing the ground point (at z = height_m) seen
    by chief pixel (rows, cols) in the same frame."""
    return pixel_from_ground(sat, frame, ground_from_pixel(0, frame, rows, cols, height_m))


def ground_pixel_size(sat, frame, height_m=0.0, rows=IMAGE_CENTRE[0], cols=IMAGE_CENTRE[1]):
    """Ground size (m/px) of one pixel step across (columns) and along (rows) on z = height_m
    at PNG (rows, cols), by central differences of ground_from_pixel."""
    g = lambda dr, dc: ground_from_pixel(sat, frame, np.asarray(rows) + dr, np.asarray(cols) + dc, height_m)
    across = np.linalg.norm(g(0, .5) - g(0, -.5), axis=-1)
    along = np.linalg.norm(g(.5, 0) - g(-.5, 0), axis=-1)
    return across, along
