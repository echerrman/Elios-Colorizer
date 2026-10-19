"""Non-destructive overlay calibration helpers used by the desktop workbench."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from scipy.spatial import Delaunay, QhullError
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .camera import Calibration, CalibrationError
from .colorize import FrameObservation


@dataclass(frozen=True)
class CalibrationAdjustments:
    """Small, bounded corrections relative to an existing camera profile."""

    focal_scale_x: float = 1.0
    focal_scale_y: float = 1.0
    principal_x_px: float = 0.0
    principal_y_px: float = 0.0
    distortion_k1: float = 0.0
    distortion_k2: float = 0.0
    distortion_p1: float = 0.0
    distortion_p2: float = 0.0
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
        if not 0.8 <= self.focal_scale_x <= 1.2:
            raise CalibrationError("Focal scale X must be between 0.8 and 1.2")
        if not 0.8 <= self.focal_scale_y <= 1.2:
            raise CalibrationError("Focal scale Y must be between 0.8 and 1.2")
        if max(abs(self.principal_x_px), abs(self.principal_y_px)) > 500:
            raise CalibrationError("Principal-point corrections must be within ±500 pixels")
        if max(abs(self.distortion_k1), abs(self.distortion_k2)) > 2:
            raise CalibrationError("Radial-distortion corrections must be within ±2.0")
        if max(abs(self.distortion_p1), abs(self.distortion_p2)) > .25:
            raise CalibrationError("Tangential-distortion corrections must be within ±0.25")
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
        if base.refinement:
            refinement["starting_refinement"] = base.refinement
        if alignment_score is not None and np.isfinite(alignment_score):
            refinement["alignment_score"] = float(alignment_score)
        distortion_adjustments = np.array([
            self.distortion_k1, self.distortion_k2,
            self.distortion_p1, self.distortion_p2,
        ], dtype=np.float64)
        distortion_model = base.distortion_model
        distortion_coefficients = tuple(base.distortion_coefficients)
        if np.any(np.abs(distortion_adjustments) > 1e-12):
            if distortion_model == "fisheye":
                raise CalibrationError(
                    "K1/K2/P1/P2 workbench corrections use the OpenCV lens model and "
                    "cannot be added to a fisheye base profile")
            if distortion_model == "pinhole":
                distortion_model = "opencv"
                coefficients = np.zeros(4, dtype=np.float64)
            else:
                coefficients = np.asarray(distortion_coefficients, dtype=np.float64).copy()
            coefficients[:4] += distortion_adjustments
            distortion_coefficients = tuple(float(value) for value in coefficients)
        return replace(
            base,
            fx=base.fx * self.focal_scale_x,
            fy=base.fy * self.focal_scale_y,
            cx=base.cx + self.principal_x_px,
            cy=base.cy + self.principal_y_px,
            distortion_model=distortion_model,
            distortion_coefficients=distortion_coefficients,
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


@dataclass(frozen=True)
class CalibrationCorrespondence:
    """One user-confirmed 2D video pixel to 3D cloud-point match."""

    reference_index: int
    image_xy: tuple[float, float]
    world_xyz: tuple[float, float, float]
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.reference_index < 0:
            raise CalibrationError("Correspondence reference index cannot be negative")
        if not np.isfinite((*self.image_xy, *self.world_xyz)).all():
            raise CalibrationError("Correspondence coordinates must be finite")


@dataclass(frozen=True)
class CalibrationFitResult:
    """A proposed calibration and measurable reprojection diagnostics."""

    calibration: Calibration
    model: str
    rms_error_px: float
    median_error_px: float
    initial_rms_error_px: float
    per_match_error_px: tuple[float, ...]
    information_score: float


def correspondence_errors(
        session: "CalibrationSession", correspondences: list[CalibrationCorrespondence],
        calibration: Calibration) -> np.ndarray:
    """Return one Euclidean pixel error for each enabled correspondence."""
    values: list[float] = []
    frames: dict[int, FrameObservation] = {}
    for match in correspondences:
        if not match.enabled:
            continue
        if match.reference_index >= len(session.references):
            raise CalibrationError("A correspondence refers to a missing reference view")
        frame = frames.setdefault(
            match.reference_index, session.frame_for(match.reference_index, calibration))
        center, camera_to_world = calibration.camera_pose(
            frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
        camera = (np.asarray(match.world_xyz, dtype=np.float64) - center) @ camera_to_world
        if camera[2] <= .01:
            values.append(10_000.0)
            continue
        projected = calibration.project_camera_points(camera.reshape(1, 3))[0]
        values.append(float(np.linalg.norm(projected - np.asarray(match.image_xy))))
    return np.asarray(values, dtype=np.float64)


def _projection_shape_metrics(calibration: Calibration) -> tuple[np.ndarray, np.ndarray]:
    """Return radial mapping scale and derivative across the buffered frame."""
    if calibration.distortion_model == "pinhole":
        return np.ones(32), np.ones(32)
    extent_x = max(calibration.cx, calibration.image_width-calibration.cx) / calibration.fx
    extent_y = max(calibration.cy, calibration.image_height-calibration.cy) / calibration.fy
    radius_max = max(.01, float(np.hypot(extent_x, extent_y))*1.35)
    coefficients = np.zeros(4, dtype=np.float64)
    supplied = np.asarray(calibration.distortion_coefficients, dtype=np.float64)
    coefficients[:min(4, len(supplied))] = supplied[:4]
    if calibration.distortion_model == "fisheye":
        theta = np.linspace(0., np.arctan(radius_max), 32)
        theta2 = theta*theta
        scale = (1. + coefficients[0]*theta2 + coefficients[1]*theta2**2
                 + coefficients[2]*theta2**3 + coefficients[3]*theta2**4)
        derivative = (1. + 3.*coefficients[0]*theta2 + 5.*coefficients[1]*theta2**2
                      + 7.*coefficients[2]*theta2**3 + 9.*coefficients[3]*theta2**4)
        return scale, derivative
    radius = np.linspace(0., radius_max, 32)
    radius2 = radius*radius
    scale = 1. + coefficients[0]*radius2 + coefficients[1]*radius2**2
    derivative = 1. + 3.*coefficients[0]*radius2 + 5.*coefficients[1]*radius2**2
    return scale, derivative


def validate_projection_shape(calibration: Calibration) -> None:
    """Reject distortion functions that fold or invert camera viewing rays."""
    scale, derivative = _projection_shape_metrics(calibration)
    if (not np.isfinite(scale).all() or not np.isfinite(derivative).all()
            or float(np.min(scale)) <= .08 or float(np.min(derivative)) <= .08):
        raise CalibrationError(
            f"The calculated {calibration.distortion_model} projection folds or reverses viewing rays. "
            "Add more well-distributed matches or use a simpler projection model.")
    if float(np.max(scale)) > 3.0 or float(np.max(derivative)) > 5.0:
        raise CalibrationError(
            f"The calculated {calibration.distortion_model} projection expands the frame unrealistically. "
            "Add matches near multiple edges or use a simpler projection model.")


def _fit_candidate(
        session: "CalibrationSession", matches: list[CalibrationCorrespondence],
        initial: Calibration, model: str) -> CalibrationFitResult:
    """Fit one supported projection family with bounded robust least squares."""
    if model not in {"pinhole", "opencv", "fisheye"}:
        raise CalibrationError(f"Unsupported fit model: {model}")
    root_fallback = initial.fallback().to_dict()
    base_rotation = Rotation.from_quat(initial.rotation_xyzw)
    base_translation = np.asarray(initial.translation_m, dtype=np.float64)
    coefficient_count = 0 if model == "pinhole" else 4
    if initial.distortion_model == model:
        start_coefficients = np.zeros(coefficient_count, dtype=np.float64)
        supplied = np.asarray(initial.distortion_coefficients, dtype=np.float64)
        start_coefficients[:min(coefficient_count, len(supplied))] = supplied[:coefficient_count]
    else:
        start_coefficients = np.zeros(coefficient_count, dtype=np.float64)

    # log focal scales, principal offsets, optical roll/pitch/yaw corrections,
    # camera translation, then model-specific coefficients.
    principal_x_low = max(-initial.image_width*.20, -initial.cx+1.)
    principal_x_high = min(initial.image_width*.20, initial.image_width-initial.cx-1.)
    principal_y_low = max(-initial.image_height*.20, -initial.cy+1.)
    principal_y_high = min(initial.image_height*.20, initial.image_height-initial.cy-1.)
    enabled = [item for item in matches if item.enabled]
    depths: list[float] = []
    for match in enabled:
        frame = session.frame_for(match.reference_index, initial)
        center, camera_to_world = initial.camera_pose(
            frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
        depths.append(float(((np.asarray(match.world_xyz)-center) @ camera_to_world)[2]))
    positive_depths = np.asarray([value for value in depths if value > .05])
    relative_depth_span = (float(np.ptp(positive_depths)/np.median(positive_depths))
                           if len(positive_depths) > 1 else 0.)
    fit_translation = len(enabled) >= 10 and relative_depth_span >= .20
    translation_limit = .30 if fit_translation else 1e-7
    observed_tilts = np.asarray([
        session.frame_for(index, initial).camera_pitch_degrees
        for index in sorted({item.reference_index for item in enabled})], dtype=float)
    fit_tilt_zero = (len(enabled) >= 12 and len(observed_tilts) >= 2
                     and float(np.ptp(observed_tilts)) >= 8.)
    tilt_limit = 10. if fit_tilt_zero else 1e-7
    lower = np.r_[np.log(.70), np.log(.70),
                  principal_x_low, principal_y_low,
                  -15., -15., -15.,
                  -translation_limit, -translation_limit, -translation_limit,
                  -tilt_limit]
    upper = np.r_[np.log(1.40), np.log(1.40),
                  principal_x_high, principal_y_high,
                  15., 15., 15.,
                  translation_limit, translation_limit, translation_limit,
                  tilt_limit]
    x0 = np.r_[np.zeros(11, dtype=np.float64), start_coefficients]
    if model == "opencv":
        lower = np.r_[lower, -.8, -.5, -.05, -.05]
        upper = np.r_[upper, .8, .5, .05, .05]
    elif model == "fisheye":
        lower = np.r_[lower, -.8, -.5, -.25, -.10]
        upper = np.r_[upper, .8, .5, .25, .10]
    x0 = np.clip(x0, lower+1e-9, upper-1e-9)

    def candidate(values: np.ndarray) -> Calibration:
        local = Rotation.from_euler("zxy", values[4:7], degrees=True)
        coefficients = tuple(float(value) for value in values[11:])
        return replace(
            initial,
            fx=float(initial.fx*np.exp(values[0])),
            fy=float(initial.fy*np.exp(values[1])),
            cx=float(initial.cx+values[2]), cy=float(initial.cy+values[3]),
            rotation_xyzw=tuple((base_rotation*local).as_quat()),
            translation_m=tuple(base_translation+values[7:10]),
            tilt_zero_degrees=float(initial.tilt_zero_degrees+values[10]),
            distortion_model=model, distortion_coefficients=coefficients,
            schema_version=2, fallback_calibration=root_fallback,
            refinement={"method": "point_correspondence_fit", "projection_model": model})

    enabled = [item for item in matches if item.enabled]
    observed = np.asarray([item.image_xy for item in enabled], dtype=np.float64)

    def pixel_residuals(values: np.ndarray) -> np.ndarray:
        calibration = candidate(values)
        result = np.empty_like(observed)
        frames: dict[int, FrameObservation] = {}
        for index, match in enumerate(enabled):
            frame = frames.setdefault(
                match.reference_index,
                session.frame_for(match.reference_index, calibration))
            center, camera_to_world = calibration.camera_pose(
                frame.position_world_m, frame.orientation_xyzw,
                frame.camera_pitch_degrees)
            camera = (np.asarray(match.world_xyz) - center) @ camera_to_world
            if camera[2] <= .01:
                result[index] = (5_000., 5_000.)
            else:
                projected = calibration.project_camera_points(camera.reshape(1, 3))[0]
                result[index] = np.nan_to_num(
                    projected-observed[index], nan=5_000., posinf=5_000., neginf=-5_000.)
        priors = np.r_[
            values[0:2]/.10,
            values[2]/(initial.image_width*.10),
            values[3]/(initial.image_height*.10),
            values[4:7]/10., values[7:10]/.18,
            values[10]/6., (values[11:]-start_coefficients)/.18,
        ]
        projection = candidate(values)
        scale, derivative = _projection_shape_metrics(projection)
        shape_penalty = np.r_[np.maximum(0., .10-scale),
                              np.maximum(0., .10-derivative)]*100.
        # A weak baseline prior prevents poorly distributed matches from
        # producing an extreme but numerically low-error camera.
        weights = np.r_[np.full(4, 3.0), np.full(7, 1.5),
                        np.full(len(values)-11, 3.0)]
        return np.r_[result.ravel(), priors*weights, shape_penalty]

    # First explain displacement through the physical camera mount/location
    # while optics remain unchanged. Then permit small intrinsic changes, and
    # only in the final stage expose model-specific distortion coefficients.
    common = 11
    pose_indices = np.arange(4, common)

    def subset_residual(values: np.ndarray, seed: np.ndarray,
                        indices: np.ndarray) -> np.ndarray:
        full = seed.copy(); full[indices] = values
        return pixel_residuals(full)

    pose = least_squares(
        lambda values: subset_residual(values, x0, pose_indices),
        x0[pose_indices], bounds=(lower[pose_indices], upper[pose_indices]),
        loss="soft_l1", f_scale=4.0, x_scale="jac", max_nfev=350)
    pose_seed = x0.copy(); pose_seed[pose_indices] = pose.x
    intrinsic = least_squares(
        lambda values: pixel_residuals(np.r_[values, pose_seed[common:]]),
        pose_seed[:common], bounds=(lower[:common], upper[:common]),
        loss="soft_l1", f_scale=4.0, x_scale="jac", max_nfev=450)
    seeded = np.r_[intrinsic.x, x0[common:]]
    solved = least_squares(
        pixel_residuals, seeded, bounds=(lower, upper), loss="soft_l1",
        f_scale=3.0, x_scale="jac", max_nfev=700)
    fitted = candidate(solved.x)
    validate_projection_shape(fitted)
    errors = correspondence_errors(session, enabled, fitted)
    initial_errors = correspondence_errors(session, enabled, initial)
    rms = float(np.sqrt(np.mean(errors**2)))
    initial_rms = float(np.sqrt(np.mean(initial_errors**2)))
    # Bayesian-information-style score discourages selecting a needlessly
    # flexible distortion family solely because its training error is lower.
    residual_count = max(1, errors.size*2)
    sse = max(float(np.sum(errors**2)), 1e-9)
    effective_parameters = (7 + (3 if fit_translation else 0)
                            + (1 if fit_tilt_zero else 0) + coefficient_count)
    information = (residual_count*np.log(sse/residual_count)
                   + effective_parameters*np.log(residual_count))
    fitted = replace(fitted, refinement={
        "method": "point_correspondence_fit", "projection_model": model,
        "match_count": len(enabled), "rms_error_px": rms,
        "median_error_px": float(np.median(errors)),
        "fit_translation": fit_translation,
        "fit_tilt_zero": fit_tilt_zero,
    })
    return CalibrationFitResult(
        fitted, model, rms, float(np.median(errors)), initial_rms,
        tuple(float(value) for value in errors), float(information))


def fit_calibration_correspondences(
        session: "CalibrationSession", matches: list[CalibrationCorrespondence],
        initial: Calibration, *, model: str = "automatic") -> CalibrationFitResult:
    """Fit a proposed camera profile from user-confirmed 2D/3D matches."""
    enabled = [item for item in matches if item.enabled]
    if len(enabled) < 8:
        raise CalibrationError("Add at least 8 enabled point matches before fitting")
    view_count = len({item.reference_index for item in enabled})
    if view_count < 2 and len(session.references) > 1:
        raise CalibrationError("Add matches in at least two reference views before fitting")
    pixels = np.asarray([item.image_xy for item in enabled], dtype=np.float64)
    normalized = np.column_stack((pixels[:, 0]/initial.image_width,
                                  pixels[:, 1]/initial.image_height))
    span_x, span_y = np.ptp(normalized, axis=0)
    centered_radius = np.linalg.norm(normalized-.5, axis=1)
    outer_matches = int(np.count_nonzero(centered_radius >= .32))
    distortion_ready = (len(enabled) >= 18 and span_x >= .55 and span_y >= .45
                        and outer_matches >= 6)
    fisheye_ready = distortion_ready and len(enabled) >= 24
    if model == "opencv" and not distortion_ready:
        raise CalibrationError(
            "Radial/tangential fitting needs at least 18 matches, including at least 6 near the image edges, "
            "with broad horizontal and vertical coverage. Add matches or fit Pinhole first.")
    if model == "fisheye" and not fisheye_ready:
        raise CalibrationError(
            "Fisheye fitting needs at least 24 matches with broad center, edge, and corner coverage. "
            "Add more matches or fit Pinhole first.")
    if model == "automatic":
        requested = ["pinhole"]
        if distortion_ready:
            requested.append("opencv")
        if fisheye_ready:
            requested.append("fisheye")
    else:
        requested = [model]
    results = []
    failures = []
    for value in requested:
        try:
            results.append(_fit_candidate(session, enabled, initial, value))
        except CalibrationError as exc:
            failures.append(str(exc))
    if not results:
        raise CalibrationError(failures[0] if failures else "No valid camera model could be fitted")
    return min(results, key=lambda item: item.information_score)


def pick_cloud_point(
        xyz: np.ndarray, frame: FrameObservation, calibration: Calibration,
        image_xy: tuple[float, float], *, radius_px: float = 14.0) -> np.ndarray | None:
    """Pick the nearest visible cloud point around an original-frame pixel."""
    target = np.asarray(image_xy, dtype=np.float64)
    center, camera_to_world = calibration.camera_pose(
        frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
    candidates: list[tuple[float, float, np.ndarray]] = []
    points = np.asarray(xyz, dtype=np.float64)
    for start in range(0, len(points), 250_000):
        chunk = points[start:start+250_000]
        camera = (chunk-center) @ camera_to_world
        valid = np.isfinite(camera).all(axis=1) & (camera[:, 2] > .05) & (camera[:, 2] < 60.)
        if not np.any(valid):
            continue
        source = chunk[valid]
        camera = camera[valid]
        uv = calibration.project_camera_points(camera)
        distance = np.linalg.norm(uv-target, axis=1)
        nearby = np.flatnonzero(np.isfinite(distance) & (distance <= radius_px))
        if not len(nearby):
            continue
        local_distance = distance[nearby]
        keep_count = min(64, len(nearby))
        keep = nearby[np.argpartition(local_distance, keep_count-1)[:keep_count]]
        candidates.extend((float(distance[index]), float(camera[index, 2]),
                           source[index].copy()) for index in keep)
    if not candidates:
        return None
    minimum = min(item[0] for item in candidates)
    # Prefer proximity to the cursor. Points within one original-frame pixel
    # of the best candidate are resolved toward the front visible surface.
    finalists = [item for item in candidates if item[0] <= minimum+1.0]
    return min(finalists, key=lambda item: item[1])[2]


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
             source_to_geometry: np.ndarray | None = None,
             progress: Callable[[str], None] | None = None) -> "CalibrationSession":
        from .flight import discover_source, inspect_video_coverage, load_telemetry
        from .service import cache_directory

        if progress:
            progress("Loading synchronized calibration frames…")
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
        view_points = load_calibration_view_points(
            geometry, [(frame, base) for frame in focus_frames],
            progress=(lambda done, total: progress(
                f"Loading full-detail calibration regions… {done / max(1, total):.0%}")
                if progress else None))
        xyz = view_points[0]
        cloud_label = (f"Merged cloud · {Path(geometry).name}" if geometry_path
                       else f"Native flight cloud · {Path(geometry).name}")
        return cls(base_path, base, xyz, telemetry, references, elapsed_values,
                   view_points, [telemetry] * len(references),
                   [name] * len(references), [transform] * len(references),
                   [cloud_label] * len(references))

    @classmethod
    def load_manual(cls, requests: list[CalibrationReferenceRequest], *,
                    calibration_path: str | Path,
                    progress: Callable[[str], None] | None = None) -> "CalibrationSession":
        """Load selected moments, including references from different flights."""
        from .flight import discover_source, inspect_video_coverage, load_telemetry
        from .service import cache_directory

        if not requests:
            raise CalibrationError("Choose at least one manual reference frame")
        if progress:
            progress("Loading synchronized calibration frames…")
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
        xyz_values: list[np.ndarray | None] = [None] * len(references)
        for geometry in dict.fromkeys(geometry_paths):
            indices = [index for index, value in enumerate(geometry_paths) if value == geometry]
            focus_frames = [_aligned_frame(
                references[index], telemetry_values[index], base, transforms[index])
                for index in indices]
            loaded = load_calibration_view_points(
                geometry, [(frame, base) for frame in focus_frames],
                progress=(lambda done, total, name=geometry.name: progress(
                    f"Loading {name} calibration regions… {done / max(1, total):.0%}")
                    if progress else None))
            for local_index, reference_index in enumerate(indices):
                xyz_values[reference_index] = loaded[local_index]
        resolved_xyz = [value for value in xyz_values if value is not None]
        if len(resolved_xyz) != len(references):
            raise CalibrationError("Could not load calibration geometry for every reference view")
        return cls(base_path, base, resolved_xyz[0], telemetry_values[0], references,
                   elapsed_values, resolved_xyz, telemetry_values, names,
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


def _view_focus_mask(xyz: np.ndarray, frame: FrameObservation,
                     calibration: Calibration,
                     buffer_fraction: float = .35) -> np.ndarray:
    """Return points inside one buffered calibration camera frustum."""
    center, camera_to_world = calibration.camera_pose(
        frame.position_world_m, frame.orientation_xyzw, frame.camera_pitch_degrees)
    camera = (np.asarray(xyz, dtype=np.float64) - center) @ camera_to_world
    depth = camera[:, 2]
    valid = np.isfinite(camera).all(axis=1) & (depth > .05) & (depth < 60.0)
    if not np.any(valid):
        return valid
    x_over_z = camera[:, 0] / np.maximum(depth, 1e-6)
    y_over_z = camera[:, 1] / np.maximum(depth, 1e-6)
    margin_x = calibration.image_width * buffer_fraction
    margin_y = calibration.image_height * buffer_fraction
    x_min = (-margin_x - calibration.cx) / calibration.fx
    x_max = (calibration.image_width + margin_x - calibration.cx) / calibration.fx
    y_min = (-margin_y - calibration.cy) / calibration.fy
    y_max = (calibration.image_height + margin_y - calibration.cy) / calibration.fy
    return (valid & (x_over_z >= x_min) & (x_over_z <= x_max)
            & (y_over_z >= y_min) & (y_over_z <= y_max))


def _focus_mask(xyz: np.ndarray,
                focus_views: list[tuple[FrameObservation, Calibration]],
                buffer_fraction: float = .35) -> np.ndarray:
    """Select the buffered union of recorded camera frustums."""
    selected = np.zeros(len(xyz), dtype=bool)
    for frame, calibration in focus_views:
        selected |= _view_focus_mask(xyz, frame, calibration, buffer_fraction)
    return selected


def load_calibration_view_points(
        path: str | Path,
        focus_views: list[tuple[FrameObservation, Calibration]], *,
        buffer_fraction: float = .35,
        progress: Callable[[int, int], None] | None = None) -> list[np.ndarray]:
    """Stream a LAS once and retain every point in each buffered reference view.

    The workbench intentionally has no global/context sample.  Each returned
    array belongs to one reference frame, so all available geometric detail is
    preserved where the user is actually calibrating.
    """
    import laspy

    if not focus_views:
        raise CalibrationError("At least one calibration reference view is required")
    source = Path(path).expanduser().resolve()
    retained: list[list[np.ndarray]] = [[] for _ in focus_views]
    with laspy.open(source) as reader:
        if int(reader.header.point_count) < 1:
            raise CalibrationError("The calibration point cloud is empty")
        processed = 0
        total = int(reader.header.point_count)
        for points in reader.chunk_iterator(1_000_000):
            xyz = np.column_stack((points.x, points.y, points.z))
            for index, (frame, calibration) in enumerate(focus_views):
                mask = _view_focus_mask(xyz, frame, calibration, buffer_fraction)
                if np.any(mask):
                    retained[index].append(xyz[mask])
            processed += len(points)
            if progress:
                progress(processed, total)
    result = [
        np.ascontiguousarray(np.concatenate(chunks, axis=0), dtype=np.float64)
        if chunks else np.empty((0, 3), dtype=np.float64)
        for chunks in retained
    ]
    empty = [str(index + 1) for index, values in enumerate(result) if not len(values)]
    if empty:
        raise CalibrationError(
            "No point-cloud geometry projects near reference view(s) " + ", ".join(empty)
            + ". Check the selected cloud and flight-to-merged-cloud alignment.")
    return result


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
            focus_budget = maximum_points
            focus_xyz = np.empty((0, 3), dtype=np.float64)
            focus_hash = np.empty(0, dtype=np.uint64)
            offset = 0
            for points in reader.chunk_iterator(1_000_000):
                xyz = np.column_stack((points.x, points.y, points.z))
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
            if not len(focus_xyz):
                raise CalibrationError("No point-cloud geometry falls inside the calibration views")
            sampled = focus_xyz
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
                           zoom: float = 1.0,
                           pan_px: tuple[float, float] = (0, 0)) -> CloudViewResult:
    """Render a dense, depth-tested, EDL-shaded point-cloud/video comparison."""
    points = np.asarray(xyz, dtype=np.float64)
    target_points = int(np.clip(target_points, 1_000, max(1_000, len(points))))
    if len(points) > target_points:
        stride = int(np.ceil(len(points) / target_points))
        points = points[::stride]
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
