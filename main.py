from datetime import datetime
import bpy
import os
import json
import numpy as np

BLENDER_CAMERA_TEMPLATE = r"""
// Consider the EOGS affine camera model: \mathcal{A}(x) = Ax+a,
// mapping from world coordinate to image plane.
// Strip the last row of A and a, such that A is 2x3 and a is 2x1.
// The matrix A should be full-rank (i.e. 2-rank)

// Now we want to preprocess it, so that the camera shaders is as light as possible.
// 1. First we need to compute the ray direction (the nullspace of A).
//       This will be equal (after normalization) to the ray direction, so get the orientation right!
// 2. Then we need to output "the starting point" of the output ray.
//       We find this by solving before-hand the linear problem:
//       given_position_image_plane == A*x+a and x_3 == z0
//       Component by component:
//              A_11*x_1+A_12*x_2+A_13*x_3+a_1*1.0 = given_position_image_plane_1
//              A_21*x_1+A_22*x_2+A_23*x_3+a_2*1.0 = given_position_image_plane_2
//              0.00*x_1+0.00*x_2+1.00*x_3-z_0*1.0 = 0.0
//              0.00*x_1+0.00*x_2+0.00*x_3+1.0*1.0 = 1.0
//       Let Atilde be the affine 4x4 matrix on the left hand side. To the shader, we pass the inverse of Atilde.

shader affine_camera_simple(
    // outputs
    // Note from https://docs.blender.org/manual/en/latest/render/cycles/osl/camera.html
    //   Both position and direction are in camera coordinates,
    //   where the origin is the position of the camera itself,
    //   the positive Z axis is the view direction, and the positive Y axis is up.
    output point position = point(0.0,0.0,0.0),
    output vector direction = vector({TEMPLATED_DIRECTION}),
    output color throughput = color(1.0)
)
{
    matrix Atildeinv = matrix({TEMPLATED_ATILDEINV});
    
    position = point(camera_shader_raster_position());
    position[0] = position[0]*2.0-1;
    position[1] = position[1]*2.0-1;
    position[2] = 0;
    position = transform(Atildeinv, position);
    //printf("yolo: %f %f %f\n", position[0], position[1], position[2]);    
}
"""

def null_space(A, rcond=None):
    u, s, vh = np.linalg.svd(A, full_matrices=True)
    M, N = u.shape[0], vh.shape[1]
    if rcond is None:
        rcond = np.finfo(s.dtype).eps * max(M, N)
    tol = np.amax(s) * rcond
    num = np.sum(s > tol, dtype=int)
    Q = vh[num:,:].T.conj()
    return Q

def EOGS_to_blender(A,b,z0=-5.0):
    if A.shape == (3,3) and b.shape == (3,):
        A = A[:2,:]
        b = b[:2]

    assert A.shape == (2,3), A.shape
    assert b.shape == (2,), b.shape
    assert np.linalg.matrix_rank(A) == 2

    N = null_space(A).flatten()
    assert N.shape == (3,)

    Atilde = np.eye(4)
    Atilde[:2,:3] = A
    Atilde[:2,3] = b
    Atilde[2,-1] = -z0

    Atilde[0,:] *= -1  # flip x axis

    # Create the template values
    templated_direction = f"{N[0]}, {N[1]}, {N[2]}"
    templated_atildeinv = ", ".join(map(str, np.linalg.inv(Atilde).T.flatten().tolist()))

    camera_blender_code = (
        BLENDER_CAMERA_TEMPLATE
        .replace("{TEMPLATED_DIRECTION}", templated_direction)
        .replace("{TEMPLATED_ATILDEINV}", templated_atildeinv)
    )

    return camera_blender_code

# Loop over all the EOGS affine model and convert them to blender format
scene = 'JAX_068'
root_path = os.path.dirname(os.path.abspath(__file__))
print(f"Root path: {root_path}")
affine_model_path = os.path.join(root_path, 'EOGS_affine_models', scene, 'affine_models.json')
assert os.path.exists(affine_model_path), affine_model_path

# Create an output subdirectory using current time
output_path = os.path.join(root_path, 'output', datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(output_path, exist_ok=True)

with open(affine_model_path, "r") as f:
    affine_models = json.load(f)
    for camera in affine_models:
        camera_name = camera['img']

        if camera_name == 'Nadir':
            continue


        ################
        # Setup satellite affine camera
        ################
        A = np.array(camera['model']['coef_'])
        b = np.array(camera['model']['intercept_'])
        blender_code = EOGS_to_blender(A, b)
        temp_shader_path = "/tmp/temp_affine_camera.osl"
        with open(temp_shader_path, "w") as out_f:
            out_f.write(blender_code)
        print(f"Wrote blender camera shader to: {temp_shader_path}")
        # Assign the OSL script to the camera's custom property
        bpy.data.cameras["Camera"].type = 'CUSTOM'
        bpy.data.cameras["Camera"].custom_mode = 'EXTERNAL'
        bpy.data.cameras["Camera"].custom_filepath = temp_shader_path


        ################
        # Setup the light source
        ################
        Sdir = np.array(camera['sun_model']['sun_dir_ecef']).flatten()
        Sdir = Sdir / np.linalg.norm(Sdir)
        # Sdir[0] *= -1
        # Sdir[1] *= -1
        # bpy.data.lights["Light"].type = 'POINT'
        # bpy.data.objects["Light"].location = (Sdir*2).tolist()

        Sdir[-1] *= -1
        bpy.data.lights["Light"].type = 'SUN'
        bpy.data.objects["Light"].location = (2,2,2)
        bpy.data.objects["Light"].rotation_mode = 'QUATERNION' #wxyz
        localforward = np.array([0,0,-1])
        theta = np.dot(Sdir, localforward)
        axis = np.cross(localforward, Sdir)
        axis = axis / np.linalg.norm(axis)
        w = np.sqrt((1.0+theta)*0.5)
        x = axis[0]*np.sqrt((1.0-theta)*0.5)
        y = axis[1]*np.sqrt((1.0-theta)*0.5)
        z = axis[2]*np.sqrt((1.0-theta)*0.5)
        bpy.data.objects["Light"].rotation_quaternion = (w,x,y,z)


        ################
        # Set the image resolution
        ################
        bpy.context.scene.render.resolution_x = camera['width']
        bpy.context.scene.render.resolution_y = camera['height']

        ################
        # Render the scene from this camera
        ################
        bpy.context.scene.render.filepath = os.path.join(output_path, f'{camera_name}_render.png')
        bpy.ops.render.render(write_still=True)



# Quit Blender without saving the current file
bpy.ops.wm.quit_blender()