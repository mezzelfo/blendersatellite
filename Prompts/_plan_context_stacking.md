
## Stacking into per-satellite cubes (steps 13-16, decided 10 November 2026)

Goal: reassemble the rendered frames of each satellite into a cube cube[g, c, b] that mimics a
linear-variable-filter acquisition (one detector row per band). Renders are RGB, so every band
holds the same ground in R, G, B: the cube tests geometry and registration, not spectra.

Decisions (by the user, do not change them)
S1. Satellites 0-4 (the rendered ones). Inputs: renders/satellite_SS/frame_KKKK.png,
    stacking_inputs.npz, scripts/stacking_geometry.py (poses.jsonl). Runs on osiride, conda env orbits.
S2. Band layouts, both centred on the optical axis (row 1535.5), passed by name or as a row list:
    - compact: 32 bands, step 1 row, rows 1520..1551, span 31. Most complete along-track lines:
      chief g = 552..1520, 969 lines, 4.60 km. This is the production cube.
    - diagnostic: 8 bands, step 127 rows, rows 1091..1980, span 889, chief g = 981..1091, 111 lines.
      Purpose: make the deputy band-to-band drift visible (span x (s - 1), up to about 0.73 px for sat 3).
    Complete lines with integer stepping: N - span (N = 1000 frames).
S3. Cube index: g = row of the ground line in the satellite's OWN frame 0 (integer grid). Columns =
    every column with scene in that satellite (alpha_col_any union over frames 0..999 of
    stacking_inputs.npz; sat 0 661..2570, sat 1 660..2570, sat 2 661..2570, sats 3-4 662..2569).
    Data RGBA uint8 for every satellite; channel 3 (alpha) is the validity mask.
S4. Bands are co-registered only within each satellite. No registration between satellites in
    steps 13-16 (scale up to 1-2 px and parallax are a later step).
S5. Two modes for every satellite, chief included:
    - none: integer stepping, sample (g, b) = PNG pixel (row r_b, col c) of frame k = r_b - g.
      No resampling, so the deputy drift stays in the cube and can be seen.
    - linear: fractional frame k* where the ground line g crosses row r_b (geometric map S6),
      RGBA interpolated linearly between frames floor(k*) and floor(k*) + 1 at row r_b, rounded
      with np.rint to uint8 (quantisation <= 0.5 DN, alpha treated like the colours).
S6. Geometric map: x_s(k, r, c) = PNG row in frame 0 of satellite s of the z = 0 ground point seen
    at (r, c) in frame k = pixel_from_ground(s, 0, ground_from_pixel(s, k, r, c))[0]. Reference
    plane z = 0; within-satellite parallax of the terrain is estimated and reported, not corrected.
S7. Outputs only under output/orbits_melbourne/eevee_full_1000/cubes/<layout>_<mode>/:
    cube_satSS.npz (cube [G, C, B, 4] uint8, g, cols, band_rows, frame index per (g, b): int for
    none, float k* for linear, metadata json). Report the expected size before writing
    (compact cube of the chief about 237 MB; 12 GB free on osiride).
