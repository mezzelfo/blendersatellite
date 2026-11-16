Step 19b: two fixes from the step-19 report. English comments, no commits, no rendering.
1. scripts/render_job.sh and scripts/launch_render_jobs.sh: output/orbits_melbourne/eevee_full_1000
   is a frozen experiment rendered with terrain_render_legacy. If the output folder is inside
   eevee_full_1000 and TERRAIN does not resolve to
   output/orbits_melbourne/eevee_full_1000/terrain_render_legacy, exit with code 1 and a message
   that says why and how to override (choose another output folder or set TERRAIN to the legacy
   folder). Test the four combinations (legacy/new terrain x eevee_full_1000/other output) on the
   script headers only, without starting Blender.
2. run_orbits_melbourne.sh: derive the cache folder name with the same formatting as
   terrain_cache.py (python: f'{float(spacing):g}m'), so TERRAIN_SPACING=1, 1.0 and 1.00 all
   give 1m/. Test the three values with a dry run (terrain_cache.py --dry-run and
   terrain_to_render.py --dry-run on run_v2): all must report 570 cached and 0 to process.
   Update the README sentence that tells users to write 1 or 2.5.
Report the diffs and the test results. STOP.
