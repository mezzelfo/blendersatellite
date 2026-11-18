"""Render with every available GPU: device discovery, a file task queue and persistent Blender workers.

Used by scripts/render_to_raw.py (raw mode) and, as a command, by run_orbits_melbourne.sh (frames
mode). Standard library only.

Devices
    The GPUs are listed with nvidia-smi when it is present (index, UUID, PCI bus id, memory), else
    from the devices Blender reports. Each GPU is mapped to Blender's device index by identity,
    never by position:
      EEVEE   one Blender probe per Vulkan device (scripts/vulkan_gpu_probe.py, run with
              --gpu-backend vulkan --gpu-device N --gpu-device-no-fallback) renders a tiny image
              and reads its own nvidia-smi UUID; software devices (llvmpipe, ...) are dropped.
              Without Vulkan, OpenGL gives one worker on the device the driver chooses.
      CYCLES  the Cycles device ids carry the PCI bus id (CUDA/OptiX), matched with nvidia-smi;
              the worker gets --gpu-devices <id>. No GPU at all: one CPU Cycles worker.
    --gpus all|<list> selects GPUs (nvidia-smi index or UUID; bN = Blender device N);
    --device-map GPU=BLENDER,... replaces the probe where it cannot identify a device.

Queue (directory, default <output>/queue)
    One task = --chunk frames of one satellite. Workers (render_swarm.py --task-queue) load the
    scene once and claim tasks by renaming pending/X.json to claimed/X@WORKER.json; a done marker
    done/X.json holds the poses and timings. Faster GPUs simply claim more tasks. A worker that dies
    has its claimed task moved back to pending/ and the run continues on the others; if every
    worker is dead the run stops with an error. Rerunning the same command resumes: done tasks
    stay done, stale workers of a killed driver are stopped, claimed tasks are requeued.

Workers per GPU
    One worker per GPU first. After its first task the driver reads the worker's GPU memory
    (nvidia-smi per process, else the drop of the GPU's free memory) and starts another worker on
    that GPU while free memory - margin > memory per worker, up to --max-workers-per-gpu, the CPU
    count and the host memory (MemAvailable - margin > worker RSS). --workers-per-gpu N fixes the
    number. Every decision is logged (and written to QUEUE/events.jsonl).

Frames mode (this file as a command): render every frame as PNG with the pool, e.g.
    python scripts/render_pool.py --cameras RUN/simulation/cameras.json --terrain RUN/terrain_render \\
        --output RUN/renders [--gpus all] -- --engine EEVEE --samples 8 [--frames 0:500 --satellites 0,1]
Arguments after '--' go to render_swarm.py; --frames and --satellites select the tasks.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RENDER_SWARM = REPO / 'render_swarm.py'
PROBE = REPO / 'scripts' / 'vulkan_gpu_probe.py'
SOFTWARE_GPU = re.compile(r'llvmpipe|lavapipe|softpipe|swiftshader|software rasterizer', re.I)
DEFAULT_BLENDER = os.environ.get('BLENDER_BIN', '/snap/blender/current/blender')


def log(msg):
    print(f'{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} [pool] {msg}', flush=True)


def write_json(path, data):
    tmp = Path(f'{path}.tmp{os.getpid()}')
    tmp.write_text(json.dumps(data, indent=1) + '\n')
    os.replace(tmp, path)


# ----------------------------------------------------------------------------- hardware

def nvidia_gpus():
    """nvidia-smi GPUs: [dict(index, uuid, pci, name, memory_total_mib, memory_free_mib)], [] without it."""
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,pci.bus_id,name,memory.total,memory.free',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=30,
                             check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.splitlines():
        f = [x.strip() for x in line.split(',')]
        if len(f) == 6:
            gpus.append(dict(index=int(f[0]), uuid=f[1], pci=f[2], name=f[3],
                             memory_total_mib=int(f[4]), memory_free_mib=int(f[5])))
    return gpus


def process_gpu_mib():
    """{pid: MiB} of every compute process from nvidia-smi ({} without it)."""
    try:
        out = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    used = {}
    for line in out.splitlines():
        f = [x.strip() for x in line.split(',')]
        if len(f) == 2 and f[0].isdigit() and f[1].isdigit():
            used[int(f[0])] = used.get(int(f[0]), 0) + int(f[1])
    return used


def gpu_free_mib():
    """{uuid: free MiB} from nvidia-smi ({} without it)."""
    return {g['uuid']: g['memory_free_mib'] for g in nvidia_gpus()}


def pci_key(text):
    """(domain, bus, device) of the last PCI address in a string ('00000000:41:00.0', '..._0000:41:00')."""
    found = re.findall(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})', text or '')
    return tuple(int(x, 16) for x in found[-1]) if found else None


def proc_rss_mib(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def mem_available_mib():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def cpu_count():
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def run_probe(blender, blender_args, extra=()):
    """Run scripts/vulkan_gpu_probe.py --json in Blender; its JSON record, or None."""
    cmd = [blender, '--background', '--factory-startup', *blender_args, '--python', str(PROBE), '--', '--json', *extra]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as error:
        log(f'probe {" ".join(blender_args)} failed: {error}')
        return None
    for line in r.stdout.splitlines():
        if line.startswith('GPU_PROBE_JSON '):
            return json.loads(line[len('GPU_PROBE_JSON '):])
    log(f'probe {" ".join(blender_args)} gave no result (exit {r.returncode}): {r.stdout[-300:]}{r.stderr[-300:]}')
    return None


def parse_device_map(text):
    """'0=1,GPU-xxxx=0' -> {'0': '1', 'GPU-xxxx': '0'} (GPU token -> Blender device index or Cycles id)."""
    out = {}
    for item in filter(None, (x.strip() for x in (text or '').split(','))):
        if '=' not in item:
            raise SystemExit(f'--device-map entry {item!r} is not GPU=BLENDER_DEVICE')
        k, v = item.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def discover(engine, gpu_backend, blender, gpus='all', device_map=None):
    """The devices to render on: list of dict(label, name, uuid, pci, kind, blender_args, render_args,
    memory_total_mib). Raises SystemExit when a requested GPU cannot be mapped."""
    device_map = parse_device_map(device_map) if isinstance(device_map, str) or device_map is None else device_map
    nv = nvidia_gpus()
    nv_by_token = {}
    for g in nv:
        nv_by_token[str(g['index'])] = g
        nv_by_token[g['uuid']] = g
    log('nvidia-smi: ' + ('; '.join(f"GPU {g['index']} {g['name']} {g['uuid']} PCI {g['pci']} "
                                    f"{g['memory_total_mib']} MiB" for g in nv) if nv else 'not available'))

    def mapped_override(g):
        for token in (str(g['index']), g['uuid']):
            if token in device_map:
                return device_map[token]
        return None

    devices, blender_only = [], []
    if engine == 'EEVEE':
        base = run_probe(blender, ['--gpu-backend', 'vulkan'])
        if base and base.get('backend') == 'VULKAN' and base.get('devices'):
            hw = [d for d in base['devices'] if not SOFTWARE_GPU.search(d['name'])
                  and not d['identifier'].startswith('10005/')]          # 0x10005 = Mesa CPU (llvmpipe)
            log('Blender Vulkan devices: ' + '; '.join(f"{d['index']} {d['name']} ({d['identifier']})"
                                                        for d in base['devices'])
                + f" -> hardware {[d['index'] for d in hw]}")
            uuid_of = {}
            overridden = {v for g in nv if (v := mapped_override(g)) is not None}
            to_probe = [d['index'] for d in hw if nv and str(d['index']) not in overridden]
            if to_probe:
                with ThreadPoolExecutor(len(to_probe)) as ex:
                    results = list(ex.map(lambda i: run_probe(blender, ['--gpu-backend', 'vulkan', '--gpu-device',
                                                                        str(i), '--gpu-device-no-fallback']),
                                          to_probe))
                for i, r in zip(to_probe, results):
                    uuids = (r or {}).get('nvidia_uuids') or []
                    log(f'probe Vulkan device {i}: {(r or {}).get("renderer")} -> nvidia-smi UUID {uuids}')
                    if len(uuids) == 1:
                        uuid_of[i] = uuids[0]
            index_of_uuid = {u: i for i, u in uuid_of.items()}

            def vulkan_device(label, index, g=None):
                d = next(d for d in hw if d['index'] == index)
                return dict(label=label, name=d['name'], uuid=g and g['uuid'], pci=g and g['pci'], kind='vulkan',
                            memory_total_mib=g and g['memory_total_mib'], blender_index=index,
                            blender_args=['--gpu-backend', 'vulkan', '--gpu-device', str(index),
                                          '--gpu-device-no-fallback'], render_args=[])
            hw_indices = {d['index'] for d in hw}
            for g in nv:
                override = mapped_override(g)
                if override is not None:
                    if not override.isdigit() or int(override) not in hw_indices:
                        raise SystemExit(f'--device-map: {override} is not a hardware Vulkan device {sorted(hw_indices)}')
                    devices.append(vulkan_device(str(g['index']), int(override), g))
                elif g['uuid'] in index_of_uuid:
                    devices.append(vulkan_device(str(g['index']), index_of_uuid[g['uuid']], g))
                else:
                    devices.append(dict(label=str(g['index']), unmapped=True, uuid=g['uuid'], name=g['name']))
            used = {d.get('blender_index') for d in devices}
            blender_only = [vulkan_device(f"b{d['index']}", d['index']) for d in hw if d['index'] not in used]
        else:
            # No Vulkan: OpenGL renders on the device the driver picks; it cannot be chosen.
            probe = run_probe(blender, ['--gpu-backend', 'opengl'])
            if not probe or probe.get('render_error') or not probe.get('backend'):
                raise SystemExit('EEVEE needs a GPU (no Vulkan or OpenGL device); use --engine CYCLES for CPU rendering')
            uuids = probe.get('nvidia_uuids') or []
            g = nv_by_token.get(uuids[0]) if len(uuids) == 1 else None
            log(f"No Vulkan backend: OpenGL on {probe.get('renderer')} (UUID {uuids}); one GPU only")
            devices.append(dict(label=str(g['index']) if g else 'default', name=probe.get('renderer'),
                                uuid=g and g['uuid'], pci=g and g['pci'], kind='opengl',
                                memory_total_mib=g and g['memory_total_mib'],
                                blender_args=['--gpu-backend', 'opengl'], render_args=[]))
    else:
        probe = run_probe(blender, [], ['--probe-cycles', gpu_backend]) or {}
        cyc = [d for d in probe.get('cycles', []) if d['type'] == gpu_backend]
        log(f'Blender Cycles {gpu_backend} devices: ' + ('; '.join(f"{i} {d['name']} ({d['id']})"
                                                                  for i, d in enumerate(cyc)) or 'none'))
        by_pci = {pci_key(d['id']): d for d in cyc if pci_key(d['id'])}
        cyc_ids = [d['id'] for d in cyc]

        def cycles_device(label, d, g=None):
            return dict(label=label, name=d['name'], uuid=g and g['uuid'], pci=g and g['pci'], kind='cycles',
                        memory_total_mib=g and g['memory_total_mib'], blender_index=cyc_ids.index(d['id']),
                        blender_args=[], render_args=['--gpu-devices', d['id']])
        for g in nv:
            override = mapped_override(g)
            if override is not None:
                d = cyc[int(override)] if override.isdigit() and int(override) < len(cyc) else \
                    next((d for d in cyc if d['id'] == override), None)
                if d is None:
                    raise SystemExit(f'--device-map: {override} is not a Cycles {gpu_backend} device {cyc_ids}')
                devices.append(cycles_device(str(g['index']), d, g))
            elif pci_key(g['pci']) in by_pci:
                devices.append(cycles_device(str(g['index']), by_pci[pci_key(g['pci'])], g))
            else:
                devices.append(dict(label=str(g['index']), unmapped=True, uuid=g['uuid'], name=g['name']))
        used = {d.get('blender_index') for d in devices}
        blender_only = [cycles_device(f'b{i}', d) for i, d in enumerate(cyc) if i not in used]
        if not cyc:
            devices = [dict(label='cpu', name='CPU', uuid=None, pci=None, kind='cpu', memory_total_mib=None,
                            blender_args=[], render_args=['--gpu-devices', 'cpu'])]
            nv, blender_only = [], []
            log(f'No Cycles {gpu_backend} GPU: one CPU Cycles worker')

    # Selection. With nvidia-smi, 'all' = every nvidia-smi GPU that could be mapped (Blender devices
    # that match none are listed but used only when named as bN); without it, every Blender device.
    chosen = []
    if gpus in (None, '', 'all'):
        if nv:
            for d in devices:
                if d.get('unmapped'):
                    log(f"WARNING: GPU {d['label']} ({d['uuid']}) has no Blender device; skipped "
                        f"(use --device-map {d['label']}=N)")
                else:
                    chosen.append(d)
            if blender_only:
                log('Blender devices without an nvidia-smi GPU (use --gpus bN to add): '
                    + ', '.join(f"{d['label']} {d['name']}" for d in blender_only))
        else:
            chosen = [d for d in devices if not d.get('unmapped')] + blender_only
    else:
        pool = {d['label']: d for d in devices + blender_only}
        for d in devices:
            if d.get('uuid'):
                pool[d['uuid']] = d
        for token in [t.strip() for t in str(gpus).split(',') if t.strip()]:
            d = pool.get(token) or (pool.get(f'b{token}') if not nv else None)
            if d is None:
                raise SystemExit(f'--gpus {token}: unknown GPU; available {sorted(k for k in pool if not k.startswith("GPU-"))}')
            if d.get('unmapped'):
                raise SystemExit(f"--gpus {token}: GPU {d['uuid']} could not be identified among Blender's "
                                 f"devices; pass --device-map {token}=<Blender device>")
            if d not in chosen:
                chosen.append(d)
    if not chosen:
        raise SystemExit('no usable device')
    return chosen


def describe(devices):
    return '; '.join(f"GPU {d['label']} = {d['name']}" + (f" {d['uuid']}" if d.get('uuid') else '')
                     + (f" PCI {d['pci']}" if d.get('pci') else '')
                     + (f" -> Blender {d['kind']} device {d.get('blender_index')}" if d['kind'] in ('vulkan', 'cycles')
                        else f" ({d['kind']})") for d in devices)


# ----------------------------------------------------------------------------- queue

class TaskQueue:
    """Directory queue shared with render_swarm.py --task-queue (see run_worker there)."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        for sub in ('pending', 'claimed', 'done', 'workers', 'logs'):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.pending, self.claimed, self.done = self.root / 'pending', self.root / 'claimed', self.root / 'done'

    @staticmethod
    def task_name(sat, start, priority=1):
        # Workers claim in sorted order: priority 0 first.
        return f'{priority}_{sat:02d}_{start:05d}'

    def done_names(self):
        return {p.stem for p in self.done.glob('*.json')}

    def counts(self):
        return dict(pending=len(list(self.pending.glob('*.json'))), claimed=len(list(self.claimed.glob('*.json'))),
                    done=len(list(self.done.glob('*.json'))))

    def add(self, name, task):
        if (self.done / f'{name}.json').exists() or (self.pending / f'{name}.json').exists() \
                or any(self.claimed.glob(f'{name}@*.json')):
            return False
        tmp = self.pending / f'{name}.json.tmp'
        tmp.write_text(json.dumps(task) + '\n')
        os.replace(tmp, self.pending / f'{name}.json')
        return True

    def requeue(self, worker_id=None):
        """Claimed tasks (of one worker, or all) back to pending/; returns their names."""
        names = []
        for p in self.claimed.glob('*.json'):
            name, wid = p.stem.rsplit('@', 1)
            if worker_id not in (None, wid):
                continue
            if (self.done / f'{name}.json').exists():
                p.unlink(missing_ok=True)
            else:
                try:
                    os.rename(p, self.pending / f'{name}.json')
                    names.append(name)
                except FileNotFoundError:
                    pass
        return names

    def read_done(self, name):
        return json.loads((self.done / f'{name}.json').read_text())

    def event(self, kind, **data):
        with open(self.root / 'events.jsonl', 'a') as f:
            f.write(json.dumps(dict(t=time.time(), event=kind, **data)) + '\n')

    def check_config(self, config):
        """Refuse to resume a queue made with another chunk size or selection."""
        path = self.root / 'config.json'
        if path.exists():
            old = json.loads(path.read_text())
            if old != config:
                raise SystemExit(f'{path} was made for {old}, not {config}: use the same options or a new queue')
        else:
            write_json(path, config)


