Read PLAN_CONTEXT.md. Steps 1-3 are done. Implement step 4 only: per-frame pose export.

In melbourne_overpass.py (and camera_geometry.py if the pose math lives there):
- turn the per-satellite pose/footprint computation into a function called for every
  frame k and every satellite; the chief pointing target (chief boresight vs WGS84) and the
  deputy retargeting/roll are recomputed at every frame with the existing functions;
- replace the 512x512 crop calibrated to 5 m/pixel with the full sensor: 4096x3072,
  sensor_width_mm = 22.528, sensor_height_mm = 16.896, focal 580 mm. Footprint corners use
  half-width/half-height of the sensor. Record per satellite: distance, native GSD
  (= distance*p/f) and resolution [4096, 3072]; remove every hard-coded 5 m and 512;
- cameras.json becomes a per-epoch schema (schema_version bumped): top-level metadata as
  today + "epochs": list of {frame, t_s, cameras:[7 records]}; selected_tiles is computed
  from the union of the footprints of ALL frames and satellites, with the existing margin.
Do not touch render_swarm.py or terrain preparation in this step.

Validation (report numbers; write a small script scripts/check_cameras.py that does it):
- chief footprint across/along = 19420.7 / 14565.5 m within 0.5% at frame 0 and 999;
- per-record GSD = distance*p/f and chief GSD ~ 4.741 m within 0.1%;
- chief ground advance between consecutive frames in pixels: report min/mean/max
  (expected about 1.01, accept 1.00 to 1.02);
- the common target projects to the image center of every deputy within 0.01 px for all
  frames (computed independently in numpy from the exported matrices);
- ground across-track alignment dot product >= 1 - 1e-10 for all deputies and frames;
- scene coverage per frame from the 570 real tiles (polygons in data/melbourne_tile_grid.json,
  each 280 x 280 m), not from the bounding box corners: for every frame, the fraction of the
  total tile area (44.7 km2 expected) that lies inside the footprint of the chief, and also
  of each deputy. The footprint is a convex quadrilateral, so clip each tile polygon against
  it (shapely if installed, else Sutherland-Hodgman). Report: min over frames, frame index
  of the minimum, number of frames with coverage < 100%, first frame with 100%, lost area in
  km2 at frames 0, 153, 500, 999, and the number of tiles never fully inside at any frame.
  Keep the window centred on the Melbourne point (decision: no shift). The step-3 bbox
  corner margin was -734 m at frame 0 and +387 m at frame 999; report the same corner
  margin from this code path as a cross-check;
- decompose the chief ground displacement between consecutive frames into the image
  along-track and across-track axes (in pixels): report mean/min/max of both. The along-track
  value should be about 1.01; the across-track value is expected small but may not be zero
  because the ground track relative to the rotating Earth is not parallel to the image rows;
- len(selected_tiles), its bounding box, file size of cameras.json;
- existing tests pass.
STOP and report.
