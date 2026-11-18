"""Export every band of the cubes of a run as images, by mode and band (step 25b).

Reads <run>/cubes/raw_bands_<mode>/cube_satSS.npz (build_cubes.py) and writes
    <out>/<mode>/band_BB_rowRRRR/satellite_SS.png    cube[:, :, BB] as stored: RGBA uint8,
                                                     image row = cube line g (g[0] at the top),
                                                     image column = scene column cols[0] + j
    <out>/<mode>/band_BB_rowRRRR/all_satellites.jpg  the 7 satellites stacked top to bottom with
                                                     a label strip (role from raw/sat_SS.json),
                                                     transparent pixels drawn white; JPEG quality 92
                                                     for viewing only (exact values: satellite_SS.png)
No resampling, no stretch: satellite_SS.png holds exactly the cube values. Default <out> is
<run>/band_samples. Parallel over (mode, satellite) for the single images.

    python scripts/export_cube_band_samples.py --run output/orbits_melbourne/<run>
"""
import argparse
import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MODES = ('none', 'linear', 'bilinear')
LABEL_H = 22


def band_dir(out, mode, b, row):
    return out / mode / f'band_{b:02d}_row{row:04d}'


def export_sat(args):
    run, out, mode, sat = args
    with np.load(run / 'cubes' / f'raw_bands_{mode}' / f'cube_sat{sat:02d}.npz') as d:
        cube, rows = d['cube'], d['band_rows']
    for b, r in enumerate(rows):
        dst = band_dir(out, mode, b, int(r))
        dst.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.ascontiguousarray(cube[:, :, b]), 'RGBA').save(dst / f'satellite_{sat:02d}.png',
                                                                         optimize=True)
    return mode, sat, cube.shape


def font():
    for name in ('DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, 15)
        except OSError:
            pass
    return ImageFont.load_default()


def contact_sheet(args):
    """all_satellites.jpg of one band folder."""
    folder, sats, roles, cols_first = args
    f = font()
    tiles = []
    width = 0
    for s in sats:
        im = Image.open(folder / f'satellite_{s:02d}.png')
        white = Image.new('RGBA', im.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(white, im).convert('RGB')
        label = Image.new('RGB', (im.width, LABEL_H), (255, 255, 255))
        ImageDraw.Draw(label).text((4, 2), f'satellite {s:02d} ({roles[s]}), {im.height} lines x {im.width} cols,'
                                           f' first scene column {cols_first[s]}', fill=(0, 0, 0), font=f)
        tiles += [label, rgb]
        width = max(width, im.width)
    sheet = Image.new('RGB', (width, sum(t.height for t in tiles)), (255, 255, 255))
    y = 0
    for t in tiles:
        sheet.paste(t, (0, y)); y += t.height
    sheet.save(folder / 'all_satellites.jpg', quality=92, subsampling=0)
    (folder / 'all_satellites.png').unlink(missing_ok=True)   # earlier PNG version of the sheet


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--modes', nargs='+', default=list(MODES), choices=MODES)
    ap.add_argument('--satellites', type=int, nargs='+', default=list(range(7)))
    ap.add_argument('--out', type=Path, help='default <run>/band_samples')
    ap.add_argument('--workers', type=int, default=21)
    args = ap.parse_args()
    out = args.out or args.run / 'band_samples'
    roles = {s: json.loads((args.run / 'raw' / f'sat_{s:02d}.json').read_text())['acquisition_role']
             for s in args.satellites}
    jobs = [(args.run, out, m, s) for m in args.modes for s in args.satellites]
    with Pool(min(args.workers, len(jobs))) as pool:
        for mode, sat, shape in pool.imap_unordered(export_sat, jobs):
            print(f'{mode} sat {sat}: {shape[2]} bands of {shape[0]} x {shape[1]}', flush=True)
        cols_first = {}
        for m in args.modes:
            for s in args.satellites:
                with np.load(args.run / 'cubes' / f'raw_bands_{m}' / f'cube_sat{s:02d}.npz') as d:
                    cols_first[(m, s)] = int(d['cols'][0])
        sheets = [(folder, args.satellites, roles, {s: cols_first[(m, s)] for s in args.satellites})
                  for m in args.modes for folder in sorted((out / m).glob('band_??_row????'))]
        pool.map(contact_sheet, sheets)
    print(f'{len(sheets)} band folders written under {out}')


if __name__ == '__main__':
    main()
