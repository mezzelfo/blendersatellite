"""Composite terrain datasets: adjacent datasets merged into one terrain.

A composite entry of terrain_datasets.json lists its members in priority order:

  "helsinki_espoo_2017": {"members": ["helsinki_2017", "espoo_2017"], ...}

The members keep their own caches (data/terrain/<member>/<spacing>m). In a render cell where
several members have mesh data the first member in the list wins; terrain_to_render.py removes
the other members' faces there (see composite_keep). The members after the first are corrected
towards the first (the reference) with a model fitted on the cells they share with it:

  height  dh = reference - member on flat cells (3 x 3 height std below flat_std m in both), median
          per block of block_m metres (blocks with at least min_cells such cells). The correction
          field is the linear interpolation of the block medians (nearest block outside their
          convex hull), in full up to one block from a measured block centre and tapered linearly
          to zero over taper_m beyond, so the member keeps its own heights far from the shared area.
          The measured offsets are local (they change sign within a few km), so a single constant
          would not do.
  colour  per channel, moments matched over the shared cells: c' = gain * c + offset with
          gain = std_reference / std_member, offset = mean_reference - gain * mean_member, applied to
          the whole member (it comes from a different flight and camera).

The model is written to data/terrain/<composite>/<spacing>m/correction.json and read by
terrain_to_render.py, which applies it to the member's render grids and facade voxels.

  python pipeline/terrain_merge.py --dataset helsinki_espoo_2017 --spacing 1 --fetch --workers 4
  python pipeline/terrain_merge.py --dataset helsinki_espoo_2017 --spacing 1 --ensure   (run_swarm.sh)

--fetch first caches, with terrain_cache.py, the members' tiles of the shared area. --ensure does
nothing for a dataset that is not composite or whose correction.json exists, else --fetch + fit.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.interpolate import LinearNDInterpolator
from scipy.ndimage import uniform_filter
from scipy.spatial import cKDTree
from scipy.spatial import QhullError

from terrain_cache import REPO, load_datasets, tile_index

HERE = Path(__file__).resolve().parent
DEFAULTS = dict(block_m=250., min_cells=2000, flat_std=0.15, taper_m=2000.)


def composite_members(datasets, name):
    """Member names of a composite dataset in priority order, or None for an ordinary dataset."""
    members = datasets[name].get('members')
    if members is None:
        return None
    epsg = {(datasets[m]['projected_epsg'], datasets[m]['geographic_epsg']) for m in members}
    if len(epsg) != 1:
        raise ValueError(f'{name}: the members must share one projected and one geographic CRS, not {epsg}')
    return list(members)


def member_cache_dirs(cache_dir, members):
    """{member: cache folder}: siblings of the composite cache folder, same <spacing>m name."""
    cache_dir = Path(cache_dir)
    return {m: cache_dir.parent.parent / m / cache_dir.name for m in members}


def shared_tile_pairs(datasets, reference, member):
    """(reference tile, member tile) pairs whose index boxes overlap with a positive area."""
    a = {t: (*r.min(0), *r.max(0)) for t, r in tile_index(datasets[reference]).items()}
    b = {t: (*r.min(0), *r.max(0)) for t, r in tile_index(datasets[member]).items()}
    return sorted((ta, tb) for ta, ba in a.items() for tb, bb in b.items()
                  if min(ba[2], bb[2]) > max(ba[0], bb[0]) and min(ba[3], bb[3]) > max(ba[1], bb[1]))


def roughness(height):
    """3 x 3 standard deviation of the height (empty cells count as missing: NaN where any is)."""
    valid = np.isfinite(height)
    z = np.where(valid, height, 0.)
    count = uniform_filter(valid.astype(np.float64), 3) * 9
    mean = uniform_filter(z, 3) * 9 / np.maximum(count, 1)
    var = uniform_filter(z * z, 3) * 9 / np.maximum(count, 1) - mean ** 2
    return np.where(count > 8.5, np.sqrt(np.maximum(var, 0.)), np.nan)


def fit_member(cache_dirs, datasets, reference, member, spacing, block_m, min_cells, flat_std):
    """Height blocks and colour moments of member against reference on their shared cells."""
    @lru_cache(maxsize=8)
    def load(name, tile):
        path = cache_dirs[name] / f'{tile}.npz'
        if not path.exists():
            return None
        with np.load(path) as data:
            h = data['height'].astype(np.float64)
            if abs(float(data['spacing']) - spacing) > 1e-9:
                raise ValueError(f'{path}: spacing {float(data["spacing"])} differs from {spacing}')
            i0, j0 = int(round(float(data['x0']) / spacing)), int(round(float(data['y0']) / spacing))
            return dict(h=h, rgb=data['rgb'].astype(np.float64), i0=i0, j0=j0, flat=roughness(h) < flat_std)

    pairs = shared_tile_pairs(datasets, reference, member)
    dh_blocks, n_pairs, missing = {}, 0, set()
    moments = np.zeros((2, 3, 3))  # [reference, member] x channel x (n, sum, sum of squares)
    for ta, tb in pairs:
        a, b = load(reference, ta), load(member, tb)
        if a is None or b is None:
            missing.update(([f'{reference}/{ta}'] if a is None else []) + ([f'{member}/{tb}'] if b is None else []))
            continue
        c0, c1 = max(a['i0'], b['i0']), min(a['i0'] + a['h'].shape[1], b['i0'] + b['h'].shape[1])
        r0, r1 = max(a['j0'], b['j0']), min(a['j0'] + a['h'].shape[0], b['j0'] + b['h'].shape[0])
        if c1 <= c0 or r1 <= r0:
            continue
        sa = (slice(r0 - a['j0'], r1 - a['j0']), slice(c0 - a['i0'], c1 - a['i0']))
        sb = (slice(r0 - b['j0'], r1 - b['j0']), slice(c0 - b['i0'], c1 - b['i0']))
        both = np.isfinite(a['h'][sa]) & np.isfinite(b['h'][sb])
        if not both.any():
            continue
        n_pairs += 1
        for k, (g, s) in enumerate(((a, sa), (b, sb))):
            values = g['rgb'][s][both]
            moments[k, :, 0] += len(values)
            moments[k, :, 1] += values.sum(0)
            moments[k, :, 2] += (values ** 2).sum(0)
        flat = both & a['flat'][sa] & b['flat'][sb]
        rows, cols = np.nonzero(flat)
        x = (c0 + cols + .5) * spacing
        y = (r0 + rows + .5) * spacing
        dh = a['h'][sa][flat] - b['h'][sb][flat]
        keys = np.floor(x / block_m).astype(np.int64) * 10_000_000 + np.floor(y / block_m).astype(np.int64)
        order = np.argsort(keys, kind='stable')
        keys, dh = keys[order], dh[order]
        cuts = np.flatnonzero(np.diff(keys)) + 1
        for key, part in zip(keys[np.r_[0, cuts]], np.split(dh, cuts)):
            dh_blocks.setdefault(int(key), []).append(part)
    blocks = []
    for key, parts in sorted(dh_blocks.items()):
        values = np.concatenate(parts)
        if len(values) >= min_cells:
            bx, by = divmod(key, 10_000_000)
            blocks.append([(bx + .5) * block_m, (by + .5) * block_m, float(np.median(values)), int(len(values))])
    n, total, squares = moments[..., 0], moments[..., 1], moments[..., 2]
    with np.errstate(invalid='ignore', divide='ignore'):
        mean = total / n
        std = np.sqrt(np.maximum(squares / n - mean ** 2, 0.))
        gain = std[0] / std[1]
    colour = None
    if n[0, 0] > 0:
        colour = dict(gain=gain.tolist(), offset=(mean[0] - gain * mean[1]).tolist(),
                      mean_reference=mean[0].tolist(), mean_member=mean[1].tolist(),
                      std_reference=std[0].tolist(), std_member=std[1].tolist(), cells=int(n[0, 0]))
    return dict(height=dict(block_m=block_m, blocks=blocks), colour=colour, pairs=len(pairs),
                pairs_used=n_pairs, missing_tiles=sorted(missing))


def fit(cache_dir, datasets, name, spacing, block_m=DEFAULTS['block_m'], min_cells=DEFAULTS['min_cells'],
        flat_std=DEFAULTS['flat_std'], taper_m=DEFAULTS['taper_m']):
    """Correction model of a composite dataset from the cached tiles of its members."""
    members = composite_members(datasets, name)
    if not members:
        raise ValueError(f'{name} is not a composite dataset (no "members")')
    dirs = member_cache_dirs(cache_dir, members)
    model = dict(dataset=name, reference=members[0], spacing=spacing, flat_std=flat_std, min_cells=min_cells,
                 taper_m=taper_m, members={})
    for member in members[1:]:
        model['members'][member] = fit_member(dirs, datasets, members[0], member, spacing, block_m, min_cells, flat_std)
        model['members'][member]['height']['taper_m'] = taper_m
    return model


def model_digest(model):
    """Short digest of a correction model (recorded by terrain_to_render.py in cache_frame.json)."""
    return hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:16]


class HeightField:
    """Height correction dz(x, y) of one member (metres to add to its heights)."""

    def __init__(self, height):
        blocks = np.asarray(height.get('blocks') or np.zeros((0, 4)), dtype=np.float64).reshape(-1, 4)
        self.block_m, self.taper_m = float(height['block_m']), float(height.get('taper_m', DEFAULTS['taper_m']))
        self.points, self.values = blocks[:, :2], blocks[:, 2]
        self.tree = cKDTree(self.points) if len(self.points) else None
        self.linear = None
        if len(self.points) >= 3:
            try:
                self.linear = LinearNDInterpolator(self.points, self.values)
            except QhullError:  # collinear blocks: nearest block only
                self.linear = None

    def __call__(self, x, y):
        x, y = np.broadcast_arrays(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
        if self.tree is None:
            return np.zeros(x.shape)
        xy = np.c_[x.ravel(), y.ravel()]
        distance, nearest = self.tree.query(xy)
        value = self.values[nearest]
        if self.linear is not None:
            inside = self.linear(xy)
            value = np.where(np.isfinite(inside), inside, value)
        weight = np.clip(1. - (distance - self.block_m) / self.taper_m, 0., 1.)
        return (weight * value).reshape(x.shape)


class MemberCorrection:
    """Corrections of one member: heights by HeightField, colours by gain and offset (sRGB DN)."""

    def __init__(self, entry):
        self.field = HeightField(entry['height'])
        colour = entry.get('colour')
        self.gain = np.asarray(colour['gain']) if colour else np.ones(3)
        self.offset = np.asarray(colour['offset']) if colour else np.zeros(3)

    def colour(self, rgb):
        return np.rint(np.clip(np.asarray(rgb, dtype=np.float64) * self.gain + self.offset, 0, 255)).astype(np.uint8)

    def grid(self, height, rgb, x, y):
        """Corrected (height, rgb) of a grid with cell centres x (cols,), y (rows,)."""
        dz = self.field(x[None, :], y[:, None])
        return height + dz, self.colour(rgb)

    def voxels(self, voxels):
        """Facade voxels moved by the height correction at their centre (rounded to whole voxels)."""
        size = float(voxels['voxel'])
        ijk = voxels['ijk'].copy()
        centre = (ijk[:, :2] + .5) * size
        ijk[:, 2] += np.rint(self.field(centre[:, 0], centre[:, 1]) / size).astype(ijk.dtype)
        return dict(voxels, ijk=ijk, rgb=self.colour(voxels['rgb']))


def load_corrections(cache_dir, datasets, name):
    """(model, {member: MemberCorrection}) of a composite dataset from <cache_dir>/correction.json."""
    path = Path(cache_dir) / 'correction.json'
    if not path.exists():
        raise SystemExit(f'{path} is missing: run pipeline/terrain_merge.py --dataset {name} first')
    model = json.loads(path.read_text())
    return model, {m: MemberCorrection(e) for m, e in model['members'].items()}


def fetch(datasets, name, spacing, workers, archives=None, datasets_path=None):
    """Cache, with terrain_cache.py, every member tile of the areas shared with the reference."""
    members = composite_members(datasets, name)
    need = {m: set() for m in members}
    for member in members[1:]:
        for ta, tb in shared_tile_pairs(datasets, members[0], member):
            need[members[0]].add(ta)
            need[member].add(tb)
    for member, tiles in need.items():
        if not tiles:
            continue
        cmd = [sys.executable, str(HERE / 'terrain_cache.py'), '--dataset', member, '--tiles', *sorted(tiles),
               '--spacing', f'{spacing:g}', '--workers', str(workers)]
        if archives:
            cmd += ['--archives', str(archives)]
        if datasets_path:
            cmd += ['--datasets', str(datasets_path)]
        print(f'{member}: caching {len(tiles)} tiles of the shared area', flush=True)
        subprocess.run(cmd, check=False)  # failed tiles are reported by terrain_cache.py and left out


def summary(model):
    lines = []
    for member, entry in model['members'].items():
        blocks = np.asarray(entry['height']['blocks']).reshape(-1, 4)
        h = (f'{len(blocks)} blocks, dh median {np.median(blocks[:, 2]):+.2f} m, range {blocks[:, 2].min():+.2f}..'
             f'{blocks[:, 2].max():+.2f} m' if len(blocks) else 'no height blocks')
        c = entry['colour']
        col = ('colour gain ' + '/'.join(f'{g:.3f}' for g in c['gain']) + ', offset '
               + '/'.join(f'{o:+.1f}' for o in c['offset']) + f' ({c["cells"]:,} cells)') if c else 'no colour model'
        lines.append(f'{member} -> {model["reference"]}: {entry["pairs_used"]}/{entry["pairs"]} tile pairs; {h}; {col}'
                     + (f'; {len(entry["missing_tiles"])} tiles not cached' if entry['missing_tiles'] else ''))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', required=True, help='composite key in terrain_datasets.json')
    parser.add_argument('--spacing', type=float, default=1.0, help='cache spacing in metres (as terrain_cache.py)')
    parser.add_argument('--output', type=Path, default=REPO / 'data' / 'terrain',
                        help='terrain root: member caches in <output>/<member>/<spacing>m')
    parser.add_argument('--datasets', type=Path, help='terrain_datasets.json to use (default: the repository one)')
    parser.add_argument('--fetch', action='store_true', help='cache the shared-area tiles first (terrain_cache.py)')
    parser.add_argument('--ensure', action='store_true',
                        help='only if composite and correction.json is missing: fetch, fit and write it')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--archives', type=Path, help='passed to terrain_cache.py with --fetch')
    for key, value in DEFAULTS.items():
        parser.add_argument('--' + key.replace('_', '-'), type=type(value), default=value)
    args = parser.parse_args()
    datasets = load_datasets(args.datasets) if args.datasets else load_datasets()
    cache_dir = args.output / args.dataset / f'{args.spacing:g}m'
    if args.ensure:
        if composite_members(datasets, args.dataset) is None or (cache_dir / 'correction.json').exists():
            return
        args.fetch = True
    if args.fetch:
        fetch(datasets, args.dataset, args.spacing, args.workers, args.archives, args.datasets)
    model = fit(cache_dir, datasets, args.dataset, args.spacing, args.block_m, args.min_cells, args.flat_std,
                args.taper_m)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / 'correction.json').write_text(json.dumps(model, indent=1) + '\n')
    print(summary(model))
    print(f'-> {cache_dir / "correction.json"} (digest {model_digest(model)})')


if __name__ == '__main__':
    main()
