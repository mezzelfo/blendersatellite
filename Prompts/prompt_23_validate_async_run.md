Step 23: validate the run made with
  ACQUISITION=hex7_async_10deg.json RENDER_MODE=raw RAW_KEEP_FRAMES=1 bash run_orbits_melbourne.sh --engine EEVEE
RUN = the newest output/orbits_melbourne/*_run_* folder (check that its pipeline.log shows that
acquisition, walls ON and EEVEE; if not, STOP and ask). Read PLAN_CONTEXT.md, README.md and
Prompts/report_21.md (acquisition checks) and Prompts/report_22c.md.

Rules: read-only on RUN; new files only under RUN/validation/. No commits, no deletion, no download.
Reuse orbits/scripts/validate_acquisition.py, scripts/validate_run.py and scripts/check_stacking_geometry.py
where they accept this run (read their arguments); otherwise write the equivalent check under
scripts/ and say so. Every number in the report is measured by code you run.

A. Completeness
1. pipeline.log ends with "Completed run", no error or traceback; terrain line says walls ON.
2. 7 satellites x 1000 frames: every PNG present and valid (4096 x 3072, RGBA, 8 bit); render reports
   complete; peak GPU memory per render job.
3. raw/: sat_SS.npy shapes and dtype, bands.json and sat_SS.json consistent, progress.json complete;
   10000 random (row, frame, column) samples per satellite equal to the PNG pixel.
4. terrain_render: 570 tiles, cache_frame.json with walls and threshold 2 m.

B. Poses and geometry (per satellite and role: nadir 0, 2, 5; fore 1, 4; aft 3, 6)
1. validate_acquisition.py on RUN/simulation: the report-21 table (time window centre, lead,
   incidence, off-nadir, range, along GSD, rows/frame, column drift) and its checks A, D, E, F.
   Expected: incidence about 0 deg for nadir roles and 10 deg for fore/aft, fore/aft windows centred
   about 11-12 s before/after the chief's, range about 507 km and GSD about 4.81 / 4.88 m at 10 deg.
2. poses.jsonl written by Blender against cameras.json: max position and rotation difference.
3. Projection check on the renders (as in check_stacking_geometry.py): project terrain vertices of
   RUN/terrain_render with each satellite's pose at frames 0, 500, 999, read the PNG; median |dRGB|
   and best sub-pixel offset (expected about 0, 0). Walls: include wall vertices as a separate row.
4. Ground motion on the images: per satellite, integer and sub-pixel shift between frame k and k+50
   (k = 0, 475, 949); expected about +50 rows and the column drift of B.1.

C. Comparison between renders
1. Scene coverage: fraction of alpha 255 per satellite at frames 0, 500, 999 (Melbourne covers only
   part of the 24 km footprint, so values below 1 are expected; report them).
2. Same role, same scene: 2 vs 5 vs 0 (nadir), 1 vs 4 (fore), 3 vs 6 (aft) at their middle frame:
   co-registration shift by phase correlation and mean |dRGB| after shifting; expected sub-pixel
   shifts and small differences.
3. Fore vs aft (1 vs 3) at their middle frames on the CBD: after co-registering on the ground, the
   residual displacement map (e.g. dense optical flow or block matching on 32 px blocks) should
   grow with building height; report its range and the correlation with terrain height.
4. Walls in oblique views: convert the same cache WITHOUT walls into RUN/validation/terrain_grid
   (terrain_to_render.py without --walls) and render satellite 1 frame 500 with it into
   RUN/validation/render_grid. Compare with the run's PNG: mean |dRGB|, where the differences are
   (CBD), shift (expected 0, 0).
5. Visual: RUN/validation/contact_sheet.png with frame 500 of the 7 satellites (crop on the CBD,
   same ground area, labelled with role) and the fore/aft difference map; PNG < 10 MB.

Report: tables for A, B.1-B.4, C.1-C.4, the list of files created with sizes. State clearly anything
that fails or looks wrong. STOP and report.
