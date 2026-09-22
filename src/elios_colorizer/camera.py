"""Explicit, validated RGB camera calibration and projection.

All world positions passed here MUST share the LAS coordinate frame. Body poses
are body-to-world rotations in xyzw order. Optical camera axes are right, down,
forward. Calibration describes the actual RGB pixels passed by the decoder,
including any display rotation (normally the upright Inspector video image).

Version 1 JSON example (NUMBERS BELOW ARE A SYNTHETIC EXAMPLE, NOT ELIOS VALUES)::

    {
      "schema_version": 1,
      "profile_name": "Lab checkerboard and cloud alignment calibration",
      "validated": true,
      "image_width": 3840, "image_height": 2160,
      "intrinsics": {"fx": 2000, "fy": 2000, "cx": 1920, "cy": 1080},
      "distortion": {"model": "opencv", "coefficients": [0,0,0,0,0]},
      "camera_to_body": {
        "rotation_xyzw": [0,0,0,1], "translation_m": [0,0,0],
        "tilt_axis_body": [0,1,0], "tilt_pivot_body_m": [0,0,0],
        "tilt_sign": 1, "tilt_zero_degrees": 0
      }
    }

Rotation and translation describe camera-to-body at the reference pitch. The
pitch rotation is angle = tilt_sign * (reported_pitch - tilt_zero_degrees),
around tilt_axis_body through tilt_pivot_body_m. Calibration values must come
from measurements, supplier data, or a separately validated calibration; this
module intentionally supplies no guessed Elios RGB profile.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.spatial.transform import Rotation


class CalibrationError(ValueError):
    """An absent, unvalidated, or inconsistent camera calibration."""


def _vector(value: Any, length: int, name: str) -> np.ndarray:
    a = np.asarray(value, dtype=np.float64)
    if a.shape != (length,) or not np.isfinite(a).all():
        raise CalibrationError(f"{name} must contain {length} finite numbers")
    return a


def _rotation(value: Any, name: str) -> Rotation:
    q = _vector(value, 4, name)
    norm = np.linalg.norm(q)
    if abs(norm - 1) > 0.02:
        raise CalibrationError(f"{name} must be a unit xyzw quaternion")
    return Rotation.from_quat(q / norm)


@dataclass(frozen=True)
class Calibration:
    image_width: int
    image_height: int
    fx: float
    fy: float
    cx: float
    cy: float
    rotation_xyzw: tuple[float, float, float, float]
    translation_m: tuple[float, float, float]
    tilt_axis_body: tuple[float, float, float]
    tilt_pivot_body_m: tuple[float, float, float]
    tilt_sign: float
    tilt_zero_degrees: float
    distortion_model: str = "pinhole"
    distortion_coefficients: tuple[float, ...] = ()
    profile_name: str = ""
    validated: bool = False

    def __post_init__(self) -> None:
        if (not isinstance(self.image_width, int) or not isinstance(self.image_height, int)
                or self.image_width < 2 or self.image_height < 2):
            raise CalibrationError("Image width and height must be integers of at least 2")
        if not np.isfinite([self.fx, self.fy, self.cx, self.cy, self.tilt_sign,
                            self.tilt_zero_degrees]).all() or min(self.fx, self.fy) <= 0:
            raise CalibrationError("Intrinsics and tilt values must be finite, with positive focal lengths")
        if not (0 <= self.cx < self.image_width and 0 <= self.cy < self.image_height):
            raise CalibrationError("Principal point must lie inside the calibrated image")
        _rotation(self.rotation_xyzw, "camera_to_body.rotation_xyzw")
        _vector(self.translation_m, 3, "camera_to_body.translation_m")
        axis = _vector(self.tilt_axis_body, 3, "camera_to_body.tilt_axis_body")
        if abs(np.linalg.norm(axis) - 1) > 1e-4:
            raise CalibrationError("Tilt axis must be a unit vector in body coordinates")
        _vector(self.tilt_pivot_body_m, 3, "camera_to_body.tilt_pivot_body_m")
        if self.tilt_sign not in (-1, 1):
            raise CalibrationError("tilt_sign must be -1 or 1")
        coefficients = np.asarray(self.distortion_coefficients, dtype=float)
        valid_lengths = {"pinhole": (0,), "opencv": (4, 5, 8, 12, 14), "fisheye": (4,)}
        if (self.distortion_model not in valid_lengths
                or coefficients.ndim != 1
                or len(coefficients) not in valid_lengths[self.distortion_model]
                or not np.isfinite(coefficients).all()):
            raise CalibrationError("Distortion must be pinhole (no coefficients), opencv (4/5/8/12/14), or fisheye (4)")

    @property
    def intrinsic_matrix(self) -> np.ndarray:
        return np.array([[self.fx, 0, self.cx], [0, self.fy, self.cy], [0, 0, 1]], dtype=np.float64)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, require_validated: bool = True) -> "Calibration":
        try:
            if data["schema_version"] != 1:
                raise CalibrationError("Unsupported calibration schema version; expected 1")
            if not isinstance(data["validated"], bool):
                raise CalibrationError("Calibration validated field must be a boolean")
            if require_validated and data["validated"] is not True:
                raise CalibrationError("Calibration JSON must explicitly declare validated: true")
            intrinsic = data["intrinsics"]
            extrinsic = data["camera_to_body"]
            distortion = data["distortion"]
            return cls(
                image_width=data["image_width"], image_height=data["image_height"],
                fx=float(intrinsic["fx"]), fy=float(intrinsic["fy"]),
                cx=float(intrinsic["cx"]), cy=float(intrinsic["cy"]),
                rotation_xyzw=tuple(extrinsic["rotation_xyzw"]),
                translation_m=tuple(extrinsic["translation_m"]),
                tilt_axis_body=tuple(extrinsic["tilt_axis_body"]),
                tilt_pivot_body_m=tuple(extrinsic["tilt_pivot_body_m"]),
                tilt_sign=float(extrinsic["tilt_sign"]),
                tilt_zero_degrees=float(extrinsic["tilt_zero_degrees"]),
                distortion_model=distortion["model"],
                distortion_coefficients=tuple(distortion["coefficients"]),
                profile_name=str(data["profile_name"]), validated=data["validated"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, CalibrationError):
                raise
            raise CalibrationError(f"Invalid calibration JSON: {exc}") from exc

    @classmethod
    def load(cls, path: str | Path, *, require_validated: bool = True) -> "Calibration":
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")),
                                 require_validated=require_validated)
        except (OSError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"Cannot read RGB calibration: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "profile_name": self.profile_name, "validated": self.validated,
            "image_width": self.image_width, "image_height": self.image_height,
            "intrinsics": {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy},
            "distortion": {"model": self.distortion_model, "coefficients": list(self.distortion_coefficients)},
            "camera_to_body": {
                "rotation_xyzw": list(self.rotation_xyzw), "translation_m": list(self.translation_m),
                "tilt_axis_body": list(self.tilt_axis_body), "tilt_pivot_body_m": list(self.tilt_pivot_body_m),
                "tilt_sign": self.tilt_sign, "tilt_zero_degrees": self.tilt_zero_degrees,
            },
        }

    def camera_pose(self, position_world_m: Any, orientation_xyzw: Any,
                    camera_pitch_degrees: float) -> tuple[np.ndarray, np.ndarray]:
        """Return optical-camera center and camera-to-world 3x3 rotation."""
        position = _vector(position_world_m, 3, "Body position")
        body_rotation = _rotation(orientation_xyzw, "Body orientation")
        if not np.isfinite(camera_pitch_degrees):
            raise CalibrationError("Camera pitch must be finite")
        angle = np.deg2rad(self.tilt_sign * (camera_pitch_degrees - self.tilt_zero_degrees))
        tilt_rotation = Rotation.from_rotvec(np.asarray(self.tilt_axis_body) * angle)
        camera_rotation = body_rotation * tilt_rotation * _rotation(self.rotation_xyzw, "Camera mount")
        pivot = np.asarray(self.tilt_pivot_body_m)
        body_center = pivot + tilt_rotation.apply(np.asarray(self.translation_m) - pivot)
        return position + body_rotation.apply(body_center), camera_rotation.as_matrix()

    def project_camera_points(self, camera_points: np.ndarray) -> np.ndarray:
        """Project points in front of the optical camera; return Nx2 float pixels."""
        points = np.ascontiguousarray(camera_points, dtype=np.float64)
        if len(points) == 0:
            return np.empty((0, 2), dtype=np.float64)
        if self.distortion_model == "pinhole":
            uv = points[:, :2] / points[:, 2:3]
            uv *= (self.fx, self.fy)
            uv += (self.cx, self.cy)
            return uv
        coefficients = np.asarray(self.distortion_coefficients, dtype=np.float64)
        if self.distortion_model == "fisheye":
            uv, _ = cv2.fisheye.projectPoints(points.reshape(-1, 1, 3), np.zeros(3), np.zeros(3),
                                             self.intrinsic_matrix, coefficients)
        else:
            uv, _ = cv2.projectPoints(points, np.zeros(3), np.zeros(3), self.intrinsic_matrix, coefficients)
        return uv.reshape(-1, 2)
