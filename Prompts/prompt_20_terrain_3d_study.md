Read PLAN_CONTEXT.md, the step-18 and step-19 reports and README.md (terrain section).
Step 20 is a study, not a pipeline change: can the terrain be 3D (facades, overhangs) without
an unreasonable memory cost? Compare, on two 3 x 3 tile blocks of Melbourne, the original
photomesh with simplified 3D meshes and with the current 2.5D grid, in geometry, cost and
rendered images at several off-nadir angles. Three parts plus report; STOP where stated.

Rules
- English comments and docstrings. No commits. Do not modify existing outputs or the pipeline
  scripts (terrain_cache.py, terrain_to_render.py, render_swarm.py, run_orbits_melbourne.sh).
- New code only under scripts/terrain_3d_study/; new files only under
  output/terrain_3d_study/<UTC timestamp>/ (call it STUDY). Archives go to STUDY/archives and
  are kept until the end of the study.
- Third-party packages: install pymeshlab (and anything else you need) in a NEW conda env
  `terrain3d` cloned from `orbits`, never in `orbits` itself. Report versions.
- Every number in the report is measured by code you run. Record wall time and peak RSS
  (/usr/bin/time -v) of every heavy command, and peak GPU memory of every Blender render.

Blocks
- CBD: D4-04 (the largest tile) and its 8 neighbours in data/terrain/melbourne_2020/tile_grid.json.
- SUBURB: a 3 x 3 block, fully inside the dataset, whose 9 tiles all have covered fraction > 0.98
  in data/terrain/melbourne_2020/1m/log.jsonl and the lowest total face count among such blocks.
Report the 9 + 9 tile ids, their archive sizes and face counts. STOP if the 18 archives together
exceed 15 GB.

A. What is inside the archives
Download the 18 ZIPs. For each tile report:
1. file list summary: number of OBJ/MTL/texture files, and whether names or folders indicate
   several levels of detail (e.g. L13..L20, LOD, levels). If several levels exist, report faces,
   vertices and texture pixels per level; this alone may answer the question, so report it
   prominently.
2. vertices, faces, textures (count, pixel size, total megapixels), texel size on the ground
   (median over faces of sqrt(face area in m^2 / face area in texels)).
3. geometry statistics: edge length percentiles; fraction of faces and of surface area that is
   near-vertical (|n_z| < 0.3); number of UV islands (connected components in UV space).
4. Also check, without downloading meshes, whether the City of Melbourne publishes the
   Photomesh 2020 as a public I3S SceneServer (follow the SLPK link of the data.gov.au dataset
   to its ArcGIS item and look for a scene service). If one exists, report its node levels and
   the triangle density per level over the CBD block. Spend at most 15 minutes on this.

B. Variants (per block, all in the dataset CRS EPSG:7855, written to STUDY/variants/<block>/)
- REF: the original meshes and textures, merged per block (no change). If REF cannot be loaded
  or rendered in 12 GB of GPU memory, use textures downscaled x2 and say so; REF stays the
  geometric reference in any case.
- GRID: the current pipeline, i.e. data/terrain/.../1m caches converted to 2.5 m with the same
  binning as terrain_to_render.py, triangulated as render_swarm.py does.
- DEC_r for r in {4, 16, 64}: quadric edge-collapse decimation with texture preservation
  (pymeshlab), target face count = REF faces / r, per tile, then textures downscaled so that the
  texel on the ground is about 0.6 m (report the factor used). Report, per r: faces after
  decimation (and how far from the target, since UV seams limit the reduction), build time and
  peak RSS per tile, disk size (OBJ + JPG, zipped), and the geometric error against REF:
  one-sided Hausdorff distance REF -> DEC (mean, p95, max) with pymeshlab.
- WALL (lower priority, skip if it takes more than about 2 hours and say so): GRID plus vertical
  quads wherever two 4-adjacent cells differ by more than 2 m in height, from the lower to the
  upper height, colour of each quad sampled by casting horizontal rays from its centre into REF
  (outward normal direction) and reading the texture at the hit.
For every variant report triangles, texture megapixels and disk size per block.

C. Rendered images (new Blender script, same engine and material as render_swarm.py:
EEVEE, 8 samples, one emission shader, no lights, transparent film, sRGB output)
1. Local ENU frame centred on each block (same transform chain as terrain_to_render.py).
   Camera as in the pipeline: lens 580 mm, sensor 22.528 x 16.896 mm, 4096 x 3072 px, at the
   distance of a 500 km circular orbit seen at zenith angle theta (spherical Earth with the
   pipeline's mean radius), looking at the block centre.
2. Views: theta in {0, 5, 15, 30} deg, azimuth in {north, east} (theta = 0 once).
   7 views x 2 blocks x every variant.
3. Crop every image to the pixels where REF has alpha 255. For each variant vs REF and each view:
   mean |dRGB|, PSNR, SSIM (on luminance), best sub-pixel shift by phase correlation, and the
   same metrics restricted to pixels showing near-vertical faces in REF (render an extra REF
   pass marking faces with |n_z| < 0.3). Also report the fraction of crop pixels that show
   near-vertical faces in REF, per view.
4. Visual: per block and view, a mosaic REF | GRID | DEC_16 | (WALL) with |difference| x10 under
   each, crop 256 x 256 px on the most facade-rich area, nearest-neighbour x4, PNG < 10 MB.
5. Repeat view theta = 15 deg, north, at half the GSD (lens 1160 mm) for REF, GRID and DEC_16 only,
   to see the differences a 2x super-resolution target would contain.

D. Extrapolation
From the measured per-block numbers, estimate for the 570 Melbourne tiles and for a
375 km^2 scene (the full HyperScape100 footprint over the 1000-frame window): triangles,
texture memory, disk size, build time with the available CPUs, and Blender GPU memory per
variant (from the measured peak GPU memory per triangle and per texture megapixel). State which
variants fit in 12 GB of GPU memory for each case.

Report: tables for the block choice, A.1-A.4, B (cost and Hausdorff per variant), C.3 (per view,
per variant), C.5, D; the list of files created with sizes; wall times. Do not recommend a
variant; present the numbers. STOP and report.
