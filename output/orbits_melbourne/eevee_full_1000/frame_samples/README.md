# Frame samples — eevee_full_1000

Small, version-controlled selection of rendered frames for visual inspection
(the full renders, 38 GB, stay git-ignored). Regenerate with:

    uv run --no-project --with pillow --with numpy python scripts/export_frame_samples.py

## Selection (extremes + t_c)

| group | frames | why |
|---|---|---|
| start | 0, 1, 2 | beginning of the burst |
| capture | 498, 499, 500, 501 | around t_c = 499.5 dt |
| end | 997, 998, 999 | end of the burst |

Same frame indices for all 5 satellites (satellite_00..04). The ground advances
~1 chief row per frame, so consecutive frames differ by ~1 px and start/end differ
by ~1000 rows.

## Files per (satellite, frame)

- `*_full_q4.jpg` — whole 4096x3072 image, alpha composited on black, downscaled 1/4 (1024x768), JPEG q90.
- `*_crop.png` — full-resolution 512x512 crop at a FIXED pixel window, box (left, top, right, bottom) = (1792, 1280, 2304, 1792), i.e. the image centre. Lossless: use it for pixel-level comparisons.

## Layout

- `by_satellite/satellite_XX/frame_NNNN_*` — real files (compare frames of one satellite).
- `by_frame/frame_NNNN/satellite_XX_*` — relative symlinks to the above (compare satellites at one frame).
- `contact_sheet_crop.jpg`, `contact_sheet_full.jpg` — overview grids, rows = satellites, columns = frames.
