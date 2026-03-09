# blendersatellite

## Project goal

This project converts [EOGS](https://mezzelfo.github.io/EOGS/) affine camera models (JSON files in `EOGS_affine_models/`) into lightweight Blender camera shaders and renders images of a Blender scene from those camera models. The script generates an OSL (Open Shading Language) camera shader per affine model, configures a Blender camera and light, sets the render resolution, and renders PNG images into the `output/` directory.

At a glance:
- Input: `EOGS_affine_models/<SCENE>/affine_models.json` (list of cameras with `model` and `sun_model` entries)
- Main driver: `main.py` — processes each camera in the chosen scene and triggers a render in Blender
- Output: `output/<camera_name>_render.png`

This repository is mainly a helper for generating Blender renders that match externally-provided affine camera models and sun directions.

## Important repository layout

- `main.py` — the Blender script that reads affine models, converts them to OSL camera code, attaches the OSL to the `Camera` data-block, configures a `Light`, and renders.
- `EOGS_affine_models/` — per-scene subfolders (e.g. `JAX_068`, `IARPA_001`, ...) each containing `affine_models.json`, `train.txt`, `test.txt`.
- `output/` — target folder where rendered PNGs are written.

## Assumptions and notes

- This project must be run only via the provided `run.sh` wrapper. Do not call `main.py` directly with system Python — `bpy` is only available inside Blender.

- `run.sh` (included) expects a Blender 5.0.1 binary. It currently sets `BLENDER_PATH` to a local Blender 5.0.1 build and runs:

```
$BLENDER_PATH --background main.blend --python main.py
```

The script also checks that the Blender executable exists; update `BLENDER_PATH` in `run.sh` if your installation is located elsewhere.

- Ensure a `main.blend` file exists in the repository root (or update `run.sh` to point to another .blend file). Render outputs are written to the `output/` folder as configured by `main.py`.

## Requirements

- Blender 5.0.1 installed on the system and reachable via the path in `run.sh`.

- A lightweight Python virtual environment for helper tools (not for running `main.py`) using `uv`.
