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

Example (Melbourne, tiles chosen by an existing simulation):
  python terrain_cache.py --dataset melbourne_2020 --spacing 1.0 \\
      --tiles-from output/.../simulation/cameras.json --output data/terrain_cache --workers 4
"""
import argparse
import io
import json
import shutil
import time
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # photomesh atlases can exceed Pillow's default guard
HERE = Path(__file__).resolve().parent
MAX_CANDIDATES = 4_000_000     # sub-cell candidates per vectorised batch (memory bound)
FACE_BATCH = 200_000           # triangles accumulated before a rasterisation pass


def load_datasets(path=HERE/'terrain_datasets.json'):
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


# ----------------------------------------------------------------------------- OBJ reader

def rasterise_obj_zip(archive_path, spacing, supersample):
    """Stream the OBJ inside a ZIP into a TopViewRaster; return the reduced grid."""
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
            raster.add(v[iv], tri_uv, image_for(material))
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
    return dict(height=height, rgb=rgb, coverage=coverage, x0=raster.x0, y0=raster.y0, faces=faces)


READERS = {'obj_zip': rasterise_obj_zip}


# ----------------------------------------------------------------------------- one tile

def process_tile(dataset_name, dataset, tile, output_dir, spacing, supersample, archives, keep):
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
        grid = READERS[dataset['format']](archive, spacing, supersample)
    finally:
        if owned and not keep:
            archive.unlink(missing_ok=True)
    temporary = destination.with_suffix('.npz.part')
    with temporary.open('wb') as stream:
        np.savez_compressed(stream, height=grid['height'], rgb=grid['rgb'], coverage=grid['coverage'],
                            x0=grid['x0'], y0=grid['y0'], spacing=spacing, supersample=supersample,
                            epsg=dataset['projected_epsg'], dataset=dataset_name, tile=tile)
    temporary.replace(destination)
    valid = np.isfinite(grid['height'])
    record = dict(tile=tile, shape=list(grid['height'].shape), faces=grid['faces'],
                  covered_fraction=float(valid.mean()), archive_bytes=archive_bytes,
                  downloaded_bytes=downloaded, cache_bytes=destination.stat().st_size,
                  download_s=round(t_download, 1), total_s=round(time.monotonic() - start, 1))
    print(f"{tile}: {record['shape']}, covered {record['covered_fraction']:.1%}, "
          f"{archive_bytes/1e6:.0f} MB -> {record['cache_bytes']/1e6:.1f} MB, {record['total_s']} s", flush=True)
    return record


# ----------------------------------------------------------------------------- tile selection

def tile_index(dataset, root=HERE):
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


def select_tiles(args, dataset):
    if args.tiles:
        return list(args.tiles)
    if args.tiles_from:
        return list(json.loads(args.tiles_from.read_text())['selected_tiles'])
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
    which.add_argument('--tiles', nargs='+', help='explicit tile ids')
    which.add_argument('--tiles-from', type=Path, help="cameras.json with 'selected_tiles'")
    which.add_argument('--bbox', type=float, nargs=4, metavar=('XMIN', 'YMIN', 'XMAX', 'YMAX'),
                       help='tiles intersecting a box in the dataset projected CRS')
    which.add_argument('--all', action='store_true', help='every tile in the dataset index')
    parser.add_argument('--output', type=Path, default=HERE / 'data' / 'terrain_cache')
    parser.add_argument('--spacing', type=float, default=1.0, help='output cell size in metres')
    parser.add_argument('--supersample', type=int, default=4, help='sub-cells per output cell along each axis')
    parser.add_argument('--archives', type=Path, help='directory with already downloaded ZIPs to reuse')
    parser.add_argument('--keep-archives', action='store_true', help='do not delete downloaded ZIPs')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--limit', type=int, help='process only the first N selected tiles')
    parser.add_argument('--dry-run', action='store_true', help='print the selection and the estimate, do nothing')
    args = parser.parse_args()

    dataset = load_datasets()[args.dataset]
    tiles = select_tiles(args, dataset)[:args.limit]
    output_dir = args.output / args.dataset / f'{args.spacing:g}m'
    output_dir.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tiles if not (output_dir / f'{t}.npz').exists()]
    print(f'{args.dataset}: {len(tiles)} tiles selected, {len(tiles) - len(todo)} cached, '
          f'{len(todo)} to process -> {output_dir}', flush=True)
    if args.dry_run:
        for t in todo:
            print(t)
        return
    log = output_dir / 'log.jsonl'
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(process_tile, args.dataset, dataset, t, output_dir, args.spacing,
                            args.supersample, args.archives, args.keep_archives): t for t in todo}
        for job, tile in jobs.items():
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
