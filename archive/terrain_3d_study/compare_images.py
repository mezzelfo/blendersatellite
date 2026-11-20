"""Image comparison of the step-20 study (C.3, C.4, C.5): every variant against REF.

  python -I compare_images.py --study STUDY --ref CBD=REF_x4,SUBURB=REF_x2

For every block, view and variant render (STUDY/renders/<block>/<variant>/<view>.png):
  domain   = pixels where the REF render has alpha 255; variant RGB is composited over black;
  vertical = domain pixels where the REF near-vertical pass (<view>_vert.png, |n_z| < 0.3) is > 0.5;
metrics on each domain: mean |dRGB| (DN, over pixels and channels), PSNR (peak 255), SSIM on
Rec.709 luminance (skimage, 7 x 7 window, map computed on the bounding box of the domain and
averaged over the domain pixels), and the shift (rows, cols) from scripts/phase_correlation.py
phase_shift with a Hann window (no low-pass; see PLAN_CONTEXT S11 for its pixel-locking bias),
on luminance multiplied by the domain mask in both images: mov(x) ~ ref(x + shift).
Also the fraction of domain pixels that are vertical, and of domain pixels the variant leaves
transparent.

Step 22: --variants restricts the variants compared, --extra-renders adds a second render root
(STUDY/wall2/renders/<block>/<variant>/) searched before STUDY/renders, and --out writes metrics.json
and mosaics elsewhere than STUDY/compare (which then stays as step 20 left it). With
--mosaic-variants the mosaic columns can be chosen (default GRID, DEC_16, WALL).

Mosaics (C.4): per block and view, top row REF | GRID | DEC_16 | WALL (those present), bottom row
|variant - REF| x 10 (the REF slot shows the near-vertical mask), 256 x 256 px window with the most
vertical pixels (ties: the one centred closest to the REF footprint), nearest-neighbour x 4. Written to STUDY/compare/mosaics/<block>_<view>.png.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from phase_correlation import phase_shift  # noqa: E402

LUMA = np.array([0.2126, 0.7152, 0.0722])
MOSAIC_VARIANTS = ['GRID', 'DEC_16', 'WALL']


def load(path):
    a = np.asarray(Image.open(path).convert('RGBA')).astype(np.float64)
    return a[..., :3] * (a[..., 3:] / 255), a[..., 3]


def metrics(ref, var, var_alpha, domain):
    n = int(domain.sum())
    if n == 0:
        return dict(pixels=0)
    d = np.abs(ref - var)[domain]
    mse = float(np.mean((ref - var)[domain] ** 2))
    lr, lv = ref @ LUMA, var @ LUMA
    ys, xs = np.nonzero(domain)
    box = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    if min(np.ptp(ys), np.ptp(xs)) >= 7:
        _, smap = structural_similarity(lr[box], lv[box], data_range=255, win_size=7, full=True)
        ssim = float(smap[domain[box]].mean())
    else:
        ssim = None
    m = domain.astype(np.float64)
    shift = phase_shift((lr * m)[box], (lv * m)[box], factor=100, hann=True)
    return dict(pixels=n, mean_abs_dRGB_DN=float(d.mean()), psnr_db=float(10 * np.log10(255 ** 2 / mse)) if mse > 0 else None,
                ssim_luma=ssim, shift_rows_cols_px=[float(shift[0]), float(shift[1])],
                variant_transparent_fraction=float(np.mean(var_alpha[domain] < 255)))


def best_window(mask, size, domain):
    """Window with the most mask pixels; ties broken by the window centred closest to the domain."""
    h, w = mask.shape
    if h <= size or w <= size:
        return 0, 0
    ii = np.pad(mask.astype(np.int64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    s = ii[size:, size:] - ii[:-size, size:] - ii[size:, :-size] + ii[:-size, :-size]
    ys, xs = np.nonzero(domain)
    cy, cx = (ys.min() + ys.max()) / 2 - size / 2, (xs.min() + xs.max()) / 2 - size / 2
    yy, xx = np.nonzero(s == s.max())
    k = np.argmin((yy - cy) ** 2 + (xx - cx) ** 2)
    return int(yy[k]), int(xx[k])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--ref', required=True, help='BLOCK=REF_xK,... reference render folder per block')
    parser.add_argument('--window', type=int, default=256)
    parser.add_argument('--variants', help='comma-separated variants to compare (default: every folder)')
    parser.add_argument('--extra-renders', type=Path, help='second render root, searched first')
    parser.add_argument('--out', type=Path, help='default STUDY/compare')
    parser.add_argument('--mosaic-variants', default=','.join(MOSAIC_VARIANTS))
    parser.add_argument('--views', help='comma-separated views (default: every REF view)')
    args = parser.parse_args()
    refs = dict(item.split('=') for item in args.ref.split(','))
    out = args.out or args.study / 'compare'
    (out / 'mosaics').mkdir(parents=True, exist_ok=True)
    mosaic_variants = args.mosaic_variants.split(',')
    rows = []
    for block, ref_name in refs.items():
        root = args.study / 'renders' / block
        roots = ([args.extra_renders / block] if args.extra_renders else []) + [root]
        if args.variants:
            variants = args.variants.split(',')
        else:
            variants = sorted({p.name for r in roots if r.exists() for p in r.iterdir()
                               if p.is_dir() and p.name != ref_name})
        views = sorted(p.stem for p in (root / ref_name).glob('*.png') if not p.stem.endswith('_vert'))
        if args.views:
            views = [v for v in views if v in args.views.split(',')]
        for view in views:
            ref, ref_alpha = load(root / ref_name / f'{view}.png')
            vert_img, _ = load(root / ref_name / f'{view}_vert.png')
            domain = ref_alpha == 255
            vertical = domain & (vert_img[..., 0] > 127.5)
            panels = {ref_name: ref}
            for variant in variants:
                path = next((r / variant / f'{view}.png' for r in roots if (r / variant / f'{view}.png').exists()),
                            None)
                if path is None:
                    continue
                var, var_alpha = load(path)
                panels[variant] = var
                row = dict(block=block, view=view, ref=ref_name, variant=variant, image=str(path),
                           vertical_fraction=float(vertical.sum() / domain.sum()),
                           all=metrics(ref, var, var_alpha, domain),
                           vertical=metrics(ref, var, var_alpha, vertical))
                rows.append(row)
                a, v = row['all'], row['vertical']
                print(f"{block} {view} {variant:10s} all: {a['mean_abs_dRGB_DN']:.2f} DN {a['psnr_db']:.2f} dB "
                      f"ssim {a['ssim_luma']:.3f} shift {a['shift_rows_cols_px']} | vert: "
                      f"{v.get('mean_abs_dRGB_DN', float('nan')):.2f} DN ssim {v.get('ssim_luma')}", flush=True)
            # Mosaic.
            size = min(args.window, ref.shape[0])
            y, x = best_window(vertical, size, domain)
            names = [ref_name] + [n for n in mosaic_variants if n in panels]
            top, bottom = [], []
            for name in names:
                img = panels[name][y:y + size, x:x + size]
                top.append(img)
                if name == ref_name:
                    bottom.append(np.repeat(vertical[y:y + size, x:x + size, None] * 255., 3, axis=2))
                else:
                    bottom.append(np.clip(np.abs(img - ref[y:y + size, x:x + size]) * 10, 0, 255))
            mosaic = np.concatenate([np.concatenate(top, 1), np.concatenate(bottom, 1)], 0).astype(np.uint8)
            mosaic = np.repeat(np.repeat(mosaic, 4, axis=0), 4, axis=1)
            path = out / 'mosaics' / f'{block}_{view}.png'
            Image.fromarray(mosaic).save(path, optimize=True)
            (out / 'mosaics' / f'{block}_{view}.json').write_text(json.dumps(dict(
                panels_left_to_right=names, window_yx=[y, x], window_px=size, upscale='nearest x4',
                bottom_row='REF slot: near-vertical mask; others: |variant - REF| x 10, clipped at 255',
                png_bytes=path.stat().st_size)) + '\n')
    (out / 'metrics.json').write_text(json.dumps(rows, indent=1) + '\n')


if __name__ == '__main__':
    main()
