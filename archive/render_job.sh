#!/usr/bin/env bash
# One render process: EEVEE (8 TAA samples, step 6c settings) bound to one GPU through the
# Vulkan backend, looping over its satellites one Blender invocation each, with
# --frames 0:1000 --skip-existing. Relaunching the same command resumes where it stopped.
# Usage: scripts/render_job.sh NVSMI_GPU "SATELLITES" [--check-only]
#   NVSMI_GPU   physical GPU as numbered by nvidia-smi
# (Superseded by scripts/render_pool.py, which uses every GPU with a shared task queue.)
#   SATELLITES  space-separated satellite ids, e.g. "0 1 2 3"
# Environment: OUTPUT (renders folder), CAMERAS, TERRAIN (default <run of CAMERAS>/terrain_render),
#   BLENDER_BIN, FRAMES (default 0:1000).
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BLENDER_BIN="${BLENDER_BIN:-/snap/blender/current/blender}"
CAMERAS="${CAMERAS:-$PROJECT_DIR/output/orbits_melbourne/20261109T180310Z_run_v2/simulation/cameras.json}"
# Default terrain: the render grids of the run that owns CAMERAS (<run>/simulation/cameras.json
# -> <run>/terrain_render, written by terrain_to_render.py). eevee_full_1000 used the legacy
# terrain: TERRAIN=$PROJECT_DIR/output/orbits_melbourne/eevee_full_1000/terrain_render_legacy.
if [[ -z "${TERRAIN:-}" ]]; then
    TERRAIN="$(dirname -- "$(dirname -- "$CAMERAS")")/terrain_render"
    if [[ ! -d "$TERRAIN" ]]; then
        echo "Terrain folder not found: $TERRAIN" >&2
        echo 'Run terrain_to_render.py for this cameras.json or set TERRAIN to a render terrain folder.' >&2
        exit 1
    fi
fi
OUTPUT="${OUTPUT:-$PROJECT_DIR/output/orbits_melbourne/eevee_full_1000/renders}"
# eevee_full_1000 is a frozen experiment rendered with the legacy terrain: refuse to add frames
# to it with any other terrain (the colours differ, see README "Terrain data").
EEVEE_FULL="$PROJECT_DIR/output/orbits_melbourne/eevee_full_1000"
output_real="$(realpath -m -- "$OUTPUT")"
if [[ "$output_real" == "$(realpath -m -- "$EEVEE_FULL")" || "$output_real" == "$(realpath -m -- "$EEVEE_FULL")"/* ]] \
        && [[ "$(realpath -m -- "$TERRAIN")" != "$(realpath -m -- "$EEVEE_FULL/terrain_render_legacy")" ]]; then
    echo "Output $OUTPUT is inside eevee_full_1000, a frozen experiment rendered with" >&2
    echo "$EEVEE_FULL/terrain_render_legacy, but TERRAIN is $TERRAIN." >&2
    echo 'Mixing terrains in one experiment is not allowed: choose another output folder (OUTPUT=...)' >&2
    echo "or set TERRAIN=$EEVEE_FULL/terrain_render_legacy." >&2
    exit 1
fi
FRAMES="${FRAMES:-0:1000}"
NVSMI_GPU="$1"; SATELLITES="$2"; MODE="${3:-}"
# The Vulkan device order is not the nvidia-smi order in general: unless VK_DEVICE is set, find the
# Vulkan device that sits on this GPU's UUID with the probe (it also checks a given VK_DEVICE below).
EXPECTED_UUID="$(nvidia-smi --query-gpu=uuid --format=csv,noheader -i "$NVSMI_GPU")" || {
    echo "nvidia-smi has no GPU $NVSMI_GPU" >&2; exit 1; }
if [[ -z "${VK_DEVICE:-}" ]]; then
    for candidate in 0 1 2 3 4 5 6 7; do
        if "$BLENDER_BIN" --background --factory-startup --gpu-backend vulkan --gpu-device "$candidate" \
                --gpu-device-no-fallback --python "$PROJECT_DIR/scripts/vulkan_gpu_probe.py" -- "$EXPECTED_UUID" \
                >/dev/null 2>&1; then
            VK_DEVICE="$candidate"; break
        fi
    done
    [[ -n "${VK_DEVICE:-}" ]] || { echo "no Vulkan device runs on nvidia-smi GPU $NVSMI_GPU ($EXPECTED_UUID)" >&2; exit 1; }
fi
GPU_OPTS=(--gpu-backend vulkan --gpu-device "$VK_DEVICE" --gpu-device-no-fallback)
mkdir -p "$OUTPUT" "$OUTPUT/../logs"
LOGS="$(cd -- "$OUTPUT/.." && pwd)/logs"
if [[ "$MODE" == --check-only ]]; then
    for sat in $SATELLITES; do
        echo "check-only: satellite $sat on nvidia-smi GPU $NVSMI_GPU (Vulkan $VK_DEVICE)"
        "$BLENDER_BIN" --background --factory-startup "${GPU_OPTS[@]}" --python-exit-code 1 \
            --python "$PROJECT_DIR/render_swarm.py" -- \
            --cameras "$CAMERAS" --terrain "$TERRAIN" --output "$OUTPUT" \
            --engine EEVEE --samples 8 --coverage-every 50 --frames "$FRAMES" --satellites "$sat" \
            --check-only 2>&1 | grep -E 'Checked|passed|Error|error' || { echo "check-only failed for satellite $sat" >&2; exit 1; }
    done
    exit 0
fi
if ! probe="$("$BLENDER_BIN" --background --factory-startup "${GPU_OPTS[@]}" \
        --python "$PROJECT_DIR/scripts/vulkan_gpu_probe.py" -- "$EXPECTED_UUID" 2>&1)"; then
    echo "$probe" | grep 'GPU probe' >&2 || true
    echo "Vulkan device $VK_DEVICE is not nvidia-smi GPU $NVSMI_GPU ($EXPECTED_UUID); set VK_DEVICE" >&2
    exit 1
fi
echo "$probe" | grep 'GPU probe'
for sat in $SATELLITES; do
    # Disk guard: room for this satellite's missing frames (8.2 MB each) plus 2 GB.
    present=$( (find "$OUTPUT/satellite_$(printf %02d "$sat")" -name 'frame_*.png' 2>/dev/null || true) | wc -l)
    need=$(( (1000-present)*8200000 + 2000000000 ))
    free=$(df -B1 --output=avail "$OUTPUT" | tail -1)
    if (( free < need )); then
        echo "$(date -u +%FT%TZ) satellite $sat: only $((free/1000000)) MB free, need $((need/1000000)) MB; stopping" >&2
        exit 1
    fi
    log="$LOGS/satellite_$(printf %02d "$sat")_$(date -u +%Y%m%dT%H%M%SZ).log"
    echo "$(date -u +%FT%TZ) satellite $sat -> $log"
    "$BLENDER_BIN" --background --factory-startup "${GPU_OPTS[@]}" --python-exit-code 1 \
        --python "$PROJECT_DIR/render_swarm.py" -- \
        --cameras "$CAMERAS" --terrain "$TERRAIN" --output "$OUTPUT" \
        --engine EEVEE --samples 8 --coverage-every 50 --frames "$FRAMES" --satellites "$sat" \
        --skip-existing > "$log" 2>&1
    echo "$(date -u +%FT%TZ) satellite $sat done: $(tail -3 "$log" | grep Rendered || echo 'see log')"
done
echo "$(date -u +%FT%TZ) job on GPU $NVSMI_GPU finished satellites: $SATELLITES"
