"""Reorganise band_samples/ by band and add visual deputy-vs-chief comparisons (no measurement).

Before: band_samples/satellite_SS/band_RRRR.png
After:  band_samples/band_RRRR/satellite_SS.png              (moved, not copied)
        band_samples/band_RRRR/diff_satSS_vs_sat00_x10.png    |deputy - chief| x 10 (RGB), white where
                                                              either alpha < 255
        band_samples/band_RRRR/blink_satSS_vs_sat00.gif       chief / deputy alternating at 2 frames/s on
                                                              two crops of the same columns: frames 0..63
                                                              (left) and frames 936..999 (right),
                                                              upscaled x8 with nearest neighbour

Within one band every satellite looks along the same detector row at the same instants, so the
images show almost the same ground. All cameras move with the same ground velocity, so the offset
deputy - chief in one band is CONSTANT along k: it is the scale / position offset of that detector
row (own slant range, up to ~1 px at the columns far from the zero point), varying across columns.
The deputy drift (s - 1) is a band-to-band effect within ONE satellite (detector rows spaced by the
deputy's own GSD, ground advancing by the chief's GSD per frame): see comparison/.
No correction and no estimate is applied: images only.

Usage: python3 scripts/organize_band_samples.py [--dir PATH]
"""
import argparse
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO / 'output/orbits_melbourne/eevee_full_1000/band_samples'
CROP_ROWS, CROP_COLS, ZOOM = 64, 96, 8


def move_by_band(root):
    """Move satellite_SS/band_RRRR.png to band_RRRR/satellite_SS.png; return {band: {sat: path}}."""
    for sat_dir in sorted(root.glob('satellite_??')):
        sat = int(sat_dir.name[-2:])
        for png in sorted(sat_dir.glob('band_????.png')):
            target = root / png.stem / f'satellite_{sat:02d}.png'
            target.parent.mkdir(exist_ok=True)
            if target.exists(): raise SystemExit(f'{target} exists; refusing to overwrite')
            os.rename(png, target)
    layout = {}
    for band_dir in sorted(root.glob('band_????')):
        band = int(band_dir.name[-4:])
        layout[band] = {int(m.group(1)): p for p in sorted(band_dir.glob('satellite_??.png'))
                        if (m := re.match(r'satellite_(\d\d)\.png', p.name))}
    return layout


def load(path):
    return np.asarray(Image.open(path).convert('RGBA'))


def luminance(rgba):
    rgb = rgba[..., :3].astype(np.float64)
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def pick_columns(images, rows):
    """Start column of the CROP_COLS window with the highest gradient energy of the chief over the
    given rows, fully opaque in every satellite; None if no such window exists."""
    opaque = np.ones(images[0].shape[1], bool)
    for im in images.values():
        opaque &= (im[rows, :, 3] == 255).all(0)
    g = luminance(images[0])[rows]
    energy = np.abs(np.diff(g, axis=0)).sum(0) + np.r_[np.abs(np.diff(g, axis=1)).sum(0), 0]
    kernel = np.ones(CROP_COLS)
    window_energy = np.convolve(energy, kernel, 'valid')
    window_ok = np.convolve(opaque.astype(float), kernel, 'valid') == CROP_COLS
    if not window_ok.any(): return None
    return int(np.argmax(np.where(window_ok, window_energy, -1)))


def crop_pair(im, c_top, c_bottom):
    """Top crop (frames 0..CROP_ROWS-1, columns c_top..) and bottom crop (last CROP_ROWS frames,
    columns c_bottom..) side by side with a white 1-px gap, upscaled ZOOM x (nearest neighbour)."""
    top = im[:CROP_ROWS, c_top:c_top + CROP_COLS, :3]
    bottom = im[-CROP_ROWS:, c_bottom:c_bottom + CROP_COLS, :3]
    gap = np.full((CROP_ROWS, 1, 3), 255, np.uint8)
    pair = np.concatenate([top, gap, bottom], axis=1)
    return Image.fromarray(pair).resize((pair.shape[1] * ZOOM, pair.shape[0] * ZOOM), Image.NEAREST)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', type=Path, default=DEFAULT_DIR)
    root = ap.parse_args().dir
    layout = move_by_band(root)
    for band, paths in layout.items():
        if 0 not in paths: raise SystemExit(f'band {band}: chief missing')
        images = {s: load(p) for s, p in paths.items()}
        chief = images[0]
        c_top = pick_columns(images, slice(0, CROP_ROWS))
        c_bottom = pick_columns(images, slice(-CROP_ROWS, None))
        for sat, im in images.items():
            if sat == 0: continue
            valid = (chief[..., 3] == 255) & (im[..., 3] == 255)
            diff = np.abs(im[..., :3].astype(np.int16) - chief[..., :3].astype(np.int16)) * 10
            out = np.where(valid[..., None], np.clip(diff, 0, 255), 255).astype(np.uint8)
            Image.fromarray(out).save(root / f'band_{band:04d}' / f'diff_sat{sat:02d}_vs_sat00_x10.png')
            if c_top is None or c_bottom is None: continue
            a, b = crop_pair(chief, c_top, c_bottom), crop_pair(im, c_top, c_bottom)
            a.save(root / f'band_{band:04d}' / f'blink_sat{sat:02d}_vs_sat00.gif', save_all=True,
                   append_images=[b], duration=500, loop=0)
        note = '' if c_top is not None and c_bottom is not None else ' (no opaque crop window: GIFs skipped)'
        print(f'band {band}: {len(images)} satellites; crop start columns top {c_top}, bottom {c_bottom} '
              f'(cube columns; PNG column = 662 + c){note}')


if __name__ == '__main__':
    main()
