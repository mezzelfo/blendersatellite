# blendersatellite

## Project goal

This project converts [EOGS](https://mezzelfo.github.io/EOGS/) affine camera models (JSON files in `EOGS_affine_models/`) into lightweight Blender camera shaders and renders images of a Blender scene from those camera models. The script generates an OSL (Open Shading Language) camera shader per affine model, configures a Blender camera and light, sets the render resolution, and renders PNG images into the `output/` directory.

At a glance:
- Input: `EOGS_affine_models/<SCENE>/affine_models.json` (list of cameras with `model` and `sun_model` entries)
- Main driver: `main.py` — processes each camera in the chosen scene and triggers a render in Blender
- Output: `output/<camera_name>_render.png`

This repository is mainly a helper for generating Blender renders that match externally-provided affine camera models and sun directions.

## Melbourne OBJ proof of concept

The Melbourne path reuses the Jacksonville affine viewing geometry and its
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

- Blender 5.x with Cycles and OSL support. The Melbourne path was validated
  headlessly with Blender 5.2.1.

- A lightweight Python virtual environment for helper tools (not for running `main.py`) using `uv`.
