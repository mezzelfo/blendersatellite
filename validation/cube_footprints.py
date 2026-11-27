"""Ground footprint of the cubes of a run, to compare where every satellite's cube lies on the ground.

For every satellite the outline, the four corners and the centre of its cube (reference band,
default the central one) are mapped to the ground with the model geometry of
analyze_cube_run.cube_ground (poses and the cube's own frame index) on a plane at --height metres
in the run's local frame, and converted to the dataset's projected CRS. Only the frame index of the
cube file is read, not the image data.

Printed and written to <out>/footprints.csv, per satellite: role, cube size, centre (projected
CRS), offset of the centre from the dataset target and from the chief's centre, length along the
cube lines (first to last line, through the centre) and width across (first to last column), and
the azimuth of the line axis. <out>/footprints.png draws the outlines (km from the target), coloured
by role, with the target, the centres and an arrow from the first to the last line; with
--background the terrain caches are drawn underneath at 25 m.

Run on osiride in the conda environment orbits, from the repository root, e.g.
    python validation/cube_footprints.py --run $RUN --mode bilinear --background
Default output folder <run>/analysis.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / 'pipeline'))
sys.path.insert(0, str(HERE))
import stacking_geometry as sg  # noqa: E402
from analyze_cube_run import Frame, azimuth_deg, cube_ground  # noqa: E402

ROLE_COLOURS = {'nadir': '#1f77b4', 'fore': '#d62728', 'aft': '#2ca02c'}


def perimeter(G, C, step=64):
    """(lines, cols) indices around a G x C grid, closed: first line, last column, last line
    backwards, first column backwards."""
    li = np.unique(np.r_[np.arange(0, G, step), G - 1])
    co = np.unique(np.r_[np.arange(0, C, step), C - 1])
    lines = np.r_[np.zeros(len(co), int), li, np.full(len(co), G - 1), li[::-1], 0]
    cols = np.r_[co, np.full(len(li), C - 1), co[::-1], np.zeros(len(li), int), 0]
    return lines, cols


def load_geometry(run, mode, sat):
    """The geometry keys of a cube file (no image data): g, cols, band_rows, frame_index."""
    path = Path(run) / 'cubes' / f'raw_bands_{mode}' / f'cube_sat{sat:02d}.npz'
    if not path.exists():
        return None
    with np.load(path) as d:
        return {k: d[k] for k in ('g', 'cols', 'band_rows', 'frame_index')}


def footprint(sat, mode, cube, band, height, frame):
    """Outline (projected x, y), corners, centre and extents of one cube."""
    G, C = len(cube['g']), len(cube['cols'])
    ground = lambda i, j: cube_ground(sat, mode, cube, np.asarray(i), np.asarray(j), band, height)  # noqa: E731
    proj = lambda p: np.c_[frame.local_to_proj(p)[:2]]  # noqa: E731
    li, co = perimeter(G, C)
    outline = proj(ground(li, co))
    corners = proj(ground([0, 0, G - 1, G - 1], [0, C - 1, C - 1, 0]))
    mid = ground([G // 2, 0, G - 1, G // 2, G // 2], [C // 2, C // 2, C // 2, 0, C - 1])
    centre = proj(mid[:1])[0]
    along = float(np.linalg.norm(mid[2, :2] - mid[1, :2]))
    across = float(np.linalg.norm(mid[4, :2] - mid[3, :2]))
    first_last = proj(mid[1:3])
    return dict(G=G, C=C, outline=outline, corners=corners, centre=centre, along_m=along, across_m=across,
                first_last=first_last, azimuth_line_deg=azimuth_deg(mid[2, :2] - mid[1, :2]))


def background(ax, cache_dirs, target, step=25):
    """Draw the terrain caches at about step metres per pixel (km from the target)."""
    for d in cache_dirs:
        for path in sorted(Path(d).glob('*.npz')):
            with np.load(path) as data:
                h, rgb = data['height'], data['rgb']
                x0, y0, s = float(data['x0']), float(data['y0']), float(data['spacing'])
            k = max(1, int(round(step / s)))
            img = rgb[::k, ::k].astype(float) / 255
            img = np.dstack([img, np.isfinite(h[::k, ::k]).astype(float)])
            rows, cols = h.shape
            ext = [(x0 - target[0]) / 1e3, (x0 + cols * s - target[0]) / 1e3,
                   (y0 - target[1]) / 1e3, (y0 + rows * s - target[1]) / 1e3]
            ax.imshow(img, origin='lower', extent=ext, zorder=0, interpolation='nearest')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--mode', default='bilinear', choices=('none', 'linear', 'bilinear'))
    ap.add_argument('--satellites', type=int, nargs='+', default=list(range(7)))
    ap.add_argument('--band', type=int, help='band (default: the central one)')
    ap.add_argument('--height', type=float, default=30., help='ground plane height (m) in the local frame')
    ap.add_argument('--background', action='store_true', help='draw the terrain caches underneath')
    ap.add_argument('--terrain-root', type=Path, default=REPO / 'data' / 'terrain')
    ap.add_argument('--out', type=Path, help='default <run>/analysis')
    args = ap.parse_args()

    run = args.run
    out = args.out or run / 'analysis'
    out.mkdir(parents=True, exist_ok=True)
    cameras = run / 'simulation' / 'cameras.json'
    sg.set_paths(run_dir=run, cameras_json=cameras)
    manifest = json.loads(cameras.read_text())
    import terrain_cache as tc
    from terrain_merge import composite_members
    datasets = tc.load_datasets()
    name = json.loads((run / 'terrain_render' / 'tiles.json').read_text())['dataset']
    members = composite_members(datasets, name) or [name]
    frame = Frame(manifest, datasets[members[0]])
    target = np.asarray(datasets[name]['target']['projected_xy'], float)
    roles = {0: 'nadir', **{int(k): v for k, v in ((manifest.get('acquisition') or {}).get('roles') or {}).items()}}

    feet, rows = {}, []
    for sat in args.satellites:
        cube = load_geometry(run, args.mode, sat)
        if cube is None:
            print(f'sat {sat}: no {args.mode} cube, skipped')
            continue
        band = len(cube['band_rows']) // 2 if args.band is None else args.band
        f = footprint(sat, args.mode, cube, band, args.height, frame)
        feet[sat] = f
        chief = feet.get(0)
        d_t = f['centre'] - target
        d_c = f['centre'] - chief['centre'] if chief else np.full(2, np.nan)
        rows.append(dict(sat=sat, role=roles.get(sat, '?'), lines=f['G'], columns=f['C'], band=band,
                         centre_x=f['centre'][0], centre_y=f['centre'][1],
                         offset_target_e_m=d_t[0], offset_target_n_m=d_t[1],
                         offset_target_m=float(np.hypot(*d_t)),
                         offset_chief_e_m=d_c[0], offset_chief_n_m=d_c[1], offset_chief_m=float(np.hypot(*d_c)),
                         along_km=f['along_m'] / 1e3, across_km=f['across_m'] / 1e3,
                         azimuth_line_deg=f['azimuth_line_deg'],
                         **{f'corner{k}_{a}': f['corners'][k, i] for k in range(4) for i, a in enumerate('xy')}))
    print(f'{name}, mode {args.mode}, plane at {args.height:g} m; target {target[0]:.0f}, {target[1]:.0f}')
    print('sat role   lines x cols   centre - target (E, N) m   centre - chief m   along x across km   line azimuth')
    for r in rows:
        print(f"{r['sat']:3d} {r['role']:6s} {r['lines']:5d} x {r['columns']:4d}   "
              f"{r['offset_target_e_m']:+9.0f} {r['offset_target_n_m']:+9.0f}   {r['offset_chief_m']:12.0f}   "
              f"{r['along_km']:6.2f} x {r['across_km']:6.2f}   {r['azimuth_line_deg']:8.2f}")
    with open(out / f'footprints_{args.mode}.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in rows)

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('matplotlib missing: no figure')
        return
    fig, ax = plt.subplots(figsize=(10, 10))
    if args.background:
        background(ax, [args.terrain_root / m / '1m' for m in members], target)
    for sat, f in feet.items():
        colour = ROLE_COLOURS.get(roles.get(sat), 'k')
        o = (f['outline'] - target) / 1e3
        ax.plot(o[:, 0], o[:, 1], '-', color=colour, lw=1.2, label=f'sat {sat} ({roles.get(sat, "?")})')
        c = (f['centre'] - target) / 1e3
        ax.plot(*c, 'o', color=colour, ms=4)
        a, b = (f['first_last'] - target) / 1e3
        ax.annotate('', xy=b, xytext=a, arrowprops=dict(arrowstyle='->', color=colour, lw=.8))
    ax.plot(0, 0, '*', color='k', ms=14, label='target')
    ax.set_aspect('equal')
    ax.set_xlabel('East from target (km)')
    ax.set_ylabel('North from target (km)')
    ax.set_title(f'{run.name}: cube footprints, {args.mode}, band {band}, plane {args.height:g} m\n'
                 'arrow: first to last cube line')
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, loc='upper left')
    fig.tight_layout()
    fig.savefig(out / f'footprints_{args.mode}.png', dpi=130)
    print('->', out / f'footprints_{args.mode}.csv', out / f'footprints_{args.mode}.png')


if __name__ == '__main__':
    main()
