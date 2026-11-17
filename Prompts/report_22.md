# Step 22 report: stopped at B.3, one pass criterion failed

WALL2 passes two of the three B.3 criteria but misses the shift limit by 0.01 px in two CBD views (0.07 px against "at most 0.06 px"). As the prompt says, I stopped there. Parts C (cost estimate), D (download and full 570-tile run) and E (README) were not started, nothing was downloaded, nothing was committed, and `data/terrain/melbourne_2020/1m` is untouched.

## B.3 pass criteria

| Criterion | Result |
|---|---|
| Near-vertical \|dRGB\| below the step-20 WALL value in every oblique CBD view | ✅ 12.71 / 11.81 / 12.86 / 12.02 DN against 14.20 / 13.20 / 14.72 / 13.61 |
| Shift at most 0.06 px | ❌ **CBD 15 N: +0.07 rows; CBD 30 N: +0.07 rows** (WALL gave 0.06 in both). Every other view, and all of SUBURB, is ≤ 0.05. |
| Nadir \|dRGB\| within 0.1 DN of GRID | ✅ CBD 4.19 vs 4.19; SUBURB 2.41 vs 2.41 |

How to read the failure:
- The shift is measured in steps of 0.01 px, and step 20 noted that this estimator locks to whole pixels.
- WALL2 sits between WALL (0.06) and GRID (0.10–0.12).
- WALL2 has 3.5× more wall triangles than WALL and is closer to REF everywhere, so 0.07 vs 0.06 is a one-step difference.
- **Decision for you:** accept it (say, a limit of 0.07 or 0.10 px) and let me continue with C and D, or treat it as a real failure.

## A. Synthetic test: 35/35 passed (`test_swarm_terrain.py` 4/4)

| Assert | Result |
|---|---|
| Facade voxels exist only on the four walls (exact set), ≥ 16 samples per m², area 2,400 m² | ✅ |
| Colours correct within 1 DN; normals correct within 1/127 | ✅ |
| Shared corner voxel (two walls in one voxel): normal within 60° of both walls | ✅ (its two wall shares come out 54–59 vs 68–73 instead of 63.5 each) |
| 2.5 m walls: every segment coloured from voxels, no fallback; north red below 15 m, green above, no mixed segment; the rest blue (≤ 1 DN) | ✅ 32 edges, 384 segments |
| No walls on flat ground | ✅ |
| Building on the tile seam: exactly one wall, built by the west tile from the east tile's voxels | ✅ |
| Grid arrays identical with and without `--facades`; grid keys identical with and without `--walls` | ✅ |
| `--walls` refuses a folder made without walls, and a cache without facades | ✅ |
| All step-18 tests | ✅ |

Two extra checks:
- Without `--walls`, the refactored `terrain_to_render.py` writes arrays byte-identical to run_v2's `terrain_render` on 5 tiles.
- I had to rebuild the deleted step-20 GRID variants and cameras. Re-rendering GRID from them is pixel-identical to the step-20 renders (10 of 10 images), and the GRID/WALL metrics reproduce step-20's `metrics.json` exactly.

## B.1 Facade caches, 18 tiles (6 in parallel, 179 s wall time)

| | CBD (9 tiles) | SUBURB (9 tiles) |
|---|---|---|
| facade voxels | 1,694,? → per tile 90,920 – 377,630 | 3,721 – 72,563 per tile |
| cache size with / without facades | 1.15 – 3.22 MB / 0.46 – 0.48 MB per tile | 0.36 – 1.02 MB / 0.33 – 0.45 MB |
| time per tile | 33 – 179 s | 9 – 24 s |
| peak RSS | 0.64 – 2.79 GB | 0.23 – 0.45 GB |

- **Totals:** 1,939,316 voxels; 22.1 MB of caches against 7.8 MB without facades (2.8×).
- **Largest tile:** D4-04 has 377,630 voxels, 4.24 M facade faces, 8.8 M samples, takes 179 s and peaks at 2.79 GB RSS. That peak is the same as step 18 without facades, because the rasteriser dominates memory.
- **Grid check:** every key of all 18 tiles is bit-for-bit identical to `data/terrain/melbourne_2020/1m`.

I don't have the exact CBD-only voxel total in front of me; the per-tile figures are in `wall2/data/.../log.jsonl`.

## B.2 Comparison with REF (mean \|dRGB\| all / near-vertical in DN, SSIM, shift in rows, cols)

**CBD (REF ×8)**

| view | GRID | WALL | WALL2 |
|---|---|---|---|
| 0 | 4.19 / 6.21 / .973 / 0, 0 | 4.19 / 6.21 / .973 / 0, 0 | 4.19 / 6.18 / .973 / 0, 0 |
| 15 N | 6.90 / 21.35 / .920 / .10, 0 | 5.85 / 14.20 / .941 / .06, 0 | **5.62 / 12.71 / .946 / .07, 0** |
| 15 E | 6.79 / 20.48 / .920 / .01, −.11 | 5.67 / 13.20 / .944 / 0, −.05 | 5.44 / 11.81 / .950 / 0, −.05 |
| 30 N | 9.62 / 21.96 / .868 / .12, −.01 | 7.50 / 14.72 / .905 / .06, 0 | **6.96 / 12.86 / .919 / .07, 0** |
| 30 E | 9.48 / 21.42 / .869 / .01, −.10 | 7.14 / 13.61 / .913 / 0, −.03 | 6.67 / 12.02 / .926 / .01, −.04 |

