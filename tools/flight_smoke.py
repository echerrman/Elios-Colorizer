"""Reproducible real-flight diagnostic, deliberately using UNVALIDATED optics.

This is a software/projection smoke test, not a calibrated deliverable. It
samples existing LAS records without reconstructing or modifying geometry.
Run from the project with PYTHONPATH=src.
"""
from dataclasses import replace
import json
from pathlib import Path
import cv2
import laspy
import numpy as np
from scipy.spatial.transform import Rotation
from elios_colorizer.camera import Calibration
from elios_colorizer.flight import discover_source, load_telemetry, iter_observations
from elios_colorizer.service import run_colorization


def main():
    folder = Path('outputs/diagnostics')
    folder.mkdir(parents=True, exist_ok=True)
    source = discover_source('Flight_Data')
    telemetry = load_telemetry(source, '.cache', progress=lambda *v: print(*v, flush=True))
    subset = folder / 'flight1_sample_source.las'
    if not subset.exists():
        with laspy.open(source.las_path) as reader, laspy.open(subset, mode='w', header=reader.header) as writer:
            offset = 0
            for points in reader.chunk_iterator(250_000):
                indices = np.arange((-offset) % 20, len(points), 20)
                writer.write_points(points[indices])
                offset += len(points)
    sample = laspy.read(subset)
    xyz = np.column_stack([sample.x, sample.y, sample.z])
    # This optical-to-body axis convention and .27m lever arm are hypotheses.
    # The navigation-camera mount suggests front is body +X; it is NOT an RGB calibration.
    mount = Rotation.from_matrix([[0, 0, 1], [-1, 0, 0], [0, -1, 0]]).as_quat()
    profile = Calibration(3840, 2160, 1600., 1600., 1920., 1080., tuple(mount),
                          (.27, 0., 0.), (0., 1., 0.), (.27, 0., 0.), -1., 0.,
                          profile_name='UNVALIDATED diagnostic assumptions; not a measured Elios RGB calibration')
    profile_path = folder / 'UNVALIDATED_camera_candidate.json'
    profile_path.write_text(json.dumps(profile.to_dict(), indent=2), encoding='utf-8')
    observation = next(iter_observations(source, telemetry, start_s=30, max_frames=1))
    cv2.imwrite(str(folder / 'frame_30s.jpg'), cv2.cvtColor(observation.rgb, cv2.COLOR_RGB2BGR))
    views = [cv2.resize(cv2.cvtColor(observation.rgb, cv2.COLOR_RGB2BGR), (960, 540))]
    for focal in (1250., 1600., 2000.):
        candidate = replace(profile, fx=focal, fy=focal)
        center, rotation = candidate.camera_pose(observation.position_world_m,
                                                 observation.orientation_xyzw, observation.camera_pitch_degrees)
        points = (xyz - center) @ rotation
        ids = np.flatnonzero(points[:, 2] > .2)
        uv = candidate.project_camera_points(points[ids]) / 4
        inside = (uv[:, 0] >= 0) & (uv[:, 0] < 960) & (uv[:, 1] >= 0) & (uv[:, 1] < 540)
        ids, uv = ids[inside], uv[inside].astype(int)
        # Draw far to near so the closest projected sample wins each pixel.
        order = np.argsort(points[ids, 2])[::-1]
        depth = np.full((540, 960), np.inf, np.float32)
        np.minimum.at(depth, (uv[:, 1], uv[:, 0]), points[ids, 2])
        depth = cv2.erode(depth, np.ones((3, 3), np.uint8))
        gray = np.where(np.isfinite(depth), np.clip(depth * 18, 1, 255), 0).astype(np.uint8)
        view = cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
        view[~np.isfinite(depth)] = 0
        cv2.putText(view, f'UNVALIDATED depth projection - focal {focal:.0f}px', (18, 30), cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 2)
        views.append(view)
    contact = np.vstack([np.hstack(views[:2]), np.hstack(views[2:])])
    cv2.imwrite(str(folder / 'alignment_candidates.jpg'), contact)
    exported = np.loadtxt(source.trajectory_path, skiprows=1)
    checks = {
        'pose_count': len(telemetry.pose_times_s), 'pitch_count': len(telemetry.pitch_times_s),
        'frame_sync_count': len(telemetry.frame_times_s), 'subset_point_count': len(sample.points),
        'native_export_pose_max_delta_m': float(np.max(np.abs(exported[:, 1:4] - telemetry.positions_m))),
        'native_export_timestamp_max_delta_s': float(np.max(np.abs(exported[:, 0] - telemetry.pose_times_s))),
        'warnings': telemetry.warnings,
    }
    (folder / 'flight_checks.json').write_text(json.dumps(checks, indent=2))
    print(json.dumps(checks, indent=2), flush=True)
    output = folder / 'flight1_EXPERIMENTAL_colorized.las'
    if not output.exists():
        result = run_colorization('Flight_Data', str(output), las_override=str(subset),
            calibration_override=str(profile_path), start_s=15., end_s=175., sample_interval_s=10.,
            experimental=True, progress=lambda *v: print(*v, flush=True))
        print(json.dumps(result, indent=2), flush=True)
    colored = laspy.read(output)
    for dimension in sample.point_format.dimension_names:
        np.testing.assert_array_equal(sample[dimension], colored[dimension])
    np.testing.assert_array_equal(sample.header.scales, colored.header.scales)
    np.testing.assert_array_equal(sample.header.offsets, colored.header.offsets)
    print('PASS: every original LAS dimension, point order, scale and offset preserved.', flush=True)


if __name__ == '__main__':
    main()
