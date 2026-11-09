# Validation report: full 1000-frame swarm render

**Verdict: PASS WITH NOTES**

Notes:

- 10 log lines are the Blender DeprecationWarning for Material.use_nodes (render_swarm.py:158, once per satellite run); benign until Blender 6.0
- chief ground advance 1.0102 px/frame (Earth rotation + real ground speed 0.05% above v_g): expected, dt kept on purpose
- chief phase-correlation vertical shift is 1.0060 px, up to 0.0043 px below the pose prediction (constant across the window: estimator bias, not a timing error)
- deputy-vs-chief image shift up to 0.67 px, drifting linearly over the window; consistent with each satellite having its own range/GSD (scale about the image centre while the scene crosses the frame); footprint and alpha IoU confirm the same ground area
- only 11.4 GB free on the output volume
- only satellites [0, 1, 2, 3, 4] rendered (5000 PNGs); propagation and formation checks include all 7 satellites

Run folder: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/eevee_full_1000`  
Simulation: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/20261106T080712Z_run_IxoFnH/simulation`  
Generated 2026-11-09T08:01:07Z by `scripts/validate_run.py` in 5.0 min (read-only on the outputs).

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
- File size min / mean / max: 8.136 / 8.156 / 8.174 MB; total 40.78 GB.
- Byte-identical files: 0 groups.

## 3. Alpha coverage (fraction of pixels with alpha > 0.5)

| satellite | min (frame) | mean | max (frame) | frames with 0 | max(alpha>0) - coverage |
|---|---|---|---|---|---|
| 00 | 15.1043% (999) | 15.1049% | 15.1055% (19) | 0 | 8.91e-04 |
| 01 | 15.1253% (0) | 15.1258% | 15.1262% (73) | 0 | 8.92e-04 |
| 02 | 15.1006% (0) | 15.1011% | 15.1013% (258) | 0 | 8.91e-04 |
| 03 | 15.0798% (964) | 15.0802% | 15.0806% (52) | 0 | 8.91e-04 |
| 04 | 15.0828% (983) | 15.0838% | 15.0846% (26) | 0 | 8.92e-04 |

Max difference from the coverage logged by render_swarm (every 50th frame): 0.00e+00.

## 4. Poses

Time grid: 1000 samples, dt = 0.6716580153 ms, max |diff(t) - dt| = 6.87e-17 s, t = 0.0 .. 0.670986357 s.

Consecutive position difference vs |v| dt (|v| of the mean of the two endpoint velocities, rel tol 1e-6):

| sat | rendered | |dr| min / mean / max (m) | speed mean (m/s) | max rel err (step) | steps > 1e-6 | poses.jsonl vs npz (m) | t_s dev (s) |
|---|---|---|---|---|---|---|---|
| 00 | yes | 5.116042 / 5.116044 / 5.116047 | 7617.0372 | 3.08e-09 (198) | 0 | 0.0e+00 | 0.0e+00 |
| 01 | yes | 5.116304 / 5.116307 / 5.116309 | 7617.4279 | 4.14e-09 (18) | 0 | 0.0e+00 | 0.0e+00 |
| 02 | yes | 5.115995 / 5.115998 / 5.116000 | 7616.9682 | 2.85e-09 (198) | 0 | 0.0e+00 | 0.0e+00 |
| 03 | yes | 5.115733 / 5.115735 / 5.115738 | 7616.5775 | 1.49e-09 (18) | 0 | 0.0e+00 | 0.0e+00 |
| 04 | yes | 5.115779 / 5.115782 / 5.115784 | 7616.6465 | 7.01e-09 (198) | 0 | 0.0e+00 | 0.0e+00 |
| 05 | no | 5.116088 / 5.116090 / 5.116093 | 7617.1062 | 4.79e-09 (18) | 0 | nan | nan |
| 06 | no | 5.116350 / 5.116353 / 5.116355 | 7617.4969 | 3.31e-09 (18) | 0 | nan | nan |

Common target (WGS84 point the chief boresight hits): distance of its projection from the image centre.

