"""Convert native-CRS terrain caches (terrain_cache.py) into render_swarm.py inputs.

For every tile in cameras.json 'selected_tiles' the native grid is binned to the
render spacing (area mean of the cache cells overlapping each render cell,
weighted by overlap area and coverage), empty cells are filled from the nearest
filled cell, and the cell centres are transformed into the simulation's local
ENU frame with the same chain as prepare_swarm_terrain.py:
projected CRS -> geographic CRS -> ECEF (height used as ellipsoidal) -> local.

The output files have the keys render_swarm.py expects (vertices, rgb, shape,
sampled_fraction) plus the cache_frame.json guard, so a run can switch to them
with --terrain <output> and no change to the renderer.

  python pipeline/terrain_to_render.py --cameras .../simulation/cameras.json \\
      --cache data/terrain/melbourne_2020/1m --dataset melbourne_2020
  (output by default in <run>/terrain_render)

--walls (needs the facade voxels, which terrain_cache.py stores unless --no-facades) adds
vertical walls: on the render grid, every shared edge between 4-adjacent cells whose heights
differ by more than --wall-threshold (2 m) gets a wall from the lower to the upper height,
split into segments of height <= the render spacing and coloured from the facade voxels (see build_walls and colour_segments). The
neighbouring tiles' grids and voxels are loaded as a one-cell halo, and a seam edge belongs to
the tile holding its west or south cell. Extra keys in the render .npz (grid keys unchanged):
  wall_vertices  float32 (4 S, 3), local frame, 4 corners per segment
                 (bottom a, bottom b, top b, top a; render_swarm.py adds two triangles each)
  wall_rgb       uint8 (4 S, 3), sRGB, the segment colour repeated on its 4 corners
  wall_segments, wall_edges, wall_sources, wall_source_counts (segments coloured from voxels,
                 from the nearest voxel, from the lower cell)
Renders with and without walls must not be mixed: cache_frame.json records the wall settings,
so --walls refuses an output folder made without them and the reverse.

A composite dataset (terrain_merge.py) is given with --cache <root>/<composite>/<spacing>m, the
folder of its correction.json; the members' caches are its siblings <root>/<member>/<spacing>m.
Every member's tiles of the run are converted, as <member>__<tile>.npz. The members after the
first get the height and colour correction of correction.json (render grids and facade voxels).
In a render cell where several members have data the first member in priority order wins: the
other members' faces there are dropped through the key face_keep (rows - 1, cols - 1) bool, true
for the grid quads to draw (see composite_keep), and the walls follow the same ownership.

tiles.json in the output folder lists the converted tiles (render_swarm.py loads those).

--texture-spacing T moves the terrain colour from the vertices to a texture: every tile gets an
RGB image <tile>_texture.png of the cache colours binned to T metres (the cache spacing, 1 m, by
default in run_swarm.sh), over the extent of its render grid, empty texels filled from the
nearest covered one; the vertices carry texture coordinates instead of colours. With the colour
in the texture the geometry can be simplified without losing colour detail: --decimate R (needs
--texture-spacing) reduces every tile's terrain to the fraction R of its triangles by quadric
error decimation (pyfqmr), with the open borders kept (tile border, cells of another member), so
neighbouring tiles still share their border vertices. --simplify-tolerance E is the earlier,
weaker method: aligned square blocks of 2 to 128 grid quads within E of a plane (simplify_grid);
on the Helsinki and Espoo meshes it kept 57 to 74 % of the triangles for E = 1.5 to 0.25 m.
The textured output has the keys
  vertices   float32 (V, 3), local frame
  uv         float32 (V, 2), texture coordinates (0, 0) = south-west corner of the texture
  triangles  int32 (T, 3), counter-clockwise seen from above
  texture    file name of the texture image (beside the .npz), texture_spacing, shape (rows, cols
             of the render grid), sampled_fraction, and the wall keys above (walls keep their
             vertex colours). face_keep is applied before the simplification and not written.
cache_frame.json records the texture and simplification settings, so the two formats never mix.
"""
import argparse
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt

from terrain_cache import load_datasets, run_tiles
from terrain_merge import composite_members, load_corrections, member_cache_dirs, model_digest


def _axis_overlap(start, spacing, count, target):
    """Split cells [start + i*spacing, +spacing) between the target bins they overlap.

    Returns the grid origin (multiple of target), and for every source cell the lower bin
    index and the fraction of the cell inside it (the rest goes to the next bin).
    Requires spacing <= target, so a cell overlaps at most two bins.
    """
    origin = np.floor(start / target) * target
    left = (start - origin) / target + np.arange(count) * (spacing / target)  # in bins
    low = np.floor(left + 1e-9).astype(np.int64)
    fraction = np.clip((low + 1 - left) * (target / spacing), 0., 1.)
    fraction[fraction > 1 - 1e-9] = 1.  # cell entirely inside one bin
    return origin, low, fraction


def bin_to_spacing(height, rgb, coverage, x0, y0, spacing, target, return_weight=False):
    """Area-mean of a native grid into a grid of cell size target aligned to multiples of target.

    Each native cell contributes to every target cell it overlaps, weighted by the
    overlap area times its coverage, so the binned value is centred on the target
    cell even when target / spacing is not an integer (e.g. 1 m -> 2.5 m).
    With return_weight, also returns the summed weight per target cell (covered native
    area in sub-cell units), used to decide which tile owns a cell shared by two tiles.
    """
    if spacing > target + 1e-9:
        raise ValueError(f'cache spacing {spacing} is coarser than the render spacing {target}')
    rows, cols = height.shape
    gx0, ci, fx = _axis_overlap(x0, spacing, cols, target)
    gy0, ri, fy = _axis_overlap(y0, spacing, rows, target)
    out_cols = int(np.max(np.where(fx < 1, ci + 1, ci))) + 1
    out_rows = int(np.max(np.where(fy < 1, ri + 1, ri))) + 1
    n = out_rows * out_cols
    base = np.where(np.isfinite(height), coverage, 0).astype(np.float64)
    hval = np.nan_to_num(height).astype(np.float64)
    wsum, hsum, csum = np.zeros(n), np.zeros(n), np.zeros((n, 3))
    for dr, wy in ((0, fy), (1, 1 - fy)):
        for dc, wx in ((0, fx), (1, 1 - fx)):
            weight = (base * wy[:, None] * wx[None, :]).ravel()
            used = weight > 0
            key = ((ri + dr)[:, None] * out_cols + (ci + dc)[None, :]).ravel()[used]
            weight = weight[used]
            wsum += np.bincount(key, weight, n)
            hsum += np.bincount(key, weight * hval.ravel()[used], n)
            for k in range(3):
                csum[:, k] += np.bincount(key, weight * rgb[..., k].ravel()[used], n)
    valid = wsum > 0
    with np.errstate(invalid='ignore', divide='ignore'):
        h = np.where(valid, hsum / wsum, np.nan).reshape(out_rows, out_cols)
        c = np.where(valid[:, None], csum / wsum[:, None], 0).reshape(out_rows, out_cols, 3)
    if return_weight:
        return h, np.rint(c).astype(np.uint8), gx0, gy0, wsum.reshape(out_rows, out_cols)
    return h, np.rint(c).astype(np.uint8), gx0, gy0


