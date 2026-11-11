"""S11: bias curve of the shift estimator on KNOWN fractional shifts of real chief cube slices.

For each layout (compact, diagnostic; chief, mode none) take slice 0 as luminance x alpha
(the estimator input of check_cubes.py), shift it along the rows by d = 0.00 .. 1.00 px
(step 0.05) with
  - Fourier shift: multiplication of the row spectrum by exp(-2 pi i f d) (circular), and
  - bilinear shift: mov[g] = (1 - d) ref[g] + d ref[g - 1] (edge row replicated),
and measure the motion with plain phase correlation and with Hann + Gaussian low-pass on the
normalised cross-power spectrum for sigma in 0.15, 0.10, 0.08, 0.05 cycles/px.
bias = measured motion (rows) - d; the column motion should be 0 and is reported too.

Choice rule (S11): sigma with max |bias| < 0.005 px for both shift methods and both layouts;
among those that pass, the LARGEST sigma (least low-pass, most of the spectrum kept).

Writes <run>/cubes/shift_estimator_bias.{png,json}; prints a markdown summary.
Run in the conda environment orbits: python scripts/shift_bias_curve.py
"""
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import check_cubes as cc
import stacking_geometry as sg
from phase_correlation import phase_shift

SHIFTS = np.round(np.arange(0, 1.0001, 0.05), 2)
SIGMAS = (0.15, 0.10, 0.08, 0.05)
LIMIT = 0.005
ESTIMATORS = [('plain', dict())] + [(f'hann+lp {s:.2f}', dict(hann=True, sigma=s)) for s in SIGMAS]


def fourier_shift(img, d):
    f = np.fft.fftfreq(img.shape[0])[:, None]
    return np.real(np.fft.ifft(np.fft.fft(img, axis=0) * np.exp(-2j * np.pi * f * d), axis=0))


def bilinear_shift(img, d):
    prev = np.vstack([img[:1], img[:-1]])            # ref[g - 1], edge replicated
    return (1 - d) * img + d * prev


def main():
    run = sg.RUN_DIR
    res = {}
    for layout in cc.LAYOUTS:
        ref = cc.gray(cc.load_cube(run, layout, 'none', 0)['cube'][:, :, 0])
        for method, fn in (('fourier', fourier_shift), ('bilinear', bilinear_shift)):
            for name, kw in ESTIMATORS:
                m = np.array([-phase_shift(ref, fn(ref, d), 1000, **kw) for d in SHIFTS])
                res[layout, method, name] = dict(bias_rows=(m[:, 0] - SHIFTS).tolist(), cols=m[:, 1].tolist())
    # summary + choice
    rows, passing = [], []
    for name, kw in ESTIMATORS:
        worst = {k: max(abs(np.array(res[l, k, name]['bias_rows'])).max() for l in cc.LAYOUTS) for k in ('fourier', 'bilinear')}
        wc = max(abs(np.array(res[l, k, name]['cols'])).max() for l in cc.LAYOUTS for k in ('fourier', 'bilinear'))
        per = {f'{l} {k}': abs(np.array(res[l, k, name]['bias_rows'])).max() for l in cc.LAYOUTS for k in ('fourier', 'bilinear')}
        ok = max(worst.values()) < LIMIT
        if ok and 'sigma' in kw: passing.append(kw['sigma'])
        rows.append([name] + [f'{v:.4f}' for v in per.values()] + [f'{wc:.4f}', 'yes' if ok else 'no'])
    print('# S11 shift-estimator bias on known shifts (chief slice 0, mode none)\n')
    print(cc.md(['estimator'] + [f'max |bias| {k}' for k in per] + ['max |cols|', f'< {LIMIT} px'], rows))
    chosen = max(passing) if passing else None
    print(f'\nPassing sigmas: {passing}; chosen (largest passing): {chosen}')
    # plot
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    for i, layout in enumerate(cc.LAYOUTS):
        for j, method in enumerate(('fourier', 'bilinear')):
            ax = axes[i, j]
            for name, _ in ESTIMATORS:
                ax.plot(SHIFTS, res[layout, method, name]['bias_rows'], marker='o', ms=3, label=name)
            ax.axhspan(-LIMIT, LIMIT, color='0.85', zorder=0)
            ax.axhline(0, color='k', lw=0.5)
            ax.set_title(f'{layout}, {method} shift'); ax.set_ylabel('bias = measured - true (rows px)')
            if i == 1: ax.set_xlabel('true shift (rows px)')
    axes[0, 0].legend(fontsize=8)
    fig.suptitle(f'Shift-estimator bias on known shifts (grey band: |bias| < {LIMIT} px); chosen sigma = {chosen}')
    fig.tight_layout()
    out = run / 'cubes' / 'shift_estimator_bias.png'
    fig.savefig(out, dpi=110)
    (run / 'cubes' / 'shift_estimator_bias.json').write_text(json.dumps(dict(
        shifts=SHIFTS.tolist(), limit_px=LIMIT, rule='largest sigma with max |bias| < limit for both shift methods and layouts',
        chosen_sigma=chosen, passing=passing,
        curves={f'{l}|{k}|{n}': v for (l, k, n), v in res.items()}), indent=1))
    print(f'\nplot: {out}')
    if chosen is None: print('\n**STOP: no sigma meets the bias limit**')


if __name__ == '__main__':
    main()
