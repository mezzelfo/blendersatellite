#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MELBOURNE_BLENDER_BIN="${BLENDER_BIN:-/snap/blender/current/blender}"

if [[ ! -x "$MELBOURNE_BLENDER_BIN" ]]; then
    echo "Error: Blender executable not found: $MELBOURNE_BLENDER_BIN" >&2
    echo "Set BLENDER_BIN to the Blender executable path." >&2
    exit 1
fi

cd "$PROJECT_DIR"
"$MELBOURNE_BLENDER_BIN" \
    --background \
    --factory-startup \
    --python render_melbourne.py \
    -- "$@"
