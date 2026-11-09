#!/usr/bin/env bash
# Step 7 benchmark: EEVEE (8 TAA samples, step 6c settings), frames 0:20, satellites 0-6,
# coverage every 50th frame, in three process layouts. Each layout gets its own
# <bench>/<config>/renders tree with the PLAN_CONTEXT.md layout. GPU state is sampled
# with nvidia-smi every 500 ms while each layout runs.
#   a_one_process        one process, default OpenGL backend
#   b_two_same_gpu       two processes in parallel, default OpenGL backend (same GPU), 0-3 / 4-6
#   c_one_per_gpu        two processes, Vulkan backend, one per GPU via --gpu-device, 0-3 / 4-6
#   d_two_on_gpu0        (extra, run on request) two Vulkan processes both on nvidia-smi GPU 0, 0-3 / 4-6
#   e_two_per_gpu        (extra, run on request) four Vulkan processes, two per GPU, 0-1 / 2-3 (GPU 0), 4-5 / 6 (GPU 1)
# Usage: scripts/benchmark_step7.sh [BENCH_DIR] [CONFIGS...]
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BLENDER_BIN="${BLENDER_BIN:-/snap/blender/current/blender}"
CAMERAS="${CAMERAS:-$PROJECT_DIR/output/orbits_melbourne/20261109T180310Z_run_v2/simulation/cameras.json}"
TERRAIN="${TERRAIN:-$PROJECT_DIR/data/swarm_terrain}"
BENCH="${1:-$PROJECT_DIR/output/orbits_melbourne/$(date -u +%Y%m%dT%H%M%SZ)_bench_step7}"
shift || true
CONFIGS=("${@:-a_one_process b_two_same_gpu c_one_per_gpu}")
FRAMES="${FRAMES:-0:20}"
# Vulkan enumerates the two TITAN Xp in the opposite order of nvidia-smi:
# Vulkan device 1 = nvidia-smi GPU 0 (bus 41), Vulkan device 0 = nvidia-smi GPU 1 (bus 42, display).
VK_NVSMI_GPU0="${VK_NVSMI_GPU0:-1}"
VK_NVSMI_GPU1="${VK_NVSMI_GPU1:-0}"
mkdir -p "$BENCH"

render() {  # render <config_dir> <log_name> <satellites> [blender GPU options...]
    local dir="$1" log="$2" sats="$3"; shift 3
    "$BLENDER_BIN" --background --factory-startup "$@" --python-exit-code 1 \
        --python "$PROJECT_DIR/render_swarm.py" -- \
        --cameras "$CAMERAS" --terrain "$TERRAIN" --output "$dir/renders" \
        --engine EEVEE --samples 8 --coverage-every 50 --frames "$FRAMES" --satellites "$sats" \
        > "$dir/$log.log" 2>&1
}

for config in ${CONFIGS[@]}; do
    dir="$BENCH/$config"; mkdir -p "$dir"
    nvidia-smi --query-gpu=timestamp,index,memory.used,utilization.gpu --format=csv,noheader,nounits \
        -lms 500 > "$dir/gpu_monitor.csv" &
    gpu_mon=$!
    nvidia-smi --query-compute-apps=timestamp,gpu_uuid,pid,process_name,used_gpu_memory --format=csv,noheader,nounits \
        -lms 500 > "$dir/gpu_apps.csv" &
    app_mon=$!
    start=$(date +%s.%N)
    case "$config" in
        a_one_process) render "$dir" p0 0,1,2,3,4,5,6 ;;
        b_two_same_gpu)
            render "$dir" p0 0,1,2,3 & p0=$!
            render "$dir" p1 4,5,6 & p1=$!
            wait $p0; wait $p1 ;;
        c_one_per_gpu)
            render "$dir" p0 0,1,2,3 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU0" --gpu-device-no-fallback & p0=$!
            render "$dir" p1 4,5,6 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU1" --gpu-device-no-fallback & p1=$!
            wait $p0; wait $p1 ;;
        d_two_on_gpu0)
            render "$dir" p0 0,1,2,3 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU0" --gpu-device-no-fallback & p0=$!
            render "$dir" p1 4,5,6 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU0" --gpu-device-no-fallback & p1=$!
            wait $p0; wait $p1 ;;
        e_two_per_gpu)
            render "$dir" p0 0,1 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU0" --gpu-device-no-fallback & p0=$!
            render "$dir" p1 2,3 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU0" --gpu-device-no-fallback & p1=$!
            render "$dir" p2 4,5 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU1" --gpu-device-no-fallback & p2=$!
            render "$dir" p3 6 --gpu-backend vulkan --gpu-device "$VK_NVSMI_GPU1" --gpu-device-no-fallback & p3=$!
            wait $p0; wait $p1; wait $p2; wait $p3 ;;
        *) echo "unknown config $config" >&2; exit 1 ;;
    esac
    end=$(date +%s.%N)
    kill $gpu_mon $app_mon; wait $gpu_mon $app_mon 2>/dev/null || true
    echo "{\"config\": \"$config\", \"frames\": \"$FRAMES\", \"wall_seconds\": $(echo "$end - $start" | bc)}" > "$dir/wall.json"
    cat "$dir/wall.json"
done
echo "Benchmark folder: $BENCH"
