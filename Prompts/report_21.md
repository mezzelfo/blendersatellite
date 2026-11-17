# Report 21: acquisition modes (sync / async) in orbits

Date: 2026-11-17. Runs exported with `melbourne_overpass.py --device cpu` and an empty tile grid (no terrain), checked with `orbits/scripts/validate_acquisition.py`. Reference: `git archive HEAD` of orbits (commit 9951b57) run with `--formation roe`. The check of aimed_basis against swarm_simulator.pointing.camera_rotation (2000 random cases, same p and d = -ground_across) gives a max difference of 3.1e-15. pytest: 92 passed, 7 skipped (CUDA and slow).


## E. Regression against the previous code

Max numeric difference over common keys: 0.000e+00. Keys added: acquisition, acquisition_role, t_s.

## Run hex7_sync_legacy (sync, line rule chief, frame period 0.6661251 ms)

Role window centres from capture [s]: {}

A. Aimed records: 0, max |stored - recomputed basis| = 0.00e+00; chief vs ground_motion rule: 1.40e-15

| sat | role | pointing | t centre [s] | lead [s] | scene row | scene col | incidence [deg] | off-nadir [deg] | range [km] | along GSD [m] | rows/frame | col drift [px] | col span [px] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | nadir | geocentric-nadir | +0.0000 |  | 1535.500 | 2047.500 | 0.1865 | 0.0000 | 500.868 | 4.750 | 1.00000 | +0.000 | 0.007 |
| 1 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.1641 | 0.0949 | 500.517 | 4.746 | 1.00071 | +0.002 | 0.008 |
| 2 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.0843 | 0.0948 | 500.931 | 4.750 | 0.99988 | -0.002 | 0.008 |
| 3 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.1595 | 0.0948 | 501.284 | 4.754 | 0.99917 | -0.005 | 0.010 |
| 4 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2521 | 0.0948 | 501.221 | 4.753 | 0.99929 | -0.005 | 0.010 |
| 5 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2888 | 0.0949 | 500.807 | 4.749 | 1.00012 | +0.003 | 0.009 |
| 6 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2552 | 0.0949 | 500.454 | 4.746 | 1.00082 | +0.007 | 0.011 |

F. render_swarm.py checks emulated (worst over all frames): target_recompute_m 0.00e+00, basis_recompute 0.00e+00, target_px 9.44e-11, pixel_step_dev 2.81e-06

D. Convergence of the mean lines of sight at the scene point: 

## Run hex7_sync (sync, line rule ground_motion, frame period 0.6716580 ms)

Role window centres from capture [s]: {}

A. Aimed records: 6000, max |stored - recomputed basis| = 0.00e+00; chief vs ground_motion rule: 1.47e-15

| sat | role | pointing | t centre [s] | lead [s] | scene row | scene col | incidence [deg] | off-nadir [deg] | range [km] | along GSD [m] | rows/frame | col drift [px] | col span [px] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | nadir | geocentric-nadir | +0.0000 |  | 1535.500 | 2047.500 | 0.1865 | 0.0000 | 500.868 | 4.750 | 1.00831 | +0.000 | 0.007 |
| 1 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.1641 | 0.0949 | 500.517 | 4.746 | 1.00902 | +0.000 | 0.007 |
| 2 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.0843 | 0.0948 | 500.931 | 4.750 | 1.00818 | +0.000 | 0.007 |
| 3 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.1595 | 0.0948 | 501.284 | 4.754 | 1.00747 | +0.000 | 0.008 |
| 4 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2521 | 0.0948 | 501.221 | 4.753 | 1.00759 | +0.000 | 0.008 |
| 5 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2888 | 0.0949 | 500.807 | 4.749 | 1.00842 | +0.000 | 0.007 |
| 6 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2552 | 0.0949 | 500.454 | 4.746 | 1.00914 | +0.000 | 0.007 |

F. render_swarm.py checks emulated (worst over all frames): target_recompute_m 0.00e+00, basis_recompute 0.00e+00, target_px 1.01e-10, pixel_step_dev 2.81e-06

D. Convergence of the mean lines of sight at the scene point: 

