Step 24b is finished; every test passed and every comparison is bit-identical. The full 7-satellite, 1000-frame walls run is now projected at about 3.8 h, down from 4.1 h. Nothing committed.

**Decision for you:** I did not make PNG compression 0 the raw-mode default, although step 3 says to if the gain is real. The gain is real but small (−5% per frame). With it, the raw run's disk check would need about 65 GB against the 39 GB free now, so the production run would refuse to start. Also, the kept reference frames would take 3.6 GB instead of 0.6 GB. The option exists if you want it. Say if you'd rather have 0 as the default anyway.

`Prompts/report_24.md` doesn't exist on disk, so I used the step-24 report from our last session.

## What changed in this step
- **`render_swarm.py`:**
  - `--coverage-every` now defaults to 50, as decided.
  - New `--png-compression` option, default 15 (Blender's own value); it is recorded in the render report header.
- **`scripts/render_to_raw.py`:** the disk check uses 51 MB per PNG instead of 8.5 MB when `--png-compression` is below 10. Unit tests pass (3/3).
- **README:** a note on the CPU side of a frame and the compression trade-off; the sizing paragraph now says when the worker samples its own memory.
- **No change to `render_pool.py`.** The step-24 outputs were deleted first; nothing outside the test folder pointed to them.

## 1. Worker kill, re-test (walls, `--gpus all`)
- I killed the GPU-1 worker after 8 tasks, while it held task `0_04_00000`. Two seconds later the task was back in the queue and the GPU-0 worker finished the run (24 tasks against 4).
- Exit 0, wall 935 s. No `partial_*` file is left in any run.
- All 280 PNGs, all raw files, `bands.json`, the `sat_SS.json` files and the poses are bit-identical to the walls `--gpus 0` run.

## 2. GRID (no walls) vs walls

Both scenes have the same 570 tiles; GRID's terrain is 87 MB against 231 MB with walls.

| Run | Wall time | Workers per GPU | Throughput (frames/s) | Peak GPU memory per worker | Peak RAM per worker | GPU utilisation, mean (p90) |
|---|---|---|---|---|---|---|
| walls `--gpus 0` | 1041 s | 1 | 0.291 | 7368 MiB | 6.5 GB | 18% (73%) |
| walls `--gpus all` (kill test) | 935 s | 1 + 1 | — | 7228–7240 MiB | 6.5 GB | 18% / — |
| GRID (1) `--gpus 0`, 1 worker | 705 s | 1 | 0.423 | 4638 MiB | 3.5 GB | 14% (56%) |
| GRID (2) `--gpus all`, 1 worker | 397 s | 1 + 1 | 0.796 | 4624–4638 MiB | 3.5 GB | 13–14% (60%) |
| GRID (3) `--gpus all`, automatic | 291 s | **2 + 2** | 1.136 | 4462–4638 MiB | 3.4–3.8 GB | 18–21% (61–70%) |

- **Bit-identical:** GRID (1), (2) and (3) match each other completely: 280/280 PNGs and every raw and side file.
- **Run (4) not needed:** run (3) chose 2 workers per GPU, so the second-worker question is answered by (3) itself.
- **Why it stopped at 2:** a third worker had no room, with about 2.8 GiB free per GPU.
- **Steady-state gain:** each worker's frame time rises from 2.50 to about 2.93 s with 2 on a GPU. That gives about 1.37 frames/s against 0.80, **+71%**. The measured +43% is lower because the second workers start after about a minute and the test has only 28 tasks.
- **GPUs mostly idle:** the GPUs show no load in about 65% of the one-second samples, in every configuration.

## 3. CPU side of a frame (walls, one worker, `--gpus 0`)

| PNG compression | Frame time | Render call | PNG write | PNG size | Pixels |
|---|---|---|---|---|---|
| 15 (default) | 3.432 s | 2.404 s | 0.994 s | 7.54 MiB | baseline |
| 0 | 3.264 s | 2.409 s | 0.821 s | 48.08 MiB | 280/280 identical, raw files identical |

- **Only two real settings:** Blender's 0–100 value maps onto zlib levels. I tested 0, 1, 3, 6 and 9 and all write the same 48 MiB uncompressed file. 15 is the fastest setting that compresses at all.
- **The PNG encoding itself costs about 0.55 s per frame** at any setting, so compression is not the main cost of the write.
- **Extraction isn't hurt:** reading 10 uncompressed frames took 2.6 s against 3.1 s compressed.
- **Coverage every 50 frames:** confirmed, the frame went from 3.75 to 3.43 s.
- **Disk cost of compression 0:** the reference frames would grow from 0.6 GB to 3.6 GB, and the PNGs on disk during a raw run would be about 6.4× larger.

## 4. Policy (proposal, not implemented)
- **Memory-based adding is the right default, but it should also require a throughput gain.** In the GRID case a second worker raised throughput by 71% because the work is CPU-bound and the GPUs sit mostly idle. That won't hold everywhere: with a heavier GPU load or the host CPU saturated, a worker that fits in memory might add nothing.
- **Proposed rule:** after a newly added worker's first task, compare the GPU's combined frame rate with the rate before it was added. Keep it, and allow another, only if the rate rose by at least 10%; otherwise freeze the count. This is about 20 lines, which is more than "a few", so I didn't implement it.
- **More workers than GPUs:** this does help in the CPU-bound case, and GRID shows +71%. With walls it isn't possible on these 12 GiB cards: two workers would need about 14.5 GiB. Load average stayed at 5–11 on 24 CPUs, so the CPU count isn't the limit.

## 5. Projection: 7 satellites × 1000 frames, walls, current defaults

This assumes 1 worker per GPU, coverage every 50 frames and PNG compression 15. With both workers running, each takes about 3.88 s per frame: 4.18 s measured in step 24, less the 0.30 s saved by the coverage change. The kill test's GPU-1 worker, measured while both were running, agrees at 3.88 s.

| Scenario | Per worker | Total throughput | 7000 frames + setup and tail | vs step 24 |
|---|---|---|---|---|
| **Walls, chosen defaults** | 3.88 s | 0.515 frames/s | **≈ 13,750 s ≈ 3.8 h** | 4.1 h |
| Walls, compression 0 | ≈ 3.71 s | 0.539 frames/s | ≈ 13,100 s ≈ 3.6 h, but the disk check refuses it here | — |
| GRID, automatic (2 per GPU) | 2.93 s | 1.37 frames/s | ≈ 5,250 s ≈ 1.5 h | — |

**Disk:** the test outputs in `output/render_parallel_test/` take 18 GB, and 39 GB is free on the disk.