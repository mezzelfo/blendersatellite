# Step 23 report: validation of 20261117T152130Z_run_Vs0yvl (hex7_async_10deg, raw + all PNGs, walls, EEVEE)

**RUN** = `output/orbits_melbourne/20261117T152130Z_run_Vs0yvl`. This is the newest run. Its pipeline.log shows the `hex7_async_10deg.json` acquisition, `walls ON (WALLS=1, threshold 2 m)` and `--engine EEVEE` on every render job.

Nothing in RUN was modified. All new files are under `RUN/validation/`, plus one new script (below). Nothing was committed, deleted or downloaded.

## Scripts

- **validate_acquisition.py** ran as is on `RUN/simulation`. Check E needs `--reference`/`--legacy` runs, and none are on disk. As a stand-in, this run's table was compared with report 21's async table.
- **validate_run.py** was not run as is, for two reasons:
  - it writes `validation_report.md` and `validation_summary.json` into the RUN root;
  - it fails fore/aft satellites by design: footprint overlap with the chief must be ≥ 0.95 and alpha IoU ≥ 0.9, and it requires a `logs/` folder.
- **check_stacking_geometry.py** is hard-wired to eevee_full_1000 and its `validation_summary.json`.
- **New script: `scripts/validate_async_run.py`** (untracked, 644 lines). It covers the checks the existing scripts can't do on this run, and reuses their functions:
  - `validate_run.check_png`;
  - `phase_correlation.phase_shift`, used as the S11 estimator (Hann window, σ = 0.05);
  - `stacking_geometry` (poses.jsonl, `pixel_from_ground` and `ground_from_pixel`);
  - the nearest-sample and bilinear-sweep method of check_stacking_geometry.

## A. Completeness

| Check | Result |
|---|---|
| pipeline.log | 14,687 lines; second to last "Completed run: …", last "Run exited with status 0"; no error/traceback/exception/fail/killed line (only the known `Material.use_nodes` DeprecationWarning) |
| PNGs | 7 × 1000 present, 7000/7000 valid (4096 × 3072, RGBA, 8 bit, IEND, full decode); 7.80–8.03 GB per satellite; only extra file per folder: poses.jsonl |
| Render report | 12 jobs, 7000 renders: 7 + 7 (band rows, frames 0 and 999) + 693 + 8 × 700 + 693; every satellite has frames 0..999; logged coverage = PNG coverage (max dev 0) |
| Job setup | 25,667,844 triangles, EEVEE 8 samples, Vulkan, TITAN Xp, 64.7–65.8 s setup, 3.34–3.55 s per render (mean per job) |
| GPU memory per job | after setup 5715 MiB (5723 for 500:600); max of the per-render nvidia-smi snapshots 7054, 7054, 7069, 7095, 7069, 7069, 7069, 7069, 7069, 7069, 7144, 7069 MiB (sampled after each render, not a true peak) |
| raw/sat_SS.npy | 7 × (256, 1000, 4096, 4) uint8, matches each sat_SS.json; rows/band starts identical to bands.json (32 bands × 8 TDI rows, starts multiple of 4, usable rows 1019–1941); 1000 frame times; extracted_frames 1000; 256/256 rows see the scene |
| progress.json | frames 0..999 complete |
| raw vs PNG | 10,000 random (row, frame, column) per satellite: **10,000/10,000 equal** (RGBA) for all 7 (about 2930–3010 of them with alpha > 0) |
| terrain_render | 570/570 tiles (= selected_tiles), all with wall keys, 5,934,979 wall segments; cache_frame.json walls threshold 2.0 m, max angle 60°, fallback 2 cells, spacing 2.5 m, frame = cameras.json |

## B.1 validate_acquisition.py

`RUN/validation/B1_validate_acquisition.md`. The along GSD is in the table. The across GSD was measured at frame 500 with `stacking_geometry.ground_pixel_size`: 4.750 m for the chief, 4.818, 4.818, 4.822 and 4.812 m for satellites 1, 3, 4 and 6.

