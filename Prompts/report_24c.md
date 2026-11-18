Step 24c is done and all three tests passed. GRID still ends at 2 workers per GPU (+79% and +70% from the second worker). The forced rejection stopped both second workers and the run finished. Walls stays at 1 per GPU. Every run exits 0 and the results are bit-identical. Nothing committed.

## What changed
- **`scripts/render_pool.py`:**
  - **When a worker is added:** the driver logs the GPU's frame rate at that moment and puts the new worker on trial.
  - **The check:** once the new worker has finished its first task, and each older worker on that GPU has finished a task it claimed after that point, the driver compares the GPU's frame rate with the earlier figure.
  - **Rate definition:** frames divided by render time over each worker's last 3 tasks. Task time runs from claim to done marker, so setup never counts.
  - **Keep or stop:** if the gain is at least `--min-worker-gain`, the worker stays and the memory check may add another. Otherwise the driver writes `queue/stop/WORKER` and freezes that GPU's count.
  - **Option:** `--min-worker-gain`, default 0.10; environment variable `MIN_WORKER_GAIN`.
  - **Logging:** every add, keep and stop decision goes to pipeline.log and to `events.jsonl` with the rates before and after, total and per worker. The "Workers per GPU" line also states the threshold.
  - `--workers-per-gpu N` still overrides everything.
- **`render_swarm.py`:** a worker that finds its stop file finishes its current task and exits cleanly.
- **`scripts/render_to_raw.py`:** passes the option through to the pool.
- **README and `run_orbits_melbourne.sh`:** document the rule, the option and the `stop/` folder.

I deleted the step-24b outputs and rebuilt the GRID terrain and the GRID reference run. Unit tests pass (3/3).

**Deviation in (b):** a rejected worker stops after its second task, not its first. The verdict needs a task from the older worker too, and by then the new worker has already claimed its next task. This follows "stop that worker after its current task" in the rule, but not the "after its first task" wording in test (b).

## Decision logs

**(a) GRID, `--gpus all`, automatic:**
```
GPU 0: 4638 MiB per worker, 7535 MiB free, ..., 0.367 frames/s with 1: adding a worker
GPU 1: 4565 MiB per worker, 7306 MiB free, ..., 0.377 frames/s with 1: adding a worker
GPU 0: 1 -> 2 workers: 0.367 -> 0.658 frames/s (+79%, needed +10%): worker s01-gpu0-w2 kept
GPU 1: 1 -> 2 workers: 0.377 -> 0.643 frames/s (+70%, needed +10%): worker s01-gpu1-w2 kept
GPU 0: 2 worker(s); final: 4638 MiB per worker, 2897 MiB free, margin 1024: no room for another worker
GPU 1: 2 worker(s); final: 4624 MiB per worker, 2623 MiB free, margin 1024: no room for another worker
```

**(b) GRID, `--min-worker-gain 5.0`:**
```
GPU 0: 2 worker(s); final: 1 -> 2 workers: 0.359 -> 0.645 frames/s (+80%, needed +500%): worker s01-gpu0-w2 stops after its current task
GPU 1: 2 worker(s); final: 1 -> 2 workers: 0.359 -> 0.634 frames/s (+77%, needed +500%): worker s01-gpu1-w2 stops after its current task
worker s01-gpu0-w2 finished (2 tasks)      [worker log: "stopped by the driver (gain +80.0% < +500%)"]
worker s01-gpu1-w2 finished (2 tasks)
```

**(c) walls, `--gpus all`:**
```
GPU 1: 1 worker(s); final: 7228 MiB per worker, 4643 MiB free, margin 1024: no room for another worker
GPU 0: 1 worker(s); final: 7240 MiB per worker, 4933 MiB free, margin 1024: no room for another worker
```

## Tables

| Run | Wall time | Workers per GPU (end) | Tasks per worker | Throughput (frames/s) | Results |
|---|---|---|---|---|---|
| GRID reference, `--gpus 0`, 1 worker | 709 s | 1 | 28 | 0.421 | reference |
| (a) GRID automatic | 289 s | 2 + 2 | 8 / 6 / 8 / 6 | 1.148 | identical to reference (22/22 checks) |
| (b) GRID, gain 5.0 | 362 s | 1 + 1, second workers stopped | 12 / 2 / 12 / 2 | 0.888 | identical to reference (22/22 checks) |
| (c) walls automatic | 648 s | 1 + 1 | 14 / 14 | 0.497 | 280/280 frames identical to the async run's raw rows |

- **"Identical" for (a) and (b):** all 280 PNGs byte for byte, all raw files, `bands.json`, the `sat_SS.json` files and the poses. No `partial_*` files in any run.
- **Why (c) is checked differently:** step 24b's walls reference was deleted as instructed. So (c) is compared with the production async run's raw data at its band rows for frames 0–39; all 280 frames match.
- **Memory:** a GRID worker peaks at about 4.6 GiB of GPU memory and 3.8 GB of RAM; a walls worker at about 7.2 GiB and 6.5 GB.
- **Per frame:** about 2.9–3.0 s per GRID worker with 2 per GPU, and about 4.0 s per walls worker with 1 per GPU.

## Caveat on the rule
The "before" rate comes from the first worker's first task, which is about 6% slower than its later tasks: 25.2 s against 23.7 s on GRID, 41.2 s against 39.4 s on walls. That inflates the measured gain by about 6 points. At the 10% threshold, a worker that really adds only about 4% would still be kept.

**Fix (not implemented):** compute the "before" rate without the first task, i.e. add the second worker after the first worker's second task. This costs one task of delay, about 25–40 s, per added worker.