## Run hex7_async_10deg (async, line rule ground_motion, frame period 0.6716580 ms)

Role window centres from capture [s]: {'nadir': 0.0, 'aft': 11.257872985346507, 'fore': -11.68358032089307}

A. Aimed records: 6000, max |stored - recomputed basis| = 0.00e+00; chief vs ground_motion rule: 1.42e-15

| sat | role | pointing | t centre [s] | lead [s] | scene row | scene col | incidence [deg] | off-nadir [deg] | range [km] | along GSD [m] | rows/frame | col drift [px] | col span [px] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | nadir | geocentric-nadir | +0.0000 |  | 1535.500 | 2047.500 | 0.1865 | 0.0000 | 500.868 | 4.750 | 1.00831 | +0.000 | 0.007 |
| 1 | fore | ground-track-lead | -11.6836 | +11.6836 | 1535.500 | 2047.500 | 10.0729 | 9.5006 | 508.040 | 4.893 | 0.97808 | -0.000 | 0.007 |
| 2 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.0843 | 0.0948 | 500.931 | 4.750 | 1.00818 | +0.000 | 0.007 |
| 3 | aft | ground-track-lead | +11.2579 | -11.2579 | 1535.500 | 2047.500 | 9.9593 | 9.0578 | 508.119 | 4.892 | 0.97937 | +0.000 | 0.008 |
| 4 | fore | ground-track-lead | -11.6836 | +11.6836 | 1535.500 | 2047.500 | 9.9272 | 9.3654 | 508.533 | 4.896 | 0.97757 | -0.000 | 0.007 |
| 5 | nadir | chief-target | +0.0000 |  | 1535.500 | 2047.500 | 0.2888 | 0.0949 | 500.807 | 4.749 | 1.00842 | +0.000 | 0.007 |
| 6 | aft | ground-track-lead | +11.2579 | -11.2579 | 1535.500 | 2047.500 | 10.0423 | 9.1350 | 507.400 | 4.886 | 0.98051 | +0.000 | 0.007 |

F. render_swarm.py checks emulated (worst over all frames): target_recompute_m 0.00e+00, basis_recompute 0.00e+00, target_px 9.91e-11, pixel_step_dev 2.68e-06

D. Convergence of the mean lines of sight at the scene point: nadir-fore 10.182 deg (B/H 0.178); nadir-aft 9.818 deg (B/H 0.172); fore-aft 20.000 deg (B/H 0.353)


## Cube on the ground from the raw rows (scripts/validate_raw_cubes.py)

Synthetic smooth texture on z = 0, real poses of the async run (frame period 0.6716580 ms),
8 bands of 2 rows spread over rows 1000..1957, columns 1900..2199, raw rows written in the
render_to_raw.py layout, cubes built by build_cubes.py --source raw --layout raw_bands.
Error against the texture at the ground point of frame-0 pixel (g, c), uint8 DN:

| sat | role | mode | lines | max error [DN] | rms [DN] | max band-to-band [DN] | samples with a missing band |
|---|---|---|---|---|---|---|---|
| 0 | nadir | bilinear | 52 | 2.24 | 0.58 | 2 | 0.33% |
| 0 | nadir | linear | 52 | 2.20 | 0.58 | 2 | 0.00% |
| 0 | nadir | none | 44 | 178.37 | 60.41 | 177 | 0.00% |
| 2 | nadir | bilinear | 52 | 2.19 | 0.59 | 2 | 0.33% |
| 2 | nadir | linear | 52 | 2.25 | 0.59 | 2 | 0.00% |
| 1 | fore | bilinear | 20 | 3.22 | 0.67 | 3 | 0.67% |
| 1 | fore | linear | 21 | 10.53 | 1.76 | 10 | 0.00% |
| 3 | aft | bilinear | 22 | 3.06 | 0.71 | 3 | 0.00% |
| 3 | aft | linear | 23 | 9.75 | 1.68 | 10 | 0.00% |

Mode none assumes one row per frame and fails with the nominal period (8 rows of drift).
Mode linear keeps the column fixed: for fore/aft a ground point moves by up to 3.2 columns at
the image edges between rows 1000 and 1956 (0.24 at column 1900), hence mode bilinear.
