# Step 22b report (continuation of step 22): pasted from the chat on 2026-11-17.
# The full report written by Claude Code is in output/terrain_3d_study/20261116T133629Z/wall2/report_22.md on osiride.

C passed for WALL2, D and E done. Full facade cache built, converted with walls, renders without shift.

Voxel totals (study blocks): CBD 1,693,918; SUBURB 245,398.

C. 570 tiles at 2.5 m: GRID 13,797,886 triangles, 3,369 MiB measured; WALL2 25,667,844 triangles,
5,967 MiB (512 px window); WALL2_BLOCK 48,163,264 triangles, 11,703 MiB (over the 10 GB limit).
The step-20 GPU formula underestimates walls (4 vertices per 2 triangles).

D.1: 570/570 tiles, 113.67 GB downloaded in 1 h 32 min, peak RSS 2.92 GB, 47,595,952 voxels, cache 583 MB.
D.2: every array of 1m bit-for-bit identical in 1m_facades; only the 8 facade_* keys are new.
D.3: run_v2/terrain_render_walls, 8 min, 231 MB; 2,084,927 wall edges, 5,934,979 segments,
25,667,844 triangles; segment colour from voxels 76.0 %, nearest voxel 20.8 %, lower cell 3.2 %.
D.4: chief frames 0 and 500 vs step-18 renders: mean |dRGB| 0.031 / 0.032 DN, 97.24 % identical pixels,
shift (0, 0); full-frame GPU peak 7.1 GiB (5.7 GiB held after render, 3.7 without walls);
scene setup 66 s instead of 28 s.
E: README subsection "Walls from the photomesh facades (optional)", diff in STUDY/wall2/README_step22.diff.
