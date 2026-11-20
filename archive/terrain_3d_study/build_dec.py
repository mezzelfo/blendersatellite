"""Variant DEC_r of the step-20 study: quadric edge-collapse decimation with texture preservation.

  python -I build_dec.py --study STUDY --block CBD --tile D4-04 --ratio 16

Input: the parsed REF tile (objtile cache) and its JPGs in STUDY/extracted/<tile>/.
Target face count = REF faces / ratio. The mesh goes to pymeshlab with:
  - positions relative to the tile's lower bbox corner (MeshLab stores float32 coordinates);
  - one UV per face corner, shifted by (4 * (m % 128), 4 * (m // 128)) for material m, so that
    faces of different materials never share a UV point; this keeps material boundaries as UV
    seams and lets us recover the material of each face from its UVs. Offsets stay below 512
    because MeshLab stores UVs as float32 (precision about 6e-5 there). Faces whose decoded corner
    materials disagree or are invalid get the centre of one texture and are counted
    (faces_with_mixed_material_uv).
  The mesh is passed through a temporary OBJ (STUDY/work/tmp) with a single dummy material and
  a 1 x 1 px texture, because MeshLab's texture-aware decimation requires a valid texture index on
  every face corner, which the numpy Mesh constructor does not set.
Filter: meshing_decimation_quadric_edge_collapse_with_texture(targetfacenum, preserveboundary=True,
other parameters at their defaults). Tile borders are preserved so neighbouring tiles still meet.

Output STUDY/variants/<block>/DEC_<r>/<tile>/<tile>.obj + .mtl + the JPGs of the materials still
used, downscaled by --texture-factor with a box filter (Image.reduce, at least 1 x 1 px), and
<tile>.json with face counts and timings (decimation, texture downscale, write).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pymeshlab
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objtile  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
UV_COLS = 128  # material offsets in UV space: 128 columns of CELL x CELL UV cells
CELL, MARGIN = 4.0, 1.5  # a UV may drift by up to 1.5 outside [0, 1] and still decode to its material


def write_obj(path, v, uv, fv, fmat, materials, mtl_name, fmt='%.4f'):
    """OBJ with one vt per face corner, faces grouped by material."""
    order = np.argsort(fmat, kind='stable')
    fv, fmat, uv = fv[order], fmat[order], uv.reshape(-1, 3, 2)[order].reshape(-1, 2)
    with open(path, 'w') as out:
        out.write(f'mtllib {mtl_name}\n')
        np.savetxt(out, v, fmt=f'v {fmt} {fmt} {fmt}')
        np.savetxt(out, uv, fmt='vt %.6f %.6f')
        starts = np.flatnonzero(np.r_[True, fmat[1:] != fmat[:-1]])
        ends = np.r_[starts[1:], len(fmat)]
        corner = np.arange(len(fv) * 3).reshape(-1, 3) + 1
        for s, e in zip(starts, ends):
            out.write(f'usemtl {materials[fmat[s]]}\n')
            block = np.stack([fv[s:e, 0] + 1, corner[s:e, 0], fv[s:e, 1] + 1, corner[s:e, 1],
                              fv[s:e, 2] + 1, corner[s:e, 2]], 1)
            np.savetxt(out, block, fmt='f %d/%d %d/%d %d/%d')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    parser.add_argument('--tile', required=True)
    parser.add_argument('--ratio', type=int, required=True)
    parser.add_argument('--texture-factor', type=int, default=32)
    args = parser.parse_args()
    t0 = time.monotonic()
    src = args.study / 'extracted' / args.tile
    mesh = objtile.load(next(src.glob('*.obj')), args.study / 'work' / 'parsed')
    v, vt, fv, ft, fmat = mesh['v'], mesh['vt'], mesh['fv'], mesh['ft'], mesh['fmat']
    materials, maps = mesh['materials'], mesh['maps']
    origin = np.floor(v.min(0))
    uv = vt[ft.ravel()].copy()
    corner = np.repeat(fmat, 3)
    uv[:, 0] += CELL * (corner % UV_COLS); uv[:, 1] += CELL * (corner // UV_COLS)
    t_load = time.monotonic() - t0

    t1 = time.monotonic()
    tmp = args.study / 'work' / 'tmp' / f'{args.tile}_r{args.ratio}'
    tmp.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (1, 1), (128, 128, 128)).save(tmp / 'dummy.png')
    (tmp / 'dummy.mtl').write_text('newmtl dummy\nKa 1 1 1\nKd 1 1 1\nmap_Kd dummy.png\n')
    write_obj(tmp / 'in.obj', v - origin, uv, fv, np.zeros(len(fv), dtype=np.int64), np.array(['dummy']),
              'dummy.mtl', fmt='%.6f')
    t_tmp = time.monotonic() - t1
    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(tmp / 'in.obj'))
    ref_faces = len(fv)
    if ms.current_mesh().face_number() != ref_faces:
        raise RuntimeError(f'pymeshlab loaded {ms.current_mesh().face_number()} faces, expected {ref_faces}')
    target = int(round(ref_faces / args.ratio))
    t_dec0 = time.monotonic()
    ms.meshing_decimation_quadric_edge_collapse_with_texture(targetfacenum=target, preserveboundary=True)
    t_filter = time.monotonic() - t_dec0
    m = ms.current_mesh()
    dv = m.vertex_matrix() + origin
    dfv = m.face_matrix()
    duv = m.wedge_tex_coord_matrix()
    def material_of(u, v):
        return (np.floor((u + MARGIN) / CELL).astype(np.int64)
                + UV_COLS * np.floor((v + MARGIN) / CELL).astype(np.int64))
    # A few collapses give corners non-finite UVs or UVs that drifted out of their material's cell.
    # Such faces take the material of their first valid corner (or the median material of the
    # corners) and all three corners are set to the centre of that texture; they are counted.
    n_mat = len(maps)
    finite = np.isfinite(duv).all(1)
    corner_mat = material_of(np.where(finite, duv[:, 0], -1e9), np.where(finite, duv[:, 1], -1e9)).reshape(-1, 3)
    valid = finite.reshape(-1, 3) & (corner_mat >= 0) & (corner_mat < n_mat)
    agree = valid.all(1) & (corner_mat == corner_mat[:, :1]).all(1)
    first_good = np.argmax(valid, axis=1)
    dmat = np.where(valid.any(1), corner_mat[np.arange(len(corner_mat)), first_good], 0)
    nan_uv_faces = int((~finite.reshape(-1, 3)).any(1).sum())
    mismatch = int((~agree).sum())
    centre = np.stack([CELL * (dmat % UV_COLS) + 0.5, CELL * (dmat // UV_COLS) + 0.5], 1)
    duv = np.where(np.repeat(~agree, 3)[:, None], np.repeat(centre, 3, axis=0), duv)
    for path in tmp.iterdir():
        path.unlink()
    tmp.rmdir()
    duv[:, 0] -= CELL * np.repeat(dmat % UV_COLS, 3); duv[:, 1] -= CELL * np.repeat(dmat // UV_COLS, 3)
    # Drop unreferenced vertices left by the collapses.
    used = np.unique(dfv)
    remap = np.full(len(dv), -1, dtype=np.int64); remap[used] = np.arange(len(used))
    dv, dfv = dv[used], remap[dfv]
    t_decimate = time.monotonic() - t1
    del ms, m
    t_diag = time.monotonic()

    # UV stretch diagnostic: texel size of each decimated face in the ORIGINAL texture pixels,
    # relative to the REF median of the tile (1 = unchanged; large values = UVs spanning islands).
    sizes = {}
    for mat in np.unique(np.r_[fmat, dmat]):
        if maps[mat]:
            with Image.open(src / maps[mat]) as im:
                sizes[mat] = im.size
    def texel(vv, ff, uu, mm):
        area, _ = objtile.face_geometry(vv, ff)
        q = uu.reshape(-1, 3, 2)
        uva = 0.5 * np.abs((q[:, 1, 0] - q[:, 0, 0]) * (q[:, 2, 1] - q[:, 0, 1]) - (q[:, 2, 0] - q[:, 0, 0]) * (q[:, 1, 1] - q[:, 0, 1]))
        wh = np.array([sizes.get(m_, (0, 0)) for m_ in mm], dtype=np.float64)
        px = uva * wh[:, 0] * wh[:, 1]
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.sqrt(area / px), area
    ref_texel, _ = texel(v, fv, vt[ft.ravel()], fmat)
    ref_median = float(np.nanmedian(ref_texel[np.isfinite(ref_texel)]))
    dec_texel, dec_area = texel(dv, dfv, duv, dmat)
    rel = dec_texel / ref_median
    finite = np.isfinite(rel)
    stretch = dict(ref_texel_median_m=ref_median, dec_texel_median_m=float(np.median(dec_texel[finite])),
                   rel_p50_p95_p99=[float(x) for x in np.percentile(rel[finite], [50, 95, 99])],
                   face_fraction_rel_gt4=float(np.mean(~finite | (rel > 4))),
                   area_fraction_rel_gt4=float(dec_area[~finite | (rel > 4)].sum() / dec_area.sum()),
                   faces_zero_uv_area=int((~finite).sum()))

    t_diag = time.monotonic() - t_diag
    t2 = time.monotonic()
    out = args.study / 'variants' / args.block / f'DEC_{args.ratio}' / args.tile
    out.mkdir(parents=True, exist_ok=True)
    k = args.texture_factor
    keep = np.unique(dmat)
    texture_px = 0
    for mat in keep:
        name = maps[mat]
        if not name:
            continue
        with Image.open(src / name) as im:
            im = im.convert('RGB')
            w, h = im.size
            small = im.reduce(min(k, w, h)) if min(w, h) >= k else im.resize((max(1, w // k), max(1, h // k)), Image.BOX)
        small.save(out / name, quality=90)
        texture_px += small.size[0] * small.size[1]
    t_texture = time.monotonic() - t2

    t3 = time.monotonic()
    (out / f'{args.tile}.mtl').write_text(''.join(
        f'newmtl {materials[m_]}\nKa 1 1 1\nKd 1 1 1\nmap_Kd {maps[m_]}\n\n' for m_ in keep if maps[m_]))
    write_obj(out / f'{args.tile}.obj', dv, duv, dfv, dmat, materials, f'{args.tile}.mtl')
    t_write = time.monotonic() - t3
    record = dict(tile=args.tile, block=args.block, ratio=args.ratio, ref_faces=ref_faces, target_faces=target,
                  faces=int(len(dfv)), vertices=int(len(dv)), faces_over_target=len(dfv) / target,
                  reduction=ref_faces / len(dfv), materials_used=int(len(keep)),
                  faces_with_mixed_material_uv=mismatch, faces_non_finite_uv=nan_uv_faces, texture_factor=k, texture_megapixels=texture_px / 1e6, uv_stretch=stretch,
                  seconds=dict(load=t_load, temp_obj=t_tmp, filter=t_filter, decimate=t_decimate, stretch_diagnostic=t_diag, texture=t_texture, write=t_write,
                               total=time.monotonic() - t0))
    (out / f'{args.tile}.json').write_text(json.dumps(record, indent=1) + '\n')
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
