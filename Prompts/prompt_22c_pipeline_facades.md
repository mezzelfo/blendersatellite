Step 22c: make the facade cache of step 22 the only terrain cache and use it in run_orbits_melbourne.sh.
Before this step the user has deleted the old grid-only data/terrain/melbourne_2020/1m and renamed
1m_facades to 1m, so data/terrain/melbourne_2020/1m now holds the 570 tiles WITH facade voxels.
English comments, no commits, no rendering, no download.

1. Check the starting state: data/terrain/melbourne_2020/1m exists, has 570 .npz and every one holds
   the facade keys; 1m_facades no longer exists. If not, STOP.
2. terrain_cache.py: facades become the default. Replace --facades with --no-facades (grid only, for
   other uses), and refuse to write into a folder whose existing tiles differ from the request
   (facades or not, spacing, supersample, facade voxel size), in both directions, so a cache can never
   mix tile types. Keep --tag, but no default use of it.
3. run_orbits_melbourne.sh:
   - terrain_cache.py without --tag; TERRAIN_CACHE_DIR is data/terrain/melbourne_2020/<spacing>m
     (same formatting as now). Tiles missing from the cache are downloaded and built with facades.
   - New variables WALLS (default 1) and WALL_THRESHOLD (default 2, metres). WALLS=1 adds
     --walls --wall-threshold "$WALL_THRESHOLD" to terrain_to_render.py; WALLS=0 converts the same
     cache without walls (GRID). Print the choice at the start of the run (it goes into pipeline.log).
4. Both render modes (frames and raw) use the same <run>/terrain_render, so render_to_raw.py needs
   no change; confirm that render_swarm.py loads the wall meshes in both modes.
5. Search the repository (scripts, README, tests, defaults) for every reference to 1m_facades or to
   --facades / --tag facades and update it to the new layout; list each change. Leave reports and
   prompts in Prompts/ and existing run outputs (e.g. run_v2/terrain_render_walls/cache_frame.json)
   as they are, but list any that still name 1m_facades.
6. Checks: bash -n; on run_v2's cameras.json, dry runs of the two Python commands with WALLS=1 and
   WALLS=0 (570 cached in 1m, 0 to process in both); the refusal of point 2 in both directions on a
   temporary folder; all test suites pass.
7. README: document the single cache layout (data/terrain/<dataset>/<spacing>m with facades),
   --no-facades, WALLS and WALL_THRESHOLD.
Report the diff and the checks. STOP.
