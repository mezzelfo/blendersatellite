Read PLAN_CONTEXT.md. Step 1 is done. Implement step 2 only.

Create orbits/src/orbits/roe.py (torch, float64, CPU and CUDA tensors):
- elements_to_alpha / alpha_to_elements (quasi-nonsingular, as in PLAN_CONTEXT.md);
- elements_to_roe(chief_elements, deputy_elements) and
  roe_to_deputy_elements(chief_elements, roe), the exact nonlinear inverse pair:
  a_d = a_c (1 + da); i_d = i_c + dix; RAAN_d = RAAN_c + diy / sin(i_c);
  (ex, ey)_d = (ex, ey)_c + (dex, dey); u_d = u_c + dlam - (RAAN_d - RAAN_c) cos(i_c);
- roe_to_eci(chief_state_eci, roe, mu) built on orbits.elements.eci_to_elements and
  elements_to_eci;
- hex_lattice_roe(sep_m, a_m, di_ratio, de_phase, n_deputies=6) reproducing the generator
  in PLAN_CONTEXT.md.
Create the data file orbits/data/formations/roe_hex7_dmax1km.json (ROE in metres, a*ROE, plus the
dimensionless values for a_ref = 6870140.5 m, 6 deputies, order as in PLAN_CONTEXT.md) with a provenance field (notebook name
swarm_configuration_analysis_new.ipynb, parameters, generator formula). The file must be
produced by hex_lattice_roe, not typed by hand. Create orbits/tests/test_roe.py.
Touch no other file. ROE are osculating and applied directly (no mean-osculating map).

Validation (report all measured numbers):
- hex_lattice_roe a*ROE matches the table in PLAN_CONTEXT.md to 1e-6 m and does not depend
  on the a_m argument (the geometry is fixed in metres);
- round trip roe -> deputy elements -> roe returns the input to 1e-12, also for a chief
  with e = 1e-3 and for nu values 0, 90, 180, 321.8 deg;
- chief: a = 6870140.5 m (the chief radius used by the code), e = 0,
  i = orbits.elements.sso_inclination(a) (print it, about 97.372 deg), RAAN = 0, argp = 0;
  roe_to_eci then orbits.frames.eci_to_lvlh gives RTN positions that differ from the
  linear map (dr, dt, dn) by less than 1 m for u = 0, 90, 180, 321.8 deg. Report the
  actual maximum residual in metres per deputy;
- in the T-N plane the 12 lattice edges are 894.427 m +/- 1 m at every u tested; the 3D
  distance between neighbours is <= 1000 m + 1 m;
- all outputs finite, shapes correct, runs on CUDA if available;
- existing orbits tests still pass.
STOP and report.
