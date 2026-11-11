# Band samples of the acquisition cube (step 17)

Nine bands of the acquisition cube `cubes/acquisition/cube_satSS.npy` (not versioned, 7.3 GB per
satellite) of run eevee_full_1000, exported by `scripts/build_acquisition_cube.py` directly from the
cube, without any correction (no shift, no resampling, no interpolation).

- `satellite_SS/band_RRRR.png`: RGBA, 8 bit, 1908 x 1000 px. A band is one detector row
  r = RRRR (PNG row of the rendered frames) stacked over the 1000 frames:
  image row k = frame k (top = frame 0), image column c = PNG column 662 + c.
  Bands: rows 1000, 1120, 1239, 1359, 1478, 1598, 1718, 1837, 1957 = round(linspace(1000, 1957, 9)).
  Satellites 00 (chief) and 01-04 (deputies). Alpha = geometry coverage (0 = no scene).
- Orientation: going DOWN a band image (increasing k) the ground goes FORWARD (the chief sees the
  ground line g = r - k of its frame 0, so g decreases), the opposite of a single frame, where
  forward is UP.
- Same ground in two bands: the chief sees in band r2 at frame k + (r2 - r1) the ground that band r1
  sees at frame k. For a deputy g is about r - s k (s = its frame-0 pixel advance per frame), so a
  residual of (r2 - r1)(s - 1) remains after the integer offset (up to about 0.8 px for 1000 vs 1957).
- `comparison/`: visual check of that drift on the ground common to bands 1000 and 1957 (43 frames),
  written by `scripts/check_acquisition_cube.py`.
