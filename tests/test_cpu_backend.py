import numpy as np
import pytest

from elios_colorizer.camera import Calibration
from elios_colorizer.cpu_backend import project


@pytest.mark.parametrize('model,coefficients', [
    ('pinhole', ()),
    ('opencv', (-.08, .015, .0005, -.0003, .001)),
    ('fisheye', (-.03, .002, 0., 0.)),
])
def test_native_cpu_projection_matches_reference(model, coefficients):
    camera = Calibration(
        384, 216, 190, 191, 192, 108, (0, 0, 0, 1), (0, 0, 0),
        (0, 1, 0), (0, 0, 0), 1, 0, distortion_model=model,
        distortion_coefficients=coefficients, validated=True)
    rng = np.random.default_rng(42)
    points = rng.uniform((-3, -2, .05), (3, 2, 12), size=(20_000, 3))
    center = np.array((.1, -.2, .3))
    rotation = np.array(((.999, -.02, .03), (.021, .999, -.01),
                         (-.03, .011, .999)))
    native = project(points, center, rotation, camera, .15, 10., .02)
    if native is None:
        pytest.skip('Native CPU projection backend was not built')
    selected, uv, depths, camera_points = native
    reference_camera = (points - center) @ rotation
    reference_depth = reference_camera[:, 2]
    candidates = np.flatnonzero(np.isfinite(reference_camera).all(axis=1)
                                & (reference_depth >= .15) & (reference_depth <= 10.))
    reference_uv = camera.project_camera_points(reference_camera[candidates])
    pad_x, pad_y = camera.image_width * .02, camera.image_height * .02
    inside = (np.isfinite(reference_uv).all(axis=1)
              & (reference_uv[:, 0] >= pad_x) & (reference_uv[:, 1] >= pad_y)
              & (reference_uv[:, 0] < camera.image_width - 1 - pad_x)
              & (reference_uv[:, 1] < camera.image_height - 1 - pad_y))
    reference_selected = candidates[inside]
    np.testing.assert_array_equal(selected, reference_selected)
    np.testing.assert_allclose(uv, reference_uv[inside], rtol=2e-12, atol=2e-10)
    np.testing.assert_allclose(depths, reference_depth[reference_selected], rtol=0, atol=1e-12)
    np.testing.assert_allclose(camera_points, reference_camera[reference_selected], rtol=0, atol=1e-12)
