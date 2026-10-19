"""Synthetic tests for non-destructive overlay calibration."""
from dataclasses import replace

import laspy
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from elios_colorizer.calibration_workbench import (
    CalibrationAdjustments, CalibrationSession, render_cloud_composite,
    render_overlay, sample_las_points, save_refined_calibration,
)
from elios_colorizer.camera import Calibration, CalibrationError
from elios_colorizer.calibration_ui import parse_reference_time
from elios_colorizer.colorize import FrameObservation


@pytest.fixture
def camera():
    return Calibration(160, 120, 90, 90, 80, 60, (0, 0, 0, 1), (0, 0, 0),
                       (0, 1, 0), (0, 0, 0), 1, 0,
                       profile_name="Synthetic base", validated=True)


def test_adjustments_are_relative_bounded_and_keep_fallback(camera):
    values = CalibrationAdjustments(focal_scale=1.01, principal_x_px=4,
        principal_y_px=-3, mount_yaw_degrees=.5, translation_x_m=.01,
        timing_offset_s=.025)
    refined = values.apply(camera, reviewed_frames=4, alignment_score=.72)
    assert refined.schema_version == 2
    assert refined.fx == pytest.approx(camera.fx * 1.01)
    assert refined.cx == pytest.approx(camera.cx + 4)
    assert refined.video_to_telemetry_offset_s == pytest.approx(.025)
    assert refined.fallback() == camera
    assert refined.refinement['alignment_score'] == pytest.approx(.72)
    with pytest.raises(CalibrationError, match="Focal scale"):
        CalibrationAdjustments(focal_scale=1.5)


@pytest.mark.parametrize(("field", "axis"), [
    ("mount_roll_degrees", np.array([0.0, 0.0, 1.0])),
    ("mount_pitch_degrees", np.array([1.0, 0.0, 0.0])),
    ("mount_yaw_degrees", np.array([0.0, 1.0, 0.0])),
])
def test_mount_labels_rotate_about_optical_camera_axes(camera, field, axis):
    refined = CalibrationAdjustments(**{field: 1.0}).apply(camera)
    rotvec = Rotation.from_quat(refined.rotation_xyzw).as_rotvec()
    np.testing.assert_allclose(rotvec, np.deg2rad(1.0) * axis, atol=1e-10)


def test_refined_profile_is_saved_separately(camera, tmp_path):
    base = tmp_path / "base.json"
    base.write_text("fallback stays byte-identical", encoding="utf-8")
    refined = CalibrationAdjustments(principal_y_px=2).apply(camera)
    with pytest.raises(CalibrationError, match="never overwritten"):
        save_refined_calibration(refined, base, base_path=base)
    output = save_refined_calibration(refined, tmp_path / "refined.json", base_path=base)
    assert base.read_text(encoding="utf-8") == "fallback stays byte-identical"
    assert Calibration.load(output).fallback() == camera


def test_las_sampling_and_overlay_are_bounded(camera, tmp_path):
    grid_x, grid_y = np.meshgrid(np.linspace(-1.5, 1.5, 80), np.linspace(-1, 1, 60))
    xyz = np.column_stack((grid_x.ravel(), grid_y.ravel(), np.full(grid_x.size, 3.0)))
    data = laspy.create(point_format=0, file_version="1.2")
    data.x, data.y, data.z = xyz.T
    source = tmp_path / "wall.las"
    data.write(source)
    sampled = sample_las_points(source, maximum_points=500)
    assert 400 <= len(sampled) <= 500

    image = np.full((120, 160, 3), 45, dtype=np.uint8)
    image[:, 79:82] = 240
    frame = FrameObservation(image, (0, 0, 0), (0, 0, 0, 1), 0, 0)
    result = render_overlay(frame, xyz, camera, point_radius=1)
    assert result.rgb.shape == image.shape
    assert result.projected_points > 1000
    assert result.guide_points > 0
    assert 0 <= result.matched_guide_points <= result.guide_points
    assert 0 <= result.alignment_score <= 1
    assert np.any(result.rgb != image)

    shifted = replace(camera, cx=camera.cx + 20)
    shifted_result = render_overlay(frame, xyz, shifted, point_radius=1)
    assert shifted_result.projected_points > 0

    composite = render_cloud_composite(
        frame, xyz, camera, target_points=2_000, point_radius=4,
        video_opacity=.5, color_mode="elevation", preview_width=320)
    assert composite.rgb.shape == image.shape
    assert 1_000 <= composite.sampled_points <= 2_000
    assert composite.visible_points > 0
    assert np.any(composite.rgb != image)
    context = render_cloud_composite(
        frame, xyz, camera, target_points=2_000, display_mode="context",
        context_angles=(10, 15), preview_width=320)
    assert context.visible_points > 0


