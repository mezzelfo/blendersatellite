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
- Window is centered on the chief pass over the Melbourne point: the "capture" instant is
  t_c = 499.5*dt. The orbits API needs times_s starting at 0, so the chief is
  back-propagated by 499.5*dt from its state at t_c, and the ROE are applied at t = 0
  (osculating ROE applied directly, no mean-osculating conversion; for a 0.67 s window
  the difference is below 0.5 m).
- With Earth rotation the chief advances about 1.01 pixel per frame on the ground
  (expected, not exactly 1.000). dt stays the formula above on purpose.

## Satellites and formation
7 satellites: satellite 0 = chief (a real satellite with a camera) + 6 deputies.
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
Check values, a*ROE in metres with a = 6878137 m:
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
With the centered window the whole bounding box should lie inside every frame
(along-track margin ~0.5 km). Terrain is the existing 2.5 m upper-surface grid.

## Output
PNG, RGBA, 8 bit, 4096 x 3072. One folder per satellite:
renders/satellite_00 (chief) ... satellite_06, files frame_0000.png ... frame_0999.png,
plus a poses file per satellite. 7000 renders in total.

## Working rules
- The description of existing code below and in the prompts comes from reading an OLDER
  local checkout (orbits at commit 1c76ca5, without camera_geometry.py and without the
  --pointing/--formation/--train-spacing-m options). The cluster version may differ.
  Verify against the real files first and report any difference before editing.
- Minimal diffs. No refactors, no formatting changes outside touched lines.
- Code comments and docstrings in English.
- Do not commit and do not push. Leave changes in the working tree for review.
- Everything runs on the cluster (Linux, CUDA, Blender 5.x, conda env "orbits").
- Each step ends with a VERIFICATION REPORT: what was run, measured numbers versus
  expected ones, deviations, open problems. Then STOP and wait. Never start the next step.
