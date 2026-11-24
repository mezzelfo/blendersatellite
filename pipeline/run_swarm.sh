#!/usr/bin/env bash
set -euo pipefail
# Swarm overpass of the target point of one terrain dataset, its terrain, and the renders.
# DATASET (default melbourne_2020) is a key of terrain_datasets.json; its 'target' is the scene point
# overflown at capture time. Everything else is the same for every dataset.
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PIPELINE_DIR="$PROJECT_DIR/pipeline"
ORBITS_DIR="$PROJECT_DIR/orbits"
DATASET="${DATASET:-melbourne_2020}"
# Optional folder of already downloaded tile archives (TERRAIN_ARCHIVES); missing tiles are downloaded.
# MESH_STORE (environment, read by terrain_cache.py): folder that keeps the original ZIPs as
# <MESH_STORE>/<dataset>/<tile>.zip; without it the downloaded ZIPs are deleted after caching.
# OUTPUT_ROOT (default <repo>/output): the runs go to $OUTPUT_ROOT/$DATASET/ (RUNS_ROOT overrides it).
TERRAIN_ARCHIVE_DIR="${TERRAIN_ARCHIVES:-}"
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
# Terrain mesh (terrain_to_render.py): TERRAIN_TEXTURE texel size in metres of the per-tile terrain
# texture (default 1, the cache spacing; 0 = colours on the vertices as before), TERRAIN_SIMPLIFY
# height tolerance in metres for merging flat grid blocks (default 0.25; 0 = full grid).
TERRAIN_TEXTURE="${TERRAIN_TEXTURE:-1}"
TERRAIN_SIMPLIFY="${TERRAIN_SIMPLIFY:-0.25}"
MESH_OPTS=()
if [[ "$TERRAIN_TEXTURE" != 0 ]]; then
    MESH_OPTS=(--texture-spacing "$TERRAIN_TEXTURE" --simplify-tolerance "$TERRAIN_SIMPLIFY")
elif [[ "$TERRAIN_SIMPLIFY" != 0 ]]; then
    echo "TERRAIN_SIMPLIFY needs a texture (TERRAIN_TEXTURE > 0): merged cells would lose their colours" >&2; exit 1
fi
# Blender engine (ENGINE, default EEVEE): passed as --engine unless the arguments already give one.
ENGINE="${ENGINE:-EEVEE}"
RENDER_ARGS=("$@")
if [[ " $* " != *" --engine "* && " $* " != *" --engine="* ]]; then RENDER_ARGS=(--engine "$ENGINE" "$@"); fi
BLENDER="${BLENDER_BIN:-/snap/blender/current/blender}"
# Rendering hardware (pipeline/render_pool.py, both render modes): GPUS=all or a list of GPUs
# (nvidia-smi index or UUID), MAX_WORKERS_PER_GPU (default 4) caps the Blender workers per GPU,
# whose number is otherwise chosen from the measured GPU memory; WORKERS_PER_GPU=N fixes it;
# MIN_WORKER_GAIN (default 0.10) the frame-rate gain an added worker must bring to be kept;
# DEVICE_MAP=GPU=BLENDER_DEVICE,... overrides the device probe; RENDER_CHUNK frames per task.
GPUS="${GPUS:-all}"
MAX_WORKERS_PER_GPU="${MAX_WORKERS_PER_GPU:-4}"
POOL_OPTS=(--gpus "$GPUS" --max-workers-per-gpu "$MAX_WORKERS_PER_GPU" --chunk "${RENDER_CHUNK:-20}")
if [[ -n "${WORKERS_PER_GPU:-}" ]]; then POOL_OPTS+=(--workers-per-gpu "$WORKERS_PER_GPU"); fi
if [[ -n "${DEVICE_MAP:-}" ]]; then POOL_OPTS+=(--device-map "$DEVICE_MAP"); fi
OUTPUT_ROOT="${OUTPUT_ROOT:-$PROJECT_DIR/output}"
RUNS_ROOT="${RUNS_ROOT:-$OUTPUT_ROOT/$DATASET}"
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
# Target point of the dataset (geographic lon/lat, full precision) and its height convention.
if ! TARGET_INFO="$(conda run -n orbits python -c "
import sys
sys.path.insert(0, sys.argv[1])
import terrain_cache as tc
datasets = tc.load_datasets()
if sys.argv[2] not in datasets:
    sys.exit('unknown DATASET ' + repr(sys.argv[2]) + '; known: ' + ', '.join(datasets))
lon, lat = tc.target_lon_lat(datasets[sys.argv[2]])
print(repr(lon)); print(repr(lat)); print(datasets[sys.argv[2]].get('vertical', 'not given'))
" "$PIPELINE_DIR" "$DATASET")"; then
    echo "Cannot read the target of dataset '$DATASET' from terrain_datasets.json" >&2
    exit 1
fi
TARGET_LON="$(sed -n 1p <<<"$TARGET_INFO")"
TARGET_LAT="$(sed -n 2p <<<"$TARGET_INFO")"
TERRAIN_VERTICAL="$(sed -n 3p <<<"$TARGET_INFO")"
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
echo "Dataset: $DATASET; target lon $TARGET_LON, lat $TARGET_LAT deg"
echo "Formation: $FORMATION; adjacent train spacing: $TRAIN_SPACING_M m; acquisition: ${ACQUISITION:-none}"
if [[ "$WALLS" == 1 ]]; then
    echo "Terrain: cache spacing $TERRAIN_SPACING m, walls ON (WALLS=1, threshold $WALL_THRESHOLD m)"
else
    echo "Terrain: cache spacing $TERRAIN_SPACING m, walls OFF (WALLS=0, grid only)"
