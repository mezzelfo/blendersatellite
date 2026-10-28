"""Build small colored height meshes directly from selected Melbourne ZIPs.

Each cell retains the highest sampled face centroid and its JPG texture color.
This is an upper-surface preview mesh, not a full-facade photomesh export.
"""
import argparse
from array import array
import io
import json
from pathlib import Path
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import torch
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import distance_transform_edt


def prepare_tile(archive_path, output, manifest, spacing, device='cpu'):
    start=time.monotonic()
    gpu=torch.device(device).type=='cuda'
    if gpu and not torch.cuda.is_available():
        raise RuntimeError('CUDA terrain sampling requested but CUDA is unavailable')
    with zipfile.ZipFile(archive_path) as archive:
        obj=next(n for n in archive.namelist() if n.lower().endswith('.obj'))
        mtl=next(n for n in archive.namelist() if n.lower().endswith('.mtl'))
        folder=str(Path(obj).parent)
        maps={}; material=None
        for line in archive.read(mtl).decode().splitlines():
            if line.startswith('newmtl '): material=line.split(maxsplit=1)[1]
            elif line.startswith('map_Kd '): maps[material]=line.split(maxsplit=1)[1]
        vertices=array('d'); uvs=array('f'); vi=array('i'); ti=array('i')
        v=None; uv=None; height=None; colors=None; minxy=None; size=None
        v_gpu=None;uv_gpu=None;minxy_gpu=None;size_gpu=None

        def flush():
            if not vi: return
            indices=np.array(vi).reshape(-1,3)
            texindices=np.array(ti).reshape(-1,3)
            if gpu:
                xyz_gpu=v_gpu[torch.as_tensor(indices,dtype=torch.int64,device=device)].mean(1)
                tex_gpu=uv_gpu[torch.as_tensor(texindices,dtype=torch.int64,device=device)].mean(1)
                cells_gpu=torch.floor((xyz_gpu[:,:2]-minxy_gpu)/spacing).to(torch.int64)
                cells_gpu=torch.minimum(cells_gpu,size_gpu-1)
                keys_gpu=cells_gpu[:,1]*int(size[0])+cells_gpu[:,0]
                # Stable two-pass lexicographic sort: cell, then height.
                order_gpu=torch.argsort(xyz_gpu[:,2],stable=True)
                order_gpu=order_gpu[torch.argsort(keys_gpu[order_gpu],stable=True)]
                sorted_gpu=keys_gpu[order_gpu]
                keep_gpu=torch.cat((sorted_gpu[1:]!=sorted_gpu[:-1],torch.ones(1,dtype=torch.bool,device=device)))
                chosen_gpu=order_gpu[keep_gpu]
                xyz=xyz_gpu[chosen_gpu].cpu().numpy()
                tex=tex_gpu[chosen_gpu].cpu().numpy()
                keys=keys_gpu[chosen_gpu].cpu().numpy()
                chosen=np.arange(len(keys))
            else:
                xyz=v[indices].mean(1)
                tex=uv[texindices].mean(1)
                cells=np.floor((xyz[:,:2]-minxy)/spacing).astype(int)
                cells=np.minimum(cells,size-1)
                keys=cells[:,1]*size[0]+cells[:,0]
                # Choose the highest surface within each cell for this material.
                order=np.lexsort((xyz[:,2], keys))
                keys_sorted=keys[order]
                keep=np.r_[keys_sorted[1:]!=keys_sorted[:-1],True]
                chosen=order[keep]; keys=keys[chosen]
            take=xyz[chosen,2]>height[keys]
            chosen=chosen[take]; keys=keys[take]
            if len(keys):
                texture=maps.get(material)
                if texture:
                    with Image.open(io.BytesIO(archive.read(folder+'/'+texture))) as im:
                        image=np.asarray(im.convert('RGB'))
                    coords=tex[chosen]
                    invalid_uv=~np.isfinite(coords).all(axis=1)
                    coords=np.nan_to_num(coords,nan=0.,posinf=1.,neginf=0.)
                    x=np.clip((coords[:,0]*image.shape[1]).astype(int),0,image.shape[1]-1)
                    y=np.clip(((1-coords[:,1])*image.shape[0]).astype(int),0,image.shape[0]-1)
                    colors[keys]=image[y,x]
                    colors[keys[invalid_uv]]=[96,96,96]
                else: colors[keys]=[100,100,100]
                height[keys]=xyz[chosen,2]
            del vi[:]; del ti[:]

        with archive.open(obj) as raw:
            for line in io.TextIOWrapper(raw,encoding='utf-8'):
                if line.startswith('v '):
                    vertices.extend(map(float,line.split()[1:4]))
                elif line.startswith('vt '):
                    uvs.extend(map(float,line.split()[1:3]))
                elif line.startswith('usemtl ') or line.startswith('f '):
                    if v is None:
                        v=np.asarray(vertices).reshape(-1,3); uv=np.asarray(uvs).reshape(-1,2)
                        minxy=v[:,:2].min(0)
                        size=np.ceil((v[:,:2].max(0)-minxy)/spacing).astype(int)
                        height=np.full(int(np.prod(size)),-np.inf)
                        colors=np.zeros((len(height),3),dtype=np.uint8)
                        if gpu:
                            v_gpu=torch.tensor(v,dtype=torch.float64,device=device)
                            uv_gpu=torch.tensor(uv,dtype=torch.float64,device=device)
                            minxy_gpu=torch.tensor(minxy,dtype=torch.float64,device=device)
                            size_gpu=torch.tensor(size,dtype=torch.int64,device=device)
                    if line.startswith('usemtl '):
                        flush(); material=line.split()[1]
                    else:
                        fields=line.split()[1:]
                        # The published photomesh uses triangles. Fan triangulate
                        # polygons if a later archive contains them.
                        for k in range(1,len(fields)-1):
                            for field in (fields[0],fields[k],fields[k+1]):
                                split=field.split('/')
                                vi.append(int(split[0])-1); ti.append(int(split[1])-1)
                        if len(vi)>=150000: flush()
        flush()
    shape=(size[1],size[0]); valid=np.isfinite(height).reshape(shape)
    if not valid.any(): raise ValueError(f'No usable faces: {archive_path}')
    nearest=distance_transform_edt(~valid,return_distances=False,return_indices=True)
    h=height.reshape(shape)[tuple(nearest)]
    rgb=colors.reshape(*shape,3)[tuple(nearest)]
    # Horizontal coordinates remain in the source projected CRS until transformed
    # in double precision into the same ENU frame as the camera poses.
    xx,yy=np.meshgrid(minxy[0]+(np.arange(size[0])+.5)*spacing,
                      minxy[1]+(np.arange(size[1])+.5)*spacing)
    geo=Transformer.from_crs(7855,7844,always_xy=True)
    ecef=Transformer.from_crs(4979,4978,always_xy=True)
    lon,lat=geo.transform(xx.ravel(),yy.ravel())
    earth=np.array(ecef.transform(lon,lat,h.ravel())).T
    rotation=np.asarray(manifest['local_from_ecef']); origin=np.asarray(manifest['origin_ecef_m'])
    if gpu:
        local=((torch.as_tensor(earth,dtype=torch.float64,device=device)-torch.as_tensor(origin,device=device))
               @ torch.as_tensor(rotation.T,device=device)).cpu().numpy()
    else:
        local=(earth-origin) @ rotation.T
    temporary=output.with_suffix('.npz.part')
    with temporary.open('wb') as stream:
        np.savez_compressed(stream,vertices=local.astype(np.float32),rgb=rgb.reshape(-1,3),
                            shape=np.array(shape),sampled_fraction=float(valid.mean()),compute_device=str(device),
                            compute_device_name=torch.cuda.get_device_name(device) if gpu else 'CPU')
    temporary.replace(output)
    print(f'{archive_path.stem}: {shape}, sampled {valid.mean():.1%}, {device}, {time.monotonic()-start:.1f}s',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cameras',type=Path,required=True)
    parser.add_argument('--archives',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--force',action='store_true',help='rebuild existing selected caches')
    parser.add_argument('--device',default='cuda:0',help='numerical sampling device; ZIP/JPEG decoding and pyproj remain on CPU')
    args=parser.parse_args()
    manifest=json.loads(args.cameras.read_text())
    if torch.device(args.device).type=='cuda':
        if not torch.cuda.is_available():raise RuntimeError('CUDA terrain sampling required but unavailable')
        print(f'Terrain sampling device: {args.device} ({torch.cuda.get_device_name(args.device)})',flush=True)
        torch.ones(1,device=args.device).square().sum().item()
    args.output.mkdir(parents=True,exist_ok=True)
    cache_frame={key:manifest[key] for key in ('origin_ecef_m','local_from_ecef','mesh_grid_spacing_m')}
    frame_path=args.output/'cache_frame.json'
    if frame_path.exists() and json.loads(frame_path.read_text())!=cache_frame:
        raise ValueError('Cache frame or grid spacing differs; use a new output directory')
    frame_path.write_text(json.dumps(cache_frame,indent=2)+'\n')
    tiles=manifest['selected_tiles'][:args.limit]
    # CUDA contexts must not be inherited by forked workers.
    import multiprocessing
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        jobs=[]
        cached=0
        for i,tile in enumerate(tiles):
            destination=args.output/(tile+'.npz')
            if destination.exists() and not args.force:
                cached+=1;continue
            jobs.append(pool.submit(prepare_tile,args.archives/(tile+'.zip'),destination,
                                    manifest,manifest['mesh_grid_spacing_m'],args.device))
        print(f'Terrain: {cached} cached tiles, {len(jobs)} to prepare',flush=True)
        for job in jobs:job.result()
    print(f'Terrain cache: {args.output}',flush=True)


if __name__=='__main__': main()
