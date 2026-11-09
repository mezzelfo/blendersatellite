#!/usr/bin/env bash
# Driver for the full 1000-frame EEVEE render, fastest step 7 layout (e): four processes,
# two per GPU, Vulkan backend. It never launches the renders: by default it checks disk
# space and prints the commands (tmux and nohup variants); with --check-only it runs the
# render_swarm.py --check-only dry run for every satellite of every job through render_job.sh.
# GPU 0 gets four satellites, GPU 1 (which also drives the display) three.
# Usage: scripts/launch_render_jobs.sh [--check-only]
# Environment: OUTPUT, CAMERAS, TERRAIN, BLENDER_BIN as in render_job.sh;
#   JOBS = semicolon-separated "NVSMI_GPU:SATELLITES" entries, one per process
#   (default "0:0 1;0:2 3;1:4 5;1:6"; satellites 0-4 only: "0:0 1;0:2;1:3;1:4").
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${OUTPUT:-$PROJECT_DIR/output/orbits_melbourne/eevee_full_1000/renders}"
JOBS="${JOBS:-0:0 1;0:2 3;1:4 5;1:6}"
JOB="$PROJECT_DIR/scripts/render_job.sh"
LOGS="$(dirname -- "$OUTPUT")/logs"
# Step 7 benchmark: 8.159 MB mean PNG (8.143-8.173 MB); 1000 frames per satellite.
PNG_BYTES=8159000
export OUTPUT
IFS=';' read -r -a jobs <<< "$JOBS"
if [[ "${1:-}" == --check-only ]]; then
    for job in "${jobs[@]}"; do "$JOB" "${job%%:*}" "${job#*:}" --check-only; done
    echo "Dry run passed for all jobs: $JOBS"
    exit 0
fi
satellites=0
for job in "${jobs[@]}"; do for s in ${job#*:}; do satellites=$((satellites+1)); done; done
existing=$( (find "$OUTPUT" -name 'frame_*.png' 2>/dev/null || true) | wc -l)
need=$(( (satellites*1000-existing)*PNG_BYTES ))
mkdir -p "$(dirname -- "$OUTPUT")"
free=$(( $(df -B1 --output=avail "$(dirname -- "$OUTPUT")" | tail -1) ))
margin=$(( need>0 ? 100*(free-need)/need : 100 ))
echo "# Disk: $satellites satellites, $existing PNGs present, need $((need/1000000000)) GB, free $((free/1000000000)) GB, margin ${margin}%"
if (( margin < 20 )); then echo "# WARNING: disk margin below 20%; free space or render fewer satellites (JOBS)"; fi
echo "# Output: $OUTPUT   (logs: $LOGS)"
echo "# Relaunching the same commands resumes: every frame with a valid PNG is skipped."
echo "mkdir -p '$LOGS'"
echo
echo "# Option 1: tmux (attach with: tmux attach -t render_job0)"
i=0
for job in "${jobs[@]}"; do
    echo "tmux new-session -d -s render_job$i \"OUTPUT='$OUTPUT' '$JOB' ${job%%:*} '${job#*:}' 2>&1 | tee -a '$LOGS/job$i.log'\""
    i=$((i+1))
done
echo
echo "# Option 2: nohup"
i=0
for job in "${jobs[@]}"; do
    echo "OUTPUT='$OUTPUT' nohup '$JOB' ${job%%:*} '${job#*:}' >> '$LOGS/job$i.log' 2>&1 &"
    i=$((i+1))
done
echo
echo "# Progress: find '$OUTPUT' -name 'frame_*.png' | wc -l ; tail -f '$LOGS'/job*.log"
echo "# Stop: pkill -f render_job.sh; pkill -f render_swarm.py"
