Step 24c: implement the worker-count rule proposed in section 4 of Prompts/report_24b.md. Read it and
scripts/render_pool.py. English comments, no commits.

1. Rule: workers are still added only while GPU memory and host RAM allow. After a newly added worker
   finishes its first task, compare that GPU's combined frame rate (frames/s over a window of recent
   tasks, excluding setup) with the rate before it was added. Keep it and allow another only if the rate
   rose by at least --min-worker-gain (default 0.10); otherwise stop that worker after its current task
   and freeze the count for that GPU. --workers-per-gpu N still overrides everything. Log every decision
   with the rates in pipeline.log and the run report.
2. Tests in output/render_parallel_test/ (delete the step 24b outputs there first):
   a. GRID, --gpus all, automatic: must still end at 2 per GPU, results bit-identical to GRID (1) of 24b
      (rebuild that reference if it was deleted).
   b. Forced rejection: same run with --min-worker-gain 5.0; the second worker on each GPU must be
      stopped after its first task, the run must finish and results must be bit-identical.
   c. Walls, --gpus all: still 1 per GPU, no change in results.
3. README: document the rule and the option. Report the diff, the decision logs and the tables. STOP.
