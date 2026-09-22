"""Render independent LAS-depth / RGB comparisons for calibration review."""
import argparse
from pathlib import Path
import cv2
import laspy
import numpy as np
from elios_colorizer.camera import Calibration
from elios_colorizer.flight import discover_source, load_telemetry, iter_observations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('profile')
    parser.add_argument('output')
    args = parser.parse_args()
    source = discover_source('Flight_Data')
    telemetry = load_telemetry(source, '.cache')
    camera = Calibration.load(args.profile, require_validated=False)
    cloud = laspy.read('outputs/diagnostics/flight1_sample_source.las')
    xyz = np.column_stack([cloud.x, cloud.y, cloud.z])
    rows = []
    for elapsed in (30., 120., 240., 390.):
        observation = next(iter_observations(source, telemetry, start_s=elapsed, max_frames=1))
        center, rotation = camera.camera_pose(observation.position_world_m, observation.orientation_xyzw,
                                               observation.camera_pitch_degrees)
        points = (xyz - center) @ rotation
        ids = np.flatnonzero(points[:, 2] > .2)
        uv = camera.project_camera_points(points[ids]) / 4
        visible = (uv[:, 0] >= 0) & (uv[:, 0] < 960) & (uv[:, 1] >= 0) & (uv[:, 1] < 540)
        ids, uv = ids[visible], uv[visible].astype(int)
        depth = np.full((540, 960), np.inf, np.float32)
        np.minimum.at(depth, (uv[:, 1], uv[:, 0]), points[ids, 2])
        depth = cv2.erode(depth, np.ones((3, 3), np.uint8))
        finite = np.isfinite(depth) & (depth < 100)
        gray = np.zeros((540, 960), np.uint8)
        gray[finite] = np.clip(depth[finite] * 18, 1, 255).astype(np.uint8)
        view = cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
        view[~finite] = 0
        rgb = cv2.resize(cv2.cvtColor(observation.rgb, cv2.COLOR_RGB2BGR), (960, 540))
        cv2.putText(rgb, f'RGB {elapsed:.0f}s / reported pitch {observation.camera_pitch_degrees:.1f} deg',
                    (16, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 2)
        cv2.putText(view, 'UNVALIDATED projected LAS depth', (16, 30), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 2)
        rows.append(np.hstack([rgb, view]))
    cv2.imwrite(args.output, np.vstack(rows))


if __name__ == '__main__':
    main()
