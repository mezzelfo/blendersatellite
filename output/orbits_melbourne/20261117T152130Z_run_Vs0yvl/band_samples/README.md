# Band samples of the cubes of run 20261117T152130Z_run_Vs0yvl (step 25)

Every band of the per-satellite cubes of this run, for the three stacking modes, arranged by mode
and band. Run: `hex7_async_10deg` acquisition, 7 satellites (0, 2, 5 nadir; 1, 4 fore; 3, 6 aft,
about 10 deg along track), EEVEE renders with walls, 1000 frames per satellite, raw rows extracted by
`render_to_raw.py`. The full analysis of these cubes is in `../report_25/report_25.md`.

## Layout

    <mode>/band_BB_rowRRRR/satellite_SS.png    band BB (detector row RRRR) of satellite SS
    <mode>/band_BB_rowRRRR/all_satellites.jpg  the 7 satellites of that band, top to bottom, labelled

- `<mode>` = `none`, `linear`, `bilinear` (see below), 32 bands each: 96 band folders, 672 PNGs,
  96 JPEGs.
- Band BB is the first detector row (TDI stage 0) of spectral band BB: rows 1020, 1048, 1080, ...,
  1932 (`raw/bands.json`, `band_start_rows`; layout `raw_bands` of `build_cubes.py`). The bands
  have no wavelength: every band is the same rendered RGB, seen by a different detector row.

## What a satellite_SS.png holds

`satellite_SS.png = cube[:, :, BB]` of `cubes/raw_bands_<mode>/cube_satSS.npz`, written as is:
RGBA, 8 bit, no stretch, no resampling. Alpha = scene coverage (0 = outside the Melbourne dataset).

- Image row i = cube line g = g[0] + i: the PNG row of the ground line in the satellite's OWN frame 0.
  Up is forward along track, as in a single frame (opposite to the acquisition band samples of
  `eevee_full_1000`, where the image row was the frame index).
- Image column j = PNG column cols[0] + j (the scene columns of that satellite, about 1880-1910).
- Size: 88 lines in mode none; 95-96 lines (nadir) and 64-68 lines (fore/aft) in modes linear and
  bilinear. Only the lines seen by all 32 bands within the 1000 frames are kept: the band span
  (912 rows) uses most of the frames.
- Because g refers to each satellite's own frame 0, the satellites are NOT co-registered with each
  other: fore and aft views also carry about 0.074 px/m of height parallax (report 23, C.3).

`all_satellites.jpg` is for viewing only (JPEG quality 92, transparent pixels white); exact values
are in the PNGs.

## The three modes

| mode | band b at line g, column c is taken from | right for |
|---|---|---|
| none | frame k = r_b - g, column c (integer frame step, no interpolation) | nothing: leaves 3.8-3.9 px (nadir) and 8.8-11 px (fore/aft) between the first/last band and band 16 |
| linear | frame k*(g, c, b) where the ground line g crosses row r_b, column c; RGBA interpolated linearly between frames floor(k*) and floor(k*)+1 | nadir views |
| bilinear | frame k* and column c* where the ground point of frame-0 pixel (g, c) crosses row r_b; RGBA interpolated bilinearly in (frame, column) | fore/aft views (column drift up to 0.7 px) |

How to see the difference: open the same band in `none/` and `bilinear/`. In mode none, a band far
from the middle (band 00 or 31) shows a different strip of ground than band 16 (shifted by several
lines); in linear/bilinear all bands show the same ground.

## Regenerating

    python scripts/build_cubes.py --run <run> --source raw --layout raw_bands --mode <mode> --satellites 0 1 2 3 4 5 6 --workers 20
    python scripts/export_cube_band_samples.py --run <run>
