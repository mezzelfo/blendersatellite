Both step-19b fixes are in and pass their tests. Nothing was committed or rendered, and Blender never started.

## 1. Terrain guard in `render_job.sh` and `launch_render_jobs.sh`

**Change:** both scripts now exit with code 1 when the output folder is inside `eevee_full_1000` and `TERRAIN` is not the legacy folder. The message names both folders and gives the two ways out: set `OUTPUT=` to another folder, or set `TERRAIN=` to the legacy folder. Paths are compared after `realpath -m`, so relative paths, trailing slashes and output folders that don't exist yet are handled.

I also made two additions you didn't ask for, in `launch_render_jobs.sh`:
- It now works out `CAMERAS` and `TERRAIN` itself, with the same default as `render_job.sh`, so the guard can run there.
- The tmux and nohup commands it prints now pass `CAMERAS` and `TERRAIN` explicitly, so each job uses the terrain that was checked.

**Tests** (script headers cut just after the guard; same results for both scripts):

| TERRAIN | Output | Result |
|---|---|---|
| legacy | `eevee_full_1000/renders` | passes, exit 0 |
| legacy | other folder | passes, exit 0 |
| new (run_v2 `terrain_render`) | `eevee_full_1000/renders` | **refused, exit 1**, with the message |
| new | other folder | passes, exit 0 |
| defaults (nothing set) | default (`eevee_full_1000`) | **refused, exit 1**, the case flagged in step 19 |
| legacy, relative path with trailing `/` | `eevee_full_1000/renders` | passes |

## 2. Cache folder name in `run_orbits_melbourne.sh`

**Change:** the folder name is now built as `f'{float(spacing):g}m'` through `conda run -n orbits python`, the same formatting `terrain_cache.py` uses. An invalid value now stops the script, because of `set -e`. In the README sentence, "written as in the folder name, e.g. `1` or `2.5`" now reads "`1`, `1.0` and `1.00` all use the same `1m/` cache folder". `bash -n` passes.

**Tests:** I ran the script's own terrain lines, extracted as they are, with `--dry-run` added, on run_v2:

| TERRAIN_SPACING | Folder | terrain_cache.py | terrain_to_render.py |
|---|---|---|---|
| `1` | `1m` | 570 cached, 0 to process | 0 to convert, 570 present, 13,797,886 triangles |
| `1.0` | `1m` | 570 cached, 0 to process | same |
| `1.00` | `1m` | 570 cached, 0 to process | same |

All three exited with 0.

## Diffs

**`run_orbits_melbourne.sh`**
```diff
+# Cache folder name formatted exactly as terrain_cache.py does (1, 1.0 and 1.00 -> 1m).
+TERRAIN_CACHE_DIR="$PROJECT_DIR/data/terrain/melbourne_2020/$(conda run -n orbits python -c \
+    "import sys; print(f'{float(sys.argv[1]):g}m')" "$TERRAIN_SPACING")"
 ...
-    --cache "$PROJECT_DIR/data/terrain/melbourne_2020/${TERRAIN_SPACING}m" --dataset melbourne_2020
+    --cache "$TERRAIN_CACHE_DIR" --dataset melbourne_2020
```

**`scripts/render_job.sh`** (inserted right after `OUTPUT=...`)
```diff
+# eevee_full_1000 is a frozen experiment rendered with the legacy terrain: refuse to add frames
+# to it with any other terrain (the colours differ, see README "Terrain data").
+EEVEE_FULL="$PROJECT_DIR/output/orbits_melbourne/eevee_full_1000"
+output_real="$(realpath -m -- "$OUTPUT")"
+if [[ "$output_real" == "$(realpath -m -- "$EEVEE_FULL")" || "$output_real" == "$(realpath -m -- "$EEVEE_FULL")"/* ]] \
+        && [[ "$(realpath -m -- "$TERRAIN")" != "$(realpath -m -- "$EEVEE_FULL/terrain_render_legacy")" ]]; then
+    echo "Output $OUTPUT is inside eevee_full_1000, a frozen experiment rendered with" >&2
+    echo "$EEVEE_FULL/terrain_render_legacy, but TERRAIN is $TERRAIN." >&2
+    echo 'Mixing terrains in one experiment is not allowed: choose another output folder (OUTPUT=...)' >&2
+    echo "or set TERRAIN=$EEVEE_FULL/terrain_render_legacy." >&2
+    exit 1
+fi
```

**`scripts/launch_render_jobs.sh`**
```diff
 OUTPUT="${OUTPUT:-$PROJECT_DIR/output/orbits_melbourne/eevee_full_1000/renders}"
+CAMERAS="${CAMERAS:-$PROJECT_DIR/output/orbits_melbourne/20261109T180310Z_run_v2/simulation/cameras.json}"
+# Same TERRAIN default as render_job.sh (<run of CAMERAS>/terrain_render), resolved here for the guard.
+TERRAIN="${TERRAIN:-$(dirname -- "$(dirname -- "$CAMERAS")")/terrain_render}"
+<same guard block as render_job.sh>
 ...
-export OUTPUT
+export OUTPUT CAMERAS TERRAIN
 ...
-    echo "tmux new-session -d -s render_job$i \"OUTPUT='$OUTPUT' '$JOB' ...
+    echo "tmux new-session -d -s render_job$i \"OUTPUT='$OUTPUT' CAMERAS='$CAMERAS' TERRAIN='$TERRAIN' '$JOB' ...
-    echo "OUTPUT='$OUTPUT' nohup '$JOB' ...
+    echo "OUTPUT='$OUTPUT' CAMERAS='$CAMERAS' TERRAIN='$TERRAIN' nohup '$JOB' ...
```

**`README.md`**
```diff
-`terrain_cache.py` processes and `TERRAIN_SPACING` (default `1`, written as in
-the folder name, e.g. `1` or `2.5`) the cache spacing in metres;
+`terrain_cache.py` processes and `TERRAIN_SPACING` (default `1`; `1`, `1.0` and
+`1.00` all use the same `1m/` cache folder) the cache spacing in metres;
```

`launch_render_jobs.sh` wasn't on step 19's list of files I may change; I edited it because this step names it.