"""Download photomesh tiles one at a time and keep only a 2.5D raster of them.

For every requested tile the script downloads the archive, rasterises the upper
surface of the textured mesh onto a regular grid in the dataset's own projected
CRS, writes one compressed .npz per tile and deletes the archive. The raster is
independent of any orbit simulation, so a tile is downloaded once and reused by
every run (see terrain_to_render.py for the conversion to the render frame).

Rasterisation is a top-view z-buffer: every cell centre of a fine sub-grid
(spacing / supersample) is tested against every triangle covering it, the
highest surface wins and its colour is read from the texture at the
interpolated UV. Each output cell then averages the valid sub-cells, so the
colour is an area mean over the cell instead of a single texel. Vertical faces
(walls) have no top-view area and are ignored: the result is the upper surface
only, like prepare_swarm_terrain.py.

Output per tile, arrays indexed [row, col] with row 0 at the SOUTH edge
(y increasing with row) and col 0 at the WEST edge:
  height    float32 (rows, cols), mean height of the covered sub-cells, NaN if empty
  rgb       uint8   (rows, cols, 3), mean sRGB colour of the covered sub-cells
  coverage  uint8   (rows, cols), number of covered sub-cells (0..supersample**2)
  x0, y0    south-west corner of cell [0, 0] in the projected CRS (multiples of spacing,
            so tiles of the same dataset share one global grid)
  spacing, supersample, epsg, dataset, tile, plus timing/size metadata

By default the same .npz also holds the near-vertical faces (|n_z| < 0.3) as coloured
samples on a global 3D voxel grid of size --facade-voxel (default 1 m), aligned to multiples
of it in the dataset CRS (see FacadeVoxels; terrain_to_render.py --walls turns them into walls,
since the wall positions depend on the render spacing chosen later). One entry per voxel:
  facade_ijk     int32 (N, 3), voxel indices: the voxel spans [i, i + 1) * facade_voxel per axis
  facade_rgb     uint8 (N, 3), area-weighted mean sRGB colour of the samples in the voxel
  facade_normal  int8 (N, 2), 127 x area-weighted mean of the unit horizontal outward normals
                 (length < 127 when the voxel holds facades facing different ways)
  facade_count   uint32 (N,), number of samples (>= 16 per m^2 of facade)
  facade_voxel, facade_faces, facade_area_m2, facade_samples (scalars)
--no-facades writes the grid keys only (for uses that need no walls); the grid arrays are
bit-for-bit the same either way. A tile folder holds one kind of tile only: the script refuses
to write into a folder whose existing tiles differ from the request (facades or not, spacing,
supersample, facade voxel size), in both directions. --tag appends a suffix to the tile folder
(<spacing>m_<tag>/) to keep a differently built cache apart; the pipeline uses no tag.

Example (Melbourne, tiles chosen by an existing simulation):
  python pipeline/terrain_cache.py --dataset melbourne_2020 --spacing 1.0 \\
      --tiles-from output/.../simulation/cameras.json --workers 4
  (tiles in data/terrain/melbourne_2020/1m/, dataset.json and tile_grid.json one level up)

log.jsonl in the tile folder gets one line per tile as soon as that tile is written. To
recreate missing lines from the .npz files present (download fields left null):
  python pipeline/terrain_cache.py --dataset melbourne_2020 --spacing 1.0 --rebuild-log
"""
import argparse
import io
import json
import os
import re
import shutil
import struct
import time
import urllib.request
import zipfile
import zlib
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # photomesh atlases can exceed Pillow's default guard
HERE = Path(__file__).resolve().parent
REPO = HERE.parent              # repository root: terrain_datasets.json, data/
MAX_CANDIDATES = 4_000_000     # sub-cell candidates per vectorised batch (memory bound)
FACE_BATCH = 200_000           # triangles accumulated before a rasterisation pass


def load_datasets(path=REPO/'terrain_datasets.json'):
    return json.loads(Path(path).read_text())


# ----------------------------------------------------------------------------- download

def download(url, destination, retries=3, chunk=1 << 20):
    """Stream url to destination atomically; return the number of bytes written.

    After the last failed attempt raises RuntimeError with the URL and the original error as
    text. The original exception is not re-raised because it may not be picklable (an
    urllib HTTPError holds the open response), and process_tile runs in worker processes
    whose exceptions are pickled back to the main process."""
    temporary = destination.with_suffix(destination.suffix + '.part')
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as out:
                shutil.copyfileobj(response, out, chunk)
            temporary.replace(destination)
            return destination.stat().st_size
        except Exception as error:
            temporary.unlink(missing_ok=True)
            if attempt == retries:
                raise RuntimeError(f'download failed after {retries} attempts: {url}: '
                                   f'{type(error).__name__}: {error}') from error
            time.sleep(5 * attempt)


# ----------------------------------------------------------------------------- truncated archives