# ----------------------------------------------------------------------------- render grid

def render_grid(cache_path, target, correction=None):
    """Render grid of one cached tile in its native CRS: binned to target, empty cells filled
    from the nearest filled cell. Returns height, rgb, weight (binned coverage, 0 on filled
    cells), x0, y0, global cell indices i0 = x0 / target, j0 = y0 / target, sampled_fraction.
    correction (terrain_merge.MemberCorrection) corrects the binned heights and colours first."""
    with np.load(cache_path) as data:
        h, rgb, x0, y0, weight = bin_to_spacing(data['height'], data['rgb'], data['coverage'],
                                                float(data['x0']), float(data['y0']), float(data['spacing']),
                                                target, return_weight=True)
    if correction is not None:
        rows, cols = h.shape
        h, rgb = correction.grid(h, rgb, x0 + (np.arange(cols) + .5) * target, y0 + (np.arange(rows) + .5) * target)
    valid = np.isfinite(h)
    if not valid.any():
        raise ValueError(f'No covered cells in {cache_path}')
    nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    h = h[tuple(nearest)]
    rgb = rgb[tuple(nearest)]
    return dict(height=h, rgb=rgb, weight=np.where(valid, weight, 0.), x0=x0, y0=y0,
                i0=int(round(x0 / target)), j0=int(round(y0 / target)), sampled_fraction=float(valid.mean()))


def render_texture(cache_path, grid, target, texel, correction=None):
    """Texture of one tile for its render grid: the cache colours binned to texel metres on a
    lattice aligned to multiples of texel, covering the render cells [x0, x0 + cols * target) x
    [y0, y0 + rows * target) of grid (render_grid), so every vertex (cell centre) falls inside it.
    Texels without cache data take the colour of the nearest covered texel; correction
    (terrain_merge.MemberCorrection) corrects the colours. Returns image (rows, cols, 3) uint8
    with row 0 at the SOUTH edge, and its south-west corner tx0, ty0."""
    with np.load(cache_path) as data:
        _, rgb, bx0, by0, weight = bin_to_spacing(data['height'], data['rgb'], data['coverage'],
                                                  float(data['x0']), float(data['y0']), float(data['spacing']),
                                                  texel, return_weight=True)
    rows, cols = grid['height'].shape
    tx0, ty0 = np.floor(grid['x0'] / texel + 1e-9) * texel, np.floor(grid['y0'] / texel + 1e-9) * texel
    n_cols = int(np.ceil((grid['x0'] + cols * target - tx0) / texel - 1e-9))
    n_rows = int(np.ceil((grid['y0'] + rows * target - ty0) / texel - 1e-9))
    image = np.zeros((n_rows, n_cols, 3), np.uint8)
    valid = np.zeros((n_rows, n_cols), bool)
    c0, r0 = int(round((bx0 - tx0) / texel)), int(round((by0 - ty0) / texel))
    br, bc = rgb.shape[:2]
    src_r0, src_c0 = max(0, -r0), max(0, -c0)
    dst_r0, dst_c0 = max(0, r0), max(0, c0)
    n_r, n_c = min(br - src_r0, n_rows - dst_r0), min(bc - src_c0, n_cols - dst_c0)
    if n_r > 0 and n_c > 0:
        dst = (slice(dst_r0, dst_r0 + n_r), slice(dst_c0, dst_c0 + n_c))
        src = (slice(src_r0, src_r0 + n_r), slice(src_c0, src_c0 + n_c))
        image[dst] = rgb[src]
        valid[dst] = weight[src] > 0
    if not valid.any():
        raise ValueError(f'No covered texels in {cache_path}')
    nearest = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    image = image[tuple(nearest)]
    if correction is not None:
        image = correction.colour(image)
    return image, float(tx0), float(ty0)


# ----------------------------------------------------------------------------- simplification

SIMPLIFY_LEVELS = (128, 64, 32, 16, 8, 4, 2)


def _block_perimeter(s):
    """(row, col) offsets of the 4 s vertices on the boundary of an s x s block of quads, counter-
    clockwise seen from above (row = +y, col = +x), starting at the south-west corner."""
    k = np.arange(s)
    rows = np.concatenate([np.zeros(s), k, np.full(s, s), s - k]).astype(np.int64)
    cols = np.concatenate([k, np.full(s, s), s - k, np.zeros(s)]).astype(np.int64)
    return rows, cols


