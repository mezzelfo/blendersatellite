#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname -- "$PROJECT_DIR")"
ORBITS_DIR="$PROJECT_DIR/orbits"
MELBOURNE_ARCHIVE_DIR="${MELBOURNE_ARCHIVES:-$WORKSPACE_DIR/OBJ/melbourne_obj}"
TERRAIN_DIR="$PROJECT_DIR/data/swarm_terrain"
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
if [[ ! -d "$MELBOURNE_ARCHIVE_DIR" ]]; then
    echo "Melbourne archive directory not found: $MELBOURNE_ARCHIVE_DIR" >&2
    echo 'Set MELBOURNE_ARCHIVES to the directory containing the downloaded ZIPs.' >&2
    exit 1
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
conda run --no-capture-output -n orbits python "$PROJECT_DIR/prepare_swarm_terrain.py" \
    --cameras "$ORBITS_RUN_DIR/cameras.json" \
    --archives "$MELBOURNE_ARCHIVE_DIR" --output "$TERRAIN_DIR" --workers "${TERRAIN_WORKERS:-4}" \
    --device "${TERRAIN_DEVICE:-cuda:0}"
"$MELBOURNE_BLENDER_BIN" --background --factory-startup --python-exit-code 1 \
    --python "$PROJECT_DIR/render_swarm.py" -- \
    --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$TERRAIN_DIR" \
    --output "$RENDER_DIR" "$@"
echo "Completed run: $RUN_DIR"
