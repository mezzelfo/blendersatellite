# Step 22c report: the facade cache is now the only terrain cache

Steps 1–7 are done, nothing failed, and I stopped there. Nothing was committed, rendered or downloaded.

## 1. Starting state ✅

- `data/terrain/melbourne_2020/` holds `1m/`, `dataset.json` and `tile_grid.json`. `1m_facades` no longer exists.
- `1m/` has 570 `.npz` files. Every one has all 8 facade keys, with 1 m voxels, spacing 1 and supersample 4. `log.jsonl` has 570 lines.

## 2. `terrain_cache.py`

- **Facades are the default.** `--facades` is gone; `--no-facades` builds grid-only tiles.
- **One kind of tile per folder.** A new `check_folder` reads every `.npz` already in the target folder, not only the selected tiles. If any was built differently, the script stops before writing anything, dry runs included. "Differently" means facades or not, spacing, supersample, or voxel size; the check works in both directions. The error message lists each kind of tile found.
- **`--tag`** is kept but not used by default. `has_facades` is replaced by `tile_kind`.

## 3. `run_orbits_melbourne.sh`

- **New variables:** `WALLS` (default `1`) and `WALL_THRESHOLD` (default `2`, metres). Any other `WALLS` value exits with an error.
  - `WALLS=1` adds `--walls --wall-threshold "$WALL_THRESHOLD"` to `terrain_to_render.py`.
  - `WALLS=0` converts the same cache without walls.
- **Log line:** `Terrain: cache spacing … m, walls ON/OFF (…)` is printed after the "Formation" line, so it goes into `pipeline.log`.
- **Cache command:** `terrain_cache.py` runs without `--tag` (it never had one). `TERRAIN_CACHE_DIR` is built as before, so missing tiles are now built with facades.

## 4. Render modes

Both modes use `<run>/terrain_render`, and `render_to_raw.py` needed no change. It calls `render_swarm.py` with the same `--terrain`, and the wall meshes are loaded in the single terrain-loading loop that runs before any mode- or crop-specific code. So frames, raw mode and crops all get the walls. Only `--check-only` skips the terrain, as before.

## 5. References to `--facades`, `--tag facades` and `1m_facades`

**Changed:**

| File | Change |
|---|---|
| `terrain_cache.py` | docstring, help texts and the check above |
| `terrain_to_render.py` | module docstring, `--walls` help, the "no facade voxels" error message (now names `--no-facades`) |
| `scripts/test_terrain_cache.py` | main cache built with the defaults into `1m/`; grid-only cache with `--no-facades --tag grid` into `1m_grid/`; all `1m_facades` paths updated; new test `test_cache_refuses_mixed_tiles` (4 refused requests, 2 accepted) |
| `scripts/terrain_3d_study/build_wall2.py` | docstring: the study cache path stays (it exists), noted as built with the old option |
| `README.md` | see 7 |

**Left as they are, but still naming `1m_facades` or `--facades`:**

- **Prompts:** `prompt_22_wall_pipeline.md`, `prompt_22c_pipeline_facades.md`, `report_22.md`, `report_22b.md`.
- **run_v2:** `terrain_check/logs_walls/cache_facades.log` and `convert_walls.time`. `terrain_render_walls/cache_frame.json` does not name it, because it stores no cache path.
- **Study** (`output/terrain_3d_study/20261116T133629Z/`):
  - `wall2/report_22.md`, `wall2/README_step22.diff`, `wall2/count_walls_570.py`, the 18 `wall2/logs/cache_*.log`, and the four `variants/*/WALL2*/summary.json`;
  - the study cache folder `wall2/data/melbourne_2020/1m_facades/` itself;
  - `wall2/cache_tile.sh`, which uses the removed `--facades` option and would fail if re-run.

## 6. Checks

| Check | Result |
|---|---|
| `bash -n run_orbits_melbourne.sh` | ok |
| `WALLS=1`, dry run on run_v2 (output `terrain_render_walls`) | cache: 570 cached, 0 to process; conversion: 0 to convert, 570 present |
| `WALLS=0`, dry run on run_v2 (output `terrain_render`) | cache: 570 cached, 0 to process; conversion: 0 to convert, 570 present |
| `WALLS=1` into a new folder | would convert 570, nothing written |
| `TERRAIN_SPACING=1.00 WALL_THRESHOLD=3` | `1m` cache, `--wall-threshold 3` |
| `WALLS=2` | refused |
| `WALLS=1` into run_v2's `terrain_render`, which was built without walls; and `WALLS=0` into `terrain_render_walls` | both refused by the existing no-mixing guard ("Frame, spacing or wall settings differ…") |
| Cache refusal on temporary folders holding real tiles (C2-27, D4-04) | see the next table |
| Test suites | `test_terrain_cache.py` 36, `test_swarm_terrain.py` 4, `test_cube_stacking.py` + `test_render_to_raw.py` 22: **all pass** |

Cache refusal, requesting tile C2-26, which neither folder contains:

| Folder holds | Request | Result |
|---|---|---|
| facade tiles | `--no-facades` | refused |
| grid-only tiles | default | refused |
| facade tiles | `--facade-voxel 0.5` | refused |
| facade tiles | `--supersample 2` | refused |
| facade tiles | default | accepted |
| grid-only tiles | `--no-facades` | accepted |

The dry runs use the script's own `WALLS` block and its exact command arguments, with `--dry-run` added.

One consequence of the guard: a run converted before this step without walls can't be re-converted with `WALLS=1` into the same `terrain_render`. New runs are unaffected, since each gets a fresh folder.

## 7. README

- **Terrain data:** describes the single layout, `data/<dataset>/<spacing>m/` with grid plus facade voxels. The manual commands now use `--walls`.
- **Refusal rule and `--no-facades`:** a new paragraph, with `--tag`/`--output` for a separate grid-only cache.
- **Walls subsection:** no longer "optional". It no longer mentions `--facades`/`--tag facades`, and it documents `WALLS`/`WALL_THRESHOLD`, the line in `pipeline.log`, and that both render modes get the walls.
- **Measured costs:** now say "with facades", with the grid-only figures from step 18 for comparison.
- **Pipeline variables section:** mentions `WALLS` and `WALL_THRESHOLD`.

## Diff

6 files changed, about +140 / −73 lines. The full patch (448 lines) is at `/tmp/claude-1002/-home-pareschi-Desktop-melbourne-blendersatellite/3689c367-05dc-4c00-a58e-93f777cd0947/scratchpad/step22c.diff`. The main changes to `run_orbits_melbourne.sh`, `terrain_cache.py` and `terrain_to_render.py` are the ones shown in the diff output above. After that output, the `terrain_to_render.py` docstring was only rewrapped to the usual line width.