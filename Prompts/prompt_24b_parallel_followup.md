Step 24b: follow-up of Prompts/report_24.md. Read it, scripts/render_pool.py, render_swarm.py and
scripts/render_to_raw.py. English comments, no commits. Tests go to output/render_parallel_test/ (delete the
step 24 test outputs there first, after checking nothing outside that folder points to them).

Decisions already taken: keep the PNG Date/RenderTime stamps off; apply --coverage-every 50 as the default.

1. Worker kill, re-test: repeat test (b) of step 24 with the partial_* cleanup in place. No partial_* file
   may remain and results must be bit-identical to the --gpus 0 run.
2. GRID scene (test e of the step 24 prompt, not run in step 24): build a terrain_render of the same
   40-epoch test from the same cache with walls off (terrain_to_render.py without --walls, into
   output/render_parallel_test/grid/). Run, chunk 10, --keep-frames: (1) --gpus 0 --workers-per-gpu 1,
   (2) --gpus all --workers-per-gpu 1, (3) --gpus all with automatic sizing. Report peak GPU memory and
   host RAM per worker for GRID vs walls, the workers per GPU chosen in (3), wall time, and GPU
   utilisation sampled every second. (1), (2), (3) must be bit-identical. If (3) chose 1 worker per GPU,
   also run (4) --gpus all --workers-per-gpu 2 if memory allows, so we see whether a second worker on the
   same GPU raises throughput.
3. CPU side of each frame: report 24 shows the GPU mostly idle and PNG writing at 26% of the frame time.
   Measure, on the walls test, frame time with PNG compression 0 (and with the current setting) and the
   resulting PNG size; pixels must stay identical. If the gain is real, make the compression a
   render_swarm.py option, set the faster value as the default in the raw mode only (where PNGs are
   deleted after extraction), and say what it costs in disk for the kept reference frames.
4. Policy: from the numbers of 2 and 3, say whether "add a worker while memory allows" is the right default
   or whether workers should be added only while measured throughput rises (on the first tasks), and
   whether more workers than GPUs (CPU-bound case) would help. Propose, do not implement, unless the
   change is a few lines.
5. Updated projection for the 7-satellite, 1000-frame walls run with the chosen defaults. Report the diff,
   the tables, the projection. STOP.