def simplify_grid(height, drawable, tolerance, levels=SIMPLIFY_LEVELS):
    """Triangles of a 2.5D grid with flat areas merged.

    height (rows, cols): vertex heights; drawable (rows - 1, cols - 1): quads to draw. From the
    largest level down, every aligned block of s x s quads that are all drawable and not taken
    by a larger block is merged when all its (s + 1)^2 vertices lie within tolerance of their
    least-squares plane. A merged block keeps, on its boundary, every vertex that is a corner of
    another block or of a single quad, lies on the grid border or touches a quad not drawn, so
    neighbouring blocks share their vertices (no T-junction, no crack). A block whose boundary
    keeps only its 4 corners becomes 2 triangles; any other block a fan of triangles around a
    new vertex at its centre, on the plane. The surface stays within 2 * tolerance of every grid
    vertex. Quads not in any block keep their 2 triangles. tolerance <= 0: no merging.
    Returns triangles (T, 3) int64 into the vertex list, and vertices as (ids, centres): ids
    (V0,) grid vertex ids (row * cols + col) used, then centres (V1, 3) = (row, col, height) of
    the added centre vertices in grid units; triangle indices refer to ids followed by centres."""
    rows, cols = height.shape
    taken = ~drawable.astype(bool)
    blocks = []
    for s in (levels if tolerance > 0 else ()):
        nr, nc = (rows - 1) // s, (cols - 1) // s
        if nr == 0 or nc == 0:
            continue
        z = sliding_window_view(height, (s + 1, s + 1))[::s, ::s][:nr, :nc]
        busy = taken[:nr * s, :nc * s].reshape(nr, s, nc, s).any(axis=(1, 3))
        u = np.arange(s + 1, dtype=np.float64) - s / 2
        var = (u ** 2).mean()
        mean = z.mean(axis=(2, 3))
        gx = (z * u[None, None, None, :]).mean(axis=(2, 3)) / var
        gy = (z * u[None, None, :, None]).mean(axis=(2, 3)) / var
        plane = mean[..., None, None] + gx[..., None, None] * u[None, None, None, :] + gy[..., None, None] * u[None, None, :, None]
        ok = ~busy & np.isfinite(mean) & (np.abs(z - plane).max(axis=(2, 3)) <= tolerance)
        if not ok.any():
            continue
        taken[:nr * s, :nc * s] |= np.repeat(np.repeat(ok, s, 0), s, 1)
        br, bc = np.nonzero(ok)
        blocks.append((s, br * s, bc * s, mean[ok]))
    qr, qc = np.nonzero(drawable.astype(bool) & ~taken)  # single quads: drawn, in no merged block
    required = np.zeros((rows, cols), bool)
    required[[0, -1], :] = True
    required[:, [0, -1]] = True
    not_drawn = ~drawable.astype(bool)
    for dr in (0, 1):
        for dc in (0, 1):
            required[dr:rows - 1 + dr, dc:cols - 1 + dc] |= not_drawn
            required[qr + dr, qc + dc] = True
    for s, r0, c0, _ in blocks:
        for dr in (0, s):
            for dc in (0, s):
                required[r0 + dr, c0 + dc] = True
    ids = lambda r, c: r * cols + c  # noqa: E731
    tris = [np.stack([ids(qr, qc), ids(qr, qc + 1), ids(qr + 1, qc + 1)], 1),
            np.stack([ids(qr, qc), ids(qr + 1, qc + 1), ids(qr + 1, qc)], 1)]
    centres, n_centres = [], 0
    for s, r0, c0, mean in blocks:
        pr, pc = _block_perimeter(s)
        rr, cc = r0[:, None] + pr[None, :], c0[:, None] + pc[None, :]
        keep = required[rr, cc]
        corners_only = keep.sum(1) == 4
        a, b, d, c = ids(r0, c0), ids(r0, c0 + s), ids(r0 + s, c0 + s), ids(r0 + s, c0)
        m = corners_only
        tris += [np.stack([a[m], b[m], d[m]], 1), np.stack([a[m], d[m], c[m]], 1)]
        fan = ~corners_only
        if fan.any():
            k = np.flatnonzero(fan)
            vid = ids(rr[k], cc[k])[keep[k]]
            owner = np.repeat(np.arange(len(k)), keep[k].sum(1))
            start = np.r_[0, np.cumsum(keep[k].sum(1))[:-1]]
            nxt = np.arange(len(vid)) + 1
            last = np.r_[start[1:], len(vid)] - 1
            nxt[last] = start
            centre_id = -1 - (n_centres + owner)  # negative: centre vertices, resolved below
            tris.append(np.stack([centre_id, vid, vid[nxt]], 1))
            centres.append(np.c_[r0[k] + s / 2, c0[k] + s / 2, mean[k]])
            n_centres += len(k)
    tris = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
    grid_used = np.unique(tris[tris >= 0])
    remap = np.full(rows * cols, -1, np.int64)
    remap[grid_used] = np.arange(len(grid_used))
    out = np.where(tris >= 0, remap[np.maximum(tris, 0)], len(grid_used) + (-1 - tris))
    centres = np.concatenate(centres) if centres else np.zeros((0, 3))
    return out, (grid_used, centres)


def decimate_mesh(xyz, triangles, ratio, aggressiveness=7.):
    """Quadric error decimation (pyfqmr) of a terrain mesh to about ratio of its triangles. Open
    border vertices are kept, so the outline (tile border, holes, cells of another member) and the
    vertices shared with the neighbouring tiles do not move. Returns (xyz, triangles)."""
    import pyfqmr
    simplifier = pyfqmr.Simplify()
    simplifier.setMesh(np.asarray(xyz, np.float64), np.asarray(triangles, np.int32))
    simplifier.simplify_mesh(target_count=max(1, int(round(ratio * len(triangles)))),
                             aggressiveness=aggressiveness, preserve_border=True, verbose=False)
    vertices, faces, _ = simplifier.getMesh()
    return np.asarray(vertices, np.float64), np.asarray(faces, np.int64)


def to_local(xyz, manifest, dataset):
    """Native projected CRS (x, y, height) -> geographic -> ECEF -> the run's local frame."""
    geo = Transformer.from_crs(dataset['projected_epsg'], dataset['geographic_epsg'], always_xy=True)
    ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    lon, lat = geo.transform(xyz[:, 0], xyz[:, 1])
    earth = np.array(ecef.transform(lon, lat, xyz[:, 2])).T
    return (earth - np.asarray(manifest['origin_ecef_m'])) @ np.asarray(manifest['local_from_ecef']).T


# ----------------------------------------------------------------------------- walls

WALL_MAX_ANGLE_DEG = 60.       # voxel normal vs wall outward normal
WALL_FALLBACK_CELLS = 2.       # nearest-voxel fallback radius, in render cells
SOURCES = ('voxels', 'nearest_voxel', 'lower_cell', 'lower_cell_small_step')
MAX_CANDIDATES_WALL = 4_000_000
RANK_BONUS = 1e6               # composite ownership: added per priority level to cells with data


