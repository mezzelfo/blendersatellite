# blendersatellite

## Project goal

This project converts [EOGS](https://mezzelfo.github.io/EOGS/) affine camera models (JSON files in `EOGS_affine_models/`) into lightweight Blender camera shaders and renders images of a Blender scene from those camera models. The script generates an OSL (Open Shading Language) camera shader per affine model, configures a Blender camera and light, sets the render resolution, and renders PNG images into the `output/` directory.

At a glance:
- Input: `EOGS_affine_models/<SCENE>/affine_models.json` (list of cameras with `model` and `sun_model` entries)
- Main driver: `main.py` — processes each camera in the chosen scene and triggers a render in Blender
- Output: `output/<camera_name>_render.png`

This repository is mainly a helper for generating Blender renders that match externally-provided affine camera models and sun directions.

## Melbourne OBJ proof of concept

The independently maintained orbit simulator is pinned as the `orbits/`
Git submodule. Clone both repositories together with:

```bash
git clone --recurse-submodules https://github.com/mezzelfo/blendersatellite
```

For an existing clone, use `git submodule update --init --recursive`. After
pulling a parent revision, use `git submodule update --recursive` to check out
its pinned orbit revision. To upgrade intentionally, commit/push orbit changes
inside `orbits/`, then commit the changed `orbits` pointer in this repository.
Do not use `git submodule update --remote` for reproducible rendering unless you
intend to change the pinned version.

Install from the pinned checkout into the existing conda environment:

```bash
conda activate orbits
python -m pip install -e './orbits[test,render]'
```

Prepared terrain under `data/` and renders under `output/` are ignored by Git.
Mesh data is provided by the City of Melbourne under CC BY 4.0; see the
[publisher's dataset](https://discover.data.vic.gov.au/dataset/city-of-melbourne-3d-textured-mesh-photomesh-2020).
Renders and terrain derived from it must carry that attribution.

### Terrain data

The original OBJ ZIPs (570 tiles, 113.67 GB = about 106 GiB) are no longer kept on disk.
`terrain_cache.py` downloads one tile at a time, rasterises the upper surface of
the textured mesh into a grid in the dataset's own projected CRS, samples its
facades into coloured voxels (see "Walls" below), writes a small compressed `.npz`
and deletes the ZIP. The cache does not depend on any orbit simulation and is
shared by every run. There is one cache per dataset and spacing:

```
data/terrain/<dataset>/
    dataset.json        copy of the dataset entry of terrain_datasets.json
    tile_grid.json      tile index (ArcGIS JSON) used to select tiles
    <spacing>m/<tile>.npz   grid (height, rgb, coverage, x0, y0, spacing, ...) and facade voxels
    <spacing>m/log.jsonl    one line per tile, written as each tile completes
```

`terrain_to_render.py` then bins the cache to the render spacing (2.5 m, from
`cameras.json`), adds the walls (`--walls`) and converts everything into the local
ENU frame of one simulation run. These render grids go to `<run>/terrain_render/`
and are tied to that run's local frame (`cache_frame.json` guards against reuse
with another frame or other wall settings). `pipeline/run_swarm.sh` runs both
steps, with walls unless `WALLS=0` (see below); by hand, for an existing run:

```bash
python pipeline/terrain_cache.py --dataset melbourne_2020 --tiles-from <run>/simulation/cameras.json \
    --spacing 1 --workers 12 [--archives /path/to/already/downloaded/zips]
python pipeline/terrain_to_render.py --cameras <run>/simulation/cameras.json \
    --cache data/terrain/melbourne_2020/1m --dataset melbourne_2020 --walls [--wall-threshold 2]
```

Cached tiles are skipped, so a second run downloads nothing (`--dry-run` on either
command shows what would be done). The cell colour is the mean of 4 x 4 sub-cell
texture samples. `--rebuild-log` recreates missing `log.jsonl` lines from the `.npz`
files present.

A cache folder holds one kind of tile: `terrain_cache.py` refuses to add tiles to a
folder whose existing tiles were built differently (with or without facades, spacing,
supersample, facade voxel size), in both directions. `--no-facades` builds grid-only
tiles (no walls possible) for other uses; give such a cache its own folder with
`--tag` (`<spacing>m_<tag>/`, e.g. `--no-facades --tag grid` -> `1m_grid/`) or
`--output`. The pipeline uses neither.

#### Walls from the photomesh facades

The grid is 2.5D: it has no facades, so oblique views of tall buildings show smeared
roof edges. The facade voxels of the cache and `terrain_to_render.py --walls` add
vertical walls coloured from the original facades.

- `terrain_cache.py` (unless `--no-facades`) samples every face with |n_z| < 0.3 (at least 16 samples per
  m^2, barycentric lattice, texel read from the full-resolution texture in the same pass) into a
  global 3D voxel grid of `--facade-voxel` metres (default 1, aligned to multiples of it in the
  dataset CRS). New keys in the same `.npz` (the grid keys are bit-for-bit unchanged):
  `facade_ijk` int32 (N,3) voxel indices (voxel = [i, i+1) x size per axis), `facade_rgb` uint8
  (N,3) area-weighted mean sRGB, `facade_normal` int8 (N,2) = 127 x area-weighted mean of the unit
  horizontal outward normals (shorter than 127 when the voxel holds facades facing different
  ways), `facade_count` uint32 (N,) samples, plus `facade_voxel`, `facade_faces`,
  `facade_area_m2`, `facade_samples`. Walls cannot be stored in the cache because they sit on the edges of the render grid,
  whose spacing is chosen later.
- `terrain_to_render.py --walls` puts a vertical wall on every shared edge between 4-adjacent
  render cells whose heights differ by more than `--wall-threshold` (default 2 m), from the
  lower to the upper height, facing the lower cell, split into segments of height <= the render
  spacing. Segment colour: count-weighted mean of the voxels whose centre is within half a render
  cell of the edge line horizontally, inside the segment's height range, with normal within 60 deg
  of the wall's; else the nearest such voxel within 2 render cells; else the lower cell's colour.
  The neighbouring tiles are loaded as a one-cell halo; a seam edge belongs to the tile holding
  its west or south cell (a cell held by two tiles belongs to the one covering more of it), so
  no wall is duplicated or missing. Extra keys in the render `.npz`: `wall_vertices` float32
  (4 per segment, local frame), `wall_rgb` uint8 (same colour on the 4 corners), `wall_segments`,
  `wall_edges`, `wall_sources`, `wall_source_counts`. `render_swarm.py` adds one `<tile>_walls`
  mesh per tile (two triangles per segment, same emission material) when these keys exist.
- **Renders with and without walls must not be mixed in one experiment.** `cache_frame.json`
  records the wall settings, so `--walls` refuses an output folder made without walls and the
  reverse; convert into a separate folder (e.g. `terrain_render_grid`) to get the other kind.
- `pipeline/run_swarm.sh`: `WALLS=1` (default) passes `--walls --wall-threshold
  "$WALL_THRESHOLD"` to `terrain_to_render.py`; `WALLS=0` converts the same cache as the
  grid only. `WALL_THRESHOLD` (default `2`, metres) is the height step above which a wall is
  built. The choice is printed at the start of `pipeline.log`. Both render modes (frames and
  `RENDER_MODE=raw`) read `<run>/terrain_render` through `render_swarm.py`, so both get the walls.

Validation (step 22, two 3 x 3 blocks of the step-20 study, REF = original photomesh, EEVEE,
mean |dRGB| in DN over all / near-vertical pixels, views 15 and 30 deg off nadir, N and E):

| | CBD near-vertical | CBD nadir (all) | shift | triangles (CBD block) |
|---|---|---|---|---|
| grid only | 20.5-22.0 | 4.19 | up to 0.12 px | 227,808 |
| walls (`--walls`, WALL2) | 11.8-12.9 | 4.19 | <= 0.07 px | 681,638 |
| WALL2_BLOCK (not available as an option) | 7.5-8.3 | 4.43 | <= 0.03 px | 1,039,350 |

SUBURB: near-vertical 8.5-9.7 (grid) -> 6.9-7.2 DN (walls), nadir unchanged (2.41). Segment
colour from voxels / nearest voxel / lower cell: 80.7 / 17.8 / 1.5 % (CBD), 77.8 / 21.1 / 1.0 %
(SUBURB). WALL2_BLOCK (each cell a flat quad at its own height, a wall on every edge with any
height difference) was measured as an alternative and is not the default: it raises the CBD
nadir |dRGB| by +0.24 DN over the grid, while improving facades from 12.7 to 8.0 DN (15 deg N),
and for the 570 tiles it needs 48.2 M triangles and 11.7 GiB of GPU memory (measured, 512 px
window), against 25.7 M triangles and 6.0 GiB with walls and 13.8 M and 3.4 GiB for the grid.

Measured costs for the 570 run_v2 tiles (step 22): `terrain_cache.py` with facades streamed
113.67 GB in 1 h 32 min with 12 workers (peak RSS 2.9 GB, the same as grid only), giving
47.6 M facade voxels and a 583 MB cache (grid only: 236 MB, 75 min in step 18, peak disk
3.8 GB of ZIPs in flight); `terrain_to_render.py --walls` took
8 min (RSS 1.2 GB) for 2,084,927 wall edges, 5,934,979 segments (colour from voxels 76.0 %,
nearest voxel 20.8 %, lower cell 3.2 %), i.e. 25.7 M triangles instead of 13.8 M and 231 MB
instead of 87 MB. A full chief frame (EEVEE, 8 samples) takes 7.1 GiB of GPU memory at peak
(5.7 GiB held by Blender after the render; 3.7 GiB without walls) and 66 s of scene setup.
At nadir the walls are almost invisible: chief frames 0 and 500 differ from the grid-only
renders by 0.03 DN mean |dRGB|, 0.35 % of pixels change by more than 2 DN (around the CBD
towers), and the phase-correlation shift is (0, 0).

Adding a dataset: add an entry to `terrain_datasets.json`.

- `format`: reader in `terrain_cache.READERS`. `obj_zip` expects one ZIP per tile holding
  OBJ files (in each, all `v`/`vt` lines before the faces, metres, z up) with their `.mtl`
  (`mtllib`, else the first MTL of the ZIP) and `map_Kd` textures in the ZIP. One OBJ
  (Melbourne), several sub-tiles (Espoo) or a ContextCapture level-of-detail tree
  `<tile>_L<level>[_<path>].obj` (Helsinki), of which only the leaves are read.
- `mesh_origin` (optional): `[x, y, z]` added to every vertex, for meshes in coordinates
  local to an origin; a `metadata.xml` in the ZIP must declare the same `SRSOrigin`.
- `raster_bounds` (optional): `tile` takes the grid extent of a tile from its index polygon
  (archives cut at the tile edges); default `vertices`, the extent of the mesh.
- `url`: download URL template with `{tile}`; used when the tile is not in `--archives`.
- `projected_epsg`: CRS of the mesh and of the cache grid.
- `geographic_epsg`: geographic CRS of the same datum; `terrain_to_render.py`
  transforms projected -> geographic -> ECEF (EPSG:4979 -> 4978).
- `vertical`: how heights are treated (informational, copied to `cameras.json`; heights are used as ellipsoidal).
- `target`: `projected_xy`, the scene point (in `projected_epsg`) overflown by the chief at capture
  time; required by `pipeline/run_swarm.sh`.
- `index`: tile index for `--bbox`, `--all` and the run selection of `--tiles-from`. Type
  `arcgis`: `url` of an ArcGIS JSON query with polygon `geometry.rings`, `id_field` (attribute
  holding the tile id used in `url`), `local_copy` (path relative to the repository, used
  instead of the URL if present). Type `grid`: square tiles of `tile_size_m` listed in
  `tiles`; `id_regex` has named groups `x` and `y`, and the south-west corner of a tile is
  `x_origin_m + id_unit_m * x`, `y_origin_m + id_unit_m * y`.

Datasets in `terrain_datasets.json`: `melbourne_2020` (570 tiles, 113.7 GB), `helsinki_2017`
(122 tiles of 2 km, 190.4 GB, levels L13-L21, leaves at L21, origin 25490000, 6668000) and
`espoo_2017` (Espoo and Kauniainen, 140 tiles of 2 km, 75.9 GB, origin 25481691, 6680412), both
in EPSG:3879. The Finnish datasets have no `target` yet, which `pipeline/run_swarm.sh` needs.

Truncated archives: 17 of the 122 Helsinki ZIPs on the server are cut (their tail is zero bytes,
no central directory; checked on 2026-11-20, about 22 GB and up to 35 km2 of 250 m sub-tiles
missing). `terrain_cache.py` reads such an archive entry by entry from its local headers
(`SequentialZip`), keeps the entries followed by a valid header (the cut one is left out) and
logs `recovered_entries` for the tile; the sub-tiles after the cut stay empty.

#### Textured, simplified terrain mesh

A city dataset that covers the whole sensor footprint is too large for the render workers with one
coloured vertex per 2.5 m cell: Helsinki + Espoo at 2.5 m was 186 M grid triangles and 216 M wall
triangles (Melbourne: 14 M and 12 M), beyond the host memory of two Blender workers and the 12 GB
of a TITAN Xp. `terrain_to_render.py --texture-spacing T` moves the terrain colour into one texture
per tile (`<tile>_texture.png`, the cache colours binned to T m, row 0 north as any image), and
`--simplify-tolerance E` then merges aligned square blocks of 2 to 128 grid quads whose vertices lie
within E m of a plane (roads, lawns, flat roofs, water) into a few triangles; the boundary keeps
every vertex shared with a neighbouring block, the tile border or a quad not drawn, so the mesh has
no crack, and it stays within 2 E of every grid vertex. `run_swarm.sh` uses `TERRAIN_TEXTURE=1`
(the cache spacing) and `TERRAIN_SIMPLIFY=0.25` by default; `TERRAIN_TEXTURE=0` gives the former
vertex-coloured grid. Walls are unchanged (vertex colours, segments of at most one render cell).
`render_swarm.py` reads either format; `cache_frame.json` keeps them apart.

#### Composite terrain (adjacent datasets)

A composite entry lists datasets in priority order, e.g. `helsinki_espoo_2017` with
`"members": ["helsinki_2017", "espoo_2017"]`; run it with `DATASET=helsinki_espoo_2017`.
The members keep their own caches; `terrain_cache.py --dataset <composite>` caches each member's
tiles. `pipeline/terrain_merge.py` fits, on the cells a member shares with the first member, a
correction of that member towards the first one and writes
`data/terrain/<composite>/<spacing>m/correction.json` (`run_swarm.sh` calls it with `--ensure`,
which also caches the shared-area tiles the first time):

- height: median of the height difference on flat cells per 250 m block, interpolated between
  blocks, in full near the shared area and tapered to zero over 2 km beyond it. The offsets
  between Helsinki and Espoo are local: median +0.09 m, up to about +5 m in the north-east of the
  shared area, coherent between neighbouring blocks. A block more than 2 m away from the median
  of the blocks within 600 m (`--outlier-m`, `--neighbour-m`) is a surface that changed between
  the flights and is left out (listed under `rejected` in `correction.json`);
- colour: per channel gain and offset matching mean and standard deviation (Espoo is about
  10 DN brighter and bluer than Helsinki), applied to the whole member.

`terrain_to_render.py` converts every member's tiles as `<member>__<tile>.npz`, applies the
correction to grids and facade voxels, and where several members have data in a cell keeps the
first member: the other members' grid quads there are dropped through the key `face_keep`, and
the walls follow the same ownership. Every conversion also writes `tiles.json` (the converted
tiles), which `render_swarm.py` loads (runs older than this list them in `cameras.json`).
- `description`, `license`: copied to `dataset.json`.

Renders made with this terrain differ in colour from `eevee_full_1000` (cell mean
instead of one texel per cell: about 12 DN mean |dRGB| on the chief PNGs, no
geometric shift, step 18). Frames rendered with the two terrains must not be mixed
in one experiment. The terrain of `eevee_full_1000` (built by the deprecated
`archive/prepare_swarm_terrain.py`, formerly `data/swarm_terrain`) is kept in
`output/orbits_melbourne/eevee_full_1000/terrain_render_legacy/`; pass it with
`TERRAIN=...` to `archive/render_job.sh` to extend that experiment.

### Orbit-driven HyperScape100 preview

Activate the `orbits` conda environment, then run from this directory:

```bash
bash pipeline/run_swarm.sh                       # DATASET=melbourne_2020 by default
DATASET=<key> bash pipeline/run_swarm.sh         # any dataset of terrain_datasets.json
```

The simulation is the same for every dataset. `DATASET` selects an entry of
`terrain_datasets.json`; its `target.projected_xy` (in the dataset's `projected_epsg`) is the
scene point overflown by the chief at capture time. The wrapper converts it to geographic
lon/lat and passes it to `melbourne_overpass.py --target`; the tiles are then selected by
`pipeline/terrain_cache.py` from the dataset index, as the tiles whose footprint overlaps the
run's `terrain_bounds_local_m` (`run_tiles`). Runs go to `output/<dataset>/`; the earlier
Melbourne runs stay in `output/orbits_melbourne/`.

The wrapper requires CUDA for the orbit integration (default `cuda:0`). Rendering uses every
GPU it finds (see "Rendering on every GPU" below); without any GPU, Cycles falls back to one CPU
worker, while EEVEE stops with an error.
The installed PyTorch build is `2.10.0+cu126`, compatible with the TITAN Xp's
Pascal architecture. To recreate it, install PyTorch before the other dependencies:

```bash
python -m pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cu126
```

`ORBITS_DEVICE` selects the numerical CUDA device;
`GPUS=0` renders on one GPU instead of all (`--gpu-devices` is set per worker by the pool). Orbit
metadata and `render_report.jsonl` record the actual selected devices. Terrain preparation
runs on the CPU: `TERRAIN_WORKERS` (default 4) sets the number of
`terrain_cache.py` processes and `TERRAIN_SPACING` (default `1`; `1`, `1.0` and
`1.00` all use the same `1m/` cache folder) the cache spacing in metres;
`TERRAIN_TEXTURE` and `TERRAIN_SIMPLIFY` choose the terrain mesh (see Terrain data, "Textured, simplified terrain mesh"); `ENGINE` (default `EEVEE`) is passed to Blender as `--engine` unless the arguments give one;
`TERRAIN_ARCHIVES=/path/to/zips` reuses already downloaded tile archives; `MESH_STORE` keeps
every downloaded archive in `$MESH_STORE/<dataset>/<tile>.zip` and reuses it (see Storage);
`OUTPUT_ROOT` (default `output/`) is where the runs go, `$OUTPUT_ROOT/$DATASET/<run>`. `WALLS` (default
`1`) and `WALL_THRESHOLD` (default `2` m) choose the terrain with or without walls
(see Terrain data, "Walls from the photomesh facades"). GPU denoising
is disabled for these small previews so it does not introduce an unsupported
CPU denoising pass.

This runs `orbits/scripts/melbourne_overpass.py` (despite the name, for any target), fills the
terrain cache with the selected tiles and converts them into `<run>/terrain_render` (see Terrain
data), then starts headless Blender with `render_swarm.py`.
The default formation is a five-satellite train with the chief in the middle:
`deputy 1 → deputy 2 → chief → deputy 3 → deputy 4`. Every adjacent pair is
1 km apart in 3D at the capture epoch. Positions and velocities are rotated
about the orbital normal using equal phase increments; the phase step is
`2*asin(1000/(2*orbit_radius))`, giving exactly 1 km chord separation.
The whole formation is propagated backward 30 seconds and forward 60 seconds
with the J2 dynamics, so the saved capture state is from the propagated
trajectory. Separation is not constrained by stationkeeping. Measured pair
distances and adjacent extrema over the sampled minute are saved in each run's
`simulation/formation_distances.json`.

Use `TRAIN_SPACING_M=100000 bash pipeline/run_swarm.sh` for a 100 km train, or
`FORMATION=random bash pipeline/run_swarm.sh` for the former compact random
swarm. The standalone exporter accepts `--formation` and `--train-spacing-m`.

`ACQUISITION=hex7_sync.json bash pipeline/run_swarm.sh` and
`ACQUISITION=hex7_async_10deg.json bash pipeline/run_swarm.sh` fly the ROE hexagon
of `orbits/data/formations/roe_hex7_dmax1km.json` with an acquisition file of
`orbits/data/acquisitions/` (exporter option `--acquisition`). The file sets the
formation file, the mode, the roles, the incidence, the detector-line rule, the
frame period and the number of frames. `sync`: every deputy on the chief's nadir
target, one window. `async`: the chief at its nadir, deputies 2 and 5 on the chief's
nadir target, 1 and 4 forward and 3 and 6 backward along the chief's ground trace
with 10 deg incidence at the target (GroundTrackLead), one window per role centred on
the time the role sees Melbourne (about -11.7 s and +11.3 s). The pointing math is the
one of swarm_simulator (`camera_geometry.aimed_basis` = `camera_rotation`), see the
docstring of `orbits/src/orbits/acquisition.py`. Each camera record carries its own
`t_s`, `acquisition_role`, `target_local_m` and `aim_across_local`; the epoch `t_s` is
the time of the chief. `orbits/scripts/validate_acquisition.py` checks an exported run.

`RENDER_MODE=raw` keeps only the raw pushbroom data instead of the full frames
(`pipeline/render_to_raw.py`): frames 0 and 999 fix the detector rows that see the
scene over the whole window for every satellite, 32 bands of 8 adjacent rows (start
rows multiples of 4) are spread over them, and the run is rendered as tasks of
`RENDER_CHUNK` (default 20) frames of one satellite, the chunks with frames 0 and 999
first. As soon as a task is done (and the band rows are known) its rows are written to
`raw/sat_SS.npy` (uint8 [256, 1000, 4096, 4], raw[i, k, c] = PNG pixel (rows[i], c) of
frame k), read back, compared with the PNGs, and the PNGs are deleted except one reference
frame every 100 (and the last), moved to `reference_frames/`, while the workers go on
rendering. About 4.2 GB per satellite instead of 8.2 GB of PNGs; launching the same command
again resumes from `raw/progress.json` (extracted (satellite, frame) pairs under `done`;
`frames` lists the frames extracted for every satellite, as before).
Example: `ACQUISITION=hex7_async_10deg.json RENDER_MODE=raw bash pipeline/run_swarm.sh --engine EEVEE`.
Do not pass `--frames` or `--satellites` in this mode.
`RAW_KEEP_FRAMES=1` extracts the raw rows but keeps every PNG (no deletion).
`RAW_BLOCK` is gone (replaced by `RENDER_CHUNK`).
`pipeline/build_cubes.py --run RUN --layout raw_bands --mode none|linear|bilinear --source raw|png`
builds the cubes on the ground from either source (identical results); `bilinear` follows the
column drift of oblique (fore/aft) views, `linear` keeps the column fixed, `none` steps whole frames.
#### Rendering on every GPU (`pipeline/render_pool.py`)

Both render modes run persistent Blender workers on every selected GPU, fed by a task queue.
Nothing is specific to one machine: devices are found and identified at start.

- **Devices.** The GPUs are listed with `nvidia-smi` (index, UUID, PCI bus id, memory) when it
  is present, else from the devices Blender reports. Each GPU is mapped to Blender's device by
  identity, never by position. EEVEE: one Blender probe per Vulkan device
  (`pipeline/vulkan_gpu_probe.py --json`, run with `--gpu-backend vulkan --gpu-device N
  --gpu-device-no-fallback`) renders a 64 px image and reads its own UUID from `nvidia-smi`;
  software devices (llvmpipe) are dropped. On osiride (Blender 5.3) Vulkan 0 = nvidia-smi GPU 1
  and Vulkan 1 = GPU 0, Vulkan 2 = llvmpipe. Without Vulkan, OpenGL gives one worker on the
  device the driver picks. Cycles: the device ids carry the PCI bus id
  (`CUDA_NVIDIA TITAN Xp_0000:41:00`), matched with `nvidia-smi`; each worker gets
  `--gpu-devices <id>` (`render_swarm.py --gpu-devices` now takes indices, ids or `cpu`).
  No GPU: one CPU Cycles worker (EEVEE needs a GPU).
- **Options** (`render_to_raw.py` and `render_pool.py`; environment of
  `pipeline/run_swarm.sh` in brackets): `--gpus all|LIST` (`GPUS`, default all; nvidia-smi
  index or UUID, `bN` = Blender device N), `--device-map GPU=BLENDER_DEVICE,...` (`DEVICE_MAP`)
  where the probe cannot identify a device, `--max-workers-per-gpu` (`MAX_WORKERS_PER_GPU`,
  default 4), `--workers-per-gpu N` (`WORKERS_PER_GPU`, fixed number, no measuring),
  `--chunk` (`RENDER_CHUNK`, default 20 frames), `--gpu-memory-margin` (MiB, default 1024),
  `--ram-margin` (MiB, default 4096), `--min-worker-gain` (`MIN_WORKER_GAIN`, default 0.10:
  the frame-rate gain an added worker must bring), `--workers` (extraction processes, default the cores not
  taken by render workers).
- **Queue** (`renders/queue/`): `pending/`, `claimed/` (a worker claims a task by an atomic
  rename), `done/` (marker with the task's poses and timings), `extracted/` (raw mode: written
  after the rows are verified, before the PNGs are deleted), `workers/` (status), `logs/`
  (one Blender log per worker), `stop/` (the driver's request to a worker to exit after its
  current task), `events.jsonl` (worker start/exit, sizing decisions,
  extraction timings), `config.json` (chunk and selection; a resume must use the same). Each
  worker (`render_swarm.py --task-queue`) loads the scene once (about 65 s) and claims tasks
  until none is left, so faster GPUs take more tasks. A dead worker's task goes back to
  `pending/` and the others finish the run; if every worker dies the driver exits non-zero.
  Rerunning the same command resumes; workers of a killed driver notice it at the next frame
  and exit, and the new driver stops any that are left.
- **Workers per GPU.** One worker per GPU first. After its first task the driver takes the
  worker's peak GPU memory (`nvidia-smi` per process, sampled by the worker after each task
  and every `--coverage-every` frames, and by the driver every 2 s; else the drop of the
  device's free memory) and adds a worker on
  that GPU while (free memory - margin) > memory per worker, up to `--max-workers-per-gpu`,
  the CPU count and the host memory. The free memory is the smaller of the current reading
  and (free before the first worker - workers x peak), because an EEVEE worker's memory swings
  within every render (2.5 to 7.2 GiB with walls). Memory is necessary but not enough: an
  added worker is on trial. Once it has finished its first task and every older worker of
  that GPU has finished a task claimed after the new worker's first claim, the driver compares
  the GPU's frame rate (the sum over its workers of frames / render time of their last 3
  tasks; a task's time runs from claim to done marker, so setup never counts) with the rate
  just before the worker was added. That rate leaves out each worker's first task (about 6%
  slower, first-frame warm-up), so a worker is added only after the previous one has finished
  its second task. If the rate rose by at least `--min-worker-gain` (default
  0.10, +10%) the worker stays and the memory check may add the next one; otherwise the driver
  writes `queue/stop/WORKER`, the worker exits after the task it is rendering (in practice its
  second), and the count of that GPU is final. `--workers-per-gpu N` skips both checks. Each
  decision goes to the log (pipeline.log) and `events.jsonl`, with the rates before and after
  (total and per worker). With walls on a 12 GiB TITAN Xp memory allows one worker per GPU;
  with the GRID scene (4.6 GiB per worker) two, and the second raises the rate by about 70%.
- **Results do not depend on the hardware**: the same PNGs and raw files bit for bit whatever
  the number of GPUs and workers (checked in step 24). For this `render_swarm.py` no longer
  writes the render date and render time into the PNG metadata (Blender's default text chunks
  `Date`, `RenderTime`); the pixels of earlier runs are unchanged, only those two chunks are
  gone. PNGs are written under a temporary name
  and renamed, so a killed worker never leaves a truncated frame. `poses.jsonl` is written by
  the driver at the end, sorted by frame; `render_report.jsonl` has one header per worker
  (with `worker_id`) and its frame lines in completion order.
- **CPU side of a frame** (walls, one worker, step 24b): 3.43 s = render call 2.40 + PNG write
  0.99 + rest 0.03. `render_swarm.py --coverage-every` defaults to 50 (the PNG reload for the
  alpha coverage and the `nvidia-smi` snapshot cost 0.25 s when done every frame).
  `--png-compression` (default 15, as Blender) sets the PNG zlib level: Blender maps 0..100 to
  zlib 0..9 and every value below 10 stores the image uncompressed (48 MiB per frame instead of
  7.5 MiB) for 0.17 s less per frame (-5%); pixels and raw files are identical. The raw mode
  keeps 15: with 0 its PNGs in flight and the reference frames take 6.4x the disk.
- **Frames mode** (`RENDER_MODE` unset) runs `pipeline/render_pool.py`, the same pool without
  extraction; `--frames`, `--satellites` and `--crop` after the wrapper are honoured,
  `--check-only` runs one process as before. `render_swarm.py` alone still works as before
  (`--frames` now defaults to every epoch). `archive/render_job.sh` is superseded; it now finds
  its Vulkan device with the probe instead of a fixed osiride mapping.

Every invocation creates a fresh directory such as
`output/<dataset>/20261028T171037Z_run_8bJvD7/`; the UTC timestamp plus
unique suffix prevents collisions even for simultaneous runs. `RUNS_ROOT`
changes the root directory; passing `--output` to the wrapper is disallowed.
Existing runs are never overwritten. Each directory contains:

- `simulation/`: trajectories, configuration, initial camera poses, tile grid,
  and formation distances.
- `renders/`: `satellite_00.png` through `satellite_04.png`, final camera poses,
  and the render report.
- `pipeline.log`: run settings, selected GPU devices, validation and render log.

The terrain cache and the downloaded tile grid under `data/` remain shared across
runs; each run also gets `terrain_render/` with its render grids. The older flat output directory and simulation outputs are preserved.
Standalone exporter runs without `--output` also create unique timestamped
folders. Explicit exporter/renderer paths refuse to overwrite prior artifacts.
Satellite 0 is the chief. The
initial poses at a common epoch, footprint corners, physical optics, and sensor
crop calibration are in the run's `simulation/cameras.json`;
the actual 61-step propagated trajectories are saved alongside it. The WGS84
target is computed analytically in ECEF at zero ellipsoidal height (semi-major
axis 6378137 m, inverse flattening 298.257223563), using the nearest forward ray
intersection. Rendering does not use a mesh or terrain hit to choose the target.
The renderer independently recomputes this target, and final poses are saved in
the run's `renders/camera_poses.json` and referenced by the render report.

The cameras are ordinary perspective cameras with a 580 mm lens and 95 mm
aperture (f/6.1), using a roughly 2.97 mm central sensor crop to obtain 512×512
images at nominal 5 m/pixel near 500 km altitude. Depth of field is disabled.
This is an RGB instantaneous-frame approximation of the HyperScape100
pushbroom instrument, not a hyperspectral or detector-noise simulation.
Specifications: <https://simera-sense.com/products/hyperscape100/>.

The chief remains geocentric nadir (camera -Z points at Earth's center), with
image +Y following projected ground-track velocity. By default each deputy
targets the chief's ray intersection with the WGS84 ellipsoid. Deputy roll
is chosen so the ground-projected image +X (across-track) direction matches the
chief's direction and sign. Projection here means the derivative of neighboring
camera rays intersecting the ground plane, including oblique perspective shear.
For forward direction `f` and image-right axis `r`, this derivative is proportional
to `r - f*r.z/f.z`. If `d` is the chief's unit ground across-track direction,
the deputy's right axis is `normalize(d - f*dot(d,f))`. This makes the projected
across-track direction a positive multiple of `d` and fixes the remaining roll.
The chief's attitude is never modified. Set `POINTING_MODE=nadir` to restore
independent nadir pointing for comparison.

The image center GSD remains 5 m/pixel in the across-track ground direction;
along-track GSD can differ slightly under oblique viewing and is recorded in
each pose. Every render validates the common target's image-center projection
and the across-track alignment. The fixed-pole orbit model uses GMST with
UTC approximating UT1 for its Earth-fixed rotation. Melbourne coordinates
are converted from EPSG:7855 into a shared local ENU frame in double precision.
For this preview, AHD heights approximate ellipsoidal heights; precision Earth
orientation, datum/geoid corrections, and attitude dynamics remain future work.

To bound memory and disk use, `terrain_cache.py` streams each ZIP and keeps
only the upper surface of the mesh (highest face per sub-cell, mean colour per
cell); `terrain_to_render.py` bins it to 2.5 m render grids. Unsampled cells use
the nearest sample; nonfinite source UVs receive neutral gray. This upper-surface
mesh simplifies façades/overhangs. Its emission material preserves illumination
already baked into the aerial textures. Images do not simulate a new Sun
position; transparent pixels identify missing geometry at tile boundaries or
outside the published dataset. The deprecated `archive/prepare_swarm_terrain.py` (one
texel per 2.5 m cell, read from already downloaded ZIPs) is kept only to
reproduce `eevee_full_1000` and for `archive/test_swarm_terrain.py`.

`TERRAIN_WORKERS=1 bash pipeline/run_swarm.sh --samples 4` reduces resource
use; `BLENDER_BIN=/path/to/blender` selects another build. Subsequent runs
reuse terrain caches. Direct stage commands accept `--help` (Blender arguments
follow `--`). `render_swarm.py --check-only` validates the five exported
footprints using Blender's projection without loading terrain. Every real
render also checks the footprint corners and 5 m-to-one-pixel calibration.

### Jacksonville affine-camera preview

The Melbourne affine path reuses the Jacksonville affine viewing geometry and its
azimuth/elevation sun metadata with one textured Melbourne photomesh tile. The
source OBJ uses EPSG:7855 metre coordinates; it must be recentered before Blender
imports it so that Blender's float mesh representation does not lose local
precision.

The checked proof of concept uses CBD tile `C4-22` and only three 192-pixel,
four-sample views:

```bash
mkdir -p data/melbourne
unzip ../OBJ/melbourne_obj/C4-22.zip -d data/melbourne
python3 affine/prepare_melbourne_tile.py
./affine/run_melbourne.sh
```

The preparation step creates `data/melbourne/C4-22/C4-22.local.obj` and an exact
`C4-22.local.transform.json` sidecar. It subtracts the tile's XYZ bounding-box
center and uses the JAX_068 scale (226.386 metres per normalized unit). East,
north, and up already agree between the datasets, so the default Z rotation is
zero; `--rotation-degrees` is available for experiments.

The render step imports the MTL/JPG textures, makes the photomesh opaque, and
writes three images plus `render_metadata.json` to
`output/melbourne_C4-22_poc/`. It accepts Blender arguments after the wrapper,
for example:

```bash
./affine/run_melbourne.sh --resolution 256 --samples 2 --camera-indices 0,6,13
```

Set `BLENDER_BIN=/path/to/blender` when Blender is installed elsewhere. OSL
custom cameras require the Cycles renderer; these validation renders use CPU
Cycles deliberately.

## Important repository layout

- `main.py` — the Blender script that reads affine models, converts them to OSL camera code, attaches the OSL to the `Camera` data-block, configures a `Light`, and renders.
- `affine/prepare_melbourne_tile.py` — recenters/scales a georeferenced Melbourne OBJ and records the transform.
- `affine/render_melbourne.py` — imports the textured OBJ and renders selected affine views.
- `affine/run_melbourne.sh` — headless Blender wrapper for the Melbourne renderer.
- `EOGS_affine_models/` — per-scene subfolders (e.g. `JAX_068`, `IARPA_001`, ...) each containing `affine_models.json`, `train.txt`, `test.txt`.
- `output/` — target folder where rendered PNGs are written.

Orbit-driven swarm pipeline:

- `terrain_datasets.json`: terrain datasets (download URL, CRS, tile index, target point).
- `pipeline/`: the pipeline. Entry point: `run_swarm.sh` (orbit simulation, terrain download and
  conversion, rendering). Composite terrains: `terrain_merge.py`. Terrain: `terrain_cache.py` (downloads and caches the mesh tiles),
  `terrain_to_render.py` (render grids in the run's local frame). Rendering: `render_swarm.py` (Blender),
  `render_pool.py` (workers on every GPU), `render_to_raw.py` (raw mode), `vulkan_gpu_probe.py`.
  Cubes: `build_cubes.py`, `cube_stacking.py`, `stacking_geometry.py`, `export_cube_band_samples.py`.
- `validation/`: validation and analysis scripts of the single steps (they import from `pipeline/`).
- `tests/`: tests of the pipeline, `python -m pytest tests/ -q` in the conda environment `orbits`.
- `archive/`: superseded scripts and the terrain 3D study of step 20, kept for reference; their paths
  are not updated and they may not run from there.
- `Prompts/`: step-by-step prompts and reports of the development (historical paths).

## Storage

The pipeline needs three kinds of space, which can live on different disks:

- terrain caches, `data/terrain/<dataset>/<spacing>m` (small, read by every run): `data` can be a
  symbolic link to a fast disk, e.g. `ln -s /media/SSD2/pareschi/blendersatellite/data data`;
- runs, `$OUTPUT_ROOT/<dataset>/<run>` (large while rendering and stacking): `OUTPUT_ROOT` on the
  fast disk; finished runs are moved by hand to the archive disk;
- original mesh ZIPs (large, written once): `MESH_STORE` on the archive disk, e.g.
  `/media/HDD/pareschi/blendersatellite/meshes`; without it the ZIPs are deleted after caching.

`output/` itself stays a folder of the repository (it holds tracked reports of the Melbourne runs).
`MESH_STORE` and `OUTPUT_ROOT` are environment variables, so they can be set once in `~/.bashrc`;
`terrain_cache.py` and `terrain_merge.py --fetch` also read `MESH_STORE`.

## Assumptions and notes

- Blender drivers must run through `run.sh` or `affine/run_melbourne.sh`; do not call
  `main.py` or `render_melbourne.py` with system Python because `bpy` is only
  available inside Blender. `prepare_melbourne_tile.py` is intentionally a
  standalone system-Python preprocessing tool.

- The legacy Jacksonville `run.sh` contains its own local Blender path. The
  Melbourne wrapper defaults to `/snap/blender/current/blender` and can be
  overridden without editing the script:

```bash
BLENDER_BIN=/path/to/blender ./affine/run_melbourne.sh
```

## Requirements

- Blender 5.x with Cycles; the legacy affine path additionally needs OSL support. The Melbourne path was validated
  headlessly with Blender 5.2.1.

- A lightweight Python virtual environment for helper tools (not for running `main.py`) using `uv`.
