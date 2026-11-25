# CLAUDE.md: context for AI-assisted work on blendersatellite

Read this file before any change. It replaces the former `Prompts/` folder (step prompts 0-25 and
reports 18-24, deleted on 25 November 2026; they stay in the git history before that commit).
README.md is the user manual of the pipeline; this file holds the decisions, conventions and
history an assistant needs to continue the work.

## 1. What the project does

Blender renders of a swarm of 7 satellites (one chief + 6 deputies) flying a pushbroom-like burst
over a photogrammetric 3D city mesh, for the IntelliSwarm ERC project (PoliTo, Prof. E. Magli):
onboard super-resolution, compression and distributed inference studies need realistic multi-view
imagery of a HyperScape100 hyperspectral camera. Each satellite renders a full sensor frame at every
time sample; the frames are then cut into per-band rows and restacked into per-satellite cubes
(one detector row per band, as a linear variable filter would see it). Renders are RGB, so the cubes
test geometry and registration, not spectra.

The original EOGS affine-camera renderer (`main.py`, `affine/`, `EOGS_affine_models/`) is a separate,
older path: do not modify it.

## 2. Working agreement

- The user is Marcello. The conversation is in Italian; code comments, docstrings, README and this
  file are in English. Prose without em-dashes.
- Ask brief clarifying questions before multi-step work; give a short plan, then act.
- The user commits and pushes himself (repo mezzelfo/blendersatellite, branch `Marcello`; submodule
  `orbits` = mezzelfo/orbits). Never commit or push unless asked; end a change with the exact
  `git add` / `git commit` commands for him.
- Minimal, targeted diffs; no refactors or reformatting outside the touched lines. Keep old
  behaviour bit-identical unless the change is meant to alter it, and say how it was checked.
- Every change comes with tests (`tests/`, pytest) and they must all pass. Heavy runs (downloads,
  Blender, full conversions) happen on osiride, launched by the user; give him the commands and
  ask for the log lines that verify them.
- Never delete or overwrite outputs, caches or archives without asking. Downloads need his consent
  (name, source, size).
- Report results with the numbers measured against the numbers expected, and flag deviations.

## 3. Machines, environments, storage

- Development copy: the user's Mac, `~/Desktop/Ecostress GitHub/blendersatellite`.
- Execution: workstation **osiride** (Ubuntu), repo in `~/Desktop/melbourne/blendersatellite`,
  conda environment `orbits` (`conda activate orbits`; the `base` env has no numpy), Blender 5.x at
  `/snap/blender/current/blender`, 2x NVIDIA TITAN Xp 12 GB (Pascal; PyTorch 2.10 cu126),
  24 CPUs, 60 GB RAM. Long jobs run inside `screen`.
- Storage on osiride (set in `~/.bashrc`):
  - `/` (system, ~426 GB): code, conda, Blender only.
  - `/media/SSD2/pareschi/blendersatellite/` (shared 3.6 TB SSD): `data/` (the repo's `data` is a
    symbolic link to it: terrain caches) and `output/` (`OUTPUT_ROOT`, runs in progress).
  - `/media/HDD/pareschi/blendersatellite/` (14 TB HDD): `meshes/` (`MESH_STORE`, original ZIPs,
    downloaded once and reused) and `runs/` (finished runs, moved there by hand, including the
    Melbourne runs of November 2026).
  - `output/` in the repo keeps only tracked reports and samples of the Melbourne runs.
- Mesh servers are reachable from osiride and from a desktop browser; not from sandboxed tools.

## 4. Repository layout

- `pipeline/`: the pipeline. `run_swarm.sh` (entry point), `terrain_cache.py`, `terrain_merge.py`,
  `terrain_to_render.py`, `render_swarm.py` (Blender), `render_pool.py`, `render_to_raw.py`,
  `vulkan_gpu_probe.py`, `build_cubes.py`, `cube_stacking.py`, `stacking_geometry.py`,
  `export_cube_band_samples.py`.
- `validation/`: validation and analysis scripts of single steps (import from `pipeline/`); several
  still default to Melbourne run paths that moved to the HDD: pass the paths explicitly.
- `tests/`: pytest suite (`conftest.py` adds `pipeline/` to the path).
- `archive/`: superseded scripts and the step-20 terrain study; paths not updated.
- `acquisitions/`: acquisition files kept in this repo (looked up before `orbits/data/acquisitions`).
- `terrain_datasets.json`: datasets (URL, CRS, tile index, target, origin, composites).
- `orbits/`: submodule, orbit simulation (`scripts/melbourne_overpass.py`, `src/orbits/`). Change it
  as little as possible; it must keep the pointing maths of swarm_simulator.

## 5. Running the pipeline

