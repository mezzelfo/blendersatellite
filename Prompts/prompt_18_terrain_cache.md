Read PLAN_CONTEXT.md and output/orbits_melbourne/eevee_full_1000/SIMULATION_DESCRIPTION.md
(section on the terrain). Step 18 replaces prepare_swarm_terrain.py with a dataset-independent
terrain cache: download one photomesh tile at a time, rasterise it to a 2.5D grid in the
dataset's own CRS, delete the archive, and convert the grids to the render frame of a run only
when needed. The code already exists (written outside Claude Code, tested only on synthetic tiles):
  terrain_cache.py, terrain_to_render.py, terrain_datasets.json, scripts/compare_terrain_caches.py
Your job is to test it on real Melbourne data, fix it if needed, and validate it against the
current caches in data/swarm_terrain. One step, five parts; STOP where stated and at the end.

Rules
- English comments and docstrings. No commits.
- Do not modify or delete existing outputs. data/swarm_terrain stays untouched (the user deletes
  it after reading your report). New files only under data/terrain/, under
  <run>/terrain_render/, under <run>/terrain_check/, and scripts/test_terrain_cache.py.
- You may fix bugs in the four new files; report every change as a diff with the reason.
  Do not change their conventions (grid aligned to multiples of the spacing, row 0 = south,
  height = mean of covered sub-cells, colour = mean of covered sub-cells) without asking.
- Every number in the report is measured by code you run.

A. Pre-checks
1. The four files exist and compile; `git status` shows them. If any is missing, STOP.
2. Find the simulation whose cameras.json produced data/swarm_terrain: its origin_ecef_m,
   local_from_ecef and mesh_grid_spacing_m must equal data/swarm_terrain/cache_frame.json.
   Call its run folder RUN (cameras.json = RUN/simulation/cameras.json). If none matches, STOP.
3. In the conda env `orbits`: numpy, scipy, PIL, pyproj import. One tile URL from
   terrain_datasets.json answers (HEAD or a ranged GET of a few bytes). If not, STOP.
4. Free disk space on the data volume, RAM, CPU count.

B. Synthetic unit test (new file scripts/test_terrain_cache.py, pytest-style, no network)
Build two OBJ ZIPs in a temp dir, 300 x 300 m at x0 = 320000, y0 = 5810000 (EPSG:7855), with a
1024 x 1024 JPEG texture whose R = u and G = v (row 0 is v = 1), B = 50, files inside a subfolder:
  SMALL: 3 m triangles on a slope z = 0.01 x, plus a 20 x 20 m building (x, y in 100..120) with a
         flat roof at 30 m and four vertical walls;
  BIG:   the ground as only two triangles, plus the same building.
Run terrain_cache.py on them with --archives at 1 m and 2.5 m. Assert: 100% covered cells,
roof height 30, ground height at (50.5, 50.5) = 0.505 (SMALL) and 0 (BIG) within 1e-3,
rgb at (200.5, 250.5) within 2 DN of (170, 213, 50), x0 and y0 multiples of the spacing.
Then terrain_to_render.py with a cameras.json whose origin is the tile centre: the 1 m and the
2.5 m caches give render grids within 0.01 m and 1 DN of each other.

C. Pilot on 3 real tiles
1. python terrain_cache.py --dataset melbourne_2020 --tiles-from RUN/simulation/cameras.json \
       --limit 3 --spacing 1 --workers 1     (under /usr/bin/time -v)
2. Report per tile: archive MB, download s, total s, cache MB, covered fraction, faces, shape;
   peak RSS. Check that data/terrain/melbourne_2020/1m/downloads/ is empty afterwards and that
   dataset.json and tile_grid.json exist (tile_grid.json copied from data/melbourne_tile_grid.json
   if that exists).
3. For the 3 tiles compare the new covered fraction with sampled_fraction in the old caches
   (expected: new >= old, because triangles are rasterised instead of sampled at centroids).
4. Extrapolate to all selected tiles: total download GB, wall time with W workers, final cache
   size, peak disk (W archives at once). Choose W so that W x peak RSS < 70% of RAM.
   STOP if the projected peak disk exceeds free space - 10 GB, otherwise continue.

D. Full run and conversion
1. Same command without --limit, --workers W. It resumes; failed tiles are listed at the end:
   rerun once; if tiles still fail, STOP and report the errors.
2. python terrain_to_render.py --cameras RUN/simulation/cameras.json \
       --cache data/terrain/melbourne_2020/1m --dataset melbourne_2020
   (default output RUN/terrain_render). Report tiles, vertices, triangles, size on disk.

E. Validation against the current caches (read-only on data/swarm_terrain)
1. python scripts/compare_terrain_caches.py data/swarm_terrain RUN/terrain_render
   Report the overall line and the 10 worst tiles by dz_p95_m.
2. Same statistics restricted to flat areas, where the old grid's local slope is < 0.1
   (add an option --max-slope to compare_terrain_caches.py; default off, behaviour unchanged).
   Expected: in flat areas |dz| close to 0 and |dRGB| a few DN; larger values at roof edges
   because the two grids are offset by up to half a cell and the new height is a mean.
3. Mean covered fraction new vs mean sampled_fraction old over all tiles.
4. Visual check in RUN/terrain_check/: a 2.5 m RGB mosaic of all tiles (old and new side by side,
   same extent, PNG < 20 MB each), and three 500 x 500 m crops on high-contrast areas at full
   resolution: old 2.5 m, new 2.5 m, new 1 m, nearest-neighbour upscaled to the same size.
5. End to end: render chief frames 0 and 500 with render_swarm.py and --terrain RUN/terrain_render
   into RUN/terrain_check/render/ (read its argparse to restrict satellites and frames; same
   samples and settings as the existing run). Compare with the existing PNGs of the same frames:
   mean |dRGB| on pixels with alpha 255 in both, and the best integer shift by phase correlation
   (expected 0, 0). Save the two pairs side by side with |difference| x10.

Report: tables for A, B (pass/fail per assert), C.2-C.4, D, E.1-E.3, E.5, the list of files
created with sizes, every diff to the four files, and wall times. STOP and report.
