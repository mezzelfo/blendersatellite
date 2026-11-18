"""Summary figures and tables of the cube-mode inspection (step 25 report).

Reads <run>/cubes/inspection/registration.json (inspect_cubes.py) and <run>/raw/bands.json, writes
to --out (default <run>/report_25/figures):
    summary_max_shift.png         max |displacement| over the bands, rows and columns, per
                                  satellite and mode (log scale)
    summary_pred_vs_meas.png      measured vs predicted displacement of every band (none rows,
                                  none/linear cols, all blocks)
    summary_nadir_linear_rows.png residual row displacement of the nadir linear cubes vs band,
                                  with the relative interpolation phase (r_b - r_ref)(1/s - 1)
    summary_keystone_cols.png     column displacement vs band in the left / centre / right blocks,
                                  fore and aft satellites, linear vs bilinear
and copies the inspection figures used by the report. Prints the Markdown tables.

    python scripts/summarize_cube_inspection.py --run output/orbits_melbourne/<run>
"""
import argparse
import json
import shutil
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

MODES = ('none', 'linear', 'bilinear')
COLOR = {'none': '#eb6834', 'linear': '#2a78d6', 'bilinear': '#1baf7a'}   # categorical slots 2, 1, 3
INK, MUTED, GRID = '#222222', '#666666', '#dddddd'
COPY = (['overview_none.png', 'overview_linear.png', 'overview_bilinear.png']
        + [f'shifts_sat{s:02d}.png' for s in range(7)]
        + [f'modes_sat{s:02d}.png' for s in (0, 1, 3)]
        + [f'spectra_sat{s:02d}.png' for s in (0, 1, 3)]
        + [f'composite_sat01_{m}.png' for m in MODES] + [f'composite_sat00_{m}.png' for m in MODES]
        + ['mosaic_sat01_none.png', 'mosaic_sat01_bilinear.png'])


def style(ax):
    for side in ('top', 'right'): ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'): ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelcolor=INK)
    ax.grid(color=GRID, lw=0.6); ax.set_axisbelow(True)


