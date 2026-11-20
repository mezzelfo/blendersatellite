"""Fast reader for the Melbourne photomesh tile OBJs (CloudCompare export) used by the step-20 study.

The OBJs hold, in this order, all 'v', 'vt' and 'vn' lines, then 'usemtl' / 'f' groups of
triangles written as v/vt/vn. Each face corner has its own 'vt' line, so UV islands are found by
merging identical UV coordinates within one texture (see uv_islands).

load(obj_path) parses one OBJ (and its MTL) into numpy arrays and caches them as an
uncompressed .npz next to the study's parsed/ folder for reuse:
  v     float64 (V, 3)   vertex positions, EPSG:7855 metres, z up
  vt    float64 (T, 2)   texture coordinates
  fv    int32   (F, 3)   vertex indices of the triangles (0-based)
  ft    int32   (F, 3)   texture-coordinate indices (0-based)
  fmat  int32   (F,)     index into materials
  materials  (M,) str    material names, maps (M,) str texture file name ('' if none)
"""
from pathlib import Path

import numpy as np


def parse_mtl(path):
    maps, material = {}, None
    for line in Path(path).read_text(errors='replace').splitlines():
        if line.startswith('newmtl '):
            material = line.split(maxsplit=1)[1].strip()
            maps.setdefault(material, '')
        elif line.startswith('map_Kd ') and material is not None:
            maps[material] = line.split(maxsplit=1)[1].strip()
    return maps


def _floats(lines, prefix_len, width):
    if not lines:
        return np.zeros((0, width))
    text = b' '.join(line[prefix_len:] for line in lines)
    values = np.array(text.split(), dtype=np.float64)
    return values.reshape(-1, width)


def parse_obj(obj_path):
    """Parse a triangle OBJ with v/vt/vn faces and usemtl groups."""
    obj_path = Path(obj_path)
    data = obj_path.read_bytes()
    lines = data.split(b'\n')
    del data
    v_lines = [l for l in lines if l[:2] == b'v ']
    v = _floats(v_lines, 2, 3); del v_lines
    vt_lines = [l for l in lines if l[:3] == b'vt ']
    vt = _floats(vt_lines, 3, 2); del vt_lines
    # Face lines and the material active for each one.
    face_lines, face_mat, names, current = [], [], {}, -1
    for line in lines:
        head = line[:2]
        if head == b'f ':
            face_lines.append(line[2:])
            face_mat.append(current)
        elif head == b'us' and line.startswith(b'usemtl '):
            name = line[7:].strip().decode()
            current = names.setdefault(name, len(names))
    del lines
    counts = {len(l.split()) for l in face_lines[:1000]}
    if counts != {3}:
        raise ValueError(f'{obj_path}: non-triangle faces {counts}')
    text = b' '.join(face_lines).replace(b'/', b' ')
    del face_lines
    idx = np.array(text.split(), dtype=np.int64)
    per = idx.size // (len(face_mat) * 3)
    idx = idx.reshape(len(face_mat), 3, per) - 1
    fv = idx[:, :, 0].astype(np.int32)
    ft = idx[:, :, 1].astype(np.int32) if per > 1 else np.zeros_like(fv)
    materials = sorted(names, key=names.get)
    mtl = next(obj_path.parent.glob('*.mtl'), None)
    maps = parse_mtl(mtl) if mtl else {}
    return dict(v=v, vt=vt, fv=fv, ft=ft, fmat=np.asarray(face_mat, dtype=np.int32),
                materials=np.array(materials), maps=np.array([maps.get(m, '') for m in materials]))


def load(obj_path, cache_dir=None):
    """Parsed tile, from cache_dir/<stem>.npz if present (written on first use)."""
    obj_path = Path(obj_path)
    if cache_dir is not None:
        cached = Path(cache_dir) / f'{obj_path.stem}.npz'
        if cached.exists():
            with np.load(cached) as data:
                return {k: data[k] for k in data.files}
    mesh = parse_obj(obj_path)
    if cache_dir is not None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        part = cached.with_suffix('.part.npz')
        np.savez(part, **mesh)
        part.replace(cached)
    return mesh


def face_geometry(v, fv):
    """3D area (m^2) and unit normal of every triangle."""
    a, b, c = v[fv[:, 0]], v[fv[:, 1]], v[fv[:, 2]]
    cross = np.cross(b - a, c - a)
    norm = np.linalg.norm(cross, axis=1)
    with np.errstate(invalid='ignore', divide='ignore'):
        normal = cross / norm[:, None]
    return norm / 2, normal


def uv_islands(vt, ft, fmat):
    """Number of connected components of faces sharing an identical UV point in the same texture.

    Returns (islands, face_island_label).
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    corner_mat = np.repeat(fmat, 3)
    uv = vt[ft.ravel()]
    keys = np.stack([corner_mat.astype(np.float64), uv[:, 0], uv[:, 1]], 1)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1, 3)
    n = int(inverse.max()) + 1
    rows = np.concatenate([inverse[:, 0], inverse[:, 1]])
    cols = np.concatenate([inverse[:, 1], inverse[:, 2]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.int8), (rows, cols)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    face_label = labels[inverse[:, 0]]
    return int(len(np.unique(face_label))), face_label
