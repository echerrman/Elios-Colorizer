"""Synthetic tests with known geometry, independent of proprietary flight data."""

from dataclasses import replace
from pathlib import Path

import laspy
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from elios_colorizer.camera import Calibration, CalibrationError
from elios_colorizer.colorize import (
    adaptively_select_observations,
    ColorizationCancelled, ColorizationError, ColorizationOptions,
    FrameObservation, colorize_las, group_nearby_observations, reduce_candidate_shards,
)


def test_parallel_candidate_reduction_matches_global_best_view(tmp_path):
    camera = Calibration(100, 100, 50, 50, 50, 50, (0, 0, 0, 1), (0, 0, 0),
                         (0, 1, 0), (0, 0, 0), 1, 0, validated=True)
    make_las(tmp_path / 'source.las', [(0, 0, 2), (.2, 0, 2), (-.2, 0, 2)])
    red = solid_frame((220, 20, 20), source_flight=1)
    blue_image = np.full((100, 100, 3), (20, 20, 220), dtype=np.uint8)
    blue = FrameObservation(blue_image, (.15, 0, 0), (0, 0, 0, 1), 0, 2., source_flight=2)
    options = ColorizationOptions(chunk_size=2, frame_batch_size=1,
                                  depth_buffer_width=100, minimum_sharpness=0,
                                  reject_exposure_extremes=False,
                                  occlusion_absolute_tolerance_m=.01,
                                  occlusion_relative_tolerance=0, merged_output=True)
    expected = colorize_las(tmp_path / 'source.las', tmp_path / 'expected.las',
                            [red, blue], camera, options=options)
    first = colorize_las(tmp_path / 'source.las', tmp_path / 'unused-1.las',
                         [red], camera, options=options,
                         candidate_directory=tmp_path / 'candidate-1')
    second = colorize_las(tmp_path / 'source.las', tmp_path / 'unused-2.las',
                          [blue], camera, options=options,
                          candidate_directory=tmp_path / 'candidate-2')
    reduced = reduce_candidate_shards(tmp_path / 'source.las', tmp_path / 'reduced.las',
                                      [first.output_path, second.output_path], chunk_size=2)
    expected_las, reduced_las = laspy.read(expected.output_path), laspy.read(reduced.output_path)
    for name in expected_las.point_format.dimension_names:
        np.testing.assert_array_equal(expected_las[name], reduced_las[name])


@pytest.fixture
def camera():
    return Calibration(100, 100, 50, 50, 50, 50, (0, 0, 0, 1), (0, 0, 0),
                       (0, 1, 0), (0, 0, 0), 1, 0, validated=True)


@pytest.fixture
def options():
    return ColorizationOptions(chunk_size=2, frame_batch_size=1,
        depth_buffer_width=100, minimum_sharpness=0, reject_exposure_extremes=False,
        occlusion_absolute_tolerance_m=0.01, occlusion_relative_tolerance=0)


def make_las(path: Path, xyz, *, point_format=1, version="1.2"):
    header = laspy.LasHeader(point_format=point_format, version=version)
    header.scales = [0.001, 0.001, 0.001]
    header.offsets = [0.123, -0.456, 0.789]
    header.vlrs.append(laspy.VLR(user_id="test_data", record_id=43,
                                description="Metadata must survive", record_data=b"custom payload"))
    data = laspy.LasData(header)
    data.add_extra_dim(laspy.ExtraBytesParams("TestValue", np.float32))
    data.x, data.y, data.z = np.asarray(xyz).T
    data.intensity = np.arange(len(xyz), dtype=np.uint16) + 100
    data.classification = np.arange(len(xyz), dtype=np.uint8) % 4
    data.TestValue = np.arange(len(xyz), dtype=np.float32) + 0.25
    if "gps_time" in header.point_format.dimension_names:
        data.gps_time = np.arange(len(xyz), dtype=float) + 123456.7
    data.write(path)
    return data


def solid_frame(rgb=(100, 150, 200), **kwargs):
    image = np.empty((100, 100, 3), dtype=np.uint8)
    image[:] = rgb
    return FrameObservation(image, (0, 0, 0), (0, 0, 0, 1), 0, 1.0, **kwargs)


