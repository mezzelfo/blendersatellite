"""Tiny EEVEE render that reports which physical GPU (nvidia-smi UUID) this Blender uses.

Run as: blender --background --factory-startup --gpu-backend vulkan --gpu-device N \
            --gpu-device-no-fallback --python vulkan_gpu_probe.py -- EXPECTED_UUID
Exits 0 when the process sits on EXPECTED_UUID, 1 otherwise. Guards the launchers against
a change of the Vulkan enumeration order (on osiride Vulkan 0 = nvidia-smi GPU 1).
"""
import os
import subprocess
import sys

import bpy
import gpu

expected=sys.argv[sys.argv.index('--')+1] if '--' in sys.argv else None
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
scene.render.engine='BLENDER_EEVEE';scene.render.resolution_x=scene.render.resolution_y=64
camera=bpy.data.objects.new('probe',bpy.data.cameras.new('probe'))
scene.collection.objects.link(camera);scene.camera=camera
bpy.ops.render.render()
apps=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader'],
                    capture_output=True,text=True,check=True).stdout.splitlines()
uuids=[line.split(',')[0].strip() for line in apps if line.split(',')[1].strip()==str(os.getpid())]
print(f'GPU probe: backend {gpu.platform.backend_type_get()}, {gpu.platform.renderer_get()}, nvidia-smi UUID {uuids}',flush=True)
status=0 if expected is None or uuids==[expected] else 1
if status:print(f'GPU probe: expected {expected}, got {uuids}',flush=True)
sys.stdout.flush();os._exit(status)
