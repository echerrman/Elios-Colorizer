"""Bounded-memory RGB projection onto original LAS geometry.

The algorithm streams small batches of decoded RGB observations. For each batch
it scans the LAS twice: once to construct visibility depth buffers and once to
select colors. Color and quality arrays live in temporary memory-mapped files,
not RAM; the final LAS is written in chunks and published atomically. Camera and
LAS coordinates must already agree, and a measured RGB calibration is required.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import copy
import ctypes
from functools import lru_cache
import hashlib
import json
from itertools import islice
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Callable, Iterable, Iterator, Any

import cv2
import laspy
import numpy as np

from .camera import Calibration
from .illumination import LINEAR, apply_gain, fit_model, radius_squared, raw_usable


class ColorizationError(RuntimeError):
    pass


class ColorizationCancelled(ColorizationError):
    pass


@dataclass(frozen=True)
class FrameObservation:
    rgb: np.ndarray
    position_world_m: tuple[float, float, float] | np.ndarray
    orientation_xyzw: tuple[float, float, float, float] | np.ndarray
    camera_pitch_degrees: float
    timestamp_s: float
    frame_id: str = ""
    source_flight: int = 1


@dataclass(frozen=True)
class ColorizationOptions:
    chunk_size: int = 250_000
    frame_batch_size: int = 4
    depth_buffer_width: int = 1280
    occlusion_radius_pixels: int = 1
    occlusion_absolute_tolerance_m: float = 0.06
    occlusion_relative_tolerance: float = 0.005
    minimum_depth_m: float = 0.15
    maximum_depth_m: float = 40.0
    image_border_fraction: float = 0.02
    minimum_luminance: float = 3.0
    maximum_luminance: float = 255.0
    minimum_sharpness: float = 2.0
    reject_exposure_extremes: bool = True
    overwrite: bool = False
    experimental_calibration: bool = False
    maximum_color_distance_m: float | None = None
    cache_xyz: bool | None = None
    worker_threads: int = 1
    illumination_balancing: bool = False
    merged_output: bool = False
    multi_frame_fusion: bool = False
    fusion_max_observations: int = 5
    fusion_min_observations: int = 3
    cuda_device_index: int = 0
    cuda_enabled: bool = True
    memory_budget_bytes: int | None = None
    scratch_directory: str | None = None
    state_storage: str = 'auto'

    def __post_init__(self) -> None:
        if self.chunk_size < 1 or self.frame_batch_size < 1 or self.depth_buffer_width < 8 or self.worker_threads < 1:
            raise ValueError("Chunk size, frame batch size, and depth-buffer width must be positive")
        if (self.fusion_max_observations < 3 or self.fusion_max_observations > 8
                or self.fusion_min_observations < 2
                or self.fusion_min_observations > self.fusion_max_observations):
            raise ValueError("Multi-frame fusion observation counts are invalid")
        if self.cuda_device_index < 0:
            raise ValueError("CUDA device index must be nonnegative")
        if self.state_storage not in ('auto', 'ram', 'disk'):
            raise ValueError("State storage must be auto, ram, or disk")
        if (not 0 <= self.occlusion_radius_pixels <= 4
                or not 0 <= self.image_border_fraction < 0.25
                or not 0 < self.minimum_depth_m < self.maximum_depth_m
                or self.occlusion_absolute_tolerance_m < 0
                or self.occlusion_relative_tolerance < 0
                or (self.maximum_color_distance_m is not None
                    and not self.minimum_depth_m < self.maximum_color_distance_m <= self.maximum_depth_m)
                or not 0 <= self.minimum_luminance < self.maximum_luminance <= 255
                or self.minimum_sharpness < 0):
            raise ValueError("Invalid projection or image-quality options")
        if not all(math.isfinite(v) for v in asdict(self).values() if isinstance(v, (float, int))):
            raise ValueError("Projection options must be finite")


@dataclass(frozen=True)
class ColorizationResult:
    output_path: Path
    point_count: int
    colored_point_count: int
    frames_received: int
    frames_used: int
    frames_rejected: int
    experimental_calibration: bool = False
    xyz_cache_used: bool = False
    illumination_balancing: dict | None = None
    multi_frame_fusion: dict | None = None
    acceleration: dict | None = None
    performance: dict | None = None

    @property
    def coverage_fraction(self) -> float:
        return self.colored_point_count / self.point_count if self.point_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["output_path"] = str(self.output_path)
        result["coverage_fraction"] = self.coverage_fraction
        return result


@dataclass
class _PreparedFrame:
    observation: FrameObservation
    center: np.ndarray
    camera_to_world: np.ndarray
    depth: np.ndarray
    scale_x: float
    scale_y: float
    sharpness_weight: float
    illumination_model: Any = None
    illumination_frame: int = 0
    diversity_group: int = 0
    depth_edge_strength: np.ndarray | None = None
    image_edge_strength: np.ndarray | None = None


class _PoseDiversityGroups:
    """Assign stable groups to genuinely different calibrated camera poses."""

    def __init__(self, translation_m: float = .15, rotation_degrees: float = 3.0):
        self.translation_m = translation_m
        self.rotation_degrees = rotation_degrees
        self._bins: dict[tuple[int, int, int, int], list[tuple[np.ndarray, np.ndarray, int]]] = {}
        self._next_group_by_source: dict[int, int] = {}

    @staticmethod
    def _rotation_degrees(first: np.ndarray, second: np.ndarray) -> float:
        cosine = float(np.clip((np.trace(first.T @ second) - 1) * .5, -1, 1))
        return math.degrees(math.acos(cosine))

    def assign(self, frame: _PreparedFrame) -> int:
        center = np.asarray(frame.center, dtype=np.float64)
        cell = np.floor(center / self.translation_m).astype(np.int64)
        source = int(frame.observation.source_flight)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    key = (source, int(cell[0] + dx), int(cell[1] + dy), int(cell[2] + dz))
                    for anchor_center, anchor_rotation, group in self._bins.get(key, ()):
                        if (np.linalg.norm(center - anchor_center) < self.translation_m
                                and self._rotation_degrees(frame.camera_to_world, anchor_rotation)
                                < self.rotation_degrees):
                            return group
        local_group = self._next_group_by_source.get(source, 1)
        if local_group >= (1 << 20) or source >= (1 << 12):
            raise ColorizationError('Pose diversity group identifier capacity exceeded')
        group = (source << 20) | local_group
        self._next_group_by_source[source] = local_group + 1
        key = (source, int(cell[0]), int(cell[1]), int(cell[2]))
        self._bins.setdefault(key, []).append((center.copy(), frame.camera_to_world.copy(), group))
        return group


ProgressCallback = Callable[[dict[str, Any]], None]


def _observation_similarity(first: FrameObservation, second: FrameObservation) -> tuple[float, float, float]:
    translation = float(np.linalg.norm(np.asarray(first.position_world_m, dtype=np.float64)
                                       - np.asarray(second.position_world_m, dtype=np.float64)))
    q1 = np.asarray(first.orientation_xyzw, dtype=np.float64)
    q2 = np.asarray(second.orientation_xyzw, dtype=np.float64)
    cosine = float(np.clip(abs(np.dot(q1, q2) / (np.linalg.norm(q1) * np.linalg.norm(q2))), 0, 1))
    rotation_degrees = float(np.degrees(2 * math.acos(cosine)))
    pitch_degrees = abs(float(first.camera_pitch_degrees) - float(second.camera_pitch_degrees))
    return translation, rotation_degrees, pitch_degrees


def _observation_image_score(frame: FrameObservation) -> float:
    """Cheap relative quality score used only to choose between pose-redundant frames."""
    image = frame.rgb
    width = min(320, image.shape[1])
    thumbnail = cv2.resize(image, (width, max(2, round(width * image.shape[0] / image.shape[1]))),
                           interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(thumbnail, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    usable = float(((gray >= 3) & (gray <= 252)).mean())
    return math.log1p(max(0, sharpness)) * usable


def adaptively_select_observations(frames: Iterable[FrameObservation], *, stats: dict[str, int] | None = None,
                                   translation_m: float = .03, rotation_degrees: float = 2.0,
                                   pitch_degrees: float = 1.5,
                                   maximum_gap_s: float = 2.0) -> Iterator[FrameObservation]:
    """Keep the best image from each nearly stationary camera-pose interval."""
    counters = stats if stats is not None else {}
    counters.update(candidates=0, retained=0, skipped=0)
    anchor = best = None
    best_score = -math.inf
    for frame in frames:
        counters['candidates'] += 1
        if anchor is None:
            anchor = best = frame
            best_score = _observation_image_score(frame)
            continue
        translation, rotation, pitch = _observation_similarity(anchor, frame)
        changed = (translation >= translation_m or rotation >= rotation_degrees
                   or pitch >= pitch_degrees or frame.timestamp_s - anchor.timestamp_s >= maximum_gap_s)
        if changed:
            counters['retained'] += 1
            yield best
            anchor = best = frame
            best_score = _observation_image_score(frame)
        else:
            score = _observation_image_score(frame)
            if score > best_score:
                best, best_score = frame, score
    if best is not None:
        counters['retained'] += 1
        yield best
    counters['skipped'] = counters['candidates'] - counters['retained']


def group_nearby_observations(frames: Iterable[FrameObservation], batch_size: int, *,
                              lookahead_batches: int = 2,
                              stats: dict[str, int] | None = None) -> Iterator[FrameObservation]:
    """Use bounded lookahead so each projection batch contains spatially similar views."""
    if batch_size < 1 or lookahead_batches < 1:
        raise ValueError('Batch size and lookahead must be positive.')
    counters = stats if stats is not None else {}
    counters.update(groups=0, reordered_views=0, maximum_buffered_views=0)
    source = iter(frames)
    limit = batch_size * lookahead_batches
    buffer = list(islice(source, limit))
    while buffer:
        counters['maximum_buffered_views'] = max(counters['maximum_buffered_views'], len(buffer))
        seed = buffer[0]
        if len(buffer) <= batch_size:
            chosen_indices = list(range(len(buffer)))
        else:
            scores = []
            for index, candidate in enumerate(buffer[1:], 1):
                translation, rotation, pitch = _observation_similarity(seed, candidate)
                scores.append((translation / .50 + rotation / 20 + pitch / 20, index))
            chosen_indices = [0, *[index for _, index in sorted(scores)[:batch_size - 1]]]
        group = [buffer[index] for index in chosen_indices]
        if chosen_indices != list(range(len(chosen_indices))):
            counters['reordered_views'] += len(group)
        for index in sorted(chosen_indices, reverse=True):
            del buffer[index]
        counters['groups'] += 1
        yield from group
        buffer.extend(islice(source, limit - len(buffer)))


def _check_cancel(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise ColorizationCancelled("Colorization cancelled; no partial output was published")


def _emit(progress: ProgressCallback | None, stage: str, **values: Any) -> None:
    if progress:
        progress({"stage": stage, **values})


def _prepare(frame: FrameObservation, calibration: Calibration,
             options: ColorizationOptions) -> _PreparedFrame | None:
    if (not isinstance(frame.rgb, np.ndarray) or frame.rgb.dtype != np.uint8
            or frame.rgb.shape != (calibration.image_height, calibration.image_width, 3)):
        raise ColorizationError("RGB observation size/type differs from calibration; expected uint8 HxWx3 RGB")
    if not np.isfinite(frame.timestamp_s):
        raise ColorizationError("Frame timestamp must be finite")
    # A common thumbnail size makes the sharpness score independent of 4K vs HD.
    thumbnail_width = min(640, calibration.image_width)
    thumbnail = cv2.resize(frame.rgb, (thumbnail_width,
                           max(2, round(thumbnail_width * calibration.image_height / calibration.image_width))),
                           interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(thumbnail, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    if sharpness < options.minimum_sharpness:
        return None
    if options.reject_exposure_extremes:
        valid_exposure = (gray >= options.minimum_luminance) & (gray <= options.maximum_luminance)
        if float(valid_exposure.mean()) < 0.1:
            return None
    center, rotation = calibration.camera_pose(frame.position_world_m, frame.orientation_xyzw,
                                                frame.camera_pitch_degrees)
    width = min(options.depth_buffer_width, calibration.image_width)
    height = max(2, round(width * calibration.image_height / calibration.image_width))
    return _PreparedFrame(frame, center, rotation,
                          np.full((height, width), np.inf, dtype=np.float32),
                          width / calibration.image_width, height / calibration.image_height,
                          float(np.clip(math.sqrt(max(sharpness, 1) / 100), 0.1, 1.0)))


def _prepare_image_edges(frame: _PreparedFrame) -> None:
    """Compute the CPU fallback's exact edge expression once per frame, lazily."""
    if frame.image_edge_strength is not None:
        return
    rgb32 = frame.observation.rgb.astype(np.float32)
    luminance = rgb32 @ np.array([.2126, .7152, .0722], dtype=np.float32)
    horizontal = np.abs(luminance[1:-1, 2:] - luminance[1:-1, :-2])
    vertical = np.abs(luminance[2:, 1:-1] - luminance[:-2, 1:-1])
    image_edges = np.zeros(luminance.shape, dtype=np.float32)
    image_edges[1:-1, 1:-1] = np.clip((np.hypot(horizontal, vertical) - 12) / 96, 0, 1)
    frame.image_edge_strength = image_edges