def merged_window(tile, grids, pad=1):
    """Heights, colours and owners on the tile's render grid padded by pad cells (the halo).

    grids: {tile: render_grid(...)} for every tile available (the tile and its neighbours).
    Tiles of a dataset share one global lattice of render cells, but neighbouring tiles can
    both hold a cell along their seam (each with its own part of the cell). Such a cell is
    owned by the tile with the largest binned coverage weight, ties going to the first tile
    name in sorted order; the owner's height and colour are used. The rule depends only on
    the tiles holding the cell, so every tile reaches the same decision.
    Returns names (sorted tiles overlapping the window), owner (index into names, -1 = no
    cell), height, rgb, and the global indices (i0, j0) of window cell [0, 0].
    """
    g = grids[tile]
    rows, cols = g['height'].shape
    i0, j0 = g['i0'] - pad, g['j0'] - pad
    n_rows, n_cols = rows + 2 * pad, cols + 2 * pad
    names = sorted(t for t, n in grids.items()
                   if n['i0'] < i0 + n_cols and n['i0'] + n['height'].shape[1] > i0
                   and n['j0'] < j0 + n_rows and n['j0'] + n['height'].shape[0] > j0)
    owner = np.full((n_rows, n_cols), -1)
    weight = np.full((n_rows, n_cols), -1.)
    height = np.full((n_rows, n_cols), np.nan)
    rgb = np.zeros((n_rows, n_cols, 3), dtype=np.uint8)
    for k, name in enumerate(names):
        n = grids[name]
        nr, nc = n['height'].shape
        c0, c1 = max(i0, n['i0']), min(i0 + n_cols, n['i0'] + nc)
        r0, r1 = max(j0, n['j0']), min(j0 + n_rows, n['j0'] + nr)
        win = (slice(r0 - j0, r1 - j0), slice(c0 - i0, c1 - i0))
        src = (slice(r0 - n['j0'], r1 - n['j0']), slice(c0 - n['i0'], c1 - n['i0']))
        score = n['weight'][src]
        if 'rank' in n:  # composite: cells with data of a higher-priority member always win
            score = np.where(score > 0, score + RANK_BONUS * (n['n_ranks'] - n['rank']), score)
        better = score > weight[win]
        weight[win] = np.where(better, score, weight[win])
        owner[win] = np.where(better, k, owner[win])
        height[win] = np.where(better, n['height'][src], height[win])
        rgb[win] = np.where(better[..., None], n['rgb'][src], rgb[win])
    return names, owner, height, rgb, i0, j0


def merge_voxels(parts):
    """Merge facade voxel sets of several tiles: one entry per (ix, iy, iz), count-weighted.
    parts: list of dicts with ijk (N,3) int, rgb (N,3), normal (N,2) (x 127), count (N,)."""
    parts = [p for p in parts if len(p['ijk'])]
    if not parts:
        return dict(ijk=np.zeros((0, 3), np.int64), rgb=np.zeros((0, 3)), normal=np.zeros((0, 2)),
                    count=np.zeros(0))
    ijk = np.concatenate([p['ijk'] for p in parts]).astype(np.int64)
    count = np.concatenate([p['count'] for p in parts]).astype(np.float64)
    rgb = np.concatenate([p['rgb'] for p in parts]).astype(np.float64)
    normal = np.concatenate([p['normal'] for p in parts]).astype(np.float64) / 127
    unique, inverse = np.unique(ijk, axis=0, return_inverse=True)
    inverse = inverse.ravel()
    total = np.bincount(inverse, count, len(unique))

    def mean(values):
        return np.stack([np.bincount(inverse, values[:, c] * count, len(unique)) for c in range(values.shape[1])],
                        1) / total[:, None]
    return dict(ijk=unique, rgb=mean(rgb), normal=mean(normal), count=total)


