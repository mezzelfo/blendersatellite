Continue step 22 from Prompts/report_22.md. Decision on B.3: the shift limit was set too tight for the
estimator (0.01 px steps, pixel locking). New criterion: shift at most 0.10 px, i.e. no worse than GRID.
WALL2 passes. Same rules as prompt 22.

1. Fill the gap in the report: exact CBD and SUBURB facade-voxel totals from wall2/.../log.jsonl.
2. Part C as written, for WALL2, and additionally the same estimate for WALL2_BLOCK (triangles and GPU
   memory for the 570 tiles at 2.5 m), so that the choice between them is documented. Same STOP rules.
3. Parts D and E as written, with WALL2 (not WALL2_BLOCK) as the --walls geometry.
4. In E, document WALL2_BLOCK as a measured alternative (its B.4 numbers) and the reason it is not the
   default: nadir |dRGB| +0.24 DN in CBD against GRID, while facades improve from 12.7 to 8.0 DN.
Report: C (both variants), D.2-D.4, the diffs of E. STOP and report. Do not delete
data/terrain/melbourne_2020/1m.
