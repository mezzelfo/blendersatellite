"""Exercise ZIP texture sampling and upper-surface selection on a tiny mesh."""
import io
import zipfile

import numpy as np
import pytest
import torch
from PIL import Image
from pyproj import Transformer

from prepare_swarm_terrain import prepare_tile


@pytest.mark.parametrize('uv,expected', [('0.5 0.5',[0,0,255]),('nan nan',[96,96,96])])
@pytest.mark.parametrize('device',['cpu',pytest.param('cuda:0',marks=pytest.mark.skipif(
    not torch.cuda.is_available(),reason='CUDA unavailable'))])
def test_upper_surface_and_texture(tmp_path,uv,expected,device):
    archive=tmp_path/'fixture.zip'
    base=320000.;north=5812000.
    vertices=''.join(f'v {base+x} {north+y} {z}\n'
                     for z in (0,20) for x,y in ((0,0),(10,0),(10,10),(0,10)))
    obj=vertices+f'vt {uv}\nusemtl ground\nf 1/1 2/1 3/1\nf 1/1 3/1 4/1\n'
    obj+='usemtl roof\nf 5/1 6/1 7/1\nf 5/1 7/1 8/1\n'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('fixture/fixture.obj',obj)
        z.writestr('fixture/fixture.mtl','newmtl ground\nmap_Kd red.jpg\nnewmtl roof\nmap_Kd blue.jpg\n')
        for name,color in [('red',(255,0,0)),('blue',(0,0,255))]:
            buffer=io.BytesIO();Image.new('RGB',(4,4),color).save(buffer,format='JPEG')
            z.writestr(f'fixture/{name}.jpg',buffer.getvalue())
    destination=tmp_path/'mesh.npz'
    prepare_tile(archive,destination,dict(local_from_ecef=np.eye(3).tolist(),origin_ecef_m=[0,0,0]),5,device)
    with np.load(destination) as data:
        assert data['shape'].tolist()==[2,2]
        assert str(data['compute_device'])==device
        np.testing.assert_allclose(data['rgb'],np.tile(expected,(4,1)),atol=2)
        lon,lat,alt=Transformer.from_crs(4978,4979,always_xy=True).transform(*data['vertices'].T)
        np.testing.assert_allclose(alt,20,atol=.4)  # cache stores float32 local coordinates
