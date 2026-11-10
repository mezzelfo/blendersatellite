Read PLAN_CONTEXT.md (section "Stacking into per-satellite cubes", S1-S7) and the reports of
steps 13-15. Step 16 only: deputy cubes (satellites 1-4) in mode linear, both layouts, and the
final comparison of the two modes.

A. Build cubes/<layout>_linear/cube_sat01..04.npz with scripts/build_cubes.py (k* per column or
   per satellite as decided in step 13 C.3).

B. Validation (scripts/check_cubes.py)
1. Checks B.1, B.2, B.6 of step 14 for every deputy (B.1 with the recomputed interpolation).
2. Residual band-to-band shift by phase correlation, every deputy and band, rows and columns.
   Required: compact |shift| < 0.02 px; diagnostic |shift_s - shift_0| < 0.02 px, with the chief
   in linear mode from cubes/chief_shifts.json (the common term is terrain parallax, S9).
   If not, STOP and report.
3. Mode comparison per satellite and layout: table "max |shift| none | max |shift| linear |
   predicted none" and the mean |dRGB| of slice b vs slice 0 in the two modes.
4. Interpolation smoothing: for each deputy, ratio of the gradient energy of slice 0 in linear vs
   none (linear interpolation acts as a low-pass along track; report how much).
5. Previews: the same crops and GIF as step 15 B.4, now in linear mode, next to the none version
   (cubes/diagnostic_linear/preview/).

C. Short documentation: a "Cubes" section in README.md (how to build and read a cube, the meaning
   of g, the two modes, the layouts) and a table of the built cubes (satellite, layout, mode,
   shape, complete lines, size).

Report: tables for B.1-B.4, preview paths, disk used by cubes/, files changed or created.
STOP and report.
