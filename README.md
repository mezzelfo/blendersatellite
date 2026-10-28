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

Keep the original ZIPs outside Git. The default archive location remains
`../OBJ/melbourne_obj`; set `MELBOURNE_ARCHIVES=/path/to/melbourne_obj` to use
another location. Prepared terrain under `data/` and renders under `output/`
are ignored. For sharing data, prefer a download manifest with source URLs and
checksums or externally hosted versioned datasets; GitHub Releases or Git LFS
can distribute a small preview cache without bloating the source-code history.
The original 570 OBJ ZIP URLs are versioned in
`melbourne_obj_download_links.txt`. Mesh data is provided by the City of
Melbourne under CC BY 4.0; see the [publisher's dataset](https://discover.data.vic.gov.au/dataset/city-of-melbourne-3d-textured-mesh-photomesh-2020).
To download the original archives on a new machine (approximately 106 GB):

```bash
mkdir -p ../OBJ/melbourne_obj
wget --continue --input-file melbourne_obj_download_links.txt --directory-prefix ../OBJ/melbourne_obj
```

### Orbit-driven HyperScape100 preview

Activate the `orbits` conda environment, then run from this directory:

```bash
bash run_orbits_melbourne.sh
```

The wrapper requires CUDA: orbit integration and numerical terrain sampling
default to `cuda:0`, and Cycles uses both detected CUDA GPUs with CPU rendering
disabled. Missing GPU access is an error; it does not silently fall back to CPU.
The installed PyTorch build is `2.10.0+cu126`, compatible with the TITAN Xp's
Pascal architecture. To recreate it, install PyTorch before the other dependencies:

```bash
python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu126
```

`ORBITS_DEVICE` and `TERRAIN_DEVICE` select the numerical CUDA device;
`--gpu-devices 0` selects one Cycles GPU instead of both. Orbit metadata and
`render_report.json` record the actual selected devices. New terrain caches
also record their numerical device; existing caches are reused irrespective
of where they were computed. ZIP/OBJ parsing, JPEG decoding, map projection,
scene construction and file output remain CPU/I/O operations. Terrain's face
centroids, cell assignment, sorting and local-frame matrix transforms run on
CUDA. Four spawned terrain workers are used by default; lower
`TERRAIN_WORKERS` if GPU memory is constrained. GPU denoising is disabled for
these small previews so it does not introduce an unsupported CPU denoising pass.

This runs `orbits/scripts/melbourne_overpass.py`, prepares the selected
downloaded ZIP tiles, then starts headless Blender with `render_swarm.py`.
Outputs are `output/orbits_melbourne/satellite_00.png` through
`satellite_04.png`, plus `render_report.json`. Satellite 0 is the chief. The
five poses at a common epoch, footprint corners, physical optics, and sensor
crop calibration are in `orbits/outputs/melbourne-overpass/cameras.json`;
the actual 61-step propagated trajectories are saved alongside it.

The cameras are ordinary perspective cameras with a 580 mm lens and 95 mm
aperture (f/6.1), using a roughly 2.97 mm central sensor crop to obtain 512×512
images at nominal 5 m/pixel near 500 km altitude. Depth of field is disabled.
This is an RGB instantaneous-frame approximation of the HyperScape100
pushbroom instrument, not a hyperspectral or detector-noise simulation.
Specifications: <https://simera-sense.com/products/hyperscape100/>.

Nadir is geocentric (camera -Z points at Earth's center); image +Y follows
projected ground-track velocity. The fixed-pole orbit model uses GMST with
UTC approximating UT1 for its Earth-fixed rotation. Melbourne coordinates
are converted from EPSG:7855 into a shared local ENU frame in double precision.
For this preview, AHD heights approximate ellipsoidal heights; precision Earth
orientation, datum/geoid corrections, and attitude dynamics remain future work.

To bound memory and disk use, `prepare_swarm_terrain.py` reads ZIPs directly
and samples their face-centroid heights and JPG colors into 2.5 m grids.
The highest sampled face wins in each cell. Unsampled cells use the nearest
sample; nonfinite source UVs receive neutral gray. This upper-surface mesh
simplifies façades/overhangs. Its emission material preserves illumination
already baked into the aerial textures. Images do not simulate a new Sun
position; transparent pixels identify missing geometry at tile boundaries or
outside the published dataset. Terrain caches total tens of MB rather than
extracting all selected full-resolution OBJs.

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