def fig_max_shift(table, path):
    sats = sorted({r['satellite'] for r in table})
    roles = {r['satellite']: r['role'] for r in table}
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.2), sharey=True)
    w = 0.26
    for ax, comp, title in zip(axes, ('row', 'col'), ('along track (rows)', 'across track (columns), worst block')):
        for i, m in enumerate(MODES):
            vals = []
            for s in sats:
                r = next(x for x in table if x['satellite'] == s and x['mode'] == m)
                vals.append(r['all_max_abs_row'] if comp == 'row' else
                            max(r[f'{b}_max_abs_col'] for b in ('all', 'left', 'centre', 'right')))
            ax.bar(np.arange(len(sats)) + (i - 1) * w, vals, w * 0.92, color=COLOR[m], label=m)
        ax.set_yscale('log'); ax.set_ylim(0.005, 20)
        ax.set_xticks(np.arange(len(sats)), [f'{s}\n{roles[s]}' for s in sats])
        ax.set_title(f'max |displacement| vs band 16, {title}', fontsize=10, color=INK)
        style(ax)
    axes[0].set_ylabel('px (log scale)')
    axes[0].legend(frameon=False)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def fig_pred_vs_meas(details, path):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    for s, d in details.items():
        for blk in ('all', 'left', 'centre', 'right'):
            m, p = np.array(d['measured']['none'][blk]), np.array(d['predicted']['none'][blk])
            axes[0].plot(p[:, 0], m[:, 0], 'o', ms=3, color=COLOR['none'], alpha=.6)
            for mode in ('none', 'linear'):
                m, p = np.array(d['measured'][mode][blk]), np.array(d['predicted'][mode][blk])
                axes[1].plot(p[:, 1], m[:, 1], 'o', ms=3, color=COLOR[mode], alpha=.5)
    for ax, lim, title in ((axes[0], 12, 'rows, mode none'), (axes[1], 0.85, 'columns, modes none and linear')):
        ax.plot([-lim, lim], [-lim, lim], color=MUTED, lw=1)
        ax.set_xlabel('predicted from the stored frame indices [px]'); ax.set_ylabel('measured [px]')
        ax.set_title(title, fontsize=10, color=INK)
        style(ax)
    axes[1].plot([], [], 'o', color=COLOR['none'], label='none'); axes[1].plot([], [], 'o', color=COLOR['linear'], label='linear')
    axes[1].legend(frameon=False)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def fig_nadir_linear(details, starts, path):
    """Residual row displacement of the nadir linear cubes and the relative interpolation phase
    phi_b = (r_b - r_ref)(1/s - 1): distance of phi_b from the nearest integer (0 = band b and the
    reference band are interpolated at the same fractional frame phase)."""
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.8), sharex=True, gridspec_kw=dict(height_ratios=[3, 1.4]))
    bands = np.arange(len(starts))
    shades = {'0': '#1c5cab', '2': '#5598e7', '5': '#9ec5f4'}
    for s, c in shades.items():
        m = np.array(details[s]['measured']['linear']['all'])[:, 0]
        axes[0].plot(bands, m, 'o-', ms=4, lw=1.5, color=c, label=f'sat {s} (nadir)')
        phi = (starts - starts[len(starts) // 2]) * (1 / details[s]['rows_per_frame'] - 1)
        axes[1].plot(bands, np.abs(phi - np.round(phi)), 'o-', ms=3, lw=1.2, color=c)
    axes[0].set_ylabel('row displacement [px]')
    axes[0].set_title('nadir, mode linear: residual row displacement vs band 16', fontsize=10, color=INK)
    axes[0].legend(frameon=False, ncol=3)
    axes[1].set_ylabel('|relative frame\nphase| [frames]'); axes[1].set_xlabel('band')
    axes[1].set_ylim(0, 0.52)
    for ax in axes: style(ax)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def fig_keystone(details, path):
    sats = [s for s, d in details.items() if d['role'] != 'nadir']
    fig, axes = plt.subplots(len(sats), 3, figsize=(13, 2.4 * len(sats)), sharex=True, sharey=True)
    for i, s in enumerate(sats):
        for j, blk in enumerate(('left', 'centre', 'right')):
            ax = axes[i, j]
            for m in ('linear', 'bilinear'):
                y = np.array(details[s]['measured'][m][blk])[:, 1]
                p = np.array(details[s]['predicted'][m][blk])[:, 1]
                ax.plot(y, 'o-', ms=3, lw=1.2, color=COLOR[m], label=f'{m} measured')
                ax.plot(p, '--', lw=1, color=COLOR[m], label=f'{m} predicted')
            ax.set_title(f'sat {s} ({details[s]["role"]}), {blk} columns', fontsize=9, color=INK)
            style(ax)
            if j == 0: ax.set_ylabel('col disp. [px]')
    for ax in axes[-1]: ax.set_xlabel('band')
    axes[0, 0].legend(frameon=False, fontsize=7)
    fig.tight_layout(); fig.savefig(path, dpi=120); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', type=Path, required=True)
    ap.add_argument('--out', type=Path)
    args = ap.parse_args()
    out = args.out or args.run / 'report_25' / 'figures'
    out.mkdir(parents=True, exist_ok=True)
    insp = args.run / 'cubes' / 'inspection'
    data = json.loads((insp / 'registration.json').read_text())
    table, details = data['table'], data['details']
    starts = np.array(json.loads((args.run / 'raw' / 'bands.json').read_text())['band_start_rows'])
    fig_max_shift(table, out / 'summary_max_shift.png')
    fig_pred_vs_meas(details, out / 'summary_pred_vs_meas.png')
    fig_nadir_linear(details, starts, out / 'summary_nadir_linear_rows.png')
    fig_keystone(details, out / 'summary_keystone_cols.png')
    for name in COPY:
        shutil.copy2(insp / name, out / name)
    # Markdown tables
    print('| sat | role | mode | max abs row | max abs col | RMS | max abs col L / C / R | pred. max abs row / col |'
          ' max abs (meas - pred) row / col |')
    print('|---|---|---|---|---|---|---|---|---|')
    for r in table:
        print(f"| {r['satellite']} | {r['role']} | {r['mode']} | {r['all_max_abs_row']:.3f} | {r['all_max_abs_col']:.3f} | "
              f"{r['all_rms']:.3f} | {r['left_max_abs_col']:.3f} / {r['centre_max_abs_col']:.3f} / "
              f"{r['right_max_abs_col']:.3f} | {r['all_pred_max_abs_row']:.3f} / {r['all_pred_max_abs_col']:.3f} | "
              f"{r['all_max_abs_residual_row']:.3f} / {r['all_max_abs_residual_col']:.3f} |")
    print()
    print('| sat | role | rows/frame | region lines (g) | region cols | mean abs(linear - none) DN | '
          'mean abs(bilinear - linear) DN | p99 abs(lin - none) / (bil - lin) DN |')
    print('|---|---|---|---|---|---|---|---|')
    for s, d in details.items():
        rg, df = d['region'], d['differences']
        print(f"| {s} | {d['role']} | {d['rows_per_frame']:.5f} | {rg['g'][0]}-{rg['g'][1]} ({rg['lines']}) | "
              f"{rg['cols'][0]}-{rg['cols'][1]} ({rg['n_cols']}) | {df['mean_abs_linear_minus_none_DN']:.2f} | "
              f"{df['mean_abs_bilinear_minus_linear_DN']:.2f} | {df['diff_scale_linear_minus_none_DN']:.1f} / "
              f"{df['diff_scale_bilinear_minus_linear_DN']:.1f} |")
    print()
    print('| sat | point | g | col |')
    print('|---|---|---|---|')
    for s, d in details.items():
        for k, v in d['points'].items():
            print(f"| {s} | {k} | {v['g']} | {v['col']} |")


if __name__ == '__main__':
    main()