def _project(xyz: np.ndarray, frame: _PreparedFrame, calibration: Calibration,
             options: ColorizationOptions, *, border: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    from .cpu_backend import project as native_cpu_project
    native = native_cpu_project(
        xyz, frame.center, frame.camera_to_world, calibration,
        options.minimum_depth_m, options.maximum_depth_m,
        options.image_border_fraction if border else 0.)
    if native is not None:
        return native
    # With row vectors, world-to-camera is multiplication by camera-to-world R.
    camera = (xyz - frame.center) @ frame.camera_to_world
    z = camera[:, 2]
    selected = np.flatnonzero(np.isfinite(camera).all(axis=1)
                             & (z >= options.minimum_depth_m) & (z <= options.maximum_depth_m))
    camera = camera[selected]
    uv = calibration.project_camera_points(camera)
    pad_x = calibration.image_width * options.image_border_fraction if border else 0
    pad_y = calibration.image_height * options.image_border_fraction if border else 0
    inside = (np.isfinite(uv).all(axis=1) & (uv[:, 0] >= pad_x) & (uv[:, 1] >= pad_y)
              & (uv[:, 0] < calibration.image_width - 1 - pad_x)
              & (uv[:, 1] < calibration.image_height - 1 - pad_y))
    return selected[inside], uv[inside], camera[inside, 2], camera[inside]


def _available_memory_bytes() -> int | None:
    """Best-effort available physical memory without another dependency."""
    try:
        if os.name == 'nt':
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                            ('total_physical', ctypes.c_ulonglong), ('available_physical', ctypes.c_ulonglong),
                            ('total_page_file', ctypes.c_ulonglong), ('available_page_file', ctypes.c_ulonglong),
                            ('total_virtual', ctypes.c_ulonglong), ('available_virtual', ctypes.c_ulonglong),
                            ('available_extended_virtual', ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.available_physical)
        page_size = os.sysconf('SC_PAGE_SIZE')
        pages = os.sysconf('SC_AVPHYS_PAGES')
        return int(page_size * pages)
    except (AttributeError, OSError, ValueError):
        return None


def _use_xyz_cache(point_count: int, options: ColorizationOptions) -> bool:
    if options.cache_xyz is not None:
        return options.cache_xyz
    needed = point_count * 3 * np.dtype(np.float64).itemsize
    available = _available_memory_bytes()
    if options.memory_budget_bytes is not None:
        available=min(available,options.memory_budget_bytes) if available is not None else options.memory_budget_bytes
    # Keep at least 2 GiB and 65% of currently available physical memory free
    # for 4K frames, projection temporaries, the UI, and other applications.
    return available is not None and needed <= 4 * 1024**3 and needed <= max(0, available - 2 * 1024**3) * .35


def _close_array(array: np.ndarray | None) -> None:
    mapping = getattr(array, '_mmap', None)
    if mapping is not None:
        mapping.close()


def _xyz_chunks(path: Path, chunk_size: int, active_offsets: set[int] | None = None,
                xyz_cache: np.ndarray | None = None,
                chunk_ranges: list[tuple[int, int]] | None = None) -> Iterator[tuple[int, np.ndarray]]:
    ranges = chunk_ranges
    if ranges is None:
        with laspy.open(path) as metadata_reader:
            point_count = int(metadata_reader.header.point_count)
        ranges = [(offset, min(chunk_size, point_count - offset))
                  for offset in range(0, point_count, chunk_size)]
    if xyz_cache is not None:
        for offset, length in ranges:
            if active_offsets is None or offset in active_offsets:
                yield offset, xyz_cache[offset:offset + length]
        return
    with laspy.open(path) as reader:
        for offset, length in ranges:
            if active_offsets is not None and offset not in active_offsets:
                continue
            reader.seek(offset)
            points = reader.read_points(length)
            if len(points) != length:
                raise ColorizationError("Source LAS is truncated or changed while reading")
            xyz = np.column_stack((points.x, points.y, points.z))
            yield offset, xyz


def _geometry_chunk_ranges(path: Path, point_count: int, chunk_size: int) -> tuple[list[tuple[int, int]], str]:
    """Use persisted spatial-tile boundaries when present, splitting only oversized tiles."""
    sidecar = path.with_suffix(path.suffix + '.tiles.json')
    try:
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        if data.get('point_count') != point_count:
            raise ValueError('point count differs')
        ranges = []
        cursor = 0
        for start, length in data['tile_ranges']:
            start, length = int(start), int(length)
            if start != cursor or length <= 0:
                raise ValueError('tile ranges are not contiguous')
            for local in range(0, length, chunk_size):
                ranges.append((start + local, min(chunk_size, length - local)))
            cursor += length
        if cursor != point_count:
            raise ValueError('tile ranges are incomplete')
        return ranges, 'persisted_spatial_tiles'
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return ([(offset, min(chunk_size, point_count - offset))
                 for offset in range(0, point_count, chunk_size)], 'fixed_chunks')


@lru_cache(maxsize=32)
def _normalized_image_bounds(width: int, height: int, fx: float, fy: float,
                             cx: float, cy: float, model: str,
                             coefficients: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Return conservative normalized ray bounds for the distorted image perimeter."""
    if model == 'pinhole':
        return (-cx / fx, (width - 1 - cx) / fx,
                -cy / fy, (height - 1 - cy) / fy)
    # Dense perimeter sampling is cheap, cached by calibration, and handles the
    # strong wide-angle distortion of the refined Elios profile. Expanding the
    # extrema protects against numerical interpolation between samples.
    samples = 1024
    xs = np.linspace(0., width - 1., samples)
    ys = np.linspace(0., height - 1., samples)
    pixels = np.concatenate((np.column_stack((xs, np.zeros_like(xs))),
                             np.column_stack((xs, np.full_like(xs, height - 1.))),
                             np.column_stack((np.zeros_like(ys), ys)),
                             np.column_stack((np.full_like(ys, width - 1.), ys))))
    matrix = np.array(((fx, 0., cx), (0., fy, cy), (0., 0., 1.)), dtype=np.float64)
    distortion = np.asarray(coefficients, dtype=np.float64)
    if model == 'fisheye':
        rays = cv2.fisheye.undistortPoints(pixels.reshape(-1, 1, 2), matrix, distortion)
    else:
        rays = cv2.undistortPoints(pixels.reshape(-1, 1, 2), matrix, distortion)
    rays = rays.reshape(-1, 2)
    finite = rays[np.isfinite(rays).all(axis=1)]
    if not len(finite):
        return (-math.inf, math.inf, -math.inf, math.inf)
    margin = max(1e-5, float(np.ptp(finite[:, 0]) + np.ptp(finite[:, 1])) / samples)
    return (float(finite[:, 0].min() - margin), float(finite[:, 0].max() + margin),
            float(finite[:, 1].min() - margin), float(finite[:, 1].max() + margin))


def _chunk_may_be_visible(bounds: np.ndarray, frame: _PreparedFrame,
                           calibration: Calibration, options: ColorizationOptions) -> bool:
    """Conservative 8-corner AABB/frustum rejection, never a visibility decision."""
    corners = bounds[np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)]),
                     np.array([0, 1, 2])]
    camera = (corners - frame.center) @ frame.camera_to_world
    x, y, z = camera.T
    if z.max() < options.minimum_depth_m or z.min() > options.maximum_depth_m:
        return False
    left, right, top, bottom = _normalized_image_bounds(
        calibration.image_width, calibration.image_height, calibration.fx, calibration.fy,
        calibration.cx, calibration.cy, calibration.distortion_model,
        tuple(calibration.distortion_coefficients))
    # Each expression is linear over the AABB, so all eight corners outside one
    # plane proves the complete chunk is outside. Bounds from distorted perimeter
    # rays are intentionally rectangular and expanded, making this rejection safe.
    if (np.all(x < left * z) or np.all(x > right * z)
            or np.all(y < top * z) or np.all(y > bottom * z)):
        return False
    return True


def _pixel_indices(uv: np.ndarray, frame: _PreparedFrame) -> tuple[np.ndarray, np.ndarray]:
    return ((uv[:, 0] * frame.scale_x).astype(np.intp),
            (uv[:, 1] * frame.scale_y).astype(np.intp))


def _sample_rgb(image: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear sample in RGB order without allocating full-frame remap grids."""
    ij = np.floor(uv).astype(np.intp)
    x, y = ij[:, 0], ij[:, 1]
    dx, dy = (uv - ij).T
    top = image[y, x] * (1 - dx[:, None]) + image[y, x + 1] * dx[:, None]
    bottom = image[y + 1, x] * (1 - dx[:, None]) + image[y + 1, x + 1] * dx[:, None]
    return np.rint(top * (1 - dy[:, None]) + bottom * dy[:, None]).astype(np.uint8)


def _update_visibility(xyz: np.ndarray, frame: _PreparedFrame, calibration: Calibration,
                       options: ColorizationOptions) -> None:
    """Update one frame's private depth buffer; safe to run across frames."""
    _, uv, z, _ = _project(xyz, frame, calibration, options, border=False)
    x, y = _pixel_indices(uv, frame)
    np.minimum.at(frame.depth, (y, x), z)


def _finalize_visibility(frame: _PreparedFrame, options: ColorizationOptions, *,
                         expand_occlusion: bool = True) -> None:
    """Record geometric boundary risk before expanding the visibility buffer."""
    kernel = np.ones((3, 3), np.uint8)
    local_min = cv2.erode(frame.depth, kernel, borderType=cv2.BORDER_CONSTANT,
                          borderValue=float("inf"))
    finite_for_max = np.where(np.isfinite(frame.depth), frame.depth, -np.inf)
    local_max = cv2.dilate(finite_for_max, kernel, borderType=cv2.BORDER_CONSTANT,
                           borderValue=-float("inf"))
    valid = np.isfinite(local_min) & np.isfinite(local_max)
    tolerance = np.full(frame.depth.shape, options.occlusion_absolute_tolerance_m,
                        dtype=np.float32)
    tolerance[valid] += local_min[valid] * options.occlusion_relative_tolerance
    denominator = np.maximum(.05, tolerance * 4)
    strength = np.zeros(frame.depth.shape, dtype=np.float32)
    strength[valid] = np.clip((local_max[valid] - local_min[valid] - tolerance[valid])
                              / denominator[valid], 0, 1)
    frame.depth_edge_strength = strength
    if expand_occlusion and options.occlusion_radius_pixels:
        size = 2 * options.occlusion_radius_pixels + 1
        frame.depth = cv2.erode(frame.depth, np.ones((size, size), np.uint8),
                                borderType=cv2.BORDER_CONSTANT, borderValue=float("inf"))


def _edge_confidence(frame: _PreparedFrame, uv: np.ndarray,
                     depth_x: np.ndarray, depth_y: np.ndarray) -> np.ndarray:
    """Downweight samples where image texture and 3-D depth boundaries are fragile."""
    image = frame.observation.rgb
    x = np.clip(np.rint(uv[:, 0]).astype(np.intp), 1, image.shape[1] - 2)
    y = np.clip(np.rint(uv[:, 1]).astype(np.intp), 1, image.shape[0] - 2)
    if frame.image_edge_strength is not None:
        image_edge = frame.image_edge_strength[y, x]
    else:
        weights = np.array([.2126, .7152, .0722], dtype=np.float32)
        horizontal = np.abs((image[y, x + 1].astype(np.float32)
                             - image[y, x - 1].astype(np.float32)) @ weights)
        vertical = np.abs((image[y + 1, x].astype(np.float32)
                           - image[y - 1, x].astype(np.float32)) @ weights)
        image_edge = np.clip((np.hypot(horizontal, vertical) - 12) / 96, 0, 1)
    depth_edge = (frame.depth_edge_strength[depth_y, depth_x]
                  if frame.depth_edge_strength is not None else np.zeros(len(uv), dtype=np.float32))
    # A texture edge alone may be real paint, and a depth edge alone may be a
    # valid thin object. Their coincidence is the high-risk projection case.
    confidence = ((1 - .15 * image_edge) * (1 - .45 * depth_edge)
                  * (1 - .55 * image_edge * depth_edge))
    return np.clip(confidence, .15, 1).astype(np.float32)


def _estimate_illumination(sample_xyz, batch, calibration, options):
    radii = np.full((len(batch), len(sample_xyz)), np.nan)
    light = np.full_like(radii, np.nan)
    for index, frame in enumerate(batch):
        selected, uv, z, camera = _project(sample_xyz, frame, calibration, options, border=True)
        x, y = _pixel_indices(uv, frame)
        near = frame.depth[y, x]
        valid = z <= near + options.occlusion_absolute_tolerance_m + near * options.occlusion_relative_tolerance
        rgb = _sample_rgb(frame.observation.rgb, uv)
        valid &= raw_usable(rgb, options.minimum_luminance, options.maximum_luminance)
        if options.maximum_color_distance_m is not None:
            valid &= np.linalg.norm(camera, axis=1) <= options.maximum_color_distance_m
        radii[index, selected[valid]] = radius_squared(uv[valid], calibration)
        light[index, selected[valid]] = LINEAR[rgb[valid]] @ np.array([.2126, .7152, .0722])
    model, report = fit_model(radii, light)
    for index, frame in enumerate(batch):
        frame.illumination_model = model
        frame.illumination_frame = index
    return report


def _assign_chunk_colors(offset: int, xyz: np.ndarray, batch: list[_PreparedFrame],
                         colors: np.ndarray, quality: np.ndarray, distances: np.ndarray,
                         sources: np.ndarray,
                         calibration: Calibration,
                         options: ColorizationOptions) -> tuple[int, int, int, int]:
    """Color one disjoint LAS slice while preserving chronological frame order."""
    corrected_count = rejected_count = 0
    for frame in batch:
        selected, uv, z, camera = _project(xyz, frame, calibration, options, border=True)
        x, y = _pixel_indices(uv, frame)
        near = frame.depth[y, x]
        visible = z <= near + options.occlusion_absolute_tolerance_m + near * options.occlusion_relative_tolerance
        if not visible.any():
            continue
        selected, uv, z, camera = selected[visible], uv[visible], z[visible], camera[visible]
        x, y = x[visible], y[visible]
        rgb = _sample_rgb(frame.observation.rgb, uv)
        luminance = rgb @ np.array([0.2126, 0.7152, 0.0722])
        usable = np.ones(len(rgb), dtype=bool)
        if options.reject_exposure_extremes:
            usable &= (luminance >= options.minimum_luminance) & (luminance <= options.maximum_luminance)
        if options.illumination_balancing:
            exposure_ok = raw_usable(rgb, options.minimum_luminance, options.maximum_luminance, luminance)
            rejected_count += int(np.count_nonzero(~exposure_ok))
            # Saturated observations are unsafe illumination-fit evidence, but
            # can be genuine white surface colors and remain eligible here.
        point_distance = np.linalg.norm(camera, axis=1)
        if options.maximum_color_distance_m is not None:
            usable &= point_distance <= options.maximum_color_distance_m
        cosine = z / point_distance
        border_distance = np.minimum.reduce((uv[:, 0] / calibration.image_width,
            uv[:, 1] / calibration.image_height,
            1 - uv[:, 0] / calibration.image_width, 1 - uv[:, 1] / calibration.image_height))
        edge_weight = np.clip(border_distance / 0.15, 0.05, 1)
        exposure_weight = 0.5 + 0.5 * (1 - np.abs(luminance - 127.5) / 127.5)
        boundary_weight = _edge_confidence(frame, uv, x, y)
        score = (frame.sharpness_weight * cosine**2 * edge_weight * boundary_weight * exposure_weight
                 / (1 + (z / 5)**2)).astype(np.float32)
        indices = selected + offset
        better = usable & (score > quality[indices])
        candidates = np.flatnonzero(better)
        chosen_rgb = rgb[candidates]
        if frame.illumination_model is not None:
            # Penalties never exceed one, so an existing loser cannot become a
            # winner. Compute gains and encode RGB only for potential winners.
            balance_ok = raw_usable(chosen_rgb, options.minimum_luminance,
                                    options.maximum_luminance)
            gains = np.ones(len(chosen_rgb), dtype=np.float32)
            penalty = np.ones(len(chosen_rgb), dtype=np.float32)
            if balance_ok.any():
                gains[balance_ok], penalty[balance_ok] = frame.illumination_model.adjustment(
                    chosen_rgb[balance_ok], radius_squared(uv[candidates][balance_ok], calibration),
                    frame.illumination_frame)
            score[candidates] *= penalty
            wins = score[candidates] > quality[indices[candidates]]
            better[candidates] = wins
            chosen_rgb = chosen_rgb[wins]
            corrected = chosen_rgb.copy()
            safe_winners = balance_ok[wins]
            if safe_winners.any():
                corrected[safe_winners] = apply_gain(
                    chosen_rgb[safe_winners], gains[wins][safe_winners])
            corrected_count += int(np.count_nonzero(np.any(corrected != chosen_rgb, axis=1)))
            chosen_rgb = corrected
        chosen = indices[better]
        colors[chosen] = chosen_rgb.astype(np.uint16) * 257
        quality[chosen] = score[better]
        distances[chosen] = point_distance[better].astype(np.float32)
        sources[chosen] = frame.observation.source_flight
    return offset, len(xyz), corrected_count, rejected_count


def _assign_chunk_fusion(offset: int, xyz: np.ndarray, batch: list[_PreparedFrame],
                         colors: np.ndarray, quality: np.ndarray, distances: np.ndarray,
                         sources: np.ndarray, fusion_colors: np.ndarray,
                         fusion_weights: np.ndarray, fusion_distances: np.ndarray,
                         fusion_sources: np.ndarray, fusion_groups: np.ndarray,
                         calibration: Calibration,
                         options: ColorizationOptions) -> tuple[int, int, int, int]:
    """Retain the strongest visible observations for later robust per-point fusion."""
    corrected_count = rejected_count = 0
    for frame in batch:
        selected, uv, z, camera = _project(xyz, frame, calibration, options, border=True)
        x, y = _pixel_indices(uv, frame)
        near = frame.depth[y, x]
        visible = z <= near + options.occlusion_absolute_tolerance_m + near * options.occlusion_relative_tolerance
        if not visible.any():
            continue
        selected, uv, z, camera = selected[visible], uv[visible], z[visible], camera[visible]
        x, y = x[visible], y[visible]
        rgb = _sample_rgb(frame.observation.rgb, uv)
        adjusted_rgb = rgb.copy()
        luminance = rgb @ np.array([0.2126, 0.7152, 0.0722])
        usable = np.ones(len(rgb), dtype=bool)
        if options.reject_exposure_extremes:
            usable &= (luminance >= options.minimum_luminance) & (luminance <= options.maximum_luminance)
        if options.illumination_balancing:
            exposure_ok = raw_usable(rgb, options.minimum_luminance, options.maximum_luminance, luminance)
            rejected_count += int(np.count_nonzero(~exposure_ok))
            # Saturated observations are unsafe illumination-fit evidence, but
            # can be genuine white surface colors and remain eligible here.
        point_distance = np.linalg.norm(camera, axis=1)
        if options.maximum_color_distance_m is not None:
            usable &= point_distance <= options.maximum_color_distance_m
        cosine = z / point_distance
        border_distance = np.minimum.reduce((uv[:, 0] / calibration.image_width,
            uv[:, 1] / calibration.image_height,
            1 - uv[:, 0] / calibration.image_width, 1 - uv[:, 1] / calibration.image_height))
        edge_weight = np.clip(border_distance / 0.15, 0.05, 1)
        exposure_weight = 0.5 + 0.5 * (1 - np.abs(luminance - 127.5) / 127.5)
        boundary_weight = _edge_confidence(frame, uv, x, y)
        score = (frame.sharpness_weight * cosine**2 * edge_weight * boundary_weight * exposure_weight
                 / (1 + (z / 5)**2)).astype(np.float32)
        if frame.illumination_model is not None:
            candidates = np.flatnonzero(usable)
            balance_ok = raw_usable(rgb[candidates], options.minimum_luminance,
                                    options.maximum_luminance)
            if balance_ok.any():
                safe = candidates[balance_ok]
                gains, penalty = frame.illumination_model.adjustment(
                    rgb[safe], radius_squared(uv[safe], calibration), frame.illumination_frame)
                score[safe] *= penalty
                corrected = apply_gain(rgb[safe], gains)
                corrected_count += int(np.count_nonzero(np.any(corrected != rgb[safe], axis=1)))
                adjusted_rgb[safe] = corrected
        candidates = np.flatnonzero(usable & (score > 0))
        if not len(candidates):
            continue
        indices = selected[candidates] + offset
        candidate_scores = score[candidates]
        candidate_rgb = adjusted_rgb[candidates]
        candidate_distances = point_distance[candidates].astype(np.float32)

        # Maintain the legacy best observation as the exact fallback for points
        # that never accumulate enough views for robust fusion.
        better = candidate_scores > quality[indices]
        if better.any():
            chosen = indices[better]
            colors[chosen] = candidate_rgb[better].astype(np.uint16) * 257
            quality[chosen] = candidate_scores[better]
            distances[chosen] = candidate_distances[better]
            sources[chosen] = frame.observation.source_flight

        rows = fusion_weights[indices]
        groups = fusion_groups[indices]
        same_group = (groups == frame.diversity_group) & (rows > 0)
        has_same_group = np.any(same_group, axis=1)
        same_slots = np.argmax(same_group, axis=1)
        empty = rows <= 0
        has_empty = np.any(empty, axis=1)
        empty_slots = np.argmax(empty, axis=1)
        weakest_slots = np.argmin(rows, axis=1)
        slots = np.where(has_same_group, same_slots,
                         np.where(has_empty, empty_slots, weakest_slots))
        retained_scores = rows[np.arange(len(indices)), slots]
        retain = candidate_scores > retained_scores
        if retain.any():
            chosen = indices[retain]
            slot = slots[retain]
            fusion_colors[chosen, slot] = candidate_rgb[retain]
            fusion_weights[chosen, slot] = candidate_scores[retain]
            fusion_distances[chosen, slot] = candidate_distances[retain]
            fusion_sources[chosen, slot] = frame.observation.source_flight
            fusion_groups[chosen, slot] = frame.diversity_group
    return offset, len(xyz), corrected_count, rejected_count


def _finalize_fusion(colors: np.ndarray, quality: np.ndarray, distances: np.ndarray,
                     sources: np.ndarray, fusion_colors: np.ndarray,
                     fusion_weights: np.ndarray, fusion_distances: np.ndarray,
                     fusion_sources: np.ndarray, fusion_groups: np.ndarray,
                     options: ColorizationOptions) -> dict[str, Any]:
    """Choose a robust observed-color medoid after rejecting luminance outliers."""
    report = dict(requested=True, applied=False, maximum_observations=options.fusion_max_observations,
                  minimum_observations=options.fusion_min_observations,
                  points_with_multiple_observations=0, points_fused=0,
                  points_using_best_observation_fallback=0, rejected_outlier_observations=0,
                  bright_consensus_points=0, diverse_bright_consensus_points=0,
                  isolated_bright_observations_rejected=0,
                  pose_translation_threshold_m=.15, pose_rotation_threshold_degrees=3.0,
                  white_consensus_fraction=.60,
                  edge_confidence_policy={
                      'image_gradient_start': 12.0,
                      'depth_tolerance': 'occlusion_absolute + depth * occlusion_relative',
                      'minimum_weight': .15,
                  },
                  method=('pose-diverse top-quality reservoir; diverse-view neutral-white majority; '
                          'linear-RGB luminance outlier rejection; weighted observed-color medoid'))
    for offset in range(0, len(colors), options.chunk_size):
        end = min(len(colors), offset + options.chunk_size)
        weights = np.asarray(fusion_weights[offset:end])
        counts = np.count_nonzero(weights > 0, axis=1)
        report['points_with_multiple_observations'] += int(np.count_nonzero(counts >= 2))
        eligible = np.flatnonzero(counts >= options.fusion_min_observations)
        report['points_using_best_observation_fallback'] += int(np.count_nonzero(counts > 0))
        if not len(eligible):
            continue
        candidate_weights = weights[eligible].astype(np.float32, copy=True)
        candidate_colors = np.asarray(fusion_colors[offset:end][eligible])
        active = candidate_weights > 0
        candidate_groups = np.asarray(fusion_groups[offset:end][eligible])
        duplicate_groups = ((candidate_groups[:, :, None] == candidate_groups[:, None, :])
                            & active[:, :, None] & active[:, None, :]
                            & ~np.eye(options.fusion_max_observations, dtype=bool)[None])
        if np.any(duplicate_groups):
            raise ColorizationError("Fusion reservoir contains duplicate pose-diversity groups")
        channel_spread = candidate_colors.max(axis=2) - candidate_colors.min(axis=2)
        srgb_luminance = candidate_colors @ np.array([.2126, .7152, .0722], dtype=np.float32)
        bright = active & (srgb_luminance >= 235) & (channel_spread <= 28)
        bright_count = np.count_nonzero(bright, axis=1)
        sorted_bright = np.sort(np.where(bright, srgb_luminance, np.inf), axis=1)
        median_slot = np.maximum(0, (bright_count - 1) // 2)
        bright_median = np.take_along_axis(sorted_bright, median_slot[:, None], axis=1)[:, 0]
        stable_bright = bright & (np.abs(srgb_luminance - bright_median[:, None]) <= 20)
        required_bright = np.maximum(2, np.ceil(counts[eligible] * .60).astype(np.intp))
        bright_consensus = np.count_nonzero(stable_bright, axis=1) >= required_bright
        report['bright_consensus_points'] += int(np.count_nonzero(bright_consensus))
        report['diverse_bright_consensus_points'] += int(np.count_nonzero(bright_consensus))
        linear = LINEAR[candidate_colors].astype(np.float32, copy=False)
        luminance = linear @ np.array([.2126, .7152, .0722], dtype=np.float32)
        luminance[~active] = np.nan
        median = np.nanmedian(luminance, axis=1)
        deviation = np.abs(luminance - median[:, None])
        mad = np.nanmedian(deviation, axis=1)
        threshold = np.maximum(.04, 3.0 * mad)
        inlier = active & (deviation <= threshold[:, None])
        isolated_bright = bright & ~bright_consensus[:, None]
        report['isolated_bright_observations_rejected'] += int(np.count_nonzero(isolated_bright & ~inlier))
        # A majority of pose-diverse neutral-white observations indicates
        # persistent surface color. Restrict those points to that consensus so
        # a few projection misses onto the darker neighborhood cannot erase it.
        inlier[bright_consensus] = stable_bright[bright_consensus]
        # The median/MAD rule always retains a central observation, but require
        # two agreeing views; otherwise preserve the legacy best observation.
        consensus = np.count_nonzero(inlier, axis=1) >= 2
        report['rejected_outlier_observations'] += int(np.count_nonzero(active & ~inlier))
        if not consensus.any():
            continue
        eligible = eligible[consensus]
        candidate_weights = candidate_weights[consensus]
        candidate_colors = candidate_colors[consensus]
        linear = linear[consensus]
        inlier = inlier[consensus]
        positive = np.where(inlier, candidate_weights, np.nan)
        typical_weight = np.nanmedian(positive, axis=1)
        robust_weights = np.where(inlier,
                                  np.minimum(candidate_weights, 2 * typical_weight[:, None]), 0)
        objectives = np.full(candidate_weights.shape, np.inf, dtype=np.float32)
        for candidate in range(options.fusion_max_observations):
            distance = np.abs(linear - linear[:, candidate:candidate + 1]).sum(axis=2)
            objectives[:, candidate] = np.sum(distance * robust_weights, axis=1)
        objectives[~inlier] = np.inf
        chosen_slots = np.argmin(objectives, axis=1)
        rows = np.arange(len(eligible))
        chosen = offset + eligible
        colors[chosen] = candidate_colors[rows, chosen_slots].astype(np.uint16) * 257
        quality[chosen] = candidate_weights[rows, chosen_slots]
        distances[chosen] = fusion_distances[chosen, chosen_slots]
        sources[chosen] = fusion_sources[chosen, chosen_slots]
        report['points_fused'] += len(chosen)
        report['points_using_best_observation_fallback'] -= len(chosen)
    report['applied'] = report['points_fused'] > 0
    return report


def _bounded_parallel_map(executor: ThreadPoolExecutor, function: Callable,
                          items: Iterable, width: int) -> Iterator:
    """Submit at most one worker-width group so cancellation stays responsive."""
    iterator = iter(items)
    while True:
        group = list(islice(iterator, width))
        if not group:
            return
        futures = [executor.submit(function, item) for item in group]
        for future in futures:
            yield future.result()


def _output_header(source: laspy.LasHeader, merged_output: bool = False) -> laspy.LasHeader:
    if source.point_format.id in (4, 5, 9, 10):
        raise ColorizationError("Waveform LAS requires waveform payload handling and is not supported")
    target_format = {0: 2, 1: 3, 2: 2, 3: 3, 6: 7, 7: 7, 8: 8}[source.point_format.id]
    header = copy.deepcopy(source)
    point_format = laspy.PointFormat(target_format)
    for dimension in source.point_format.extra_dimensions:
        point_format.add_extra_dimension(laspy.ExtraBytesParams(
            dimension.name, dimension.dtype, description=dimension.description,
            scales=dimension.scales, offsets=dimension.offsets, no_data=dimension.no_data,
        ))
    # RGB point formats require LAS 1.2 or later (old format 0/1 inputs may be 1.0).
    version = max(str(source.version), "1.2")
    header.set_version_and_point_format(laspy.header.Version.from_str(version), point_format)
    if "Colorized" in header.point_format.dimension_names:
        if header.point_format.dimension_by_name("Colorized").dtype != np.dtype("uint8"):
            raise ColorizationError("Existing Colorized dimension must be uint8")
    else:
        header.add_extra_dim(laspy.ExtraBytesParams("Colorized", "uint8", description="1=RGB observed; 0=unobserved"))
    for name, description in (
        ("ColorConfidence", "Selected RGB confidence"),
        ("ColorDistance", "Selected camera distance (m)"),
    ):
        if merged_output:
            continue
        if name in header.point_format.dimension_names:
            if header.point_format.dimension_by_name(name).dtype != np.dtype("float32"):
                raise ColorizationError(f"Existing {name} dimension must be float32")
        else:
            header.add_extra_dim(laspy.ExtraBytesParams(name, "float32", description=description))
    if "SourceFlight" in header.point_format.dimension_names:
        if header.point_format.dimension_by_name("SourceFlight").dtype != np.dtype("uint16"):
            raise ColorizationError("Existing SourceFlight dimension must be uint16")
    else:
        header.add_extra_dim(laspy.ExtraBytesParams("SourceFlight", "uint16",
                                                    description="Selected source flight (1-based)"))
    if merged_output:
        header.remove_extra_dims([name for name in ("ColorConfidence", "ColorDistance")
                                  if name in header.point_format.extra_dimension_names])
    return header


def colorize_las(
    input_path: str | Path,
    output_path: str | Path,
    frames: Iterable[FrameObservation],
    calibration: Calibration,
    *,
    options: ColorizationOptions | None = None,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
    candidate_directory: str | Path | None = None,
) -> ColorizationResult:
    """Color an existing LAS/LAZ and atomically publish a new .las file.

    RGB, Colorized and LAS format/storage bookkeeping are updated. Original raw
    integer XYZ, scale, offset, order, attributes, and VLR/EVLR metadata remain.
    Unobserved points have RGB=(0,0,0), Colorized=0. Real observed black is
    distinguishable via Colorized=1. No nearest-neighbor color fill is performed.
    Highest quality visible observation wins, avoiding seam blur from imperfect
    alignment. Output color channels use uint16 values with 8-bit values * 257.

    Frames are consumed lazily and retained only for one bounded batch. Supply a
    re-iterable source to run again. Cancellation is checked at each LAS chunk.
    """
    options = options or ColorizationOptions()
    candidate_path = Path(candidate_directory).resolve() if candidate_directory is not None else None
    from .runtime import StageTimings, prefetch
    timings = StageTimings()
    processing_started = time.perf_counter()
    from .cuda_backend import (CudaBackendError, cuda_checkpoint,
                               cuda_projection_backend)
    acceleration = cuda_checkpoint().to_dict()
    timings.add('acceleration_initialization', time.perf_counter() - processing_started)
    source, destination = Path(input_path).resolve(), Path(output_path).resolve()
    if source == destination:
        raise ColorizationError("Input and output must be different files")
    if destination.suffix.lower() != ".las":
        raise ColorizationError("Output must use .las; this preserves geometry without compression dependencies")
    if not isinstance(calibration, Calibration) or (not calibration.validated and not options.experimental_calibration):
        raise ColorizationError("A validated RGB camera calibration is required")
    cuda_backend, cuda_reason = (cuda_projection_backend(calibration) if options.cuda_enabled else
                                 (None,'CUDA disabled by custom hardware resource settings'))
    from .cpu_backend import available as native_cpu_available
    cpu_backend_name = ('native_cpu_projection' if native_cpu_available(calibration)
                        else 'numpy_opencv_cpu')
    acceleration.update(
        processing_backend=(('cuda_pose_diverse_fusion' if options.multi_frame_fusion
                             else 'cuda_edge_aware_color')
                            if cuda_backend else cpu_backend_name),
        cuda_acceleration_active=cuda_backend is not None,
        fallback_reason=cuda_reason,
        visibility_chunks=0,
        color_chunks=0,
        points_projected_for_visibility=0,
        points_processed_for_color=0,
        cuda_streams=1 if cuda_backend is not None else 0,
        bounded_prefetch_depth=2,
        cpu_gpu_io_overlap=cuda_backend is not None,
        selected_cuda_device=options.cuda_device_index if cuda_backend is not None else None,
    )
    acceleration['summary'] = (
        (f"CUDA device {options.cuda_device_index + 1} deterministic "
         f"{'pose-diverse fusion' if options.multi_frame_fusion else 'edge-aware color selection'} "
         f"enabled on {acceleration.get('device_name')}")
        if cuda_backend else f"CPU fallback ({cpu_backend_name}): {cuda_reason}")
    _emit(progress, "acceleration", **acceleration)
    if destination.exists() and not options.overwrite:
        raise ColorizationError("Output already exists; choose another file name")
    checkpoint_path = candidate_path / 'checkpoint.json' if candidate_path is not None else None
    resume_checkpoint = None
    signature = hashlib.sha256(json.dumps({
        'source': str(source), 'source_size': source.stat().st_size,
        'source_mtime_ns': source.stat().st_mtime_ns,
        'calibration': calibration.to_dict(),
        'options': asdict(options),
    }, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    if candidate_path is not None and candidate_path.exists():
        try:
            resume_checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ColorizationError("Candidate shard directory already exists and is not resumable")
        if resume_checkpoint.get('signature') != signature:
            raise ColorizationError("Candidate shard checkpoint belongs to different inputs or settings")
    _check_cancel(cancelled)
    with laspy.open(source) as reader:
        original_header = copy.deepcopy(reader.header)
    count = original_header.point_count
    if count == 0:
        raise ColorizationError("Source LAS contains no points")
    header = _output_header(original_header, options.merged_output)
    chunk_ranges, chunk_layout = _geometry_chunk_ranges(source, count, options.chunk_size)
    acceleration['chunk_layout'] = chunk_layout
    acceleration['geometry_tasks'] = len(chunk_ranges)
    destination.parent.mkdir(parents=True, exist_ok=True)
    use_xyz_cache = _use_xyz_cache(count, options)
    fusion_bytes = (count * options.fusion_max_observations * (3 + 4 + 4 + 2 + 4)
                    if options.multi_frame_fusion else 0)
    state_bytes = count * 14 + fusion_bytes
    available_memory = _available_memory_bytes()
    ram_budget = (min(available_memory, options.memory_budget_bytes)
                  if available_memory is not None and options.memory_budget_bytes is not None
                  else options.memory_budget_bytes or available_memory)
    state_in_ram = (candidate_path is None and options.state_storage != 'disk'
                    and ram_budget is not None
                    and (options.state_storage == 'ram' or state_bytes <= max(0, ram_budget - 4 * 1024**3) * .30))
    acceleration.update(state_storage='ram' if state_in_ram else 'memory_mapped_scratch',
                        state_bytes=state_bytes)
    scratch_root = Path(options.scratch_directory or os.environ.get(
        'ELIOS_COLORIZER_SCRATCH', str(destination.parent))).resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    required_bytes = (count * (16 if candidate_path is not None else header.point_format.size)
                      + (0 if state_in_ram else state_bytes) + 64 * 1024**2)
    if shutil.disk_usage(scratch_root).free < required_bytes:
        raise ColorizationError(f"Insufficient output disk space; need about {required_bytes / 1024**3:.2f} GiB")
    received, used, rejected = 0, 0, 0
    illumination = dict(requested=options.illumination_balancing, applied=False,
        accepted_batches=0, skipped_batches=0, overlap_sample_count=0,
        radial_gain_range=[1., 1.], exposure_gain_range=[1., 1.],
        corrected_observations=0, excluded_from_correction_observations=0,
        rejected_exposure_observations=0,
        minimum_model_confidence=None, fallback_reasons={},
        scope="batch-local radial response and matched per-frame exposure offsets")
    fusion = dict(requested=options.multi_frame_fusion, applied=False,
                  maximum_observations=options.fusion_max_observations,
                  minimum_observations=options.fusion_min_observations,
                  points_with_multiple_observations=0, points_fused=0,
                  points_using_best_observation_fallback=0,
                  rejected_outlier_observations=0,
                  method=('pose-diverse top-quality reservoir; linear-RGB luminance outlier rejection; '
                          'weighted observed-color medoid'))
    sample_ids = (np.linspace(0, count - 1, min(count, 8192), dtype=np.int64)
                  if options.illumination_balancing else np.empty(0, dtype=np.int64))
    sample_xyz = np.empty((len(sample_ids), 3))
    with tempfile.TemporaryDirectory(prefix=".elios-colorize-", dir=scratch_root) as scratch:
        work = candidate_path if candidate_path is not None else Path(scratch)
        if candidate_path is not None and resume_checkpoint is None:
            candidate_path.mkdir(parents=True, exist_ok=False)
        resume_mode = resume_checkpoint is not None
        completed_batches = int((resume_checkpoint or {}).get('completed_batches', 0))
        def state_array(name, dtype, shape):
            return (np.zeros(shape, dtype=dtype) if state_in_ram else
                    np.memmap(work / name, mode='r+' if resume_mode else "w+", dtype=dtype, shape=shape))
        colors = state_array("rgb.u16", np.uint16, (count, 3))
        quality = state_array("quality.f32", np.float32, (count,))
        distances = state_array("distance.f32", np.float32, (count,))
        sources = state_array("source.u16", np.uint16, (count,))
        fusion_colors = fusion_weights = fusion_distances = fusion_sources = fusion_groups = None
        if options.multi_frame_fusion:
            shape = (count, options.fusion_max_observations)
            fusion_colors = state_array("fusion-rgb.u8", np.uint8, (*shape, 3))
            fusion_weights = state_array("fusion-weight.f32", np.float32, shape)
            fusion_distances = state_array("fusion-distance.f32", np.float32, shape)
            fusion_sources = state_array("fusion-source.u16", np.uint16, shape)
            fusion_groups = state_array("fusion-group.u32", np.uint32, shape)
        # Freshly created mapped files are zero initialized by the operating system.
        active_cuda_batch = None
        diversity_groups = _PoseDiversityGroups()
        try:
            # Chunk bounds are tiny (about 4 KB per 20M points) and let later passes
            # seek past distant/behind-camera chunks without decoding their XYZ.
            chunk_bounds: dict[int, np.ndarray] = {}
            chunk_lengths: dict[int, int] = {}
            xyz_cache = np.empty((count, 3), dtype=np.float64) if use_xyz_cache else None
            indexing_started = time.perf_counter()
            for offset, xyz in _xyz_chunks(source, options.chunk_size, chunk_ranges=chunk_ranges):
                _check_cancel(cancelled)
                if not np.isfinite(xyz).all():
                    raise ColorizationError("Source LAS contains nonfinite coordinates")
                lo, hi = np.searchsorted(sample_ids, [offset, offset + len(xyz)])
                sample_xyz[lo:hi] = xyz[sample_ids[lo:hi] - offset]
                chunk_bounds[offset] = np.array([xyz.min(axis=0), xyz.max(axis=0)])
                chunk_lengths[offset] = len(xyz)
                if xyz_cache is not None:
                    xyz_cache[offset:offset + len(xyz)] = xyz
                _emit(progress, "indexing", points_processed=offset + len(xyz), point_count=count,
                      xyz_cache_used=use_xyz_cache, worker_threads=options.worker_threads)
            timings.add('geometry_indexing', time.perf_counter() - indexing_started)
            # Decode the next observations while the current batch is projected.
            # This queue stays tiny because full-resolution RGB frames are large.
            stream = iter(prefetch(frames, depth=2))
            batch_index = 0
            pool_context = (ThreadPoolExecutor(max_workers=options.worker_threads,
                                               thread_name_prefix='elios-project')
                            if options.worker_threads > 1 else nullcontext(None))
            with pool_context as executor:
              while True:
                _check_cancel(cancelled)
                batch: list[_PreparedFrame] = []
                # islice bounds decoded-frame memory even when every frame is rejected.
                stream_started = time.perf_counter()
                observations = list(islice(stream, options.frame_batch_size))
                timings.add('frame_stream_wait', time.perf_counter() - stream_started)
                if not observations:
                    break
                batch_index += 1
                preparation_started = time.perf_counter()
                for observation in observations:
                    received += 1
                    prepared = _prepare(observation, calibration, options)
                    if prepared is None:
                        rejected += 1
                    else:
                        prepared.diversity_group = diversity_groups.assign(prepared)
                        batch.append(prepared)
                timings.add('frame_preparation', time.perf_counter() - preparation_started,
                            operations=len(observations))
                if not batch:
                    _emit(progress, "frames_rejected", frames_received=received, frames_rejected=rejected)
                    continue
                used += len(batch)
                if batch_index <= completed_batches:
                    _emit(progress, 'checkpoint_resume', batch=batch_index,
                          completed_batches=completed_batches,
                          frames_received=received, frames_used=used)
                    continue
                planning_started = time.perf_counter()
                from .task_graph import build_projection_task_graph
                task_graph = build_projection_task_graph(
                    chunk_bounds, chunk_lengths, batch,
                    lambda bounds, frame: _chunk_may_be_visible(
                        bounds, frame, calibration, options))
                visible_frames_by_offset = task_graph.frame_masks
                timings.add('frustum_task_planning', time.perf_counter() - planning_started)
                active_offsets = set(visible_frames_by_offset)
                batch_work_total = sum(chunk_lengths[offset] * len(indices)
                                       for offset, indices in visible_frames_by_offset.items())
                visibility_work = 0
                acceleration['chunk_frame_pairs_after_culling'] = (
                    acceleration.get('chunk_frame_pairs_after_culling', 0)
                    + sum(len(indices) for indices in visible_frames_by_offset.values()))
                acceleration['chunk_frame_pairs_before_culling'] = (
                    acceleration.get('chunk_frame_pairs_before_culling', 0)
                    + len(chunk_bounds) * len(batch))
                acceleration['work_units_rejected_by_culling'] = (
                    acceleration.get('work_units_rejected_by_culling', 0)
                    + task_graph.rejected_work_units)
                visibility_started = time.perf_counter()
                cuda_batch = None
                if cuda_backend is not None:
                    try:
                        cuda_batch = cuda_backend.begin_batch(batch, calibration, options)
                        active_cuda_batch = cuda_batch
                        for offset, xyz in prefetch(
                                _xyz_chunks(source, options.chunk_size, active_offsets, xyz_cache, chunk_ranges), 2):
                            _check_cancel(cancelled)
                            frame_indices = visible_frames_by_offset[offset]
                            cuda_batch.visibility(xyz, frame_indices)
                            acceleration['visibility_chunks'] += 1
                            acceleration['points_projected_for_visibility'] += len(xyz) * len(frame_indices)
                            visibility_work += len(xyz) * len(frame_indices)
                            _emit(progress, "visibility", batch=batch_index,
                                  points_processed=offset + len(xyz), point_count=count,
                                  work_completed=visibility_work, work_total=batch_work_total,
                                  frames_received=received, frames_used=used)
                        # Copy the raw CUDA depth map back before applying the
                        # shared edge analysis and occlusion expansion on CPU.
                        cuda_batch.finish_visibility(options.occlusion_radius_pixels)
                    except CudaBackendError as exc:
                        if cuda_batch is not None:
                            cuda_batch.close()
                        cuda_batch = active_cuda_batch = None
                        cuda_backend = None
                        cuda_reason = str(exc)
                        acceleration.update(processing_backend=cpu_backend_name,
                                            cuda_acceleration_active=False,
                                            fallback_reason=cuda_reason,
                                            summary=f'CPU fallback: {cuda_reason}')
                        _emit(progress, "acceleration", **acceleration)
                        for frame in batch:
                            frame.depth.fill(np.inf)
                if cuda_batch is None:
                    for offset, xyz in prefetch(
                            _xyz_chunks(source, options.chunk_size, active_offsets, xyz_cache, chunk_ranges), 2):
                        _check_cancel(cancelled)
                        active_frames = [batch[index] for index in visible_frames_by_offset[offset]]
                        if executor is None or len(active_frames) == 1:
                            for frame in active_frames:
                                _update_visibility(xyz, frame, calibration, options)
                        else:
                            list(executor.map(lambda frame: _update_visibility(xyz, frame, calibration, options), active_frames))
                        visibility_work += len(xyz) * len(active_frames)
                        _emit(progress, "visibility", batch=batch_index, points_processed=offset + len(xyz),
                              point_count=count, work_completed=visibility_work,
                              work_total=batch_work_total, frames_received=received, frames_used=used)
                for frame in batch:
                    _finalize_visibility(frame, options)
                timings.add('cuda_visibility' if cuda_batch is not None else 'cpu_visibility',
                            time.perf_counter() - visibility_started)
                if options.illumination_balancing:
                    illumination_started = time.perf_counter()
                    fit = _estimate_illumination(sample_xyz, batch, calibration, options)
                    timings.add('illumination_fitting', time.perf_counter() - illumination_started)
                    illumination['overlap_sample_count'] += fit['overlap_sample_count']
                    key = 'accepted_batches' if fit['accepted'] else 'skipped_batches'
                    illumination[key] += 1
                    if fit['accepted']:
                        for name in ('radial_gain_range', 'exposure_gain_range'):
                            illumination[name][0] = min(illumination[name][0], fit[name][0])
                            illumination[name][1] = max(illumination[name][1], fit[name][1])
                        prior = illumination['minimum_model_confidence']
                        illumination['minimum_model_confidence'] = min(prior, fit['confidence']) if prior is not None else fit['confidence']
                    else:
                        reasons = illumination['fallback_reasons']
                        reasons[fit['reason']] = reasons.get(fit['reason'], 0) + 1
                    _emit(progress, "illumination", batch=batch_index, **fit)
                    if cuda_batch is not None:
                        try:
                            cuda_batch.set_illumination()
                        except CudaBackendError as exc:
                            cuda_batch.close()
                            cuda_batch = active_cuda_batch = None
                            cuda_backend = None
                            cuda_reason = str(exc)
                            acceleration.update(processing_backend=cpu_backend_name,
                                                cuda_acceleration_active=False,
                                                fallback_reason=cuda_reason,
                                                summary=f'CPU fallback: {cuda_reason}')
                            _emit(progress, "acceleration", **acceleration)
                color_started = time.perf_counter()
                completed_chunks = None
                cuda_counts = []
                color_work = 0
                if cuda_batch is not None:
                    try:
                        for offset, xyz in prefetch(
                                _xyz_chunks(source, options.chunk_size, active_offsets, xyz_cache, chunk_ranges), 2):
                            indices = visible_frames_by_offset[offset]
                            target = slice(offset, offset + len(xyz))
                            if options.multi_frame_fusion:
                                corrected_count, rejected_count = cuda_batch.assign_fusion(
                                    xyz, colors[target], quality[target], distances[target], sources[target],
                                    fusion_colors[target], fusion_weights[target], fusion_distances[target],
                                    fusion_sources[target], fusion_groups[target], indices)
                            else:
                                corrected_count, rejected_count = cuda_batch.assign(
                                    xyz, colors[target], quality[target], distances[target], sources[target], indices)
                            cuda_counts.append((offset, len(xyz), corrected_count, rejected_count))
                            acceleration['color_chunks'] += 1
                            acceleration['points_processed_for_color'] += len(xyz) * len(indices)
                            color_work += len(xyz) * len(indices)
                            _check_cancel(cancelled)
                            _emit(progress, "colorizing", batch=batch_index,
                                  points_processed=offset + len(xyz), point_count=count,
                                  work_completed=color_work, work_total=batch_work_total,
                                  frames_received=received, frames_used=used)
                    except CudaBackendError as exc:
                        cuda_batch.close()
                        cuda_batch = active_cuda_batch = None
                        cuda_backend = None
                        cuda_reason = str(exc)
                        cuda_counts.clear()
                        acceleration.update(processing_backend=cpu_backend_name,
                                            cuda_acceleration_active=False,
                                            fallback_reason=cuda_reason,
                                            summary=f'CPU fallback: {cuda_reason}')
                        _emit(progress, "acceleration", **acceleration)
                if cuda_batch is None:
                    edge_started = time.perf_counter()
                    for frame in batch:
                        _prepare_image_edges(frame)
                    timings.add('cpu_edge_map_preparation', time.perf_counter() - edge_started,
                                operations=len(batch))
                    chunks = prefetch(_xyz_chunks(source, options.chunk_size, active_offsets, xyz_cache,
                                                  chunk_ranges), 2)
                    if options.multi_frame_fusion:
                        def assign(item):
                            offset, xyz = item
                            active = [batch[index] for index in visible_frames_by_offset[offset]]
                            return _assign_chunk_fusion(
                                offset, xyz, active, colors, quality, distances, sources,
                                fusion_colors, fusion_weights, fusion_distances, fusion_sources,
                                fusion_groups, calibration, options)
                    else:
                        def assign(item):
                            offset, xyz = item
                            active = [batch[index] for index in visible_frames_by_offset[offset]]
                            return _assign_chunk_colors(offset, xyz, active, colors, quality, distances,
                                                        sources, calibration, options)
                    completed_chunks = (map(assign, chunks) if executor is None else
                                        _bounded_parallel_map(executor, assign, chunks, options.worker_threads))
                    result_counts = completed_chunks
                else:
                    result_counts = iter(cuda_counts)
                color_work = 0
                for completed_offset, completed_length, corrected_count, rejected_count in result_counts:
                    illumination['corrected_observations'] += corrected_count
                    illumination['rejected_exposure_observations'] += rejected_count
                    illumination['excluded_from_correction_observations'] += rejected_count
                    color_work += completed_length * len(visible_frames_by_offset[completed_offset])
                    if cuda_batch is None:
                        _emit(progress, "colorizing", batch=batch_index,
                              points_processed=completed_offset + completed_length, point_count=count,
                              work_completed=color_work, work_total=batch_work_total,
                              frames_received=received, frames_used=used)
                timings.add(('cuda_fusion_candidates' if options.multi_frame_fusion
                             else 'cuda_edge_aware_color_assignment') if cuda_batch is not None else
                            ('cpu_fusion_candidates' if options.multi_frame_fusion
                             else 'cpu_edge_aware_color_assignment'),
                            time.perf_counter() - color_started)
                if cuda_batch is not None:
                    cuda_batch.close()
                    cuda_batch = active_cuda_batch = None
                if checkpoint_path is not None:
                    checkpoint_started = time.perf_counter()
                    for array in (colors, quality, distances, sources, fusion_colors,
                                  fusion_weights, fusion_distances, fusion_sources, fusion_groups):
                        if array is not None and hasattr(array, 'flush'):
                            array.flush()
                    checkpoint_temp = checkpoint_path.with_suffix('.tmp')
                    checkpoint_temp.write_text(json.dumps({
                        'format': 'elios-color-checkpoint-v1', 'signature': signature,
                        'completed_batches': batch_index, 'frames_received': received,
                        'frames_used': used, 'frames_rejected': rejected,
                    }), encoding='utf-8')
                    os.replace(checkpoint_temp, checkpoint_path)
                    timings.add('checkpoint_io', time.perf_counter() - checkpoint_started)
                # Drop the full-resolution RGB arrays before decoding the next batch.
                del batch, observations, prepared, observation
            if used == 0:
                raise ColorizationError("No usable video observations; check frame timing, image quality and calibration")
            _check_cancel(cancelled)
            if candidate_path is not None:
                for array in (colors, quality, distances, sources):
                    if hasattr(array, 'flush'):
                        array.flush()
                colored = sum(int(np.count_nonzero(quality[offset:offset + options.chunk_size] > 0))
                              for offset in range(0, count, options.chunk_size))
                timings.add('total', time.perf_counter() - processing_started)
                metadata = {
                    'format': ('elios-color-candidates-v2' if options.multi_frame_fusion
                               else 'elios-color-candidates-v1'),
                    'point_count': count,
                    'frames_received': received, 'frames_used': used, 'frames_rejected': rejected,
                    'colored_point_count': colored, 'source_flight_values_preserved': True,
                    'multi_frame_fusion': options.multi_frame_fusion,
                    'fusion_max_observations': options.fusion_max_observations,
                    'fusion_min_observations': options.fusion_min_observations,
                    'acceleration': acceleration, 'performance': timings.to_dict(),
                }
                (candidate_path / 'candidate.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                checkpoint_path.unlink(missing_ok=True)
                result = ColorizationResult(candidate_path, count, colored, received, used, rejected,
                                             experimental_calibration=not calibration.validated,
                                             xyz_cache_used=use_xyz_cache,
                                             illumination_balancing=illumination,
                                             multi_frame_fusion=fusion,
                                             acceleration=acceleration,
                                             performance=timings.to_dict())
                _emit(progress, "candidate_complete", **result.to_dict())
                return result
            if options.multi_frame_fusion:
                fusion_started = time.perf_counter()
                _emit(progress, "fusion", points_processed=0, point_count=count)
                fusion = _finalize_fusion(colors, quality, distances, sources,
                                          fusion_colors, fusion_weights, fusion_distances,
                                          fusion_sources, fusion_groups, options)
                _emit(progress, "fusion", points_processed=count, point_count=count, **fusion)
                timings.add('final_fusion', time.perf_counter() - fusion_started)
                _check_cancel(cancelled)
            temporary_output = work / "result.las"
            colored = 0
            writing_started = time.perf_counter()
            with laspy.open(source) as reader, laspy.open(temporary_output, mode="w", header=header) as writer:
                offset = 0
                for original_points in reader.chunk_iterator(options.chunk_size):
                    _check_cancel(cancelled)
                    length = len(original_points)
                    points = laspy.PackedPointRecord.from_point_record(original_points, header.point_format)
                    # PackedPointRecord copies raw XYZ integers exactly, without re-quantization.
                    points.red = colors[offset:offset + length, 0]
                    points.green = colors[offset:offset + length, 1]
                    points.blue = colors[offset:offset + length, 2]
                    observed = quality[offset:offset + length] > 0
                    points["Colorized"] = observed.astype(np.uint8)
                    if not options.merged_output:
                        points["ColorConfidence"] = quality[offset:offset + length]
                        points["ColorDistance"] = distances[offset:offset + length]
                    points["SourceFlight"] = sources[offset:offset + length]
                    colored += int(observed.sum())
                    writer.write_points(points)
                    offset += length
                    _emit(progress, "writing", points_processed=offset, point_count=count,
                          colored_point_count=colored, frames_used=used)
                if original_header.evlrs:
                    writer.write_evlrs(original_header.evlrs)
            timings.add('las_writing', time.perf_counter() - writing_started)
            if colored == 0:
                raise ColorizationError("No LAS points received color; verify LAS/pose coordinates, camera calibration and time synchronization")
            _check_cancel(cancelled)
            # On Windows rename refuses an existing destination, avoiding an overwrite race.
            if options.overwrite:
                os.replace(temporary_output, destination)
            elif os.name != "nt":
                # POSIX rename overwrites; a same-filesystem hard link atomically
                # fails if the requested name was created by another process.
                os.link(temporary_output, destination)
                temporary_output.unlink()
            else:
                os.rename(temporary_output, destination)
            illumination['applied'] = illumination['corrected_observations'] > 0
            timings.add('total', time.perf_counter() - processing_started)
            result = ColorizationResult(destination, count, colored, received, used, rejected,
                                         experimental_calibration=not calibration.validated,
                                         xyz_cache_used=use_xyz_cache, illumination_balancing=illumination,
                                         multi_frame_fusion=fusion,
                                         acceleration=acceleration,
                                         performance=timings.to_dict())
            _emit(progress, "complete", **result.to_dict())
            return result
        finally:
            # Windows cannot remove an open memory-mapped file.
            if active_cuda_batch is not None:
                active_cuda_batch.close()
            _close_array(colors)
            _close_array(quality)
            _close_array(distances)
            _close_array(sources)
            for array in (fusion_colors, fusion_weights, fusion_distances, fusion_sources, fusion_groups):
                _close_array(array)


def reduce_candidate_shards(input_path: str | Path, output_path: str | Path,
                            candidate_directories: Iterable[str | Path], *,
                            chunk_size: int = 250_000,
                            progress: ProgressCallback | None = None,
                            cancelled: Callable[[], bool] | None = None) -> ColorizationResult:
    """Deterministically reduce independent flight winners onto unchanged geometry."""
    from .runtime import StageTimings
    source = Path(input_path).resolve()
    destination = Path(output_path).resolve()
    shards = [Path(path).resolve() for path in candidate_directories]
    if not shards:
        raise ColorizationError('At least one candidate shard is required')
    if destination.exists():
        raise ColorizationError('Output already exists; choose another file name')
    with laspy.open(source) as reader:
        original_header = copy.deepcopy(reader.header)
    count = int(original_header.point_count)
    header = _output_header(original_header, merged_output=True)
    opened = []
    metadata = []
    expected_files = (('rgb.u16', np.uint16, (count, 3)),
                      ('quality.f32', np.float32, (count,)),
                      ('distance.f32', np.float32, (count,)),
                      ('source.u16', np.uint16, (count,)))
    try:
        fusion_enabled = None
        fusion_slots = fusion_minimum = None
        for shard in shards:
            info = json.loads((shard / 'candidate.json').read_text(encoding='utf-8'))
            if (info.get('format') not in ('elios-color-candidates-v1', 'elios-color-candidates-v2')
                    or info.get('point_count') != count):
                raise ColorizationError(f'Candidate shard does not match merged geometry: {shard}')
            shard_fusion = info.get('format') == 'elios-color-candidates-v2'
            if fusion_enabled is None:
                fusion_enabled = shard_fusion
                fusion_slots = int(info.get('fusion_max_observations', 5))
                fusion_minimum = int(info.get('fusion_min_observations', 3))
            if shard_fusion != fusion_enabled or (shard_fusion and
                    (int(info.get('fusion_max_observations', 0)) != fusion_slots
                     or int(info.get('fusion_min_observations', 0)) != fusion_minimum)):
                raise ColorizationError('Candidate shards use incompatible fusion settings')
            files = list(expected_files)
            if shard_fusion:
                shape = (count, fusion_slots)
                files.extend((('fusion-rgb.u8', np.uint8, (*shape, 3)),
                              ('fusion-weight.f32', np.float32, shape),
                              ('fusion-distance.f32', np.float32, shape),
                              ('fusion-source.u16', np.uint16, shape),
                              ('fusion-group.u32', np.uint32, shape)))
            arrays = tuple(np.memmap(shard / name, mode='r', dtype=dtype, shape=shape)
                           for name, dtype, shape in files)
            opened.append(arrays)
            metadata.append(info)
        destination.parent.mkdir(parents=True, exist_ok=True)
        timings = StageTimings()
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='.elios-reduce-', dir=destination.parent) as scratch:
            temporary = Path(scratch) / 'result.las'
            colored = 0
            fused_report = {'requested': False, 'applied': False}
            merged = None
            if fusion_enabled:
                shape = (count, fusion_slots)
                merged = (
                    np.memmap(Path(scratch) / 'rgb.u16', mode='w+', dtype=np.uint16, shape=(count, 3)),
                    np.memmap(Path(scratch) / 'quality.f32', mode='w+', dtype=np.float32, shape=(count,)),
                    np.memmap(Path(scratch) / 'distance.f32', mode='w+', dtype=np.float32, shape=(count,)),
                    np.memmap(Path(scratch) / 'source.u16', mode='w+', dtype=np.uint16, shape=(count,)),
                    np.memmap(Path(scratch) / 'fusion-rgb.u8', mode='w+', dtype=np.uint8, shape=(*shape, 3)),
                    np.memmap(Path(scratch) / 'fusion-weight.f32', mode='w+', dtype=np.float32, shape=shape),
                    np.memmap(Path(scratch) / 'fusion-distance.f32', mode='w+', dtype=np.float32, shape=shape),
                    np.memmap(Path(scratch) / 'fusion-source.u16', mode='w+', dtype=np.uint16, shape=shape),
                    np.memmap(Path(scratch) / 'fusion-group.u32', mode='w+', dtype=np.uint32, shape=shape),
                )
                out_colors, out_quality, out_distances, out_sources, out_fc, out_fw, out_fd, out_fs, out_fg = merged
                for offset in range(0, count, chunk_size):
                    _check_cancel(cancelled)
                    end = min(count, offset + chunk_size)
                    rows = np.arange(end - offset)
                    for arrays in opened:
                        colors, quality, distances, sources, fc, fw, fd, fs, fg = arrays
                        candidate_quality = np.asarray(quality[offset:end])
                        better = candidate_quality > out_quality[offset:end]
                        if better.any():
                            chosen = np.flatnonzero(better) + offset
                            out_quality[chosen] = candidate_quality[better]
                            out_colors[chosen] = colors[offset:end][better]
                            out_distances[chosen] = distances[offset:end][better]
                            out_sources[chosen] = sources[offset:end][better]
                        for candidate_slot in range(fusion_slots):
                            candidate_weights = np.asarray(fw[offset:end, candidate_slot])
                            valid = candidate_weights > 0
                            if not valid.any():
                                continue
                            weights = out_fw[offset:end]
                            groups = out_fg[offset:end]
                            candidate_groups = np.asarray(fg[offset:end, candidate_slot])
                            same = (groups == candidate_groups[:, None]) & (weights > 0)
                            has_same = np.any(same, axis=1)
                            same_slots = np.argmax(same, axis=1)
                            empty = weights <= 0
                            has_empty = np.any(empty, axis=1)
                            empty_slots = np.argmax(empty, axis=1)
                            weakest = np.argmin(weights, axis=1)
                            slots = np.where(has_same, same_slots, np.where(has_empty, empty_slots, weakest))
                            retained = weights[rows, slots]
                            take = valid & (candidate_weights > retained)
                            if take.any():
                                local = np.flatnonzero(take)
                                absolute = local + offset
                                selected_slots = slots[take]
                                out_fc[absolute, selected_slots] = fc[offset:end, candidate_slot][take]
                                out_fw[absolute, selected_slots] = candidate_weights[take]
                                out_fd[absolute, selected_slots] = fd[offset:end, candidate_slot][take]
                                out_fs[absolute, selected_slots] = fs[offset:end, candidate_slot][take]
                                out_fg[absolute, selected_slots] = candidate_groups[take]
                    _emit(progress, 'reducing_candidates', points_processed=end,
                          point_count=count, colored_point_count=int(np.count_nonzero(out_quality[:end] > 0)))
                fusion_options = ColorizationOptions(
                    chunk_size=chunk_size, multi_frame_fusion=True,
                    fusion_max_observations=fusion_slots,
                    fusion_min_observations=fusion_minimum)
                fused_report = _finalize_fusion(
                    out_colors, out_quality, out_distances, out_sources,
                    out_fc, out_fw, out_fd, out_fs, out_fg, fusion_options)
            with laspy.open(source) as reader, laspy.open(temporary, mode='w', header=header) as writer:
                offset = 0
                for original_points in reader.chunk_iterator(chunk_size):
                    _check_cancel(cancelled)
                    length = len(original_points)
                    best_colors = (np.asarray(merged[0][offset:offset + length]) if merged is not None
                                   else np.zeros((length, 3), dtype=np.uint16))
                    best_quality = (np.asarray(merged[1][offset:offset + length]) if merged is not None
                                    else np.zeros(length, dtype=np.float32))
                    best_sources = (np.asarray(merged[3][offset:offset + length]) if merged is not None
                                    else np.zeros(length, dtype=np.uint16))
                    # Strict greater-than and source order reproduce the existing
                    # chronological winner policy, including deterministic ties.
                    if merged is None:
                        for colors, quality, _distances, sources in opened:
                            candidate_quality = np.asarray(quality[offset:offset + length])
                            better = candidate_quality > best_quality
                            if better.any():
                                best_quality[better] = candidate_quality[better]
                                best_colors[better] = colors[offset:offset + length][better]
                                best_sources[better] = sources[offset:offset + length][better]
                    points = laspy.PackedPointRecord.from_point_record(original_points, header.point_format)
                    points.red, points.green, points.blue = best_colors.T
                    observed = best_quality > 0
                    points['Colorized'] = observed.astype(np.uint8)
                    points['SourceFlight'] = best_sources
                    writer.write_points(points)
                    colored += int(np.count_nonzero(observed))
                    offset += length
                    _emit(progress, 'reducing_candidates', points_processed=offset,
                          point_count=count, colored_point_count=colored)
                if original_header.evlrs:
                    writer.write_evlrs(original_header.evlrs)
            _check_cancel(cancelled)
            os.rename(temporary, destination)
            if merged is not None:
                for array in merged:
                    array._mmap.close()
        timings.add('candidate_reduction', time.perf_counter() - started)
        acceleration = {
            'processing_backend': 'parallel_flight_map_reduce',
            'cuda_acceleration_active': any(bool(item.get('acceleration', {}).get('cuda_acceleration_active'))
                                            for item in metadata),
            'candidate_shards': len(shards),
            'summary': (f'Reduced {len(shards)} independent flight '
                        f"{'fusion reservoirs' if fusion_enabled else 'candidate shards'}"),
        }
        return ColorizationResult(
            destination, count, colored,
            sum(int(item['frames_received']) for item in metadata),
            sum(int(item['frames_used']) for item in metadata),
            sum(int(item['frames_rejected']) for item in metadata),
            multi_frame_fusion=fused_report,
            acceleration=acceleration, performance=timings.to_dict())
    finally:
        for arrays in opened:
            for array in arrays:
                array._mmap.close()