def test_adaptive_selection_and_spatial_view_grouping():
    base = solid_frame()
    frames = [
        replace(base, position_world_m=(0, 0, 0), timestamp_s=0),
        replace(base, position_world_m=(.01, 0, 0), timestamp_s=1),
        replace(base, position_world_m=(.04, 0, 0), timestamp_s=2),
        replace(base, position_world_m=(.04, 0, 0), timestamp_s=4),
    ]
    selection_stats = {}
    selected = list(adaptively_select_observations(frames, stats=selection_stats))
    assert selection_stats == {'candidates': 4, 'retained': 3, 'skipped': 1}
    assert [frame.timestamp_s for frame in selected] == [0, 2, 4]

    positions = [0, 100, 1, 101]
    unordered = [replace(base, position_world_m=(x, 0, 0), timestamp_s=i)
                 for i, x in enumerate(positions)]
    grouping_stats = {}
    grouped = list(group_nearby_observations(unordered, 2, lookahead_batches=2,
                                             stats=grouping_stats))
    assert [frame.position_world_m[0] for frame in grouped] == [0, 1, 100, 101]
    assert grouping_stats['groups'] == 2
    assert grouping_stats['reordered_views'] == 2


@pytest.mark.parametrize("point_format,version,expected", [(0, "1.2", 2), (1, "1.2", 3), (6, "1.4", 7)])
def test_las_preservation_and_occlusion_across_chunks(tmp_path, camera, options, point_format, version, expected):
    # Front/back share a pixel and lie in separate LAS chunks.
    source = make_las(tmp_path / "source.las", [(0, 0, 2), (1, 0, 2), (0, 0, 4), (0, 0, -2)],
                      point_format=point_format, version=version)
    result = colorize_las(tmp_path / "source.las", tmp_path / "output.las", [solid_frame()], camera, options=options)
    output = laspy.read(result.output_path)
    assert result.point_count == 4 and result.colored_point_count == 2
    assert result.coverage_fraction == 0.5
    assert output.point_format.id == expected
    for name in source.point_format.dimension_names:
        np.testing.assert_array_equal(output[name], source[name])
    np.testing.assert_array_equal(output.header.scales, source.header.scales)
    np.testing.assert_array_equal(output.header.offsets, source.header.offsets)
    assert next(v for v in output.vlrs if v.user_id == "test_data").record_data == b"custom payload"
    np.testing.assert_array_equal(output.Colorized, [1, 1, 0, 0])
    np.testing.assert_array_equal(output.red, [25700, 25700, 0, 0])
    np.testing.assert_array_equal(output.green, [38550, 38550, 0, 0])
    np.testing.assert_array_equal(output.blue, [51400, 51400, 0, 0])
    assert not list(tmp_path.glob(".elios-colorize-*"))


def test_body_orientation_and_camera_tilt(camera):
    # A camera translated away from its pivot moves as the tilt rotates.
    camera = replace(camera, translation_m=(0, 0, 1))
    body = Rotation.from_euler("z", 90, degrees=True)
    center, rotation = camera.camera_pose((10, 20, 30), body.as_quat(), 90)
    np.testing.assert_allclose(center, [10, 21, 30], atol=1e-12)
    np.testing.assert_allclose(rotation @ [0, 0, 1], [0, 1, 0], atol=1e-12)
    point = center + rotation @ np.array([0, 0, 2])
    camera_coordinates = (point - center) @ rotation
    np.testing.assert_allclose(camera.project_camera_points(camera_coordinates[None]), [[50, 50]])


def test_bilinear_sampling_and_real_black_flag(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0.02, 0.02, 2), (-1, 0, 2), (0, 0, -2)])
    frame = solid_frame((0, 0, 0))
    frame.rgb[50, 50] = [20, 40, 60]
    frame.rgb[50, 51] = [40, 60, 80]
    frame.rgb[51, 50] = [60, 80, 100]
    frame.rgb[51, 51] = [80, 100, 120]
    colorize_las(tmp_path / "source.las", tmp_path / "output.las", [frame], camera, options=options)
    output = laspy.read(tmp_path / "output.las")
    np.testing.assert_array_equal(output.Colorized, [1, 1, 0])
    np.testing.assert_array_equal(output.red, [50 * 257, 0, 0])


def test_best_observation_wins(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0, 0, 3)])
    farther = replace(solid_frame((200, 50, 50)), position_world_m=(0, 0, -10), source_flight=1)
    nearer = replace(solid_frame((50, 200, 50)), source_flight=2)
    colorize_las(tmp_path / "source.las", tmp_path / "output.las", [farther, nearer], camera, options=options)
    output = laspy.read(tmp_path / "output.las")
    assert output.green[0] == 200 * 257
    assert output.SourceFlight[0] == 2