```bash
conda activate orbits
DATASET=helsinki_espoo_2017 ACQUISITION=hex7_async_20deg.json RENDER_MODE=raw WALLS=0 \
    bash pipeline/run_swarm.sh
```

Steps: orbit simulation (`orbits/scripts/melbourne_overpass.py --target ... --terrain-vertical ...`)
-> `terrain_cache.py` (tiles of the footprint, downloaded one at a time) -> `terrain_merge.py --ensure`
(composites only) -> `terrain_to_render.py` (render grids in the run frame) -> `render_pool.py` (PNG
frames) or `render_to_raw.py` (raw band rows). Every invocation makes a new
`$OUTPUT_ROOT/<dataset>/<UTC stamp>_run_XXXXXX/` with `simulation/`, `terrain_render/`, `renders/`,
`raw/`, `reference_frames/`, `pipeline.log`.

Environment variables: `DATASET` (default `melbourne_2020`), `ACQUISITION` (acquisition file;
without it the old 5-satellite `train` formation of 61 epochs is used, which is never wanted now),
`RENDER_MODE` (`frames` or `raw`), `WALLS` (1/0), `WALL_THRESHOLD` (2 m), `TERRAIN_TEXTURE` (texel m,
default 1; 0 = old vertex colours), `TERRAIN_DECIMATE` (fraction of terrain triangles kept, default
0.15), `ENGINE` (default `EEVEE`), `TERRAIN_SPACING` (cache, default 1 m), `TERRAIN_WORKERS`,
`TERRAIN_ARCHIVES`, `MESH_STORE`, `OUTPUT_ROOT`, `RUNS_ROOT`, `GPUS`, `WORKERS_PER_GPU`,
`MAX_WORKERS_PER_GPU`, `MIN_WORKER_GAIN`, `RENDER_CHUNK`, `RAW_BANDS` (32), `RAW_TDI_ROWS` (8),
`RAW_REF_EVERY` (100), `RAW_BAND_PITCH`, `RAW_KEEP_FRAMES`. Check the first log lines (dataset,
acquisition, terrain mesh, render arguments) before leaving a run.

Tests (on the Mac VM or osiride): `python -m pytest tests/ -q` (about 90 tests, ~2 min).

## 6. Orbit, sensor and acquisition model (decisions)

- HyperScape100: f = 580 mm, pixel 5.5 um, 4096 columns (across) x 3072 rows (along), 22.528 x
  16.896 mm, optical centre row 1535.5, column 2047.5.
- Circular SSO. D1: a_chief = R_avg + 500 km with R_avg = (2a + b)/3 = 6371008.7714 m (WGS84), so the
  orbit is identical for every dataset; the real height above a target changes with latitude
  (Melbourne ~500.9 km, Helsinki ~508.9 km), accepted on purpose. D2: J2, gravity and the SSO
  formula keep the equatorial radius. D3: inclination = sso_inclination(a_chief), ~97.3745 deg.
- Frame period: `nominal` = GSD / v_g = 0.6716580 ms (GSD 4.741 m at 500 km) is used by all
  current acquisitions; `ground` makes the chief advance exactly one row per frame at the target.
  With `nominal` the ground advances ~1.008 rows/frame over Melbourne and ~0.99 over Helsinki
  (0.98 for fore/aft views).
- Formation: `roe_hex7_dmax1km.json`, triangular ROE lattice (sep 1/sqrt(5) km, di_ratio 2, de_phase
  0), lattice edges 894.427 m, neighbours within 1 km. Osculating ROE applied directly at the window
  start. Specification, a x ROE in metres (da = dlam = 0):

  | deputy | dex | dey | dix | diy |
  |---|---|---|---|---|
  | 1 | 447.2136 | 0 | 0 | 894.4272 |
  | 2 | 223.6068 | 387.2983 | -774.5967 | 447.2136 |
  | 3 | -223.6068 | 387.2983 | -774.5967 | -447.2136 |
  | 4 | -447.2136 | 0 | 0 | -894.4272 |
  | 5 | -223.6068 | -387.2983 | 774.5967 | -447.2136 |
  | 6 | 223.6068 | -387.2983 | 774.5967 | 447.2136 |
- Pointing: chief geocentric nadir; deputies aim the chief's ground target, recomputed every frame.
  Acquisition files (`orbits/data/acquisitions`, `acquisitions/`): `hex7_sync` (all nadir),
  `hex7_async_10deg` / `hex7_async_20deg` (deputies 1, 4 fore; 2, 5 nadir; 3, 6 aft; fore/aft along
  the chief's ground trace with the given incidence at the target, line rule `ground_motion`; one
  window of n_frames per role, centred on the time the role sees the scene), `hex7_async_20deg_1550`
  and `hex7_async_20deg_4200` (same with more frames, see section 9).
- Window centred on the capture instant (frame (N-1)/2). Targets are in `terrain_datasets.json`.

