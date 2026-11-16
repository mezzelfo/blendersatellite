Read PLAN_CONTEXT.md and the step-18 report (terrain cache). Step 19 makes the new terrain
pipeline the default and cleans up after step 18. The step-18 fixes to terrain_cache.py,
terrain_to_render.py and scripts/compare_terrain_caches.py may be uncommitted in this working
tree: keep them, and work on top of them. One step, five parts; STOP at the end and report.

Rules
- English comments and docstrings. No commits. No rendering, no download.
- Files you may change: run_orbits_melbourne.sh, terrain_cache.py, terrain_to_render.py,
  scripts/compare_terrain_caches.py, prepare_swarm_terrain.py, scripts/render_job.sh,
  scripts/benchmark_step7.sh, scripts/check_stacking_geometry.py, scripts/describe_simulation.py,
  README.md. Anything else: ask first.
- Do not delete anything. The only move allowed is the one in part A.
- Every number in the report is measured by code you run.

A. Keep the legacy terrain with the renders it produced
1. If data/swarm_terrain exists: check that its 570 .npz names equal the selected_tiles of
   output/orbits_melbourne/20261109T180310Z_run_v2/simulation/cameras.json and that its
   cache_frame.json matches that cameras.json. Then move it (mv, not copy) to
   output/orbits_melbourne/eevee_full_1000/terrain_render_legacy/ and write a README.md there:
   built by prepare_swarm_terrain.py on <dates of the files>, 2.5 m, colour = one texel per cell,
   local frame of run_v2, used for every PNG and cube in eevee_full_1000 (steps 6-17).
2. If data/swarm_terrain does not exist, say so and skip A.1; part C must then fail with a clear
   message where the legacy terrain is required.

B. New default pipeline in run_orbits_melbourne.sh
Replace the prepare_swarm_terrain.py call with:
   python terrain_cache.py --dataset melbourne_2020 --tiles-from "$ORBITS_RUN_DIR/cameras.json" \
       --spacing "${TERRAIN_SPACING:-1}" --workers "${TERRAIN_WORKERS:-4}" [--archives "$MELBOURNE_ARCHIVES"]
   python terrain_to_render.py --cameras "$ORBITS_RUN_DIR/cameras.json" \
       --cache "$PROJECT_DIR/data/terrain/melbourne_2020/${TERRAIN_SPACING:-1}m" --dataset melbourne_2020
   render_swarm.py ... --terrain "$RUN_DIR/terrain_render"
- --archives only if MELBOURNE_ARCHIVES is set and the folder exists. The script must no longer
  exit when the archive folder is missing. Remove TERRAIN_DIR and TERRAIN_DEVICE.
- Keep the tile-grid handling (melbourne_overpass.py still needs --grid).
- Check: bash -n, and a dry run of the two Python commands on run_v2's cameras.json
  (terrain_cache.py --dry-run must report 570 cached, 0 to process).

C. Scripts that read the old cache
- scripts/render_job.sh and scripts/benchmark_step7.sh: TERRAIN default = the terrain_render
  folder of the run that owns the cameras.json they use (<run>/terrain_render); if the variable
  is unset and that folder is missing, exit with a message. Overriding with TERRAIN=... must
  keep working.
- scripts/check_stacking_geometry.py: it checks eevee_full_1000, so its default becomes
  output/orbits_melbourne/eevee_full_1000/terrain_render_legacy; add --terrain to override.
  Run it and confirm the same numbers as SIMULATION_DESCRIPTION.md section on the
  convention check (report old vs new values).
- scripts/describe_simulation.py: replace the hard-coded data/swarm_terrain paths and texts with
  the terrain folder actually used by the described run (legacy for eevee_full_1000). Do not
  regenerate SIMULATION_DESCRIPTION.md; just run the script to a temporary file and diff it
  against the existing one: only path strings may differ.
- prepare_swarm_terrain.py: keep it (test_swarm_terrain.py and the legacy terrain depend on it).
  Add to the docstring and as a stderr warning at startup: deprecated, superseded by
  terrain_cache.py + terrain_to_render.py, kept to reproduce eevee_full_1000.
  test_swarm_terrain.py must still pass.

D. Small fixes from the step-18 report
1. terrain_cache.py: write each log.jsonl line when its tile completes (as_completed), so an
   interrupted run loses no record. Add a --rebuild-log option that rebuilds log.jsonl from the
   .npz files present (shape, covered fraction, cache bytes; download fields null) and use it
   once on data/terrain/melbourne_2020/1m: 570 lines expected, the 564 existing lines kept as
   they are, 6 rebuilt.
2. terrain_to_render.py: print the exact triangle count, sum over tiles of 2 (rows-1)(cols-1);
   it must equal 13,797,886 on run_v2.
3. scripts/compare_terrain_caches.py: docstring example with the new paths.
4. scripts/test_terrain_cache.py (step 18) must still pass: 27/27.

E. README.md
Replace the "download approximately 106 GB" section with the new procedure:
- data layout: data/terrain/<dataset>/{dataset.json, tile_grid.json, <spacing>m/*.npz, log.jsonl};
  render grids in <run>/terrain_render, tied to that run's local frame;
- how to add a dataset to terrain_datasets.json (fields and what each reader expects);
- the two commands, the measured cost for Melbourne (113.67 GB streamed, about 75 min with 12
  workers, peak disk 3.8 GB, final cache 236 MB at 1 m, peak RSS 2.8 GB on the largest tile);
- licence attribution (City of Melbourne, CC BY 4.0) and that renders made with the new terrain
  differ in colour from eevee_full_1000 (cell mean instead of one texel; about 12 DN mean
  |dRGB|, no geometric shift), so frames from the two must not be mixed in one experiment.
Update the line about prepare_swarm_terrain.py (README line ~153) accordingly.

Report: result of A (or why skipped), the diff of every changed file, the checks of B, C and D
with their numbers, the new log.jsonl line count, test results. STOP and report.
