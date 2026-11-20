"""Run render_variant.py in Blender on one GPU and record its peak GPU memory (step-20 study).

  python run_render.py --nvsmi-gpu 0 --log DIR/render.json -- <render_variant.py arguments>

Blender runs headless with Vulkan on the given nvidia-smi GPU (Vulkan device indices are reversed
with respect to nvidia-smi on osiride, see scripts/render_job.sh; scripts/vulkan_gpu_probe.py checks
the UUID first). The command runs under /usr/bin/time -v. While it runs, nvidia-smi memory.used of
that GPU is polled every 0.2 s; the record holds the baseline before launch, the peak, and
peak - baseline (the GPU is otherwise idle), plus wall time and peak RSS from /usr/bin/time.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLENDER = '/snap/blender/current/blender'


def used_mib(gpu):
    out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits', '-i', str(gpu)],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip())


def main():
    split = sys.argv.index('--')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nvsmi-gpu', type=int, default=0)
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args(sys.argv[1:split])
    vk = {0: 1, 1: 0}[args.nvsmi_gpu]
    gpu_opts = ['--gpu-backend', 'vulkan', '--gpu-device', str(vk), '--gpu-device-no-fallback']
    uuid = subprocess.run(['nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader', '-i', str(args.nvsmi_gpu)],
                          capture_output=True, text=True, check=True).stdout.strip()
    probe = subprocess.run([BLENDER, '--background', '--factory-startup', *gpu_opts, '--python',
                            str(ROOT / 'scripts/vulkan_gpu_probe.py'), '--', uuid], capture_output=True, text=True)
    if probe.returncode != 0:
        raise SystemExit(f'Vulkan device {vk} is not nvidia-smi GPU {args.nvsmi_gpu}: {probe.stdout[-500:]}')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    time_file = args.log.with_suffix('.time')
    stdout_file = args.log.with_suffix('.log')
    baseline = used_mib(args.nvsmi_gpu)
    cmd = ['/usr/bin/time', '-v', '-o', str(time_file), BLENDER, '--background', '--factory-startup', *gpu_opts,
           '--python-exit-code', '1', '--python', str(Path(__file__).resolve().parent / 'render_variant.py'),
           '--', *sys.argv[split + 1:]]
    start = time.monotonic()
    peak, samples = baseline, []
    with stdout_file.open('w') as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            m = used_mib(args.nvsmi_gpu)
            peak = max(peak, m)
            samples.append([round(time.monotonic() - start, 2), m])
            time.sleep(0.2)
    timing = time_file.read_text()
    wall = re.search(r'Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)', timing).group(1)
    rss = int(re.search(r'Maximum resident set size \(kbytes\): (\d+)', timing).group(1))
    parts = [float(x) for x in wall.split(':')]
    wall_s = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
    record = dict(command=cmd, returncode=proc.returncode, gpu=args.nvsmi_gpu, vulkan_device=vk, uuid=uuid,
                  gpu_baseline_mib=baseline, gpu_peak_mib=peak, gpu_peak_minus_baseline_mib=peak - baseline,
                  wall_s=wall_s, peak_rss_kb=rss, gpu_samples=samples[::5])
    args.log.write_text(json.dumps(record, indent=1) + '\n')
    print(f'{args.log}: rc={proc.returncode} wall {wall_s:.0f} s, GPU peak {peak} MiB (baseline {baseline}), '
          f'RSS {rss / 1e6:.2f} GB', flush=True)
    if proc.returncode != 0:
        print(stdout_file.read_text()[-3000:])
        sys.exit(proc.returncode)


if __name__ == '__main__':
    main()
