Read PLAN_CONTEXT.md (section "Stacking into per-satellite cubes", decisions S1-S7) and
output/orbits_melbourne/eevee_full_1000/SIMULATION_DESCRIPTION.md (sections 4 and 5).
Steps 1-12 are done. Step 13 only: the stacking module, synthetic tests and the geometric map on
the real poses. Do NOT read any PNG in this step and do not write anything under output/.

A. Module scripts/cube_stacking.py (NumPy only, no file I/O in the core functions)
1. LAYOUTS = {'compact': rows 1520..1551 step 1, 'diagnostic': 8 rows 1091 + 127 i}, plus
   band_rows(spec) that accepts a layout name or an explicit list of rows (sorted, unique, inside
   0..3071).
2. complete_lines_integer(band_rows, n_frames) -> (g_min, g_max): max(r_b) - (n_frames - 1) .. min(r_b).
3. integer_index(g, band_rows) -> frame index k[g, b] = r_b - g.
4. ground_line_map(sat, frames, rows, cols) -> x[k, r, c] (S6), using stacking_geometry
   (ground_from_pixel, pixel_from_ground). Vectorised over frames.
5. fractional_index(x_of_k, g) -> k*(g) for one band and column: x is monotone in k (check it and
   raise otherwise); solve x(k*) = g by linear interpolation between integer frames; NaN outside
   0..n_frames-1.
6. complete_lines_fractional(k_star) -> range of g where k* is finite for every band.
7. build_cube(frames_reader, band_rows, cols, g, mode) -> uint8 [G, C, B, 4] plus the index arrays.
   frames_reader(k) returns the rows needed from frame k (so tests can pass synthetic frames).
   For linear, the column used for k* is given by S6 at that column (see C.3 for whether one
   column per satellite is enough).

B. Synthetic tests (scripts/test_cube_stacking.py, pytest, run in env orbits)
1. Integer: random RGBA texture T[L, C] uint8; frame k row r = T[r - k] (rows outside T
   transparent). mode none, both layouts: cube[g, :, b] == T[g] bit for bit for every band;
   number of complete lines == N - span (969 and 111 with N = 1000).
2. Off-by-one guard: build with k = r_b - g + 1; the test above must fail (assert it does).
3. Fractional: a smooth band-limited texture (sum of a few sinusoids, period >= 8 rows) sampled at
   row coordinate r - s k with s = 1.0008 and s = 0.9992, values in float then rounded to uint8.
   linear mode: max |cube - truth| <= 1 DN. none mode: band-to-band shift measured by
   1-D cross-correlation of the slices equals span x (s - 1) within 0.01 rows.
4. Monotonicity / NaN handling of fractional_index at both ends of the window.

C. Geometric map on the real poses (satellites 0-4; read stacking_inputs.npz and poses.jsonl)
1. For each satellite and layout: x_s(k, r_b, c) at the centre column of its scene (S3) for all
   1000 frames. Fit x = a + s k per band: report s and the max fit residual.
2. Cross-check with stacking_inputs.npz: s at the centre row vs mean image_motion_rows_px[s]
   (expected 1.000000, 1.000706, 0.999879, 0.999173, 0.999291); difference < 1e-5.
3. Column dependence: max over frames and bands of |x(k, r_b, c) - x(k, r_b, c_centre)| for c at
   the two scene edges. If it is above 0.01 px, k* must be computed per column; report which.
4. Complete lines, integer and fractional, per satellite and layout.
5. Predicted band-to-band drift in mode none: for each g, x(k = r_b - g, r_b) - g, max over g, per
   band. Expected order: compact up to about 0.026 px, diagnostic up to about 0.73 px (sat 3).
6. Within-satellite parallax: repeat 5 with the ground point at z = 30, 100, 300 m instead of 0
   (ground_from_pixel height_m), report the change in px per layout.
7. Expected cube sizes (G x C x B x 4 bytes) per satellite, layout and mode.

Report: tables for C.1-C.7, test results, list of files created. STOP and report.
