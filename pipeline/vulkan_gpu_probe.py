"""Tiny EEVEE render that reports which physical GPU (nvidia-smi UUID) this Blender uses.

Run as: blender --background --factory-startup --gpu-backend vulkan --gpu-device N \
            --gpu-device-no-fallback --python vulkan_gpu_probe.py -- EXPECTED_UUID
Exits 0 when the process sits on EXPECTED_UUID, 1 otherwise. Guards the launchers against
a change of the Vulkan enumeration order (it is not the nvidia-smi order in general).

With '-- --json [--probe-cycles BACKEND]' it prints one line 'GPU_PROBE_JSON {...}' instead, used by
pipeline/render_pool.py for device discovery: the GPU backend, the devices Blender enumerates for
it (gpu.platform.devices_get), the device actually used, the nvidia-smi UUIDs of this process
(empty without nvidia-smi) and, with --probe-cycles, the Cycles devices of that compute backend
(name, id, type; CUDA/OptiX ids carry the PCI bus id).
"""
import json
import os
import subprocess
import sys

import bpy
import gpu

argv=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
as_json='--json' in argv
cycles_backend=argv[argv.index('--probe-cycles')+1] if '--probe-cycles' in argv else None
expected=None if as_json else (argv[0] if argv else None)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
scene.render.engine='BLENDER_EEVEE';scene.render.resolution_x=scene.render.resolution_y=64
camera=bpy.data.objects.new('probe',bpy.data.cameras.new('probe'))
scene.collection.objects.link(camera);scene.camera=camera
render_error=None
try:
    bpy.ops.render.render()
except Exception as error:  # no usable GPU context: still report the Cycles devices
    render_error=str(error)
try:
    apps=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader'],
                        capture_output=True,text=True,check=True,timeout=30).stdout.splitlines()
    uuids=[line.split(',')[0].strip() for line in apps if line.split(',')[1].strip()==str(os.getpid())]
except (OSError,subprocess.SubprocessError):
    uuids=[]
if as_json:
    info=dict(pid=os.getpid(),render_error=render_error,nvidia_uuids=uuids)
    try:
        info.update(backend=gpu.platform.backend_type_get(),renderer=gpu.platform.renderer_get(),
                    vendor=gpu.platform.vendor_get(),
                    devices=[dict(index=d.index,identifier=d.identifier,name=d.name)
                             for d in gpu.platform.devices_get()])
    except Exception as error:
        info.update(backend=None,devices=[],gpu_error=str(error))
    if cycles_backend:
        preferences=bpy.context.preferences.addons['cycles'].preferences
        try:
            preferences.compute_device_type=cycles_backend
            preferences.get_devices()
            info['cycles']=[dict(name=d.name,id=d.id,type=d.type) for d in preferences.devices]
        except Exception as error:  # backend not supported by this build
            info['cycles']=[];info['cycles_error']=str(error)
    print('GPU_PROBE_JSON '+json.dumps(info),flush=True)
    sys.stdout.flush();os._exit(0)
print(f'GPU probe: backend {gpu.platform.backend_type_get()}, {gpu.platform.renderer_get()}, nvidia-smi UUID {uuids}',flush=True)
status=0 if expected is None or uuids==[expected] else 1
if status:print(f'GPU probe: expected {expected}, got {uuids}',flush=True)
sys.stdout.flush();os._exit(status)
