Step 24: make rendering use whatever hardware is available as fully as possible, with nothing specific to
osiride. Read README.md, Prompts/report_23.md (render times and GPU memory of run
20261117T152130Z_run_Vs0yvl), render_swarm.py, scripts/render_to_raw.py, scripts/render_job.sh and
scripts/vulkan_gpu_probe.py. English comments, no commits. Do not modify existing run outputs; tests go to
output/render_parallel_test/.

Design (ask before deviating):
1. Device discovery: enumerate the GPUs at start (nvidia-smi when present; otherwise the devices Blender
   reports for the chosen backend) and map each one to Blender's device index by UUID or PCI id with the
   probe, never by a hard-coded order. Options: --gpus all|<list> (default all), and an override map for
   systems where the probe cannot identify devices. Works with EEVEE (Vulkan/OpenGL device selection) and
   CYCLES (--gpu-devices); also with no GPU (CPU Cycles) as a degenerate case.
2. Persistent workers with a task queue: render_swarm.py gets a worker mode (e.g. --task-queue DIR): it
   loads the scene once, then repeatedly claims a task atomically (rename of a task file), renders it, writes
   a done marker, and exits when the queue is empty. A task = a chunk of frames (--chunk, default 20) of one
   satellite. The driver (render_to_raw.py) creates the tasks, starts the workers and monitors them. Faster
   GPUs naturally take more tasks. If a worker dies, its claimed task returns to the queue and the run
   continues on the others; if all die, exit non-zero. Rerunning the same command resumes.
3. Workers per GPU from measured memory: start one worker per GPU, read its GPU memory after scene setup
   (per-process figure from nvidia-smi or the device's free memory before/after), then add workers on that
   GPU while (free memory - margin) > per-worker memory, up to --max-workers-per-gpu (default 4) and to the
   CPU count. --workers-per-gpu N overrides. Log the decision.
4. Extraction overlapped with rendering: as soon as a task is done, the driver extracts its raw rows,
   verifies them against the PNGs and handles reference frames / deletion exactly as now. progress.json
   records done (satellite, frame) pairs. CPU workers for extraction default to the free cores.
5. Disk check before starting, as now. Results must not depend on the hardware: the same PNGs and raw files
   bit for bit whatever the number of GPUs and workers.
6. Keep the frames mode of run_orbits_melbourne.sh (render_swarm.py without the driver) working; give it
   the same worker pool if that is simple, otherwise leave it as it is and say so. run_orbits_melbourne.sh:
   pass GPUS / MAX_WORKERS_PER_GPU through; print the hardware decision in pipeline.log. README: document
   all of this.

Tests (on the current machine):
a. Small run: copy the async run's simulation into output/render_parallel_test/ with only the first 40
   epochs of cameras.json (keep selected_tiles) and its terrain_render. Run with --gpus 0 (one worker) and
   with --gpus all (automatic workers), chunk 10, --keep-frames. Report wall time, workers per GPU chosen,
   GPU memory per worker, tasks done per worker, device UUIDs. Every PNG and both raw files must be
   bit-identical between the two runs; if not, report where and STOP.
b. Worker failure: kill one worker during the --gpus all run; the run must finish with identical results.
c. Interruption: kill the driver, rerun the same command; it must finish with identical results.
d. Projected time of a 7-satellite, 1000-frame run with walls on this machine, from the measured rates,
   and the per-task timing breakdown (setup, render, extraction) to show where time goes.
Report the diff, the test tables and the projection. STOP.