| sat | role | t centre [s] | lead [s] | incidence [°] | off-nadir [°] | range [km] | along GSD [m] | rows/frame | col drift [px] |
|---|---|---|---|---|---|---|---|---|---|
| 0 | nadir | +0.0000 | | 0.1865 | 0.0000 | 500.868 | 4.750 | 1.00831 | +0.000 |
| 1 | fore | −11.6836 | +11.6836 | 10.0729 | 9.5006 | 508.040 | 4.893 | 0.97808 | −0.000 |
| 2 | nadir | +0.0000 | | 0.0843 | 0.0948 | 500.931 | 4.750 | 1.00818 | +0.000 |
| 3 | aft | +11.2579 | −11.2579 | 9.9593 | 9.0578 | 508.119 | 4.892 | 0.97937 | +0.000 |
| 4 | fore | −11.6836 | +11.6836 | 9.9272 | 9.3654 | 508.533 | 4.896 | 0.97757 | −0.000 |
| 5 | nadir | +0.0000 | | 0.2888 | 0.0949 | 500.807 | 4.749 | 1.00842 | +0.000 |
| 6 | aft | +11.2579 | −11.2579 | 10.0423 | 9.1350 | 507.400 | 4.886 | 0.98051 | +0.000 |

All satellites put the scene point at (1535.500, 2047.500), with a column span of 0.007–0.008 px.

- **A:** 6000 aimed records, max |stored − recomputed| = 0; chief vs the ground_motion rule 1.50e-15.
- **D:** nadir–fore 10.182° (B/H 0.178), nadir–aft 9.818° (0.172), fore–aft 20.000° (0.353).
- **F:** target_recompute 0, basis_recompute 0, target_px 9.73e-11, pixel_step_dev 2.68e-06.
- **E:** not run (no reference runs). Every printed value of the table, A and D is identical to report 21's hex7_async_10deg run; F's target_px is 9.73e-11 here against 9.91e-11 there.

## B.2 poses.jsonl vs cameras.json

For all 7 satellites: 1000 lines, frames exactly 0..999, no duplicates. The maximum |Δposition| is **0 m**, the maximum |Δ camera_to_local element| is **0**, and the maximum |Δt_s| is 0. The rotation-angle formula returns ≤ 3.7e-8 rad on these identical matrices; that is arccos round-off.

## B.3 Projection of terrain vertices

200,000 random grid vertices out of 7,024,880, and 200,000 wall-segment centres out of 5,934,979. Nearest-pixel |dRGB| uses alpha > 0. The sub-pixel offset is the minimum of the bilinear median over ±0.5 px in steps of 0.125, refined with a parabola.

| sats (role) | grid median / mean [DN] | grid best offset (rows, cols) [px] | walls all, median | walls facing camera, median |
|---|---|---|---|---|
| 0, 2, 5 (nadir), 9 cases | 6.0–6.33 / 10.3 | −0.010…+0.001, +0.005…+0.012 | 20.67 | 19.7–20.7 |
| 1, 4 (fore), 6 cases | 6.33 / 10.8–10.9 | −0.015…−0.003, +0.010…+0.017 | 19.67 | 17.33 |
| 3, 6 (aft), 6 cases | 6.33 / 10.9–11.0 | −0.008…+0.001, +0.001…+0.018 | 20.33 | 18.67 |

- **Grid:** the offset is within 0.02 px in every case. Projection, poses and PNG convention agree.
- **Walls:** the walls row does not validate the walls geometrically.
  - The median is 17–21 DN, and the bilinear sweep is flat (best vs offset 0 within 0.5 DN). The "best offset" wanders between −0.2 and +0.5 rows and sometimes sits on the edge of the sweep.
  - At 10° a 2.5 m wall segment covers about 0.09 px on the image, and at nadir about 0. The pixel at a wall centre is therefore mostly roof or ground.
  - Facing walls (25% of segments for fore/aft, by the grid height on either side) are 2–3 DN better only in fore/aft.

## B.4 Ground motion, frame k → k+50

Integer shift from the masked SSD; sub-pixel shift from S11 phase correlation over the scene box. The prediction is the alpha-weighted mean of the z = 0 pose map.

