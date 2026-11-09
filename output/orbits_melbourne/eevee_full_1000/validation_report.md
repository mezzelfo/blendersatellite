# Validation report: full 1000-frame swarm render

**Verdict: PASS WITH NOTES**

Notes:

- 10 log lines are the Blender DeprecationWarning for Material.use_nodes (render_swarm.py:158, once per satellite run); benign until Blender 6.0
- chief ground advance 1.0000 px/frame (Earth rotation + real ground speed 0.05% above v_g): expected, dt kept on purpose
- chief phase-correlation vertical shift is 1.0000 px, up to 0.0000 px below the pose prediction (constant across the window: estimator bias, not a timing error)
- deputy-vs-chief image shift up to 0.66 px, drifting linearly over the window; consistent with each satellite having its own range/GSD (scale about the image centre while the scene crosses the frame); footprint and alpha IoU confirm the same ground area
- only 12.0 GB free on the output volume
- only satellites [0, 1, 2, 3, 4] rendered (5000 PNGs); propagation and formation checks include all 7 satellites

Run folder: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/eevee_full_1000`  
Simulation: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/20261109T180310Z_run_v2/simulation`  
Generated 2026-11-09T21:55:43Z by `scripts/validate_run.py` in 5.0 min (read-only on the outputs).

## 1. Folders and frames

| folder | PNGs | missing | unexpected | duplicate names | other files |
|---|---|---|---|---|---|
| satellite_00 | 1000 | 0 | 0 | 0 | poses.jsonl |
| satellite_01 | 1000 | 0 | 0 | 0 | poses.jsonl |
| satellite_02 | 1000 | 0 | 0 | 0 | poses.jsonl |
| satellite_03 | 1000 | 0 | 0 | 0 | poses.jsonl |
| satellite_04 | 1000 | 0 | 0 | 0 | poses.jsonl |

5 satellite folders (expected 5).

## 2. PNG integrity

- 5000 PNGs checked, 5000 pass: signature, IHDR 4096x3072, bit depth 8, colour type 6 (RGBA), IEND present, Pillow mode RGBA, full decode.
- File size min / mean / max: 8.125 / 8.140 / 8.159 MB; total 40.70 GB.
- Byte-identical files: 0 groups.

## 3. Alpha coverage (fraction of pixels with alpha > 0.5)

| satellite | min (frame) | mean | max (frame) | frames with 0 | max(alpha>0) - coverage |
|---|---|---|---|---|---|
| 00 | 15.0520% (999) | 15.0525% | 15.0529% (1) | 0 | 8.89e-04 |
| 01 | 15.0731% (999) | 15.0734% | 15.0736% (9) | 0 | 8.90e-04 |
| 02 | 15.0487% (981) | 15.0489% | 15.0490% (410) | 0 | 8.89e-04 |
| 03 | 15.0276% (868) | 15.0280% | 15.0284% (52) | 0 | 8.89e-04 |
| 04 | 15.0308% (983) | 15.0316% | 15.0323% (21) | 0 | 8.90e-04 |

Max difference from the coverage logged by render_swarm (every 50th frame): 0.00e+00.

## 4. Poses

Time grid: 1000 samples, dt = 0.6661251071 ms, max |diff(t) - dt| = 1.01e-16 s, t = 0.0 .. 0.665458982 s.

Consecutive position difference vs |v| dt (|v| of the mean of the two endpoint velocities, rel tol 1e-6):

| sat | rendered | |dr| min / mean / max (m) | speed mean (m/s) | max rel err (step) | steps > 1e-6 | poses.jsonl vs npz (m) | t_s dev (s) |
|---|---|---|---|---|---|---|---|
| 00 | yes | 5.073577 / 5.073579 / 5.073582 | 7616.5559 | 3.94e-09 (18) | 0 | 0.0e+00 | 0.0e+00 |
| 01 | yes | 5.073837 / 5.073839 / 5.073842 | 7616.9466 | 5.48e-09 (200) | 0 | 0.0e+00 | 0.0e+00 |
| 02 | yes | 5.073531 / 5.073533 / 5.073536 | 7616.4869 | 3.49e-09 (200) | 0 | 0.0e+00 | 0.0e+00 |
| 03 | yes | 5.073270 / 5.073273 / 5.073276 | 7616.0963 | 4.79e-10 (13) | 0 | 0.0e+00 | 0.0e+00 |
| 04 | yes | 5.073316 / 5.073319 / 5.073321 | 7616.1653 | 7.96e-09 (18) | 0 | 0.0e+00 | 0.0e+00 |
| 05 | no | 5.073623 / 5.073625 / 5.073627 | 7616.6249 | 2.31e-09 (18) | 0 | nan | nan |
| 06 | no | 5.073883 / 5.073885 / 5.073888 | 7617.0156 | 4.59e-10 (245) | 0 | nan | nan |

