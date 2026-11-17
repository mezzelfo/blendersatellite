Read PLAN_CONTEXT.md, README.md (section "Terrain data"), Prompts/report_18.md, Prompts/report_19.md,
and Prompts/report_20.md (section B describes how WALL was built in the study).
Step 22 brings the WALL variant of step 20 into the pipeline: the terrain keeps the 2.5D grid and gains
vertical walls coloured from the original photomesh facades. Five parts; STOP where stated.

Design (keep it; ask before changing it)
- The cache cannot store wall quads, because walls live on the edges of the RENDER grid and the render
  spacing is chosen later. The cache therefore stores the facades as coloured 3D samples, and the walls
  are built when a run's render grid is made.
- terrain_cache.py, new output per tile (same .npz, new keys, written only with --facades):
  facade voxels on a global 3D grid of size --facade-voxel (default 1 m) aligned to multiples of it in the
  dataset CRS: integer indices (ix, iy, iz), mean sRGB colour, mean horizontal outward normal (2 x int8),
  sample count. Built from the faces with |n_z| < 0.3 (undersides with n_z < -0.3 excluded), sampled
  area-uniformly at >= 16 points per m^2 (e.g. a barycentric lattice), colour read from the full-resolution
  texture at each sample, while the ZIP is still open. No ray casting.
- terrain_to_render.py, new option --walls (default off, behaviour unchanged without it):
  1. On the render grid of each tile, a vertical wall on every shared edge between 4-adjacent cells whose
     heights differ by more than --wall-threshold (default 2 m), from the lower to the upper height.
  2. Each wall is split vertically into segments of height <= the render spacing. Segment colour = mean
     of the facade voxels whose centre is within half a render cell of the edge line horizontally, inside
     the segment's height range, and whose normal is within 60 deg of the wall's outward normal (pointing
     to the lower cell). Fallback: nearest such voxel within 2 render cells; otherwise the lower cell's
     colour. Count segments per case.
  3. Tile seams: walls on the edge between two tiles need both sides. Load the neighbouring tiles' caches
     (when present) as a one-cell halo; each seam edge belongs to exactly one tile, the one holding the
     west or south cell, so no wall is duplicated or missing.
  4. Output keys in the render .npz: wall_vertices (4 per segment, local frame, float32), wall_rgb
     (4 per segment, same colour), plus counts. The grid keys stay as they are.
- render_swarm.py: if wall_vertices is present, add one extra mesh per tile with two triangles per segment
  and the same emission material and colour handling as the grid. Without the keys nothing changes.

Rules
- English comments and docstrings. No commits.
- Do not modify or delete existing outputs. data/terrain/melbourne_2020/1m stays as it is: the new cache
  goes to data/terrain/melbourne_2020/1m_facades/ (option --tag facades appends the suffix).
  Respect the eevee_full_1000 guard of step 19b: never render into eevee_full_1000.
- Files you may change: terrain_cache.py, terrain_to_render.py, render_swarm.py, README.md,
  scripts/test_terrain_cache.py, scripts/terrain_3d_study/* (to reuse its block renders and metrics).
  Anything else: ask first.
- Every number in the report is measured by code you run.

A. Synthetic test (extend scripts/test_terrain_cache.py, no network)
Add a tile with a 20 x 20 m building, 30 m high, whose four walls use a texture region of known colours:
north wall red (255, 0, 0) below z = 15 m and green above, the other walls blue. Assert: facade voxels
exist only on the four walls, with the right colours and normals; with --walls at 2.5 m every wall
segment gets its colour from voxels (no fallback), north segments red below 15 m and green above (one
mixed segment allowed at the transition), the others blue; no wall where the ground is flat; a building
split across two tiles gives exactly one wall on the seam. All step-18 tests still pass.

B. Validation on the step-20 blocks (CBD and SUBURB, 18 archives in
output/terrain_3d_study/20261116T133629Z/archives, used with --archives; no download)
1. Build the 18 tiles with --facades into a temporary data root inside the study folder (not into
   data/terrain). Report per tile: voxels, cache size with and without facades, time, peak RSS.
2. Render with the step-20 cameras and settings (views 0, 15 N, 15 E, 30 N, 30 E) a new variant WALL2
   from these caches, and compare with REF exactly as in step 20 C.3 (regenerate the study's render
   inputs from the archives if they were deleted). Report the step-20 table rows GRID, WALL and the new
   WALL2 side by side: mean |dRGB| all / near-vertical pixels, SSIM, shift; fallback statistics of 2.;
   triangles and GPU peak per block.
3. Pass criteria for WALL2: near-vertical |dRGB| below the step-20 WALL value in every oblique view of
   CBD, shift at most 0.06 px, nadir |dRGB| within 0.1 DN of GRID. If any fails, STOP and report.
4. Optional, only if 3 passes: variant WALL2_BLOCK, where each grid cell is a flat quad at its own height
   and every shared edge with any height difference gets a wall (no ramps, no cracks), coloured as above
   when the difference exceeds the threshold and with the lower cell's colour otherwise. Same metrics,
   plus triangles. Do not make it the default; report it.

C. Cost estimate (before any download)
From B.1 and the step-20 GPU model (MiB = 749 + 182.2 x M triangles + ...), estimate for the 570 tiles:
voxels, cache size, wall segments, triangles, GPU memory at 2.5 m render spacing; build time with the
available CPUs and RAM. STOP if the projected GPU memory exceeds 10 GB or the free disk would drop
below 20 GB; otherwise continue.

D. Full run
1. python terrain_cache.py --dataset melbourne_2020 --tiles-from
   output/orbits_melbourne/20261109T180310Z_run_v2/simulation/cameras.json --spacing 1 --facades
   --tag facades --workers W  (download, about 114 GB streamed; choose W from measured RSS as in step 18).
2. Check: 570 tiles, every grid array identical to data/terrain/melbourne_2020/1m (same height, rgb,
   coverage bit for bit), so only the facade keys are new.
3. python terrain_to_render.py --cameras <same cameras.json> --cache data/terrain/melbourne_2020/1m_facades
   --dataset melbourne_2020 --walls --output <a NEW folder inside run_v2, e.g. terrain_render_walls>.
   Report tiles, grid and wall triangles, fallback statistics, size.
4. Render chief frames 0 and 500 into run_v2/terrain_check/render_walls/ and compare with the
   step-18 renders of the same frames without walls (run_v2/terrain_check/render/): mean |dRGB|,
   phase-correlation shift (expected 0, 0, differences only around tall buildings). GPU peak.

E. README.md: document --facades, --tag, --walls, --wall-threshold, the facade-voxel format, the
measured costs and the validation numbers; say that renders with and without walls must not be mixed.

Report: tables for A (pass/fail), B.1-B.4, C, D.2-D.4, every diff, wall times, files created with sizes.
STOP and report. Do not delete data/terrain/melbourne_2020/1m: the user will after reading the report.