| sat | role | k = 0 rows, cols | k = 475 | k = 949 | integer | measured − predicted rows |
|---|---|---|---|---|---|---|
| 0 | nadir | +50.416, +0.001 | +50.415, +0.002 | +50.415, +0.001 | 50, 0 | +0.001…+0.002 |
| 2 | nadir | +50.409, −0.000 | +50.408, +0.001 | +50.410, −0.001 | 50, 0 | 0.000…+0.002 |
| 5 | nadir | +50.422, +0.001 | +50.421, +0.001 | +50.420, +0.001 | 50, 0 | +0.001 |
| 1 | fore | +48.804, −0.029 | +48.884, −0.031 | +48.969, −0.031 | 49, 0 | +0.008…+0.012 |
| 4 | fore | +48.783, −0.029 | +48.855, −0.029 | +48.939, −0.031 | 49, 0 | +0.004…+0.012 |
| 3 | aft | +49.065, +0.032 | +48.985, +0.030 | +48.908, +0.029 | 49, 0 | −0.009…−0.006 |
| 6 | aft | +49.120, +0.031 | +49.046, +0.030 | +48.964, +0.029 | 49, 0 | −0.009…−0.005 |

- **Rows:** nadir +50.41 = 50 × 1.0083. Fore/aft move 48.8–49.1 rows (50 × 0.978–0.981) and change over the window: fore speeds up and aft slows down, as predicted.
- **Columns:** the drift is ±0.03 px over 50 frames, ±0.6 px over the window. B.1 reports col drift 0.000 because it is measured at the scene-centre point. Here the drift is the alpha-weighted mean over the whole scene, and matches the prediction to 0.002 px.

## C.1 Scene coverage (alpha = 255 fraction)

| sat | role | frame 0 | 500 | 999 |
|---|---|---|---|---|
| 0 | nadir | 0.14987 | 0.14987 | 0.14985 |
| 1 | fore | 0.14265 | 0.14302 | 0.14341 |
| 2 | nadir | 0.14983 | 0.14983 | 0.14983 |
| 3 | aft | 0.14349 | 0.14314 | 0.14277 |
| 4 | fore | 0.14246 | 0.14281 | 0.14320 |
| 5 | nadir | 0.14991 | 0.14990 | 0.14988 |
| 6 | aft | 0.14386 | 0.14350 | 0.14314 |

Fore/aft are 4–5% below nadir (larger GSD). Fore coverage rises and aft coverage falls over the window, as their range changes.

## C.2 Same role, frame 500

| pair | S11 shift (rows, cols) | predicted z = 0 | \|dRGB\| before | after global shift (symmetric) | pose-mapped + residual | pose map spread p5…p95 (rows / cols) |
|---|---|---|---|---|---|---|
| 0–2 nadir | +0.006, +0.048 | +0.023, +0.048 | 0.99 | 0.98 | 0.83 | −0.06…0.11 / −0.03…0.14 |
| 0–5 nadir | −0.005, −0.046 | −0.023, −0.047 | 0.96 | 0.96 | 0.82 | −0.11…0.05 / −0.13…0.03 |
| 2–5 nadir | −0.012, −0.094 | −0.046, −0.096 | 1.90 | 1.78 | 1.37 | −0.22…0.11 / −0.27…0.07 |
| 1–4 fore | −0.115, +0.363 | −0.101, +0.357 | 5.86 | 4.91 | 2.45 | −0.68…0.34 / −0.27…1.10 |
| 3–6 aft | −0.333, −0.520 | −0.407, −0.527 | 9.82 | 7.42 | 2.76 | −1.26…0.63 / −1.59…0.39 |

- **Shifts** are sub-pixel and match the poses within 0.07 px. The residual after pose mapping is ≤ 0.02 px.
- **Fore and aft pairs need more than a global shift.** Satellites 3 and 6 differ in range by 0.14% (508.12 vs 507.40 km), and 1 and 4 by 0.10%. This scale difference spreads the per-pixel shift over up to 1.9 px across the scene, so a single global shift still leaves 4.9–7.4 DN. Per-pixel pose mapping brings this to 2.5–2.8 DN.
- **Unexplained remainder:** fore/aft pairs stay about 2× the nadir pairs (0.8–1.4 DN). Fore/aft cross-pair parallax at 100 m is about 0.07 px, which is too small to explain it. One untested explanation: bilinear resampling of the fore/aft images (fractional phases up to 1 px) against almost none for the nadir pairs.

## C.3 Fore (1) vs aft (3), CBD, frame 500

- **Ground grid:** both images were resampled on z = 0 onto the chief's frame-500 pixels: 512 × 512 px = 2.43 km, centred at local (−750, 0) m. That is the 1 km window with the largest height relief, mean p95 − p5 = 108 m: the Hoddle grid and Southbank.
- **Co-registration:** global S11 shift +3.57 rows, +0.11 cols (≈ 48 m mean height × 0.074 px/m).
- **Block matching:** 32 px blocks, search ±30 rows, ±4 cols, parabolic sub-pixel; 209 of 210 blocks valid.