## 7. Terrain

Datasets (`terrain_datasets.json`):
- `melbourne_2020`: City of Melbourne photomesh 2020, 570 OBJ ZIPs (~114 GB), EPSG:7855, ArcGIS index,
  8.4 x 8.4 km, covers about 15-25% of a frame.
- `helsinki_2017`: Helsinki reality mesh 2017 (ContextCapture LOD tree, leaves L21), 2 km tiles of 64
  sub-tiles of 250 m, EPSG:3879, origin 25490000, 6668000. 17 ZIPs on the server are truncated
  (zero-filled tails): `terrain_cache.py` reads them entry by entry (`SequentialZip`) and logs the
  entries recovered.
- `espoo_2017`: Espoo + Kauniainen reality mesh 2017 (Blom), 64 flat sub-tiles of 250 m per 2 km
  ZIP, EPSG:3879, origin 25481691, 6680412; all 140 ZIPs intact.
- `helsinki_espoo_2017`: composite, members in priority order. `terrain_merge.py` fits on the shared
  cells a height field (median per 250 m block on flat cells, outlier blocks > 2 m from the median of
  their neighbours within 600 m rejected, linear interpolation, tapered to zero over 2 km) and a
  per-channel colour gain/offset, and corrects Espoo towards Helsinki (`correction.json`). Measured:
  dh median +0.09 m, up to +5.6 m in the north-east (coherent), 4 outlier blocks rejected; colour
  gain ~0.81, Espoo brighter and bluer. Where Helsinki has data its cells win (`face_keep`).
- Targets: composite and Espoo at E 25483000, N 6677500 (96% of the footprint covered); Helsinki
  alone at E 25501000, N 6677000 (93%).

Cache (`terrain_cache.py`): per tile a 2.5D top-view raster at 1 m (supersample 4) of height, RGB and
coverage, plus facade voxels (1 m) for walls; one kind of tile per folder; the ZIP is deleted unless
`MESH_STORE` keeps it.

Render terrain (`terrain_to_render.py`): binned to the 2.5 m render grid, empty cells filled from the
nearest covered cell (this makes flat colour patches over water and holes), transformed to the run's
local ENU frame. Walls (`--walls`): on every edge between render cells whose heights differ by more
than 2 m, coloured from the facade voxels. Textured mode (`--texture-spacing`): colour moved to one
texture per tile (`<tile>_texture.png`), terrain triangles decimated by quadric error (`--decimate`,
pyfqmr, open borders fixed so tiles and members meet without cracks), walls merged along facades
into one quad per run (`--wall-merge-tolerance` 0.5 m) and coloured from a per-tile atlas
(`<tile>_walls.png`, nearest sampling). `cache_frame.json` keeps formats from mixing.

Measured scene sizes: Melbourne grid 13.8 M + walls 11.9 M triangles, 5.7 GB GPU per EEVEE worker.
Helsinki + Espoo at 2.5 m: grid 186 M, walls 216 M triangles: did not fit (host RAM filled while
building the scene). Textured at 1 m and decimated to 0.15: grid 27.9 M triangles, 9.5 GB GPU per
worker (textures take 3-4 GB), one worker per GPU. Decimation 0.15 checked on Melbourne renders:
0.5 DN mean difference, 0.003 px shift in an oblique view; texture vs vertex colours: 2.8 DN mean, no
shift. Textured walls not yet measured on Helsinki.

## 8. Rendering

`render_pool.py` runs persistent Blender workers on every GPU (devices matched by identity), feeds
them tasks of `RENDER_CHUNK` frames, adds workers while memory allows and the frame rate gains at
least 10%, and requeues the task of a dead worker; results are bit-identical whatever the hardware.
EEVEE (Vulkan, 8 samples, shadows and ray tracing off) is the production engine; Cycles only for
checks. Emission shading: the mesh colours are shown as captured, no illumination model.

Raw mode (`render_to_raw.py`): stores only the band rows (`raw/sat_SS.npy`, uint8
[rows, frames, 4096, 4]) plus one reference frame every 100 and the last, deleting the PNGs while
rendering; resumable (`raw/progress.json`). Band rows (`raw/bands.json`): 32 bands of 8 adjacent TDI
rows, start rows multiple of 4, spread over the rows that see the scene in frames 0 and F-1 of every
satellite, or packed every `RAW_BAND_PITCH` rows around the optical centre.

Constraint learned on Helsinki: a ground line is complete in a cube only if every band sees it, so
complete lines ~ (rows advanced per frame x frames) - (rows spanned by the bands). On Melbourne the
small mesh limited the bands to ~900 rows (about 80-100 lines with 1000 frames); on a mesh covering
the whole frame the bands spread over ~3000 rows and 1000 frames give no complete line. Raw data
cannot be reused for other band rows: only the stored rows exist.

