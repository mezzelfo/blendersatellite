Read PLAN_CONTEXT.md. Steps 1-5 are done. Step 6 only: adapt render_swarm.py.

Changes:
- read resolution, sensor_width_mm, sensor_height_mm and native GSD from each camera
  record and the per-epoch cameras.json; remove the hard-coded 512 and 5 m (corner
  reprojection check, pixel_step check, render resolution);
- cameras.json is schema_version 2 (step 4): list of per-frame "epochs", each with its own
  common target and chief across-track direction, and no sensor_crop_mm field. render_swarm.py
  currently expects the old top-level "cameras" list and must be adapted to this schema;
  the independent recomputation of the chief-target uses the per-epoch values;
- scene and terrain loaded once; loop over frames and satellites;
- output layout: <output>/satellite_NN/frame_KKKK.png (RGBA 8 bit, transparent film) and
  <output>/satellite_NN/poses.jsonl (one line per frame, appended);
- new options: --frames START:STOP (default 0:1000), --skip-existing (skip a frame when
  its PNG exists and is a valid file), --satellites takes any subset (default 0,1,2,3,4,5,6), keep --check-only;
- replace the single render_report.json with a per-frame JSONL report; keep the
  independent recomputation of the chief-target and all existing validations, now per frame;
- measure the per-frame cost of the nvidia-smi snapshot and of the PNG reload used for the
  coverage fraction. If together they exceed 2% of the render time, compute coverage
  from the in-memory render result or every 50th frame instead, and say so.
Do not change the Cycles settings (samples, denoising, GPU selection).
Step 5 is done: all 570 terrain caches exist in data/swarm_terrain. Generate the cameras.json
for the render with run_orbits_melbourne.sh using FORMATION=roe (its default is train), 1000
frames, 7 satellites, and keep the run directory outside /tmp. At the start, report the
Blender version used (the repo pins bpy==5.0.1, the audit found the snap Blender 5.2.2), and
the Cycles devices it detects (names, backend CUDA/OPTIX, compute capability: the GPUs are
TITAN Xp, Pascal, no RT cores). If any API call fails or is deprecated in that Blender,
report it before changing anything.

Validation (report numbers):
- --check-only passes for all 7 x 1000 poses;
- render frames 0, 499 and 999 for satellites 0 and 3 only; every PNG is RGBA, 8 bit,
  4096x3072 (verify with PIL), alpha coverage reported (expected about 15.8% at mid-burst, 0 < coverage <= 25%);
- rerun the same command with --skip-existing: nothing is rendered again;
- phase correlation between frame 499 and 500 of the chief (grayscale, alpha as mask):
  report the shift along the image vertical axis in pixels (expected 1.0102 +/- 0.02)
  and across (expected ~0, accept < 0.1; step 4 measured -3e-8 px in the exported matrices);
- the per-frame validations (target error, corner reprojection, pixel step) all pass;
- existing tests pass.
STOP and report.