| sat | re-projected from poses.jsonl, max px (frame) | render_report.jsonl entries | logged max px |
|---|---|---|---|
| 00 | 9.91e-11 (251) | 1000 | 1.83e-04 |
| 01 | 0.00e+00 (0) | 1000 | 1.83e-04 |
| 02 | 0.00e+00 (0) | 1000 | 1.83e-04 |
| 03 | 0.00e+00 (0) | 1000 | 1.83e-04 |
| 04 | 2.27e-13 (6) | 1000 | 2.20e-04 |

Chief ground advance per frame (999 steps):

| quantity | min | mean | max |
|---|---|---|---|
| ground centre displacement (m) | 4.7900 | 4.7900 | 4.7900 |
| ground centre displacement / native GSD (px) | 1.01023 | 1.01025 | 1.01027 |
| image motion, rows (px) | +1.01023 | +1.01024 | +1.01026 |
| image motion, columns (px) | -0.00000 | -0.00000 | -0.00000 |
| image motion, norm (px) | 1.01023 | 1.01024 | 1.01026 |

Total chief ground-centre travel frame 0 -> 999: 4785.18 m. Image motion = frame-k ground centre projected into camera k+1 (rows +down).

## 5. Formation (all propagated satellites, chief RTN from Cartesian r, v)

Nominal T-N edge 894.427 m +/- 1 m; 3D neighbour distance <= 1001 m.

Frame 0: T-N 894.364 .. 894.490 m (max dev 0.063 m); 3D 896.568 .. 985.924 m.

Frame 499: T-N 894.364 .. 894.490 m (max dev 0.063 m); 3D 896.557 .. 985.898 m.

Frame 999: T-N 894.364 .. 894.490 m (max dev 0.063 m); 3D 896.545 .. 985.872 m.

| edge | T-N f0 (m) | 3D f0 (m) | T-N f499 (m) | 3D f499 (m) | T-N f999 (m) | 3D f999 (m) |
|---|---|---|---|---|---|---|
| 0-1 | 894.382 | 961.293 | 894.382 | 961.330 | 894.382 | 961.368 |
| 0-2 | 894.408 | 896.578 | 894.408 | 896.567 | 894.408 | 896.555 |
| 0-3 | 894.490 | 985.924 | 894.490 | 985.898 | 894.490 | 985.872 |
| 0-4 | 894.472 | 961.339 | 894.472 | 961.377 | 894.472 | 961.414 |
| 0-5 | 894.445 | 896.620 | 894.445 | 896.609 | 894.445 | 896.597 |
| 0-6 | 894.364 | 985.855 | 894.364 | 985.829 | 894.364 | 985.803 |
| 1-2 | 894.436 | 985.906 | 894.436 | 985.880 | 894.436 | 985.853 |
| 2-3 | 894.380 | 961.265 | 894.380 | 961.303 | 894.380 | 961.340 |
| 3-4 | 894.395 | 896.568 | 894.395 | 896.557 | 894.395 | 896.545 |
| 4-5 | 894.418 | 985.873 | 894.418 | 985.847 | 894.418 | 985.821 |
| 5-6 | 894.474 | 961.367 | 894.474 | 961.404 | 894.474 | 961.442 |
| 6-1 | 894.457 | 896.630 | 894.457 | 896.619 | 894.457 | 896.607 |

## 6. Chief phase correlation (upsampled DFT, 1/1000 px, luminance x alpha)

| frames | vertical (rows, px) | across (cols, px) | predicted from poses rows / cols (px) | |shift| (px) |
|---|---|---|---|---|
| 0/1 | +1.0060 | -0.0000 | +1.0102 / -0.0000 | 1.0060 |
| 499/500 | +1.0060 | -0.0000 | +1.0102 / -0.0000 | 1.0060 |
| 998/999 | +1.0060 | -0.0000 | +1.0103 / -0.0000 | 1.0060 |

Expected vertical about 1.01 px.

## 7. Same ground area across satellites (every 100th frame)

Footprint overlap = area(deputy footprint ∩ chief footprint) / area(chief footprint), from cameras.json for all 7 satellites. Image checks (rendered satellites only): alpha IoU with the chief and phase-correlation shift deputy vs chief (1/100 px).