| Quantity | Value |
|---|---|
| Residual row displacement | range −7.84…+11.92 px; p5…p95 −3.17…+2.30 px |
| Residual col displacement | range −2.18…+1.49 px; p5…p95 −0.24…+0.22 px |
| Block mean height (terrain_render, AHD) | 0.1…63.1 m |
| corr(row disp., block mean / p90 / max height) | **0.73 / 0.78 / 0.48** |
| corr(col disp., mean height) | 0.13 |
| Predicted slope (tan 10.073° + tan 9.959°) / 4.750 m | 0.0744 px/m |
| Fitted slope: all blocks vs mean height / vs p90 height / 74 uniform blocks (height std < 10 m) | 0.095 / 0.044 / **0.072** px/m |
| Fore−aft \|dRGB\| on the ortho (z = 0) → orthorectified on the terrain heights | **15.99 → 5.68 DN** (tall pixels > 30 m: 14.44 → 6.68) |
| Residual global shift after orthorectifying on the terrain heights | +0.92 rows, +0.07 cols |

- **The displacement grows with building height.** On blocks of uniform height, the slope is within 3% of the geometric prediction. Fits on all blocks are biased either way by blocks that mix towers and ground.
- **Open point:** after orthorectifying on the terrain heights, a residual of 0.92 rows (about 12 m of height) remains between fore and aft. Not explained. Possible causes, not tested: occlusion behind towers, walls visible on opposite sides in the two views, and the per-pixel height average at roof edges.

## C.4 Walls vs grid only, satellite 1 frame 500

The grid-only terrain was converted from the same cache into `validation/terrain_grid`: 570 tiles, 13,797,886 triangles, 13 s. It was rendered with EEVEE on the same GPU at 3738 MiB, against 5715 MiB after setup with walls.

| Quantity | Value |
|---|---|
| Mean \|dRGB\| (1,799,300 opaque pixels in both) | **0.71 DN** |
| Pixels > 2 DN / > 10 DN | 8.5% / 1.15% |
| Alpha disagreement | 323 pixels |
| S11 shift grid → walls | **−0.048 rows, +0.008 cols** |
| Mean \|dRGB\| inside the CBD box / outside | 1.44 / 0.59 DN |
| Share of changed pixels (> 2 DN) inside the CBD box | 26.5% (the box is 13.8% of the scene) |
| On the CBD grid: \|dRGB\| where height < 10 m / > 50 m | 0.55 / 4.42 DN; corr with height 0.38 |
| Bounding box of changed pixels | rows 539–2425, cols 685–2557 (the whole scene) |

- **Shift** is (0, 0) within 0.05 px.
- **Where the differences are:** they are 2.4× denser in the CBD and grow with height, but walls change pixels across the whole city.
- **Compared with nadir:** the > 2 DN share is 8.5% here against 0.35% at nadir (README).

## C.5 Contact sheet

`validation/contact_sheet.png` (4.1 MB, 1560 × 1650) holds nine panels:
- frame 500 of the 7 satellites, on the same 2.43 km CBD ground grid (z = 0), labelled with satellite and role;
- the fore−aft |dRGB| (× 4) map;
- the block row-displacement map.

## Files created

| File | Size |
|---|---|
| validation/A_completeness.json, .log | 12 KB, 159 B |
| validation/alpha_per_frame.npz (alpha = 255 and alpha > 0.5 fractions, 7 × 1000) | 29 KB |
| validation/B1_validate_acquisition.md | 1.9 KB |
| validation/B2_poses.json | 1.8 KB |
| validation/B3_projection.json, .log | 23 KB, 4.2 KB |
| validation/B4_motion.json, .log | 4.2 KB, 3.9 KB |
| validation/C_compare.json, .log | 5.5 KB, 5.1 KB |
| validation/contact_sheet.png | 4.1 MB |
| validation/terrain_grid/ (570 npz + cache_frame.json), terrain_grid.log | 87 MB, 1.4 KB |
| validation/render_grid/satellite_01/frame_0500.png, poses.jsonl, render_report.jsonl, render_grid.log | 7.5 MB, 2.2 KB, 2.4 KB, 648 B |
| validation/report_23.md | this file |
| scripts/validate_async_run.py (repository, untracked) | 644 lines |