**SUBURB (REF ×2)**

| view | GRID | WALL | WALL2 |
|---|---|---|---|
| 0 | 2.41 / 6.29 / .979 | 2.41 / 6.29 / .979 | 2.41 / 6.29 / .979 |
| 15 N | 2.67 / 8.54 / .976 | 2.62 / 7.22 / .976 | 2.60 / 7.15 / .977 |
| 15 E | 2.62 / 8.54 / .976 | 2.55 / 6.98 / .977 | 2.54 / 6.94 / .977 |
| 30 N | 3.05 / 9.73 / .970 | 2.89 / 7.68 / .973 | 2.83 / 7.08 / .974 |
| 30 E | 2.87 / 9.10 / .972 | 2.71 / 7.15 / .976 | 2.67 / 6.92 / .976 |

All SUBURB WALL2 shifts are ≤ 0.05 px.

**Walls, triangles and GPU memory**

| | CBD | SUBURB |
|---|---|---|
| WALL2 wall edges / segments | 48,283 / 226,915 | 12,031 / 28,329 |
| segment colour from voxels / nearest voxel / lower cell | 80.7 % / 17.8 % / 1.5 % | 77.8 % / 21.1 % / 1.0 % |
| step-20 WALL quads without a hit (for comparison) | 14.0 % | 13.5 % |
| wall segments appearing in two tiles | 0 | 0 |
| triangles: GRID / WALL / WALL2 | 227,808 / 325,368 / 681,638 | 227,808 / 252,080 / 284,466 |
| GPU peak − baseline: GRID / WALL / WALL2 | 700 / 727 / 1,018 MiB | 702 / 705 / 703 MiB |
| WALL2 build (9 tiles, from the caches) | 16.8 s, 0.89 GB RSS | 2.7 s, 0.56 GB RSS |

## B.4 WALL2_BLOCK (optional; measured, not acted on)

I built and rendered WALL2_BLOCK together with WALL2, before checking B.3, so these numbers exist although B.3 did not pass:

| | CBD | SUBURB |
|---|---|---|
| near-vertical \|dRGB\|, 15 N / 15 E / 30 N / 30 E | 8.02 / 7.53 / 8.31 / 7.52 DN (best of all variants) | 6.44 / 6.35 / 6.61 / 6.40 DN |
| shift | ≤ 0.03 px | ≤ 0.01 px |
| nadir \|dRGB\| | 4.43 DN (+0.24 vs GRID) | 2.55 DN (+0.14 vs GRID) |
| triangles | 1,039,350 | 714,682 |
| GPU peak − baseline | 1,036 MiB | 1,079 MiB |

WALL2_BLOCK would fail the nadir criterion if it were applied to it. It is not the default.

## Code changes (`git status`: 6 modified, 1 new)

- **`terrain_cache.py`:**
  - new options `--facades`, `--facade-voxel` (default 1 m), `--tag` (1 m with `--tag facades` writes to `1m_facades/`);
  - new `FacadeVoxels` sampler;
  - `--facades` refuses to complete a folder of cached tiles that have no facades.
- **`terrain_to_render.py`:**
  - new options `--walls` and `--wall-threshold`;
  - library functions `render_grid`, `merged_window`, `build_walls`, `colour_segments`;
  - `cache_frame.json` records the wall settings.
- **`render_swarm.py`:** adds an extra `<tile>_walls` mesh when the tile file holds `wall_vertices`; otherwise the code path is the same.
- **Study scripts:**
  - `compare_images.py`: new options `--out`, `--extra-renders`, `--variants`, `--views`, `--mosaic-variants`;
  - `prepare_render.py`: WALL2 variants;
  - new `build_wall2.py`.
- **Tests:** `scripts/test_terrain_cache.py`.

Choices I made that you may want to check:
1. **Seam ownership:** neighbouring tiles' 2.5 m grids can share a column. Such a cell belongs to the tile that covers more of it (ties go to the first tile name). Edges are then assigned by the west/south rule.
2. **Voxel mean:** the stored normal is the plain mean, not renormalised. The colour mean of a segment is weighted by each voxel's sample count.
3. **Sample position:** samples are computed as `a + u(b−a) + v(c−a)`, so a sample on an axis-aligned facade gets its exact coordinate and always lands in the same voxel.
4. **Redundant rule in the prompt:** "\|n_z\| < 0.3, undersides with n_z < −0.3 excluded" — the second part is already implied by the first.

## Files created (study folder `output/terrain_3d_study/20261116T133629Z/`)

| path | size | content |
|---|---|---|
| `wall2/` | 31 MB | `data/…/1m_facades` (18 caches, 22.1 MB), `renders/` (GRID_check, WALL2, WALL2_BLOCK), `compare/` (`metrics.json` and mosaics), `logs/`, `cache_tile.sh` |
| `variants/` | 21 MB | regenerated: GRID, WALL2, WALL2_BLOCK |
| `render_inputs/` | 108 MB | regenerated: the frames, GRID, WALL2, WALL2_BLOCK |

The step-20 `renders/` and `compare/` folders are unchanged.