from datetime import datetime
import bpy
from mathutils import Vector
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

def start_from_scratch():
    """
    Start from a truly empty Blender scene, independent of any .blend file.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_tsdf_mesh_from_ply(ply_path):
    """
    Import the .ply mesh and force the object/data names expected by the script.
    """
    assert os.path.exists(ply_path), ply_path

    # Blender 4.x
    if hasattr(bpy.ops.wm, "ply_import"):
        bpy.ops.wm.ply_import(filepath=ply_path)
    # Older Blender versions
    else:
        bpy.ops.import_mesh.ply(filepath=ply_path)

    mesh_obj = bpy.context.object
    mesh_obj.name = "tsdf_mesh_B"
    mesh_obj.data.name = "tsdf_mesh_B"

    return mesh_obj


def create_camera():
    cam_data = bpy.data.cameras.new("Camera")
    cam_obj = bpy.data.objects.new("Camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)

    cam_obj.location = (0, 0, 0)
    cam_obj.rotation_euler = (0, 0, 0)

    bpy.context.scene.camera = cam_obj

    return cam_obj


def create_sun():
    light_data = bpy.data.lights.new("Light", type="SUN")
    light_obj = bpy.data.objects.new("Light", light_data)
    bpy.context.collection.objects.link(light_obj)

    light_obj.location = (2, 2, 2)

    return light_obj


def setup_scene_from_scratch(root_path):
    start_from_scratch()

    ply_path = os.path.join(root_path, "tsdf_mesh_B.ply")

    mesh_obj = import_tsdf_mesh_from_ply(ply_path)
    camera_obj = create_camera()
    light_obj = create_sun()

    bpy.context.scene.render.engine = "CYCLES"

    # Required for the OSL custom camera shader.
    bpy.context.scene.cycles.shading_system = True

    # Reduce the number of samples for faster rendering during testing.
    bpy.context.scene.cycles.samples = 4

    # Disable denoising for faster rendering during testing.
    bpy.context.scene.cycles.use_denoising = False

    return mesh_obj, camera_obj, light_obj

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
    # Convert coordinates used by the Blender mesh/shader into the coordinates
    # expected by the EOGS affine model.
    #
    # This says:
    #   x_EOGS =  x_Blender
    #   y_EOGS =  y_Blender
    #   z_EOGS = -z_Blender
    EOGS_FROM_BLENDER = np.diag([1.0, 1.0, -1.0])
    A = A @ EOGS_FROM_BLENDER


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

    # Convert between image/raster vertical-axis conventions.
    # This flips the second image coordinate, not the world X axis.
    # Many image/camera models use image coordinates where the vertical
    # coordinate increases downward, as in standard pixel row coordinates.
    # Blender-style camera/raster coordinates are treated as increasing upward.
    Atilde[1, :] *= -1

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

mesh_obj, camera_obj, light_obj = setup_scene_from_scratch(root_path)

affine_model_path = os.path.join(root_path, 'EOGS_affine_models', scene, 'affine_models.json')
assert os.path.exists(affine_model_path), affine_model_path

# Create an output subdirectory using current time
output_path = os.path.join(root_path, 'output', datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(output_path, exist_ok=True)

with open(affine_model_path, "r") as f:
    affine_models = json.load(f)

    # Extract the scale factor from the first camera model
    scale_factor = affine_models[0]['model']['scale']
    # Apply the scale factor to the mesh
    mesh_obj.scale = (1.0/scale_factor, 1.0/scale_factor, 1.0/scale_factor)

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
        bpy.data.lights["Light"].type = 'SUN'
        bpy.data.objects["Light"].location = (2, 2, 2)
        bpy.data.objects["Light"].rotation_mode = 'QUATERNION'
        bpy.data.objects["Light"].rotation_quaternion = Vector(Sdir).to_track_quat('Z', 'X')

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