fi
if [[ "$TERRAIN_TEXTURE" != 0 ]]; then
    echo "Terrain mesh: texture $TERRAIN_TEXTURE m, flat blocks merged within $TERRAIN_SIMPLIFY m"
else
    echo "Terrain mesh: vertex colours, full grid (TERRAIN_TEXTURE=0)"
fi
echo "Render arguments: ${RENDER_ARGS[*]}"
echo "Render hardware: GPUS=$GPUS, MAX_WORKERS_PER_GPU=$MAX_WORKERS_PER_GPU${WORKERS_PER_GPU:+, WORKERS_PER_GPU=$WORKERS_PER_GPU}${DEVICE_MAP:+, DEVICE_MAP=$DEVICE_MAP} (decision logged by render_pool below)"
trap 'echo "Run exited with status $?; artifacts: $RUN_DIR"' EXIT
ARCHIVE_OPTS=()
if [[ -n "$TERRAIN_ARCHIVE_DIR" && -d "$TERRAIN_ARCHIVE_DIR" ]]; then
    ARCHIVE_OPTS=(--archives "$TERRAIN_ARCHIVE_DIR")
    echo "Reusing downloaded archives from $TERRAIN_ARCHIVE_DIR"
fi
if [[ -n "${MESH_STORE:-}" ]]; then
    echo "Mesh store: $MESH_STORE/<dataset>/ (original ZIPs kept and reused)"
fi
# Always import the pinned submodule, even if an old editable installation points
# at a different checkout of orbits in the conda environment.
export PYTHONPATH="$ORBITS_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$ORBITS_DIR"
conda run --no-capture-output -n orbits python scripts/melbourne_overpass.py \
    --target "$TARGET_LON" "$TARGET_LAT" --terrain-vertical "$TERRAIN_VERTICAL" \
    --output "$ORBITS_RUN_DIR" --device "${ORBITS_DEVICE:-cuda:0}" \
    --pointing "${POINTING_MODE:-chief-target}" --formation "$FORMATION" \
    --train-spacing-m "$TRAIN_SPACING_M" ${ACQUISITION_OPTS[@]+"${ACQUISITION_OPTS[@]}"}
# Terrain: native-CRS cache shared by every run (selects the dataset tiles inside the run's terrain
# bounds, downloads only the missing ones and builds them
# with facade voxels), then the render grids in this run's local frame (<run>/terrain_render),
# with walls unless WALLS=0. Both render modes below read <run>/terrain_render.
# Cache folder name formatted exactly as terrain_cache.py does (1, 1.0 and 1.00 -> 1m).
TERRAIN_CACHE_DIR="$PROJECT_DIR/data/terrain/$DATASET/$(conda run -n orbits python -c \
    "import sys; print(f'{float(sys.argv[1]):g}m')" "$TERRAIN_SPACING")"
conda run --no-capture-output -n orbits python "$PROJECT_DIR/pipeline/terrain_cache.py" \
    --dataset "$DATASET" --tiles-from "$ORBITS_RUN_DIR/cameras.json" \
    --spacing "$TERRAIN_SPACING" --workers "${TERRAIN_WORKERS:-4}" ${ARCHIVE_OPTS[@]+"${ARCHIVE_OPTS[@]}"}
# Composite dataset (members in terrain_datasets.json): fit the correction of the members towards the
# first one on their shared area, once (data/terrain/$DATASET/<spacing>m/correction.json); no-op otherwise.
conda run --no-capture-output -n orbits python "$PROJECT_DIR/pipeline/terrain_merge.py" \
    --dataset "$DATASET" --spacing "$TERRAIN_SPACING" --ensure --workers "${TERRAIN_WORKERS:-4}" \
    ${ARCHIVE_OPTS[@]+"${ARCHIVE_OPTS[@]}"}
conda run --no-capture-output -n orbits python "$PROJECT_DIR/pipeline/terrain_to_render.py" \
    --cameras "$ORBITS_RUN_DIR/cameras.json" \
    --cache "$TERRAIN_CACHE_DIR" --dataset "$DATASET" ${WALL_OPTS[@]+"${WALL_OPTS[@]}"} \
    ${MESH_OPTS[@]+"${MESH_OPTS[@]}"}
# Both render modes run persistent Blender workers on every selected GPU (pipeline/render_pool.py);
# worker logs are in renders/queue/logs/, the hardware and sizing decisions in this log.
if [[ "${RENDER_MODE:-frames}" == raw ]]; then
    # Keep only the band rows (raw/) and one reference frame every 100; see pipeline/render_to_raw.py.
    conda run --no-capture-output -n orbits python "$PROJECT_DIR/pipeline/render_to_raw.py" \
        --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$RUN_DIR/terrain_render" \
        --output "$RENDER_DIR" --blender "$BLENDER" "${POOL_OPTS[@]}" \
        --n-bands "${RAW_BANDS:-32}" --tdi-rows "${RAW_TDI_ROWS:-8}" \
        --ref-every "${RAW_REF_EVERY:-100}" ${RAW_KEEP_FRAMES:+--keep-frames} -- "${RENDER_ARGS[@]}"
else
    # Every frame as PNG; --frames/--satellites/--crop in "$@" are honoured, --check-only runs one process.
    conda run --no-capture-output -n orbits python "$PROJECT_DIR/pipeline/render_pool.py" \
        --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$RUN_DIR/terrain_render" \
        --output "$RENDER_DIR" --blender "$BLENDER" "${POOL_OPTS[@]}" -- "${RENDER_ARGS[@]}"
fi
echo "Completed run: $RUN_DIR"
