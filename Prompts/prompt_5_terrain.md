Read PLAN_CONTEXT.md. Steps 1-4 are done. Step 5 only: build the terrain caches.

Context from the audit on this machine: data/swarm_terrain already holds 228 of the 570 tile
caches (.npz, 2.5 m grid, with rgb) and cache_frame.json (origin_ecef_m, local_from_ecef,
mesh_grid_spacing_m = 2.5). 342 tiles are missing; their names are in missing_tiles.txt and
their URLs in missing_links.txt (repo root, untracked, do not commit). There is no room for
all ZIPs at once (about 64 GB for the missing ones, 51 GB free), so ZIPs are handled one tile
at a time.

1. Reuse check. Compare cache_frame.json with the frame that prepare_swarm_terrain.py would
   write for the step-4 cameras.json (same origin, same rotation, same spacing). If they
   differ by more than 1e-6 m in origin or 1e-9 in the rotation, STOP and report: the
   existing caches cannot be reused. If they match, also rebuild ONE already cached tile
   (download its ZIP from the links file) into a temporary folder and compare with the
   existing .npz (vertices, rgb): report the maximum difference.
2. Missing tiles. For each tile in missing_tiles.txt: download its ZIP, run
   prepare_swarm_terrain.py for that tile with the same options as run_orbits_melbourne.sh
   (device, workers, 2.5 m grid), check the new .npz (finite vertices and rgb, expected
   shape fields), and only then delete that ZIP. Delete only ZIPs downloaded by this step.
   Be resumable: skip tiles whose .npz already exists and is valid; retry a failed download
   up to 3 times and list the tiles that still fail. Keep free disk above 15 GB at all times
   and stop if it drops below. Do not change the code unless it fails; if it must change,
   make the smallest fix and explain it.

Validation (report numbers):
- one valid .npz per tile of the grid, count == 570, none missing;
- total cache size on disk, wall time, peak GPU/CPU memory per worker, peak disk used;
- coverage map: fraction of non-empty cells inside the 8.4 x 8.4 km bounding box, and the
  total triangle count the renderer will load;
- seam check on 20 random pairs of adjacent tiles (include pairs between old and new caches):
  median |dz| across the shared edge.
STOP and report.
