"""Node levels and triangle density of a public I3S IntegratedMesh SceneServer over a block (A.4).

Walks every node page of the layer (no geometry is downloaded), derives each node's level
from the parent links (root = level 0), and counts triangles = vertexCount / 3 (the
'PerAttributeArray' topology stores 3 vertices per triangle). Over the block, a node counts
if the centre of its oriented bounding box falls inside the block's lon/lat box; density is
triangles / block area. Output: <study>/i3s/<layer name>.json.
"""
import argparse
import gzip
import json
import urllib.request
from pathlib import Path

import numpy as np
from pyproj import Transformer


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return json.loads(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layer', required=True, help='.../SceneServer/layers/0')
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', default='CBD')
    args = parser.parse_args()
    block = json.loads((args.study / 'blocks.json').read_text())['blocks'][args.block]
    x0, y0, x1, y1 = block['bbox_7855']
    to_ll = Transformer.from_crs(7855, 4326, always_xy=True)
    lon, lat = to_ll.transform([x0, x1, x1, x0], [y0, y0, y1, y1])
    area = (x1 - x0) * (y1 - y0)
    layer = fetch(args.layer + '?f=json')
    per_page = layer['nodePages']['nodesPerPage']
    nodes, page = [], 0
    while True:
        try:
            content = fetch(f'{args.layer}/nodepages/{page}?f=json')
        except Exception:
            break
        if not content.get('nodes'):
            break
        nodes.extend(content['nodes'])
        page += 1
        if len(content['nodes']) < per_page:
            break
    byid = {n['index']: n for n in nodes}
    level = {}
    for n in nodes:
        depth, k = 0, n
        while k.get('parentIndex') is not None and k['parentIndex'] in byid:
            k = byid[k['parentIndex']]; depth += 1
        level[n['index']] = depth
    levels = {}
    for n in nodes:
        lv = level[n['index']]
        geom = (n.get('mesh') or {}).get('geometry') or {}
        tris = geom.get('vertexCount', 0) // 3
        cx, cy = n['obb']['center'][:2]
        inside = min(lon) <= cx <= max(lon) and min(lat) <= cy <= max(lat)
        rec = levels.setdefault(lv, dict(level=lv, nodes=0, leaf_nodes=0, triangles=0, block_nodes=0,
                                         block_triangles=0, half_size_m=[], lod_threshold=[]))
        rec['nodes'] += 1; rec['triangles'] += tris
        rec['leaf_nodes'] += not n.get('children')
        rec['half_size_m'].append(float(np.max(n['obb']['halfSize'][:2])))
        rec['lod_threshold'].append(n.get('lodThreshold', float('nan')))
        if inside:
            rec['block_nodes'] += 1; rec['block_triangles'] += tris
    table = []
    for lv in sorted(levels):
        r = levels[lv]
        table.append(dict(level=lv, nodes=r['nodes'], leaf_nodes=r['leaf_nodes'], triangles=r['triangles'],
                          median_obb_half_size_m=float(np.median(r['half_size_m'])),
                          median_lod_threshold=float(np.nanmedian(r['lod_threshold'])),
                          block_nodes=r['block_nodes'], block_triangles=r['block_triangles'],
                          block_triangles_per_m2=r['block_triangles'] / area))
    out = dict(layer=args.layer, version=layer['store'].get('version'), lod_type=layer['store'].get('lodType'),
               lod_model=layer['store'].get('lodModel'), metric=layer['nodePages'].get('lodSelectionMetricType'),
               full_extent=layer.get('fullExtent', {}) and {k: layer['fullExtent'][k] for k in
                                                         ('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax')},
               block=args.block, block_lonlat=[min(lon), min(lat), max(lon), max(lat)], block_area_m2=area,
               node_pages=page, nodes=len(nodes), levels=table)
    name = args.layer.rstrip('/').split('/services/')[1].split('/')[0]
    (args.study / 'i3s').mkdir(exist_ok=True)
    (args.study / 'i3s' / f'{name}.json').write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'levels'}))
    for r in table:
        print(r)


if __name__ == '__main__':
    main()
