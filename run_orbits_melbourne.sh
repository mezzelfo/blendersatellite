#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname -- "$PROJECT_DIR")"
ORBITS_DIR="$PROJECT_DIR/orbits"
# Optional folder of already downloaded ZIPs; tiles missing there are downloaded by terrain_cache.py.
MELBOURNE_ARCHIVE_DIR="${MELBOURNE_ARCHIVES:-$WORKSPACE_DIR/OBJ/melbourne_obj}"
TERRAIN_SPACING="${TERRAIN_SPACING:-1}"
# Terrain walls (README "Walls from the photomesh facades"): WALLS=1 (default) adds vertical walls
# coloured from the facade voxels of the cache wherever adjacent render cells differ in height by
# more than WALL_THRESHOLD metres; WALLS=0 renders the same cache as the 2.5D grid only (GRID).
WALLS="${WALLS:-1}"
WALL_THRESHOLD="${WALL_THRESHOLD:-2}"
case "$WALLS" in
    1) WALL_OPTS=(--walls --wall-threshold "$WALL_THRESHOLD") ;;
    0) WALL_OPTS=() ;;
    *) echo "WALLS must be 0 or 1, not '$WALLS'" >&2; exit 1 ;;
esac
MELBOURNE_BLENDER_BIN="${BLENDER_BIN:-/snap/blender/current/blender}"
# Rendering hardware (scripts/render_pool.py, both render modes): GPUS=all or a list of GPUs
# (nvidia-smi index or UUID), MAX_WORKERS_PER_GPU (default 4) caps the Blender workers per GPU,
# whose number is otherwise chosen from the measured GPU memory; WORKERS_PER_GPU=N fixes it;
# DEVICE_MAP=GPU=BLENDER_DEVICE,... overrides the device probe; RENDER_CHUNK frames per task.
GPUS="${GPUS:-all}"
MAX_WORKERS_PER_GPU="${MAX_WORKERS_PER_GPU:-4}"
POOL_OPTS=(--gpus "$GPUS" --max-workers-per-gpu "$MAX_WORKERS_PER_GPU" --chunk "${RENDER_CHUNK:-20}")
if [[ -n "${WORKERS_PER_GPU:-}" ]]; then POOL_OPTS+=(--workers-per-gpu "$WORKERS_PER_GPU"); fi
if [[ -n "${DEVICE_MAP:-}" ]]; then POOL_OPTS+=(--device-map "$DEVICE_MAP"); fi
RUNS_ROOT="${RUNS_ROOT:-$PROJECT_DIR/output/orbits_melbourne}"
FORMATION="${FORMATION:-train}"
TRAIN_SPACING_M="${TRAIN_SPACING_M:-1000}"
# Optional acquisition file (orbits/data/acquisitions/*.json, e.g. hex7_async_10deg.json):
# formation file, sync/async mode, roles, incidence, line rule, frame period, n_frames.
ACQUISITION="${ACQUISITION:-}"
ACQUISITION_OPTS=()
if [[ -n "$ACQUISITION" ]]; then
    ACQUISITION_OPTS=(--acquisition "$ACQUISITION")
fi
for argument in "$@"; do
    if [[ "$argument" == --output || "$argument" == --output=* ]]; then
        echo 'Use RUNS_ROOT to select the output root; every invocation creates a new run folder.' >&2
        exit 1
    fi
done
if [[ ! -f "$ORBITS_DIR/src/orbits/cli.py" ]]; then
    echo 'Initialize the orbit submodule: git submodule update --init --recursive' >&2
    exit 1
fi
mkdir -p "$RUNS_ROOT"
RUNS_ROOT="$(cd -- "$RUNS_ROOT" && pwd)"
RUN_DIR="$(mktemp -d "$RUNS_ROOT/$(date -u +%Y%m%dT%H%M%SZ)_run_XXXXXX")"
ORBITS_RUN_DIR="$RUN_DIR/simulation"
RENDER_DIR="$RUN_DIR/renders"
mkdir -p "$ORBITS_RUN_DIR" "$RENDER_DIR" "$PROJECT_DIR/data"
exec > >(tee "$RUN_DIR/pipeline.log") 2>&1
echo "Run folder: $RUN_DIR"
echo "Formation: $FORMATION; adjacent train spacing: $TRAIN_SPACING_M m; acquisition: ${ACQUISITION:-none}"
if [[ "$WALLS" == 1 ]]; then
    echo "Terrain: cache spacing $TERRAIN_SPACING m, walls ON (WALLS=1, threshold $WALL_THRESHOLD m)"
else
    echo "Terrain: cache spacing $TERRAIN_SPACING m, walls OFF (WALLS=0, grid only)"
