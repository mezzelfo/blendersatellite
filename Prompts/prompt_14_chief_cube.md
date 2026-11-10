Read PLAN_CONTEXT.md (section "Stacking into per-satellite cubes", S1-S7) and the step 13 report.
Step 14 only: the chief cubes (satellite 0) from the real PNGs, both layouts, modes none and
linear. Do not modify anything under output/ except the new cubes/ folder (S7).

A. Script scripts/build_cubes.py
- Arguments: --satellites, --layout (name or rows), --mode {none,linear}, --workers, --overwrite.
- Reads every needed frame once (Pillow, RGBA), keeps only the band rows (none) or the band rows
  of the two frames around k* (linear), and only the scene columns of the satellite (S3).
  Parallel over frames; memory must stay well below the machine RAM (report the peak).
- Writes cubes/<layout>_<mode>/cube_sat00.npz as in S7 and refuses to overwrite without --overwrite.
  Linear mode: k* per column, float32 [G, C, B] in the same file (S8).
- Print the expected size before writing and the wall time after.

B. Validation (scripts/check_cubes.py, read-only on PNGs and cubes)
1. Direct read: 1000 random (g, c, b) samples of each cube compared with the PNG pixel the index
   arrays point to (none) or with the interpolation recomputed from the two PNGs (linear): all equal.
2. Band slices (mode none, both layouts): for each band b against band 0, on pixels with alpha 255
   in both: mean |dRGB|, 99th percentile, fraction of identical pixels. The ground is the same,
   so differences come only from the 3-D terrain seen from slightly different angles and from
   anti-aliasing. Report the numbers; do not assume they are zero.
3. Sensitivity: repeat 2 with a deliberate offset k = r_b - g + 1 and + 2. The statistics must grow
   clearly (state the ratio). If the correct index is not the minimum among offsets -2..+2, STOP.
4. Phase correlation (scripts/phase_correlation.py method, luminance x alpha) of slice b vs slice 0:
   shift rows / cols per band, both modes. Compact: expected |shift| < 0.01 px. Diagnostic: report
   measured vs the S9 prediction (mode none) and the residual (mode linear), no threshold: the
   difference is the terrain-parallax term, the reference for steps 15-16 (S9). Save the per-band
   chief shifts (both layouts and modes) to cubes/chief_shifts.json.
5. none vs linear for the chief: max and mean |difference| (DN); expected tiny (drift 0.004 rows).
6. Alpha: compare the alpha slices across bands (count of differing pixels).
7. Previews in cubes/<layout>_<mode>/preview/: RGB of band 0 and of the last band (downsampled
   if needed), and |last - band 0| amplified x10, as PNG.

Report: size, time, peak memory, tables for B.1-B.6, preview paths, files created. STOP and report.
