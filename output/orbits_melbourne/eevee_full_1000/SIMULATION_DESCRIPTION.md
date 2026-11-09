# Simulation description: 7-satellite swarm burst over Melbourne (run eevee_full_1000)

Generated 2026-11-09T22:20:35Z by `scripts/describe_simulation.py` (numbers measured by that script, by `scripts/check_stacking_geometry.py`, or read from `validation_summary.json` / `stacking_inputs.npz`; code references are `file:line` in this repository, `orbits/` being the submodule; line numbers refer to repository HEAD da02b7c and submodule 9951b57, plus the uncommitted step 8/9 scripts). Read-only on every existing output.

- Run folder: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/eevee_full_1000`
- Simulation: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/20261109T180310Z_run_v2/simulation` (cameras.json, results.npz, config.yaml)
- Geometry module: `scripts/stacking_geometry.py`; stacking inputs: `stacking_inputs.npz` in the run folder.

## 1. Time and orbit

| quantity | value | source |
|---|---|---|
| epoch of sample 0 (t = 0) | 2026-10-27T23:59:59.667271+00:00 | cameras.json epochs[0].timestamp; melbourne_overpass.py:198-203 |
| capture instant t_c | 2026-10-28T00:00:00+00:00, t_c = 0.332729491019 s = 499.500000 dt | cameras.json capture_time_s; melbourne_overpass.py:116 |
| last sample | 2026-10-28T00:00:00.332730+00:00, t = 0.665458982037 s | cameras.json epochs[999] |
| time grid | t_k = k dt, k = 0..999; max abs(diff(t) - dt) = 1.01e-16 s | results.npz times; sensor_timing.py:38-41 |
| dt | 0.6661251071 ms (frame_period_mode ground; nominal sensor_timing.frame_period(500 km) = 0.6716580153 ms, not used) | cameras.json frame_period_s; melbourne_overpass.py:106-122 |
| dt definition (ground, used) | dt = 1 / rate_rows = 1 / 1501.219500 rows/s: the common target at t_c (chief boresight x WGS84, fixed to the Earth) projected into the chief camera at t_c -/+ 0.01 s, rate_rows = (row(+) - row(-)) / (2 delta), so the ground advances exactly one chief row per frame (rate_cols = 1.95e-08 cols/s). Cross-check dt_closed_form = GSD_true / v_t = rho p / f / v_t = 0.6661217456 ms (rho = 500868.223 m, GSD_true = 4.749612457 m, v_t = 7130.2468 m/s; relative difference 5.0e-06) | orbits/src/orbits/burst_geometry.py:118-140 (pixel_rate_frame_period), 143-161 (closed_form_frame_period); melbourne_overpass.py:106-112, 118-122; cameras.json |
| dt definition (nominal, not used) | GSD / v_g = 4.741379310345 m / 7059.2164500564 m/s (nominal h = 500 km, Re = 6378137 m) = 0.6716580153 ms | sensor_timing.py:20-35; melbourne_overpass.py:93 |
| window | 665.458982 ms (1000 samples) | results.npz |
| altitude reference | mean: R_avg = (2a + b)/3 (WGS84 a = 6378137.0, b = 6356752.314245) = 6371008.7714 m | orbits/src/orbits/physics.py:8-11; melbourne_overpass.py:44-45, 79 |
| a_chief | R_avg + 500 km = 6871008.7714 m, on the geocentric radial of the Melbourne point | cameras.json a_chief_m; melbourne_overpass.py:79-80, 89 |
| height above the target | 500868.223 m (a_chief - abs(Melbourne point), geocentric) | cameras.json height_above_target_m; melbourne_overpass.py:103-105 |
| SSO inclination | sso_inclination(a_chief) = 97.374489 deg (J2 with the equatorial Re = 6378137 m) | elements.py:8-17; melbourne_overpass.py:90 |

Chief osculating elements (orbits.elements.eci_to_elements, elements.py:32-59; u = argp + nu). The state at t_c is integrated from sample 499 by dt/2 (RK4, 8 steps, same J2 model) and agrees with the chief_capture state rebuilt from melbourne_overpass.py:77-102 within 2.08e-06 mm (from sample 500 backwards: 1.86e-06 mm).

| element | t = 0 | t = t_c |
|---|---|---|
| a m | 6871002.0025 | 6871008.7714 |
| e | 9.870e-07 | 3.512e-16 |
| i deg | 97.374492202 | 97.374488549 |
| raan deg | 175.594050931 | 175.594053810 |
| argp deg | 138.430570 | 0.000000 |
| nu deg | 183.550676 | 322.002379 |
| u deg | 321.981245792 | 322.002378714 |

