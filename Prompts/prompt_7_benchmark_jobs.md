Read PLAN_CONTEXT.md. Steps 1-6 (including 6b and 6c) are done. Step 7 only: benchmark
and job scripts. Do NOT launch the full run.

1. Engine: EEVEE (--engine EEVEE, 8 TAA samples, settings of step 6c), coverage every
   50th frame. Render frames 0:20 for satellites 0-6 into a NEW output folder, in three
   configurations: (a) one process; (b) two processes in parallel on the same GPU,
   satellites split 0-3 / 4-6; (c) one process per GPU, if Blender can be bound to a
   chosen GPU. For (c) try --gpu-device and --gpu-backend vulkan; report which works
   headless and which GPU each process actually uses (nvidia-smi). Report throughput in
   renders/hour, GPU utilisation and peak memory per GPU for each configuration.
2. Report: seconds per frame (mean, min, max), peak GPU memory, PNG size per frame
   (mean, min, max), and the extrapolation to 7000 renders: total wall time with the
   fastest configuration of point 1, total disk for 7 satellites (7000 renders) and for
   satellites 0-4 only (5000 renders), stating the uncertainty.
3. Check free disk where output/ lives against the estimate; flag if the margin is < 20%.
   Also report the disk used by the ZIPs (~106 GB, deletable once the step 5 caches exist) and
   the temporary extraction space of prepare_swarm_terrain with the configured workers.
4. Write plain launchers (nohup/tmux, or the scheduler from step 0 if any) for the
   fastest configuration, each process looping over its satellites with --frames 0:1000
   --skip-existing, so that relaunching resumes it. Prefer GPU 0 if (c) works, since
   GPU 1 drives the display. Include a driver script that prints the commands without
   launching them. State the safety margin used for time and memory.
Validation: the launchers pass a dry run (--check-only for each satellite); folder structure
after the benchmark equals PLAN_CONTEXT.md; all benchmark PNGs (20 frames x 7 satellites
per configuration) valid RGBA 4096x3072.
STOP and report; I will launch the jobs myself.