Common target (WGS84 point the chief boresight hits): distance of its projection from the image centre.

| sat | re-projected from poses.jsonl, max px (frame) | render_report.jsonl entries | logged max px |
|---|---|---|---|
| 00 | 9.33e-11 (820) | 1000 | 1.83e-04 |
| 01 | 0.00e+00 (0) | 1000 | 1.83e-04 |
| 02 | 0.00e+00 (0) | 1000 | 1.83e-04 |
| 03 | 2.27e-13 (962) | 1000 | 1.83e-04 |
| 04 | 2.27e-13 (12) | 1000 | 1.83e-04 |

Chief ground advance per frame (999 steps):

| quantity | min | mean | max |
|---|---|---|---|
| ground centre displacement (m) | 4.7496 | 4.7496 | 4.7497 |
| ground centre displacement / native GSD (px) | 0.99999 | 1.00001 | 1.00002 |
| image motion, rows (px) | +0.99998 | +1.00000 | +1.00002 |
| image motion, columns (px) | -0.00000 | -0.00000 | -0.00000 |
| image motion, norm (px) | 0.99998 | 1.00000 | 1.00002 |

Total chief ground-centre travel frame 0 -> 999: 4744.89 m. Image motion = frame-k ground centre projected into camera k+1 (rows +down).

## 5. Formation (all propagated satellites, chief RTN from Cartesian r, v)

Nominal T-N edge 894.427 m +/- 1 m; 3D neighbour distance <= 1001 m.

Frame 0: T-N 894.364 .. 894.490 m (max dev 0.063 m); 3D 896.568 .. 985.924 m.

Frame 499: T-N 894.364 .. 894.490 m (max dev 0.063 m); 3D 896.557 .. 985.899 m.

Frame 999: T-N 894.364 .. 894.490 m (max dev 0.063 m); 3D 896.546 .. 985.872 m.

| edge | T-N f0 (m) | 3D f0 (m) | T-N f499 (m) | 3D f499 (m) | T-N f999 (m) | 3D f999 (m) |
|---|---|---|---|---|---|---|
| 0-1 | 894.382 | 961.293 | 894.382 | 961.330 | 894.382 | 961.367 |
| 0-2 | 894.408 | 896.578 | 894.408 | 896.567 | 894.408 | 896.556 |
| 0-3 | 894.490 | 985.924 | 894.490 | 985.899 | 894.490 | 985.872 |
| 0-4 | 894.472 | 961.339 | 894.472 | 961.376 | 894.472 | 961.413 |
| 0-5 | 894.445 | 896.620 | 894.445 | 896.609 | 894.445 | 896.598 |
| 0-6 | 894.364 | 985.855 | 894.364 | 985.829 | 894.364 | 985.803 |
| 1-2 | 894.436 | 985.906 | 894.436 | 985.880 | 894.436 | 985.854 |
| 2-3 | 894.380 | 961.265 | 894.380 | 961.302 | 894.380 | 961.339 |
| 3-4 | 894.395 | 896.568 | 894.395 | 896.557 | 894.395 | 896.546 |
| 4-5 | 894.418 | 985.874 | 894.418 | 985.848 | 894.418 | 985.822 |
| 5-6 | 894.474 | 961.367 | 894.474 | 961.404 | 894.474 | 961.441 |
| 6-1 | 894.457 | 896.630 | 894.457 | 896.619 | 894.457 | 896.607 |

## 6. Chief phase correlation (upsampled DFT, 1/1000 px, luminance x alpha)

| frames | vertical (rows, px) | across (cols, px) | predicted from poses rows / cols (px) | |shift| (px) |
|---|---|---|---|---|
| 0/1 | +1.0000 | -0.0000 | +1.0000 / -0.0000 | 1.0000 |
| 499/500 | +1.0000 | -0.0000 | +1.0000 / -0.0000 | 1.0000 |
| 998/999 | +1.0000 | -0.0000 | +1.0000 / -0.0000 | 1.0000 |

Expected vertical about 1.01 px.

## 7. Same ground area across satellites (every 100th frame)

Footprint overlap = area(deputy footprint ∩ chief footprint) / area(chief footprint), from cameras.json for all 7 satellites. Image checks (rendered satellites only): alpha IoU with the chief and phase-correlation shift deputy vs chief (1/100 px).

