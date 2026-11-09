# Simulation description: 7-satellite swarm burst over Melbourne (run eevee_full_1000)

Generated 2026-11-09T11:25:48Z by `scripts/describe_simulation.py` (numbers measured by that script, by `scripts/check_stacking_geometry.py`, or read from `validation_summary.json` / `stacking_inputs.npz`; code references are `file:line` in this repository, `orbits/` being the submodule; line numbers refer to repository HEAD 6892e4c and submodule 1d8ac1f, plus the uncommitted step 8/9 scripts). Read-only on every existing output.

- Run folder: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/eevee_full_1000`
- Simulation: `/home/pareschi/Desktop/melbourne/blendersatellite/output/orbits_melbourne/20261106T080712Z_run_IxoFnH/simulation` (cameras.json, results.npz, config.yaml)
- Geometry module: `scripts/stacking_geometry.py`; stacking inputs: `stacking_inputs.npz` in the run folder.

## 1. Time and orbit

| quantity | value | source |
|---|---|---|
| epoch of sample 0 (t = 0) | 2026-10-27T23:59:59.664507+00:00 | cameras.json epochs[0].timestamp; melbourne_overpass.py:215-219 |
| capture instant t_c | 2026-10-28T00:00:00+00:00, t_c = 0.335493178637 s = 499.500000 dt | cameras.json capture_time_s; melbourne_overpass.py:132 |
| last sample | 2026-10-28T00:00:00.335493+00:00, t = 0.670986357274 s | cameras.json epochs[999] |
| time grid | t_k = k dt, k = 0..999; max abs(diff(t) - dt) = 6.87e-17 s | results.npz times; sensor_timing.py:38-41 |
| dt | 0.6716580152896 ms (sensor_timing.frame_period(500 km) = 0.6716580152896 ms) | formation_distances.json; sensor_timing.py:32-35 |
| dt definition | GSD / v_g = 4.741379310345 m / 7059.2164500564 m/s (nominal h = 500 km, Re = 6378137 m) | sensor_timing.py:20-35; melbourne_overpass.py:130 |
| window | 670.986357 ms (1000 samples) | results.npz |

Chief osculating elements (orbits.elements.eci_to_elements, elements.py:32-59; u = argp + nu). The state at t_c is integrated from sample 499 by dt/2 (RK4, 8 steps, same J2 model) and agrees with the chief_capture state rebuilt from melbourne_overpass.py:107-141 within 1.86e-06 mm (from sample 500 backwards: 2.08e-06 mm).

| element | t = 0 | t = t_c |
|---|---|---|
| a m | 6870133.7213 | 6870140.5487 |
| e | 9.957e-07 | 7.117e-16 |
| i deg | 97.371213183 | 97.371209500 |
| raan deg | 175.596633903 | 175.596636805 |
| argp deg | 138.430722 | 0.000000 |
| nu deg | 183.550675 | 322.002710 |
| u deg | 321.981397685 | 322.002710179 |

- At t_c the chief state is built exactly circular in the two-body sense (r perpendicular to v, |v| = sqrt(mu/r); melbourne_overpass.py:137-141), hence a = r and e = 0 (argp = 0 by the circular convention, elements.py:55). The elements are two-body osculating elements of a J2 trajectory, so they carry the short-period J2 variation: between t = 0 and t_c a changes by +6.827 m and e by -9.96e-07. With e ~ 1e-6, argp and nu are individually ill-defined; u = argp + nu is the meaningful angle.
- SSO inclination actually used: sso_inclination(a_chief) = 97.371209500 deg for a_chief = 6870140.548691 m (melbourne_overpass.py:126-128, elements.py:8-17; pipeline.log prints the same value; cameras.json formation_geometry.inclination_deg = 97.371209500). The osculating i at t = 0 is 97.371213183 deg (back-propagated state, config.yaml chief.inclination_deg).
- RAAN drift of the chief over 1 day of J2 propagation from the t = 0 state (RK4 10 s, linear fit of the osculating RAAN): 0.98607 deg/day (target 0.9856 deg/day).
- Satellite geodetic position at t_c (WGS84, pyproj EPSG:4978 -> 4979): lat -37.800979 deg, lon 144.965039 deg, h 499997.552 m; geocentric radius 6870140.549 m. The chief is placed 500 km along the GEOCENTRIC radial of the Melbourne point (melbourne_overpass.py:117): its geocentric nadir hits WGS84 at lat -37.814555, lon 144.965039, 0.000 m from the Melbourne origin (lat/lon -37.814555, 144.965039); the geodetic sub-satellite point is the satellite lat/lon above.
- Ground-track azimuth at t_c (ECEF-relative velocity, ENU at the sub-satellite point): 347.7594 deg from north (-12.2406 deg, i.e. towards north-north-west); horizontal ECEF-relative speed 7691.250 m/s, vertical -22.652 m/s.
- Pass: ascending (ECI v_z = +5952.923 m/s at t_c).
- Local mean solar time at the pass: 09:39:52 (UTC 00:00:00 + lon/15 h).
- Ground speed of the chief nadir point (= common target) on WGS84: 7131.5576 m/s (min 7131.5403, max 7131.5749), versus v_g = 7059.2165 m/s used for dt (ratio 1.010248).
- Propagator: point mass + J2 (orbits/src/orbits/physics.py:15-36; mu = 3.986004418e14, Re = 6378137 m, J2 = 1.08262668e-3), fixed-pole ECI, torchdiffeq odeint dopri5 with rtol 1.0e-10, atol 1.0e-09 (simulation config.yaml:1032-1036; cli.py:39-40, 62; propagation.py:21), float64 on cuda:0, 1000 output samples (dense output of the adaptive steps). The back-propagation of the chief from t_c to t = 0 uses dopri5 with rtol 1e-11, atol 1e-10 (melbourne_overpass.py:145-146). Deputies are created at t = 0 with roe_to_eci (melbourne_overpass.py:190-192, roe.py:86-89).
- Dense-output error (recomputed in this step): all 7 satellites, every sample versus an independent fixed-step RK4 (step dt/4, numpy, same J2 model) started from sample 0: max position error 4.346e-08 m.

## 2. Formation

ROE (quasi-nonsingular, osculating, applied at t = 0 without mean/osculating conversion; roe.py:1-14). The metre values are the specification (roe_hex7_dmax1km.json, generator roe.py:101-124). The dimensionless ROE used by the run are roe_m / a_chief = roe_m / 6870140.548691 (melbourne_overpass.py:190-192); the JSON "roe" field uses a_ref = 6870140.5 m (difference 7e-9 relative, not used). "Realised" = elements_to_roe of the propagated states at t = 0 times a_chief. da = dlam = 0 for every deputy.

| sat | (W, X) | a dex (m) | a dey (m) | a dix (m) | a diy (m) | dex | dey | dix | diy | realised max abs diff (m) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | (1, 0) | 447.2136 | 0.0000 | 0.0000 | 894.4272 | 6.509526e-05 | 0.000000e+00 | 0.000000e+00 | 1.301905e-04 | 9.2e-09 |
| 2 | (0, 1) | 223.6068 | 387.2983 | -774.5967 | 447.2136 | 3.254763e-05 | 5.637415e-05 | -1.127483e-04 | 6.509526e-05 | 3.1e-09 |
| 3 | (-1, 1) | -223.6068 | 387.2983 | -774.5967 | -447.2136 | -3.254763e-05 | 5.637415e-05 | -1.127483e-04 | -6.509526e-05 | 3.7e-09 |
| 4 | (-1, 0) | -447.2136 | 0.0000 | 0.0000 | -894.4272 | -6.509526e-05 | 0.000000e+00 | 0.000000e+00 | -1.301905e-04 | 7.5e-09 |
| 5 | (0, -1) | -223.6068 | -387.2983 | 774.5967 | -447.2136 | -3.254763e-05 | -5.637415e-05 | 1.127483e-04 | -6.509526e-05 | 6.1e-09 |
| 6 | (1, -1) | 223.6068 | -387.2983 | 774.5967 | 447.2136 | 3.254763e-05 | -5.637415e-05 | 1.127483e-04 | 6.509526e-05 | 6.5e-09 |

Max deviation of the metre values from the PLAN_CONTEXT table: 3.5e-05 m.

Chief RTN frame (frames.py:6-39, eci_to_lvlh with the chief J2 acceleration): position (r, t, n) in metres and rotating-frame relative velocity in m/s.

| sat | (r, t, n) m, frame 0 | frame 499 | frame 999 | (vr, vt, vn) m/s, frame 0 | frame 999 |
|---|---|---|---|---|---|
| 0 | (+0.000, +0.000, +0.000) | (+0.000, +0.000, +0.000) | (+0.000, +0.000, +0.000) | (+0.0000, +0.0000, +0.0000) | (+0.0000, +0.0000, +0.0000) |
| 1 | (-352.371, -550.916, -704.564) | (-352.474, -550.654, -704.768) | (-352.576, -550.392, -704.974) | (-0.3054, +0.7814, -0.6110) | (-0.3051, +0.7819, -0.6104) |
| 2 | (+62.350, -885.647, +124.877) | (+62.186, -885.693, +124.548) | (+62.021, -885.740, +124.218) | (-0.4910, -0.1384, -0.9822) | (-0.4910, -0.1377, -0.9823) |
| 3 | (+414.650, -334.782, +829.478) | (+414.588, -335.090, +829.353) | (+414.526, -335.399, +829.228) | (-0.1856, -0.9197, -0.3712) | (-0.1859, -0.9194, -0.3719) |
| 4 | (+352.268, +550.869, +704.715) | (+352.370, +550.607, +704.920) | (+352.472, +550.345, +705.125) | (+0.3054, -0.7814, +0.6109) | (+0.3051, -0.7818, +0.6103) |
| 5 | (-62.417, +885.715, -124.659) | (-62.253, +885.762, -124.330) | (-62.088, +885.808, -124.000) | (+0.4910, +0.1383, +0.9822) | (+0.4910, +0.1376, +0.9823) |
| 6 | (-414.756, +334.796, -829.337) | (-414.694, +335.104, -829.212) | (-414.631, +335.413, -829.087) | (+0.1856, +0.9198, +0.3713) | (+0.1860, +0.9196, +0.3720) |

- T-N lattice edges (12 edges, every frame): 894.364 .. 894.490 m, max deviation from 894.427 m = 0.063 m.
- Pairwise 3D distance over the whole window (21 pairs x 1000 frames): min 896.545 m (pair [3, 4], frame 999), max 1971.779 m (pair [3, 6], frame 0); lattice neighbours max 985.924 m; largest change of any pair distance across the window 0.1502 m.

Slant distance `distance_m` (camera centre to the z = 0 point of its boresight; camera_geometry.py:97-99), all 7 satellites (5 and 6 from cameras.json, not rendered):

| sat | frame 0 (m) | frame 499 (m) | frame 999 (m) | min (m) | max (m) | minus chief f0 / f499 / f999 (m) | source |
|---|---|---|---|---|---|---|---|
| 0 | 500007.151 | 500000.008 | 499991.950 | 499991.950 | 500007.151 | +0.000 / +0.000 / +0.000 | poses.jsonl |
| 1 | 499655.581 | 499648.335 | 499640.175 | 499640.175 | 499655.581 | -351.571 / -351.673 / -351.776 | poses.jsonl |
| 2 | 500070.302 | 500062.993 | 500054.771 | 500054.771 | 500070.302 | +63.150 / +62.986 / +62.821 | poses.jsonl |
| 3 | 500422.601 | 500415.395 | 500407.275 | 500407.275 | 500422.601 | +415.450 / +415.388 / +415.325 | poses.jsonl |
| 4 | 500360.218 | 500353.177 | 500345.222 | 500345.222 | 500360.218 | +353.067 / +353.169 / +353.272 | poses.jsonl |
| 5 | 499945.534 | 499938.555 | 499930.663 | 499930.663 | 499945.534 | -61.617 / -61.453 / -61.288 | cameras.json |
| 6 | 499593.196 | 499586.114 | 499578.119 | 499578.119 | 499593.196 | -413.955 / -413.893 / -413.831 | cameras.json |

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
3. Measured: min alignment over all deputies and frames = 1 - 4.4e-16 (cameras.json ground_across_alignment_with_chief).

Chief image axes in ENU (unit vectors, columns of camera_to_local) and azimuth of image-up from north (atan2(E, N) of the up column):

| frame | image-right (E, N, U) | image-up (E, N, U) | boresight (E, N, U) | azimuth of image-up (deg) |
|---|---|---|---|---|
| 0 | (+0.977272, +0.211987, -0.000689) | (-0.211988, +0.977268, -0.002801) | (-0.000080, -0.002884, -0.999996) | 347.7611 (-12.2389) |
| 499 | (+0.977266, +0.212016, -0.000689) | (-0.212017, +0.977261, -0.003176) | (-0.000000, -0.003250, -0.999995) | 347.7594 (-12.2406) |
| 999 | (+0.977260, +0.212045, -0.000689) | (-0.212046, +0.977253, -0.003552) | (+0.000080, -0.003618, -0.999993) | 347.7576 (-12.2424) |

Chief boresight versus the WGS84 ellipsoid normal at the common target (geocentric vs geodetic nadir): 0.1863 deg, 0.1862 deg, 0.1862 deg at frames 0, 499, 999.

Off-nadir angle (boresight vs the satellite's own geocentric nadir, recomputed; matches cameras.json off_nadir_deg) and angle between each boresight and the chief boresight, min / max over the 1000 frames:

| sat | off-nadir min (deg) | off-nadir max (deg) | max diff vs off_nadir_deg | vs chief min (deg) | vs chief max (deg) |
|---|---|---|---|---|---|
| 0 | 0.000000 | 0.000002 | 0.0e+00 | 0.000000 | 0.000002 |
| 1 | 0.095100 | 0.095103 | 0.0e+00 | 0.102559 | 0.102562 |
| 2 | 0.095018 | 0.095021 | 0.0e+00 | 0.102477 | 0.102480 |
| 3 | 0.094955 | 0.094958 | 0.0e+00 | 0.102414 | 0.102417 |
| 4 | 0.094966 | 0.094969 | 0.0e+00 | 0.102425 | 0.102428 |
| 5 | 0.095047 | 0.095050 | 0.0e+00 | 0.102507 | 0.102510 |
| 6 | 0.095111 | 0.095114 | 0.0e+00 | 0.102570 | 0.102573 |

## 4. Image conventions

Mapping from a PNG pixel to a ray (implemented in `scripts/stacking_geometry.py`):

1. PNG index (r, c): row r from the TOP (0..3071), column c from the LEFT (0..4095). The integer index is the pixel CENTRE; pixel (r, c) covers [r - 1/2, r + 1/2) x [c - 1/2, c + 1/2).
2. Blender view coordinates (world_to_camera_view, origin at the BOTTOM-LEFT, [0, 1]^2): u = (c + 1/2) / 4096, v = 1 - (r + 1/2) / 3072. The renderer writes the image top row first, so row 0 is v ~ 1.
3. Camera ray: sensor_fit HORIZONTAL, f = 580 mm, Sw = 22.528 mm, Sh = Sw * 3072/4096 = 16.896 mm (render_swarm.py:190-192, 251-253, 259): d_cam = ((u - 1/2) Sw/f, (v - 1/2) Sh/f, -1).
4. ENU: d_local = camera_to_local d_cam (normalised), origin position_local_m. Inverse: p = camera_to_local^T (X - C), u = 1/2 + (f/Sw) p_x/(-p_z), v = 1/2 + (f/Sh) p_y/(-p_z).
5. Optical axis / image centre: (r, c) = (1535.5, 2047.5), between the 4 central pixels. The footprint corners in cameras.json are the outer pixel EDGES, (r, c) = (-1/2, -1/2) etc. (camera_geometry.py:105-109; checked by render_swarm.py:262-265).

Direction of motion: image-up (decreasing row) is the chief's ECEF-relative velocity, so ground features move towards INCREASING PNG row as the frame index grows. Advance of the ground in the chief's own pixels (stacking_inputs.npz image_motion_rows_px[0]): +1.01023 .. +1.01026 rows per frame (sign +, i.e. downward), columns -3.0e-08 .. -2.9e-08. Checked on the PNGs without the poses: the integer row shift that best maps chief frame 0 onto frame 50 is +51 rows (masked SSD 144.1 vs 2521.5 for the opposite sign; poses predict +50.511).

Real-image check of the convention (`check_stacking_geometry.py`): 200000 random vertices of the 570 terrain caches (of 6960533; seed 0), projected with pixel_from_ground, PNG read at the nearest pixel where alpha > 0, |dRGB| = mean over R, G, B of |PNG - vertex| (8-bit DN). n = samples with alpha > 0.

| sat / frame | variant | n | median | p95 | within 25 DN |
|---|---|---|---|---|---|
| 0/0 | correct | 199983 | 15.00 | 71.67 | 66.71% |
| 0/0 | row_flipped | 21526 | 36.67 | 113.33 | 34.94% |
| 0/0 | col_flipped | 79317 | 41.33 | 120.00 | 30.63% |
| 0/0 | half_px_plus | 199984 | 17.33 | 82.00 | 61.93% |
| 0/0 | half_px_minus | 199982 | 17.67 | 83.00 | 61.53% |
| 0/499 | correct | 200000 | 15.00 | 71.33 | 66.61% |
| 0/499 | row_flipped | 104594 | 42.67 | 120.33 | 30.07% |
| 0/499 | col_flipped | 79333 | 41.33 | 119.80 | 30.59% |
| 0/499 | half_px_plus | 200000 | 17.33 | 82.00 | 61.88% |
| 0/499 | half_px_minus | 200000 | 17.67 | 83.00 | 61.59% |
| 0/999 | correct | 200000 | 15.00 | 71.33 | 66.61% |
| 0/999 | row_flipped | 97118 | 42.33 | 118.00 | 29.30% |
| 0/999 | col_flipped | 79326 | 41.33 | 119.67 | 30.50% |
| 0/999 | half_px_plus | 200000 | 17.33 | 82.00 | 61.92% |
| 0/999 | half_px_minus | 200000 | 17.67 | 83.33 | 61.63% |
| 3/0 | correct | 199990 | 15.00 | 71.67 | 66.64% |
| 3/0 | row_flipped | 21524 | 36.33 | 113.33 | 34.51% |
| 3/0 | col_flipped | 79340 | 41.33 | 120.00 | 30.58% |
| 3/0 | half_px_plus | 199992 | 17.33 | 82.00 | 61.98% |
| 3/0 | half_px_minus | 199987 | 17.67 | 83.00 | 61.60% |
| 3/499 | correct | 200000 | 15.00 | 71.67 | 66.66% |
| 3/499 | row_flipped | 104606 | 42.67 | 120.00 | 30.08% |
| 3/499 | col_flipped | 79325 | 41.33 | 119.67 | 30.59% |
| 3/499 | half_px_plus | 200000 | 17.33 | 82.00 | 61.85% |
| 3/499 | half_px_minus | 200000 | 17.33 | 83.33 | 61.61% |
| 3/999 | correct | 200000 | 15.00 | 71.33 | 66.77% |
| 3/999 | row_flipped | 97116 | 42.33 | 117.67 | 29.26% |
| 3/999 | col_flipped | 79331 | 41.33 | 119.67 | 30.52% |
| 3/999 | half_px_plus | 200000 | 17.33 | 82.00 | 61.89% |
| 3/999 | half_px_minus | 200000 | 17.67 | 83.33 | 61.57% |

half_px_plus / minus shift both axes by +1/2 / -1/2 px (pixel-corner instead of pixel-centre convention, either sign). Bilinear sweep of a sub-pixel offset (dr, dc) in {-0.5, -0.25, 0, 0.25, 0.5}^2: the minimum mean |dRGB| is at +0.00,+0.00 for every satellite/frame.

Round trip pixel -> ground (z = 0, 30, 100 m) -> pixel on a 61 x 81 grid incl. the outer edges, frames 0/499/999: max error 1.86e-11 px over all 7 satellites (per satellite: 0: 1.9e-11, 1: 2.3e-12, 2: 1.6e-12, 3: 1.9e-12, 4: 1.9e-12, 5: 2.1e-12, 6: 1.5e-12).

Common target -> image centre, all 1000 frames (this module vs step 8, validation_summary.json target.reproj_max):

| sat | max px (this module) | frame | step 8 max px |
|---|---|---|---|
| 0 | 9.91e-11 | 251 | 9.91e-11 |
| 1 | 4.55e-13 | 985 | 0.00e+00 |
| 2 | 0.00e+00 | 0 | 0.00e+00 |
| 3 | 0.00e+00 | 0 | 0.00e+00 |
| 4 | 4.55e-13 | 165 | 2.27e-13 |
| 5 | 4.55e-13 | 219 | not rendered |
| 6 | 4.55e-13 | 734 | not rendered |

## 5. Render pipeline

- Blender 5.2.2 LTS, engine BLENDER_EEVEE, GPU NVIDIA TITAN Xp via VULKAN (renders/render_report.jsonl run header).
- Settings that differ from the factory defaults (scene from read_factory_settings(use_empty=True), render_swarm.py:132): engine BLENDER_EEVEE (136); taa_render_samples 8 (137, --samples 8); use_shadows, use_raytracing, use_fast_gi, use_volumetric_shadows, use_overscan = False (139-141); motion blur off (142); resolution 4096 x 3072 at 100% (151, 259); PNG RGBA (152-153), colour depth left at 8 bit; film_transparent True (154); view transform Standard, look None (155-156); camera PERSP, sensor_fit HORIZONTAL, DOF off (190-193); clip range distance_m -/+ 10 km per camera (257-258); lens 580 mm, sensor 22.528 x 16.896 mm (251-253). The pixel filter, colour space of the PNG (sRGB) and everything else are factory defaults. No lights, no world.
- Material: one emission shader fed by the "Color" point attribute, strength 1 (render_swarm.py:157-164). Vertex colours are the 8-bit sRGB texture samples of the cache converted to scene-linear (render_swarm.py:179-183); the Standard view transform converts back to sRGB on output. A pixel therefore holds the baked aerial texture colour (including the sun/shadows of the original aerial survey) interpolated over the 2.5 m triangles and averaged by the 8-sample anti-aliasing. No illumination model, no atmosphere, no spectral content beyond R, G, B.
- Terrain: 570 tiles (cameras.json selected_tiles; 570 .npz in data/swarm_terrain), each a regular 2.5 m grid of the photomesh UPPER surface (highest face per cell, empty cells filled from the nearest sampled cell; prepare_swarm_terrain.py:62-85, 117-121), so roofs are kept and facades/overhangs are lost. Vertices 6,960,533, triangles 13,670,356 (2 per grid cell, render_swarm.py:174-176; run header reports 13,670,356); mean sampled fraction of cells 96.1%; local z range -13.78 .. 322.63 m. Heights are AHD used as ellipsoidal height (cameras.json terrain_vertical), converted with the full ECEF -> ENU transform, so the z = 0 tangent plane is not the ellipsoid: the ellipsoid height of z = 0 at the 4 dataset bbox corners is 4.13, 1.74, 1.92, 4.32 m.
- Alpha: film_transparent, so alpha = geometry coverage of the pixel (0 outside the dataset, fractional on the anti-aliased boundary). Pixels with 0 < alpha < 255: 24029 .. 24550 per image. Alpha is straight (not premultiplied): median luminance of pixels with alpha 1-127 / 128-254 / 255 at chief frame 0 = 115.2 / 113.8 / 117.9 DN.
- Format: PNG, 8-bit RGBA, 4096 x 3072, sRGB (validation_summary.json png: 5000/5000 pass).

Scene extent in the image, computed from the PNGs (alpha > 0, read-only; stacking_inputs.npz alpha_row_any / alpha_col_any / alpha_bounds). Union = rows/columns with alpha > 0 in at least one frame; intersection = in every frame. All ranges are contiguous and inclusive (PNG indices).

| sat | union rows | union cols | intersection rows | intersection cols | frames touching row 0 (last) | bottom/left/right edge touched |
|---|---|---|---|---|---|---|
| 0 | 0..2963 (2964) | 659..2571 (1913) | 1003..1954 (952) | 659..2571 (1913) | 8 (7) | none |
| 1 | 0..2964 (2965) | 658..2571 (1914) | 1002..1954 (953) | 658..2571 (1914) | 9 (8) | none |
| 2 | 0..2963 (2964) | 659..2571 (1913) | 1003..1953 (951) | 659..2571 (1913) | 7 (6) | none |
| 3 | 0..2962 (2963) | 660..2570 (1911) | 1003..1953 (951) | 660..2570 (1911) | 6 (5) | none |
| 4 | 0..2962 (2963) | 660..2570 (1911) | 1003..1953 (951) | 660..2570 (1911) | 7 (6) | none |

The imaged tiles touch the top image row (forward, north-north-west) only in the first 6-9 frames; no other image edge is ever reached. The bbox-corner margin is more pessimistic because the 570 tiles do not fill the 8.4 x 8.4 km bbox: chief margin of the 4 EPSG:7855 bbox corners (on the ellipsoid; metres = pixels x native GSD; negative = outside) frame 0: -734.2 m, frame 500: +1660.7 m, frame 999: +387.2 m.

Stacking geometry (stacking_inputs.npz; ground sizes at the image centre on z = 0; advance = image motion of the ground in the satellite's own pixels between consecutive frames):

| sat | native GSD f0 / f999 (m) | ground px across f0 / f999 (m) | ground px along f0 / f999 (m) | centre step (m) | advance rows (px/frame) | advance cols (px/frame) | ratio to chief |
|---|---|---|---|---|---|---|---|
| 0 | 4.741447 / 4.741303 | 4.741448 / 4.741304 | 4.741466 / 4.741333 | 4.789968 | 1.010226 .. 1.010261 | -3.0e-08 .. -2.9e-08 | 1.000000 .. 1.000000 |
| 1 | 4.738113 / 4.737967 | 4.738123 / 4.737977 | 4.738120 / 4.737981 | 4.789968 | 1.010940 .. 1.010976 | +1.9e-06 .. +1.9e-06 | 1.000707 .. 1.000707 |
| 2 | 4.742046 / 4.741899 | 4.742046 / 4.741899 | 4.742049 / 4.741906 | 4.789968 | 1.010102 .. 1.010139 | -1.7e-06 .. -1.7e-06 | 0.999878 .. 0.999878 |
| 3 | 4.745387 / 4.745241 | 4.745389 / 4.745244 | 4.745398 / 4.745262 | 4.789968 | 1.009389 .. 1.009425 | -4.9e-06 .. -4.9e-06 | 0.999171 .. 0.999172 |
| 4 | 4.744795 / 4.744653 | 4.744796 / 4.744654 | 4.744833 / 4.744706 | 4.789968 | 1.009509 .. 1.009544 | -5.1e-06 .. -5.1e-06 | 0.999290 .. 0.999290 |
| 5 | 4.740863 / 4.740722 | 4.740865 / 4.740724 | 4.740912 / 4.740789 | 4.789968 | 1.010343 .. 1.010378 | +2.9e-06 .. +2.9e-06 | 1.000115 .. 1.000116 |
| 6 | 4.737522 / 4.737379 | 4.737535 / 4.737392 | 4.737549 / 4.737419 | 4.789968 | 1.011061 .. 1.011096 | +6.8e-06 .. +6.8e-06 | 1.000826 .. 1.000827 |

Parallax (step 9 B3): chief pixel (r, c) -> ground point at height h -> pixel of the deputy at the same frame (chief_to_satellite_pixel). "Offset" = deputy pixel - chief pixel (rows, cols), largest norm over frames 0, 499, 999 (frame in brackets). "Parallax" = largest norm of offset(h) - offset(0) over the same frames, i.e. the part caused by the height alone; offset(0) is the scale/position difference on the z = 0 plane.

| sat | chief pixel | offset h=0 (rows, cols) | offset h=30 m | offset h=100 m | parallax 30 m (px) | parallax 100 m (px) |
|---|---|---|---|---|---|---|
| 1 | top-left (0, 0) | (-1.020, -1.353) [999] | (-1.027, -1.361) [999] | (-1.045, -1.382) [999] | 0.0114 | 0.0381 |
| 1 | top-right (0, 4095) | (-1.101, +1.453) [999] | (-1.108, +1.444) [999] | (-1.126, +1.425) [999] | 0.0113 | 0.0377 |
| 1 | bottom-left (3071, 0) | (+1.072, -1.415) [999] | (+1.065, -1.424) [999] | (+1.048, -1.444) [999] | 0.0114 | 0.0379 |
| 1 | bottom-right (3071, 4095) | (+1.153, +1.530) [999] | (+1.146, +1.522) [999] | (+1.128, +1.502) [999] | 0.0112 | 0.0374 |
| 1 | centre (1535.5, 2047.5) | (-0.000, -0.000) [999] | (-0.008, -0.009) [999] | (-0.025, -0.029) [999] | 0.0113 | 0.0378 |
| 2 | top-left (0, 0) | (+0.218, +0.299) [0] | (+0.207, +0.302) [0] | (+0.181, +0.307) [0] | 0.0113 | 0.0377 |
| 2 | top-right (0, 4095) | (+0.238, -0.322) [0] | (+0.227, -0.319) [0] | (+0.201, -0.315) [0] | 0.0113 | 0.0377 |
| 2 | bottom-left (3071, 0) | (-0.140, +0.190) [0] | (-0.151, +0.192) [0] | (-0.177, +0.197) [0] | 0.0113 | 0.0378 |
| 2 | bottom-right (3071, 4095) | (-0.160, -0.222) [0] | (-0.171, -0.220) [0] | (-0.197, -0.215) [0] | 0.0113 | 0.0378 |
| 2 | centre (1535.5, 2047.5) | (-0.000, +0.000) [999] | (-0.011, +0.002) [999] | (-0.037, +0.007) [999] | 0.0113 | 0.0377 |
| 3 | top-left (0, 0) | (+1.234, +1.659) [999] | (+1.231, +1.670) [999] | (+1.222, +1.695) [999] | 0.0114 | 0.0379 |
| 3 | top-right (0, 4095) | (+1.336, -1.778) [0] | (+1.332, -1.768) [0] | (+1.324, -1.743) [0] | 0.0112 | 0.0373 |
| 3 | bottom-left (3071, 0) | (-1.209, +1.610) [0] | (-1.213, +1.620) [0] | (-1.222, +1.646) [0] | 0.0114 | 0.0381 |
| 3 | bottom-right (3071, 4095) | (-1.309, -1.758) [999] | (-1.312, -1.747) [999] | (-1.321, -1.722) [999] | 0.0112 | 0.0374 |
| 3 | centre (1535.5, 2047.5) | (-0.000, +0.000) [999] | (-0.004, +0.011) [999] | (-0.012, +0.036) [999] | 0.0113 | 0.0377 |
| 4 | top-left (0, 0) | (+1.025, +1.366) [999] | (+1.033, +1.374) [999] | (+1.050, +1.395) [999] | 0.0114 | 0.0381 |
| 4 | top-right (0, 4095) | (+1.106, -1.456) [999] | (+1.113, -1.447) [999] | (+1.131, -1.428) [999] | 0.0113 | 0.0376 |
| 4 | bottom-left (3071, 0) | (-1.077, +1.418) [999] | (-1.070, +1.427) [999] | (-1.053, +1.447) [999] | 0.0113 | 0.0378 |
| 4 | bottom-right (3071, 4095) | (-1.158, -1.542) [999] | (-1.150, -1.534) [999] | (-1.133, -1.514) [999] | 0.0112 | 0.0373 |
| 4 | centre (1535.5, 2047.5) | (+0.000, +0.000) [999] | (+0.008, +0.009) [999] | (+0.025, +0.029) [999] | 0.0113 | 0.0377 |
| 5 | top-left (0, 0) | (-0.208, -0.295) [0] | (-0.197, -0.297) [0] | (-0.171, -0.302) [0] | 0.0113 | 0.0377 |
| 5 | top-right (0, 4095) | (-0.229, +0.313) [0] | (-0.217, +0.311) [0] | (-0.192, +0.306) [0] | 0.0113 | 0.0377 |
| 5 | bottom-left (3071, 0) | (+0.130, -0.182) [0] | (+0.141, -0.184) [0] | (+0.167, -0.189) [0] | 0.0113 | 0.0378 |
| 5 | bottom-right (3071, 4095) | (+0.150, +0.217) [0] | (+0.161, +0.215) [0] | (+0.187, +0.210) [0] | 0.0113 | 0.0378 |
| 5 | centre (1535.5, 2047.5) | (+0.000, -0.000) [999] | (+0.011, -0.002) [999] | (+0.037, -0.007) [999] | 0.0113 | 0.0377 |
| 6 | top-left (0, 0) | (-1.231, -1.652) [999] | (-1.227, -1.663) [999] | (-1.219, -1.689) [999] | 0.0114 | 0.0380 |
| 6 | top-right (0, 4095) | (-1.333, +1.767) [0] | (-1.330, +1.756) [0] | (-1.321, +1.731) [0] | 0.0112 | 0.0374 |
| 6 | bottom-left (3071, 0) | (+1.206, -1.597) [0] | (+1.210, -1.608) [0] | (+1.218, -1.633) [0] | 0.0115 | 0.0382 |
| 6 | bottom-right (3071, 4095) | (+1.306, +1.752) [999] | (+1.310, +1.741) [999] | (+1.319, +1.716) [999] | 0.0113 | 0.0376 |
| 6 | centre (1535.5, 2047.5) | (+0.000, -0.000) [999] | (+0.004, -0.011) [999] | (+0.012, -0.036) [999] | 0.0113 | 0.0378 |

## 6. Results of steps 1-8 (expected versus measured)

The step 1-7 verification reports were given in the chat sessions and are NOT on disk; every line below was re-measured in this step (source "step 9": describe_simulation.py / check_stacking_geometry.py) or read from validation_summary.json (step 8), pipeline.log or render_report.jsonl. Values quoted from an earlier step without re-measurement are marked "from step N".

| step | check | expected | measured | source |
|---|---|---|---|---|
| 1 | GSD = h p / f | 4.741379310344828 m | 4.741379310344828 m | step 9 (sensor_timing.gsd) |
| 1 | v_g | 7059.2164500563995 m/s | 7059.2164500564 m/s | step 9 (sensor_timing.ground_speed) |
| 1 | dt = GSD / v_g | 0.6716580152896355 ms | 0.6716580152896355 ms | step 9 (formation_distances.json, results.npz) |
| 1 | nominal footprint across x along | 19420.7 x 14565.5 m | 19420.7 x 14565.5 m | step 9 (sensor_timing.footprint_m) |
| 2 | ROE metre table | PLAN_CONTEXT values | max dev 3.5e-05 m | step 9 (roe_hex7_dmax1km.json) |
| 2 | ROE realised at t = 0 | equal to roe_m | max dev 9.2e-09 m | step 9 (elements_to_roe of results.npz) |
| 3 | states_eci shape | [1000, 7, 6] | [1000, 7, 6] | step 9 (results.npz) |
| 3 | diff(times) = dt | rel 1e-12 | max abs dev 6.9e-17 s | validation_summary.json time_grid |
| 3 | chief at t_c vs chief_capture | < 1 mm | 1.86e-06 mm | step 9 (RK4 half step from sample 499) |
| 3 | chief geocentric nadir at t_c hits the Melbourne point | < 1 m | 0.000 m | step 9 |
| 3 | dense output vs RK4 dt/4 | < 1e-6 m | 4.35e-08 m | step 9 |
| 3 | T-N lattice edges | 894.427 +/- 1 m | 894.364 .. 894.490 m | step 9; validation_summary.json formation |
| 3 | 3D neighbour distance | <= 1001 m | max 985.924 m | step 9 |
| 3 | change of any pair distance over the window | < 1 m | 0.1502 m | step 9 |
| 3 | |dr| = |v| dt | rel 1e-6 | max rel err 7.0e-09 | validation_summary.json poses |
| 3 | SSO RAAN drift | 0.9856 deg/day +/- 0.5% | 0.98607 deg/day (+0.05%) | step 9 (1-day J2 RK4) |
| 3 | chief nadir ground speed | report vs v_g = 7059.216 m/s | 7131.558 m/s | step 9 (common_target_ecef_m) |
| 3 | bbox corner margin in the chief frame f0 / f999 | -734 / +387 m (from step 3, prompt_4_per_frame_poses.md:32) | -734.2 / +387.2 m | step 9 |
| 4 | chief footprint across x along, f0 / f999 | 19420.7 x 14565.5 m +/- 0.5% | 19421.0 x 14565.8 / 19420.4 x 14565.4 m | step 9 (cameras.json footprint_local_m) |
| 4 | chief native GSD | 4.741 m +/- 0.1% | 4.741303 .. 4.741447 m | step 9 (poses.jsonl) |
| 4 | chief ground advance per frame | 1.00 .. 1.02 px | 1.01023 .. 1.01027 px; image rows 1.01023 .. 1.01026 | validation_summary.json advance; step 9 (stacking_inputs.npz) |
| 4 | common target -> image centre | < 0.01 px | max 9.9e-11 px (7 sats) | step 9; validation_summary.json target |
| 4 | deputy ground across alignment | >= 1 - 1e-10 | 1 - 4.4e-16 | step 9 (cameras.json) |
| 4 | selected tiles | 570 | 570 | step 9 (cameras.json) |
| 5 | terrain caches | 570 valid .npz | 570 files, 570 selected present | step 9 (data/swarm_terrain) |
| 5 | triangle count | as loaded by the renderer | 13,670,356 (run header 13,670,356) | step 9; render_report.jsonl |
| 6 | Blender projection checks (7000 poses) | corner < 0.1 px, GSD step < 0.002, target < 0.01 px | corner 0.000732 px, pixel step dev 0.000122, target 0.000220 px, alignment 1 - 4.4e-16 | pipeline.log:13 (simulation run) |
| 6 | render-time target error | < 0.01 px | max 2.20e-04 px | validation_summary.json target.logged_max |
| 7 | EEVEE render time per frame | n/a | 2.84 .. 4.07 s mean per satellite | validation_summary.json time.render_s |
| 7 | EEVEE minus Cycles brightness | about 0 (from step 7: about 0.4 DN) | sat 0 f0: +0.405 DN, sat 0 f499: +0.416 DN, sat 0 f500: +0.410 DN, sat 0 f999: +0.406 DN, sat 3 f999: +0.404 DN | step 9 (Cycles PNGs in the simulation run folder, alpha = 255 in both) |
| 8 | PNGs | 5000 valid RGBA 8-bit 4096x3072 | 5000 / 5000 | validation_summary.json png |
| 8 | alpha coverage (alpha > 0.5) | non-zero every frame | 15.0798 .. 15.1262% | validation_summary.json coverage |
| 8 | chief phase correlation, vertical | about 1.01 px | 1.0060 px vs pose prediction 1.0102 | validation_summary.json phase |
| 8 | deputy vs chief image shift | small | up to 0.67 px; predicted from poses on terrain vertices within 0.11 px | validation_summary.json overlap; step 9 (deputy_offsets) |
| 8 | verdict | PASS | PASS WITH NOTES | validation_summary.json verdict |

## 7. Known limitations and deviations

- Terrain is a 2.5 m heightfield of the photomesh upper surface: no facades, overhangs, bridges underside or vertical walls; steep edges become ramps one cell wide. Missing cells are filled from the nearest sampled cell.
- RGB only: pixels are the baked aerial texture colours (with the survey's own sun and shadows) through an emission shader; no illumination model, BRDF, atmosphere, noise, MTF or spectral bands. Any hyperspectral cube built from these frames has 3 channels per satellite.
- Full-frame perspective camera, not a pushbroom: each frame is an instantaneous snapshot ("RGB full-frame perspective approximation of pushbroom imager", cameras.json mode).
- Footprints, ground pixel sizes, distance_m and native_gsd_m are on the local z = 0 tangent plane; the common target is on the WGS84 ellipsoid (z about -0.45 m at the target). The z = 0 plane departs from the ellipsoid by up to 4.3 m at the dataset corners. Terrain heights are AHD used as ellipsoidal height.
- EEVEE is about 0.41 DN brighter than Cycles on average over the opaque pixels of the frames rendered with both engines (mean abs difference about 4.5 DN per channel).
- Chief phase correlation is 0.0042 .. 0.0043 px below the pose prediction, constant over the window (estimator bias on this scene, not a timing error; step 8).
- Deputy images are shifted up to 0.67 px relative to the chief (step 8 phase correlation). The poses predict the same shifts within 0.11 px from the mean deputy-minus-chief position of terrain vertices: the shift is the scale difference (distance differs by up to 415.4 m from the chief, i.e. up to 2.22 px at the image corners on z = 0, parallax table) acting on a scene that is off-centre and moves across the frame. It is geometry, not an error; use chief_to_satellite_pixel to co-register.
- Satellites 5 and 6 are propagated and have poses (cameras.json) but were not rendered; stacking_geometry reads their poses from cameras.json.
- The chief ground advance is 1.0102 .. 1.0103 px per frame, not 1: dt is defined with the nominal 500 km, non-rotating spherical v_g, while the real ground motion includes Earth rotation and the actual radius (chief nadir speed 7131.56 m/s on WGS84). Over 999 frames the scene moves 1009.2 rows. The deputies advance at 0.99917 .. 1.00083 times the chief rate in their own pixels (own GSD).
- In the first 9 frames the dataset crosses the top image edge (alpha > 0 in row 0): part of the northern tiles is not imaged in those frames. Only 1003..1954 (952) rows contain the scene in every frame of the chief.
- The chief boresight is the GEOCENTRIC nadir; it is 0.1862 deg from the ellipsoid normal at the target. The chief is placed 500 km along the geocentric radial (ellipsoidal height 499997.55 m).
- Earth orientation: fixed pole, GMST with UTC ~ UT1, no precession/nutation/polar motion; J2 only, no drag, no third bodies, no SRP (irrelevant over 0.67 s, but the absolute epoch geometry is approximate).
- ROE are osculating and applied directly at t = 0 (no mean/osculating map).
- PLAN_CONTEXT disagreements found: ground track azimuth is -12.24 deg from north at t_c (PLAN estimate "about 9 deg"); the run integrator for the 1000-sample propagation uses rtol 1.0e-10, atol 1.0e-09 (config default), tighter values only for the chief back-propagation; the osculating a at t = 0 is 6870133.7 m, not a_chief = 6870140.5 m (short-period J2 variation of the osculating a; a_chief is exact at t_c only); the export sample of formation_distances.json is 500, not t_c = 499.5 dt. Everything else checked here matches.

