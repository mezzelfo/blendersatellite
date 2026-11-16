"""Export a small, git-tracked set of CBD sample renders of the step-20 study.

  python -I export_samples.py --study STUDY [--views t00,t15_N,t30_N]

Copies STUDY/renders/CBD/<variant>/<view>.png (RGBA, 512 x 512, unchanged) for REF (the x8 texture
reference), GRID, WALL, DEC_4, DEC_16 and DEC_64 to STUDY/samples/CBD_<view>_<variant>.png, and
writes STUDY/samples/CBD_contact_sheet.png: rows = views, columns = variants, each cell the block
crop (common bounding box of the REF alpha over all views, plus 8 px) on black, nearest-neighbour
x 2, with a label. README.md in the same folder describes the files.
"""
import argparse
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

VARIANTS = [('REF_x8', 'REF (textures x8)'), ('GRID', 'GRID 2.5 m'), ('WALL', 'WALL'),
            ('DEC_4', 'DEC_4'), ('DEC_16', 'DEC_16'), ('DEC_64', 'DEC_64')]
TITLES = {'t00': 'nadir', 't05_N': '5 deg N', 't05_E': '5 deg E', 't15_N': '15 deg N', 't15_E': '15 deg E',
          't30_N': '30 deg N', 't30_E': '30 deg E'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--views', default='t00,t15_N,t30_N')
    args = parser.parse_args()
    views = args.views.split(',')
    root = args.study / 'renders' / 'CBD'
    out = args.study / 'samples'
    out.mkdir(exist_ok=True)
    for view in views:
        for variant, _ in VARIANTS:
            shutil.copyfile(root / variant / f'{view}.png', out / f'CBD_{view}_{variant}.png')
    # Common crop around the block.
    boxes = [np.nonzero(np.asarray(Image.open(root / 'REF_x8' / f'{v}.png'))[..., 3] > 0) for v in views]
    y0 = max(0, min(b[0].min() for b in boxes) - 8); y1 = max(b[0].max() for b in boxes) + 9
    x0 = max(0, min(b[1].min() for b in boxes) - 8); x1 = max(b[1].max() for b in boxes) + 9
    w, h, label, scale = x1 - x0, y1 - y0, 22, 2
    sheet = Image.new('RGB', (len(VARIANTS) * w * scale, len(views) * (h * scale + label)), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for i, view in enumerate(views):
        for j, (variant, name) in enumerate(VARIANTS):
            im = Image.open(root / variant / f'{view}.png').convert('RGBA').crop((x0, y0, x1, y1))
            cell = Image.new('RGB', im.size, (0, 0, 0)); cell.paste(im, mask=im.split()[3])
            cell = cell.resize((w * scale, h * scale), Image.NEAREST)
            top = i * (h * scale + label)
            sheet.paste(cell, (j * w * scale, top + label))
            draw.text((j * w * scale + 6, top + 5), f'{name} - {TITLES.get(view, view)}', fill=(255, 255, 255))
    sheet.save(out / 'CBD_contact_sheet.png', optimize=True)
    lines = ['# CBD sample renders (step 20, terrain 3D study)', '',
             'Block: D4-04 and its 8 neighbours (840 m x 840 m), EEVEE 8 samples, emission shader, transparent film,',
             'lens 580 mm, 500 km circular orbit seen from the block at zenith angle theta, azimuth N = camera north of',
             'the block looking south, image up = north. 512 x 512 px window of the 4096 x 3072 frame (GSD 4.74 m at nadir,',
             '4.90 / 5.07 m at 15 deg, 5.41 / 6.25 m at 30 deg, across / along the tilt direction).', '',
             'Variants: REF = original photomesh (textures box-downscaled x8 to fit 12 GB); GRID = current 2.5D pipeline',
             'terrain; WALL = GRID + vertical quads coloured from REF; DEC_r = REF decimated by r (texture-preserving',
             'quadric), textures at ~0.6 m. Metrics and method: Prompts/report_20.md.', '',
             f'Files: CBD_<view>_<variant>.png (renders unchanged; views {", ".join(views)}) and CBD_contact_sheet.png',
             f'(rows = views, columns = variants, block crop x {scale}, nearest neighbour).', '']
    (out / 'README.md').write_text('\n'.join(lines))
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f'{len(list(out.iterdir()))} files, {total / 1e6:.2f} MB -> {out}; sheet {sheet.size}')


if __name__ == '__main__':
    main()
