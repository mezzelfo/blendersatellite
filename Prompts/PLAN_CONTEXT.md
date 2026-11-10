# Project context (read fully before any step)

Goal: Blender-rendered burst of 1000 frames of a 7-satellite swarm over Melbourne.
Every satellite renders a full HyperScape100 frame at every time sample. The formation
is parametrized with relative orbital elements (ROE), converted to Cartesian ECI and
propagated with the existing orbits J2 propagator. Pointing law is the existing one
(chief geocentric nadir; each deputy targets the chief's WGS84 ground target; deputy roll
aligns the ground across-track direction with the chief's), with the target RECOMPUTED AT
EVERY FRAME.

## Fixed parameters
- Sensor (HyperScape100): focal length f = 0.580 m, pixel pitch p = 5.5e-6 m,
  4096 columns (across-track) x 3072 rows (along-track) = 22.528 x 16.896 mm,
  FOV 2.2252 deg x 1.6690 deg.
- Altitude h = 500e3 m over the ellipsoid at the Melbourne point already used by the
  pipeline (EPSG:7855 x = 320877.49072265625, y = 5812809.471992493).
- GSD = h*p/f = 4.741379310344828 m.
- Ground speed v_g = sqrt(mu/(Re+h)) * Re/(Re+h), with mu = 3.986004418e14 and
  Re = 6378137 (orbits.physics.Earth defaults) -> 7059.2164500563995 m/s.
- Frame period dt = GSD / v_g = 0.6716580152896355 ms. Time grid t_k = k*dt, k = 0..999
  (N = 1000, window 0.671 s, ground advance 4.741 km).
- Nominal chief footprint: 19.4207 km across x 14.5655 km along.
- Orbit: circular (e = 0), sun-synchronous. Inclination = orbits.elements.sso_inclination(a_chief),
  replacing the fixed 97.4 deg with sso=False found in the code (SSO value: 97.372 deg for
  a = 6 870 140.5 m, 97.402 deg for 6 878 137 m; report the value actually used). Ascending
  pass over Melbourne, as in the code.
- Chief radius: the code places the chief 500 km along the geocentric radial above the
  Melbourne point, so a_chief = 6 870 140.5 m (ellipsoidal height 499 997.55 m, kept as is).
  The nominal 6 878 137 m is used only for the sensor timing and for the check table below.
  The formation geometry is defined in metres: dimensionless ROE = (a*ROE in metres) / a_chief
  of the code, so the lattice distances stay 894.427 m. The real chief ground speed is about
  0.05% above v_g (audit); dt stays as defined above and the resulting drift is only reported.
- Window is centered on the chief pass over the Melbourne point: the "capture" instant is
  t_c = 499.5*dt. The orbits API needs times_s starting at 0, so the chief is
  back-propagated by 499.5*dt from its state at t_c, and the ROE are applied at t = 0
  (osculating ROE applied directly, no mean-osculating conversion; for a 0.67 s window
  the difference is below 0.5 m).
- With Earth rotation the chief advances about 1.01 pixel per frame on the ground
  (expected, not exactly 1.000). dt stays the formula above on purpose.

## Satellites and formation
7 satellites: satellite 0 = chief (a real satellite with a camera) + 6 deputies.
The existing code handles 5 (TRAIN_SLOTS with 5 entries, swarm.num_deputies: 4, --satellites
default 0,1,2,3,4, docstring "five-spacecraft"); in roe mode it must handle 7
(num_deputies = 6). Propagation always includes all 7. Rendering accepts any subset through
--satellites (for example 0,1,2,3,4 = chief + 4 deputies, 5000 renders instead of 7000).
Formation = "high-density" triangle lattice from the author's notebook
(sep = 1/sqrt(5) km, di_ratio = 2, de_phase = 0, nominal a = 6878.137 km).
ROE definition (quasi-nonsingular, dimensionless), alpha = (a, u, ex, ey, i, RAAN),
u = argp + M, ex = e cos(argp), ey = e sin(argp):
  da   = (a_d - a_c)/a_c
  dlam = (u_d - u_c) + (RAAN_d - RAAN_c) cos(i_c)
  dex  = ex_d - ex_c,  dey = ey_d - ey_c
  dix  = i_d - i_c
  diy  = (RAAN_d - RAAN_c) sin(i_c)
Linear reference map to the chief RTN frame (u = chief argument of latitude):
  dr = a (da - dex cos u - dey sin u)
  dt = a (dlam + 2 (dex sin u - dey cos u))
  dn = a (dix sin u - diy cos u)
Generator: de_sep = sep/a; lattice points (W,X) for the 6 deputies:
  columns (1,0), (0,1), (-1,1), (-1,0), (0,-1), (1,-1);
  basis_e = [[cos(phi), cos(phi+60deg)], [sin(phi), sin(phi+60deg)]], phi = de_phase = 0;
  (dex, dey) = de_sep * basis_e @ WX;
  basis_i = di_ratio * R90 @ basis_e, R90 = [[0,-1],[1,0]];
  (dix, diy) = de_sep * basis_i @ WX;   da = dlam = 0 for all deputies.
Check values, a*ROE in metres (these metre values are the specification):
  dep  dex_m      dey_m      dix_m       diy_m
  1    447.2136    0          0           894.4272
  2    223.6068    387.2983  -774.5967    447.2136
  3   -223.6068    387.2983  -774.5967   -447.2136
  4   -447.2136    0          0          -894.4272
  5   -223.6068   -387.2983   774.5967   -447.2136
  6    223.6068   -387.2983   774.5967    447.2136
Expected geometry: in the T-N plane the 12 lattice edges (6 outer ring + 6 chief-to-
deputy) are 894.427 m long at any u (rigid lattice rotating with u); 3D distance
between lattice neighbours is <= 1000 m (R = N/2).

## Dataset
City of Melbourne photomesh OBJ, 570 ZIP tiles (~106 GB), bounding box 8.4 x 8.4 km
(EPSG:7855 x 314857.49..323257.49, y 5808749.47..5817149.47). A full frame is far larger
than the dataset: the scene fills at most ~25% of a frame, the rest is transparent.
The window is centered on the Melbourne point, which is 1.8 km east of the bbox centre
(319057.49, 5812949.47). The ground track is tilted about 9 deg from north (estimate), so the
rotated bbox may leave the frame by a few tens of metres at the first or last frames. Step 3
measures this (minimum margin of the bbox corners inside the chief frame at samples 0, 500,
999). Missing geometry stays transparent. Terrain is the existing 2.5 m upper-surface grid.

## Output
PNG, RGBA, 8 bit, 4096 x 3072. One folder per satellite:
renders/satellite_00 (chief) ... satellite_06, files frame_0000.png ... frame_0999.png,
plus a poses file per satellite. 7000 renders in total (5000 for satellites 0-4 only).

## Working rules
- Step 0 audited the working tree on osiride: submodule orbits at 1c76ca5 plus uncommitted
  camera_geometry.py, train.py and the --pointing/--formation/--train-spacing-m options. The
  user commits this baseline himself before step 1. Verify against the real files and report
  any difference before editing.
- Minimal diffs. No refactors, no formatting changes outside touched lines.
- Code comments and docstrings in English.
- Do not commit and do not push. Leave changes in the working tree for review.
- Everything runs on the cluster (Linux, CUDA, Blender 5.x, conda env "orbits").
- Each step ends with a VERIFICATION REPORT: what was run, measured numbers versus
  expected ones, deviations, open problems. Then STOP and wait. Never start the next step.

## Step 10 decisions (supersede "Chief radius" and the dt formula above for new runs)
- D1. a_chief = R_avg + 500 km, R_avg = (2 a + b)/3 = 6371008.7714 m (WGS84 a = 6378137.0,
  b = 6356752.314245) -> a_chief = 6871008.7714 m, on the geocentric radial of the Melbourne
  point (about 500868 m above it). Constant orbits.physics.WGS84_MEAN_RADIUS (Earth().mean_radius).
- D2. J2, Gravity, sso_inclination keep the equatorial radius Earth().radius = 6378137 m.
- D3. Inclination = sso_inclination(a_chief) at the new a_chief (about 97.3748 deg).
- D4. dt from the true geometry at t_c: the common target (fixed to the Earth) moves exactly
  one chief row per frame (orbits.burst_geometry.pixel_rate_frame_period, central difference
  +/- 10 ms); cross-check closed_form_frame_period = rho p / f / v_t.
- D5. One clock for the swarm: the chief advances 1 row/frame; deputies 1 +/- ~1e-3 rows in
  their own pixels (reported, not corrected).
- Defaults of melbourne_overpass.py: --altitude-reference mean (local = old |origin| + 500 km),
  --frame-period ground (nominal = old sensor_timing.frame_period(500 km)).

## Stacking into per-satellite cubes (steps 13-16, decided 10 November 2026)

Goal: reassemble the rendered frames of each satellite into a cube cube[g, c, b] that mimics a
linear-variable-filter acquisition (one detector row per band). Renders are RGB, so every band
holds the same ground in R, G, B: the cube tests geometry and registration, not spectra.

Decisions (by the user, do not change them)
S1. Satellites 0-4 (the rendered ones). Inputs: renders/satellite_SS/frame_KKKK.png,
    stacking_inputs.npz, scripts/stacking_geometry.py (poses.jsonl). Runs on osiride, conda env orbits.
S2. Band layouts, both centred on the optical axis (row 1535.5), passed by name or as a row list:
    - compact: 32 bands, step 1 row, rows 1520..1551, span 31. Most complete along-track lines:
      chief g = 552..1520, 969 lines, 4.60 km. This is the production cube.
    - diagnostic: 8 bands, step 127 rows, rows 1091..1980, span 889, chief g = 981..1091, 111 lines.
      Purpose: make the deputy band-to-band drift visible (span x (s - 1), up to about 0.73 px for sat 3).
    Complete lines with integer stepping: N - span (N = 1000 frames).
S3. Cube index: g = row of the ground line in the satellite's OWN frame 0 (integer grid). Columns =
    every column with scene in that satellite (alpha_col_any union over frames 0..999 of
    stacking_inputs.npz; sat 0 661..2570, sat 1 660..2570, sat 2 661..2570, sats 3-4 662..2569).
    Data RGBA uint8 for every satellite; channel 3 (alpha) is the validity mask.
S4. Bands are co-registered only within each satellite. No registration between satellites in
    steps 13-16 (scale up to 1-2 px and parallax are a later step).
S5. Two modes for every satellite, chief included:
    - none: integer stepping, sample (g, b) = PNG pixel (row r_b, col c) of frame k = r_b - g.
      No resampling, so the deputy drift stays in the cube and can be seen.
    - linear: fractional frame k* where the ground line g crosses row r_b (geometric map S6),
      RGBA interpolated linearly between frames floor(k*) and floor(k*) + 1 at row r_b, rounded
      with np.rint to uint8 (quantisation <= 0.5 DN, alpha treated like the colours).
S6. Geometric map: x_s(k, r, c) = PNG row in frame 0 of satellite s of the z = 0 ground point seen
    at (r, c) in frame k = pixel_from_ground(s, 0, ground_from_pixel(s, k, r, c))[0]. Reference
    plane z = 0; within-satellite parallax of the terrain is estimated and reported, not corrected.
S7. Outputs only under output/orbits_melbourne/eevee_full_1000/cubes/<layout>_<mode>/:
    cube_satSS.npz (cube [G, C, B, 4] uint8, g, cols, band_rows, frame index per (g, b): int for
    none, float k* for linear, metadata json). Report the expected size before writing
    (compact cube of the chief about 237 MB; 12 GB free on osiride).