- At t_c the chief state is built exactly circular in the two-body sense (r perpendicular to v, |v| = sqrt(mu/r); melbourne_overpass.py:97-102), hence a = r and e = 0 (argp = 0 by the circular convention, elements.py:55). The elements are two-body osculating elements of a J2 trajectory, so they carry the short-period J2 variation: between t = 0 and t_c a changes by +6.769 m and e by -9.87e-07. With e ~ 1e-6, argp and nu are individually ill-defined; u = argp + nu is the meaningful angle.
- SSO inclination actually used: sso_inclination(a_chief) = 97.374488549 deg for a_chief = 6871008.771415 m (melbourne_overpass.py:88-90, elements.py:8-17; pipeline.log prints the same value; cameras.json formation_geometry.inclination_deg = 97.374488549). The osculating i at t = 0 is 97.374492202 deg (back-propagated state, config.yaml chief.inclination_deg).
- RAAN drift of the chief over 1 day of J2 propagation from the t = 0 state (RK4 10 s, linear fit of the osculating RAAN): 0.98608 deg/day (target 0.9856 deg/day).
- Satellite geodetic position at t_c (WGS84, pyproj EPSG:4978 -> 4979): lat -37.800957 deg, lon 144.965039 deg, h 500865.771 m; geocentric radius 6871008.771 m. The chief is placed at a_chief along the GEOCENTRIC radial of the Melbourne point (melbourne_overpass.py:79-80): its geocentric nadir hits WGS84 at lat -37.814555, lon 144.965039, 0.000 m from the Melbourne origin (lat/lon -37.814555, 144.965039); the geodetic sub-satellite point is the satellite lat/lon above.
- Ground-track azimuth at t_c (ECEF-relative velocity, ENU at the sub-satellite point): 347.7547 deg from north (-12.2453 deg, i.e. towards north-north-west); horizontal ECEF-relative speed 7690.808 m/s, vertical -22.647 m/s.
- Pass: ascending (ECI v_z = +5952.476 m/s at t_c).
- Local mean solar time at the pass: 09:39:52 (UTC 00:00:00 + lon/15 h).
- Ground speed of the chief nadir point (= common target) on WGS84: 7130.2468 m/s (min 7130.2296, max 7130.2640), versus v_t = 7130.2468 m/s at t_c used for the closed-form cross-check of dt (ratio 1.000000); the nominal v_g = 7059.2165 m/s is not used.
- Propagator: point mass + J2 (orbits/src/orbits/physics.py:14-41; mu = 3.986004418e14, Re = 6378137 m, J2 = 1.08262668e-3), fixed-pole ECI, torchdiffeq odeint dopri5 with rtol 1.0e-10, atol 1.0e-09 (simulation config.yaml:1032-1036; cli.py:39-40, 62; propagation.py:21), float64 on cuda:0, 1000 output samples (dense output of the adaptive steps). The back-propagation of the chief from t_c to t = 0 uses dopri5 with rtol 1e-11, atol 1e-10 (melbourne_overpass.py:129-130). Deputies are created at t = 0 with roe_to_eci (melbourne_overpass.py:174-176, roe.py:86-89).
- Dense-output error (recomputed in this step): all 7 satellites, every sample versus an independent fixed-step RK4 (step dt/4, numpy, same J2 model) started from sample 0: max position error 4.393e-08 m.

## 2. Formation

ROE (quasi-nonsingular, osculating, applied at t = 0 without mean/osculating conversion; roe.py:1-14). The metre values are the specification (roe_hex7_dmax1km.json, generator roe.py:101-124). The dimensionless ROE used by the run are roe_m / a_chief = roe_m / 6871008.771415 (melbourne_overpass.py:174-176); the JSON "roe" field uses a_ref = 6870140.5 m (relative difference 1.3e-04, not used). "Realised" = elements_to_roe of the propagated states at t = 0 times a_chief. da = dlam = 0 for every deputy.

| sat | (W, X) | a dex (m) | a dey (m) | a dix (m) | a diy (m) | dex | dey | dix | diy | realised max abs diff (m) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | (1, 0) | 447.2136 | 0.0000 | 0.0000 | 894.4272 | 6.508704e-05 | 0.000000e+00 | 0.000000e+00 | 1.301741e-04 | 1.5e-08 |
| 2 | (0, 1) | 223.6068 | 387.2983 | -774.5967 | 447.2136 | 3.254352e-05 | 5.636703e-05 | -1.127341e-04 | 6.508704e-05 | 9.2e-09 |
| 3 | (-1, 1) | -223.6068 | 387.2983 | -774.5967 | -447.2136 | -3.254352e-05 | 5.636703e-05 | -1.127341e-04 | -6.508704e-05 | 6.1e-09 |
| 4 | (-1, 0) | -447.2136 | 0.0000 | 0.0000 | -894.4272 | -6.508704e-05 | 0.000000e+00 | 0.000000e+00 | -1.301741e-04 | 9.2e-09 |
| 5 | (0, -1) | -223.6068 | -387.2983 | 774.5967 | -447.2136 | -3.254352e-05 | -5.636703e-05 | 1.127341e-04 | -6.508704e-05 | 6.1e-09 |
| 6 | (1, -1) | 223.6068 | -387.2983 | 774.5967 | 447.2136 | 3.254352e-05 | -5.636703e-05 | 1.127341e-04 | 6.508704e-05 | 3.7e-09 |

Max deviation of the metre values from the PLAN_CONTEXT table: 3.5e-05 m.

Chief RTN frame (frames.py:6-39, eci_to_lvlh with the chief J2 acceleration): position (r, t, n) in metres and rotating-frame relative velocity in m/s.

| sat | (r, t, n) m, frame 0 | frame 499 | frame 999 | (vr, vt, vn) m/s, frame 0 | frame 999 |
|---|---|---|---|---|---|
| 0 | (+0.000, +0.000, +0.000) | (+0.000, +0.000, +0.000) | (+0.000, +0.000, +0.000) | (+0.0000, +0.0000, +0.0000) | (+0.0000, +0.0000, +0.0000) |
| 1 | (-352.370, -550.918, -704.562) | (-352.472, -550.658, -704.765) | (-352.574, -550.398, -704.969) | (-0.3054, +0.7813, -0.6109) | (-0.3051, +0.7817, -0.6103) |
| 2 | (+62.352, -885.647, +124.879) | (+62.188, -885.693, +124.553) | (+62.025, -885.739, +124.226) | (-0.4909, -0.1384, -0.9820) | (-0.4909, -0.1377, -0.9821) |
| 3 | (+414.651, -334.779, +829.478) | (+414.589, -335.085, +829.355) | (+414.527, -335.391, +829.231) | (-0.1855, -0.9195, -0.3711) | (-0.1859, -0.9193, -0.3718) |
| 4 | (+352.267, +550.871, +704.714) | (+352.368, +550.611, +704.917) | (+352.470, +550.351, +705.120) | (+0.3053, -0.7812, +0.6107) | (+0.3050, -0.7817, +0.6102) |
| 5 | (-62.418, +885.715, -124.661) | (-62.255, +885.761, -124.335) | (-62.092, +885.807, -124.008) | (+0.4909, +0.1383, +0.9820) | (+0.4909, +0.1375, +0.9821) |
| 6 | (-414.756, +334.793, -829.338) | (-414.695, +335.099, -829.214) | (-414.633, +335.405, -829.090) | (+0.1856, +0.9197, +0.3712) | (+0.1859, +0.9194, +0.3719) |

