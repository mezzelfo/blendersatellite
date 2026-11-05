Read PLAN_CONTEXT.md. Steps 1 and 2 are done. Implement step 3 only.

Changes:
a) orbits/src/orbits/cli.py: add an optional keyword y0_override (Tensor [S,6] or [B,S,6],
   default None) to run(). When given it replaces the result of construct_states; behavior
   with None must be identical to today. Nothing else changes in cli.py.
b) orbits/scripts/melbourne_overpass.py: add --formation roe, --roe-file (default the JSON
   of step 2), --n-frames (default 1000). In this mode:
   - dt and times_s come from sensor_timing (times start at 0, k*dt, k = 0..N-1);
   - the chief state at t_c = (N-1)/2*dt is the existing chief_capture; back-propagate it
     by (N-1)/2*dt (same Gravity, same tight tolerances as today) to get the chief state at
     t = 0, then elements -> ROE applied there via roe_to_eci -> y0_override;
   - simulation.samples = N, simulation.times_s set explicitly, device as today;
   - the camera/footprint export keeps the EXISTING code path but only for sample index 500
     (transition only; step 4 replaces it), so the pipeline stays runnable.
   - orbit: circular sun-synchronous, i = orbits.elements.sso_inclination(a_chief) instead of
     the fixed 97.4 deg with sso=False, in this mode only; print the value used;
   - swarm size 7 (num_deputies = 6) in this mode; the 5-satellite train mode stays as is.
   Keep the existing formation modes working.

Validation (CPU is acceptable; report numbers):
- all existing orbits tests pass; with y0_override=None the saved npz is bit-identical to
  the pre-change one for the smoke config;
- states_eci has shape [1000, 7, 6]; np.diff(times) equals dt (rel tol 1e-12);
- chief position at t_c (integrate separately with rtol 1e-12) differs from chief_capture
  by less than 1 mm; and the nadir ray of the chief at t_c hits the Melbourne point within 1 m;
- dense-output accuracy: compare the 1000-sample dopri5 trajectory against an independent
  fixed-step RK4 reference (step dt/4) for all satellites; report the max position error
  (target < 1e-6 m; if larger, report and propose a fix, do not apply it);
- formation at t = 0 and at sample 500: T-N lattice edges 894.427 m +/- 1 m, 3D neighbour
  distance <= 1000 m + 1 m; the change of any pair distance over the whole window < 1 m;
- consecutive chief positions differ by |v|*dt (rel tol 1e-6);
- SSO: the RAAN drift of the chief from the propagation equals 0.9856 deg/day (within 0.5%),
  and the inclination used is the one printed;
- report the chief sub-point ground speed (m/s) versus 7059.216, and the minimum margin of
  the Melbourne bbox corners inside the nominal chief frame at samples 0, 500 and 999
  (negative means the bbox leaves the frame; give the amount in metres).
STOP and report.
