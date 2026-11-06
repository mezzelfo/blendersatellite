"""Pixel extent of the Melbourne tiles in every satellite image of a per-epoch cameras.json.

Tile corners (EPSG:7855) are converted once to the local ENU frame (pyproj, as in
orbits/scripts/check_cameras.py) at the lowest and highest terrain height; the
projection into each camera is pure NumPy from the exported camera_to_local,
position_local_m, focal length, pixel pitch and resolution. Pixel origin is the
top-left corner of the image; columns grow right, rows grow down. Straight tile
edges stay straight under perspective, so the corners bound each projected tile.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer


def tile_corners_local(grid, manifest, heights):
    rings = np.array([np.array(ft['geometry']['rings'][0])[:4] for ft in json.loads(grid.read_text())['features']])
    lon, lat = Transformer.from_crs(7855, 7844, always_xy=True).transform(rings[..., 0], rings[..., 1])
    to_ecef = Transformer.from_crs(4979, 4978, always_xy=True)
    rotation = np.array(manifest['local_from_ecef']); origin = np.array(manifest['origin_ecef_m'])
    corners = [(np.stack(to_ecef.transform(lon, lat, np.full_like(lon, h)), -1) - origin) @ rotation.T
               for h in heights]
    return np.concatenate(corners, axis=1)  # tiles x (4*len(heights)) x 3


def project(record, points):
    basis = np.array(record['camera_to_local']); position = np.array(record['position_local_m'])
    width, height = record['resolution']
    scale = record['focal_length_mm'] / (record['sensor_width_mm'] / width)  # focal length in pixels
    camera = (points - position) @ basis  # right, up, backward
    depth = -camera[..., 2]
    if np.any(depth <= 0): raise ValueError('point behind camera')
    col = width / 2 + scale * camera[..., 0] / depth
    row = height / 2 - scale * camera[..., 1] / depth
    return col, row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cameras', type=Path)
    parser.add_argument('--grid', type=Path, required=True)
    parser.add_argument('--heights', default='-14,323', help='terrain height bounds [m] at which corners are projected')
    args = parser.parse_args()
    manifest = json.loads(args.cameras.read_text())
    corners = tile_corners_local(args.grid, manifest, [float(h) for h in args.heights.split(',')])
    width, height = manifest['sensor']['resolution']
    union = np.array([np.inf, -np.inf, np.inf, -np.inf])  # col min/max, row min/max
    always = np.ones(height, dtype=bool)
    per_sat = {}
    worst_frame = {}
    for epoch in manifest['epochs']:
        for record in epoch['cameras']:
            col, row = project(record, corners)
            box = np.array([col.min(), col.max(), row.min(), row.max()])
            union = np.array([min(union[0], box[0]), max(union[1], box[1]), min(union[2], box[2]), max(union[3], box[3])])
            s = per_sat.setdefault(record['satellite_id'], [np.inf, -np.inf, np.inf, -np.inf])
            per_sat[record['satellite_id']] = [min(s[0], box[0]), max(s[1], box[1]), min(s[2], box[2]), max(s[3], box[3])]
            # Rows touched by at least one tile in this image (pixel row r spans [r, r+1)).
            lo = np.clip(np.floor(row.min(1)).astype(int), 0, height)
            hi = np.clip(np.ceil(row.max(1)).astype(int), 0, height)
            present = np.zeros(height + 1, dtype=int)
            np.add.at(present, lo, 1); np.add.at(present, hi, -1)
            always &= np.cumsum(present)[:-1] > 0
            worst_frame.setdefault('first_row', []).append((row.min(), epoch['frame'], record['satellite_id']))
            worst_frame.setdefault('last_row', []).append((row.max(), epoch['frame'], record['satellite_id']))
    def describe(c0, c1, r0, r1):
        return (f'cols {c0:.2f}..{c1:.2f} ({c0/width:.4f}..{c1/width:.4f} of {width}), '
                f'rows {r0:.2f}..{r1:.2f} ({r0/height:.4f}..{r1/height:.4f} of {height})')
    print(f'{len(corners)} tiles, corners at heights {args.heights} m, {len(manifest["epochs"])} frames')
    print('Union bounding box over all frames and satellites (unclipped, may exceed the sensor):')
    print('  ' + describe(*union))
    clip = [max(0, np.floor(union[0])), min(width, np.ceil(union[1])), max(0, np.floor(union[2])), min(height, np.ceil(union[3]))]
    clip = [int(v) for v in clip]
    print(f'  clipped to the sensor, integer pixels: X {clip[0]}:{clip[1]}, Y {clip[2]}:{clip[3]} '
          f'-> {clip[1]-clip[0]} x {clip[3]-clip[2]} px = {(clip[1]-clip[0])*(clip[3]-clip[2])/(width*height):.4f} of the frame '
          f'(cols {(clip[1]-clip[0])/width:.4f}, rows {(clip[3]-clip[2])/height:.4f})')
    for sid, box in sorted(per_sat.items()):
        print(f'  satellite {sid}: ' + describe(*box))
    print(f'  extreme top row {min(worst_frame["first_row"])}, extreme bottom row {max(worst_frame["last_row"])} (row, frame, satellite)')
    rows = np.flatnonzero(always)
    if len(rows) == 0:
        print('No row contains geometry at every frame')
    else:
        contiguous = np.all(np.diff(rows) == 1)
        print(f'Rows containing geometry at every frame and satellite: {rows[0]}..{rows[-1]} (inclusive, '
              f'{len(rows)} rows, {"contiguous" if contiguous else "NOT contiguous"}) = '
              f'{rows[0]/height:.4f}..{(rows[-1]+1)/height:.4f} of {height} ({len(rows)/height:.4f})')


if __name__ == '__main__':
    main()
