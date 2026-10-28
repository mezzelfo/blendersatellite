#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname -- "$PROJECT_DIR")"
ORBITS_DIR="$PROJECT_DIR/orbits"
ORBITS_RUN_DIR="$ORBITS_DIR/outputs/melbourne-overpass"
MELBOURNE_ARCHIVE_DIR="${MELBOURNE_ARCHIVES:-$WORKSPACE_DIR/OBJ/melbourne_obj}"
TERRAIN_DIR="$PROJECT_DIR/data/swarm_terrain"
MELBOURNE_BLENDER_BIN="${BLENDER_BIN:-/snap/blender/current/blender}"
mkdir -p "$ORBITS_RUN_DIR"
if [[ ! -f "$ORBITS_DIR/src/orbits/cli.py" ]]; then
    echo 'Initialize the orbit submodule: git submodule update --init --recursive' >&2
    exit 1
fi
if [[ ! -d "$MELBOURNE_ARCHIVE_DIR" ]]; then
    echo "Melbourne archive directory not found: $MELBOURNE_ARCHIVE_DIR" >&2
    echo 'Set MELBOURNE_ARCHIVES to the directory containing the downloaded ZIPs.' >&2
    exit 1
fi
# Always import the pinned submodule, even if an old editable installation points
# at a different checkout of orbits in the conda environment.
export PYTHONPATH="$ORBITS_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$ORBITS_DIR"
if [[ ! -f "$ORBITS_RUN_DIR/tile_grid.json" ]]; then
    curl -fsSL 'https://services1.arcgis.com/KGdHCCUjGBpOPPac/arcgis/rest/services/Photomesh_2020_OBJ_Grid/FeatureServer/0/query?where=1%3D1&outFields=Region&returnGeometry=true&outSR=7855&f=json' -o "$ORBITS_RUN_DIR/tile_grid.json"
fi
conda run --no-capture-output -n orbits python scripts/melbourne_overpass.py \
    --grid "$ORBITS_RUN_DIR/tile_grid.json" --output "$ORBITS_RUN_DIR" --device "${ORBITS_DEVICE:-cuda:0}"
conda run --no-capture-output -n orbits python "$PROJECT_DIR/prepare_swarm_terrain.py" \
    --cameras "$ORBITS_RUN_DIR/cameras.json" \
    --archives "$MELBOURNE_ARCHIVE_DIR" --output "$TERRAIN_DIR" --workers "${TERRAIN_WORKERS:-4}" \
    --device "${TERRAIN_DEVICE:-cuda:0}"
"$MELBOURNE_BLENDER_BIN" --background --factory-startup --python-exit-code 1 \
    --python "$PROJECT_DIR/render_swarm.py" -- \
    --cameras "$ORBITS_RUN_DIR/cameras.json" --terrain "$TERRAIN_DIR" \
    --output "$PROJECT_DIR/output/orbits_melbourne" "$@"
