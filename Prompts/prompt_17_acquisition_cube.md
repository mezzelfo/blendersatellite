Read PLAN_CONTEXT.md (sections "Stacking into per-satellite cubes", S1-S11, and "Acquisition cube",
F1-F7) and output/orbits_melbourne/eevee_full_1000/SIMULATION_DESCRIPTION.md (sections 4 and 5).
Step 17 is a separate experiment from steps 13-16: the full acquisition cube of every rendered
satellite, WITHOUT any correction, then a 9-band subset exported for the user's computer, then the
validation. One step, three parts; STOP at the end (or earlier where stated) and report.

Rules
- English comments and docstrings. No commits. Do not modify or delete existing outputs; new files
  only under output/orbits_melbourne/eevee_full_1000/cubes/acquisition/ and .../band_samples/.
- No correction of any kind in the cube or in the exported bands: no shift, no resampling, no
  interpolation. The deputy drift must stay in the data.
- Every number in the report is measured by code you run.

A. Full cube (new script scripts/build_acquisition_cube.py)
1. Before building, from stacking_inputs.npz: check that rows 1000..1957 have alpha > 0 in EVERY
   frame (alpha_row_any) and columns 662..2569 likewise (alpha_col_any) for EVERY satellite 0-4.
   If not, STOP and report the actual common intersection.
2. Check the free disk space on the output volume. Needed: 5 x 958 x 1000 x 1908 x 4 bytes =
   36.6 GB. STOP if free space < needed + 10 GB.
3. For each satellite: D = np.lib.format.open_memmap(cubes/acquisition/cube_satSS.npy, uint8,
   shape (958, 1000, 1908, 4)), axes (band b, frame k, column c, RGBA), band b = PNG row 1000 + b,
   column c = PNG column 662 + c. D[:, k] = frame_k[1000:1958, 662:2570] (RGBA as stored in the PNG).
   Read every PNG once, in parallel; write in blocks of frames (e.g. 50) to keep memory bounded.
   Write to a .tmp file and rename at the end. Refuse to overwrite without --overwrite.
4. Next to each cube, cube_satSS.json: shape, axes, rows, cols, dtype, run folder, git commit,
   creation time, and the convention: "D[b, k] is detector row r_b = 1000 + b at frame k; for the
   chief it sees the ground line g = r_b - k (row in frame 0); for a deputy g is approximately
   r_b - s k with s from step 13 C.1 (no correction applied)".
5. Report size, wall time and peak RSS per satellite.

B. 9-band subset for export (in the same script, from the cube, not from the PNGs)
1. Band rows = np.round(np.linspace(1000, 1957, 9)).astype(int) = 1000, 1120, 1239, 1359, 1478,
   1598, 1718, 1837, 1957 (assert these values).
2. For each satellite and band: band_samples/satellite_SS/band_RRRR.png, RGBA, 1908 x 1000 px, image
   row = frame k (top = frame 0), column = PNG column 662 + c. Write a short band_samples/README.md
   with the convention, and note the orientation: going down the band image the ground goes
   FORWARD (g = r_b - k decreases), the opposite of a single frame where forward is up.
3. Add to .gitignore, next to the frame_samples exception:
       !output/orbits_melbourne/eevee_full_1000/band_samples/
       !output/orbits_melbourne/eevee_full_1000/band_samples/**
   Check with git check-ignore that the 45 PNGs are no longer ignored and that cubes/ still is.
   Report the size of every file (each must be < 50 MB) and the total.

C. Validation (new script scripts/check_acquisition_cube.py, read-only)
1. Direct read: 10000 random (b, k, c) per satellite compared with the PNG pixel; and 20 random
   frames compared in full (D[:, k] == frame_k[rows, cols]). All must be equal, otherwise STOP.
2. Exported bands: every band_RRRR.png equals D[r - 1000] bit for bit.
3. Alpha: per satellite and band, fraction of pixels with alpha 255, 0 < alpha < 255 and alpha 0
   over the 1000 frames; where the alpha-0 pixels are (image corners expected, because rows and
   columns were chosen with alpha_row_any / alpha_col_any, not per pixel).
4. Same ground in two bands, chief, no correction applied to the data: for band pairs (b1, b2)
   with delta = r_b2 - r_b1, the chief sees the same ground in D[b2, k + delta] and D[b1, k].
   For adjacent pairs of the 9 bands and for the pair (1000, 1957): mean |dRGB| on alpha 255 at
   offsets delta - 2 .. delta + 2. The minimum must be at delta for the chief, otherwise STOP.
5. Drift of the deputies (measurement only): same pairs, integer offset delta, then the residual
   sub-pixel shift along k between D[b2, k + delta] and D[b1, k] with the estimator fixed in S11
   (if S11 is not settled yet, use Hann + low-pass and report sigma). Prediction: delta x (s - 1)
   with s from step 13 C.1 (frame-0 pixels), alpha-weighted over columns as in S9; parallax term
   P from the chief on the same pair (S10 logic). Required: |measured - predicted - P| < 0.05 px.
   Expected for the pair (1000, 1957), delta = 957: about +0.68 (sat 1), -0.12 (sat 2),
   -0.78 (sat 3), -0.65 px (sat 4). Also report the best integer offset per deputy and pair (it
   may be delta +/- 1 for the large pair, as in step 15).
6. Visual check in band_samples/comparison/ (also exported, inside the gitignore exception):
   for the chief and for each deputy, the ground common to bands 1000 and 1957 (43 frames):
   a 43 x 256 crop on a high-contrast area (same columns for every satellite), upscaled x8 with
   nearest neighbour: band 1000, band 1957 at offset delta, and |difference| x10, side by side;
   plus a GIF cycling the 9 bands on the ground common to all 9 (43 frames, offset r_b - 1000 per
   band), 2 frames/s, same crop. File sizes < 10 MB each.

Report: tables for A.5, B.3, C.1-C.5, the list of exported files with sizes, the .gitignore diff,
and every file created or changed. STOP and report.
