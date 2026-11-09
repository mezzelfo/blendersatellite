Read PLAN_CONTEXT.md. Steps 1-8 are done: the run for satellites 0-4 is in
output/orbits_melbourne/eevee_full_1000 and was validated in step 8. Step 9 only: write
the authoritative description of the simulation and export the geometry needed to stack
the frames of each satellite into a hyperspectral cube.

Rules for this step
- READ-ONLY on the renders and on every existing output. No re-rendering, no edits to
  render_swarm.py, melbourne_overpass.py or the submodule. No commits.
- New files only (listed below). Scripts in scripts/, English comments and docstrings.
- Source of truth is the real code and the real data (cameras.json of
  20261106T080712Z_run_IxoFnH/simulation, results.npz, poses.jsonl, the PNGs, the terrain
  caches in data/swarm_terrain), NOT PLAN_CONTEXT.md. Where they disagree, report it.
- Every number in the description is either measured by a script you run (keep the script)
  or carries a file:line reference. Do not copy numbers from earlier reports without
  recomputing them, except where the report says "from step N" explicitly.

A. SIMULATION_DESCRIPTION.md (write it in output/orbits_melbourne/eevee_full_1000/)
Sections, in this order:
1. Time and orbit. Epoch, capture time t_c, time grid and dt. Chief osculating elements
   at t = 0 and at t_c (a, e, i, RAAN, argp, nu, u). SSO inclination actually used.
   Sub-satellite geodetic lat/lon/height at t_c, ground-track azimuth at t_c,
   ascending or descending, local mean solar time at the pass. Propagator: model (J2),
   integrator, tolerances, number of samples, dense-output error measured in step 3.
2. Formation. ROE table of the 6 deputies (metres and dimensionless, a_ref used).
   RTN position (r, t, n) of all 7 satellites at frames 0, 499, 999, and their relative
   velocities. Min and max pairwise distance over the whole window. For every satellite
   (all 7, even those not rendered): slant distance distance_m at frames 0, 499, 999 and
   its min/max over the window, and the difference with the chief.
3. Pointing law, with file:line for every step. Chief: geocentric nadir, image-up from
   the ECEF-relative velocity. Per frame: chief boresight intersected with WGS84 (h = 0)
   gives the common target. Deputies: aimed_basis towards the common target, roll that
   aligns the ground across-track direction with the chief's. State the frames and
   conventions: ECI, ECEF (GMST rate), local ENU at the Melbourne origin, camera_to_local
   columns (right, up, backward), Blender camera. Give the ENU unit vectors of image-right
   and image-up and the azimuth of image-up from north for the chief at frames 0, 499,
   999. Off-nadir angle per satellite (min/max over frames). Angle between each deputy
   boresight and the chief boresight (min/max over frames).
4. Image conventions. Exact mapping PNG pixel (row from top, column from left) to
   Blender camera view coordinates (world_to_camera_view has its origin at the bottom
   left) to the camera ray to ENU. Define the pixel-centre convention. State whether
   ground features move towards increasing or decreasing PNG row as the frame index grows,
   and give the sign of the 1.01 px per frame advance in PNG row units.
5. Render pipeline. Engine and every setting that differs from defaults, the emission
   material with vertex colours, what a pixel contains (baked aerial texture colour, no
   illumination model, no spectral content beyond RGB), the terrain (2.5 m upper-surface
   grid, no facades, 570 tiles, triangle count), alpha semantics, PNG format. Scene
   extent in the image: for each rendered satellite the union over frames of the rows and
   columns with alpha > 0, and the intersection (rows and columns with alpha > 0 in ALL
   frames). Say whether you computed it from the PNGs (read-only) or from projected tile
   polygons.
6. Results of steps 1-8: one table, expected versus measured, built from the step reports
   and validation_summary.json (cite the source of each line).
7. Known limitations and deviations (heightfield only, RGB only, footprints on the z = 0
   plane, EEVEE about 0.4 DN brighter than Cycles, phase correlation 0.004 px below the
   pose prediction, deputy images shifted up to 0.67 px relative to the chief, satellites
   5 and 6 have poses but no images, anything else you find).

B. Geometry module and export for stacking
1. scripts/stacking_geometry.py (numpy only, no Blender, no torch):
   - load_poses(satellite): reads poses.jsonl INDEXED BY the frame field, never by line
     order; works for satellites 5 and 6 from cameras.json when no poses.jsonl exists.
   - pixel_to_ray(sat, frame, rows, cols) and ground_from_pixel(sat, frame, rows, cols,
     height_m=0.0): ENU ground point of a PNG pixel on the plane z = height_m.
   - pixel_from_ground(sat, frame, xyz_enu): inverse of the above, PNG row and column.
   - chief_to_satellite_pixel(sat, frame, rows, cols, height_m): the pixel of satellite
     sat that sees the ground point seen by chief pixel (rows, cols) at the same frame,
     for a point at height height_m.
   Use the conventions found in section A4; document them in the module docstring.
2. stacking_inputs.npz in the run folder, arrays for all 7 satellites and 1000 frames:
   position_local_m, camera_to_local, distance_m, native_gsd_m, ground pixel size across
   and along on z = 0 (m/px), ground displacement of the image-centre point between
   consecutive frames in metres and in the satellite's own pixels (along and across the
   image axes), and the ratio of that pixel advance to the chief's at the same frame.
   Also the per-frame row/column bounds of the alpha > 0 region for satellites 0-4.
3. Parallax table (in the description, section 2 or 5): for chief pixels at the four image
   corners and the centre, points at height 0, 30 and 100 m, the pixel offset between the
   chief and each deputy at frames 0, 499, 999 (max over those frames).

C. Verification (report numbers)
- pixel_to_ray / ground_from_pixel / pixel_from_ground round trip: max error in pixels,
  expected below 1e-6, for every satellite at frames 0, 499, 999.
- The common target maps to the image centre of every satellite for every frame (max
  error in pixels), recomputed with this module and compared with step 8.
- Real-image check of the conventions, satellites 0 and 3, frames 0, 499, 999: take 200000
  random vertices from the terrain caches (local coordinates and rgb), project them with
  pixel_from_ground, read the PNG pixel at that position (only where alpha > 0), and
  compare with the vertex rgb. Report the median and 95th percentile of |dRGB| and the
  fraction within 25 DN. Repeat with the PNG row axis flipped, with the column axis
  flipped, and with a half-pixel offset; the correct convention must give the lowest
  error. Vertices are on a 2.5 m grid and a pixel covers 4.74 m, so expect a nonzero
  error: what matters is the ranking between conventions and a clear gap.
- Existing tests pass.
STOP and report. Files: SIMULATION_DESCRIPTION.md, stacking_inputs.npz,
scripts/stacking_geometry.py and its checks script.