class SequentialZip:
    """Read-only view of a ZIP whose central directory is lost (an interrupted upload or download:
    the file is cut or its tail is zero bytes). The entries are found by walking the local file
    headers from the start of the file. An entry is kept only when a valid local header (or the
    central directory) follows it, so the entry cut by the truncation is left out; each read is
    checked against the CRC of its header. Entries must be stored or deflated and carry their
    sizes in the local header (no data descriptor). Same reading API as zipfile.ZipFile for what
    rasterise_obj_zip uses: infolist, namelist, read, open."""

    def __init__(self, path):
        self.path = Path(path)
        self.fp = open(self.path, 'rb')
        size = os.fstat(self.fp.fileno()).st_size
        self.entries, pending, offset = {}, None, 0
        while offset + 30 <= size:
            self.fp.seek(offset)
            head = self.fp.read(30)
            if head[:4] == b'PK\x01\x02' and pending:  # central directory: the last entry is whole
                self.entries[pending[0]] = pending[1]
                pending = None
            if head[:4] != b'PK\x03\x04':
                break
            _, _, flags, method, _, _, crc, csize, usize, nlen, xlen = struct.unpack('<IHHHHHIIIHH', head)
            name = self.fp.read(nlen).decode('utf-8' if flags & 0x800 else 'cp437')
            extra = self.fp.read(xlen)
            if 0xFFFFFFFF in (csize, usize):  # ZIP64 extra field: sizes stored there
                at = 0
                while at + 4 <= len(extra):
                    tag, length = struct.unpack('<HH', extra[at:at + 4])
                    if tag == 1:
                        values = list(struct.unpack(f'<{length // 8}Q', extra[at + 4:at + 4 + 8 * (length // 8)]))
                        if usize == 0xFFFFFFFF:
                            usize = values.pop(0)
                        if csize == 0xFFFFFFFF:
                            csize = values.pop(0)
                    at += 4 + length
            if flags & 0x8:
                raise zipfile.BadZipFile(f'{self.path}: entries with a data descriptor cannot be recovered')
            if pending:  # a valid header follows the previous entry: it is whole
                self.entries[pending[0]] = pending[1]
            data = offset + 30 + nlen + xlen
            pending = (name, SimpleNamespace(filename=name, file_size=usize, offset=data, csize=csize,
                                             method=method, crc=crc))
            offset = data + csize
        self.recovered_bytes = min(offset, size)

    def infolist(self):
        return list(self.entries.values())

    def namelist(self):
        return list(self.entries)

    def read(self, name):
        entry = self.entries[name]
        self.fp.seek(entry.offset)
        data = self.fp.read(entry.csize)
        if entry.method == 8:
            data = zlib.decompress(data, -15)
        elif entry.method != 0:
            raise zipfile.BadZipFile(f'{name}: compression method {entry.method} not supported')
        if zlib.crc32(data) != entry.crc:
            raise zipfile.BadZipFile(f'{name}: CRC mismatch')
        return data

    def open(self, name):
        return io.BytesIO(self.read(name))

    def close(self):
        self.fp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_archive(path):
    """zipfile.ZipFile, or a SequentialZip of the whole entries when the central directory is lost."""
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        archive = SequentialZip(path)
        if not archive.entries:
            archive.close()
            raise
        return archive


# ----------------------------------------------------------------------------- rasteriser

class TopViewRaster:
    """Z-buffer of the highest surface on a regular sub-grid, with texture colour."""

    def __init__(self, x0, y0, cols, rows, step):
        self.x0, self.y0, self.cols, self.rows, self.step = x0, y0, cols, rows, step
        self.z = np.full(rows * cols, -np.inf)
        self.rgb = np.zeros((rows * cols, 3), dtype=np.uint8)

    def add(self, tri_xyz, tri_uv, image):
        """Rasterise triangles (n,3,3) with UVs (n,3,2); image is an RGB array or None."""
        xy = tri_xyz[:, :, :2]
        # Signed doubled area in the horizontal plane; ~0 for walls and slivers.
        area = ((xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1])
                - (xy[:, 2, 0] - xy[:, 0, 0]) * (xy[:, 1, 1] - xy[:, 0, 1]))
        keep = np.abs(area) > 1e-9
        tri_xyz, tri_uv, xy, area = tri_xyz[keep], tri_uv[keep], xy[keep], area[keep]
        if not len(area):
            return
        s = self.step
        # Index range of sub-cell centres inside each triangle's bounding box.
        c0 = np.ceil((xy[:, :, 0].min(1) - self.x0) / s - .5).astype(np.int64)
        c1 = np.floor((xy[:, :, 0].max(1) - self.x0) / s - .5).astype(np.int64)
        r0 = np.ceil((xy[:, :, 1].min(1) - self.y0) / s - .5).astype(np.int64)
        r1 = np.floor((xy[:, :, 1].max(1) - self.y0) / s - .5).astype(np.int64)
        c0 = np.maximum(c0, 0); r0 = np.maximum(r0, 0)
        c1 = np.minimum(c1, self.cols - 1); r1 = np.minimum(r1, self.rows - 1)
        width = np.maximum(c1 - c0 + 1, 0); height = np.maximum(r1 - r0 + 1, 0)
        counts = width * height
        # Split into batches whose candidate count stays under MAX_CANDIDATES.
        ends = np.cumsum(counts)
        start = 0
        while start < len(counts):
            stop = int(np.searchsorted(ends, (ends[start - 1] if start else 0) + MAX_CANDIDATES, 'right'))
            stop = max(stop, start + 1)
            sl = slice(start, stop)
            self._batch(tri_xyz[sl], tri_uv[sl], area[sl], c0[sl], r0[sl], width[sl], counts[sl], image)
            start = stop

    def _batch(self, tri_xyz, tri_uv, area, c0, r0, width, counts, image):
        total = int(counts.sum())
        if not total:
            return
        tid = np.repeat(np.arange(len(counts)), counts)
        offset = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
        col = c0[tid] + offset % width[tid]
        row = r0[tid] + offset // width[tid]
        px = self.x0 + (col + .5) * self.step
        py = self.y0 + (row + .5) * self.step
        a, b, c = tri_xyz[tid, 0], tri_xyz[tid, 1], tri_xyz[tid, 2]
        # Barycentric weights from horizontal edge functions.
        w0 = ((b[:, 0] - px) * (c[:, 1] - py) - (c[:, 0] - px) * (b[:, 1] - py)) / area[tid]
        w1 = ((c[:, 0] - px) * (a[:, 1] - py) - (a[:, 0] - px) * (c[:, 1] - py)) / area[tid]
        w2 = 1 - w0 - w1
        eps = -1e-9
        inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
        if not inside.any():
            return
        tid, w0, w1, w2 = tid[inside], w0[inside], w1[inside], w2[inside]
        key = row[inside] * self.cols + col[inside]
        z = w0 * tri_xyz[tid, 0, 2] + w1 * tri_xyz[tid, 1, 2] + w2 * tri_xyz[tid, 2, 2]
        # Highest candidate per sub-cell within the batch, then against the z-buffer.
        order = np.lexsort((z, key))
        last = np.r_[key[order][1:] != key[order][:-1], True]
        chosen = order[last]
        chosen = chosen[z[chosen] > self.z[key[chosen]]]
        if not len(chosen):
            return
        k = key[chosen]
        self.z[k] = z[chosen]
        if image is None:
            self.rgb[k] = 100
            return
        t = tid[chosen]
        uv = (w0[chosen, None] * tri_uv[t, 0] + w1[chosen, None] * tri_uv[t, 1]
              + w2[chosen, None] * tri_uv[t, 2])
        bad = ~np.isfinite(uv).all(1)
        uv = np.nan_to_num(uv, nan=0., posinf=1., neginf=0.)
        h, w = image.shape[:2]
        x = np.clip((uv[:, 0] * w).astype(np.int64), 0, w - 1)
        y = np.clip(((1 - uv[:, 1]) * h).astype(np.int64), 0, h - 1)
        self.rgb[k] = image[y, x]
        self.rgb[k[bad]] = 96

    def reduce(self, factor):
        """Average factor x factor sub-cells into output cells."""
        rows, cols = self.rows // factor, self.cols // factor
        z = self.z.reshape(rows, factor, cols, factor)
        valid = np.isfinite(z)
        count = valid.sum((1, 3))
        zsum = np.where(valid, z, 0.).sum((1, 3))
        # Colour sums one sub-cell offset at a time (sums of integers, exact in float64, so the
        # same values as a float64 sum over the whole array) to keep the peak memory low:
        # a 2 km tile at 1 m with supersample 4 has 64 M sub-cells.
        rgb = self.rgb.reshape(rows, factor, cols, factor, 3)
        rgbsum = np.zeros((rows, cols, 3))
        for i in range(factor):
            for j in range(factor):
                rgbsum += rgb[:, i, :, j] * valid[:, i, :, j, None]
        with np.errstate(invalid='ignore', divide='ignore'):
            height = (zsum / count).astype(np.float32)
            colour = np.rint(rgbsum / count[..., None])
        height[count == 0] = np.nan
        colour[count == 0] = 0
        return height, colour.astype(np.uint8), count.astype(np.uint8)


# ----------------------------------------------------------------------------- facades

FACADE_MAX_NZ = 0.3            # faces with |n_z| below this are facades
FACADE_DENSITY = 16.0          # minimum samples per m^2 of facade
MAX_SAMPLES = 4_000_000        # facade samples per vectorised batch (memory bound)


def _lattice(k):
    """Barycentric weights (k*k, 3) of the centroids of the k*k congruent sub-triangles.

    Each sub-triangle has area A / k^2, so the centroids are an area-uniform sample.
    """
    i, j = np.meshgrid(np.arange(k), np.arange(k), indexing='ij')
    up = i + j <= k - 1                      # k (k + 1) / 2 upright sub-triangles
    down = i + j <= k - 2                    # k (k - 1) / 2 inverted ones
    u = np.r_[(i[up] + 1 / 3) / k, (i[down] + 2 / 3) / k]
    v = np.r_[(j[up] + 1 / 3) / k, (j[down] + 2 / 3) / k]
    return np.c_[1 - u - v, u, v]


class FacadeVoxels:
    """Coloured samples of the near-vertical faces, accumulated on a global 3D voxel grid.

    Facades are the faces with |n_z| < 0.3 (this already excludes undersides, n_z < -0.3).
    Each is sampled at the centroids of a k x k barycentric lattice, k = ceil(sqrt(16 area)),
    i.e. >= 16 area-uniform samples per m^2 (at least one per face), and each sample reads the
    full-resolution texel at its interpolated UV. No ray casting.

    Voxel (ix, iy, iz) covers [ix, ix + 1) * voxel in x (same for y, z) in the dataset CRS, so
    tiles of a dataset share one grid. Per voxel: area-weighted mean sRGB colour, area-weighted
    mean of the unit horizontal outward normals (outward = the OBJ winding's face normal), and
    the number of samples.
    """

    def __init__(self, voxel, density=FACADE_DENSITY, max_nz=FACADE_MAX_NZ):
        self.voxel, self.density, self.max_nz = voxel, density, max_nz
        self.parts = []          # per batch: (keys int64 (n, 3), sums float64 (n, 6), counts int64 (n,))
        self.samples = 0
        self.faces = 0
        self.area = 0.

    def add(self, tri_xyz, tri_uv, image):
        """Sample the facade faces among triangles (n,3,3) with UVs (n,3,2); image RGB array or None."""
        e1, e2 = tri_xyz[:, 1] - tri_xyz[:, 0], tri_xyz[:, 2] - tri_xyz[:, 0]
        normal = np.cross(e1, e2)
        norm = np.linalg.norm(normal, axis=1)
        with np.errstate(invalid='ignore', divide='ignore'):
            nz = normal[:, 2] / norm
        keep = (norm > 1e-12) & (np.abs(nz) < self.max_nz)
        if not keep.any():
            return
        tri_xyz, tri_uv, normal, area = tri_xyz[keep], tri_uv[keep], normal[keep], .5 * norm[keep]
        horizontal = normal[:, :2] / np.linalg.norm(normal[:, :2], axis=1, keepdims=True)
        k = np.maximum(1, np.ceil(np.sqrt(self.density * area))).astype(np.int64)
        self.faces += len(area)
        self.area += float(area.sum())
        for kk in np.unique(k):
            lattice = _lattice(int(kk))
            members = np.flatnonzero(k == kk)
            step = max(1, MAX_SAMPLES // len(lattice))
            for start in range(0, len(members), step):
                f = members[start:start + step]
                self._sample(tri_xyz[f], tri_uv[f], horizontal[f], area[f] / kk ** 2, lattice, image)

    def _sample(self, tri_xyz, tri_uv, horizontal, weight, lattice, image):
        n, m = len(tri_xyz), len(lattice)

        def interpolate(corners):
            # a + u (b - a) + v (c - a): exact on axis-aligned facades (no rounding across a voxel face).
            return (corners[:, None, 0] + lattice[None, :, 1, None] * (corners[:, None, 1] - corners[:, None, 0])
                    + lattice[None, :, 2, None] * (corners[:, None, 2] - corners[:, None, 0])).reshape(n * m, -1)
        xyz = interpolate(tri_xyz)
        if image is None:
            rgb = np.full((n * m, 3), 100, dtype=np.float64)
        else:
            uv = interpolate(tri_uv)
            bad = ~np.isfinite(uv).all(1)
            uv = np.nan_to_num(uv, nan=0., posinf=1., neginf=0.)
            h, w = image.shape[:2]
            x = np.clip((uv[:, 0] * w).astype(np.int64), 0, w - 1)
            y = np.clip(((1 - uv[:, 1]) * h).astype(np.int64), 0, h - 1)
            rgb = image[y, x].astype(np.float64)
            rgb[bad] = 96
        keys = np.floor(xyz / self.voxel).astype(np.int64)
        wt = np.repeat(weight, m)
        values = np.c_[rgb * wt[:, None], np.repeat(horizontal, m, axis=0) * wt[:, None], wt]
        self.samples += len(keys)
        self.parts.append(self._reduce(keys, values, np.ones(len(keys), dtype=np.int64)))

    @staticmethod
    def _reduce(keys, values, counts):
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        inverse = inverse.ravel()
        sums = np.stack([np.bincount(inverse, values[:, c], len(unique)) for c in range(values.shape[1])], 1)
        return unique, sums, np.bincount(inverse, counts, len(unique))

    def result(self):
        """Arrays for the cache: ijk int32 (N,3), rgb uint8 (N,3), normal int8 (N,2) (x 127), count uint32 (N,)."""
        if self.parts:
            keys, sums, counts = self._reduce(np.concatenate([p[0] for p in self.parts]),
                                              np.concatenate([p[1] for p in self.parts]),
                                              np.concatenate([p[2] for p in self.parts]))
        else:
            keys, sums, counts = np.zeros((0, 3), np.int64), np.zeros((0, 6)), np.zeros(0, np.int64)
        self.parts = []
        w = sums[:, 5:6]
        rgb = np.rint(sums[:, :3] / np.where(w > 0, w, 1)).clip(0, 255).astype(np.uint8)
        # Mean of unit vectors, not renormalised: its length (<= 1) tells how coherent the facade
        # directions inside the voxel are (opposite faces of a thin wall cancel out).
        normal = np.rint(127 * sums[:, 3:5] / np.where(w > 0, w, 1)).clip(-127, 127).astype(np.int8)
        return dict(facade_ijk=keys.astype(np.int32), facade_rgb=rgb, facade_normal=normal,
                    facade_count=counts.astype(np.uint32), facade_voxel=self.voxel,
                    facade_faces=self.faces, facade_area_m2=self.area, facade_samples=self.samples)


# ----------------------------------------------------------------------------- OBJ reader

LOD_NODE = re.compile(r'^(?P<prefix>.+)_L(?P<level>\d+)(?:_(?P<path>\d+))?$')


def mesh_objs(names, sizes):
    """OBJ entries of an archive that together make the full-resolution mesh, sorted by name.

    In a level-of-detail tree (ContextCapture naming <tile>_L<level>[_<path>].obj, where a child
    node is one level down and adds one digit to its parent's path) only the leaves are kept,
    the nodes without children; the tree structure counts empty files too. Every OBJ whose name
    does not follow that pattern is kept. Empty files are skipped."""
    objs = sorted(n for n in names if n.lower().endswith('.obj'))
    nodes = {}
    for name in objs:
        match = LOD_NODE.match(name[:-4])
        if match:
            nodes[name] = (match['prefix'], int(match['level']), match['path'] or '')
    parents = {(prefix, level - 1, path[:-1]) for prefix, level, path in nodes.values() if path}
    return [n for n in objs if sizes[n] > 0 and (n not in nodes or nodes[n] not in parents)]


def archive_origin(archive, names, origin):
    """Offset to add to the OBJ vertices: the dataset's mesh_origin, or None when it is absent or
    zero. A metadata.xml in the archive (ContextCapture) must declare the same SRSOrigin."""
    declared = None if origin is None else np.asarray(origin, dtype=np.float64)
    for name in names:
        if Path(name).name.lower() != 'metadata.xml':
            continue
        match = re.search(r'<SRSOrigin>([^<]+)</SRSOrigin>', archive.read(name).decode(errors='replace'))
        if match:
            found = np.array([float(x) for x in match.group(1).split(',')])
            if declared is None or found.shape != declared.shape or not np.allclose(found, declared):
                raise ValueError(f'{name} declares SRSOrigin {found.tolist()}, the dataset mesh_origin is '
                                 f'{None if declared is None else declared.tolist()}')
    return declared if declared is not None and declared.any() else None


def mtl_maps(archive, path):
    """{material: texture file name} of one MTL in the archive."""
    maps, material = {}, None
    for line in archive.read(path).decode(errors='replace').splitlines():
        if line.startswith('newmtl '):
            material = line.split(maxsplit=1)[1].strip()
        elif line.startswith('map_Kd ') and material is not None:
            maps[material] = line.split(maxsplit=1)[1].strip()
    return maps


def raster_for(xmin, ymin, xmax, ymax, spacing, supersample):
    """TopViewRaster on the dataset's global grid (corner at multiples of spacing) covering a box."""
    x0 = np.floor(xmin / spacing) * spacing
    y0 = np.floor(ymin / spacing) * spacing
    cols = int(np.ceil((xmax - x0) / spacing))
    rows = int(np.ceil((ymax - y0) / spacing))
    return TopViewRaster(x0, y0, cols * supersample, rows * supersample, spacing / supersample)


def vertex_bounds(archive, objs, offset):
    """(xmin, ymin, xmax, ymax) of the vertices of several OBJs (one extra pass over the 'v' lines)."""
    low, high = np.full(2, np.inf), np.full(2, -np.inf)
    for obj in objs:
        xy = []
        with archive.open(obj) as raw:
            for line in io.TextIOWrapper(raw, encoding='utf-8', errors='replace'):
                if line.startswith('v '):
                    xy.extend(map(float, line.split()[1:3]))
        if xy:
            xy = np.asarray(xy).reshape(-1, 2) + (0 if offset is None else offset[:2])
            low, high = np.minimum(low, xy.min(0)), np.maximum(high, xy.max(0))
    return (*low, *high)


def rasterise_obj_zip(archive_path, spacing, supersample, facade_voxel=None, origin=None, bounds=None):
    """Stream the OBJ files of a ZIP into one TopViewRaster; return the reduced grid.

    The archive may hold one OBJ (Melbourne) or many: the leaves of a level-of-detail tree
    (Helsinki) or a set of sub-tiles (Espoo), see mesh_objs. Each OBJ is read with the MTL of its
    mtllib line (else the first MTL of the archive) and its texture paths are relative to its own
    folder. origin (x, y, z) is added to every vertex (meshes in coordinates local to an origin).
    bounds (xmin, ymin, xmax, ymax), e.g. the tile box from the dataset index, is the box rasterised
    in a single pass (faces outside it are clipped), then cropped to the covered cells; without it
    the grid covers the vertices (one extra pass when there are several OBJs).

    With facade_voxel (metres), the near-vertical faces are also sampled into FacadeVoxels in the
    same pass (same decoded texture), and the facade_* arrays are added to the result. The grid
    is computed exactly as without it.
    """
    with open_archive(archive_path) as archive:
        recovered = isinstance(archive, SequentialZip)
        infos = archive.infolist()
        names = [info.filename for info in infos]
        name_set = set(names)
        objs = mesh_objs(names, {info.filename: info.file_size for info in infos})
        if not objs:
            raise ValueError(f'No OBJ in {archive_path}')
        offset = archive_origin(archive, names, origin)
        first_mtl = next((n for n in names if n.lower().endswith('.mtl')), None)
        if bounds is None and len(objs) > 1:
            bounds = vertex_bounds(archive, objs, offset)
        raster = None if bounds is None else raster_for(*bounds, spacing, supersample)
        facades = FacadeVoxels(facade_voxel) if facade_voxel else None
        faces = 0
        image_cache = {}

        def image_at(path):
            if path not in image_cache:
                image_cache.clear()  # keep one decoded atlas in memory at a time
                image_cache[path] = None
                if path:
                    with Image.open(io.BytesIO(archive.read(path))) as im:
                        image_cache[path] = np.asarray(im.convert('RGB'))
            return image_cache[path]

        for obj in objs:
            folder = str(Path(obj).parent)

            def resolve(name, folder=folder):
                path = name if folder in ('', '.') else folder + '/' + name
                return path if path in name_set else next((n for n in names if n.endswith('/' + name) or n == name), None)

            maps = None
            vertices, uvs, fv, ft = [], [], [], []
            v = uv = material = None

            def flush():
                nonlocal faces
                if not fv:
                    return
                iv = np.asarray(fv, dtype=np.int64).reshape(-1, 3)
                it = np.asarray(ft, dtype=np.int64).reshape(-1, 3)
                tri_uv = uv[it] if len(uv) else np.zeros((len(iv), 3, 2))
                image = image_at(resolve(maps[material]) if material in maps else None)
                raster.add(v[iv], tri_uv, image)
                if facades is not None:
                    facades.add(v[iv], tri_uv, image)
                faces += len(iv)
                fv.clear(); ft.clear()

            with archive.open(obj) as raw:
                for line in io.TextIOWrapper(raw, encoding='utf-8', errors='replace'):
                    if line.startswith('v '):
                        vertices.extend(map(float, line.split()[1:4]))
                    elif line.startswith('vt '):
                        uvs.extend(map(float, line.split()[1:3]))
                    elif line.startswith('mtllib ') and maps is None:
                        path = resolve(line.split(maxsplit=1)[1].strip())
                        if path:
                            maps = mtl_maps(archive, path)
                    elif line.startswith(('f ', 'usemtl ')):
                        if v is None:
                            # Like the published photomeshes, all vertices precede the faces.
                            v = np.asarray(vertices).reshape(-1, 3)
                            if offset is not None:
                                v = v + offset
                            uv = np.asarray(uvs).reshape(-1, 2)
                            vertices = uvs = None
                            if maps is None:
                                maps = mtl_maps(archive, first_mtl) if first_mtl else {}
                            if raster is None:
                                raster = raster_for(v[:, 0].min(), v[:, 1].min(), v[:, 0].max(), v[:, 1].max(),
                                                    spacing, supersample)
                        if line.startswith('usemtl '):
                            flush()
                            material = line.split(maxsplit=1)[1].strip()
                            continue
                        fields = line.split()[1:]
                        for k in range(1, len(fields) - 1):  # fan-triangulate polygons
                            for field in (fields[0], fields[k], fields[k + 1]):
                                parts = field.split('/')
                                fv.append(int(parts[0]) - 1)
                                ft.append(int(parts[1]) - 1 if len(parts) > 1 and parts[1] else 0)
                        if len(fv) >= 3 * FACE_BATCH:
                            flush()
            if v is not None:
                flush()
        if raster is None:
            raise ValueError(f'No faces in {archive_path}')
    height, rgb, coverage = raster.reduce(supersample)
    x0, y0 = raster.x0, raster.y0
    if bounds is not None:
        # Crop the box to the covered cells, as a grid built from the vertices would be: the render
        # grid fills empty cells from the nearest covered one, which must not spread over a tile
        # that the mesh covers only in part (coast, edge of the dataset).
        rows = np.flatnonzero(coverage.any(1))
        cols = np.flatnonzero(coverage.any(0))
        if len(rows):
            r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
            height, rgb, coverage = height[r0:r1, c0:c1], rgb[r0:r1, c0:c1], coverage[r0:r1, c0:c1]
            x0, y0 = x0 + c0 * spacing, y0 + r0 * spacing
    result = dict(height=height, rgb=rgb, coverage=coverage, x0=x0, y0=y0, faces=faces, objs=len(objs))
    if recovered:  # truncated archive read entry by entry: only its whole entries are in the grid
        result['recovered_entries'] = len(infos)
    if facades is not None:
        result.update(facades.result())
    return result


READERS = {'obj_zip': rasterise_obj_zip}
FACADE_KEYS = ('facade_ijk', 'facade_rgb', 'facade_normal', 'facade_count', 'facade_voxel',
               'facade_faces', 'facade_area_m2', 'facade_samples')


def tile_kind(path):
    """How a cached tile was built: (spacing, supersample, facade voxel size or None)."""
    with np.load(path) as data:
        voxel = float(data['facade_voxel']) if 'facade_voxel' in data.files else None
        return float(data['spacing']), int(data['supersample']), voxel


def describe_kind(kind):
    spacing, supersample, voxel = kind
    facades = f'facades {voxel:g} m' if voxel is not None else 'no facades'
    return f'spacing {spacing:g} m, supersample {supersample}, {facades}'


def check_folder(output_dir, kind):
    """Refuse a folder holding any tile built differently from kind (see tile_kind), so a cache never
    mixes grid-only and facade tiles, spacings, supersampling or voxel sizes."""
    found = {}
    for path in sorted(output_dir.glob('*.npz')):
        found.setdefault(tile_kind(path), []).append(path.stem)
    other = {k: v for k, v in found.items() if not (abs(k[0] - kind[0]) < 1e-9 and k[1] == kind[1] and (
        (k[2] is None) == (kind[2] is None) and (k[2] is None or abs(k[2] - kind[2]) < 1e-9)))}
    if other:
        lines = [f'  {len(v)} tiles with {describe_kind(k)} (e.g. {v[:3]})' for k, v in other.items()]
        raise SystemExit(f'{output_dir} already holds tiles built differently from this request '
                         f'({describe_kind(kind)}):\n' + '\n'.join(lines)
                         + '\nA cache folder holds one kind of tile: use another --output or --tag.')


# ----------------------------------------------------------------------------- one tile

def process_tile(dataset_name, dataset, tile, output_dir, spacing, supersample, archives, keep,
                 facade_voxel=None, bounds=None):
    """Download (or reuse) one archive, rasterise it, write the cache, drop the archive.

    bounds (xmin, ymin, xmax, ymax) fixes the grid extent (dataset raster_bounds 'tile')."""
    start = time.monotonic()
    destination = output_dir / f'{tile}.npz'
    url = dataset['url'].format(tile=tile)
    local = None
    if archives:  # an already downloaded archive: <tile>.zip or the file name of its URL
        names = (f'{tile}.zip', url.rsplit('/', 1)[-1].split('?', 1)[0])
        local = next((archives / n for n in names if (archives / n).exists()), None)
    if local is not None:
        archive, downloaded, owned = local, 0, False
    else:
        archive = output_dir / 'downloads' / f'{tile}.zip'
        archive.parent.mkdir(exist_ok=True)
        downloaded = download(url, archive)
        owned = True
    archive_bytes = archive.stat().st_size
    t_download = time.monotonic() - start
    try:
        extra = dict(origin=dataset.get('mesh_origin'), bounds=bounds)
        if facade_voxel:
            extra['facade_voxel'] = facade_voxel
        grid = READERS[dataset['format']](archive, spacing, supersample, **extra)
    finally:
        if owned and not keep:
            archive.unlink(missing_ok=True)
    facade = {k: grid[k] for k in FACADE_KEYS} if facade_voxel else {}
    temporary = destination.with_suffix('.npz.part')
    with temporary.open('wb') as stream:
        np.savez_compressed(stream, height=grid['height'], rgb=grid['rgb'], coverage=grid['coverage'],
                            x0=grid['x0'], y0=grid['y0'], spacing=spacing, supersample=supersample,
                            epsg=dataset['projected_epsg'], dataset=dataset_name, tile=tile, **facade)
    temporary.replace(destination)
    valid = np.isfinite(grid['height'])
    record = dict(tile=tile, shape=list(grid['height'].shape), faces=grid['faces'], objs=grid.get('objs'),
                  recovered_entries=grid.get('recovered_entries'),
                  covered_fraction=float(valid.mean()), archive_bytes=archive_bytes,
                  downloaded_bytes=downloaded, cache_bytes=destination.stat().st_size,
                  download_s=round(t_download, 1), total_s=round(time.monotonic() - start, 1))
    if facade_voxel:
        record.update(facade_voxel=facade_voxel, facade_voxels=int(len(grid['facade_ijk'])),
                      facade_faces=int(grid['facade_faces']), facade_area_m2=round(float(grid['facade_area_m2']), 1),
                      facade_samples=int(grid['facade_samples']))
    print(f"{tile}: {record['shape']}, covered {record['covered_fraction']:.1%}, "
          f"{archive_bytes/1e6:.0f} MB -> {record['cache_bytes']/1e6:.1f} MB, {record['total_s']} s"
          + (f", {record['facade_voxels']:,} facade voxels" if facade_voxel else '')
          + (f", TRUNCATED archive: {record['recovered_entries']:,} whole entries recovered"
             if record['recovered_entries'] else ''), flush=True)
    return record


# ----------------------------------------------------------------------------- tile selection

def grid_index(spec):
    """{tile_id: square polygon (5, 2)} of an index of type 'grid': the ids in spec['tiles'] name the
    south-west corner, x = x_origin_m + id_unit_m * <x>, y = y_origin_m + id_unit_m * <y>, with <x>
    and <y> the named groups of id_regex; each tile is tile_size_m on a side."""
    pattern, size, unit = re.compile(spec['id_regex']), spec['tile_size_m'], spec['id_unit_m']
    index = {}
    for tile in spec['tiles']:
        match = pattern.match(tile)
        if not match:
            raise ValueError(f'tile id {tile!r} does not match {spec["id_regex"]}')
        x = spec['x_origin_m'] + unit * int(match['x'])
        y = spec['y_origin_m'] + unit * int(match['y'])
        index[tile] = np.array([[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]], dtype=np.float64)
    return index


def tile_index(dataset, root=REPO):
    """Return {tile_id: polygon (n,2) in the projected CRS} from the dataset index.

    Index types: 'arcgis' (ArcGIS JSON query with polygon geometry.rings and the id in
    attributes[id_field]; local_copy is used instead of the url when present, and written after a
    download) and 'grid' (regular square tiles listed in the entry, see grid_index)."""
    spec = dataset['index']
    if spec.get('type') == 'grid':
        return grid_index(spec)
    path = root / spec['local_copy'] if spec.get('local_copy') else None
    if path is not None and path.exists():
        payload = json.loads(path.read_text())
    else:
        with urllib.request.urlopen(spec['url'], timeout=120) as response:
            payload = json.loads(response.read())
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
    return {f['attributes'][spec['id_field']]: np.asarray(f['geometry']['rings'][0])
            for f in payload['features']}


def tile_bounds(dataset, tiles):
    """{tile: (xmin, ymin, xmax, ymax)} of the index polygons when the dataset's raster_bounds is
    'tile' (archives of several OBJs cut at the tile edges), else {} (grid from the vertices)."""
    if dataset.get('raster_bounds', 'vertices') != 'tile':
        return {}
    index = tile_index(dataset)
    return {t: tuple(float(c) for c in (*index[t].min(0), *index[t].max(0))) for t in tiles}


def describe_dataset(name, dataset, dataset_dir, root=REPO):
    """Write <output>/<dataset>/dataset.json and tile_grid.json (the index of the tiles).

    tile_grid.json is copied from the index local_copy if present, otherwise downloaded;
    a failed download only prints a warning, since the tiles do not depend on it.
    """
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / 'dataset.json').write_text(json.dumps(dict(name=name, **dataset), indent=2) + '\n')
    grid = dataset_dir / 'tile_grid.json'
    if grid.exists():
        return
    spec = dataset.get('index', {})
    local = root / spec['local_copy'] if spec.get('local_copy') else None
    try:
        if spec.get('type') == 'grid':  # same layout as an ArcGIS query, attribute 'tile'
            features = [dict(attributes=dict(tile=t), geometry=dict(rings=[ring.tolist()]))
                        for t, ring in grid_index(spec).items()]
            grid.write_text(json.dumps(dict(features=features)))
        elif local is not None and local.exists():
            shutil.copyfile(local, grid)
        elif spec.get('url'):
            download(spec['url'], grid)
    except Exception as error:
        print(f'warning: tile index not saved ({error!r})', flush=True)


def rebuild_log(output_dir):
    """Add a log.jsonl line for every .npz in output_dir that has none; return (kept, added).

    Existing lines are kept unchanged and in order. Rebuilt lines hold what the .npz still
    tells (shape, covered fraction, cache bytes, faces = null); download and timing fields are null.
    """
    log = output_dir / 'log.jsonl'
    lines = log.read_text().splitlines() if log.exists() else []
    logged = {json.loads(line)['tile'] for line in lines if line.strip()}
    added = []
    for path in sorted(output_dir.glob('*.npz')):
        tile = path.stem
        if tile in logged:
            continue
        with np.load(path) as data:
            height = data['height']
        added.append(dict(tile=tile, shape=list(height.shape), faces=None,
                          covered_fraction=float(np.isfinite(height).mean()), archive_bytes=None,
                          downloaded_bytes=None, cache_bytes=path.stat().st_size,
                          download_s=None, total_s=None, rebuilt=True))
    if added:
        with log.open('a') as stream:
            for record in added:
                stream.write(json.dumps(record) + '\n')
    return len(lines), len(added)


def target_lon_lat(dataset):
    """Geographic lon/lat in degrees of the dataset's target point, the scene point overflown at
    capture time (passed to the orbit simulation as --target).

    dataset['target']['projected_xy'] is in the dataset's projected CRS; the result is in its
    geographic CRS."""
    from pyproj import Transformer
    if 'target' not in dataset:
        raise KeyError("the dataset entry has no 'target' (projected_xy of the scene point)")
    x, y = dataset['target']['projected_xy']
    return Transformer.from_crs(dataset['projected_epsg'], dataset['geographic_epsg'],
                                always_xy=True).transform(x, y)


def run_tiles(dataset, manifest, root=REPO):
    """Tile ids of a simulation run (manifest = its cameras.json).

    manifest['selected_tiles'] if present (runs made with melbourne_overpass.py --grid); otherwise
    every tile of the dataset index whose footprint, taken vertex by vertex to the run's local ENU
    frame, overlaps the box manifest['terrain_bounds_local_m'] (the same test as --grid).
    Tile vertices: projected CRS -> geographic CRS -> ECEF at zero ellipsoidal height
    (EPSG:4979 -> 4978) -> local frame (origin_ecef_m, local_from_ecef)."""
    if 'selected_tiles' in manifest:
        return list(manifest['selected_tiles'])
    from pyproj import Transformer
    to_geo = Transformer.from_crs(dataset['projected_epsg'], dataset['geographic_epsg'], always_xy=True)
    to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    origin = np.asarray(manifest['origin_ecef_m'])
    local_from_ecef = np.asarray(manifest['local_from_ecef'])
    low, high = (np.asarray(b) for b in manifest['terrain_bounds_local_m'])
    selected = []
    for tile, ring in tile_index(dataset, root).items():
        lon, lat = to_geo.transform(ring[:, 0], ring[:, 1])
        ecef = np.stack(to_ecef.transform(lon, lat, np.zeros_like(lon)), axis=-1)
        local = ((ecef - origin) @ local_from_ecef.T)[:, :2]
        if np.all(local.max(0) >= low) and np.all(local.min(0) <= high):
            selected.append(tile)
    return sorted(selected)


def select_tiles(args, dataset):
    if args.tiles:
        return list(args.tiles)
    if args.tiles_from:
        return run_tiles(dataset, json.loads(args.tiles_from.read_text()))
    index = tile_index(dataset)
    if args.all:
        return sorted(index)
    x0, y0, x1, y1 = args.bbox
    return sorted(t for t, ring in index.items()
                  if ring[:, 0].max() >= x0 and ring[:, 0].min() <= x1
                  and ring[:, 1].max() >= y0 and ring[:, 1].min() <= y1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', required=True, help='key in terrain_datasets.json')
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--rebuild-log', action='store_true',
                       help='only add log.jsonl lines for cached tiles that have none, then exit')
    which.add_argument('--tiles', nargs='+', help='explicit tile ids')
    which.add_argument('--tiles-from', type=Path,
                       help="cameras.json of a run: its 'selected_tiles', or the index tiles inside its terrain bounds")
    which.add_argument('--bbox', type=float, nargs=4, metavar=('XMIN', 'YMIN', 'XMAX', 'YMAX'),
                       help='tiles intersecting a box in the dataset projected CRS')
    which.add_argument('--all', action='store_true', help='every tile in the dataset index')
    parser.add_argument('--output', type=Path, default=REPO / 'data' / 'terrain',
                        help='root folder; tiles go to <output>/<dataset>/<spacing>m/')
    parser.add_argument('--spacing', type=float, default=1.0, help='output cell size in metres')
    parser.add_argument('--supersample', type=int, default=4, help='sub-cells per output cell along each axis')
    parser.add_argument('--archives', type=Path, help='directory with already downloaded ZIPs to reuse')
    parser.add_argument('--keep-archives', action='store_true', help='do not delete downloaded ZIPs')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--limit', type=int, help='process only the first N selected tiles')
    parser.add_argument('--dry-run', action='store_true', help='print the selection and the estimate, do nothing')
    parser.add_argument('--no-facades', action='store_true',
                        help='grid only: do not store the facade voxels (terrain_to_render.py --walls needs them)')
    parser.add_argument('--facade-voxel', type=float, default=1.0, help='facade voxel size in metres (default 1)')
    parser.add_argument('--tag', help='suffix of the tile folder, <spacing>m_<tag>/ (not used by the pipeline)')
    parser.add_argument('--datasets', type=Path, help='terrain_datasets.json to use (default: the repository one)')
    args = parser.parse_args()

    datasets = load_datasets(args.datasets) if args.datasets else load_datasets()
    dataset = datasets[args.dataset]
    members = dataset.get('members')
    if members and args.tiles:
        raise SystemExit(f'{args.dataset} is composite ({", ".join(members)}): select its tiles with '
                         '--tiles-from, --bbox or --all, or cache a member with --dataset <member> --tiles')
    failures = []
    for name in members or [args.dataset]:  # a composite dataset caches each member in its own folder
        failures += cache_dataset(name, datasets[name], args)
    if failures:
        raise SystemExit(f'{len(failures)} tiles failed: {failures}')


def cache_dataset(name, dataset, args):
    """Cache the selected tiles of one (non-composite) dataset; return the tiles that failed."""
    output_dir = args.output / name / (f'{args.spacing:g}m' + (f'_{args.tag}' if args.tag else ''))
    if args.rebuild_log:
        kept, added = rebuild_log(output_dir)
        print(f'{output_dir / "log.jsonl"}: {kept} lines kept, {added} rebuilt', flush=True)
        return []
    tiles = select_tiles(args, dataset)[:args.limit]
    facade_voxel = None if args.no_facades else args.facade_voxel
    # Cached tiles are reused as they are: check every tile already in the folder (not only the
    # selected ones) before adding to it, so the folder never mixes tile kinds.
    if output_dir.exists():
        check_folder(output_dir, (args.spacing, args.supersample, facade_voxel))
    output_dir.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tiles if not (output_dir / f'{t}.npz').exists()]
    print(f'{name}: {len(tiles)} tiles selected, {len(tiles) - len(todo)} cached, '
          f'{len(todo)} to process -> {output_dir}', flush=True)
    if args.dry_run:
        for t in todo:
            print(t)
        return []
    describe_dataset(name, dataset, args.output / name)
    log = output_dir / 'log.jsonl'
    failures = []
    bounds = tile_bounds(dataset, todo)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(process_tile, name, dataset, t, output_dir, args.spacing,
                            args.supersample, args.archives, args.keep_archives,
                            facade_voxel, bounds.get(t)): t for t in todo}
        for job in as_completed(jobs):  # log each tile when it completes, so an interrupted run loses no record
            tile = jobs[job]
            try:
                record = job.result()
                with log.open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
            except Exception as error:  # keep going; failed tiles are retried on the next run
                failures.append(f'{name}/{tile}')
                print(f'{tile}: FAILED {error!r}', flush=True)
    return failures


if __name__ == '__main__':
    main()
