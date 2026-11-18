Step 25: compare the three cube modes (none, linear, bilinear) of the async run and show the bands of every
satellite. Read README.md, Prompts/report_23.md, scripts/build_cubes.py, scripts/cube_stacking.py and the
run's raw/bands.json and raw/sat_SS.json. Run: output/orbits_melbourne/20261117T152130Z_run_Vs0yvl.
English comments, no commits. Do not modify or delete anything in the run except the new folder
<run>/cubes/inspection/. Keep raw/sat_*.npy.

1. Inventory: list <run>/cubes/raw_bands_{none,linear,bilinear}/cube_satSS.npz for satellites 0-6, with
   shape, dtype, size, number of complete lines and the keys in each file. If a mode or satellite is
   missing, build it with build_cubes.py (same options as the others: --source raw --layout raw_bands
   --workers 20) and say so.
2. Script scripts/inspect_cubes.py (--run, --modes, --satellites, --out), CPU only, parallel over
   satellites. For each satellite and mode it writes to <run>/cubes/inspection/:
   a. band mosaic: all 32 bands in one figure (grid 4x8), same stretch for all bands of a satellite
      (2-98 percentile over the whole cube), band index and centre wavelength from bands.json in each title;
   b. a false-colour composite from three bands spread over the range (say which), plus a pseudo-RGB if
      bands fall near 640/550/470 nm;
   c. mode comparison per satellite: the same 3 bands side by side for none / linear / bilinear, and the
      difference maps linear-none and bilinear-linear with a symmetric colour scale in DN;
   d. mean spectrum of the cube and of a few fixed points (bright roof, road, vegetation, water if present),
      one line per mode.
   Plus one overview figure per mode: the false-colour composite of all 7 satellites in a row, labelled
   with role (nadir / fore / aft) from sat_SS.json.
3. Band-to-band registration, which is what the modes are meant to fix: for each satellite and mode,
   estimate the sub-pixel shift (rows, cols) of every band against a reference band (the middle one) by
   phase correlation on the complete lines only, on gradient images to reduce spectral contrast effects.
   Report a table: satellite, role, mode, max |shift| row, max |shift| col, RMS shift, and a plot of shift
   vs band per satellite with the three modes overlaid. Expected: none leaves shifts up to about 0.5 px
   along track; linear removes most of it on nadir views; bilinear is needed on fore/aft views where the
   column also drifts. If the numbers disagree, show where and do not change the cube code.
4. Report: inventory table, registration table, the list of figures written, total size of the inspection
   folder, and a short verdict on which mode to use for nadir and for fore/aft views. STOP.
