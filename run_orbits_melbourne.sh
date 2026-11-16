#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname -- "$PROJECT_DIR")"
ORBITS_DIR="$PROJECT_DIR/orbits"
# Optional folder of already downloaded ZIPs; tiles missing there are downloaded by terrain_cache.py.
MELBOURNE_ARCHIVE_DIR="${MELBOURNE_ARCHIVES:-$WORKSPACE_DIR/OBJ/melbourne_obj}"
TERRAIN_SPACING="${TERRAIN_SPACING:-1}"
MELBOURNE_BLENDER_BIN="${BLENDER_BIN:-/snap/blender/current/blender}"
RUNS_ROOT="${RUNS_ROOT:-$PROJECT_DIR/output/orbits_melbourne}"
FORMATION="${FORMATION:-train}"
TRAIN_SPACING_M="${TRAIN_SPACING_M:-1000}"
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
echo "Formation: $FORMATION; adjacent train spacing: $TRAIN_SPACING_M m"
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
    --train-spacing-m "$TRAIN_SPACING_M"
# Terrain: native-CRS cache shared by every run (downloads only the missing tiles), then the
# render grids in this run's local frame (<run>/terrain_render).
conda run --no-capture-output -n orbits python "$PROJECT_DIR/terrain_cache.py" \
    --dataset melbourne_2020 --tiles-from "$ORBITS_RUN_DIR/cameras.json" \
    --spacing "$TERRAIN_SPACING" --workers "${TERRAIN_WORKERS:-4}" ${ARCHIVE_OPTS[@]+"${ARCHIVE_OPTS[@]}"}
conda run --no-capture-output -n orbits python "$PROJECT_DIR/terrain_to_render.py" \
    --cameras "$ORBITS_RUN_DIR/cameras.json" \
    --cache "$PROJECT_DIR/data/terrain/melbourne_2020/${TERRAIN_SPACING}m" --dataset melbourne_2020
"$MELBOURNE_BLENDER_BIN" --background --factory-startup --python-exit-code 1 \
    --python "$PROJECT_DIR/render_swarm.py" -- \
    --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$RUN_DIR/terrain_render" \
    --output "$RENDER_DIR" "$@"
echo "Completed run: $RUN_DIR"
