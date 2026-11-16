"""Part A.1-A.3 of the step-20 study: what is inside one photomesh tile archive.

  python -I analyze_tile.py --study STUDY --tile D4-04

Reads STUDY/archives/<tile>.zip (file list) and STUDY/extracted/<tile>/ (OBJ, MTL, JPG), caches the
parsed OBJ in STUDY/work/parsed/, and writes STUDY/analysis/<tile>.json with:
  A.1 file counts by type and level-of-detail indicators found in names and folders;
  A.2 vertices, faces, textures (count, pixel sizes, megapixels), texel size on the ground
      = median over faces of sqrt(3D face area in m^2 / face area in texels);
  A.3 unique-edge length percentiles, fraction of faces and of area with |n_z| < 0.3, UV islands.
"""
import argparse
import json
import re
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objtile  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
LOD_PATTERNS = {'L<nn>': re.compile(r'(^|[^A-Za-z])L\d{2}([^0-9]|$)'), 'LOD': re.compile(r'lod', re.I),
                'level': re.compile(r'level', re.I)}


def file_summary(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
    names = [i.filename for i in infos if not i.is_dir()]
    ext = Counter(Path(n).suffix.lower() for n in names)
    folders = sorted({str(Path(n).parent) for n in names})
    lod_hits = {k: sorted({n for n in names + folders if p.search(Path(n).name)})[:5] for k, p in LOD_PATTERNS.items()}
    # PhotoMesh texture names: <prefix>_Tile-<x>-<y>-<a>-<b>_m-<material>.jpg; report the distinct
    # prefixes and the distinct (a, b) fields, the only fields that could carry a level.
    tile_fields = Counter()
    prefixes = Counter()
    for n in names:
        m = re.search(r'([A-Za-z0-9_]+?)_?Tile-([-+]?\d+)-([-+]?\d+)-(\d+)-(\d+)', Path(n).name)
        if m:
            prefixes[m.group(1)] += 1
            tile_fields[f'{m.group(4)}-{m.group(5)}'] += 1
    return dict(files=len(names), by_extension=dict(ext), folders=folders,
                uncompressed_bytes=sum(i.file_size for i in infos), archive_bytes=zip_path.stat().st_size,
                lod_name_hits=lod_hits, texture_name_prefixes=dict(prefixes),
                texture_tile_level_fields=dict(tile_fields))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--tile', required=True)
    args = parser.parse_args()
    t0 = time.monotonic()
    out = dict(tile=args.tile)
    out['A1'] = file_summary(args.study / 'archives' / f'{args.tile}.zip')
    folder = args.study / 'extracted' / args.tile
    obj = next(folder.glob('*.obj'))
    mesh = objtile.load(obj, args.study / 'work' / 'parsed')
    t_parse = time.monotonic() - t0
    v, vt, fv, ft, fmat, maps = mesh['v'], mesh['vt'], mesh['fv'], mesh['ft'], mesh['fmat'], mesh['maps']
    # Texture sizes (header only).
    sizes = {}
    for jpg in sorted(folder.glob('*.jpg')):
        with Image.open(jpg) as im:
            sizes[jpg.name] = im.size
    used = sorted({m for m in maps[np.unique(fmat)] if m})
    size_counts = Counter(f'{w}x{h}' for w, h in sizes.values())
    total_px = sum(w * h for w, h in sizes.values())
    used_px = sum(sizes[m][0] * sizes[m][1] for m in used if m in sizes)
    # Texel size on the ground.
    area, normal = objtile.face_geometry(v, fv)
    wh = np.array([sizes.get(m, (0, 0)) for m in maps], dtype=np.float64)[fmat]
    uv = vt[ft]
    uv_area = 0.5 * np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1])
                           - (uv[:, 2, 0] - uv[:, 0, 0]) * (uv[:, 1, 1] - uv[:, 0, 1]))
    texels = uv_area * wh[:, 0] * wh[:, 1]
    ok = (texels > 0) & (area > 0)
    texel_m = np.sqrt(area[ok] / texels[ok])
    out['A2'] = dict(vertices=int(len(v)), faces=int(len(fv)), texcoords=int(len(vt)),
                     materials=int(len(maps)), materials_used=int(len(np.unique(fmat))),
                     textures=len(sizes), textures_used=len(used), texture_sizes=dict(size_counts),
                     texture_megapixels=total_px / 1e6, texture_megapixels_used=used_px / 1e6,
                     jpg_bytes=sum((folder / n).stat().st_size for n in sizes),
                     obj_bytes=obj.stat().st_size,
                     texel_m_median=float(np.median(texel_m)),
                     texel_m_p5_p95=[float(x) for x in np.percentile(texel_m, [5, 95])],
                     texel_m_area_weighted_median=float(np.sqrt(np.sum(area[ok]) / np.sum(texels[ok]))),
                     faces_zero_uv_area=int((~ok).sum()),
                     bbox=[v.min(0).tolist(), v.max(0).tolist()])
    # Geometry statistics.
    edges = np.sort(np.concatenate([fv[:, [0, 1]], fv[:, [1, 2]], fv[:, [2, 0]]]), axis=1)
    edges = np.unique(edges.astype(np.int64)[:, 0] * len(v) + edges[:, 1])
    a, b = edges // len(v), edges % len(v)
    length = np.linalg.norm(v[a] - v[b], axis=1)
    pct = [1, 5, 25, 50, 75, 95, 99]
    nz = np.abs(normal[:, 2])
    valid = np.isfinite(nz)
    vertical = valid & (nz < 0.3)
    islands, _ = objtile.uv_islands(vt, ft, fmat)
    out['A3'] = dict(unique_edges=int(len(edges)),
                     edge_m_percentiles={str(p): float(x) for p, x in zip(pct, np.percentile(length, pct))},
                     edge_m_max=float(length.max()),
                     surface_m2=float(area.sum()), degenerate_faces=int((~valid).sum()),
                     vertical_face_fraction=float(vertical.sum() / len(fv)),
                     vertical_area_fraction=float(area[vertical].sum() / area[valid].sum()),
                     uv_islands=islands, faces_per_uv_island=len(fv) / islands)
    out['seconds'] = dict(parse_or_cache=t_parse, total=time.monotonic() - t0)
    (args.study / 'analysis').mkdir(exist_ok=True)
    (args.study / 'analysis' / f'{args.tile}.json').write_text(json.dumps(out, indent=1) + '\n')
    print(args.tile, out['A2']['faces'], f"{out['seconds']['total']:.0f}s", flush=True)


if __name__ == '__main__':
    main()