- T-N lattice edges (12 edges, every frame): 894.364 .. 894.490 m, max deviation from 894.427 m = 0.063 m.
- Pairwise 3D distance over the whole window (21 pairs x 1000 frames): min 896.546 m (pair [3, 4], frame 999), max 1971.780 m (pair [3, 6], frame 0); lattice neighbours max 985.924 m; largest change of any pair distance across the window 0.1489 m.

Slant distance `distance_m` (camera centre to the z = 0 point of its boresight; camera_geometry.py:97-99), all 7 satellites (5 and 6 from cameras.json, not rendered):

| sat | frame 0 (m) | frame 499 (m) | frame 999 (m) | min (m) | max (m) | minus chief f0 / f499 / f999 (m) | source |
|---|---|---|---|---|---|---|---|
| 0 | 500875.317 | 500868.230 | 500860.245 | 500860.245 | 500875.317 | +0.000 / +0.000 / +0.000 | poses.jsonl |
| 1 | 500523.746 | 500516.557 | 500508.470 | 500508.470 | 500523.746 | -351.571 / -351.673 / -351.774 | poses.jsonl |
| 2 | 500938.468 | 500931.217 | 500923.068 | 500923.068 | 500938.468 | +63.150 / +62.987 / +62.823 | poses.jsonl |
| 3 | 501290.766 | 501283.618 | 501275.570 | 501275.570 | 501290.766 | +415.449 / +415.387 / +415.325 | poses.jsonl |
| 4 | 501228.382 | 501221.397 | 501213.513 | 501213.513 | 501228.382 | +353.065 / +353.166 / +353.268 | poses.jsonl |
| 5 | 500813.698 | 500806.774 | 500798.952 | 500798.952 | 500813.698 | -61.620 / -61.456 / -61.293 | cameras.json |
| 6 | 500461.360 | 500454.335 | 500446.411 | 500446.411 | 500461.360 | -413.957 / -413.895 / -413.834 | cameras.json |

## 3. Pointing law and frames

Frames and conventions:
- ECI: J2000-aligned, fixed Earth pole (results.json "frame"; physics.py:1). ECEF = R_z(theta)^T ECI with theta(t) = GMST(capture) + GMST_RATE (t - t_c), GMST from the linear IAU formula with UTC ~ UT1 (melbourne_overpass.py:34-38, 45-46, 214-218). No polar motion, precession or nutation.
- Local frame: ENU at the Melbourne origin (EPSG:7855 320877.4907, 5812809.4720 -> GDA2020 lon/lat -> WGS84 ellipsoid, h = 0), x = East, y = North, z = Up; local = local_from_ecef (ECEF - origin) (melbourne_overpass.py:107-115, 62-63). z = 0 is the tangent plane at the origin.
- camera_to_local: 3x3 with columns (image-right, image-up, backward); boresight = -column 2 (camera_geometry.py:3, 72; melbourne_overpass.py:63). Blender places the camera with matrix_world = [camera_to_local | position_local_m] (render_swarm.py:248-250); Blender cameras look along their local -Z with +Y up, so this matrix is used as is.

Chief (satellite 0), per frame:
1. ECEF position r and ECEF-relative velocity v_rel = R v_ECI - omega x r (melbourne_overpass.py:56-57).
2. Boresight = geocentric nadir, forward = -r/|r| (melbourne_overpass.py:58).
3. Image-up = component of v_rel perpendicular to forward; right = forward x up; up re-orthogonalised (melbourne_overpass.py:59-61); basis rotated to local (melbourne_overpass.py:62-63).
4. Common target = chief boresight intersected with WGS84 at h = 0 (camera_geometry.py:13-42, melbourne_overpass.py:71); recomputed every frame (melbourne_overpass.py:217-224) and re-checked by the renderer (render_swarm.py:229-241).

Deputies (1..6), per frame (camera_geometry.py:75-96):
1. across = unit ground direction of the chief image-right on z = 0, i.e. the derivative of the ray/plane intersection (camera_geometry.py:53-61, 90).
2. aimed_basis: forward = unit(target - position); right = across made perpendicular to forward; up = right x forward (camera_geometry.py:64-72, 96). This is the roll that makes the deputy ground across-track direction parallel to the chief's (alignment = dot of the unit ground directions, camera_geometry.py:114-115).
3. Measured: min alignment over all deputies and frames = 1 - 3.3e-16 (cameras.json ground_across_alignment_with_chief).

Chief image axes in ENU (unit vectors, columns of camera_to_local) and azimuth of image-up from north (atan2(E, N) of the up column):

| frame | image-right (E, N, U) | image-up (E, N, U) | boresight (E, N, U) | azimuth of image-up (deg) |
|---|---|---|---|---|
| 0 | (+0.977255, +0.212067, -0.000689) | (-0.212068, +0.977251, -0.002804) | (-0.000079, -0.002887, -0.999996) | 347.7564 (-12.2436) |
| 499 | (+0.977249, +0.212095, -0.000689) | (-0.212097, +0.977244, -0.003176) | (-0.000000, -0.003250, -0.999995) | 347.7547 (-12.2453) |
| 999 | (+0.977242, +0.212124, -0.000690) | (-0.212125, +0.977236, -0.003549) | (+0.000079, -0.003615, -0.999993) | 347.7530 (-12.2470) |

Chief boresight versus the WGS84 ellipsoid normal at the common target (geocentric vs geodetic nadir): 0.1863 deg, 0.1862 deg, 0.1862 deg at frames 0, 499, 999.

