# blendersatellite

## Project goal

This project converts [EOGS](https://mezzelfo.github.io/EOGS/) affine camera models (JSON files in `EOGS_affine_models/`) into lightweight Blender camera shaders and renders images of a Blender scene from those camera models. The script generates an OSL (Open Shading Language) camera shader per affine model, configures a Blender camera and light, sets the render resolution, and renders PNG images into the `output/` directory.

At a glance:
- Input: `EOGS_affine_models/<SCENE>/affine_models.json` (list of cameras with `model` and `sun_model` entries)
- Main driver: `main.py` — processes each camera in the chosen scene and triggers a render in Blender
- Output: `output/<camera_name>_render.png`

This repository is mainly a helper for generating Blender renders that match externally-provided affine camera models and sun directions.

## Melbourne OBJ proof of concept

The independently maintained orbit simulator is pinned as the `orbits/`
Git submodule. Clone both repositories together with:

```bash
git clone --recurse-submodules https://github.com/mezzelfo/blendersatellite
```

For an existing clone, use `git submodule update --init --recursive`. After
pulling a parent revision, use `git submodule update --recursive` to check out
its pinned orbit revision. To upgrade intentionally, commit/push orbit changes
inside `orbits/`, then commit the changed `orbits` pointer in this repository.
Do not use `git submodule update --remote` for reproducible rendering unless you
intend to change the pinned version.

Install from the pinned checkout into the existing conda environment:

```bash
conda activate orbits
python -m pip install -e './orbits[test,render]'
```

Prepared terrain under `data/` and renders under `output/` are ignored by Git.
Mesh data is provided by the City of Melbourne under CC BY 4.0; see the
[publisher's dataset](https://discover.data.vic.gov.au/dataset/city-of-melbourne-3d-textured-mesh-photomesh-2020).
Renders and terrain derived from it must carry that attribution.

### Terrain data

The original OBJ ZIPs (570 tiles, 113.67 GB = about 106 GiB) are no longer kept on disk.
`terrain_cache.py` downloads one tile at a time, rasterises the upper surface of
the textured mesh into a grid in the dataset's own projected CRS, writes a small
compressed `.npz` and deletes the ZIP. The cache does not depend on any orbit
simulation and is shared by every run:

```
data/terrain/<dataset>/
    dataset.json        copy of the dataset entry of terrain_datasets.json
    tile_grid.json      tile index (ArcGIS JSON) used to select tiles
    <spacing>m/<tile>.npz   height, rgb, coverage, x0, y0, spacing, ... per tile
    <spacing>m/log.jsonl    one line per tile, written as each tile completes
```

`terrain_to_render.py` then bins the cache to the render spacing (2.5 m, from
`cameras.json`) and converts it into the local ENU frame of one simulation run.
These render grids go to `<run>/terrain_render/` and are tied to that run's
local frame (`cache_frame.json` guards against reuse with another frame).
`run_orbits_melbourne.sh` runs both steps; by hand, for an existing run:

```bash
python terrain_cache.py --dataset melbourne_2020 --tiles-from <run>/simulation/cameras.json \
    --spacing 1 --workers 4 [--archives /path/to/already/downloaded/zips]
python terrain_to_render.py --cameras <run>/simulation/cameras.json \
    --cache data/terrain/melbourne_2020/1m --dataset melbourne_2020
```

Cached tiles are skipped, so a second run downloads nothing (`--dry-run` on either
command shows what would be done). Measured for Melbourne in step 18 (570 tiles
at 1 m): 113.67 GB streamed, about 75 min with 12 workers, peak disk 3.8 GB
(ZIPs in flight), peak RSS 2.8 GB on the largest tile, final cache 236 MB. The
cell colour is the mean of 4 x 4 sub-cell texture samples. `--rebuild-log`
recreates missing `log.jsonl` lines from the `.npz` files present.

Adding a dataset: add an entry to `terrain_datasets.json`.

- `format`: reader in `terrain_cache.READERS`. `obj_zip` expects one ZIP per tile
  holding one `.obj` (all `v`/`vt` lines before the faces, coordinates in metres
  of `projected_epsg`, z up) and optionally a `.mtl` whose `map_Kd` textures are
  in the ZIP.
- `url`: download URL template with `{tile}`; used when the tile is not in `--archives`.
- `projected_epsg`: CRS of the mesh and of the cache grid.
- `geographic_epsg`: geographic CRS of the same datum; `terrain_to_render.py`
  transforms projected -> geographic -> ECEF (EPSG:4979 -> 4978).
- `vertical`: how heights are treated (informational; heights are used as ellipsoidal).
- `index`: tile index for `--bbox` and `--all`: `url` of an ArcGIS JSON query with
  polygon `geometry.rings`, `id_field` (attribute holding the tile id used in `url`),
  `local_copy` (path relative to the repository, used instead of the URL if present).
- `description`, `license`: copied to `dataset.json`.

Renders made with this terrain differ in colour from `eevee_full_1000` (cell mean
instead of one texel per cell: about 12 DN mean |dRGB| on the chief PNGs, no
geometric shift, step 18). Frames rendered with the two terrains must not be mixed
in one experiment. The terrain of `eevee_full_1000` (built by the deprecated
`prepare_swarm_terrain.py`, formerly `data/swarm_terrain`) is kept in
`output/orbits_melbourne/eevee_full_1000/terrain_render_legacy/`; pass it with
`TERRAIN=...` to `scripts/render_job.sh` to extend that experiment.

### Orbit-driven HyperScape100 preview

Activate the `orbits` conda environment, then run from this directory:

```bash
bash run_orbits_melbourne.sh
```

The wrapper requires CUDA: orbit integration defaults to `cuda:0`, and Cycles uses both detected CUDA GPUs with CPU rendering
disabled. Missing GPU access is an error; it does not silently fall back to CPU.
The installed PyTorch build is `2.10.0+cu126`, compatible with the TITAN Xp's
Pascal architecture. To recreate it, install PyTorch before the other dependencies:

```bash
python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu126
```

`ORBITS_DEVICE` selects the numerical CUDA device;
`--gpu-devices 0` selects one Cycles GPU instead of both. Orbit metadata and
`render_report.json` record the actual selected devices. Terrain preparation
runs on the CPU: `TERRAIN_WORKERS` (default 4) sets the number of
`terrain_cache.py` processes and `TERRAIN_SPACING` (default `1`; `1`, `1.0` and
`1.00` all use the same `1m/` cache folder) the cache spacing in metres;
`MELBOURNE_ARCHIVES=/path/to/zips` reuses already downloaded ZIPs. GPU denoising
is disabled for these small previews so it does not introduce an unsupported
CPU denoising pass.

This runs `orbits/scripts/melbourne_overpass.py`, fills the terrain cache with
the selected tiles and converts them into `<run>/terrain_render` (see Terrain
data), then starts headless Blender with `render_swarm.py`.
The default formation is a five-satellite train with the chief in the middle:
`deputy 1 → deputy 2 → chief → deputy 3 → deputy 4`. Every adjacent pair is
1 km apart in 3D at the capture epoch. Positions and velocities are rotated
about the orbital normal using equal phase increments; the phase step is
`2*asin(1000/(2*orbit_radius))`, giving exactly 1 km chord separation.
The whole formation is propagated backward 30 seconds and forward 60 seconds
with the J2 dynamics, so the saved capture state is from the propagated
trajectory. Separation is not constrained by stationkeeping. Measured pair
distances and adjacent extrema over the sampled minute are saved in each run's
`simulation/formation_distances.json`.

Use `TRAIN_SPACING_M=100000 bash run_orbits_melbourne.sh` for a 100 km train, or
`FORMATION=random bash run_orbits_melbourne.sh` for the former compact random
swarm. The standalone exporter accepts `--formation` and `--train-spacing-m`.

`ACQUISITION=hex7_sync.json bash run_orbits_melbourne.sh` and
`ACQUISITION=hex7_async_10deg.json bash run_orbits_melbourne.sh` fly the ROE hexagon
of `orbits/data/formations/roe_hex7_dmax1km.json` with an acquisition file of
`orbits/data/acquisitions/` (exporter option `--acquisition`). The file sets the
formation file, the mode, the roles, the incidence, the detector-line rule, the
frame period and the number of frames. `sync`: every deputy on the chief's nadir
target, one window. `async`: the chief at its nadir, deputies 2 and 5 on the chief's
nadir target, 1 and 4 forward and 3 and 6 backward along the chief's ground trace
with 10 deg incidence at the target (GroundTrackLead), one window per role centred on
the time the role sees Melbourne (about -11.7 s and +11.3 s). The pointing math is the
one of swarm_simulator (`camera_geometry.aimed_basis` = `camera_rotation`), see the
docstring of `orbits/src/orbits/acquisition.py`. Each camera record carries its own
`t_s`, `acquisition_role`, `target_local_m` and `aim_across_local`; the epoch `t_s` is
the time of the chief. `orbits/scripts/validate_acquisition.py` checks an exported run.

`RENDER_MODE=raw` keeps only the raw pushbroom data instead of the full frames
(`scripts/render_to_raw.py`): frames 0 and 999 fix the detector rows that see the
scene over the whole window for every satellite, 32 bands of 8 adjacent rows (start
rows multiples of 4) are spread over them, and the run is rendered in blocks of 100
frames. After each block the rows are written to `raw/sat_SS.npy` (uint8
[256, 1000, 4096, 4], raw[i, k, c] = PNG pixel (rows[i], c) of frame k), read back,
compared with the PNGs, and the PNGs are deleted except one reference frame every
100 (and the last), moved to `reference_frames/`. About 4.2 GB per satellite instead
of 8.2 GB of PNGs; launching the same command again resumes from `raw/progress.json`.
Example: `ACQUISITION=hex7_async_10deg.json RENDER_MODE=raw bash run_orbits_melbourne.sh --engine EEVEE`.
Do not pass `--frames` or `--satellites` in this mode.
`RAW_KEEP_FRAMES=1` extracts the raw rows but keeps every PNG (no deletion).
`scripts/build_cubes.py --run RUN --layout raw_bands --mode none|linear|bilinear --source raw|png`
builds the cubes on the ground from either source (identical results); `bilinear` follows the
column drift of oblique (fore/aft) views, `linear` keeps the column fixed, `none` steps whole frames.
Every invocation creates a fresh directory such as
`output/orbits_melbourne/20261028T171037Z_run_8bJvD7/`; the UTC timestamp plus
unique suffix prevents collisions even for simultaneous runs. `RUNS_ROOT`
changes the root directory; passing `--output` to the wrapper is disallowed.
Existing runs are never overwritten. Each directory contains:

- `simulation/`: trajectories, configuration, initial camera poses, tile grid,
  and formation distances.
- `renders/`: `satellite_00.png` through `satellite_04.png`, final camera poses,
  and the render report.
- `pipeline.log`: run settings, selected GPU devices, validation and render log.

The terrain cache and the downloaded tile grid under `data/` remain shared across
runs; each run also gets `terrain_render/` with its render grids. The older flat output directory and simulation outputs are preserved.
Standalone exporter runs without `--output` also create unique timestamped
folders. Explicit exporter/renderer paths refuse to overwrite prior artifacts.
Satellite 0 is the chief. The
initial poses at a common epoch, footprint corners, physical optics, and sensor
crop calibration are in the run's `simulation/cameras.json`;
the actual 61-step propagated trajectories are saved alongside it. The WGS84
target is computed analytically in ECEF at zero ellipsoidal height (semi-major
axis 6378137 m, inverse flattening 298.257223563), using the nearest forward ray
intersection. Rendering does not use a mesh or terrain hit to choose the target.
The renderer independently recomputes this target, and final poses are saved in
the run's `renders/camera_poses.json` and referenced by the render report.

The cameras are ordinary perspective cameras with a 580 mm lens and 95 mm
aperture (f/6.1), using a roughly 2.97 mm central sensor crop to obtain 512×512
images at nominal 5 m/pixel near 500 km altitude. Depth of field is disabled.
This is an RGB instantaneous-frame approximation of the HyperScape100
pushbroom instrument, not a hyperspectral or detector-noise simulation.
Specifications: <https://simera-sense.com/products/hyperscape100/>.

The chief remains geocentric nadir (camera -Z points at Earth's center), with
image +Y following projected ground-track velocity. By default each deputy
targets the chief's ray intersection with the WGS84 ellipsoid. Deputy roll
is chosen so the ground-projected image +X (across-track) direction matches the
chief's direction and sign. Projection here means the derivative of neighboring
camera rays intersecting the ground plane, including oblique perspective shear.
For forward direction `f` and image-right axis `r`, this derivative is proportional
to `r - f*r.z/f.z`. If `d` is the chief's unit ground across-track direction,
the deputy's right axis is `normalize(d - f*dot(d,f))`. This makes the projected
across-track direction a positive multiple of `d` and fixes the remaining roll.
The chief's attitude is never modified. Set `POINTING_MODE=nadir` to restore
independent nadir pointing for comparison.

The image center GSD remains 5 m/pixel in the across-track ground direction;
along-track GSD can differ slightly under oblique viewing and is recorded in
each pose. Every render validates the common target's image-center projection
and the across-track alignment. The fixed-pole orbit model uses GMST with
UTC approximating UT1 for its Earth-fixed rotation. Melbourne coordinates
are converted from EPSG:7855 into a shared local ENU frame in double precision.
For this preview, AHD heights approximate ellipsoidal heights; precision Earth
orientation, datum/geoid corrections, and attitude dynamics remain future work.

To bound memory and disk use, `terrain_cache.py` streams each ZIP and keeps
only the upper surface of the mesh (highest face per sub-cell, mean colour per
cell); `terrain_to_render.py` bins it to 2.5 m render grids. Unsampled cells use
the nearest sample; nonfinite source UVs receive neutral gray. This upper-surface
mesh simplifies façades/overhangs. Its emission material preserves illumination
already baked into the aerial textures. Images do not simulate a new Sun
position; transparent pixels identify missing geometry at tile boundaries or
outside the published dataset. The deprecated `prepare_swarm_terrain.py` (one
texel per 2.5 m cell, read from already downloaded ZIPs) is kept only to
reproduce `eevee_full_1000` and for `test_swarm_terrain.py`.

`TERRAIN_WORKERS=1 bash run_orbits_melbourne.sh --samples 4` reduces resource
use; `BLENDER_BIN=/path/to/blender` selects another build. Subsequent runs
reuse terrain caches. Direct stage commands accept `--help` (Blender arguments
follow `--`). `render_swarm.py --check-only` validates the five exported
footprints using Blender's projection without loading terrain. Every real
render also checks the footprint corners and 5 m-to-one-pixel calibration.

### Jacksonville affine-camera preview

The Melbourne affine path reuses the Jacksonville affine viewing geometry and its
azimuth/elevation sun metadata with one textured Melbourne photomesh tile. The
source OBJ uses EPSG:7855 metre coordinates; it must be recentered before Blender
imports it so that Blender's float mesh representation does not lose local
precision.

The checked proof of concept uses CBD tile `C4-22` and only three 192-pixel,
four-sample views:

```bash
mkdir -p data/melbourne
unzip ../OBJ/melbourne_obj/C4-22.zip -d data/melbourne
python3 prepare_melbourne_tile.py
./run_melbourne.sh
```

The preparation step creates `data/melbourne/C4-22/C4-22.local.obj` and an exact
`C4-22.local.transform.json` sidecar. It subtracts the tile's XYZ bounding-box
center and uses the JAX_068 scale (226.386 metres per normalized unit). East,
north, and up already agree between the datasets, so the default Z rotation is
zero; `--rotation-degrees` is available for experiments.

The render step imports the MTL/JPG textures, makes the photomesh opaque, and
writes three images plus `render_metadata.json` to
`output/melbourne_C4-22_poc/`. It accepts Blender arguments after the wrapper,
for example:

```bash
./run_melbourne.sh --resolution 256 --samples 2 --camera-indices 0,6,13
```

Set `BLENDER_BIN=/path/to/blender` when Blender is installed elsewhere. OSL
custom cameras require the Cycles renderer; these validation renders use CPU
Cycles deliberately.

## Important repository layout

- `main.py` — the Blender script that reads affine models, converts them to OSL camera code, attaches the OSL to the `Camera` data-block, configures a `Light`, and renders.
- `prepare_melbourne_tile.py` — recenters/scales a georeferenced Melbourne OBJ and records the transform.
- `render_melbourne.py` — imports the textured OBJ and renders selected affine views.
- `run_melbourne.sh` — headless Blender wrapper for the Melbourne renderer.
- `EOGS_affine_models/` — per-scene subfolders (e.g. `JAX_068`, `IARPA_001`, ...) each containing `affine_models.json`, `train.txt`, `test.txt`.
- `output/` — target folder where rendered PNGs are written.

## Assumptions and notes

- Blender drivers must run through `run.sh` or `run_melbourne.sh`; do not call
  `main.py` or `render_melbourne.py` with system Python because `bpy` is only
  available inside Blender. `prepare_melbourne_tile.py` is intentionally a
  standalone system-Python preprocessing tool.

- The legacy Jacksonville `run.sh` contains its own local Blender path. The
  Melbourne wrapper defaults to `/snap/blender/current/blender` and can be
  overridden without editing the script:

```bash
BLENDER_BIN=/path/to/blender ./run_melbourne.sh
```

## Requirements

- Blender 5.x with Cycles; the legacy affine path additionally needs OSL support. The Melbourne path was validated
  headlessly with Blender 5.2.1.

- A lightweight Python virtual environment for helper tools (not for running `main.py`) using `uv`.