| frame | sat 01 | sat 02 | sat 03 | sat 04 | sat 05 | sat 06 |
|---|---|---|---|---|---|---|
| 0 | 99.8596% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8352% |
| 100 | 99.8596% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8352% |
| 200 | 99.8596% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8352% |
| 300 | 99.8595% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8352% |
| 400 | 99.8595% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8352% |
| 500 | 99.8595% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8353% |
| 600 | 99.8595% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8353% |
| 700 | 99.8595% | 100.0000% | 100.0000% | 100.0000% | 99.9763% | 99.8353% |
| 800 | 99.8595% | 100.0000% | 100.0000% | 100.0000% | 99.9763% | 99.8353% |
| 900 | 99.8594% | 100.0000% | 100.0000% | 100.0000% | 99.9763% | 99.8353% |

Footprint overlap min / mean: 99.8352% / 99.9452%; ground-centre offset from chief max 0.001 m.

| frame | sat 01 IoU / shift r,c (px) | sat 02 IoU / shift r,c (px) | sat 03 IoU / shift r,c (px) | sat 04 IoU / shift r,c (px) |
|---|---|---|---|---|
| 0 | 0.9977 / -0.49, -0.20 | 0.9996 / +0.06, +0.03 | 0.9973 / +0.61, +0.25 | 0.9977 / +0.50, +0.20 |
| 100 | 0.9978 / -0.40, -0.20 | 0.9996 / +0.05, +0.03 | 0.9974 / +0.51, +0.25 | 0.9978 / +0.41, +0.20 |
| 200 | 0.9979 / -0.32, -0.20 | 0.9996 / +0.04, +0.03 | 0.9975 / +0.40, +0.25 | 0.9979 / +0.32, +0.20 |
| 300 | 0.9979 / -0.24, -0.20 | 0.9996 / +0.03, +0.03 | 0.9975 / +0.30, +0.25 | 0.9979 / +0.24, +0.20 |
| 400 | 0.9979 / -0.17, -0.20 | 0.9996 / +0.02, +0.03 | 0.9975 / +0.21, +0.25 | 0.9979 / +0.17, +0.20 |
| 500 | 0.9979 / -0.11, -0.20 | 0.9996 / +0.01, +0.03 | 0.9976 / +0.14, +0.25 | 0.9979 / +0.11, +0.20 |
| 600 | 0.9979 / -0.06, -0.20 | 0.9996 / -0.00, +0.03 | 0.9976 / +0.07, +0.25 | 0.9979 / +0.06, +0.20 |
| 700 | 0.9979 / -0.01, -0.20 | 0.9997 / -0.00, +0.03 | 0.9976 / -0.00, +0.25 | 0.9979 / +0.01, +0.20 |
| 800 | 0.9979 / +0.04, -0.19 | 0.9997 / -0.01, +0.03 | 0.9976 / -0.06, +0.24 | 0.9979 / -0.05, +0.20 |
| 900 | 0.9979 / +0.10, -0.19 | 0.9997 / -0.02, +0.03 | 0.9976 / -0.12, +0.24 | 0.9979 / -0.10, +0.19 |

## 8. Logs, disk, render time

- Log lines flagged (error / warning / traceback / fail / killed / OOM): 0; benign DeprecationWarning lines: 10.
- Disk used by the run folder: 40.72 GB (37.92 GiB); free on the volume: 12.0 GB.
- Wall clock first start -> last finish: 1.954 h (7033 s).

| satellite | start (UTC) | done (UTC) | wall (min) | frames in report | sum render_seconds (min) | render s/frame min / mean / max |
|---|---|---|---|---|---|---|
| 00 | 18:18:09 | 19:27:01 | 68.9 | 1000 | 67.9 | 3.06 / 4.07 / 5.45 |
| 01 | 19:27:01 | 20:15:22 | 48.4 | 1000 | 47.5 | 2.71 / 2.85 / 4.16 |
| 02 | 18:18:10 | 19:27:00 | 68.8 | 1000 | 67.9 | 3.20 / 4.07 / 5.77 |
| 03 | 18:18:09 | 19:26:23 | 68.2 | 1000 | 67.3 | 3.20 / 4.04 / 5.64 |
| 04 | 18:18:09 | 19:26:17 | 68.1 | 1000 | 67.2 | 3.23 / 4.03 / 5.71 |

Total render time (sum of per-frame render_seconds, incl. PNG write): 5.295 GPU-process hours; scene setup per run: 31.7, 32.0, 32.3, 32.5, 30.2 s.