Off-nadir angle (boresight vs the satellite's own geocentric nadir, recomputed; matches cameras.json off_nadir_deg) and angle between each boresight and the chief boresight, min / max over the 1000 frames:

| sat | off-nadir min (deg) | off-nadir max (deg) | max diff vs off_nadir_deg | vs chief min (deg) | vs chief max (deg) |
|---|---|---|---|---|---|
| 0 | 0.000000 | 0.000002 | 0.0e+00 | 0.000000 | 0.000002 |
| 1 | 0.094923 | 0.094926 | 0.0e+00 | 0.102381 | 0.102384 |
| 2 | 0.094841 | 0.094844 | 0.0e+00 | 0.102300 | 0.102303 |
| 3 | 0.094778 | 0.094782 | 0.0e+00 | 0.102237 | 0.102240 |
| 4 | 0.094789 | 0.094792 | 0.0e+00 | 0.102248 | 0.102251 |
| 5 | 0.094871 | 0.094874 | 0.0e+00 | 0.102329 | 0.102332 |
| 6 | 0.094934 | 0.094937 | 0.0e+00 | 0.102392 | 0.102395 |

## 4. Image conventions

Mapping from a PNG pixel to a ray (implemented in `scripts/stacking_geometry.py`):

1. PNG index (r, c): row r from the TOP (0..3071), column c from the LEFT (0..4095). The integer index is the pixel CENTRE; pixel (r, c) covers [r - 1/2, r + 1/2) x [c - 1/2, c + 1/2).
2. Blender view coordinates (world_to_camera_view, origin at the BOTTOM-LEFT, [0, 1]^2): u = (c + 1/2) / 4096, v = 1 - (r + 1/2) / 3072. The renderer writes the image top row first, so row 0 is v ~ 1.
3. Camera ray: sensor_fit HORIZONTAL, f = 580 mm, Sw = 22.528 mm, Sh = Sw * 3072/4096 = 16.896 mm (render_swarm.py:190-192, 251-253, 259): d_cam = ((u - 1/2) Sw/f, (v - 1/2) Sh/f, -1).
4. ENU: d_local = camera_to_local d_cam (normalised), origin position_local_m. Inverse: p = camera_to_local^T (X - C), u = 1/2 + (f/Sw) p_x/(-p_z), v = 1/2 + (f/Sh) p_y/(-p_z).
5. Optical axis / image centre: (r, c) = (1535.5, 2047.5), between the 4 central pixels. The footprint corners in cameras.json are the outer pixel EDGES, (r, c) = (-1/2, -1/2) etc. (camera_geometry.py:105-109; checked by render_swarm.py:262-265).

Direction of motion: image-up (decreasing row) is the chief's ECEF-relative velocity, so ground features move towards INCREASING PNG row as the frame index grows. Advance of the ground in the chief's own pixels (stacking_inputs.npz image_motion_rows_px[0]): +0.99998 .. +1.00002 rows per frame (sign +, i.e. downward), columns -3.0e-08 .. -2.9e-08. Checked on the PNGs without the poses: the integer row shift that best maps chief frame 0 onto frame 50 is +50 rows (masked SSD 0.4 vs 2500.8 for the opposite sign; poses predict +49.999).

Real-image check of the convention (`check_stacking_geometry.py`): 200000 random vertices of the 570 terrain caches (of 6960533; seed 0), projected with pixel_from_ground, PNG read at the nearest pixel where alpha > 0, |dRGB| = mean over R, G, B of |PNG - vertex| (8-bit DN). n = samples with alpha > 0.

| sat / frame | variant | n | median | p95 | within 25 DN |
|---|---|---|---|---|---|
| 0/0 | correct | 200000 | 15.00 | 71.33 | 66.62% |
| 0/0 | row_flipped | 22026 | 36.67 | 112.00 | 34.81% |
| 0/0 | col_flipped | 79331 | 41.33 | 119.67 | 30.50% |
| 0/0 | half_px_plus | 200000 | 17.33 | 82.00 | 61.91% |
| 0/0 | half_px_minus | 200000 | 17.67 | 83.00 | 61.71% |
| 0/499 | correct | 200000 | 15.00 | 71.33 | 66.60% |
| 0/499 | row_flipped | 104594 | 42.67 | 120.00 | 30.02% |
| 0/499 | col_flipped | 79334 | 41.33 | 119.67 | 30.45% |
| 0/499 | half_px_plus | 200000 | 17.33 | 82.00 | 61.91% |
| 0/499 | half_px_minus | 200000 | 17.67 | 83.33 | 61.70% |
| 0/999 | correct | 200000 | 15.00 | 71.33 | 66.61% |
| 0/999 | row_flipped | 98175 | 42.33 | 118.00 | 29.31% |
| 0/999 | col_flipped | 79335 | 41.33 | 120.00 | 30.51% |
| 0/999 | half_px_plus | 200000 | 17.33 | 82.00 | 61.89% |
| 0/999 | half_px_minus | 200000 | 17.67 | 83.33 | 61.70% |
| 3/0 | correct | 200000 | 15.00 | 71.00 | 66.68% |
| 3/0 | row_flipped | 22026 | 37.00 | 112.33 | 34.98% |
| 3/0 | col_flipped | 79323 | 41.33 | 120.00 | 30.51% |
| 3/0 | half_px_plus | 200000 | 17.33 | 82.00 | 62.02% |
| 3/0 | half_px_minus | 200000 | 17.67 | 83.33 | 61.52% |
| 3/499 | correct | 200000 | 15.00 | 71.33 | 66.62% |
| 3/499 | row_flipped | 104603 | 42.67 | 120.00 | 30.00% |
| 3/499 | col_flipped | 79333 | 41.33 | 120.00 | 30.52% |
| 3/499 | half_px_plus | 200000 | 17.33 | 82.00 | 61.84% |
| 3/499 | half_px_minus | 200000 | 17.67 | 83.33 | 61.62% |
| 3/999 | correct | 200000 | 15.00 | 71.33 | 66.66% |
| 3/999 | row_flipped | 98172 | 42.33 | 118.00 | 29.26% |
| 3/999 | col_flipped | 79334 | 41.33 | 120.00 | 30.67% |
| 3/999 | half_px_plus | 200000 | 17.33 | 82.00 | 61.90% |
| 3/999 | half_px_minus | 200000 | 17.67 | 83.33 | 61.58% |

half_px_plus / minus shift both axes by +1/2 / -1/2 px (pixel-corner instead of pixel-centre convention, either sign). Bilinear sweep of a sub-pixel offset (dr, dc) in {-0.5, -0.25, 0, 0.25, 0.5}^2: the minimum mean |dRGB| is at +0.00,+0.00 for every satellite/frame.

Round trip pixel -> ground (z = 0, 30, 100 m) -> pixel on a 61 x 81 grid incl. the outer edges, frames 0/499/999: max error 1.20e-11 px over all 7 satellites (per satellite: 0: 1.2e-11, 1: 2.0e-12, 2: 1.9e-12, 3: 1.6e-12, 4: 2.1e-12, 5: 2.3e-12, 6: 2.0e-12).

Common target -> image centre, all 1000 frames (this module vs step 8, validation_summary.json target.reproj_max):

| sat | max px (this module) | frame | step 8 max px |
|---|---|---|---|
| 0 | 9.35e-11 | 820 | 9.33e-11 |
| 1 | 4.55e-13 | 966 | 0.00e+00 |
| 2 | 0.00e+00 | 0 | 0.00e+00 |
| 3 | 4.55e-13 | 969 | 2.27e-13 |
| 4 | 4.55e-13 | 12 | 2.27e-13 |
| 5 | 4.55e-13 | 19 | not rendered |
| 6 | 4.55e-13 | 645 | not rendered |

## 5. Render pipeline

- Blender 5.2.2 LTS, engine BLENDER_EEVEE, GPU NVIDIA TITAN Xp via VULKAN (renders/render_report.jsonl run header).
- Settings that differ from the factory defaults (scene from read_factory_settings(use_empty=True), render_swarm.py:132): engine BLENDER_EEVEE (136); taa_render_samples 8 (137, --samples 8); use_shadows, use_raytracing, use_fast_gi, use_volumetric_shadows, use_overscan = False (139-141); motion blur off (142); resolution 4096 x 3072 at 100% (151, 259); PNG RGBA (152-153), colour depth left at 8 bit; film_transparent True (154); view transform Standard, look None (155-156); camera PERSP, sensor_fit HORIZONTAL, DOF off (190-193); clip range distance_m -/+ 10 km per camera (257-258); lens 580 mm, sensor 22.528 x 16.896 mm (251-253). The pixel filter, colour space of the PNG (sRGB) and everything else are factory defaults. No lights, no world.
- Material: one emission shader fed by the "Color" point attribute, strength 1 (render_swarm.py:157-164). Vertex colours are the 8-bit sRGB texture samples of the cache converted to scene-linear (render_swarm.py:179-183); the Standard view transform converts back to sRGB on output. A pixel therefore holds the baked aerial texture colour (including the sun/shadows of the original aerial survey) interpolated over the 2.5 m triangles and averaged by the 8-sample anti-aliasing. No illumination model, no atmosphere, no spectral content beyond R, G, B.
- Terrain: 570 tiles (cameras.json selected_tiles; 570 .npz in data/swarm_terrain), each a regular 2.5 m grid of the photomesh UPPER surface (highest face per cell, empty cells filled from the nearest sampled cell; prepare_swarm_terrain.py:62-85, 117-121), so roofs are kept and facades/overhangs are lost. Vertices 6,960,533, triangles 13,670,356 (2 per grid cell, render_swarm.py:174-176; run header reports 13,670,356); mean sampled fraction of cells 96.1%; local z range -13.78 .. 322.63 m. Heights are AHD used as ellipsoidal height (cameras.json terrain_vertical), converted with the full ECEF -> ENU transform, so the z = 0 tangent plane is not the ellipsoid: the ellipsoid height of z = 0 at the 4 dataset bbox corners is 4.13, 1.74, 1.92, 4.32 m.
- Alpha: film_transparent, so alpha = geometry coverage of the pixel (0 outside the dataset, fractional on the anti-aliased boundary). Pixels with 0 < alpha < 255: 23985 .. 24511 per image. Alpha is straight (not premultiplied): median luminance of pixels with alpha 1-127 / 128-254 / 255 at chief frame 0 = 115.2 / 113.7 / 118.0 DN.
- Format: PNG, 8-bit RGBA, 4096 x 3072, sRGB (validation_summary.json png: 5000/5000 pass).

Scene extent in the image, computed from the PNGs (alpha > 0, read-only; stacking_inputs.npz alpha_row_any / alpha_col_any / alpha_bounds). Union = rows/columns with alpha > 0 in at least one frame; intersection = in every frame. All ranges are contiguous and inclusive (PNG indices).

| sat | union rows | union cols | intersection rows | intersection cols | frames touching row 0 (last) | bottom/left/right edge touched |
|---|---|---|---|---|---|---|
| 0 | 0..2956 (2957) | 661..2570 (1910) | 999..1957 (959) | 661..2570 (1910) | 1 (0) | none |
| 1 | 0..2957 (2958) | 660..2570 (1911) | 999..1957 (959) | 660..2570 (1911) | 2 (1) | none |
| 2 | 0..2956 (2957) | 661..2570 (1910) | 999..1957 (959) | 661..2570 (1910) | 1 (0) | none |
| 3 | 1..2955 (2955) | 662..2569 (1908) | 1000..1957 (958) | 662..2569 (1908) | 0 (-) | none |
| 4 | 1..2955 (2955) | 662..2569 (1908) | 1000..1957 (958) | 662..2569 (1908) | 0 (-) | none |

The imaged tiles touch the top image row (forward, north-north-west) only in the first 0-2 frames; no other image edge is ever reached. The bbox-corner margin is more pessimistic because the 570 tiles do not fill the 8.4 x 8.4 km bbox: chief margin of the 4 EPSG:7855 bbox corners (on the ellipsoid; metres = pixels x native GSD; negative = outside) frame 0: -701.8 m, frame 500: +1672.9 m, frame 999: +419.9 m.

Stacking geometry (stacking_inputs.npz; ground sizes at the image centre on z = 0; advance = image motion of the ground in the satellite's own pixels between consecutive frames):

| sat | native GSD f0 / f999 (m) | ground px across f0 / f999 (m) | ground px along f0 / f999 (m) | centre step (m) | advance rows (px/frame) | advance cols (px/frame) | ratio to chief |
|---|---|---|---|---|---|---|---|
| 0 | 4.749680 / 4.749537 | 4.749681 / 4.749538 | 4.749698 / 4.749567 | 4.749637 | 0.999983 .. 1.000018 | -3.0e-08 .. -2.9e-08 | 1.000000 .. 1.000000 |
| 1 | 4.746346 / 4.746201 | 4.746356 / 4.746211 | 4.746352 / 4.746214 | 4.749637 | 1.000689 .. 1.000724 | +1.9e-06 .. +1.9e-06 | 1.000705 .. 1.000706 |
| 2 | 4.750279 / 4.750133 | 4.750279 / 4.750133 | 4.750281 / 4.750140 | 4.749637 | 0.999861 .. 0.999897 | -1.7e-06 .. -1.7e-06 | 0.999878 .. 0.999879 |
| 3 | 4.753619 / 4.753475 | 4.753622 / 4.753478 | 4.753631 / 4.753496 | 4.749637 | 0.999156 .. 0.999192 | -4.8e-06 .. -4.8e-06 | 0.999173 .. 0.999173 |
| 4 | 4.753028 / 4.752887 | 4.753029 / 4.752888 | 4.753065 / 4.752940 | 4.749637 | 0.999275 .. 0.999309 | -5.1e-06 .. -5.1e-06 | 0.999291 .. 0.999291 |
| 5 | 4.749095 / 4.748956 | 4.749098 / 4.748958 | 4.749145 / 4.749022 | 4.749637 | 1.000099 .. 1.000134 | +2.8e-06 .. +2.9e-06 | 1.000115 .. 1.000116 |
| 6 | 4.745754 / 4.745613 | 4.745768 / 4.745626 | 4.745782 / 4.745653 | 4.749637 | 1.000809 .. 1.000843 | +6.7e-06 .. +6.7e-06 | 1.000825 .. 1.000825 |

Parallax (step 9 B3): chief pixel (r, c) -> ground point at height h -> pixel of the deputy at the same frame (chief_to_satellite_pixel). "Offset" = deputy pixel - chief pixel (rows, cols), largest norm over frames 0, 499, 999 (frame in brackets). "Parallax" = largest norm of offset(h) - offset(0) over the same frames, i.e. the part caused by the height alone; offset(0) is the scale/position difference on the z = 0 plane.

| sat | chief pixel | offset h=0 (rows, cols) | offset h=30 m | offset h=100 m | parallax 30 m (px) | parallax 100 m (px) |
|---|---|---|---|---|---|---|
| 1 | top-left (0, 0) | (-1.018, -1.350) [999] | (-1.026, -1.359) [999] | (-1.043, -1.379) [999] | 0.0114 | 0.0380 |
| 1 | top-right (0, 4095) | (-1.099, +1.450) [999] | (-1.106, +1.442) [999] | (-1.124, +1.422) [999] | 0.0113 | 0.0376 |
| 1 | bottom-left (3071, 0) | (+1.070, -1.413) [999] | (+1.063, -1.421) [999] | (+1.046, -1.442) [999] | 0.0113 | 0.0377 |
| 1 | bottom-right (3071, 4095) | (+1.151, +1.527) [999] | (+1.144, +1.519) [999] | (+1.126, +1.499) [999] | 0.0112 | 0.0373 |
| 1 | centre (1535.5, 2047.5) | (-0.000, -0.000) [999] | (-0.008, -0.009) [999] | (-0.025, -0.029) [999] | 0.0113 | 0.0377 |
| 2 | top-left (0, 0) | (+0.217, +0.299) [0] | (+0.206, +0.301) [0] | (+0.180, +0.306) [0] | 0.0113 | 0.0376 |
| 2 | top-right (0, 4095) | (+0.238, -0.321) [0] | (+0.226, -0.319) [0] | (+0.201, -0.314) [0] | 0.0113 | 0.0375 |
| 2 | bottom-left (3071, 0) | (-0.139, +0.190) [0] | (-0.150, +0.192) [0] | (-0.176, +0.197) [0] | 0.0113 | 0.0376 |
| 2 | bottom-right (3071, 4095) | (-0.159, -0.221) [0] | (-0.171, -0.219) [0] | (-0.196, -0.214) [0] | 0.0113 | 0.0376 |
| 2 | centre (1535.5, 2047.5) | (-0.000, +0.000) [999] | (-0.011, +0.002) [999] | (-0.037, +0.007) [999] | 0.0113 | 0.0376 |
| 3 | top-left (0, 0) | (+1.232, +1.656) [999] | (+1.229, +1.667) [999] | (+1.220, +1.692) [999] | 0.0113 | 0.0378 |
| 3 | top-right (0, 4095) | (+1.334, -1.775) [0] | (+1.330, -1.765) [0] | (+1.322, -1.740) [0] | 0.0111 | 0.0371 |
| 3 | bottom-left (3071, 0) | (-1.207, +1.607) [0] | (-1.211, +1.618) [0] | (-1.220, +1.643) [0] | 0.0114 | 0.0380 |
| 3 | bottom-right (3071, 4095) | (-1.306, -1.755) [999] | (-1.310, -1.744) [999] | (-1.319, -1.719) [999] | 0.0112 | 0.0373 |
| 3 | centre (1535.5, 2047.5) | (-0.000, +0.000) [999] | (-0.004, +0.011) [999] | (-0.012, +0.036) [999] | 0.0113 | 0.0375 |
| 4 | top-left (0, 0) | (+1.024, +1.363) [999] | (+1.031, +1.372) [999] | (+1.048, +1.392) [999] | 0.0114 | 0.0379 |
| 4 | top-right (0, 4095) | (+1.104, -1.453) [999] | (+1.111, -1.445) [999] | (+1.129, -1.425) [999] | 0.0112 | 0.0375 |
| 4 | bottom-left (3071, 0) | (-1.075, +1.416) [999] | (-1.068, +1.424) [999] | (-1.051, +1.444) [999] | 0.0113 | 0.0376 |
| 4 | bottom-right (3071, 4095) | (-1.156, -1.540) [999] | (-1.148, -1.531) [999] | (-1.131, -1.512) [999] | 0.0112 | 0.0372 |
| 4 | centre (1535.5, 2047.5) | (+0.000, +0.000) [999] | (+0.007, +0.009) [999] | (+0.025, +0.029) [999] | 0.0113 | 0.0376 |
| 5 | top-left (0, 0) | (-0.208, -0.294) [0] | (-0.197, -0.297) [0] | (-0.171, -0.302) [0] | 0.0113 | 0.0376 |
| 5 | top-right (0, 4095) | (-0.228, +0.313) [0] | (-0.217, +0.311) [0] | (-0.191, +0.306) [0] | 0.0113 | 0.0376 |
| 5 | bottom-left (3071, 0) | (+0.130, -0.181) [0] | (+0.141, -0.184) [0] | (+0.167, -0.189) [0] | 0.0113 | 0.0377 |
| 5 | bottom-right (3071, 4095) | (+0.150, +0.217) [0] | (+0.161, +0.215) [0] | (+0.187, +0.210) [0] | 0.0113 | 0.0376 |
| 5 | centre (1535.5, 2047.5) | (+0.000, -0.000) [999] | (+0.011, -0.002) [999] | (+0.037, -0.007) [999] | 0.0113 | 0.0376 |
| 6 | top-left (0, 0) | (-1.229, -1.650) [999] | (-1.225, -1.660) [999] | (-1.217, -1.686) [999] | 0.0114 | 0.0379 |
| 6 | top-right (0, 4095) | (-1.331, +1.763) [0] | (-1.327, +1.753) [0] | (-1.319, +1.728) [0] | 0.0112 | 0.0373 |
| 6 | bottom-left (3071, 0) | (+1.204, -1.594) [0] | (+1.208, -1.605) [0] | (+1.216, -1.630) [0] | 0.0114 | 0.0381 |
| 6 | bottom-right (3071, 4095) | (+1.304, +1.749) [999] | (+1.307, +1.738) [999] | (+1.316, +1.713) [999] | 0.0112 | 0.0374 |
| 6 | centre (1535.5, 2047.5) | (+0.000, -0.000) [999] | (+0.004, -0.011) [999] | (+0.012, -0.036) [999] | 0.0113 | 0.0377 |

## 6. Results of steps 1-8 (expected versus measured)

The step 1-7 verification reports were given in the chat sessions and are NOT on disk; every line below was re-measured in this step (source "step 9": describe_simulation.py / check_stacking_geometry.py) or read from validation_summary.json (step 8), pipeline.log or render_report.jsonl. Values quoted from an earlier step without re-measurement are marked "from step N".

| step | check | expected | measured | source |
|---|---|---|---|---|
| 1 | nominal GSD = h p / f (h = 500 km) | 4.741379310344828 m | 4.741379310344828 m | step 9 (sensor_timing.gsd) |
| 1 | nominal v_g (h = 500 km) | 7059.2164500563995 m/s | 7059.2164500564 m/s | step 9 (sensor_timing.ground_speed) |
| 1 | dt = 1 / rate_rows (frame_period_mode ground, step 10 D4) | 0.6661251071 ms | 0.6661251071 ms | step 9 (cameras.json frame_period_s, results.npz) |
| 1 | dt closed form rho p / f / v_t | equal to dt within 1e-5 relative | 0.6661217456 ms (rel diff 5.0e-06) | cameras.json dt_closed_form_s |
| 1 | a_chief = R_avg + 500 km (step 10 D1) | 6871008.7714 m | 6871008.7714 m | cameras.json a_chief_m |
| 1 | SSO inclination at a_chief (step 10 D3) | about 97.3748 deg | 97.374489 deg | step 9 (sso_inclination) |
| 1 | nominal footprint across x along | 19420.7 x 14565.5 m | 19420.7 x 14565.5 m | step 9 (sensor_timing.footprint_m) |
| 2 | ROE metre table | PLAN_CONTEXT values | max dev 3.5e-05 m | step 9 (roe_hex7_dmax1km.json) |
| 2 | ROE realised at t = 0 | equal to roe_m | max dev 1.5e-08 m | step 9 (elements_to_roe of results.npz) |
| 3 | states_eci shape | [1000, 7, 6] | [1000, 7, 6] | step 9 (results.npz) |
| 3 | diff(times) = dt | rel 1e-12 | max abs dev 1.0e-16 s | validation_summary.json time_grid |
| 3 | chief at t_c vs chief_capture | < 1 mm | 2.08e-06 mm | step 9 (RK4 half step from sample 499) |
| 3 | chief geocentric nadir at t_c hits the Melbourne point | < 1 m | 0.000 m | step 9 |
| 3 | dense output vs RK4 dt/4 | < 1e-6 m | 4.39e-08 m | step 9 |
| 3 | T-N lattice edges | 894.427 +/- 1 m | 894.364 .. 894.490 m | step 9; validation_summary.json formation |
| 3 | 3D neighbour distance | <= 1001 m | max 985.924 m | step 9 |
| 3 | change of any pair distance over the window | < 1 m | 0.1489 m | step 9 |
| 3 | |dr| = |v| dt | rel 1e-6 | max rel err 8.0e-09 | validation_summary.json poses |
| 3 | SSO RAAN drift | 0.9856 deg/day +/- 0.5% | 0.98608 deg/day (+0.05%) | step 9 (1-day J2 RK4) |
| 3 | chief nadir ground speed | report vs v_t = 7130.247 m/s at t_c | 7130.247 m/s | step 9 (common_target_ecef_m) |
| 3 | bbox corner margin in the chief frame f0 / f999 | -734 / +387 m (from step 3, prompt_4_per_frame_poses.md:32) | -701.8 / +419.9 m | step 9 |
| 4 | chief footprint across x along, f0 / f999 | 19420.7 x 14565.5 m +/- 0.5% | 19454.7 x 14591.1 / 19454.1 x 14590.7 m | step 9 (cameras.json footprint_local_m) |
| 4 | chief native GSD | GSD_true = 4.7496 m +/- 0.1% | 4.749537 .. 4.749680 m | step 9 (poses.jsonl) |
| 4 | chief ground advance per frame | 1 +/- 2e-5 px per frame (slant range changes ~15 m over the window) | 0.99999 .. 1.00002 px; image rows 0.99998 .. 1.00002 | validation_summary.json advance; step 9 (stacking_inputs.npz) |
| 4 | common target -> image centre | < 0.01 px | max 9.3e-11 px (7 sats) | step 9; validation_summary.json target |
| 4 | deputy ground across alignment | >= 1 - 1e-10 | 1 - 3.3e-16 | step 9 (cameras.json) |
| 4 | selected tiles | 570 | 570 | step 9 (cameras.json) |
| 5 | terrain caches | 570 valid .npz | 570 files, 570 selected present | step 9 (data/swarm_terrain) |
| 5 | triangle count | as loaded by the renderer | 13,670,356 (run header 13,670,356) | step 9; render_report.jsonl |
| 6 | Blender projection checks (every rendered pose) | corner < 0.1 px, GSD step < 0.002, target < 0.01 px | corner 0.000822 px, pixel step dev 0.000122, target 0.000183 px, alignment 1 - 3.3e-16 | output/orbits_melbourne/eevee_full_1000/logs/satellite_00_20261109T181809Z.log:1024, output/orbits_melbourne/eevee_full_1000/logs/satellite_01_20261109T192701Z.log:1024, output/orbits_melbourne/eevee_full_1000/logs/satellite_02_20261109T181810Z.log:1024, output/orbits_melbourne/eevee_full_1000/logs/satellite_03_20261109T181809Z.log:1024, output/orbits_melbourne/eevee_full_1000/logs/satellite_04_20261109T181809Z.log:1024 |
| 6 | render-time target error | < 0.01 px | max 1.83e-04 px | validation_summary.json target.logged_max |
| 7 | EEVEE render time per frame | n/a | 2.85 .. 4.07 s mean per satellite | validation_summary.json time.render_s |
| 7 | EEVEE minus Cycles brightness | about 0 (from step 7: about 0.4 DN) | not re-measured: no Cycles renders in this simulation run | step 9 (Cycles PNGs in the simulation run folder, alpha = 255 in both) |
| 8 | PNGs | 5000 valid RGBA 8-bit 4096x3072 | 5000 / 5000 | validation_summary.json png |
| 8 | alpha coverage (alpha > 0.5) | non-zero every frame | 15.0276 .. 15.0736% | validation_summary.json coverage |
| 8 | chief phase correlation, vertical | about 1.00 px | 1.0000 px vs pose prediction 1.0000 | validation_summary.json phase |
| 8 | deputy vs chief image shift | small | up to 0.66 px; predicted from poses on terrain vertices within 0.11 px | validation_summary.json overlap; step 9 (deputy_offsets) |
| 8 | verdict | PASS | PASS WITH NOTES | validation_summary.json verdict |

## 7. Known limitations and deviations

- Terrain is a 2.5 m heightfield of the photomesh upper surface: no facades, overhangs, bridges underside or vertical walls; steep edges become ramps one cell wide. Missing cells are filled from the nearest sampled cell.
- RGB only: pixels are the baked aerial texture colours (with the survey's own sun and shadows) through an emission shader; no illumination model, BRDF, atmosphere, noise, MTF or spectral bands. Any hyperspectral cube built from these frames has 3 channels per satellite.
- Full-frame perspective camera, not a pushbroom: each frame is an instantaneous snapshot ("RGB full-frame perspective approximation of pushbroom imager", cameras.json mode).
- Footprints, ground pixel sizes, distance_m and native_gsd_m are on the local z = 0 tangent plane; the common target is on the WGS84 ellipsoid (z about -0.45 m at the target). The z = 0 plane departs from the ellipsoid by up to 4.3 m at the dataset corners. Terrain heights are AHD used as ellipsoidal height.
- EEVEE vs Cycles brightness not re-measured on this run (no Cycles renders in the simulation run folder; step 7 measured about +0.4 DN on the previous run).
- Chief phase correlation (1/1000 px) measures 1.0000 .. 1.0000 px; minus the pose prediction: -1.8e-05 .. +1.7e-05 px, within the 1e-3 px resolution of the estimator (step 8).
- Deputy images are shifted up to 0.66 px relative to the chief (step 8 phase correlation). The poses predict the same shifts within 0.11 px from the mean deputy-minus-chief position of terrain vertices: the shift is the scale difference (distance differs by up to 415.4 m from the chief, i.e. up to 2.22 px at the image corners on z = 0, parallax table) acting on a scene that is off-centre and moves across the frame. It is geometry, not an error; use chief_to_satellite_pixel to co-register.
- Satellites 5 and 6 are propagated and have poses (cameras.json) but were not rendered; stacking_geometry reads their poses from cameras.json.
- One clock for the swarm (step 10 D5): dt makes the chief advance 0.99998 .. 1.00002 rows per frame (exactly 1 at t_c); the deputies advance 0.99916 .. 1.00084 rows per frame in their own pixels (own range/GSD), reported, not corrected.
- In the first 2 frames the dataset crosses the top image edge (alpha > 0 in row 0): part of the northern tiles is not imaged in those frames. Only 999..1957 (959) rows contain the scene in every frame of the chief.
- The chief boresight is the GEOCENTRIC nadir; it is 0.1862 deg from the ellipsoid normal at the target. The chief is placed at a_chief = 6871008.7714 m along the geocentric radial (ellipsoidal height 500865.77 m).
- Earth orientation: fixed pole, GMST with UTC ~ UT1, no precession/nutation/polar motion; J2 only, no drag, no third bodies, no SRP (irrelevant over 0.67 s, but the absolute epoch geometry is approximate).
- ROE are osculating and applied directly at t = 0 (no mean/osculating map).
- PLAN_CONTEXT disagreements found: ground track azimuth is -12.25 deg from north at t_c (PLAN estimate "about 9 deg"); the run integrator for the 1000-sample propagation uses rtol 1.0e-10, atol 1.0e-09 (config default), tighter values only for the chief back-propagation; the osculating a at t = 0 is 6871002.0 m, not a_chief = 6871008.8 m (short-period J2 variation of the osculating a; a_chief is exact at t_c only); the export sample of formation_distances.json is 500, not t_c = 499.5 dt. Everything else checked here matches.

