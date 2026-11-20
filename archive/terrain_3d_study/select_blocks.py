"""Select the two 3 x 3 tile blocks of the terrain 3D study (step 20).

CBD: the largest tile (by archive size in log.jsonl, expected D4-04) and its 8 neighbours.
SUBURB: among all 3 x 3 blocks whose 9 tiles exist in tile_grid.json and all have
covered_fraction > 0.98 in log.jsonl, the one with the lowest total face count
(blocks containing a tile with unknown face count are listed but not eligible).

Writes <study>/blocks.json.
"""
import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--cbd-centre', default='D4-04')
    args = parser.parse_args()
    grid = json.loads((ROOT / 'data/terrain/melbourne_2020/tile_grid.json').read_text())
    log = {}
    for line in (ROOT / 'data/terrain/melbourne_2020/1m/log.jsonl').read_text().splitlines():
        record = json.loads(line)
        log[record['tile']] = record
    rings = {f['attributes']['Region']: np.asarray(f['geometry']['rings'][0]) for f in grid['features']}
    xmin = min(r[:, 0].min() for r in rings.values()); ymin = min(r[:, 1].min() for r in rings.values())
    cell = {}
    for tile, ring in rings.items():
        i = int(round((ring[:, 0].min() - xmin) / 280.)); j = int(round((ring[:, 1].min() - ymin) / 280.))
        cell[(i, j)] = tile
    where = {t: ij for ij, t in cell.items()}

    def block(centre):
        i, j = where[centre]
        tiles = [cell.get((i + di, j + dj)) for dj in (1, 0, -1) for di in (-1, 0, 1)]  # rows north->south
        return tiles

    largest = max(log.values(), key=lambda r: r['archive_bytes'] or 0)['tile']
    blocks = {}
    candidates = []
    for centre in rings:
        tiles = block(centre)
        if None in tiles:
            continue
        fractions = [log[t]['covered_fraction'] for t in tiles]
        if min(fractions) <= 0.98:
            continue
        faces = [log[t]['faces'] for t in tiles]
        candidates.append(dict(centre=centre, tiles=tiles, faces=None if None in faces else sum(faces),
                               unknown=[t for t, f in zip(tiles, faces) if f is None]))
    eligible = [c for c in candidates if c['faces'] is not None]
    suburb = min(eligible, key=lambda c: c['faces'])
    for name, tiles in (('CBD', block(args.cbd_centre)), ('SUBURB', suburb['tiles'])):
        if None in tiles:
            raise SystemExit(f'{name} block incomplete: {tiles}')
        recs = [log[t] for t in tiles]
        x0 = min(rings[t][:, 0].min() for t in tiles); y0 = min(rings[t][:, 1].min() for t in tiles)
        x1 = max(rings[t][:, 0].max() for t in tiles); y1 = max(rings[t][:, 1].max() for t in tiles)
        blocks[name] = dict(centre_tile=tiles[4], tiles=tiles,
                            bbox_7855=[float(x0), float(y0), float(x1), float(y1)],
                            per_tile=[dict(tile=r['tile'], archive_bytes=r['archive_bytes'], faces=r['faces'],
                                           covered_fraction=r['covered_fraction']) for r in recs],
                            archive_bytes=sum(r['archive_bytes'] or 0 for r in recs),
                            faces=sum(r['faces'] or 0 for r in recs))
    out = dict(largest_tile_by_archive=largest, blocks=blocks,
               suburb_candidates=len(candidates), suburb_eligible=len(eligible),
               suburb_ineligible_unknown_faces=[c for c in candidates if c['faces'] is None],
               suburb_ranking=sorted(([c['centre'], c['faces']] for c in eligible), key=lambda x: x[1])[:10],
               total_archive_bytes=sum(b['archive_bytes'] for b in blocks.values()))
    args.study.mkdir(parents=True, exist_ok=True)
    (args.study / 'blocks.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'blocks'}, indent=1))
    for name, b in blocks.items():
        print(name, b['centre_tile'], f"{b['archive_bytes']/1e9:.3f} GB", f"{b['faces']:,} faces")
        for r in b['per_tile']:
            print('  ', r)


if __name__ == '__main__':
    main()
