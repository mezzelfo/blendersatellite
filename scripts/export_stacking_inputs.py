"""Write stacking_inputs.npz in the run folder (step 9 B2). Read-only on every input.

Arrays for all 7 satellites x 1000 frames (axis 0 = satellite id, axis 1 = frame):
  position_local_m [7,N,3], camera_to_local [7,N,3,3], distance_m [7,N], native_gsd_m [7,N]
  ground_pixel_across_m, ground_pixel_along_m [7,N]: ground size of one pixel step along
      image columns / rows at the image centre on z = 0 (central differences of
      stacking_geometry.ground_from_pixel)
  centre_ground_local_m [7,N,3]: z = 0 ground point of the image centre (optical axis)
  centre_step_m [7,N-1]: |centre_ground[k+1] - centre_ground[k]|
  centre_step_along_m, centre_step_across_m [7,N-1]: that step projected on the unit ground
      directions of image-up (decreasing row) and image-right (increasing column) of frame k
  image_motion_rows_px, image_motion_cols_px [7,N-1]: PNG position in camera k+1 of the
      frame-k centre ground point minus IMAGE_CENTRE, i.e. the apparent motion of the ground
      in the satellite's own pixels (rows +down, cols +right)
  image_motion_ratio_to_chief [7,N-1]: image_motion_rows_px / image_motion_rows_px[chief]
  alpha_satellites [5]; alpha_bounds [5,N,4] = (row_min, row_max, col_min, col_max) of the
      pixels with alpha > 0 (inclusive, PNG indices); alpha_row_any [5,N,H], alpha_col_any
      [5,N,W] (bool); alpha_partial_pixels, alpha_opaque_pixels [5,N] (0 < alpha < 255, = 255)
  t_s [N], common_target_local_m [N,3], pose_source [7], plus the intrinsics and conventions.

Run in the conda environment "orbits" (NumPy + Pillow):
    python scripts/export_stacking_inputs.py [--workers 20]
"""
import argparse
import json
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image

import stacking_geometry as g

RENDERED = (0, 1, 2, 3, 4)


def alpha_stats(path):
    alpha = np.asarray(Image.open(path).getchannel('A'))
    rows, cols = alpha.any(1), alpha.any(0)
    r, c = np.flatnonzero(rows), np.flatnonzero(cols)
    bounds = (r[0], r[-1], c[0], c[-1]) if r.size else (-1, -1, -1, -1)
    return rows, cols, bounds, int(np.count_nonzero((alpha > 0) & (alpha < 255))), int(np.count_nonzero(alpha == 255))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=g.RUN_DIR)
    parser.add_argument('--cameras', type=Path, default=g.CAMERAS_JSON)
    parser.add_argument('--workers', type=int, default=20)
    parser.add_argument('--output', type=Path, help='default <run>/stacking_inputs.npz')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    g.set_paths(args.run, args.cameras)
    start = time.monotonic()
    S, N = g.N_SATELLITES, g.N_FRAMES
    poses = [g.load_poses(s) for s in range(S)]
    frames = np.arange(N)
    out = dict(
        t_s=poses[0]['t_s'], common_target_local_m=g.common_target_local_m(),
        position_local_m=np.stack([p['position_local_m'] for p in poses]),
        camera_to_local=np.stack([p['camera_to_local'] for p in poses]),
        distance_m=np.stack([p['distance_m'] for p in poses]),
        native_gsd_m=np.stack([p['native_gsd_m'] for p in poses]),
        pose_source=np.array([p['source'] for p in poses]))
    across, along, centre = [], [], []
    for s in range(S):
        a, b = g.ground_pixel_size(s, frames)
        across.append(a); along.append(b)
        centre.append(g.ground_from_pixel(s, frames, *g.IMAGE_CENTRE))
    out['ground_pixel_across_m'], out['ground_pixel_along_m'] = np.stack(across), np.stack(along)
    out['centre_ground_local_m'] = centre = np.stack(centre)
    step = centre[:, 1:] - centre[:, :-1]
    out['centre_step_m'] = np.linalg.norm(step, axis=-1)
    up_dir, right_dir = [], []
    for s in range(S):
        r0, c0 = g.IMAGE_CENTRE
        up = g.ground_from_pixel(s, frames[:-1], r0 - .5, c0) - g.ground_from_pixel(s, frames[:-1], r0 + .5, c0)
        right = g.ground_from_pixel(s, frames[:-1], r0, c0 + .5) - g.ground_from_pixel(s, frames[:-1], r0, c0 - .5)
        up_dir.append(up / np.linalg.norm(up, axis=-1, keepdims=True))
        right_dir.append(right / np.linalg.norm(right, axis=-1, keepdims=True))
    out['centre_step_along_m'] = np.einsum('skx,skx->sk', step, np.stack(up_dir))
    out['centre_step_across_m'] = np.einsum('skx,skx->sk', step, np.stack(right_dir))
    motion = [g.pixel_from_ground(s, frames[1:], centre[s, :-1]) for s in range(S)]
    out['image_motion_rows_px'] = np.stack([m[0] for m in motion]) - g.IMAGE_CENTRE[0]
    out['image_motion_cols_px'] = np.stack([m[1] for m in motion]) - g.IMAGE_CENTRE[1]
    out['image_motion_ratio_to_chief'] = out['image_motion_rows_px'] / out['image_motion_rows_px'][0]
    geometry_seconds = time.monotonic() - start

    paths = [args.run / 'renders' / f'satellite_{s:02d}' / f'frame_{k:04d}.png' for s in RENDERED for k in frames]
    with Pool(args.workers) as pool:
        stats = pool.map(alpha_stats, paths, chunksize=4)
    n = len(RENDERED)
    out['alpha_satellites'] = np.array(RENDERED)
    out['alpha_row_any'] = np.array([x[0] for x in stats]).reshape(n, N, g.HEIGHT)
    out['alpha_col_any'] = np.array([x[1] for x in stats]).reshape(n, N, g.WIDTH)
    out['alpha_bounds'] = np.array([x[2] for x in stats], dtype=np.int32).reshape(n, N, 4)
    out['alpha_partial_pixels'] = np.array([x[3] for x in stats]).reshape(n, N)
    out['alpha_opaque_pixels'] = np.array([x[4] for x in stats]).reshape(n, N)

    out.update(width=g.WIDTH, height=g.HEIGHT, focal_length_mm=g.FOCAL_MM, sensor_width_mm=g.SENSOR_WIDTH_MM,
               sensor_height_mm=g.SENSOR_HEIGHT_MM, image_centre_row_col=np.array(g.IMAGE_CENTRE),
               conventions=json.dumps(dict(
                   local='ENU at the Melbourne origin (cameras.json origin_ecef_m, local_from_ecef), metres',
                   camera_to_local='columns (right, up, backward); boresight = -column 2',
                   png='row from top, col from left; integer index = pixel centre',
                   view_from_png='u = (col + 0.5)/W, v = 1 - (row + 0.5)/H (Blender world_to_camera_view, origin bottom-left)',
                   image_motion='rows +down, cols +right; ground features move to increasing row')),
               sources=json.dumps(dict(run=str(args.run.resolve()), cameras=str(args.cameras.resolve()))))
    target = args.output or args.run / 'stacking_inputs.npz'
    if target.exists() and not args.overwrite: raise SystemExit(f'{target} exists; refusing to overwrite')
    np.savez_compressed(target, **out)
    print(f'wrote {target} ({target.stat().st_size/1e6:.1f} MB); geometry {geometry_seconds:.1f} s, '
          f'total {time.monotonic()-start:.1f} s')


if __name__ == '__main__':
    main()
