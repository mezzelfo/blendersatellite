# CBD sample renders (step 20, terrain 3D study)

Block: D4-04 and its 8 neighbours (840 m x 840 m), EEVEE 8 samples, emission shader, transparent film,
lens 580 mm, 500 km circular orbit seen from the block at zenith angle theta, azimuth N = camera north of
the block looking south, image up = north. 512 x 512 px window of the 4096 x 3072 frame (GSD 4.74 m at nadir,
4.90 / 5.07 m at 15 deg, 5.41 / 6.25 m at 30 deg, across / along the tilt direction).

Variants: REF = original photomesh (textures box-downscaled x8 to fit 12 GB); GRID = current 2.5D pipeline
terrain; WALL = GRID + vertical quads coloured from REF; DEC_r = REF decimated by r (texture-preserving
quadric), textures at ~0.6 m. Metrics and method: Prompts/report_20.md.

Files: CBD_<view>_<variant>.png (renders unchanged; views t00, t15_N, t30_N) and CBD_contact_sheet.png
(rows = views, columns = variants, block crop x 2, nearest neighbour).
