Read PLAN_CONTEXT.md and output/orbits_melbourne/eevee_full_1000/SIMULATION_DESCRIPTION.md.
Steps 1-9 are done. Step 10 only: (1) define the chief semi-major axis from the MEAN Earth
radius, (2) make the burst frame period such that the ground advances exactly ONE chief pixel
per frame, computed from the true geometry at the latitude of the target. Code changes plus a
short validation. No rendering of the full run, no deletion of existing outputs (that is step 11).

Diagnosis (already verified, confirm only with numbers)
- melbourne_overpass.py:117,126: a_chief = |Melbourne ECEF| + 500 km = 6870140.55 m.
- melbourne_overpass.py:130-131: dt = sensor_timing.frame_period(500e3) = GSD / v_g with
  GSD = h p / f, h = 500 km, v_g = sqrt(mu/(Re+h)) Re/(Re+h), Re = 6378137 m, Earth not rotating.
  dt = 0.6716580 ms.
- The real ECEF-relative speed of the common target is 7131.56 m/s (Earth rotation: the SSO is
  retrograde, surface speed 367.9 m/s eastward at -37.63 deg geocentric). The ground advances
  7131.56 / 7059.22 = 1.0102 chief rows per frame (stacking_inputs.npz image_motion_rows_px).

Decisions (by the user, do not change them)
D1. Semi-major axis: a_chief = R_avg + 500 km, R_avg = (2 a_WGS84 + b_WGS84) / 3 with
    a_WGS84 = 6378137.0 m, b_WGS84 = 6356752.314245 m, so R_avg = 6371008.7714 m and
    a_chief = 6871008.7714 m. The chief stays on the geocentric radial of the Melbourne point
    (its geocentric nadir still hits the Melbourne origin); only |r_ecef| changes. Its height
    above the Melbourne point becomes a_chief - |origin| = about 500868 m.
D2. J2 and every gravity / SSO formula keep the EQUATORIAL radius (Earth().radius = 6378137 m):
    J2 is defined with it. Do not use R_avg in Gravity, sso_inclination or anywhere in physics.
    Add R_avg as a separate, documented constant (e.g. Earth.mean_radius or a WGS84 helper);
    do not change Earth.radius.
D3. sso_inclination is evaluated at the new a_chief (expected about 97.3748 deg).
D4. Frame period from the true geometry at t_c: GSD and ground speed come from the real chief
    position and the real target on WGS84, not from the nominal 500 km.
D5. One clock for the whole swarm: dt makes the CHIEF advance exactly 1 row per frame. Deputies
    advance 1 +/- ~1e-3 rows in their own pixels (their own scale); report it, do not correct it.

Rules
- English comments and docstrings. numpy float64 for timing. No commits.
- Keep sensor_timing.frame_period / ground_speed / gsd / time_grid and their tests unchanged
  (they are the "nominal" model). Add new functions.
- Do not modify or delete anything under output/. New run folders go under
  output/orbits_melbourne/step10_validation/.
- Every number in the report is measured by code you run.

A. Mean radius and chief placement
1. Add the mean radius constant (D2) and use it in melbourne_overpass.py:117 for the chief
   radius: r_ecef = unit(origin) * (R_avg + 500e3). Make the reference selectable with a flag
   --altitude-reference {mean,local}, default mean (local = old behaviour |origin| + 500 km).
2. Report: a_chief, height above the Melbourne point, SSO inclination.

