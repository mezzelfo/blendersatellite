Step 19 is done and I stopped there. All five parts passed their checks, nothing was committed, and I didn't render or download anything.

One thing before the details: I couldn't find a step-18 report file in the repo. I worked from the uncommitted step-18 changes (kept and built on) and the files in `run_v2/terrain_check/`.

## A. Legacy terrain: moved
- `data/swarm_terrain` existed. Its 570 `.npz` names exactly match `selected_tiles` in run_v2's `cameras.json`. All three keys of `cache_frame.json` (`origin_ecef_m`, `local_from_ecef`, `mesh_grid_spacing_m`) match too.
- I moved it with `mv` (same filesystem) to `output/orbits_melbourne/eevee_full_1000/terrain_render_legacy/`. It holds 571 files: 570 `.npz` plus `cache_frame.json`.
- I wrote a `README.md` there with the build dates from the file timestamps: 228 tiles on 2026-10-28, 342 on 2026-11-05, and `cache_frame.json` on 2026-11-06. Git ignores this README (`output/**`), so it is not under version control.

## B. Default pipeline in `run_orbits_melbourne.sh`
- `bash -n` passes. `TERRAIN_DIR` and `TERRAIN_DEVICE` are gone. The script no longer exits when the archive folder is missing, and adds `--archives` only when `MELBOURNE_ARCHIVES` is set and the folder exists. The tile-grid handling is unchanged.
- `terrain_cache.py --dry-run` on run_v2: **570 selected, 570 cached, 0 to process**.
- I added a `--dry-run` to `terrain_to_render.py` so this check writes nothing. On run_v2 it reports **0 to convert, 570 already present, 7,024,880 vertices, 13,797,886 triangles**.
- As a cross-check I pointed it at an empty output folder, so all 570 shapes are computed from the cache. It gives the same 13,797,886 and wrote nothing.

## C. Scripts that read the old cache
- **`render_job.sh` and `benchmark_step7.sh`:** I tested only the script headers, so Blender never started.
  - With no `TERRAIN`, it resolves to `…/20261109T180310Z_run_v2/terrain_render`.
  - `TERRAIN=...` overrides it.
  - If the folder is missing, the script exits with code 1 and a message.
- **`check_stacking_geometry.py`:** the default is now `terrain_render_legacy`, with a new `--terrain` option. It stops with a clear message if the terrain folder is missing. It ran with `FAILURES: none`. All 30 rows of the convention table (n, median, p95, within 25 DN) are identical to `SIMULATION_DESCRIPTION.md`. Old vs new, all unchanged:
  - vertex total: 6,960,533
  - best bilinear offset: +0.00,+0.00 for every satellite and frame
  - motion: +50 rows (cost 0.4 vs 2500.8, predicted 49.999)
  - round-trip maximum: 1.20e-11 px
- **`describe_simulation.py`:** the terrain path now comes from the checker's terrain folder. Diffing a temporary output against the existing description:
  - lines 199 and 295 differ only in the path string (identical after substituting it back);
  - line 3 differs in the generation timestamp and the HEAD hash it embeds (da02b7c → 145b0e0). These are produced by the script, not paths.
- **`prepare_swarm_terrain.py`:** the deprecation notice is in the docstring and printed to stderr at startup. `test_swarm_terrain.py`: **4/4 passed**.

## D. Fixes from step 18
1. **`log.jsonl`** lines are now written as each tile completes. `--rebuild-log` on `1m` reported "564 lines kept, 6 rebuilt" (C4-04, -05, -06, -07, -10, -16). The file now has **570 lines**, and the first 564 are byte-identical to my snapshot taken before. Rebuilt lines also carry `"faces": null` and `"rebuilt": true`.
2. **Exact triangle count:** 13,797,886 on run_v2, as required.
3. **`compare_terrain_caches.py`:** the docstring example now uses the legacy and run_v2 paths. Running it over all 570 tiles gives mean |dRGB| 23.17 and |dz| median 0.33 m, which reproduces step 18's `compare_all.jsonl` exactly.
4. **`test_terrain_cache.py`: 27/27 passed.** The final combined run was 31/31.

## E. README
I replaced the 106 GB download section with a "Terrain data" section covering the data layout, the two commands, the dataset fields and what each reader expects, the licence, and the warning not to mix frames from the two terrains. I also updated the `prepare_swarm_terrain.py` paragraph, and the device paragraph, which still described `TERRAIN_DEVICE` and CUDA terrain sampling.

Not every number in the README comes from code I ran:
- **Remeasured:** cache 236M (`du -sh`) and supersample 4. 113.67 GB is 105.86 GiB, which explains the old "106 GB".
- **Read from a step-18 file:** 12.54 DN mean |dRGB| and phase shift [0,0], from `render_compare.json`.
- **Quoted from your prompt and not reproducible here:** 75 min, 3.8 GB peak disk, 2.8 GB peak RSS, and 113.67 GB. `log.jsonl` only accounts for **111.72 GB**, because the 6 rebuilt lines have no download sizes.

## Diffs
The full patch of all 10 changed files is at `/tmp/claude-1002/-home-pareschi-Desktop-melbourne-blendersatellite/abad6816-ceed-4f53-b871-52e22d72f799/scratchpad/step19.diff` (764 lines). Because step 18 was never committed, it also contains the step-18 hunks.

## Open issues
1. **Decision needed: risk of mixing terrains.** `render_job.sh` and `launch_render_jobs.sh` still write by default to `eevee_full_1000/renders`, but the default `TERRAIN` is now run_v2's new terrain. Launching with defaults would render satellites 5 and 6 into eevee_full_1000 with the new colours. I kept the default you specified. I suggest either a guard that refuses this combination, or changing the default output folder.
2. **Spacing must match the folder name.** `TERRAIN_SPACING=1.0` would write the cache to `1m/` but `terrain_to_render.py` would look in `1.0m/`. The README tells people to write `1` or `2.5`.
3. **Line references in the description.** It cites `prepare_swarm_terrain.py:62-85, 117-121`, pinned to commit da02b7c. With this step's edits the same code is now 6 lines lower in the working tree. I left the reference as it is.