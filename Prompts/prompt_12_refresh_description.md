Read Prompts/PLAN_CONTEXT.md (decisions D1-D5 of step 10). The full run has been redone with the
corrected timing: simulation output/orbits_melbourne/20261109T180310Z_run_v2/simulation, renders
of satellites 0-4 in output/orbits_melbourne/eevee_full_1000 (5000 PNGs), step 8 already re-run:
validation_report.md says PASS WITH NOTES, chief advance 1.00000 px/frame, phase correlation
1.0000 px. The old run 20261106T080712Z_run_IxoFnH was deleted; the scripts already point to
the new one (5 one-line path edits, uncommitted).

Step 12 only: remove the text that still describes the OLD timing, then regenerate the step 9
outputs. READ-ONLY on renders, poses and simulation. No commits.

A. Stale text to fix (wording and expected values only; no logic change except where stated)
1. scripts/validate_run.py
   - :317 failure message "expected about 1.01" -> expected about 1.00 (the 0.9 < |v| < 1.1 test
     stays as it is).
   - :404-408 the advance note: today it always says "Earth rotation + real ground speed 0.05%
     above v_g: expected, dt kept on purpose". Make it depend on cameras.json frame_period_mode:
     ground -> "chief ground advance X px/frame, dt from the true ground speed (frame_period_mode
     ground)"; nominal -> keep the old explanation. The 1.00-1.03 range check at :402: change to
     |mean - 1| < 1e-3 for ground, keep 1.00-1.03 for nominal.
   - :409-412 the phase-correlation note: say "above" or "below" from the sign and drop the
     "estimator bias" sentence when |dev| < 1e-3 px.
   - :520 "Expected vertical about 1.01 px" -> mode-dependent as above.
2. scripts/describe_simulation.py
   - :315 dt definition row: for ground mode describe dt = 1 / rate_rows from the pixel-rate
     definition, with dt_closed_form, rho, v_t, gsd_true from cameras.json, file:line of
     orbits/src/orbits/burst_geometry.py and melbourne_overpass.py. Keep the nominal row as a
     second row "nominal (not used)".
   - section 1: add a_chief, altitude reference (mean radius R_avg = (2a+b)/3 WGS84), height above
     the target, and the SSO inclination at the new a.
   - :630 expected advance "1.00 .. 1.02 px" -> "1 +/- 2e-5 px per frame (slant range changes
     ~15 m over the window)".
   - :644 expected phase correlation "about 1.01 px" -> "about 1.00 px".
   - :664-666 phase-correlation limitation: rewrite from the measured numbers (no bias claim if
     |dev| < 1e-3 px).
   - the last limitation (chief advance 1.0102, "dt is defined with the nominal 500 km ...") ->
     remove for ground mode; replace with one line: one shared clock, chief exactly 1 px/frame,
     deputies 0.9992-1.0008 in their own pixels (measure it).
   - any other place that states 1.01 / 1.0102 / "nominal 500 km" as the current behaviour.
3. scripts/stacking_geometry.py:40 docstring "+1.0102 rows per frame for the chief" -> current
   value, or drop the number.
4. grep -rn "1\.01\b\|1\.0102\|nominal 500\|IxoFnH" scripts/ orbits/scripts orbits/src and fix
   what describes the current run (leave validate_step10.py: it compares with the old run on
   purpose, but make its OLD_SIM a CLI option defaulting to
   output/orbits_melbourne/step10_validation/regression_old/simulation, which reproduces the old
   run bit for bit).

B. Regenerate (same commands as step 9, on the new run)
- export_stacking_inputs.py -> eevee_full_1000/stacking_inputs.npz
- check_stacking_geometry.py (real-image convention check)
- validate_run.py again (the notes must now read correctly)
- describe_simulation.py -> eevee_full_1000/SIMULATION_DESCRIPTION.md

C. Checks (report the numbers)
- stacking_inputs.npz: chief image_motion_rows_px 0.99998 .. 1.00002, cols |.| < 1e-5.
- check_stacking_geometry: "correct" variant has the lowest error on every satellite/frame, the
  bilinear sweep minimum at (0, 0), round trip < 1e-9 px.
- SIMULATION_DESCRIPTION.md: grep it for 1.01, 1.0102, "nominal 500", IxoFnH: no hit describing the
  current run. dt = 0.6661251071 ms, a_chief = 6871008.7714 m, i = 97.374489 deg appear.
- git diff --stat: list the changed files.
STOP and report.
