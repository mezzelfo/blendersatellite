Read PLAN_CONTEXT.md. This is a READ-ONLY audit: do not modify, create or delete any file
in the repo (temporary files outside the repo are fine).

Report:
1. git submodule status; does orbits/src/orbits/camera_geometry.py exist; the CLI options
   of orbits/scripts/melbourne_overpass.py, render_swarm.py, run_orbits_melbourne.sh.
2. Every hard-coded 5 (m GSD), 512 (pixels), 580, 5.5e-6, "sensor_crop_mm",
   "reference_plane_gsd_m", "satellite_%02d.png" in melbourne_overpass.py, render_swarm.py,
   camera_geometry.py, prepare_swarm_terrain.py (file:line).
3. Environment: conda env "orbits", torch version, CUDA devices (names, memory), Blender
   binary path and version, whether pytest runs the existing orbits tests (pass/fail counts).
4. Data: MELBOURNE_ARCHIVES path, number of ZIPs (expected 570), total size, free disk
   where data/ and output/ live, whether data/melbourne_tile_grid.json exists.
5. Scheduler (Slurm or other), partitions, GPU limits, maximum wall time.
6. Every difference between PLAN_CONTEXT.md and the real code.

Validation: all six items answered with real values, existing tests executed. STOP.
