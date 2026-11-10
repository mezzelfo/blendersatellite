Read PLAN_CONTEXT.md (section "Stacking into per-satellite cubes", S1-S7) and the reports of
steps 13 and 14. Step 15 only: deputy cubes (satellites 1-4) in mode none, both layouts, to
measure and SEE the band-to-band drift before correcting it. Same scripts as step 14; change
them only if needed for the deputies and report the change.

A. Build cubes/<layout>_none/cube_sat01..04.npz with scripts/build_cubes.py.

B. Validation (scripts/check_cubes.py)
1. Checks B.1, B.2, B.6 of step 14 for every deputy.
2. Band-to-band shift: phase correlation of slice b vs slice 0 (luminance x alpha), rows and
   columns, for every deputy and band. Prediction as in S9 (alpha-weighted mean of the per-pixel
   z = 0 drift over the slice). Parallax term per band and layout from the chief in mode none
   (S10): P_b = measured_0 - predicted_0, from cubes/chief_shifts.json. Required for every band:
   |measured_s - predicted_s - P_b| < 0.05 px. Report also the raw |measured - predicted|.
   If the requirement fails, STOP and report.
3. Plot measured and predicted shift vs band row, one curve per satellite, both layouts
   (cubes/<layout>_none/preview/drift_vs_band.png). Expected: compact flat within about 0.03 px;
   diagnostic linear in the band row, last band of the order of step 13 C.5 (centre column 0.60,
   0.14, 0.76, 0.65 px for sats 1-4; signs from s - 1).
4. Visual check, diagnostic layout, the satellite with the largest drift: crop of 200 x 200 px on
   a high-contrast area (pick it by gradient energy), first band, last band, and their difference
   amplified x10, side by side; also a GIF that cycles the 8 bands of that crop at 2 frames/s.
   Same crop for the chief for comparison.

Report: tables for B.1 and B.2, plot and preview paths, files created. STOP and report.
