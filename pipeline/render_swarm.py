"""Headless Blender perspective rendering of exported orbits spacecraft poses."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import subprocess
import importlib.util

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector
import numpy as np

# Load the pinned, NumPy-only helper directly: importing orbits.__init__ inside
# Blender would also import PyTorch, which belongs in the conda environment.
_geometry_path=Path(__file__).resolve().parents[1]/'orbits/src/orbits/camera_geometry.py'
_spec=importlib.util.spec_from_file_location('orbits_camera_geometry',_geometry_path)
camera_geometry=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(camera_geometry)


def configure_gpu(scene,backend,indices):
    """Select the Cycles devices: 'all', comma-separated indices or Cycles device ids within the
    backend (ids such as 'CUDA_NVIDIA TITAN Xp_0000:41:00' name a GPU by its PCI bus), or 'cpu'."""
    preferences=bpy.context.preferences.addons['cycles'].preferences
    if indices=='cpu':
        # Degenerate case without GPU (pipeline/render_pool.py on a machine with no usable GPU).
        preferences.compute_device_type='NONE'
        scene.cycles.device='CPU';scene.cycles.use_denoising=False
        print('Cycles CPU rendering (--gpu-devices cpu)',flush=True)
        return [dict(index=None,name='CPU',id='CPU',backend='CPU')]
    preferences.compute_device_type=backend
    preferences.get_devices()
    available=[device for device in preferences.devices if device.type==backend]
    if not available:
        raise RuntimeError(f'No {backend} GPUs found; GPU rendering is required (check driver and sandbox access)')
    ids=[device.id for device in available]
    tokens=[x.strip() for x in indices.split(',')]
    selected=(list(range(len(available))) if indices=='all' else
              [int(x) if x.lstrip('-').isdigit() else (ids.index(x) if x in ids else -1) for x in tokens])
    if not selected or any(i<0 or i>=len(available) for i in selected):
        raise ValueError(f'Invalid GPU devices {indices}; {backend} devices available: {ids}')
    for device in preferences.devices:device.use=False
    records=[]
    for i in selected:
        device=available[i];device.use=True
        records.append(dict(index=i,name=device.name,id=device.id,backend=device.type))
    scene.cycles.device='GPU'
    # GPU denoising is not supported on every CUDA device (including some
    # Pascal builds). Disable it so this small preview does not add a CPU pass.
    scene.cycles.use_denoising=False
    print(f'Cycles GPU devices: {records}; CPU rendering disabled',flush=True)
    return records


def gpu_process_snapshot():
    try:
        result=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory',
                               '--format=csv,noheader'],capture_output=True,text=True,timeout=10,check=True)
        # Only record this Blender process, rather than unrelated desktop apps.
        return [line for line in result.stdout.splitlines() if f', {os.getpid()},' in line]
    except (OSError,subprocess.SubprocessError) as error:
        return [f'nvidia-smi monitoring unavailable: {error}']


def snapshot_memory(snapshot):
    """(UUID, MiB) of this process from a gpu_process_snapshot, or (None, None)."""
    for line in snapshot or []:
        parts=[p.strip() for p in line.split(',')]
        if len(parts)==4 and parts[3].endswith('MiB'):
            try:return parts[0],int(parts[3].split()[0])
            except ValueError:pass
    return None,None


def append_line(path,record):
    """Append one JSON line with a single O_APPEND write, so concurrent workers never interleave."""
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o644)
    try:os.write(fd,(json.dumps(record)+'\n').encode())
    finally:os.close(fd)


def write_json_atomic(path,data):
    tmp=Path(f'{path}.tmp{os.getpid()}')
    tmp.write_text(json.dumps(data)+'\n');os.replace(tmp,path)


def valid_png(path,width,height):
    """True for a complete 8-bit RGBA PNG of the expected size (signature, IHDR, IEND)."""
    try:
        with open(path,'rb') as f:
            head=f.read(33)
            f.seek(-12,os.SEEK_END);tail=f.read(12)
    except OSError:
        return False
    return (len(head)==33 and head[:8]==b'\x89PNG\r\n\x1a\n' and head[12:16]==b'IHDR'
            and int.from_bytes(head[16:20],'big')==width and int.from_bytes(head[20:24],'big')==height
            and head[24]==8 and head[25]==6 and tail[4:8]==b'IEND')


def parse_frames(text,count):
    start,stop=(int(x) for x in text.split(':'))
    if not 0<=start<stop<=count:
        raise ValueError(f'--frames {text} outside 0:{count}')
    return range(start,stop)


def parse_crop(text):
    """X0:X1,Y0:Y1 in sensor pixels, origin top-left, X1/Y1 excluded."""
    xs,ys=text.split(',')
    x0,x1=(int(v) for v in xs.split(':'));y0,y1=(int(v) for v in ys.split(':'))
    if not (0<=x0<x1 and 0<=y0<y1):raise ValueError(f'invalid --crop {text}')
    return dict(x0=x0,x1=x1,y0=y0,y1=y1,width=x1-x0,height=y1-y0)


def set_crop(render,crop,width,height):
    """Render only the crop (Blender border, origin bottom-left) and write it at crop size."""
    if crop is None:
        render.use_border=False;render.use_crop_to_border=False;return
    if crop['x1']>width or crop['y1']>height:raise ValueError(f'crop {crop} outside {width}x{height}')
    render.use_border=True;render.use_crop_to_border=True
    render.border_min_x=crop['x0']/width;render.border_max_x=crop['x1']/width
    render.border_min_y=(height-crop['y1'])/height;render.border_max_y=(height-crop['y0'])/height


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cameras',type=Path,required=True)
    parser.add_argument('--terrain',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--satellites',default='0,1,2,3,4,5,6',help='any comma-separated subset of satellite ids')
    parser.add_argument('--frames',default=None,help='START:STOP frame range (STOP excluded); default all epochs')
    parser.add_argument('--skip-existing',action='store_true',help='skip frames whose PNG exists and is a valid file')
    parser.add_argument('--coverage-every',type=int,default=50,
                        help='reload the PNG and record alpha coverage (and an nvidia-smi snapshot) every N-th frame '
                             '(default 50, about 0.3 s per frame saved); 1 = every frame')
    parser.add_argument('--png-compression',type=int,default=15,choices=range(0,101),metavar='0..100',
                        help='PNG zlib compression (Blender image_settings.compression, default 15 as in Blender); '
                             'lossless at every level: only the write time and the file size change')
    parser.add_argument('--crop',action='append',type=parse_crop,metavar='X0:X1,Y0:Y1',
                        help='render only this sensor-pixel window (origin top-left, end excluded); camera intrinsics '
                             'unchanged. Repeat for several windows per frame, written as frame_KKKK_xX0_yY0.png')
    parser.add_argument('--engine',choices=['CYCLES','EEVEE'],default='CYCLES',
                        help='CYCLES (GPU path tracing, default) or EEVEE (rasterizer, single OpenGL/Vulkan GPU)')
    parser.add_argument('--samples',type=int,default=8,help='Cycles samples or EEVEE taa_render_samples')
    parser.add_argument('--check-only',action='store_true',help='validate Blender projections without loading terrain or rendering')
    parser.add_argument('--gpu-backend',choices=['CUDA','OPTIX','HIP','ONEAPI','METAL'],default='CUDA')
    parser.add_argument('--gpu-devices',default='all',
                        help="Cycles: all, comma-separated indices or device ids within the chosen backend, or cpu")
    parser.add_argument('--task-queue',type=Path,
                        help='worker mode (pipeline/render_pool.py): load the scene once, then claim tasks '
                             '(QUEUE/pending/*.json, frames of one satellite) until the queue is empty; '
                             '--frames/--satellites are ignored and existing valid PNGs are always skipped')
    parser.add_argument('--worker-id',default=None,help='worker mode: name used in QUEUE/claimed and QUEUE/workers')
    parser.add_argument('--parent-pid',type=int,default=None,help='worker mode: exit when this driver process is gone')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    manifest=json.loads(args.cameras.read_text())
    if manifest.get('schema_version')!=2 or 'epochs' not in manifest:
        parser.error('--cameras must be a schema_version 2 (per-epoch) cameras.json')
    epochs=manifest['epochs']
    frames=parse_frames(args.frames or f'0:{len(epochs)}',len(epochs))
    known={c['satellite_id'] for c in epochs[0]['cameras']}
    worker=args.task_queue is not None
    if worker:
        if args.check_only:parser.error('--task-queue cannot be combined with --check-only')
        args.skip_existing=True;selected=set(known)
        args.worker_id=args.worker_id or f'pid{os.getpid()}'
    else:
        selected={int(x) for x in args.satellites.split(',')}
    if not selected<=known:parser.error(f'--satellites {sorted(selected-known)} not in cameras.json {sorted(known)}')
    crops=args.crop or [None]
    def frame_path(index,frame,crop=None):
        suffix='' if len(crops)==1 else f'_x{crop["x0"]:04d}_y{crop["y0"]:04d}'
        return args.output/f'satellite_{index:02d}'/f'frame_{frame:04d}{suffix}.png'
    if not args.check_only and not args.skip_existing and any(
            frame_path(i,k,c).exists() for k in frames for i in selected for c in crops):
        parser.error('--output already contains frames of this selection; use --skip-existing or a new run directory')
    args.output.mkdir(parents=True,exist_ok=True)
    setup_start=time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    eevee=args.engine=='EEVEE'
    if eevee:
        scene.render.engine='BLENDER_EEVEE'
        scene.eevee.taa_render_samples=args.samples
        # Emission-only scene without lights: switch off every unused effect.
        eevee_disabled=dict(use_shadows=False,use_raytracing=False,use_fast_gi=False,
                            use_volumetric_shadows=False,use_overscan=False)
        for key,value in eevee_disabled.items():setattr(scene.eevee,key,value)
        scene.render.use_motion_blur=False;eevee_disabled['render.use_motion_blur']=False
        gpu_devices=None
    else:
        scene.render.engine='CYCLES'
        gpu_devices=configure_gpu(scene,args.gpu_backend,args.gpu_devices)
        scene.cycles.samples=args.samples
        scene.cycles.use_denoising=False
        scene.render.use_persistent_data=True
    # Resolution is set per camera record (full HyperScape100 frame).
    scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGBA'
    scene.render.image_settings.compression=args.png_compression
    scene.render.film_transparent=True
    # Leave the render date and render time out of the PNG metadata (Blender writes them as text
    # chunks by default): with them no two runs give byte-identical files, although the pixels are.
    scene.render.use_stamp_date=False;scene.render.use_stamp_render_time=False
    scene.view_settings.view_transform='Standard'
    scene.view_settings.look='None'
    material=bpy.data.materials.new('Baked aerial texture colors')
    material.use_nodes=True
    nodes=material.node_tree.nodes;nodes.clear()
    output=nodes.new('ShaderNodeOutputMaterial')
    emission=nodes.new('ShaderNodeEmission')
    color=nodes.new('ShaderNodeVertexColor');color.layer_name='Color'
    material.node_tree.links.new(color.outputs['Color'],emission.inputs['Color'])
    material.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
    # Emission preserves the captured aerial texture illumination. This MVP
    # validates geometry/poses and does not infer illumination from orbit time.
    missing=[];triangles=0
    for tile in ([] if args.check_only else manifest['selected_tiles']):
        path=args.terrain/(tile+'.npz')
        if not path.exists(): missing.append(tile);continue
        with np.load(path) as data:
            vertices=data['vertices'];rgb=data['rgb'].astype(float)/255
            rows,cols=data['shape']
            # Optional walls (terrain_to_render.py --walls): 4 corners per segment, same colour handling.
            walls=(data['wall_vertices'],data['wall_rgb'].astype(float)/255) if 'wall_vertices' in data.files else None
        ids=np.arange(rows*cols).reshape(rows,cols)
        a=ids[:-1,:-1].ravel();b=a+1;c=a+cols;d=c+1
        faces=np.concatenate([np.stack([a,b,d],1),np.stack([a,d,c],1)])
        meshes=[(tile,vertices,faces,rgb)]
        if walls is not None and len(walls[0]):
            q=np.arange(len(walls[0])).reshape(-1,4)
            meshes.append((tile+'_walls',walls[0],np.concatenate([q[:,[0,1,2]],q[:,[0,2,3]]]),walls[1]))
        for name,mesh_vertices,mesh_faces,mesh_rgb in meshes:
            mesh=bpy.data.meshes.new(name)
            mesh.from_pydata(mesh_vertices.tolist(),[],mesh_faces.tolist());mesh.update()
            attribute=mesh.color_attributes.new(name='Color',type='FLOAT_COLOR',domain='POINT')
            # PNG textures are sRGB; Blender FLOAT_COLOR attributes are scene linear.
            linear=np.where(mesh_rgb<=.04045,mesh_rgb/12.92,((mesh_rgb+.055)/1.055)**2.4)
            rgba=np.c_[linear,np.ones(len(mesh_rgb))].astype(np.float32)
            attribute.data.foreach_set('color',rgba.ravel())
            obj=bpy.data.objects.new(name,mesh);scene.collection.objects.link(obj)
            obj.data.materials.append(material);triangles+=len(mesh_faces)
    if missing: raise RuntimeError(f'Missing {len(missing)} selected terrain caches: {missing}')
    camera_data=bpy.data.cameras.new('HyperScape100 full-frame RGB approximation')
    camera=bpy.data.objects.new('SatelliteCamera',camera_data)
    scene.collection.objects.link(camera);scene.camera=camera
    camera_data.type='PERSP'
    camera_data.sensor_fit='HORIZONTAL'
    camera_data.clip_start=1;camera_data.clip_end=1000000
    camera_data.dof.use_dof=False
    chief_target=manifest.get('pointing_mode')=='chief-target'
    report_path=args.output/'render_report.jsonl'
    header=dict(type='run',cameras=str(args.cameras.resolve()),frames=args.frames,
                satellites=sorted(selected),pointing_mode=manifest.get('pointing_mode','nadir'),
                target_source=manifest.get('target_source'),terrain_tiles=len(manifest['selected_tiles']),
                triangles=triangles,samples=args.samples,compute_device=scene.cycles.device,
                gpu_backend=args.gpu_backend,gpu_devices=gpu_devices,denoising=False,
                coverage_every=args.coverage_every,png_compression=args.png_compression,crops=args.crop,blender_version=bpy.app.version_string,
                illumination='Source baked aerial texture colors, emission shader')
    if eevee:
        header.update(engine='BLENDER_EEVEE',compute_device='GPU (EEVEE rasterizer)',gpu_backend=None,denoising=None,
                      eevee_taa_render_samples=args.samples,eevee_disabled=eevee_disabled,
                      clip='per camera: distance_m -/+ 10 km',scene_setup_seconds=time.monotonic()-setup_start)
    if worker:header.update(worker_id=args.worker_id,task_queue=str(args.task_queue.resolve()))
    def write_header():append_line(report_path,header)
    header_pending=False
    if not args.check_only:
        # One run header per invocation, then one line per frame. EEVEE writes it after
        # its first render, once the GPU context exists and its device can be recorded.
        if eevee:header_pending=True
        else:write_header()
    # Frames already listed in each satellite's poses.jsonl (for --skip-existing resumes).
    posed={}
    for index in ([] if worker else selected):
        poses_path=args.output/f'satellite_{index:02d}'/'poses.jsonl'
        posed[index]={(json.loads(line)['frame'],json.dumps(json.loads(line).get('crop')))
                      for line in poses_path.read_text().splitlines() if line.strip()} if poses_path.exists() else set()

    worst=dict(corner_px=0.,pixel_step_dev=0.,target_px=0.,alignment=1.,target_recompute_m=0.,basis_recompute=0.)
    counts=dict(rendered=0,skipped=0,checked=0)

    def file_pose_sink(index,key,pose,rendered_now):
        # Frames mode: append to the satellite's poses.jsonl (a skipped frame only when missing).
        if rendered_now or key not in posed[index]:
            append_line(args.output/f'satellite_{index:02d}'/'poses.jsonl',pose);posed[index].add(key)

    def render_frames(frames,selected,pose_sink,timing=None):
        """Check every pose of the frames and render the selected satellites (the loop of one invocation)."""
        nonlocal header_pending
        for frame in frames:
            if args.parent_pid and os.getppid()!=args.parent_pid:
                print(f'Driver process {args.parent_pid} is gone: worker {args.worker_id} exits',flush=True)
                sys.stdout.flush();os._exit(3)
            epoch=epochs[frame]
            if epoch['frame']!=frame:raise RuntimeError(f'Epoch {frame} is labelled frame {epoch["frame"]}')
            records=epoch['cameras'];target=epoch.get('common_target_local_m');across=epoch.get('chief_ground_across_local')
            target_delta=basis_delta=None
            if chief_target:
                # Independent recomputation of the per-epoch chief WGS84 target and deputy retargeting.
                chief=next(c for c in records if c['role']=='chief')
                target_local,target_ecef=camera_geometry.chief_wgs84_target(chief,
                    manifest['origin_ecef_m'],manifest['local_from_ecef'])
                recomputed,target,across=camera_geometry.retarget_cameras(records,'chief-target',target_local)
                target_delta=float(np.linalg.norm(np.subtract(target,epoch['common_target_local_m'])))
                # Deputies with their own aim (acquisition files, orbits.acquisition): check the stored
                # pose against aimed_basis(position, target_local_m, aim_across_local) and keep it.
                aimed={i for i,r in enumerate(records) if 'aim_across_local' in r}
                recomputed=[records[i] if i in aimed else r for i,r in enumerate(recomputed)]
                basis_delta=float(max(camera_geometry.aimed_record_error(b) if i in aimed else
                                      np.max(np.abs(np.subtract(a['camera_to_local'],b['camera_to_local'])))
                                      for i,(a,b) in enumerate(zip(recomputed,records))))
                if target_delta>1e-3 or basis_delta>1e-9:
                    raise RuntimeError(f'Frame {frame}: recomputed target differs by {target_delta} m, basis by {basis_delta}')
                worst['target_recompute_m']=max(worst['target_recompute_m'],target_delta)
                worst['basis_recompute']=max(worst['basis_recompute'],basis_delta)
                records=recomputed
            results=[]
            for record in records:
                index=record['satellite_id']
                if index not in selected:continue
                width,height=record['resolution']
                basis=Matrix(record['camera_to_local']).to_4x4()
                basis.translation=Vector(record['position_local_m'])
                camera.matrix_world=basis
                camera_data.lens=record['focal_length_mm']
                camera_data.sensor_width=record['sensor_width_mm']
                camera_data.sensor_height=record['sensor_height_mm']
                camera_data.dof.aperture_fstop=record['f_number']
                camera_data.dof.focus_distance=record['distance_m']
                if eevee:
                    # Tight depth range for rasterizer depth precision (Cycles keeps 1 m .. 1000 km).
                    camera_data.clip_start=record['distance_m']-10e3;camera_data.clip_end=record['distance_m']+10e3
                scene.render.resolution_x=width;scene.render.resolution_y=height
                bpy.context.view_layer.update()
                # Independently check Blender's own projection against exported corners (in pixels).
                projected=[world_to_camera_view(scene,camera,Vector(p)) for p in record['footprint_local_m']]
                expected=np.array([[0,0],[1,0],[1,1],[0,1]])
                error=float(np.max(np.abs(np.array([[p.x,p.y] for p in projected])-expected)*[width,height]))
                if error>.1:raise RuntimeError(f'Frame {frame} camera {index} footprint reprojection failed: {error} px')
                center=Vector(record['ground_center_local_m'])
                # One native GSD on the reference plane in image-horizontal direction is one pixel.
                gsd=record['native_gsd_m']
                forward=-np.asarray(record['camera_to_local'])[:,2]
                right=np.asarray(record['camera_to_local'])[:,0]
                projected_across=camera_geometry.unit(camera_geometry.ground_direction(forward,right))
                direction=Vector(projected_across)
                p0=world_to_camera_view(scene,camera,center)
                p1=world_to_camera_view(scene,camera,center+direction*gsd)
                pixel_step=float(np.hypot((p1.x-p0.x)*width,(p1.y-p0.y)*height))
                if abs(pixel_step-1)>.002:raise RuntimeError(f'Frame {frame} camera {index} {gsd} m projection={pixel_step} pixels')
                worst['corner_px']=max(worst['corner_px'],error)
                worst['pixel_step_dev']=max(worst['pixel_step_dev'],abs(pixel_step-1))
                target_error=None;alignment=None
                if chief_target:
                    # Aimed deputies (acquisition files) look at their own target and orient the detector
                    # line by their own rule: their pose was checked above against aimed_basis, so here the
                    # alignment with the chief's across-track is only reported.
                    aimed_record='aim_across_local' in record
                    own_target=record['target_local_m'] if aimed_record else target
                    target_pixel=world_to_camera_view(scene,camera,Vector(own_target))
                    target_error=float(np.hypot((target_pixel.x-.5)*width,(target_pixel.y-.5)*height))
                    alignment=float(np.dot(projected_across,across))
                    if target_error>.05 or (alignment<1-1e-10 and not aimed_record):
                        raise RuntimeError(f'Frame {frame} camera {index}: target error {target_error}px, across-track alignment {alignment}')
                    worst['target_px']=max(worst['target_px'],target_error)
                    worst['alignment']=min(worst['alignment'],alignment)
                counts['checked']+=1
                if args.check_only:continue
                for crop in crops:
                    path=frame_path(index,frame,crop)
                    path.parent.mkdir(exist_ok=True)
                    set_crop(scene.render,crop,width,height)
                    out_w,out_h=(width,height) if crop is None else (crop['width'],crop['height'])
                    pose=dict(record,frame=frame,t_s=record.get('t_s',epoch['t_s']),image=path.name,
                              common_target_local_m=target,chief_ground_across_local=across)
                    if crop is not None:pose['crop']=crop  # pixel (0,0) of the PNG is sensor pixel (x0,y0)
                    key=(frame,json.dumps(crop))
                    if args.skip_existing and valid_png(path,out_w,out_h):
                        counts['skipped']+=1;pose_sink(index,key,pose,False)
                        continue
                    # Write to a temporary name and rename, so a killed process never leaves a
                    # truncated frame_KKKK.png behind (valid_png would reject it anyway).
                    partial=path.with_name(f'partial_{os.getpid()}_{path.name}')
                    scene.render.filepath=str(partial)
                    print(f'Frame {frame} satellite {index}: corner error {error:.2g}px; {gsd:.4f}m={pixel_step:.6f}px',flush=True)
                    render_call_seconds=png_write_seconds=None
                    if eevee:
                        # Separate the render call from the PNG write (same scene image settings).
                        start=time.monotonic();bpy.ops.render.render()
                        render_call_seconds=time.monotonic()-start
                        bpy.data.images['Render Result'].save_render(str(partial))
                        render_seconds=time.monotonic()-start;png_write_seconds=render_seconds-render_call_seconds
                        if header_pending:
                            import gpu
                            header.update(eevee_gpu=dict(backend=gpu.platform.backend_type_get(),
                                vendor=gpu.platform.vendor_get(),renderer=gpu.platform.renderer_get(),
                                version=gpu.platform.version_get()),eevee_gpu_processes=gpu_process_snapshot())
                            write_header();header_pending=False
                    else:
                        start=time.monotonic();bpy.ops.render.render(write_still=True)
                        render_seconds=time.monotonic()-start
                    os.replace(partial,path)
                    coverage=snapshot=None;coverage_seconds=snapshot_seconds=None
                    if frame%args.coverage_every==0:
                        start=time.monotonic()
                        saved=bpy.data.images.load(str(path.resolve()),check_existing=False)
                        pixels=np.empty(out_w*out_h*4,dtype=np.float32)
                        saved.pixels.foreach_get(pixels);pixels=pixels.reshape(-1,4)
                        coverage=float(np.mean(pixels[:,3]>.5))
                        bpy.data.images.remove(saved);del pixels
                        coverage_seconds=time.monotonic()-start
                        start=time.monotonic();snapshot=gpu_process_snapshot();snapshot_seconds=time.monotonic()-start
                        print(f'Frame {frame} satellite {index}: geometry coverage {coverage:.2%}',flush=True)
                    pose_sink(index,key,pose,True);counts['rendered']+=1
                    if timing is not None:
                        timing['render_s']+=render_seconds;timing['coverage_s']+=coverage_seconds or 0.
                        timing['snapshot_s']+=snapshot_seconds or 0.;timing['frames']+=1
                        if eevee:
                            timing['render_call_s']+=render_call_seconds;timing['png_write_s']+=png_write_seconds
                        mib=snapshot_memory(snapshot)[1]
                        if mib is not None:timing['gpu_mib_max']=max(timing.get('gpu_mib_max') or 0,mib)
                    results.append(dict(satellite_id=index,image=str(path.resolve()),crop=crop,png_bytes=path.stat().st_size,
                                        render_seconds=render_seconds,
                                        **(dict(render_call_seconds=render_call_seconds,png_write_seconds=png_write_seconds,
                                                clip_start_m=camera_data.clip_start,clip_end_m=camera_data.clip_end) if eevee else {}),
                                        corner_reprojection_error_px=error,native_gsd_m=gsd,pixels_per_native_gsd=pixel_step,
                                        common_target_error_pixels=target_error,ground_across_alignment_with_chief=alignment,
                                        gpu_process_snapshot=snapshot,gpu_snapshot_seconds=snapshot_seconds,
                                        geometry_coverage_fraction=coverage,coverage_reload_seconds=coverage_seconds))
            if results:
                append_line(report_path,dict(type='frame',frame=frame,t_s=epoch['t_s'],timestamp=epoch.get('timestamp'),
                    common_target_local_m=target,target_recompute_delta_m=target_delta,
                    basis_recompute_max_abs=basis_delta,renders=results,
                    **(dict(worker_id=args.worker_id) if worker else {})))
    if worker:
        run_worker(args,render_frames,counts,worst,setup_start,eevee)
    render_frames(frames,selected,file_pose_sink)
    print(f'Checked {counts["checked"]} poses ({len(frames)} frames x {len(selected)} satellites); worst: {worst}',flush=True)
    if args.check_only:
        print('All Blender camera projection checks passed',flush=True)
        sys.stdout.flush();os._exit(0)
    if header_pending:write_header()
    print(f'Rendered {counts["rendered"]}, skipped {counts["skipped"]} existing',flush=True)
    print('Rendering complete',flush=True)
    # Avoid the installed snap build's headless shutdown hang after flushing files.
    sys.stdout.flush();sys.stderr.flush();os._exit(0)


def run_worker(args,render_frames,counts,worst,setup_start,eevee):
    """Worker mode (--task-queue): claim tasks until every task of the queue is done.

    Queue layout (written by pipeline/render_pool.py):
      pending/NAME.json             task {"satellite": s, "frames": [start, stop]}
      claimed/NAME@WORKER.json      claimed by an atomic rename from pending/ (one winner)
      done/NAME.json                done marker: poses of every frame of the task, timings, worker
      workers/WORKER.json           status: pid, state, setup time, GPU memory, tasks done
      stop/WORKER                   written by the driver: finish the current task, claim no other
    A worker that finds no pending task waits while other workers still hold claimed tasks
    (the driver puts a dead worker's task back in pending/), and exits when none is left.
    """
    queue=args.task_queue
    pending,claimed,done=queue/'pending',queue/'claimed',queue/'done'
    status=dict(worker_id=args.worker_id,pid=os.getpid(),state='ready',
                setup_seconds=time.monotonic()-setup_start,tasks_done=0,frames_rendered=0,
                gpu_uuid=None,gpu_mib=None,gpu_mib_after_first_task=None)
    status_path=queue/'workers'/f'{args.worker_id}.json'
    write_json_atomic(status_path,status)
    print(f'Worker {args.worker_id}: scene ready after {status["setup_seconds"]:.1f} s',flush=True)

    def driver_alive():
        return not args.parent_pid or os.getppid()==args.parent_pid

    stop_path=queue/'stop'/args.worker_id  # written by the driver when this worker adds no throughput

    def claim():
        while driver_alive():
            if stop_path.exists():
                print(f'Worker {args.worker_id}: stopped by the driver ({stop_path.read_text().strip()})',flush=True)
                return None
            for name in sorted(os.listdir(pending)):
                if not name.endswith('.json'):continue
                target=claimed/f'{name[:-5]}@{args.worker_id}.json'
                try:os.rename(pending/name,target)
                except FileNotFoundError:continue  # another worker was faster
                return name[:-5],target
            if not any(n.endswith('.json') for n in os.listdir(claimed)):return None
            time.sleep(2)  # tasks still held by other workers: one may die and be requeued
        print(f'Driver process {args.parent_pid} is gone: worker {args.worker_id} exits',flush=True)
        sys.stdout.flush();os._exit(3)

    while (claimed_task:=claim()) is not None:
        name,claimed_path=claimed_task
        task=json.loads(claimed_path.read_text())
        sat=int(task['satellite']);start,stop=task['frames']
        status.update(state='rendering',task=name);write_json_atomic(status_path,status)
        poses={}
        timing=dict(frames=0,render_s=0.,render_call_s=0.,png_write_s=0.,coverage_s=0.,snapshot_s=0.,gpu_mib_max=None)
        claimed_at=time.time();rendered_before,skipped_before=counts['rendered'],counts['skipped']
        render_frames(range(start,stop),{sat},lambda index,key,pose,_:poses.__setitem__(key,pose),timing)
        snapshot=gpu_process_snapshot();uuid,mib=snapshot_memory(snapshot)
        peak=max([m for m in (mib,timing['gpu_mib_max']) if m is not None],default=None)
        rendered=counts['rendered']-rendered_before
        marker=dict(task=name,satellite=sat,frames=[start,stop],worker_id=args.worker_id,pid=os.getpid(),
                    claimed_at=claimed_at,finished_at=time.time(),rendered=rendered,
                    skipped=counts['skipped']-skipped_before,gpu_uuid=uuid,gpu_mib_after_task=mib,gpu_mib_max=peak,timing=timing,
                    poses=[poses[key] for key in sorted(poses,key=lambda key:(key[0],key[1]))])
        write_json_atomic(done/f'{name}.json',marker)
        claimed_path.unlink()
        status['tasks_done']+=1;status['frames_rendered']+=rendered
        if uuid:status['gpu_uuid']=uuid
        if peak is not None:
            status['gpu_mib']=max(status['gpu_mib'] or 0,peak)
            if rendered and status['gpu_mib_after_first_task'] is None:status['gpu_mib_after_first_task']=peak
        status.update(state='idle',task=None);write_json_atomic(status_path,status)
        print(f'Worker {args.worker_id}: task {name} done ({rendered} rendered, {time.time()-claimed_at:.1f} s)',flush=True)
    status.update(state='finished');write_json_atomic(status_path,status)
    print(f'Worker {args.worker_id}: queue empty after {status["tasks_done"]} tasks; '
          f'checked {counts["checked"]} poses; worst: {worst}',flush=True)
    print('Rendering complete',flush=True)
    sys.stdout.flush();sys.stderr.flush();os._exit(0)

if __name__=='__main__':main()
