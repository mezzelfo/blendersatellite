Read PLAN_CONTEXT.md. The full run has finished. Step 8 only: final validation.
Write and run scripts/validate_run.py, read-only on the outputs, and write
validation_report.md in the run folder.

Checks (report numbers, flag every failure with the frame and satellite):
- exactly 5 satellite folders with 1000 frames each (frame_0000..0999), no gaps or duplicates;
- every PNG opens, is RGBA, 8 bit, 4096x3072, and not truncated (file size > 0, decode ok);
- alpha coverage per satellite: min / mean / max over frames; list frames with coverage 0;
- poses: consecutive position differences equal |v|*dt (rel tol 1e-6) for every satellite;
  common-target error < 0.01 px for all frames; ground advance per frame of the chief:
  min/mean/max in pixels;
- formation: at frames 0, 499, 999 the T-N lattice edges are 894.427 m +/- 1 m and the 3D
  neighbour distance <= 1000 m + 1 m;
- phase correlation of the chief between frames 0/1, 499/500, 998/999: vertical shift
  in pixels (expected about 1.01);
- sampling every 100th frame: the same frame index across the 7 satellites shows the same
  ground area (report the overlap of footprints);
- logs: no errors or warnings in the job logs; total disk used; total render time.
Final verdict at the top: PASS, or PASS WITH NOTES, or FAIL with the list of failures.