fi
echo "Render hardware: GPUS=$GPUS, MAX_WORKERS_PER_GPU=$MAX_WORKERS_PER_GPU${WORKERS_PER_GPU:+, WORKERS_PER_GPU=$WORKERS_PER_GPU}${DEVICE_MAP:+, DEVICE_MAP=$DEVICE_MAP} (decision logged by render_pool below)"
trap 'echo "Run exited with status $?; artifacts: $RUN_DIR"' EXIT
ARCHIVE_OPTS=()
if [[ -n "${MELBOURNE_ARCHIVES:-}" && -d "$MELBOURNE_ARCHIVE_DIR" ]]; then
    ARCHIVE_OPTS=(--archives "$MELBOURNE_ARCHIVE_DIR")
    echo "Reusing downloaded ZIPs from $MELBOURNE_ARCHIVE_DIR"
fi
# Always import the pinned submodule, even if an old editable installation points
# at a different checkout of orbits in the conda environment.
export PYTHONPATH="$ORBITS_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$ORBITS_DIR"
TILE_GRID="$PROJECT_DIR/data/melbourne_tile_grid.json"
if [[ ! -f "$TILE_GRID" ]]; then
    if [[ -f "$ORBITS_DIR/outputs/melbourne-overpass/tile_grid.json" ]]; then
        cp "$ORBITS_DIR/outputs/melbourne-overpass/tile_grid.json" "$TILE_GRID"
    else
        curl -fsSL 'https://services1.arcgis.com/KGdHCCUjGBpOPPac/arcgis/rest/services/Photomesh_2020_OBJ_Grid/FeatureServer/0/query?where=1%3D1&outFields=Region&returnGeometry=true&outSR=7855&f=json' -o "$RUN_DIR/tile_grid.download.json"
        mv "$RUN_DIR/tile_grid.download.json" "$TILE_GRID"
    fi
fi
cp "$TILE_GRID" "$ORBITS_RUN_DIR/tile_grid.json"
conda run --no-capture-output -n orbits python scripts/melbourne_overpass.py \
    --grid "$ORBITS_RUN_DIR/tile_grid.json" --output "$ORBITS_RUN_DIR" --device "${ORBITS_DEVICE:-cuda:0}" \
    --pointing "${POINTING_MODE:-chief-target}" --formation "$FORMATION" \
    --train-spacing-m "$TRAIN_SPACING_M" ${ACQUISITION_OPTS[@]+"${ACQUISITION_OPTS[@]}"}
# Terrain: native-CRS cache shared by every run (downloads only the missing tiles and builds them
# with facade voxels), then the render grids in this run's local frame (<run>/terrain_render),
# with walls unless WALLS=0. Both render modes below read <run>/terrain_render.
# Cache folder name formatted exactly as terrain_cache.py does (1, 1.0 and 1.00 -> 1m).
TERRAIN_CACHE_DIR="$PROJECT_DIR/data/terrain/melbourne_2020/$(conda run -n orbits python -c \
    "import sys; print(f'{float(sys.argv[1]):g}m')" "$TERRAIN_SPACING")"
conda run --no-capture-output -n orbits python "$PROJECT_DIR/terrain_cache.py" \
    --dataset melbourne_2020 --tiles-from "$ORBITS_RUN_DIR/cameras.json" \
    --spacing "$TERRAIN_SPACING" --workers "${TERRAIN_WORKERS:-4}" ${ARCHIVE_OPTS[@]+"${ARCHIVE_OPTS[@]}"}
conda run --no-capture-output -n orbits python "$PROJECT_DIR/terrain_to_render.py" \
    --cameras "$ORBITS_RUN_DIR/cameras.json" \
    --cache "$TERRAIN_CACHE_DIR" --dataset melbourne_2020 ${WALL_OPTS[@]+"${WALL_OPTS[@]}"}
# Both render modes run persistent Blender workers on every selected GPU (scripts/render_pool.py);
# worker logs are in renders/queue/logs/, the hardware and sizing decisions in this log.
if [[ "${RENDER_MODE:-frames}" == raw ]]; then
    # Keep only the band rows (raw/) and one reference frame every 100; see scripts/render_to_raw.py.
    conda run --no-capture-output -n orbits python "$PROJECT_DIR/scripts/render_to_raw.py" \
        --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$RUN_DIR/terrain_render" \
        --output "$RENDER_DIR" --blender "$MELBOURNE_BLENDER_BIN" "${POOL_OPTS[@]}" \
        --n-bands "${RAW_BANDS:-32}" --tdi-rows "${RAW_TDI_ROWS:-8}" \
        --ref-every "${RAW_REF_EVERY:-100}" ${RAW_KEEP_FRAMES:+--keep-frames} -- "$@"
else
    # Every frame as PNG; --frames/--satellites/--crop in "$@" are honoured, --check-only runs one process.
    conda run --no-capture-output -n orbits python "$PROJECT_DIR/scripts/render_pool.py" \
        --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$RUN_DIR/terrain_render" \
        --output "$RENDER_DIR" --blender "$MELBOURNE_BLENDER_BIN" "${POOL_OPTS[@]}" -- "$@"
fi
echo "Completed run: $RUN_DIR"