def chunk_tasks(sats, frames_by_sat, chunk, priority_frames=()):
    """[(name, task)] covering the given frames of each satellite in chunks of `chunk` aligned frames.
    Chunks holding a frame of `priority_frames` are claimed first."""
    tasks = []
    for s in sats:
        frames = sorted(frames_by_sat[s])
        starts = sorted({k - k % chunk for k in frames})
        for a in starts:
            stop = a + chunk
            sub = [k for k in frames if a <= k < stop]
            lo, hi = sub[0], sub[-1] + 1
            priority = 0 if any(lo <= k < hi for k in priority_frames) else 1
            tasks.append((TaskQueue.task_name(s, lo, priority), dict(satellite=s, frames=[lo, hi])))
    return sorted(tasks)


def stop_stale_workers(queue):
    """Stop render workers left by a driver that was killed: any process whose command line has
    '--task-queue QUEUE' (they also exit on their own once they notice the driver is gone)."""
    needle = f'--task-queue\0{queue.root}\0'.encode()
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if needle not in (proc / 'cmdline').read_bytes():
                continue
            pid = int(proc.name)
        except OSError:
            continue
        if pid == os.getpid():
            continue
        log(f'stopping stale worker pid {pid} of a previous driver')
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                time.sleep(0.2)
                os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# ----------------------------------------------------------------------------- workers

