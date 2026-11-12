# Band samples of the acquisition cube (step 17)

This folder holds 9 bands of the acquisition cube of run `eevee_full_1000`, for the chief (satellite
00) and the four rendered deputies (01-04), plus visual comparisons. Nothing here is corrected,
resampled or interpolated: every pixel is a pixel of a rendered frame, moved to another place.

## 1. Input

- Rendered frames: `renders/satellite_SS/frame_KKKK.png`, k = 0..999, RGBA 8 bit, 4096 x 3072 px
  (PNG row from the top, column from the left). Satellites 0-4 only (5 and 6 were not rendered).
- One clock for the swarm: frame k is taken at t_k = k dt for every satellite, dt = 0.6661251 ms,
  chosen so that the ground advances exactly one row of the CHIEF per frame (towards increasing
  row). The deputies advance 0.99918 .. 1.00071 rows per frame in their own pixels.
- Alpha = scene coverage of the pixel (0 = outside the Melbourne dataset, which is not a rectangle).

## 2. How the bands were selected

A band is one detector row r: the band image is that row stacked over the 1000 frames. To give every
band the longest possible along-track extent (1000 pixels, one per frame) with scene in every
frame, the rows were selected as follows.

1. For each satellite, the rows and the columns that contain scene in EVERY frame were taken from
   `stacking_inputs.npz` (`alpha_row_any`, `alpha_col_any`: true when at least one pixel of that
   row or column has alpha > 0):

   | satellite | rows with scene in every frame | columns with scene in every frame |
   |---|---|---|
   | 00 | 999..1957 | 661..2570 |
   | 01 | 999..1957 | 660..2570 |
   | 02 | 999..1957 | 661..2570 |
   | 03 | 1000..1957 | 662..2569 |
   | 04 | 1000..1957 | 662..2569 |

2. The intersection over the 5 satellites was kept, so that band b is the same detector row in
   every satellite: rows 1000..1957 (958 bands) and columns 662..2569 (1908 columns).
3. For export, 9 of the 958 bands, evenly spaced over the whole span:
   rows = round(linspace(1000, 1957, 9)) = 1000, 1120, 1239, 1359, 1478, 1598, 1718, 1837, 1957.
   The first and the last are the two extreme rows of the cube.

The criterion works on whole rows and columns, not on single pixels: a selected row has scene in
every frame somewhere, but not necessarily in every column. Pixels outside the dataset keep alpha 0
and appear white.

## 3. How the stacking was done

`scripts/build_acquisition_cube.py` builds, for each satellite, the cube

    D[b, k, c] = frame_k[1000 + b, 662 + c]      (RGBA exactly as stored in the PNG)

with shape (958, 1000, 1908, 4), axes (band b, frame k, column c, RGBA), uint8, stored as
`cubes/acquisition/cube_satSS.npy` (7.3 GB per satellite, not versioned) with `cube_satSS.json`.
Each PNG is read once and its rows 1000..1957, columns 662..2569 are copied into frame k of the
cube. No shift, no resampling, no interpolation, the same for the chief and the deputies.

The band images in this folder are slices of that cube, `band_RRRR/satellite_SS.png = D[RRRR - 1000]`,
RGBA, 1908 x 1000 px: image row k = frame k (top = frame 0), image column c = PNG column 662 + c.
`scripts/organize_band_samples.py` then arranged them by band and wrote the comparisons.

## 4. What a band image shows

- Band r of the chief at frame k sees the ground line g = r - k (g = row of that line in frame 0).
  Over the 1000 frames the band covers lines r .. r - 999, about 4.75 km along track.
- Orientation: going DOWN a band image (increasing k) the ground goes FORWARD (g decreases), the
  opposite of a single frame, where forward is UP.
- Different bands see different strips of ground. Two bands r1 < r2 see the same ground with a
  delay of r2 - r1 frames, so in a 1000-frame window they share only 1000 - (r2 - r1) lines:
  about 880 lines for neighbouring exported bands (119-120 rows apart), 43 lines for bands 1000 and
  1957. Seeing L common lines in bands spanning S rows needs L + S frames.

## 5. Same band, different satellites

`band_RRRR/diff_satSS_vs_sat00_x10.png` is |deputy - chief| x 10 in the same band (white where
either alpha < 255). `band_RRRR/blink_satSS_vs_sat00.gif` alternates chief and deputy at 2 frames/s
on two crops upscaled x8: frames 0..63 (left) and frames 936..999 (right). Bands 1000, 1718 and 1957
have no GIF: no 96-column window is fully opaque in all satellites at the top or at the bottom.

- All satellites move with the same ground velocity, so in one band the deputy - chief offset is
  CONSTANT along k (top and bottom crops shift in the same way).
- Each deputy points at the common target, which falls at the image centre (row 1535.5, column
  2047.5): there the offset is zero.
- Each deputy sees the ground from its own distance (mean slant range minus the chief: -352 m,
  +63 m, +415 m, +353 m for satellites 1-4), so its pixel is a little larger or smaller (GSD
  4.7463, 4.7502, 4.7536, 4.7530 m against 4.7496 m for the chief). This is a small zoom about
  the image centre: a point Delta_c columns from the centre is offset by about
  -(GSD_s / GSD_0 - 1) Delta_c, and the same along the rows.
- For satellite 3 this gives about 1.2 px at the left edge of the band (PNG column 662), zero near
  cube column 1385 (PNG column 2047) and about 0.4 px at the right edge; bands far from row 1535
  (1000 and 1957) get a further row offset of about 0.4 px. The difference image shows the offset
  times the local contrast: uniform areas (water, parks) stay dark even where the offset is large.
- The sign follows the distance: satellite 1 is closer than the chief (zoom the other way),
  satellite 2 is almost at the chief's distance (almost dark map).

## 6. Different bands, same deputy: the drift

The drift of a deputy is a band-to-band effect inside one satellite. Two detector rows r1, r2 of a
deputy are (r2 - r1) x GSD_s apart on the ground, while in (r2 - r1) frames the ground advances by
(r2 - r1) x GSD_0 (one chief row per frame). After the integer frame offset r2 - r1 a residual of
(r2 - r1)(s - 1) rows remains, s being the deputy's advance per frame in its own pixels: for bands
1000 and 1957 about +0.68 (sat 1), -0.12 (sat 2), -0.78 (sat 3) and -0.65 px (sat 4); zero for the
chief by construction of dt.

`comparison/` shows it on the ground common to bands 1000 and 1957 (43 frames), written by
`scripts/check_acquisition_cube.py`:
- `satSS_band1000_vs_band1957_offset957_absdiff_x10.png`: band 1000 and band 1957 at frame offset
  957 side by side with |difference| x 10;
- `satSS_9bands_common_ground.gif`: the 9 bands in sequence on that common ground, each at its own
  frame offset r - 1000.

## 7. Regenerating

1. `python scripts/build_acquisition_cube.py` (cluster, conda env orbits): cubes and the 9 band
   PNGs, written in the old per-satellite layout `satellite_SS/band_RRRR.png`.
2. `python scripts/check_acquisition_cube.py`: fidelity checks and `comparison/`.
3. `python3 scripts/organize_band_samples.py`: moves the PNGs to `band_RRRR/satellite_SS.png` and
   writes the diff images and the blink GIFs.