def _enumerate_boxes(lo, hi, voxel, chunk=MAX_CANDIDATES_WALL):
    """Voxels whose centre lies in the boxes [lo, hi] (S,3); yields (segment ids, ijk) in chunks."""
    a = np.ceil(lo / voxel - .5).astype(np.int64)
    b = np.floor(hi / voxel - .5).astype(np.int64)
    size = np.maximum(b - a + 1, 0)
    counts = size.prod(1)
    ends = np.cumsum(counts)
    start = 0
    while start < len(counts):
        stop = max(int(np.searchsorted(ends, (ends[start - 1] if start else 0) + chunk, 'right')), start + 1)
        sl = slice(start, stop)
        total = int(counts[sl].sum())
        if total:
            sid = np.repeat(np.arange(start, stop), counts[sl])
            offset = np.arange(total) - np.repeat(np.cumsum(counts[sl]) - counts[sl], counts[sl])
            sx, sy = size[sid, 0], size[sid, 1]
            ijk = a[sid] + np.stack([offset % sx, (offset // sx) % sy, offset // (sx * sy)], 1)
            yield sid, ijk
        start = stop


def _rect_distance(centre, xa, xb, ya, yb, z0, z1):
    """Horizontal and vertical distances from points (n,3) to axis-aligned wall rectangles."""
    dx = np.maximum(0, np.maximum(xa - centre[:, 0], centre[:, 0] - xb))
    dy = np.maximum(0, np.maximum(ya - centre[:, 1], centre[:, 1] - yb))
    dz = np.maximum(0, np.maximum(z0 - centre[:, 2], centre[:, 2] - z1))
    return np.hypot(dx, dy), dz


def colour_segments(seg, voxels, voxel_size, spacing, max_angle_deg=WALL_MAX_ANGLE_DEG,
                    fallback_cells=WALL_FALLBACK_CELLS):
    """Colour wall segments from facade voxels.

    seg: dict of (S,) arrays xa, xb, ya, yb (horizontal extent of the segment, degenerate in the
    axis normal to it), z0, z1, nx, ny (outward normal, unit, axis-aligned).
    A voxel is compatible with a segment if its mean normal is within max_angle_deg of the
    segment's outward normal. Colour = count-weighted mean of the compatible voxels whose centre
    is within spacing / 2 of the segment's edge line horizontally and inside [z0, z1]; else the
    nearest compatible voxel (3D distance from its centre to the segment) within fallback_cells
    render cells; else none. Returns rgb (S,3) float (NaN where none) and source (S,) int8:
    0 = voxels, 1 = nearest voxel, 2 = none.
    """
    n_seg = len(seg['z0'])
    rgb = np.full((n_seg, 3), np.nan)
    source = np.full(n_seg, 2, dtype=np.int8)
    if not n_seg or not len(voxels['ijk']):
        return rgb, source
    ijk = voxels['ijk']
    base = ijk.min(0) - 1
    dims = ijk.max(0) - base + 2
    keys = ((ijk[:, 2] - base[2]) * dims[1] + (ijk[:, 1] - base[1])) * dims[0] + (ijk[:, 0] - base[0])
    order = np.argsort(keys)
    sorted_keys = keys[order]
    length = np.linalg.norm(voxels['normal'], axis=1)
    unit = voxels['normal'] / np.where(length > 0, length, 1)[:, None]
    cos_max = np.cos(np.radians(max_angle_deg))

    def lookup(sid, cand):
        inside = ((cand > base) & (cand < base + dims - 1)).all(1)
        sid, cand = sid[inside], cand[inside]
        k = ((cand[:, 2] - base[2]) * dims[1] + (cand[:, 1] - base[1])) * dims[0] + (cand[:, 0] - base[0])
        pos = np.minimum(np.searchsorted(sorted_keys, k), len(sorted_keys) - 1)
        hit = sorted_keys[pos] == k
        vid = order[pos[hit]]
        sid = sid[hit]
        ok = (length[vid] > 0) & (unit[vid, 0] * seg['nx'][sid] + unit[vid, 1] * seg['ny'][sid] >= cos_max - 1e-12)
        return sid[ok], vid[ok]

    xa, xb, ya, yb, z0, z1 = (seg[k] for k in ('xa', 'xb', 'ya', 'yb', 'z0', 'z1'))
    half = spacing / 2
    # Direct: centre within spacing / 2 of the edge line horizontally, inside the height range.
    lo = np.c_[xa - half, ya - half, z0]
    hi = np.c_[xb + half, yb + half, z1]
    wsum = np.zeros(n_seg)
    csum = np.zeros((n_seg, 3))
    for sid, cand in _enumerate_boxes(lo, hi, voxel_size):
        sid, vid = lookup(sid, cand)
        centre = (ijk[vid] + .5) * voxel_size
        hd, dz = _rect_distance(centre, xa[sid], xb[sid], ya[sid], yb[sid], z0[sid], z1[sid])
        ok = (hd <= half + 1e-9) & (dz <= 1e-9)
        sid, vid = sid[ok], vid[ok]
        w = voxels['count'][vid]
        wsum += np.bincount(sid, w, n_seg)
        for c in range(3):
            csum[:, c] += np.bincount(sid, w * voxels['rgb'][vid, c], n_seg)
    direct = wsum > 0
    rgb[direct] = csum[direct] / wsum[direct, None]
    source[direct] = 0
    # Fallback: nearest compatible voxel within fallback_cells render cells of the segment.
    rest = np.flatnonzero(~direct)
    if len(rest):
        reach = fallback_cells * spacing
        lo = np.c_[xa[rest] - reach, ya[rest] - reach, z0[rest] - reach]
        hi = np.c_[xb[rest] + reach, yb[rest] + reach, z1[rest] + reach]
        best = np.full(len(rest), np.inf)
        best_vid = np.full(len(rest), -1)
        sub = {k: seg[k][rest] for k in seg}
        for local, cand in _enumerate_boxes(lo, hi, voxel_size):
            sid, vid = lookup(rest[local], cand)
            local = np.searchsorted(rest, sid)  # rest is sorted: segment id -> position in rest
            centre = (ijk[vid] + .5) * voxel_size
            hd, dz = _rect_distance(centre, sub['xa'][local], sub['xb'][local], sub['ya'][local],
                                    sub['yb'][local], sub['z0'][local], sub['z1'][local])
            d = np.hypot(hd, dz)
            ok = d <= reach + 1e-9
            local, vid, d = local[ok], vid[ok], d[ok]
            if not len(local):  # no voxel within reach in this batch
                continue
            # Per segment: smallest distance, ties to the smallest voxel key (deterministic).
            o = np.lexsort((keys[vid], d, local))
            local, vid, d = local[o], vid[o], d[o]
            first = np.r_[True, local[1:] != local[:-1]]
            local, vid, d = local[first], vid[first], d[first]
            better = d < best[local]
            best[local[better]] = d[better]
            best_vid[local[better]] = vid[better]
        found = best_vid >= 0
        rgb[rest[found]] = voxels['rgb'][best_vid[found]]
        source[rest[found]] = 1
    return rgb, source


def build_walls(tile, grids, load_voxels, spacing, threshold=2.0, all_steps=False):
    """Walls of one tile on its render grid, in the native CRS.

    A vertical wall stands on every shared edge between two 4-adjacent render cells whose
    heights differ by more than threshold, from the lower to the upper height, facing the lower
    cell. Edges are taken from the merged window (merged_window, one-cell halo of the
    neighbouring tiles); an edge belongs to the tile owning its west cell (east-west pairs) or
    its south cell (north-south pairs), so a seam edge is built exactly once. Each wall is split
    into ceil(dh / spacing) segments of equal height (<= spacing), coloured by colour_segments
    from the facade voxels of every tile in the window; segments without voxels take the lower
    cell's colour.

    all_steps (study variant WALL2_BLOCK): walls on every edge with any height difference;
    steps <= threshold take the lower cell's colour; the owned cells are also returned as flat
    quads at their own height.

    load_voxels(tile) -> dict(ijk, rgb, normal, count, voxel) of the tile's cache.
    Returns quads (S,4,3) float64 [bottom a, bottom b, top b, top a], rgb (S,3) uint8,
    normal (S,2), source (S,) int8 (index into SOURCES), edges, and with all_steps
    cell_quads (C,4,3), cell_rgb (C,3).
    """
    names, owner, height, rgb, i0, j0 = merged_window(tile, grids)
    me = names.index(tile)
    own = owner == me
    s = spacing
    limit = 0. if all_steps else threshold
    parts = []
    # East-west pairs (r, c) | (r, c + 1): edge x = (i0 + c + 1) s, y in [(j0 + r) s, (j0 + r + 1) s].
    a, b = height[:, :-1], height[:, 1:]
    r, c = np.nonzero(own[:, :-1] & (owner[:, 1:] >= 0) & (np.abs(b - a) > limit))
    west_low = height[r, c] < height[r, c + 1]
    x = (i0 + c + 1) * s
    parts.append(dict(xa=x, xb=x, ya=(j0 + r) * s, yb=(j0 + r + 1) * s,
                      lo=np.minimum(height[r, c], height[r, c + 1]), hi=np.maximum(height[r, c], height[r, c + 1]),
                      nx=np.where(west_low, -1., 1.), ny=np.zeros(len(r)),
                      low_rgb=np.where(west_low[:, None], rgb[r, c], rgb[r, c + 1])))
    # North-south pairs (r, c) / (r + 1, c): edge y = (j0 + r + 1) s, x in [(i0 + c) s, (i0 + c + 1) s].
    a, b = height[:-1], height[1:]
    r, c = np.nonzero(own[:-1] & (owner[1:] >= 0) & (np.abs(b - a) > limit))
    south_low = height[r, c] < height[r + 1, c]
    y = (j0 + r + 1) * s
    parts.append(dict(xa=(i0 + c) * s, xb=(i0 + c + 1) * s, ya=y, yb=y,
                      lo=np.minimum(height[r, c], height[r + 1, c]), hi=np.maximum(height[r, c], height[r + 1, c]),
                      nx=np.zeros(len(r)), ny=np.where(south_low, -1., 1.),
                      low_rgb=np.where(south_low[:, None], rgb[r, c], rgb[r + 1, c])))
    edge = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    n_edges = len(edge['lo'])
    # Vertical split into equal segments of height <= spacing.
    dh = edge['hi'] - edge['lo']
    n_split = np.maximum(1, np.ceil(dh / s - 1e-9).astype(np.int64))
    eid = np.repeat(np.arange(n_edges), n_split)
    k = np.arange(len(eid)) - np.repeat(np.cumsum(n_split) - n_split, n_split)
    seg = {key: edge[key][eid] for key in ('xa', 'xb', 'ya', 'yb', 'nx', 'ny')}
    seg['z0'] = edge['lo'][eid] + dh[eid] * k / n_split[eid]
    seg['z1'] = edge['lo'][eid] + dh[eid] * (k + 1) / n_split[eid]
    coloured = dh[eid] > threshold
    rgb_seg = edge['low_rgb'][eid].astype(np.float64)
    source = np.full(len(eid), 3, dtype=np.int8)
    if coloured.any():
        sub = {key: v[coloured] for key, v in seg.items()}
        sets = [load_voxels(t) for t in names]
        voxel_size = float(sets[0]['voxel'])
        if any(abs(float(v['voxel']) - voxel_size) > 1e-9 for v in sets):
            raise ValueError(f'Facade voxel sizes differ around {tile}')
        # Only voxels that can reach the window (fallback radius included).
        reach = WALL_FALLBACK_CELLS * s + voxel_size
        x_lo, y_lo = i0 * s - reach, j0 * s - reach
        x_hi, y_hi = (i0 + owner.shape[1]) * s + reach, (j0 + owner.shape[0]) * s + reach
        clipped = []
        for v in sets:
            centre = (v['ijk'][:, :2] + .5) * voxel_size
            keep = ((centre[:, 0] >= x_lo) & (centre[:, 0] <= x_hi) & (centre[:, 1] >= y_lo) & (centre[:, 1] <= y_hi))
            clipped.append({key: v[key][keep] for key in ('ijk', 'rgb', 'normal', 'count')})
        colour, src = colour_segments(sub, merge_voxels(clipped), voxel_size, s)
        found = src < 2
        idx = np.flatnonzero(coloured)
        rgb_seg[idx[found]] = colour[found]
        source[idx] = src
    xa, xb, ya, yb, z0, z1 = (seg[key] for key in ('xa', 'xb', 'ya', 'yb', 'z0', 'z1'))
    quads = np.stack([np.c_[xa, ya, z0], np.c_[xb, yb, z0], np.c_[xb, yb, z1], np.c_[xa, ya, z1]], 1)
    result = dict(quads=quads, rgb=np.rint(rgb_seg).clip(0, 255).astype(np.uint8),
                  normal=np.c_[seg['nx'], seg['ny']], source=source, edges=n_edges)
    if all_steps:
        r, c = np.nonzero(own)
        x0, x1, y0, y1, z = (i0 + c) * s, (i0 + c + 1) * s, (j0 + r) * s, (j0 + r + 1) * s, height[r, c]
        result['cell_quads'] = np.stack([np.c_[x0, y0, z], np.c_[x1, y0, z], np.c_[x1, y1, z], np.c_[x0, y1, z]], 1)
        result['cell_rgb'] = rgb[r, c]
    return result


def facade_loader(cache_dir, maxsize=32):
    """load_voxels(tile) for build_walls, reading the facade keys of <cache_dir>/<tile>.npz (cached)."""
    @lru_cache(maxsize=maxsize)
    def load(tile):
        with np.load(Path(cache_dir) / f'{tile}.npz') as data:
            if 'facade_ijk' not in data.files:
                raise ValueError(f'{cache_dir}/{tile}.npz has no facade voxels (built with terrain_cache.py --no-facades)')
            return dict(ijk=data['facade_ijk'].astype(np.int64), rgb=data['facade_rgb'], normal=data['facade_normal'],
                        count=data['facade_count'], voxel=float(data['facade_voxel']))
    return load


def composite_keep(name, grids):
    """Cells (rows, cols) bool of tile name to draw in a composite terrain.

    A cell is drawn when its member has data there (binned weight > 0) and no higher-priority
    member does, or when no member has data there and no higher-priority member's grid holds the
    cell (the nearest-cell fill is drawn once). grids: render grids with keys member and rank."""
    g = grids[name]
    rows, cols = g['height'].shape
    own = g['weight'] > 0
    higher_data = np.zeros((rows, cols), bool)
    higher_holds = np.zeros((rows, cols), bool)
    other_data = np.zeros((rows, cols), bool)
    for other, n in grids.items():
        if n['member'] == g['member']:
            continue
        nr, nc = n['height'].shape
        c0, c1 = max(g['i0'], n['i0']), min(g['i0'] + cols, n['i0'] + nc)
        r0, r1 = max(g['j0'], n['j0']), min(g['j0'] + rows, n['j0'] + nr)
        if c1 <= c0 or r1 <= r0:
            continue
        mine = (slice(r0 - g['j0'], r1 - g['j0']), slice(c0 - g['i0'], c1 - g['i0']))
        data = n['weight'][r0 - n['j0']:r1 - n['j0'], c0 - n['i0']:c1 - n['i0']] > 0
        other_data[mine] |= data
        if n['rank'] < g['rank']:
            higher_data[mine] |= data
            higher_holds[mine] = True
    return (own & ~higher_data) | (~own & ~other_data & ~higher_holds)


def face_keep_of(keep):
    """Grid quads (rows - 1, cols - 1) to draw: those with at least one drawn corner cell, so the
    surfaces of two members meet without a gap (they overlap by at most one cell)."""
    return keep[:-1, :-1] | keep[1:, :-1] | keep[:-1, 1:] | keep[1:, 1:]


# ----------------------------------------------------------------------------- output

def convert_tile(grid, output, manifest, dataset, target, walls=None, face_keep=None, texture=None,
                 tolerance=0., decimate=1.):
    """Write the render .npz of one tile from its render_grid (and build_walls result).

    texture: (image, tx0, ty0, texel) from render_texture, for the textured format (written as
    <output stem>_texture.png), with the grid simplified by simplify_grid(tolerance) and then
    decimated to the fraction decimate of its triangles (decimate_mesh).
    Returns rows, cols of the render grid and the number of terrain triangles."""
    h, rgb = grid['height'], grid['rgb']
    rows, cols = h.shape
    extra = {}
    if walls is not None:
        n = len(walls['quads'])
        counts = np.bincount(walls['source'], minlength=len(SOURCES))
        extra = dict(wall_vertices=to_local(walls['quads'].reshape(-1, 3), manifest, dataset).astype(np.float32),
                     wall_rgb=np.repeat(walls['rgb'], 4, axis=0), wall_segments=n, wall_edges=walls['edges'],
                     wall_sources=np.array(SOURCES), wall_source_counts=counts)
    if texture is not None:
        image, tx0, ty0, texel = texture
        drawable = face_keep if face_keep is not None else np.ones((rows - 1, cols - 1), bool)
        triangles, (ids, centres) = simplify_grid(h, drawable, tolerance)
        r, c = np.divmod(ids, cols)
        grid_rc = np.c_[r, c, h.ravel()[ids]]
        rcz = np.concatenate([grid_rc, centres]) if len(centres) else grid_rc
        # metres from the tile corner (small numbers: the decimation works in float64 anyway)
        xyz = np.c_[(rcz[:, 1] + .5) * target, (rcz[:, 0] + .5) * target, rcz[:, 2]]
        if decimate < 1 and len(triangles):
            xyz, triangles = decimate_mesh(xyz, triangles, decimate)
        x, y = grid['x0'] + xyz[:, 0], grid['y0'] + xyz[:, 1]
        local = to_local(np.c_[x, y, xyz[:, 2]], manifest, dataset)
        uv = np.c_[(x - tx0) / (image.shape[1] * texel), (y - ty0) / (image.shape[0] * texel)]
        name = output.stem + '_texture.png'
        part = output.parent / (name + '.part')
        Image.fromarray(np.ascontiguousarray(image[::-1])).save(part, format='PNG', compress_level=3)
        part.replace(output.parent / name)
        arrays = dict(vertices=local.astype(np.float32), uv=uv.astype(np.float32),
                      triangles=triangles.astype(np.int32), texture=name, texture_spacing=texel)
        n_triangles = len(triangles)
    else:
        xx, yy = np.meshgrid(grid['x0'] + (np.arange(cols) + .5) * target,
                             grid['y0'] + (np.arange(rows) + .5) * target)
        local = to_local(np.c_[xx.ravel(), yy.ravel(), h.ravel()], manifest, dataset)
        arrays = dict(vertices=local.astype(np.float32), rgb=rgb.reshape(-1, 3))
        if face_keep is not None:
            extra['face_keep'] = face_keep
        n_triangles = 2 * int(face_keep.sum() if face_keep is not None else (rows - 1) * (cols - 1))
    temporary = output.with_suffix('.npz.part')
    with temporary.open('wb') as stream:
        np.savez_compressed(stream, shape=np.array([rows, cols]), sampled_fraction=grid['sampled_fraction'],
                            compute_device='cpu', compute_device_name='terrain_to_render.py', **arrays, **extra)
    temporary.replace(output)
    return rows, cols, n_triangles


def binned_shape(cache_path, target):
    """(rows, cols) that convert_tile would write for this cache, without converting it."""
    with np.load(cache_path) as data:
        rows, cols = data['height'].shape
        x0, y0, spacing = float(data['x0']), float(data['y0']), float(data['spacing'])
    _, ci, fx = _axis_overlap(x0, spacing, cols, target)
    _, ri, fy = _axis_overlap(y0, spacing, rows, target)
    return int(np.max(np.where(fy < 1, ri + 1, ri))) + 1, int(np.max(np.where(fx < 1, ci + 1, ci))) + 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cameras', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True, help='folder with <tile>.npz from terrain_cache.py')
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', type=Path,
                        help='default: <run>/terrain_render, where cameras.json is <run>/simulation/cameras.json')
    parser.add_argument('--spacing', type=float, help="render grid spacing; default: cameras.json mesh_grid_spacing_m")
    parser.add_argument('--walls', action='store_true',
                        help='add vertical walls coloured from the facade voxels (cache not built with --no-facades)')
    parser.add_argument('--wall-threshold', type=float, default=2.0,
                        help='height difference (m) between adjacent render cells above which a wall is built')
    parser.add_argument('--texture-spacing', type=float,
                        help='texel size (m) of a per-tile terrain texture (e.g. the cache spacing, 1); '
                             'default: colours on the vertices, as before')
    parser.add_argument('--simplify-tolerance', type=float, default=0.,
                        help='merge grid blocks whose vertices lie within this height (m) of a plane '
                             '(needs --texture-spacing); 0: no merging')
    parser.add_argument('--decimate', type=float, default=1.,
                        help='keep about this fraction of each tile\'s terrain triangles by quadric error decimation '
                             '(pyfqmr; needs --texture-spacing); 1: no decimation')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='report what would be converted, write nothing')
    parser.add_argument('--datasets', type=Path, help='terrain_datasets.json to use (default: the repository one)')
    args = parser.parse_args()

    if (args.simplify_tolerance > 0 or args.decimate < 1) and not args.texture_spacing:
        parser.error('--simplify-tolerance and --decimate need --texture-spacing (merged cells would lose their colours)')
    if not 0 < args.decimate <= 1:
        parser.error('--decimate must be in (0, 1]')
    if args.output is None:
        args.output = args.cameras.resolve().parent.parent / 'terrain_render'
    manifest = json.loads(args.cameras.read_text())
    datasets = load_datasets(args.datasets) if args.datasets else load_datasets()
    members = composite_members(datasets, args.dataset)
    target = args.spacing or manifest['mesh_grid_spacing_m']
    frame = {k: manifest[k] for k in ('origin_ecef_m', 'local_from_ecef')}
    frame['mesh_grid_spacing_m'] = target
    if args.walls:  # renders with and without walls must not share an output folder
        frame['walls'] = dict(threshold_m=args.wall_threshold, max_angle_deg=WALL_MAX_ANGLE_DEG,
                              fallback_cells=WALL_FALLBACK_CELLS)
    if args.texture_spacing:
        frame['mesh'] = dict(texture_spacing_m=args.texture_spacing, simplify_tolerance_m=args.simplify_tolerance)
        if args.decimate < 1:
            frame['mesh']['decimate'] = args.decimate
    corrections = {}
    if members:  # composite: one entry per member tile, member caches beside the composite folder
        caches = member_cache_dirs(args.cache, members)
        model, corrections = load_corrections(args.cache, datasets, args.dataset)
        frame['composite'] = dict(members=members, correction=model_digest(model))
        entries = [(f'{m}__{t}', m, t, caches[m]) for m in members for t in run_tiles(datasets[m], manifest)]
    else:
        entries = [(t, args.dataset, t, args.cache) for t in run_tiles(datasets[args.dataset], manifest)]
    rank = {m: k for k, m in enumerate(members or [args.dataset])}
    frame_path = args.output / 'cache_frame.json'
    if frame_path.exists() and json.loads(frame_path.read_text()) != frame:
        raise ValueError('Frame, spacing, wall, texture or simplification settings or composite correction differ '
                         'from the existing output; use a new output directory')
    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)
        frame_path.write_text(json.dumps(frame, indent=2) + '\n')
    where = {out: (m, t, c) for out, m, t, c in entries}
    missing = [out for out, m, t, c in entries if not (c / f'{t}.npz').exists()]
    todo = [out for out, m, t, c in entries
            if out not in missing and (args.force or not (args.output / f'{out}.npz').exists())]
    grids = {}
    if (args.walls or members) and todo and not args.dry_run:
        # Walls need the neighbours' grids (halo) and voxels, a composite the other members' grids:
        # build every available grid first.
        for out, (m, t, c) in where.items():
            if out in missing:
                continue
            grids[out] = render_grid(c / f'{t}.npz', target, corrections.get(m))
            if members:
                grids[out].update(member=m, rank=rank[m], n_ranks=len(members))
        if members:
            loaders = {m: facade_loader(caches[m]) for m in members}

            def load_voxels(out):
                m, t, _ = where[out]
                voxels = loaders[m](t)
                return corrections[m].voxels(voxels) if m in corrections else voxels
        else:
            load_voxels = facade_loader(args.cache)
    done, kept, vertices, triangles, wall_triangles = 0, 0, 0, 0, 0
    sources = np.zeros(len(SOURCES), dtype=np.int64)
    for out, m, t, c in entries:
        if out in missing:
            continue
        source, destination = c / f'{t}.npz', args.output / f'{out}.npz'
        quads, n_triangles = None, None
        if out not in todo:
            with np.load(destination) as data:  # already converted: count what the renderer will load
                rows, cols = (int(n) for n in data['shape'])
                if 'triangles' in data.files:
                    n_triangles = int(data['triangles'].shape[0])
                elif 'face_keep' in data.files:
                    quads = int(data['face_keep'].sum())
                if 'wall_segments' in data.files:
                    wall_triangles += 2 * int(data['wall_segments'])
                    sources += data['wall_source_counts']
            kept += 1
        elif args.dry_run:
            rows, cols = binned_shape(source, target)
            done += 1
        else:
            grid = grids[out] if grids else render_grid(source, target)
            walls = build_walls(out, grids, load_voxels, target, args.wall_threshold) if args.walls else None
            face_keep = face_keep_of(composite_keep(out, grids)) if members else None
            texture = None
            if args.texture_spacing:
                image, tx0, ty0 = render_texture(source, grid, target, args.texture_spacing, corrections.get(m))
                texture = (image, tx0, ty0, args.texture_spacing)
            rows, cols, n_triangles = convert_tile(grid, destination, manifest, datasets[m], target, walls, face_keep,
                                                   texture, args.simplify_tolerance, args.decimate)
            if face_keep is not None:
                quads = int(face_keep.sum())
                print(f'{out}: {quads:,} of {face_keep.size:,} grid quads drawn', flush=True)
            if texture is not None:
                full = 2 * (quads if quads is not None else (rows - 1) * (cols - 1))
                print(f'{out}: {n_triangles:,} terrain triangles ({n_triangles / max(full, 1):.1%} of the '
                      f'{full:,} of the drawn grid), texture {texture[0].shape[1]} x {texture[0].shape[0]} '
                      f'at {args.texture_spacing:g} m', flush=True)
            if walls is not None:
                wall_triangles += 2 * len(walls['quads'])
                sources += np.bincount(walls['source'], minlength=len(SOURCES))
                print(f'{out}: {walls["edges"]:,} wall edges, {len(walls["quads"]):,} segments '
                      f'({", ".join(f"{n} {k}" for k, n in zip(SOURCES, np.bincount(walls["source"], minlength=len(SOURCES))))})',
                      flush=True)
            done += 1
        vertices += rows * cols
        # textured: the triangles written; else two per drawn grid quad, as render_swarm.py builds them
        triangles += (n_triangles if n_triangles is not None
                      else 2 * (quads if quads is not None else (rows - 1) * (cols - 1)))
    if not args.dry_run:
        present = [out for out, *_ in entries if out not in missing]
        (args.output / 'tiles.json').write_text(json.dumps(dict(dataset=args.dataset, tiles=present), indent=1) + '\n')
    print(f'{"would convert" if args.dry_run else "converted"} {done} tiles at {target:g} m, '
          f'{kept} already present; {done + kept} tiles, {vertices:,} vertices, '
          f'{triangles:,} triangles -> {args.output}')
    if args.walls and not args.dry_run:
        print(f'walls: {wall_triangles // 2:,} segments, {wall_triangles:,} triangles; segment colour from '
              + ', '.join(f'{k} {n:,}' for k, n in zip(SOURCES, sources)))
    if missing:
        raise SystemExit(f'{len(missing)} selected tiles missing from the cache: {missing[:10]}...')


if __name__ == '__main__':
    main()