class PoolFailed(RuntimeError):
    pass


class WorkerPool:
    def __init__(self, queue, devices, command, workers_per_gpu=None, max_workers_per_gpu=4,
                 gpu_margin_mib=1024, ram_margin_mib=4096, max_workers=None, output=None):
        self.queue, self.devices, self.command, self.output = queue, devices, command, output
        self.fixed, self.max_per_gpu = workers_per_gpu, max_workers_per_gpu
        self.gpu_margin, self.ram_margin = gpu_margin_mib, ram_margin_mib
        self.max_workers = max_workers or cpu_count()
        self.workers = []           # dict(wid, device, proc, log, started, alive, measured)
        self.frozen = {}            # device label -> reason the worker count is final
        self.free_before = {}       # device label -> free MiB before its first worker
        self.session = 1 + max([int(m.group(1)) for p in (queue.root / 'workers').glob('s*-*.json')
                                if (m := re.match(r's(\d+)-', p.stem))], default=0)
        self.decisions = []
        self.peak = {}              # pid -> highest per-process GPU MiB sampled by the driver

    def alive(self, device=None):
        return [w for w in self.workers if w['alive'] and (device is None or w['device'] is device)]

    def launch(self, device):
        n = 1 + sum(w['device'] is device for w in self.workers)
        wid = f"s{self.session:02d}-gpu{device['label']}-w{n}"
        log_path = self.queue.root / 'logs' / f'{wid}.log'
        cmd = self.command(device, wid)
        with open(log_path, 'w') as out:
            out.write('$ ' + ' '.join(cmd) + '\n')
            out.flush()
            # Own session: Ctrl-C reaches the driver only, which then stops the workers itself.
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        self.workers.append(dict(wid=wid, device=device, proc=proc, log=log_path, started=time.time(),
                                 alive=True, measured=False))
        self.queue.event('worker_start', worker=wid, gpu=device['label'], pid=proc.pid)
        log(f"worker {wid} started on GPU {device['label']} (pid {proc.pid}, log {log_path})")

    def remove_partial(self, pid=None):
        """Delete the temporary PNGs (partial_PID_frame_*.png) of a dead worker, or all of them."""
        if self.output is not None:
            for p in Path(self.output).glob(f"satellite_*/partial_{pid if pid else '*'}_*.png"):
                p.unlink(missing_ok=True)

    def start(self):
        self.remove_partial()                                          # left by killed workers
        free = gpu_free_mib()
        for d in self.devices:
            self.free_before[d['label']] = free.get(d.get('uuid'))
            n = 1 if d['kind'] == 'cpu' else (self.fixed or 1)
            for _ in range(n):
                if len(self.alive()) < self.max_workers:
                    self.launch(d)
            if d['kind'] == 'cpu':
                self.decide(d, 'final: 1 CPU worker (Cycles uses every core)')
            elif self.fixed:
                self.decide(d, f'final: {n} workers (--workers-per-gpu {self.fixed})')

    def decide(self, device, text, **data):
        self.frozen[device['label']] = text
        msg = f"GPU {device['label']}: {len(self.alive(device))} worker(s); {text}"
        self.decisions.append(msg)
        self.queue.event('sizing', gpu=device['label'], workers=len(self.alive(device)), decision=text, **data)
        log(msg)

    def status(self, w):
        try:
            return json.loads((self.queue.root / 'workers' / f"{w['wid']}.json").read_text())
        except (OSError, ValueError):
            return {}

    def size(self, device):
        """Add one worker on `device` if the measured memory allows it, else freeze its count."""
        mine = self.alive(device)
        if not mine:
            return
        statuses = [self.status(w) for w in mine]
        newest = statuses[-1]
        if newest.get('gpu_mib_after_first_task') is None and newest.get('state') != 'finished':
            return                                                     # wait for its first task
        uuids = {s.get('gpu_uuid') for s in statuses if s.get('gpu_uuid')}
        if device.get('uuid') and uuids - {device['uuid']}:
            raise PoolFailed(f"worker on GPU {device['label']} runs on {uuids}, expected {device['uuid']}: "
                             'device mapping is wrong (use --device-map)')
        per = max([s.get('gpu_mib') or 0 for s in statuses] + [self.peak.get(w['proc'].pid, 0) for w in mine],
                  default=0) or None
        free = gpu_free_mib().get(device.get('uuid'))
        before = self.free_before.get(device['label'])
        if per is None and free is not None and before is not None:
            per = (before - free) / len(mine)                          # no per-process figure
        elif per is not None and free is not None and before is not None:
            # A worker's memory swings within every render (EEVEE: ~2.5 to ~7.2 GiB), so one reading of
            # the free memory can fall in a trough: count every worker at its measured peak instead.
            free = min(free, before - len(mine) * per)
        rss = max([proc_rss_mib(w['proc'].pid) or 0 for w in self.alive()], default=0)
        avail = mem_available_mib()
        settling = sum(1 for w in self.alive() if self.status(w).get('gpu_mib_after_first_task') is None
                       and self.status(w).get('state') != 'finished')
        data = dict(gpu_mib_per_worker=per, gpu_free_mib=free, gpu_margin_mib=self.gpu_margin, rss_mib=rss,
                    mem_available_mib=avail, ram_margin_mib=self.ram_margin, cpus=self.max_workers)
        txt = f"{per:.0f} MiB per worker, {free} MiB free, margin {self.gpu_margin}" if per and free is not None else ''
        if self.queue.counts()['pending'] == 0:
            self.decide(device, f'final: no pending task left ({txt})', **data)
        elif per is None or free is None:
            self.decide(device, 'final: GPU memory cannot be measured on this device '
                                '(use --workers-per-gpu N)', **data)
        elif len(mine) >= self.max_per_gpu:
            self.decide(device, f'final: --max-workers-per-gpu {self.max_per_gpu} reached ({txt})', **data)
        elif len(self.alive()) >= self.max_workers:
            self.decide(device, f'final: CPU count {self.max_workers} reached ({txt})', **data)
        elif free - self.gpu_margin <= per:
            self.decide(device, f'final: {txt}: no room for another worker', **data)
        elif avail is not None and avail - self.ram_margin - settling * rss <= rss:
            self.decide(device, f'final: host memory {avail} MiB available, {rss} MiB RSS per worker, '
                                f'margin {self.ram_margin}: no room for another worker', **data)
        else:
            log(f"GPU {device['label']}: {txt}, host {avail} MiB available / {rss} MiB RSS: adding a worker")
            self.queue.event('sizing', gpu=device['label'], workers=len(mine) + 1, decision='add', **data)
            self.launch(device)

    def poll(self):
        """Reap dead workers (requeue their tasks), grow the pool; returns the number alive."""
        for w in self.workers:
            if not w['alive']:
                continue
            rc = w['proc'].poll()
            if rc is None:
                continue
            w['alive'] = False
            back = self.queue.requeue(w['wid'])
            self.remove_partial(w['proc'].pid)
            st = self.status(w)
            self.queue.event('worker_exit', worker=w['wid'], returncode=rc, requeued=back,
                             tasks_done=st.get('tasks_done'))
            if rc == 0 and st.get('state') == 'finished':
                log(f"worker {w['wid']} finished ({st.get('tasks_done')} tasks)")
            else:
                log(f"WORKER {w['wid']} DIED (exit {rc}); task(s) {back or 'none'} back in the queue; "
                    f"see {w['log']}")
                if w['device']['label'] not in self.frozen:
                    self.decide(w['device'], f'final: a worker died (exit {rc}), no more workers on this GPU')
        undecided = [d for d in self.devices if d['label'] not in self.frozen]
        if undecided and any(d.get('uuid') for d in undecided):
            # Sample the workers' GPU memory at every poll until their GPU's count is decided:
            # the worker's own snapshots (between renders) can miss the peak inside a render.
            for pid, mib in process_gpu_mib().items():
                if any(w['proc'].pid == pid for w in self.alive()):
                    self.peak[pid] = max(self.peak.get(pid, 0), mib)
        for d in undecided:
            self.size(d)
        return len(self.alive())

    def stop(self):
        for w in self.alive():
            try:
                os.killpg(w['proc'].pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.time() + 20
        for w in self.alive():
            try:
                w['proc'].wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                os.killpg(w['proc'].pid, signal.SIGKILL)
                w['proc'].wait()
            w['alive'] = False
        self.queue.requeue()

    def summary(self):
        rows = []
        for w in self.workers:
            st = self.status(w)
            rows.append(dict(worker=w['wid'], gpu=w['device']['label'], uuid=st.get('gpu_uuid'), pid=w['proc'].pid,
                             tasks=st.get('tasks_done'), frames=st.get('frames_rendered'),
                             setup_s=st.get('setup_seconds'), gpu_mib=st.get('gpu_mib'),
                             returncode=w['proc'].returncode))
        return rows


def worker_command(blender, cameras, terrain, output, queue, render_args):
    """Factory of the Blender worker command line for WorkerPool."""
    def command(device, wid):
        return [blender, '--background', '--factory-startup', *device['blender_args'], '--python-exit-code', '1',
                '--python', str(RENDER_SWARM), '--', '--cameras', str(cameras), '--terrain', str(terrain),
                '--output', str(output), '--task-queue', str(queue.root), '--worker-id', wid,
                '--parent-pid', str(os.getpid()), *device['render_args'], *render_args]
    return command


def drive(queue, pool, on_done=lambda name: None, busy=lambda: False, tick=lambda: None, total=None,
          interval=2.0, report_every=60.0):
    """Run the pool until every task is done and the callbacks are idle.

    on_done(name) is called once per done marker (also those present at start); busy() tells whether
    work started by on_done is still running (extraction); tick() runs every loop. Raises PoolFailed
    when every worker is dead while tasks remain (after busy() drains)."""
    handled, t0, last = set(), time.time(), 0.0
    done0 = len(queue.done_names())
    lock = open(queue.root / 'driver.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f'another driver is running on {queue.root}')

    def stop_handler(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    old = {s: signal.signal(s, stop_handler) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        stop_stale_workers(queue)
        back = queue.requeue()
        if back:
            log(f'{len(back)} task(s) claimed by a previous driver back in the queue')
        if queue.counts()['pending']:
            pool.start()
        while True:
            alive = pool.poll()
            for name in sorted(queue.done_names() - handled):
                handled.add(name)
                on_done(name)
            tick()
            c = queue.counts()
            if c['pending'] + c['claimed'] == 0 and not busy():
                break
            if alive == 0 and c['pending'] + c['claimed'] > 0:
                while busy():
                    tick()
                    time.sleep(interval)
                raise PoolFailed(f"every worker is dead with {c['pending'] + c['claimed']} task(s) left; "
                                 'rerun the same command to resume')
            if time.time() - last >= report_every:
                last = time.time()
                n = c['done'] - done0
                rate = n / (time.time() - t0) * 3600
                left = c['pending'] + c['claimed']
                eta = f', ETA {left / (n / (time.time() - t0)) / 60:.0f} min' if n else ''
                log(f"tasks done {c['done']}/{total or c['done'] + left} ({c['claimed']} running, {alive} workers, "
                    f'{rate:.0f} tasks/h{eta})')
            time.sleep(interval)
        for w in pool.alive():
            try:
                w['proc'].wait(60)
            except subprocess.TimeoutExpired:
                pass
        pool.poll()
    except BaseException:
        pool.stop()
        raise
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        lock.close()


def merge_poses(output, queue, sats):
    """Write satellite_SS/poses.jsonl sorted by frame from the done markers and any existing lines."""
    by_sat = {s: {} for s in sats}
    for s in sats:
        path = Path(output) / f'satellite_{s:02d}' / 'poses.jsonl'
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    by_sat[s][(rec['frame'], json.dumps(rec.get('crop')))] = line
    for name in sorted(queue.done_names()):
        marker = queue.read_done(name)
        for rec in marker['poses']:
            by_sat.setdefault(rec['satellite_id'], {})[(rec['frame'], json.dumps(rec.get('crop')))] = json.dumps(rec)
    for s, recs in by_sat.items():
        if not recs:
            continue
        path = Path(output) / f'satellite_{s:02d}' / 'poses.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.jsonl.tmp')
        tmp.write_text(''.join(recs[k] + '\n' for k in sorted(recs, key=lambda k: (k[0], k[1]))))
        os.replace(tmp, path)


# ----------------------------------------------------------------------------- command line

def add_pool_arguments(ap):
    ap.add_argument('--blender', default=DEFAULT_BLENDER)
    ap.add_argument('--gpus', default=os.environ.get('GPUS', 'all'),
                    help='all (default) or comma-separated GPUs: nvidia-smi index or UUID, bN = Blender device N')
    ap.add_argument('--device-map', default=os.environ.get('DEVICE_MAP'),
                    help='GPU=BLENDER_DEVICE,... when the probe cannot identify a device (EEVEE: Vulkan index; '
                         'Cycles: index or id within the backend)')
    ap.add_argument('--workers-per-gpu', type=int, default=None, help='fixed number of workers per GPU (no measuring)')
    ap.add_argument('--max-workers-per-gpu', type=int, default=4)
    ap.add_argument('--gpu-memory-margin', type=int, default=1024, help='MiB kept free on each GPU')
    ap.add_argument('--ram-margin', type=int, default=4096, help='MiB of host memory kept free')
    ap.add_argument('--chunk', type=int, default=20, help='frames per task (one satellite)')
    ap.add_argument('--queue', type=Path, default=None, help='queue directory (default OUTPUT/queue)')


def take_option(args, name):
    """Remove '--name VALUE' or '--name=VALUE' from a list; returns (value or None, rest)."""
    out, value, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a == name and i + 1 < len(args):
            value = args[i + 1]
            i += 2
            continue
        if a.startswith(name + '='):
            value = a.split('=', 1)[1]
        else:
            out.append(a)
        i += 1
    return value, out


def render_engine(render_args):
    engine, _ = take_option(render_args, '--engine')
    backend, _ = take_option(render_args, '--gpu-backend')
    return engine or 'CYCLES', backend or 'CUDA'


def build_pool(args, render_args, output, n_tasks):
    """Discover the devices and make the queue and pool for render_args (shared by both modes)."""
    for reserved in ('--task-queue', '--worker-id', '--parent-pid', '--gpu-devices'):
        if take_option(render_args, reserved)[0] is not None or reserved in render_args:
            raise SystemExit(f'{reserved} is set by the pool; select GPUs with --gpus (GPUS=...)')
    engine, backend = render_engine(render_args)
    devices = discover(engine, backend, args.blender, args.gpus, args.device_map)
    log(f'Hardware: {cpu_count()} CPUs, {mem_available_mib()} MiB host memory available; engine {engine}; '
        f'devices: {describe(devices)}')
    mode = (f'--workers-per-gpu {args.workers_per_gpu}' if args.workers_per_gpu else
            f'automatic from measured memory (max {args.max_workers_per_gpu} per GPU, GPU margin '
            f'{args.gpu_memory_margin} MiB, host margin {args.ram_margin} MiB)')
    log(f'Workers per GPU: {mode}; {n_tasks} task(s) of up to {args.chunk} frames')
    return devices


def main():
    argv = sys.argv[1:]
    render_args = argv[argv.index('--') + 1:] if '--' in argv else []
    argv = argv[:argv.index('--')] if '--' in argv else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cameras', type=Path, required=True)
    ap.add_argument('--terrain', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    add_pool_arguments(ap)
    args = ap.parse_args(argv)
    if '--check-only' in render_args:
        # Pose check without rendering: one process, as before the pool.
        cmd = [args.blender, '--background', '--factory-startup', '--python-exit-code', '1', '--python',
               str(RENDER_SWARM), '--', '--cameras', str(args.cameras), '--terrain', str(args.terrain),
               '--output', str(args.output), *render_args]
        sys.exit(subprocess.run(cmd).returncode)
    frames_text, render_args = take_option(render_args, '--frames')
    sats_text, render_args = take_option(render_args, '--satellites')
    skip = '--skip-existing' in render_args
    render_args = [a for a in render_args if a != '--skip-existing']
    manifest = json.loads(args.cameras.read_text())
    n = len(manifest['epochs'])
    lo, hi = (int(x) for x in (frames_text or f'0:{n}').split(':'))
    if not 0 <= lo < hi <= n:
        raise SystemExit(f'--frames {frames_text} outside 0:{n}')
    known = sorted(c['satellite_id'] for c in manifest['epochs'][0]['cameras'])
    sats = known if sats_text is None else [int(s) for s in sats_text.split(',')]
    if not set(sats) <= set(known):
        raise SystemExit(f'--satellites {sats_text} not in cameras.json {known}')
    queue_dir = args.queue or args.output / 'queue'
    resuming = (queue_dir / 'config.json').exists()
    if not resuming and not skip and any((args.output / f'satellite_{s:02d}' / f'frame_{k:04d}.png').exists()
                                         for s in sats for k in range(lo, hi)):
        raise SystemExit('--output already contains frames of this selection; use --skip-existing or a new run directory')
    args.output.mkdir(parents=True, exist_ok=True)
    queue = TaskQueue(queue_dir)
    queue.check_config(dict(cameras=str(args.cameras.resolve()), satellites=sats, frames=[lo, hi], chunk=args.chunk,
                            render_args=render_args))
    tasks = chunk_tasks(sats, {s: range(lo, hi) for s in sats}, args.chunk)
    devices = build_pool(args, render_args, args.output, len(tasks))
    for name, task in tasks:
        queue.add(name, task)
    pool = WorkerPool(queue, devices, worker_command(args.blender, args.cameras, args.terrain, args.output, queue,
                                                     render_args),
                      args.workers_per_gpu, args.max_workers_per_gpu, args.gpu_memory_margin, args.ram_margin,
                      output=args.output)
    t0 = time.time()
    try:
        drive(queue, pool, total=len(tasks))
    except PoolFailed as error:
        log(f'FAILED: {error}')
        merge_poses(args.output, queue, sats)
        sys.exit(1)
    except KeyboardInterrupt as error:
        log(f'interrupted ({error}); workers stopped; launch the same command again to resume')
        merge_poses(args.output, queue, sats)
        sys.exit(130)
    merge_poses(args.output, queue, sats)
    for row in pool.summary():
        log(f'worker summary: {row}')
    log(f'Rendered {len(tasks)} task(s) in {time.time() - t0:.0f} s; decisions: ' + ' | '.join(pool.decisions))


if __name__ == '__main__':
    main()