## 9. Cubes (`build_cubes.py`, `cube_stacking.py`, `stacking_geometry.py`)

Decisions S1-S11 (10 November 2026), still valid:
- Cube `cube[g, c, b, RGBA]` uint8 per satellite; g = row of the ground line in the satellite's own
  frame 0; columns = every column with scene; alpha = validity. Bands co-registered only within a
  satellite; no inter-satellite registration yet.
- Layouts: `raw_bands` / `raw_rows` (from raw data), `compact` (32 rows 1520..1551), `diagnostic`
  (8 rows every 127 from 1091), or an explicit comma-separated row list (rows must be stored in raw).
- Modes: `none` (integer frame k = r_b - g, no resampling, deputy drift visible), `linear` (fractional
  frame k* from the z = 0 geometric map, linear interpolation in time), `bilinear` (fractional frame
  and column, for oblique views). Index stored per file (int or float32).
- Geometric map S6: x_s(k, r, c) = row in frame 0 of the z = 0 ground point seen at (r, c) in frame k;
  terrain parallax estimated, not corrected. Shift estimator S11: phase correlation with Hann window
  and Gaussian low-pass (sigma ~0.05 cycles/px) to avoid pixel locking (`validation/phase_correlation.py`).
- Output `<run>/cubes/<layout>_<mode>/cube_satSS.npz`; refuses to overwrite without `--overwrite`.

```bash
python pipeline/build_cubes.py --run $RUN --satellites 0 1 2 3 4 5 6 --layout raw_bands \
    --mode none|linear|bilinear --source raw --workers 20
```

## 10. History

- Steps 0-9 (5-9 Nov): audit of orbits; HyperScape100 timing; ROE -> ECI; 7-satellite overpass hook;
  per-frame poses; terrain grid; `render_swarm.py`; EEVEE chosen over Cycles; benchmark and launcher;
  final validation and `SIMULATION_DESCRIPTION.md` (eevee_full_1000, Melbourne, satellites 0-4).
- Steps 10-12 (9 Nov): mean-radius semi-major axis and true frame period (D1-D5), rerun, description.
- Steps 13-16 (10 Nov): cube stacking module, chief and deputy cubes (none, linear), S1-S11.
- Step 17 (11 Nov): acquisition cube (every row seeing the scene is a band) and band samples.
- Steps 18-19b (12-16 Nov): general tile-by-tile terrain cache, binning bug fixed (0.35 m shift),
  cache as pipeline default, guards for the frozen eevee_full_1000 experiment.
- Step 20 (16 Nov): 3D terrain study (REF photomesh vs GRID vs WALL variants); WALL2 chosen.
- Steps 21-22c (17 Nov): sync/async acquisition modes in orbits (same maths as swarm_simulator);
  facade walls (WALL2) as default; facade cache as the only cache.
- Steps 23-25 (17-18 Nov): validation of the async 10 deg run; parallel rendering on both GPUs
  (`render_pool.py`, adaptive workers); cube modes none/linear/bilinear inspected.
- 19-20 Nov: repo reorganised (pipeline/, validation/, tests/, archive/, affine/); pipeline made
  dataset-general (`DATASET`, targets, `run_swarm.sh`); Helsinki and Espoo support (LOD trees, grid
  indices, local origins, truncated ZIP recovery); composite Helsinki + Espoo with correction.
- 23 Nov: storage reorganised (SSD2 / HDD); first Helsinki + Espoo run failed for memory.
- 24 Nov: textured terrain, quadric decimation, textured walls; first Helsinki + Espoo run done
  (`20261124T164423Z_run_0gu8qs`: async 20 deg, 1000 frames, raw, no walls, 1 m texture, 0.15).
- 25 Nov: its cubes had no complete line (bands spread over the whole sensor); band pitch option and
  1550 / 4200-frame acquisitions added; 4200-frame run started (no walls, 2 m texture).

## 11. Open points

- Walls on Helsinki + Espoo: convert with `--walls` (textured) and measure triangles and GPU memory;
  memory is tight at 9.5 GB of 12 with 1 m textures.
- Flat colour patches where the mesh has no data (water, holes) come from the nearest-cell fill.
- Water near the Helsinki / Espoo seam may show a patchwork of the two flights (colour correction
  is fitted on land); a member-ownership map of a frame would confirm it.
- Long windows run past the mesh (the composite covers ~14.5 km south and ~17 km north of the
  target): the first or last frames have empty areas.
- Geoid vs ellipsoid: mesh heights (AHD, EGM96, N2000) are used as ellipsoidal heights.
- Validation scripts still read `selected_tiles` from cameras.json and old Melbourne paths.
- Helsinki truncated ZIPs: an e-mail to helsinki3d@hel.fi was drafted, not sent.
