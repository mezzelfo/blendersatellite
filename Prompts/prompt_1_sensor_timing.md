Read PLAN_CONTEXT.md. Implement step 1 only.

Create orbits/src/orbits/sensor_timing.py (numpy, float64, no torch) with:
- a HyperScape100 spec (f, p, columns, rows) as a frozen dataclass;
- gsd(h, spec), ground_speed(h, earth) using orbits.physics.Earth as the only source of
  mu and Re, frame_period(h, spec, earth), time_grid(n_frames, h, spec, earth),
  footprint_m(h, spec) returning (across, along);
- docstrings stating the formula and units (SI).
Create orbits/tests/test_sensor_timing.py. Touch no other file.

Validation (all must pass, report the measured values):
- gsd(500e3) == 4.741379310344828 (rel tol 1e-12);
- ground_speed == 7059.2164500563995 (rel tol 1e-12);
- frame_period == 0.6716580152896355e-3 (rel tol 1e-12);
- frame_period * ground_speed / gsd == 1 (rel tol 1e-12);
- time_grid(1000): length 1000, t[0] == 0, strictly increasing, np.diff == dt (rel tol 1e-12),
  dtype float64;
- footprint == (19420.69, 14565.52) m (abs tol 0.01);
- the existing orbits tests still pass.
STOP and report.