| frame | sat 01 | sat 02 | sat 03 | sat 04 | sat 05 | sat 06 |
|---|---|---|---|---|---|---|
| 0 | 99.8593% | 100.0000% | 100.0000% | 100.0000% | 99.9760% | 99.8349% |
| 100 | 99.8593% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8349% |
| 200 | 99.8593% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8349% |
| 300 | 99.8593% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8350% |
| 400 | 99.8593% | 100.0000% | 100.0000% | 100.0000% | 99.9761% | 99.8350% |
| 500 | 99.8593% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8350% |
| 600 | 99.8592% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8350% |
| 700 | 99.8592% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8350% |
| 800 | 99.8592% | 100.0000% | 100.0000% | 100.0000% | 99.9762% | 99.8350% |
| 900 | 99.8592% | 100.0000% | 100.0000% | 100.0000% | 99.9763% | 99.8350% |

Footprint overlap min / mean: 99.8349% / 99.9451%; ground-centre offset from chief max 0.001 m.

| frame | sat 01 IoU / shift r,c (px) | sat 02 IoU / shift r,c (px) | sat 03 IoU / shift r,c (px) | sat 04 IoU / shift r,c (px) |
|---|---|---|---|---|
| 0 | 0.9977 / -0.50, -0.20 | 0.9996 / +0.06, +0.03 | 0.9973 / +0.62, +0.25 | 0.9977 / +0.50, +0.20 |
| 100 | 0.9978 / -0.41, -0.20 | 0.9996 / +0.05, +0.03 | 0.9974 / +0.51, +0.25 | 0.9978 / +0.41, +0.20 |
| 200 | 0.9979 / -0.32, -0.20 | 0.9996 / +0.04, +0.03 | 0.9975 / +0.40, +0.25 | 0.9979 / +0.32, +0.20 |
| 300 | 0.9979 / -0.24, -0.20 | 0.9996 / +0.03, +0.03 | 0.9975 / +0.30, +0.25 | 0.9979 / +0.24, +0.20 |
| 400 | 0.9979 / -0.17, -0.20 | 0.9996 / +0.02, +0.03 | 0.9975 / +0.21, +0.25 | 0.9979 / +0.18, +0.20 |
| 500 | 0.9979 / -0.11, -0.20 | 0.9996 / +0.01, +0.03 | 0.9976 / +0.14, +0.25 | 0.9979 / +0.11, +0.20 |
| 600 | 0.9979 / -0.06, -0.20 | 0.9996 / -0.00, +0.03 | 0.9976 / +0.07, +0.25 | 0.9979 / +0.06, +0.20 |
| 700 | 0.9980 / -0.01, -0.20 | 0.9996 / -0.00, +0.03 | 0.9976 / -0.00, +0.25 | 0.9979 / +0.01, +0.20 |
| 800 | 0.9979 / +0.05, -0.19 | 0.9997 / -0.01, +0.03 | 0.9976 / -0.06, +0.24 | 0.9979 / -0.05, +0.20 |
| 900 | 0.9979 / +0.10, -0.19 | 0.9997 / -0.02, +0.03 | 0.9976 / -0.13, +0.24 | 0.9979 / -0.10, +0.19 |

## 8. Logs, disk, render time

- Log lines flagged (error / warning / traceback / fail / killed / OOM): 0; benign DeprecationWarning lines: 10.
- Disk used by the run folder: 40.79 GB (37.99 GiB); free on the volume: 11.4 GB.
- Wall clock first start -> last finish: 1.950 h (7020 s).

| satellite | start (UTC) | done (UTC) | wall (min) | frames in report | sum render_seconds (min) | render s/frame min / mean / max |
|---|---|---|---|---|---|---|
| 00 | 15:51:19 | 17:00:03 | 68.7 | 1000 | 67.8 | 3.14 / 4.07 / 5.85 |
| 01 | 17:00:03 | 17:48:19 | 48.3 | 1000 | 47.4 | 2.70 / 2.84 / 4.18 |
| 02 | 15:51:19 | 17:00:07 | 68.8 | 1000 | 67.8 | 2.95 / 4.07 / 5.54 |
| 03 | 15:51:19 | 16:59:29 | 68.2 | 1000 | 67.2 | 3.15 / 4.03 / 5.98 |
| 04 | 15:51:19 | 16:59:20 | 68.0 | 1000 | 67.0 | 3.21 / 4.02 / 5.79 |

Total render time (sum of per-frame render_seconds, incl. PNG write): 5.287 GPU-process hours; scene setup per run: 31.6, 32.1, 31.8, 32.1, 30.8 s.