B. True frame period (new functions in orbits/src/orbits/sensor_timing.py or a small new module)
1. pixel_rate_frame_period(...): the definition used by the pipeline.
   Take chief_capture (state at t_c built in melbourne_overpass.py). Propagate it to t_c - delta
   and t_c + delta (delta = 1e-2 s) with Gravity() and rtol 1e-11, atol 1e-10. Build the chief
   camera at the three instants with the SAME code used for the poses (frame_cameras, ECEF from
   GMST at each instant). Take the common target at t_c (ECEF, fixed to the Earth) and project it
   into the chief camera at t_c - delta and t_c + delta with the same pinhole model as
   stacking_geometry.pixel_from_ground (f = 580 mm, sensor 22.528 x 16.896 mm, row from the top).
   rate_rows = (row(t_c + delta) - row(t_c - delta)) / (2 delta)   [rows/s]
   rate_cols the same for columns (must be ~0: image-up follows the ECEF-relative velocity).
   dt = 1 / rate_rows.
   Refactor only as much as needed to call frame_cameras and the projection from here; their
   results must not change.
2. closed_form_frame_period(...): independent cross-check.
   rho = |chief position - common target| at t_c (ECEF); GSD_true = rho p / f;
   v_t = speed of the common target in ECEF by central difference over the same three instants;
   dt_cf = GSD_true / v_t.
   Expected (spherical estimate, the ellipsoid changes the 4th-5th digit): rho ~ 500868 m,
   GSD_true ~ 4.7496 m, v_t ~ 7130.2 m/s, dt ~ 0.66612 ms. Required: |dt - dt_cf| / dt < 1e-5.

C. melbourne_overpass.py
- New flag --frame-period {nominal,ground}, default ground. nominal reproduces the old dt.
- With ground: dt from B.1, computed BEFORE the back-propagation (it only needs chief_capture),
  times_s = arange(n_frames) * dt, capture_offset_s = (n_frames - 1) / 2 * dt.
- Store in formation_distances.json and cameras.json: frame_period_s, frame_period_mode,
  altitude_reference, mean_radius_m, a_chief_m, height_above_target_m, dt_nominal_s,
  dt_closed_form_s, rate_rows_per_s, rate_cols_per_s, v_target_m_s, rho_m, gsd_true_m.
- Update PLAN_CONTEXT.md with D1-D5 and the new defaults (short section, no other edits).

D. Tests (new file orbits/tests/test_true_frame_period.py)
- Non-rotating Earth (GMST rate 0) and target at the equator: dt equals
  sensor_timing.frame_period(h) for the same radius within 1e-5 relative.
- Melbourne case: |dt - dt_cf| / dt < 1e-5; |rate_cols| * dt < 1e-6 px.
- Mean radius constant == (2*6378137.0 + 6356752.314245)/3 (rel 1e-15); Earth().radius unchanged.
- The existing orbits tests still pass, unchanged.

E. Short validation (poses only, then 2 chief frames)
1. Run melbourne_overpass.py with the arguments of 20261106T080712Z_run_IxoFnH (read them from
   its config.yaml / pipeline.log) and the new defaults, --n-frames 1000, into
   output/orbits_melbourne/step10_validation/.
2. On the new poses (stacking_geometry with set_paths on the new run):
   - chief advance per frame = 1.000000 +/- 1e-5 rows on every frame, columns |.| < 1e-5 px;
   - cumulative chief advance over 999 frames = 999.00 +/- 0.01 rows;
   - if the first check fails, apply ONE correction dt <- dt / mean_advance, re-run, report both;
   - deputies: advance range in their own pixels (D5);
   - common target -> image centre < 0.01 px; formation T-N edges 894.427 +/- 1 m;
     all 570 tiles still selected; chief native GSD (expected ~4.7496 m).
3. Render ONLY chief frames 0 and 50 with EEVEE and the step 6/7 settings into
   output/orbits_melbourne/step10_validation/renders_check/, and measure the vertical shift by
   phase correlation as validate_run.py does. Expected 50 rows (step 8 had an estimator bias of
   about -0.004 px/frame: report it the same way).

Report: table "quantity | old run (eevee_full_1000) | new run | expected" for a_chief, height
above target, inclination, GSD, dt, v_t, rho, rate_rows, rate_cols, chief advance min/max,
cumulative advance over 999 frames, deputy advance range, phase correlation 0 -> 50.
List every file changed or created. STOP and report.