def test_multi_frame_fusion_rejects_bright_glare_outlier(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0, 0, 3)])
    true_values = [90, 100, 105, 110]
    frames = [replace(solid_frame((value, value, value)), timestamp_s=float(index),
                      source_flight=index + 1)
              for index, value in enumerate(true_values)]
    glare = solid_frame((240, 240, 240), source_flight=5)
    glare.rgb[::2, ::2] = 225
    glare.rgb[1::2, 1::2] = 225
    frames.append(replace(glare, timestamp_s=5.0))

    legacy = colorize_las(tmp_path / "source.las", tmp_path / "legacy.las", frames,
                          camera, options=options)
    fused = colorize_las(tmp_path / "source.las", tmp_path / "fused.las", frames,
                         camera, options=replace(options, multi_frame_fusion=True))
    legacy_cloud, fused_cloud = laspy.read(legacy.output_path), laspy.read(fused.output_path)
    assert legacy_cloud.red[0] // 257 >= 225
    assert fused_cloud.red[0] // 257 in true_values
    assert fused_cloud.SourceFlight[0] in (1, 2, 3, 4)
    assert fused.multi_frame_fusion['applied']
    assert fused.multi_frame_fusion['points_fused'] == 1
    assert fused.multi_frame_fusion['rejected_outlier_observations'] == 1


def test_multi_frame_fusion_falls_back_and_off_is_identical(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0, 0, 3), (.5, 0, 3)])
    frames = [solid_frame((80, 120, 200), source_flight=1),
              replace(solid_frame((100, 140, 220), source_flight=2), timestamp_s=2)]
    default = colorize_las(tmp_path / "source.las", tmp_path / "default.las", frames,
                           camera, options=options)
    explicit_off = colorize_las(tmp_path / "source.las", tmp_path / "off.las", frames,
                                camera, options=replace(options, multi_frame_fusion=False))
    fused = colorize_las(tmp_path / "source.las", tmp_path / "fallback.las", frames,
                         camera, options=replace(options, multi_frame_fusion=True))
    baseline = laspy.read(default.output_path)
    for path in (explicit_off.output_path, fused.output_path):
        candidate = laspy.read(path)
        for name in ('red', 'green', 'blue', 'Colorized', 'ColorConfidence',
                     'ColorDistance', 'SourceFlight'):
            np.testing.assert_array_equal(candidate[name], baseline[name])
    assert not default.multi_frame_fusion['requested']
    assert not fused.multi_frame_fusion['applied']
    assert fused.multi_frame_fusion['points_using_best_observation_fallback'] == 2


def test_optional_distance_limit_and_provenance(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0, 0, 2), (3, 0, 5)])
    colorize_las(tmp_path / "source.las", tmp_path / "output.las", [solid_frame()], camera,
                 options=replace(options, maximum_color_distance_m=3.0))
    output = laspy.read(tmp_path / "output.las")
    np.testing.assert_array_equal(output.Colorized, [1, 0])
    assert output.ColorConfidence[0] > 0
    assert output.ColorDistance[0] == pytest.approx(2.0)
    assert output.ColorConfidence[1] == output.ColorDistance[1] == 0


def test_cancel_is_atomic_and_does_not_touch_input(tmp_path, camera, options):
    source = tmp_path / "source.las"
    make_las(source, [(0, 0, 2), (1, 0, 2)])
    original = source.read_bytes()
    cancelled = [False]
    def progress(event):
        if event["stage"] == "colorizing":
            cancelled[0] = True
    with pytest.raises(ColorizationCancelled):
        colorize_las(source, tmp_path / "output.las", [solid_frame()], camera,
                     options=options, progress=progress, cancelled=lambda: cancelled[0])
    assert source.read_bytes() == original
    assert not (tmp_path / "output.las").exists()
    assert not list(tmp_path.glob(".elios-colorize-*"))


def test_refuses_unsafe_or_useless_output(tmp_path, camera, options):
    source = tmp_path / "source.las"
    make_las(source, [(0, 0, -2)])
    with pytest.raises(ColorizationError, match="different"):
        colorize_las(source, source, [solid_frame()], camera, options=options)
    with pytest.raises(ColorizationError, match="No LAS points"):
        colorize_las(source, tmp_path / "output.las", [solid_frame()], camera, options=options)
    assert not (tmp_path / "output.las").exists()
    output = tmp_path / "protected.las"
    output.write_bytes(b"do not overwrite")
    with pytest.raises(ColorizationError, match="already exists"):
        colorize_las(source, output, [solid_frame()], camera, options=options)
    assert output.read_bytes() == b"do not overwrite"


