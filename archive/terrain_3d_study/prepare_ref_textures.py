"""Downscale the REF textures of a block by an integer factor for rendering (step-20 study).

  python -I prepare_ref_textures.py --study STUDY --block CBD --factor 4 [--workers 12]

Every JPG of every tile of the block (STUDY/extracted/<tile>/) is box-filtered by --factor
(Image.reduce, i.e. the mean of factor x factor texels; textures smaller than the factor become
1 px wide) and written as JPEG quality 95 to STUDY/variants/<block>/REF/tex_x<factor>/<tile>/.
The geometry and UVs are unchanged. Writes tex_x<factor>/summary.json (megapixels, bytes, time).
"""
import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def shrink(job):
    src, dst, k = job
    with Image.open(src) as im:
        im = im.convert('RGB')
        w, h = im.size
        if k == 1:
            small = im
        elif min(w, h) >= k:
            small = im.reduce(k)
        else:
            small = im.resize((max(1, w // k), max(1, h // k)), Image.BOX)
        small.save(dst, quality=95)
        return small.size[0] * small.size[1], dst.stat().st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--block', required=True)
    parser.add_argument('--factor', type=int, required=True)
    parser.add_argument('--workers', type=int, default=12)
    args = parser.parse_args()
    t0 = time.monotonic()
    block = json.loads((args.study / 'blocks.json').read_text())['blocks'][args.block]
    root = args.study / 'variants' / args.block / 'REF' / f'tex_x{args.factor}'
    jobs = []
    for tile in block['tiles']:
        (root / tile).mkdir(parents=True, exist_ok=True)
        for src in sorted((args.study / 'extracted' / tile).glob('*.jpg')):
            jobs.append((src, root / tile / src.name, args.factor))
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(shrink, jobs, chunksize=64))
    summary = dict(block=args.block, factor=args.factor, textures=len(jobs),
                   megapixels=sum(r[0] for r in results) / 1e6, bytes=sum(r[1] for r in results),
                   seconds=time.monotonic() - t0, workers=args.workers)
    (root / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
