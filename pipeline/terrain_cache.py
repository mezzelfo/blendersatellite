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
import shutil
import time
import urllib.request
import zipfile
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
    """Stream url to destination atomically; return the number of bytes written."""
    temporary = destination.with_suffix(destination.suffix + '.part')
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as out:
                shutil.copyfileobj(response, out, chunk)
            temporary.replace(destination)
            return destination.stat().st_size
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == retries:
                raise
            time.sleep(5 * attempt)


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
        rgb = self.rgb.reshape(rows, factor, cols, factor, 3).astype(np.float64)
        rgbsum = (rgb * valid[..., None]).sum((1, 3))
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

def rasterise_obj_zip(archive_path, spacing, supersample, facade_voxel=None):
    """Stream the OBJ inside a ZIP into a TopViewRaster; return the reduced grid.

    With facade_voxel (metres), the near-vertical faces are also sampled into FacadeVoxels in the
    same pass (same decoded texture), and the facade_* arrays are added to the result. The grid
    is computed exactly as without it.
    """
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        obj = next(n for n in names if n.lower().endswith('.obj'))
        mtl = next((n for n in names if n.lower().endswith('.mtl')), None)
        folder = str(Path(obj).parent)
        maps = {}
        material = None
        if mtl:
            for line in archive.read(mtl).decode(errors='replace').splitlines():
                if line.startswith('newmtl '):
                    material = line.split(maxsplit=1)[1].strip()
                elif line.startswith('map_Kd ') and material is not None:
                    maps[material] = line.split(maxsplit=1)[1].strip()

        def texture_path(name):
            path = name if folder in ('', '.') else folder + '/' + name
            return path if path in names else next((n for n in names if n.endswith('/' + name) or n == name), None)

        vertices, uvs, fv, ft = [], [], [], []
        v = uv = raster = None
        material, image_cache = None, {}
        faces = 0
        facades = FacadeVoxels(facade_voxel) if facade_voxel else None

        def image_for(mat):
            if mat not in image_cache:
                image_cache.clear()  # keep one decoded atlas in memory at a time
                path = texture_path(maps[mat]) if mat in maps else None
                image_cache[mat] = None
                if path:
                    with Image.open(io.BytesIO(archive.read(path))) as im:
                        image_cache[mat] = np.asarray(im.convert('RGB'))
            return image_cache[mat]

        def flush():
            nonlocal faces
            if not fv:
                return
            iv = np.asarray(fv, dtype=np.int64).reshape(-1, 3)
            it = np.asarray(ft, dtype=np.int64).reshape(-1, 3)
            tri_uv = uv[it] if len(uv) else np.zeros((len(iv), 3, 2))
            image = image_for(material)
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
                elif line.startswith(('f ', 'usemtl ')):
                    if v is None:
                        # Like the published photomeshes, all vertices precede the faces.
                        v = np.asarray(vertices).reshape(-1, 3)
                        uv = np.asarray(uvs).reshape(-1, 2)
                        vertices = uvs = None
                        x0 = np.floor(v[:, 0].min() / spacing) * spacing
                        y0 = np.floor(v[:, 1].min() / spacing) * spacing
                        cols = int(np.ceil((v[:, 0].max() - x0) / spacing))
                        rows = int(np.ceil((v[:, 1].max() - y0) / spacing))
                        raster = TopViewRaster(x0, y0, cols * supersample, rows * supersample,
                                               spacing / supersample)
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
        if raster is None:
            raise ValueError(f'No faces in {archive_path}')
        flush()
    height, rgb, coverage = raster.reduce(supersample)
    result = dict(height=height, rgb=rgb, coverage=coverage, x0=raster.x0, y0=raster.y0, faces=faces)
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
                 facade_voxel=None):
    """Download (or reuse) one archive, rasterise it, write the cache, drop the archive."""
    start = time.monotonic()
    destination = output_dir / f'{tile}.npz'
    local = archives / f'{tile}.zip' if archives else None
    if local is not None and local.exists():
        archive, downloaded, owned = local, 0, False
    else:
        archive = output_dir / 'downloads' / f'{tile}.zip'
        archive.parent.mkdir(exist_ok=True)
        downloaded = download(dataset['url'].format(tile=tile), archive)
        owned = True
    archive_bytes = archive.stat().st_size
    t_download = time.monotonic() - start
    try:
        if facade_voxel:
            grid = READERS[dataset['format']](archive, spacing, supersample, facade_voxel=facade_voxel)
        else:
            grid = READERS[dataset['format']](archive, spacing, supersample)
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
    record = dict(tile=tile, shape=list(grid['height'].shape), faces=grid['faces'],
                  covered_fraction=float(valid.mean()), archive_bytes=archive_bytes,
                  downloaded_bytes=downloaded, cache_bytes=destination.stat().st_size,
                  download_s=round(t_download, 1), total_s=round(time.monotonic() - start, 1))
    if facade_voxel:
        record.update(facade_voxel=facade_voxel, facade_voxels=int(len(grid['facade_ijk'])),
                      facade_faces=int(grid['facade_faces']), facade_area_m2=round(float(grid['facade_area_m2']), 1),
                      facade_samples=int(grid['facade_samples']))
    print(f"{tile}: {record['shape']}, covered {record['covered_fraction']:.1%}, "
          f"{archive_bytes/1e6:.0f} MB -> {record['cache_bytes']/1e6:.1f} MB, {record['total_s']} s"
          + (f", {record['facade_voxels']:,} facade voxels" if facade_voxel else ''), flush=True)
    return record


# ----------------------------------------------------------------------------- tile selection

def tile_index(dataset, root=REPO):
    """Return {tile_id: polygon (n,2) in the projected CRS} from the dataset index."""
    spec = dataset['index']
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
        if local is not None and local.exists():
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
    args = parser.parse_args()

    dataset = load_datasets()[args.dataset]
    output_dir = args.output / args.dataset / (f'{args.spacing:g}m' + (f'_{args.tag}' if args.tag else ''))
    if args.rebuild_log:
        kept, added = rebuild_log(output_dir)
        print(f'{output_dir / "log.jsonl"}: {kept} lines kept, {added} rebuilt', flush=True)
        return
    tiles = select_tiles(args, dataset)[:args.limit]
    facade_voxel = None if args.no_facades else args.facade_voxel
    # Cached tiles are reused as they are: check every tile already in the folder (not only the
    # selected ones) before adding to it, so the folder never mixes tile kinds.
    if output_dir.exists():
        check_folder(output_dir, (args.spacing, args.supersample, facade_voxel))
    output_dir.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tiles if not (output_dir / f'{t}.npz').exists()]
    print(f'{args.dataset}: {len(tiles)} tiles selected, {len(tiles) - len(todo)} cached, '
          f'{len(todo)} to process -> {output_dir}', flush=True)
    if args.dry_run:
        for t in todo:
            print(t)
        return
    describe_dataset(args.dataset, dataset, args.output / args.dataset)
    log = output_dir / 'log.jsonl'
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(process_tile, args.dataset, dataset, t, output_dir, args.spacing,
                            args.supersample, args.archives, args.keep_archives,
                            facade_voxel): t for t in todo}
        for job in as_completed(jobs):  # log each tile when it completes, so an interrupted run loses no record
            tile = jobs[job]
            try:
                record = job.result()
                with log.open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
            except Exception as error:  # keep going; failed tiles are retried on the next run
                failures.append(tile)
                print(f'{tile}: FAILED {error!r}', flush=True)
    if failures:
        raise SystemExit(f'{len(failures)} tiles failed: {failures}')


if __name__ == '__main__':
    main()