def test_calibration_validation_and_round_trip(camera, tmp_path):
    assert Calibration.from_dict(camera.to_dict()) == camera
    invalid = camera.to_dict()
    invalid["validated"] = False
    with pytest.raises(CalibrationError, match="validated"):
        Calibration.from_dict(invalid)
    invalid = camera.to_dict()
    del invalid["camera_to_body"]["tilt_axis_body"]
    with pytest.raises(CalibrationError, match="tilt_axis_body"):
        Calibration.from_dict(invalid)
    with pytest.raises(CalibrationError, match="unit xyzw"):
        replace(camera, rotation_xyzw=(0, 0, 0, 10))


def test_distributed_default_and_template_camera_profiles():
    profiles = Path(__file__).resolve().parents[1] / "camera_profiles"
    default = Calibration.load(profiles / "elios_3_builtin_rgb_default_profile.json")
    assert default.validated
    assert default.profile_name == "Elios 3 Built-in RGB Camera - Tested Default Baseline"
    assert (default.image_width, default.image_height) == (3840, 2160)
    with pytest.raises(CalibrationError, match="validated"):
        Calibration.load(profiles / "camera_profile_template.json")
    template = Calibration.load(profiles / "camera_profile_template.json",
                                require_validated=False)
    assert not template.validated


@pytest.mark.parametrize("model,coefficients", [("opencv", (0, 0, 0, 0, 0)), ("fisheye", (0, 0, 0, 0))])
def test_distortion_projection_center(camera, model, coefficients):
    calibrated = replace(camera, distortion_model=model, distortion_coefficients=coefficients)
    np.testing.assert_allclose(calibrated.project_camera_points(np.array([[0, 0, 2]])), [[50, 50]])


def test_blurred_frame_rejection(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0, 0, 2)])
    with pytest.raises(ColorizationError, match="No usable"):
        colorize_las(tmp_path / "source.las", tmp_path / "output.las", [solid_frame()], camera,
                     options=replace(options, minimum_sharpness=2))
    assert not (tmp_path / "output.las").exists()


def test_unvalidated_camera_requires_explicit_experiment(tmp_path, camera, options):
    make_las(tmp_path / "source.las", [(0, 0, 2)])
    data = camera.to_dict()
    data["validated"] = False
    experimental = Calibration.from_dict(data, require_validated=False)
    with pytest.raises(ColorizationError, match="validated"):
        colorize_las(tmp_path / "source.las", tmp_path / "output.las", [solid_frame()], experimental, options=options)
    result = colorize_las(tmp_path / "source.las", tmp_path / "output.las", [solid_frame()], experimental,
                           options=replace(options, experimental_calibration=True))
    assert result.experimental_calibration


@pytest.mark.parametrize('fusion', [False, True])
def test_acceleration_cache_is_color_identical(tmp_path, camera, options, fusion):
    points = [(x / 10, y / 10, 2 + ((x + y) % 3) / 10)
              for x in range(-5, 6) for y in range(-5, 6)]
    make_las(tmp_path / 'source.las', points)
    frames = [replace(solid_frame(color, source_flight=index + 1),
                      position_world_m=(position, 0, 0), timestamp_s=float(index))
              for index, (color, position) in enumerate([
                  ((80, 120, 200), 0), ((200, 100, 60), .1), ((90, 125, 195), -.1),
                  ((85, 118, 205), .05), ((88, 122, 198), -.05)])]
    colorize_las(tmp_path / 'source.las', tmp_path / 'streamed.las', frames, camera,
                 options=replace(options, chunk_size=13, frame_batch_size=1, cache_xyz=False,
                                 multi_frame_fusion=fusion))
    result = colorize_las(tmp_path / 'source.las', tmp_path / 'cached.las', frames, camera,
                          options=replace(options, chunk_size=17, frame_batch_size=2, cache_xyz=True,
                                          worker_threads=4, multi_frame_fusion=fusion))
    streamed, cached = laspy.read(tmp_path / 'streamed.las'), laspy.read(tmp_path / 'cached.las')
    for name in ('red', 'green', 'blue', 'Colorized', 'ColorConfidence',
                 'ColorDistance', 'SourceFlight'):
        np.testing.assert_array_equal(streamed[name], cached[name])
    assert result.xyz_cache_used
    assert result.multi_frame_fusion['requested'] == fusion