def test_adaptive_las_sampling_concentrates_on_reference_frustum(camera, tmp_path):
    rng = np.random.default_rng(8)
    focused = np.column_stack((
        rng.uniform(-1.0, 1.0, 5_000), rng.uniform(-.7, .7, 5_000),
        rng.uniform(2.5, 3.5, 5_000)))
    distant = np.column_stack((
        rng.uniform(90.0, 110.0, 5_000), rng.uniform(-5.0, 5.0, 5_000),
        rng.uniform(2.5, 3.5, 5_000)))
    data = laspy.create(point_format=0, file_version="1.2")
    data.x, data.y, data.z = np.concatenate((focused, distant), axis=0).T
    source = tmp_path / "adaptive.las"
    data.write(source)
    frame = FrameObservation(np.zeros((120, 160, 3), dtype=np.uint8),
                             (0, 0, 0), (0, 0, 0, 1), 0, 0)
    sampled = sample_las_points(
        source, maximum_points=1_000, focus_views=[(frame, camera)])
    assert len(sampled) == 1_000
    assert np.mean(sampled[:, 0] < 10) > .70
    assert np.any(sampled[:, 0] > 50)  # sparse global context is retained


def test_session_applies_timing_offset_to_pose(camera, tmp_path):
    class Telemetry:
        def __init__(self):
            self.requested = None
        def interpolate(self, timestamp):
            self.requested = timestamp
            return type("Pose", (), dict(position_world_m=np.array([timestamp, 0, 0]),
                orientation_xyzw=np.array([0, 0, 0, 1]), camera_pitch_degrees=0))()
    telemetry = Telemetry()
    frame = FrameObservation(np.zeros((120, 160, 3), dtype=np.uint8),
                             (0, 0, 0), (0, 0, 0, 1), 0, 10)
    session = CalibrationSession(tmp_path / "base.json", camera, np.zeros((1, 3)),
                                 telemetry, [frame], [2.0])
    refined = CalibrationAdjustments(timing_offset_s=.075).apply(camera)
    aligned = session.frame_for(0, refined)
    assert telemetry.requested == pytest.approx(10.075)
    assert aligned.position_world_m[0] == pytest.approx(10.075)


def test_manual_reference_time_parser():
    assert parse_reference_time("2:34") == 154
    assert parse_reference_time("00:05") == 5
    with pytest.raises(ValueError, match="Seconds"):
        parse_reference_time("1:75")
    with pytest.raises(ValueError, match="minutes:seconds"):
        parse_reference_time("154")


def test_session_uses_reference_specific_flight_context(camera, tmp_path):
    class Telemetry:
        def __init__(self, marker):
            self.marker = marker
        def interpolate(self, timestamp):
            return type("Pose", (), dict(position_world_m=np.array([self.marker, 0, 0]),
                orientation_xyzw=np.array([0, 0, 0, 1]), camera_pitch_degrees=0))()

    frames = [FrameObservation(np.zeros((120, 160, 3), dtype=np.uint8),
                               (0, 0, 0), (0, 0, 0, 1), 0, value)
              for value in (10, 20)]
    xyz = [np.full((1, 3), 1.0), np.full((1, 3), 2.0)]
    telemetry = [Telemetry(1), Telemetry(2)]
    session = CalibrationSession(tmp_path / "base.json", camera, xyz[0], telemetry[0],
        frames, [2.0, 4.0], xyz, telemetry, ["Flight 1", "Flight 6"])
    assert session.frame_for(1, camera).position_world_m[0] == 2
    assert session.xyz_for(1)[0, 0] == 2
    assert session.label_for(1) == "Flight 6 · 0:04"


def test_session_moves_camera_pose_into_merged_cloud_coordinates(camera, tmp_path):
    class Telemetry:
        def interpolate(self, timestamp):
            return type("Pose", (), dict(position_world_m=np.array([1., 2., 3.]),
                orientation_xyzw=np.array([0., 0., 0., 1.]), camera_pitch_degrees=0))()

    frame = FrameObservation(np.zeros((120, 160, 3), dtype=np.uint8),
                             (0, 0, 0), (0, 0, 0, 1), 0, 10)
    matrix = np.eye(4)
    matrix[:3, :3] = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    matrix[:3, 3] = (10, 0, 0)
    telemetry = Telemetry()
    session = CalibrationSession(
        tmp_path / "base.json", camera, np.zeros((1, 3)), telemetry, [frame], [2.0],
        [np.zeros((1, 3))], [telemetry], ["Flight 1"], [matrix], ["Merged cloud"])
    transformed = session.frame_for(0, camera)
    np.testing.assert_allclose(transformed.position_world_m, [8, 1, 3])
    np.testing.assert_allclose(
        Rotation.from_quat(transformed.orientation_xyzw).as_matrix(), matrix[:3, :3], atol=1e-8)
