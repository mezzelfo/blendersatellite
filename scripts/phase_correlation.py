"""Subpixel phase correlation (upsampled DFT, Guizar-Sicairos 2008) between two RGBA PNGs.

Images are converted to Rec.709 luminance and multiplied by alpha, so the transparent
film contributes zero. The reported motion is the apparent displacement of the scene
from REFERENCE to MOVING in image pixels: vertical = rows (+down), across = columns
(+right). With --compare, also report per-pixel colour statistics between the two
images (pixels with alpha > 0.5 in both) and the alpha disagreement fraction.

Run in the conda environment (NumPy + Pillow), e.g.:
    python scripts/phase_correlation.py frame_0499.png frame_0500.png
"""
import argparse
import json

import numpy as np
from PIL import Image


def load(path):
    rgba = np.asarray(Image.open(path).convert('RGBA')).astype(np.float64) / 255
    return rgba


def masked_gray(rgba):
    return (rgba[..., :3] @ [0.2126, 0.7152, 0.0722]) * rgba[..., 3]


def upsampled_dft(data, region, factor, offsets):
    rows, cols = data.shape
    kc = np.fft.ifftshift(np.arange(cols)) - cols // 2
    kr = np.fft.ifftshift(np.arange(rows)) - rows // 2
    col_kernel = np.exp(-2j * np.pi / (cols * factor) * np.outer(kc, np.arange(region) - offsets[1]))
    row_kernel = np.exp(-2j * np.pi / (rows * factor) * np.outer(np.arange(region) - offsets[0], kr))
    return row_kernel @ data @ col_kernel


def phase_shift(ref, mov, factor=1000):
    """Shift s (rows, cols) such that mov(x) ~ ref(x + s); scene motion is -s."""
    cross = np.fft.fft2(ref) * np.conj(np.fft.fft2(mov))
    cross /= np.maximum(np.abs(cross), 1e-15)
    corr = np.fft.ifft2(cross)
    peak = np.array(np.unravel_index(np.argmax(np.abs(corr)), corr.shape), dtype=float)
    mid = np.array(corr.shape) // 2
    peak[peak > mid] -= np.array(corr.shape)[peak > mid]
    shift = np.round(peak * factor) / factor
    region = int(np.ceil(factor * 1.5)); centre = region // 2
    up = upsampled_dft(cross.conj(), region, factor, centre - shift * factor).conj()
    fine = np.array(np.unravel_index(np.argmax(np.abs(up)), up.shape), dtype=float) - centre
    return shift + fine / factor


def compare(a, b):
    both = (a[..., 3] > .5) & (b[..., 3] > .5)
    diff = np.abs(a[..., :3] - b[..., :3])[both] * 255  # 8-bit digital numbers
    per_pixel = diff.mean(1)
    return dict(pixels_both_opaque=int(both.sum()), mean_abs_dRGB_DN=float(diff.mean()),
                p99_abs_dRGB_DN=float(np.percentile(per_pixel, 99)), max_abs_dRGB_DN=float(diff.max()),
                alpha_disagreement_fraction=float(np.mean((a[..., 3] > .5) != (b[..., 3] > .5))),
                alpha_disagreement_fraction_of_geometry=float(np.sum((a[..., 3] > .5) != (b[..., 3] > .5))
                                                              / max(1, np.sum((a[..., 3] > .5) | (b[..., 3] > .5)))))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('reference')
    parser.add_argument('moving')
    parser.add_argument('--upsample', type=int, default=1000, help='subpixel resolution = 1/UPSAMPLE px')
    parser.add_argument('--compare', action='store_true', help='also report colour/alpha statistics')
    parser.add_argument('--json', action='store_true', help='print one JSON object')
    args = parser.parse_args()
    a, b = load(args.reference), load(args.moving)
    if a.shape != b.shape: parser.error(f'shape mismatch {a.shape} vs {b.shape}')
    motion = -phase_shift(masked_gray(a), masked_gray(b), args.upsample)
    result = dict(reference=args.reference, moving=args.moving, vertical_px=float(motion[0]), across_px=float(motion[1]))
    if args.compare: result.update(compare(a, b))
    if args.json:
        print(json.dumps(result))
    else:
        print(f'{args.moving} vs {args.reference}: vertical(rows) {motion[0]:+.4f} px, across(cols) {motion[1]:+.4f} px')
        for key, value in result.items():
            if key not in ('reference', 'moving', 'vertical_px', 'across_px'): print(f'  {key}: {value}')


if __name__ == '__main__':
    main()
