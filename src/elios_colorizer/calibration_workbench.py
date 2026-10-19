"""Non-destructive overlay calibration helpers used by the desktop workbench."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.spatial import Delaunay, QhullError
from scipy.spatial.transform import Rotation

from .camera import Calibration, CalibrationError
from .colorize import FrameObservation


@dataclass(frozen=True)
class CalibrationAdjustments:
    """Small, bounded corrections relative to an existing camera profile."""

    focal_scale: float = 1.0
    principal_x_px: float = 0.0
    principal_y_px: float = 0.0
    mount_roll_degrees: float = 0.0
    mount_pitch_degrees: float = 0.0
    mount_yaw_degrees: float = 0.0
    translation_x_m: float = 0.0
    translation_y_m: float = 0.0
    translation_z_m: float = 0.0
    tilt_zero_degrees: float = 0.0
    timing_offset_s: float = 0.0

    def __post_init__(self) -> None:
        values = np.asarray(list(asdict(self).values()), dtype=np.float64)
        if not np.isfinite(values).all():
            raise CalibrationError("Calibration adjustments must be finite")
        if not 0.8 <= self.focal_scale <= 1.2:
            raise CalibrationError("Focal scale must be between 0.8 and 1.2")
        if max(abs(self.principal_x_px), abs(self.principal_y_px)) > 500:
            raise CalibrationError("Principal-point corrections must be within ±500 pixels")
        if max(abs(self.mount_roll_degrees), abs(self.mount_pitch_degrees),
               abs(self.mount_yaw_degrees), abs(self.tilt_zero_degrees)) > 15:
            raise CalibrationError("Angular corrections must be within ±15 degrees")
        if max(abs(self.translation_x_m), abs(self.translation_y_m),
               abs(self.translation_z_m)) > .30:
            raise CalibrationError("Translation corrections must be within ±0.30 m")
        if abs(self.timing_offset_s) > 2:
            raise CalibrationError("Timing correction must be within ±2 seconds")

    def apply(self, base: Calibration, *, profile_name: str | None = None,
              reviewed_frames: int = 0, alignment_score: float | None = None) -> Calibration:
        mount = Rotation.from_quat(base.rotation_xyzw)
        # Camera coordinates are optical coordinates: X points right, Y points
        # down, and Z points forward.  The user-facing aircraft-style labels
        # therefore rotate about Z (roll), X (pitch), and Y (yaw), respectively.
        local = Rotation.from_euler(
            "zxy", [self.mount_roll_degrees, self.mount_pitch_degrees,
                    self.mount_yaw_degrees], degrees=True)
        rotation = tuple((mount * local).as_quat())
        translation = tuple(np.asarray(base.translation_m, dtype=np.float64)
                            + np.array([self.translation_x_m, self.translation_y_m,
                                        self.translation_z_m]))
        fallback = base.fallback().to_dict()
        refinement: dict[str, Any] = {
            "method": "interactive_video_point_cloud_workbench",
            "adjustments": asdict(self),
            "reviewed_reference_frames": int(reviewed_frames),
        }
        if alignment_score is not None and np.isfinite(alignment_score):
            refinement["alignment_score"] = float(alignment_score)
        return replace(
            base,
            fx=base.fx * self.focal_scale,
            fy=base.fy * self.focal_scale,
            cx=base.cx + self.principal_x_px,
            cy=base.cy + self.principal_y_px,
            rotation_xyzw=rotation,
            translation_m=translation,
            tilt_zero_degrees=base.tilt_zero_degrees + self.tilt_zero_degrees,
            video_to_telemetry_offset_s=(base.video_to_telemetry_offset_s
                                         + self.timing_offset_s),
            schema_version=2,
            profile_name=(profile_name or f"{base.profile_name} - Workbench refined"),
            fallback_calibration=fallback,
            refinement=refinement,
        )


@dataclass(frozen=True)
class OverlayResult:
    rgb: np.ndarray
    projected_points: int
    visible_points: int
    alignment_score: float
    guide_points: int = 0
    matched_guide_points: int = 0


@dataclass(frozen=True)
class CloudViewResult:
    rgb: np.ndarray
    sampled_points: int
    projected_points: int
    visible_points: int
    alignment_score: float


@dataclass(frozen=True)
class CalibrationReferenceRequest:
    """One user-selected synchronized reference moment."""

    flight_name: str
    folder: Path
    las_override: Path | None
    elapsed_s: float
    geometry_path: Path | None = None
    source_to_geometry: np.ndarray | None = None


@dataclass
class CalibrationSession:
    """Bounded local data used for interactive multi-frame calibration."""

    base_path: Path
    base_calibration: Calibration
    xyz: np.ndarray
    telemetry: Any
    references: list[FrameObservation]
    reference_elapsed_s: list[float]
    reference_xyz: list[np.ndarray] | None = None
    reference_telemetry: list[Any] | None = None
    reference_flight_names: list[str] | None = None
    reference_transforms: list[np.ndarray] | None = None
    reference_cloud_labels: list[str] | None = None

    @classmethod
    def load(cls, folder: str | Path, *, las_override: str | Path | None = None,
             calibration_path: str | Path, reference_count: int = 3,
             geometry_path: str | Path | None = None,
             source_to_geometry: np.ndarray | None = None) -> "CalibrationSession":
        from .flight import discover_source, inspect_video_coverage, load_telemetry
        from .service import cache_directory

        source = discover_source(str(folder), las_override=(str(las_override) if las_override else None),
                                 calibration_override=str(calibration_path))
        if not source.las_path or not source.videos or not source.starnet_path or not source.trajectory_path:
            raise CalibrationError("A complete flight and its LAS-frame trajectory are required for calibration")
        base_path = Path(calibration_path).expanduser().resolve()
        base = Calibration.load(base_path)
        telemetry = load_telemetry(source, cache_directory())
        coverage = inspect_video_coverage(source, telemetry)
        origin = float(telemetry.frame_times_s[0])
        last = (float(telemetry.frame_times_s[-1]) if coverage.last_available_sync_time_s is None
                else float(coverage.last_available_sync_time_s))
        duration = max(0.0, last - origin)
        if duration <= 0:
            raise CalibrationError("The selected flight has no synchronized RGB duration")
        fractions = np.linspace(.15, .85, max(1, reference_count))
        elapsed_values = [float(duration * value) for value in fractions]
        references = [_observation_near(source, telemetry, coverage, elapsed, duration)[0]
                      for elapsed in elapsed_values]
        geometry = Path(geometry_path).resolve() if geometry_path else source.las_path
        name = Path(folder).name
        transform = (_validated_transform(source_to_geometry)
                     if geometry_path else np.eye(4, dtype=np.float64))
        focus_frames = [_aligned_frame(reference, telemetry, base, transform)
                        for reference in references]
        xyz = sample_las_points(
            geometry, maximum_points=1_000_000,
            focus_views=[(frame, base) for frame in focus_frames])
        cloud_label = (f"Merged cloud · {Path(geometry).name}" if geometry_path
                       else f"Native flight cloud · {Path(geometry).name}")
        return cls(base_path, base, xyz, telemetry, references, elapsed_values,
                   [xyz] * len(references), [telemetry] * len(references),
                   [name] * len(references), [transform] * len(references),
                   [cloud_label] * len(references))

    @classmethod
    def load_manual(cls, requests: list[CalibrationReferenceRequest], *,
                    calibration_path: str | Path) -> "CalibrationSession":
        """Load selected moments, including references from different flights."""
        from .flight import discover_source, inspect_video_coverage, load_telemetry
        from .service import cache_directory

        if not requests:
            raise CalibrationError("Choose at least one manual reference frame")
        base_path = Path(calibration_path).expanduser().resolve()
        base = Calibration.load(base_path)
        contexts: dict[tuple[Path, Path | None], tuple[Any, Any, Any, Path, float]] = {}
        references: list[FrameObservation] = []
        elapsed_values: list[float] = []
        telemetry_values: list[Any] = []
        names: list[str] = []
        transforms: list[np.ndarray] = []
        geometry_paths: list[Path] = []
        cloud_labels: list[str] = []
        for request in requests:
            folder = Path(request.folder).expanduser().resolve()
            las_override = (Path(request.las_override).expanduser().resolve()
                            if request.las_override else None)
            key = (folder, las_override)
            if key not in contexts:
                source = discover_source(
                    str(folder), las_override=(str(las_override) if las_override else None),
                    calibration_override=str(base_path))
                if (not source.las_path or not source.videos or not source.starnet_path
                        or not source.trajectory_path):
                    raise CalibrationError(
                        f"{request.flight_name} is not complete enough for calibration")
                telemetry = load_telemetry(source, cache_directory())
                coverage = inspect_video_coverage(source, telemetry)
                origin = float(telemetry.frame_times_s[0])
                last = (float(telemetry.frame_times_s[-1])
                        if coverage.last_available_sync_time_s is None
                        else float(coverage.last_available_sync_time_s))
                duration = max(0.0, last - origin)
                if duration <= 0:
                    raise CalibrationError(
                        f"{request.flight_name} has no synchronized RGB duration")
                contexts[key] = source, telemetry, coverage, source.las_path, duration
            source, telemetry, coverage, native_geometry, duration = contexts[key]
            geometry = (Path(request.geometry_path).expanduser().resolve()
                        if request.geometry_path else native_geometry)
            if request.elapsed_s > duration + .5:
                raise CalibrationError(
                    f"{request.flight_name} ends at {_format_elapsed(duration)}; "
                    f"the requested time is {_format_elapsed(request.elapsed_s)}")
            frame, actual_elapsed = _observation_near(
                source, telemetry, coverage, request.elapsed_s, duration)
            references.append(frame)
            elapsed_values.append(actual_elapsed)
            telemetry_values.append(telemetry)
            names.append(request.flight_name)
            transforms.append(_validated_transform(request.source_to_geometry)
                              if request.geometry_path else np.eye(4, dtype=np.float64))
            geometry_paths.append(geometry)
            cloud_labels.append(
                f"Merged cloud · {geometry.name}" if request.geometry_path
                else f"Native flight cloud · {geometry.name}")
        geometry_cache: dict[Path, np.ndarray] = {}
        for geometry in dict.fromkeys(geometry_paths):
            indices = [index for index, value in enumerate(geometry_paths) if value == geometry]
            focus_frames = [_aligned_frame(
                references[index], telemetry_values[index], base, transforms[index])
                for index in indices]
            geometry_cache[geometry] = sample_las_points(
                geometry, maximum_points=1_000_000,
                focus_views=[(frame, base) for frame in focus_frames])
        xyz_values = [geometry_cache[path] for path in geometry_paths]
        return cls(base_path, base, xyz_values[0], telemetry_values[0], references,
                   elapsed_values, xyz_values, telemetry_values, names,
                   transforms, cloud_labels)

    def frame_for(self, index: int, calibration: Calibration) -> FrameObservation:
        reference = self.references[index]
        telemetry = (self.reference_telemetry[index]
                     if self.reference_telemetry else self.telemetry)
        transform = (self.reference_transforms[index]
                     if self.reference_transforms else np.eye(4, dtype=np.float64))
        return _aligned_frame(reference, telemetry, calibration, transform)

    def xyz_for(self, index: int) -> np.ndarray:
        return self.reference_xyz[index] if self.reference_xyz else self.xyz

    def label_for(self, index: int) -> str:
        name = (self.reference_flight_names[index]
                if self.reference_flight_names else "Flight")
        return f"{name} · {_format_elapsed(self.reference_elapsed_s[index])}"

    def cloud_label_for(self, index: int) -> str:
        return (self.reference_cloud_labels[index] if self.reference_cloud_labels
                else "Native flight cloud")


def _validated_transform(value: np.ndarray | None) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64) if value is None else np.asarray(value, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise CalibrationError("Calibration cloud transform must be a finite 4 × 4 matrix")
    if not np.allclose(matrix[3], (0, 0, 0, 1), atol=1e-6):
        raise CalibrationError("Calibration cloud transform has an invalid homogeneous row")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-3):
        raise CalibrationError("Calibration cloud transform must be rigid")
    return matrix


def _aligned_frame(reference: FrameObservation, telemetry: Any,
                   calibration: Calibration, transform: np.ndarray) -> FrameObservation:
    pose = telemetry.interpolate(
        reference.timestamp_s + calibration.video_to_telemetry_offset_s)
    if pose is None:
        raise CalibrationError(
            "The timing correction moves this frame outside available pose or tilt telemetry")
    position = np.asarray(pose.position_world_m, dtype=np.float64)
    orientation = np.asarray(pose.orientation_xyzw, dtype=np.float64)
    if not np.allclose(transform, np.eye(4), atol=1e-12):
        position = position @ transform[:3, :3].T + transform[:3, 3]
        orientation = (Rotation.from_matrix(transform[:3, :3])
                       * Rotation.from_quat(orientation)).as_quat()
    return replace(reference, position_world_m=position,
                   orientation_xyzw=orientation,
                   camera_pitch_degrees=pose.camera_pitch_degrees)


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"


def _observation_near(source: Any, telemetry: Any, coverage: Any,
                      elapsed_s: float, duration_s: float) -> tuple[FrameObservation, float]:
    """Return the decoded observation nearest a requested elapsed video time."""
    from .flight import iter_observations

    target = float(np.clip(elapsed_s, 0, duration_s))
    start = max(0.0, target - .35)
    end = min(duration_s, target + .35)
    if end <= start:
        end = min(duration_s, start + .25)
    candidates = list(iter_observations(
        source, telemetry, sample_interval_s=1 / 30, max_frames=32,
        start_s=start, end_s=end, video_coverage=coverage,
        video_to_telemetry_offset_s=0.0))
    if not candidates:
        raise CalibrationError(
            f"No synchronized RGB frame is available near {_format_elapsed(target)}")
    origin = float(telemetry.frame_times_s[0])
    frame = min(candidates, key=lambda item: abs((item.timestamp_s - origin) - target))
    return frame, max(0.0, float(frame.timestamp_s - origin))


def _focus_mask(xyz: np.ndarray,
                focus_views: list[tuple[FrameObservation, Calibration]],
                buffer_fraction: float = .30) -> np.ndarray:
    """Select a generously buffered union of recorded camera frustums."""
    selected = np.zeros(len(xyz), dtype=bool)
    for frame, calibration in focus_views:
        center, camera_to_world = calibration.camera_pose(
            frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
        camera = (xyz - center) @ camera_to_world
        depth = camera[:, 2]
        valid = np.isfinite(camera).all(axis=1) & (depth > .08) & (depth < 50.0)
        if not np.any(valid):
            continue
        x_over_z = camera[:, 0] / np.maximum(depth, 1e-6)
        y_over_z = camera[:, 1] / np.maximum(depth, 1e-6)
        margin_x = calibration.image_width * buffer_fraction
        margin_y = calibration.image_height * buffer_fraction
        x_min = (-margin_x - calibration.cx) / calibration.fx
        x_max = (calibration.image_width + margin_x - calibration.cx) / calibration.fx
        y_min = (-margin_y - calibration.cy) / calibration.fy
        y_max = (calibration.image_height + margin_y - calibration.cy) / calibration.fy
        selected |= (valid & (x_over_z >= x_min) & (x_over_z <= x_max)
                     & (y_over_z >= y_min) & (y_over_z <= y_max))
    return selected


def _stable_hash(indices: np.ndarray) -> np.ndarray:
    values = np.asarray(indices, dtype=np.uint64) + np.uint64(0x9E3779B97F4A7C15)
    values = (values ^ (values >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    values = (values ^ (values >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return values ^ (values >> np.uint64(31))


def sample_las_points(path: str | Path, maximum_points: int = 250_000, *,
                      focus_views: list[tuple[FrameObservation, Calibration]] | None = None) -> np.ndarray:
    """Read a deterministic bounded sample, concentrating detail around reference views."""
    import laspy

    source = Path(path).resolve()
    with laspy.open(source) as reader:
        count = int(reader.header.point_count)
        if count < 1:
            raise CalibrationError("The calibration point cloud is empty")
        maximum_points = max(1, int(maximum_points))
        chunks = []
        if focus_views and count > maximum_points:
            global_budget = max(1, maximum_points // 4)
            focus_budget = max(1, maximum_points - global_budget)
            global_stride = max(1, int(np.ceil(count / global_budget)))
            focus_xyz = np.empty((0, 3), dtype=np.float64)
            focus_hash = np.empty(0, dtype=np.uint64)
            offset = 0
            for points in reader.chunk_iterator(1_000_000):
                xyz = np.column_stack((points.x, points.y, points.z))
                first = (-offset) % global_stride
                chunks.append(xyz[first::global_stride])
                mask = _focus_mask(xyz, focus_views)
                if np.any(mask):
                    local_indices = offset + np.flatnonzero(mask)
                    candidate_xyz = xyz[mask]
                    candidate_hash = _stable_hash(local_indices)
                    focus_xyz = np.concatenate((focus_xyz, candidate_xyz), axis=0)
                    focus_hash = np.concatenate((focus_hash, candidate_hash), axis=0)
                    if len(focus_xyz) > focus_budget * 2:
                        keep = np.argpartition(focus_hash, focus_budget - 1)[:focus_budget]
                        focus_xyz, focus_hash = focus_xyz[keep], focus_hash[keep]
                offset += len(points)
            if len(focus_xyz) > focus_budget:
                keep = np.argpartition(focus_hash, focus_budget - 1)[:focus_budget]
                focus_xyz = focus_xyz[keep]
            global_xyz = np.concatenate(chunks, axis=0)[:global_budget]
            sampled = np.concatenate((focus_xyz, global_xyz), axis=0)
            order = np.random.default_rng(0).permutation(len(sampled))
            sampled = sampled[order]
            chunks = []
        elif count > maximum_points * 3:
            # Reading every point from a compressed 50M-point LAZ only to discard
            # most of it makes calibration startup needlessly expensive. Sample
            # short, evenly distributed windows instead. The operation is
            # deterministic and never writes to the source.
            block_count = min(64, max(8, maximum_points // 10_000))
            points_per_block = int(np.ceil(maximum_points / block_count))
            last_start = max(0, count - points_per_block)
            for start in np.linspace(0, last_start, block_count, dtype=np.int64):
                reader.seek(int(start))
                points = reader.read_points(min(points_per_block, count - int(start)))
                if len(points):
                    chunks.append(np.column_stack((points.x, points.y, points.z)))
        else:
            stride = max(1, int(np.ceil(count / maximum_points)))
            offset = 0
            for points in reader.chunk_iterator(1_000_000):
                xyz = np.column_stack((points.x, points.y, points.z))
                first = (-offset) % stride
                chunks.append(xyz[first::stride])
                offset += len(points)
        if chunks:
            sampled = np.concatenate(chunks, axis=0)
    if len(sampled) > maximum_points:
        sampled = sampled[:maximum_points]
    return np.ascontiguousarray(sampled, dtype=np.float64)


def project_visible_samples(xyz: np.ndarray, frame: FrameObservation,
                            calibration: Calibration, *, depth_width: int = 960,
                            tolerance_m: float = .08) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project a bounded point sample and retain its front visible surface and indices."""
    center, camera_to_world = calibration.camera_pose(
        frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
    camera = (np.asarray(xyz, dtype=np.float64) - center) @ camera_to_world
    valid = np.isfinite(camera).all(axis=1) & (camera[:, 2] > .15) & (camera[:, 2] < 40)
    source_indices = np.flatnonzero(valid)
    camera = camera[valid]
    if not len(camera):
        return np.empty((0, 2)), np.empty(0), np.empty(0, dtype=np.int64)
    uv = calibration.project_camera_points(camera)
    inside = (np.isfinite(uv).all(axis=1)
              & (uv[:, 0] >= 0) & (uv[:, 0] < calibration.image_width)
              & (uv[:, 1] >= 0) & (uv[:, 1] < calibration.image_height))
    uv, depth = uv[inside], camera[inside, 2]
    source_indices = source_indices[inside]
    if not len(uv):
        return uv, depth, source_indices
    scale = min(1.0, depth_width / calibration.image_width)
    width = max(2, round(calibration.image_width * scale))
    height = max(2, round(calibration.image_height * scale))
    x = np.clip((uv[:, 0] * scale).astype(np.int64), 0, width - 1)
    y = np.clip((uv[:, 1] * scale).astype(np.int64), 0, height - 1)
    flat = y * width + x
    nearest = np.full(width * height, np.inf, dtype=np.float32)
    np.minimum.at(nearest, flat, depth.astype(np.float32))
    visible = depth <= nearest[flat] + tolerance_m + depth * .005
    return uv[visible], depth[visible], source_indices[visible]


def project_visible_points(xyz: np.ndarray, frame: FrameObservation,
                           calibration: Calibration, *, depth_width: int = 960,
                           tolerance_m: float = .08) -> tuple[np.ndarray, np.ndarray]:
    """Project a bounded point sample and retain its front visible surface."""
    uv, depth, _ = project_visible_samples(
        xyz, frame, calibration, depth_width=depth_width, tolerance_m=tolerance_m)
    return uv, depth


def render_cloud_composite(frame: FrameObservation, xyz: np.ndarray,
                           calibration: Calibration, *, target_points: int = 300_000,
                           point_radius: int = 3, video_opacity: float = .55,
                           color_mode: str = "elevation", edl_strength: float = 1.2,
                           display_mode: str = "composite", preview_width: int = 1280,
                           zoom: float = 1.0, pan_px: tuple[float, float] = (0, 0),
                           context_angles: tuple[float, float] = (0, 0)) -> CloudViewResult:
    """Render a dense, depth-tested, EDL-shaded point-cloud/video comparison."""
    points = np.asarray(xyz, dtype=np.float64)
    target_points = int(np.clip(target_points, 1_000, max(1_000, len(points))))
    if len(points) > target_points:
        stride = int(np.ceil(len(points) / target_points))
        points = points[::stride]
    if display_mode == "context":
        center, camera_to_world = calibration.camera_pose(
            frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
        camera_points = (points - center) @ camera_to_world
        target = np.array((0.0, 0.0, 4.0), dtype=np.float64)
        pitch, yaw = context_angles
        orbit = Rotation.from_euler("xy", (pitch, yaw), degrees=True).as_matrix()
        camera_points = (camera_points - target) @ orbit + target
        identity = replace(
            calibration, rotation_xyzw=(0, 0, 0, 1), translation_m=(0, 0, 0),
            tilt_axis_body=(0, 1, 0), tilt_pivot_body_m=(0, 0, 0),
            tilt_zero_degrees=0, distortion_model="pinhole", distortion_coefficients=())
        context_frame = replace(
            frame, position_world_m=np.zeros(3), orientation_xyzw=np.array((0, 0, 0, 1)),
            camera_pitch_degrees=0)
        context_result = render_cloud_composite(
            context_frame, camera_points, identity, target_points=len(camera_points),
            point_radius=point_radius, video_opacity=0, color_mode="neutral",
            edl_strength=edl_strength, display_mode="cloud", preview_width=preview_width,
            zoom=zoom, pan_px=pan_px)
        image = context_result.rgb.copy()
        frustum_depth = 1.2
        corners = np.array([
            ((0 - calibration.cx) / calibration.fx * frustum_depth,
             (0 - calibration.cy) / calibration.fy * frustum_depth, frustum_depth),
            ((calibration.image_width - calibration.cx) / calibration.fx * frustum_depth,
             (0 - calibration.cy) / calibration.fy * frustum_depth, frustum_depth),
            ((calibration.image_width - calibration.cx) / calibration.fx * frustum_depth,
             (calibration.image_height - calibration.cy) / calibration.fy * frustum_depth,
             frustum_depth),
            ((0 - calibration.cx) / calibration.fx * frustum_depth,
             (calibration.image_height - calibration.cy) / calibration.fy * frustum_depth,
             frustum_depth),
        ])
        frustum = np.vstack((np.zeros((1, 3)), corners))
        frustum = (frustum - target) @ orbit + target
        valid = frustum[:, 2] > .05
        projected = np.zeros((5, 2), dtype=np.float64)
        projected[valid, 0] = (frustum[valid, 0] / frustum[valid, 2] * calibration.fx
                               + calibration.cx)
        projected[valid, 1] = (frustum[valid, 1] / frustum[valid, 2] * calibration.fy
                               + calibration.cy)
        view_scale = image.shape[1] / calibration.image_width
        projected *= view_scale
        projected[:, 0] = ((projected[:, 0] - image.shape[1] / 2) * zoom
                           + image.shape[1] / 2 + pan_px[0])
        projected[:, 1] = ((projected[:, 1] - image.shape[0] / 2) * zoom
                           + image.shape[0] / 2 + pan_px[1])
        segments = [(0, 1), (0, 2), (0, 3), (0, 4),
                    (1, 2), (2, 3), (3, 4), (4, 1)]
        for first, second in segments:
            if valid[first] and valid[second]:
                cv2.line(image, tuple(np.rint(projected[first]).astype(int)),
                         tuple(np.rint(projected[second]).astype(int)),
                         (75, 220, 245), 2, cv2.LINE_AA)
        return replace(context_result, rgb=image)
    scale = min(1.0, preview_width / calibration.image_width)
    width = max(2, round(calibration.image_width * scale))
    height = max(2, round(calibration.image_height * scale))
    video = cv2.resize(frame.rgb, (width, height), interpolation=cv2.INTER_AREA)
    zoom = float(np.clip(zoom, 1.0, 8.0))
    pan_x, pan_y = (float(pan_px[0]), float(pan_px[1]))
    transform = np.array([[zoom, 0, (1 - zoom) * width / 2 + pan_x],
                          [0, zoom, (1 - zoom) * height / 2 + pan_y]], dtype=np.float32)
    video = cv2.warpAffine(video, transform, (width, height), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(8, 12, 16))
    if display_mode == "video":
        return CloudViewResult(video, len(points), 0, 0, 0.0)

    uv, depth, indices = project_visible_samples(
        points, frame, calibration, depth_width=preview_width)
    if not len(uv):
        return CloudViewResult(video if display_mode == "composite" else np.zeros_like(video),
                               len(points), 0, 0, 0.0)
    projected_count = len(uv)
    score = _edge_alignment_score(frame.rgb, uv, depth, calibration)
    pixels = uv * scale
    pixels[:, 0] = (pixels[:, 0] - width / 2) * zoom + width / 2 + pan_x
    pixels[:, 1] = (pixels[:, 1] - height / 2) * zoom + height / 2 + pan_y
    inside = ((pixels[:, 0] >= 0) & (pixels[:, 0] < width)
              & (pixels[:, 1] >= 0) & (pixels[:, 1] < height))
    pixels, depth, indices = pixels[inside], depth[inside], indices[inside]
    if not len(pixels):
        return CloudViewResult(video if display_mode == "composite" else np.zeros_like(video),
                               len(points), projected_count, 0, score)
    x = pixels[:, 0].astype(np.int64)
    y = pixels[:, 1].astype(np.int64)
    flat = y * width + x
    nearest = np.full(width * height, np.inf, dtype=np.float32)
    np.minimum.at(nearest, flat, depth.astype(np.float32))
    visible = depth <= nearest[flat] + .04 + depth * .002
    x, y, depth, indices = x[visible], y[visible], depth[visible], indices[visible]

    inverse_depth = np.zeros((height, width), dtype=np.float32)
    np.maximum.at(inverse_depth.ravel(), y * width + x,
                  1.0 / depth.astype(np.float32))
    radius = int(np.clip(point_radius, 1, 12))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    splat_inverse = cv2.dilate(inverse_depth, kernel)
    occupied = splat_inverse > 0

    colors = np.zeros((height, width, 3), dtype=np.uint8)
    if color_mode == "neutral":
        colors[occupied] = (190, 205, 215)
    else:
        elevations = points[indices, 2]
        low, high = np.percentile(elevations, (2, 98)) if len(elevations) > 5 else (0, 1)
        span = max(float(high - low), 1e-6)
        normalized = np.clip((elevations - low) / span, 0, 1)
        point_colors = cv2.applyColorMap(
            np.rint(normalized * 255).astype(np.uint8), cv2.COLORMAP_TURBO)[:, 0, ::-1]
        seed = np.zeros_like(colors)
        seed[y, x] = point_colors
        for channel in range(3):
            colors[:, :, channel] = cv2.dilate(seed[:, :, channel], kernel)

    neighborhood = cv2.dilate(splat_inverse, np.ones((5, 5), np.uint8))
    relative_break = np.maximum(0.0, neighborhood - splat_inverse) / np.maximum(
        neighborhood, 1e-6)
    shade = np.exp(-float(np.clip(edl_strength, 0, 4)) * relative_break * 9.0)
    shade *= np.where(occupied, .72 + .28 * np.clip(splat_inverse / max(
        float(np.percentile(splat_inverse[occupied], 95)), 1e-6), 0, 1), 0)
    shaded = np.clip(colors.astype(np.float32) * shade[:, :, None], 0, 255).astype(np.uint8)

    if display_mode == "cloud":
        output = np.full_like(video, (8, 12, 16))
        output[occupied] = shaded[occupied]
    else:
        output = video.copy()
        alpha = float(np.clip(video_opacity, 0, 1))
        output[occupied] = np.clip(
            shaded[occupied].astype(np.float32) * (1 - alpha)
            + video[occupied].astype(np.float32) * alpha, 0, 255).astype(np.uint8)
    return CloudViewResult(output, len(points), projected_count,
                           int(np.count_nonzero(visible)), score)


def _edge_alignment_score(rgb: np.ndarray, uv: np.ndarray, depth: np.ndarray,
                          calibration: Calibration, width: int = 640) -> float:
    if len(uv) < 100:
        return 0.0
    scale = min(1.0, width / calibration.image_width)
    width = max(2, round(calibration.image_width * scale))
    height = max(2, round(calibration.image_height * scale))
    gray = cv2.cvtColor(cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_RGB2GRAY)
    image_edges = cv2.Canny(gray, 55, 140) > 0
    distance = cv2.distanceTransform((~image_edges).astype(np.uint8), cv2.DIST_L2, 3)
    depth_map = np.full((height, width), np.nan, dtype=np.float32)
    x = np.clip((uv[:, 0] * scale).astype(np.int64), 0, width - 1)
    y = np.clip((uv[:, 1] * scale).astype(np.int64), 0, height - 1)
    flat = y * width + x
    nearest = np.full(width * height, np.inf, dtype=np.float32)
    np.minimum.at(nearest, flat, depth.astype(np.float32))
    occupied = np.isfinite(nearest).reshape(height, width)
    depth_map[occupied] = nearest.reshape(height, width)[occupied]
    filled = np.nan_to_num(depth_map, nan=0.0)
    gx = cv2.Sobel(filled, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(filled, cv2.CV_32F, 0, 1, ksize=3)
    neighbors = cv2.dilate(occupied.astype(np.uint8), np.ones((3, 3), np.uint8))
    cloud_edges = occupied & ((np.hypot(gx, gy) > .18) | (neighbors != occupied))
    samples = distance[cloud_edges]
    if not len(samples):
        return 0.0
    return float(np.mean(np.exp(-np.minimum(samples, 20) / 3.0)))


def _structure_edge_indices(uv: np.ndarray, depth: np.ndarray,
                            scale: float) -> np.ndarray:
    """Find projected samples that describe silhouettes or real depth breaks."""
    if len(uv) < 4:
        return np.arange(len(uv), dtype=np.int64)
    source_indices = np.arange(len(uv), dtype=np.int64)
    if len(source_indices) > 10_000:
        source_indices = source_indices[::int(np.ceil(len(source_indices) / 10_000))]
    points = np.asarray(uv[source_indices] * scale, dtype=np.float64)
    sampled_depth = depth[source_indices]
    try:
        triangulation = Delaunay(points)
    except QhullError:
        return source_indices[:min(len(points), 500)]
    simplices = triangulation.simplices
    edges = np.concatenate((simplices[:, [0, 1]], simplices[:, [1, 2]],
                            simplices[:, [2, 0]]), axis=0)
    edges.sort(axis=1)
    edges = np.unique(edges, axis=0)
    lengths = np.linalg.norm(points[edges[:, 0]] - points[edges[:, 1]], axis=1)
    local = lengths <= max(18.0, float(np.percentile(lengths, 80)))
    first, second = edges[:, 0], edges[:, 1]
    threshold = .08 + np.minimum(sampled_depth[first], sampled_depth[second]) * .012
    jumps = local & (np.abs(sampled_depth[first] - sampled_depth[second]) > threshold)
    boundary = np.unique(triangulation.convex_hull.ravel())
    selected = np.unique(np.concatenate((edges[jumps].ravel(), boundary)))
    if len(selected) < min(24, len(points)):
        stride = max(1, len(points) // 120)
        selected = np.unique(np.concatenate((selected, np.arange(0, len(points), stride))))
    return source_indices[selected]


def render_overlay(frame: FrameObservation, xyz: np.ndarray, calibration: Calibration,
                   *, opacity: float = .72, point_radius: int = 2,
                   show_video: bool = True, preview_width: int | None = 1280) -> OverlayResult:
    """Render an actionable geometry-to-video alignment guide."""
    uv, depth = project_visible_points(xyz, frame, calibration)
    scale = (min(1.0, preview_width / calibration.image_width)
             if preview_width is not None else 1.0)
    size = (max(2, round(calibration.image_width * scale)),
            max(2, round(calibration.image_height * scale)))
    video = (cv2.resize(frame.rgb, size, interpolation=cv2.INTER_AREA)
             if scale != 1 else frame.rgb.copy())
    base = video if show_video else np.zeros_like(video)
    overlay = base.copy()
    guide_count = matched_count = 0
    if len(uv):
        gray = cv2.cvtColor(video, cv2.COLOR_RGB2GRAY)
        video_edges = cv2.Canny(gray, 55, 140) > 0
        distance = cv2.distanceTransform((~video_edges).astype(np.uint8), cv2.DIST_L2, 3)
        edge_layer = overlay.copy()
        edge_layer[video_edges] = (220, 70, 210)
        overlay = cv2.addWeighted(edge_layer, .28, overlay, .72, 0)
        indices = _structure_edge_indices(uv, depth, scale)
        if len(indices) > 4_000:
            indices = indices[::max(1, int(np.ceil(len(indices) / 4_000)))]
        pixels = np.rint(uv[indices] * scale).astype(np.int64)
        pixels[:, 0] = np.clip(pixels[:, 0], 0, size[0] - 1)
        pixels[:, 1] = np.clip(pixels[:, 1], 0, size[1] - 1)
        matched = distance[pixels[:, 1], pixels[:, 0]] <= 3.0
        guide_count = len(indices)
        matched_count = int(np.count_nonzero(matched))
        order = np.argsort(depth[indices])[::-1]
        for item in order:
            point = tuple(int(value) for value in pixels[item])
            color = (65, 235, 125) if matched[item] else (255, 170, 45)
            cv2.drawMarker(overlay, point, color, cv2.MARKER_CROSS,
                           max(3, point_radius * 2 + 1), 1, cv2.LINE_AA)
    opacity = float(np.clip(opacity, 0, 1))
    composed = cv2.addWeighted(overlay, opacity, base, 1 - opacity, 0)
    score = _edge_alignment_score(frame.rgb, uv, depth, calibration)
    return OverlayResult(composed, len(uv), len(uv), score,
                         guide_count, matched_count)


def save_refined_calibration(calibration: Calibration, destination: str | Path,
                             *, base_path: str | Path | None = None) -> Path:
    """Save a new refined profile, refusing to overwrite its fallback source."""
    target = Path(destination).expanduser().resolve()
    if target.suffix.lower() != ".json":
        raise CalibrationError("Refined calibration must be saved as a .json file")
    if base_path is not None and target == Path(base_path).expanduser().resolve():
        raise CalibrationError("Save the refined profile separately; the fallback profile is never overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(calibration.to_dict(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
