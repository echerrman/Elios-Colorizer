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
from itertools import islice
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Iterable, Iterator, Any

import cv2
import laspy
import numpy as np

from .camera import Calibration


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
    maximum_luminance: float = 252.0
    minimum_sharpness: float = 2.0
    reject_exposure_extremes: bool = True
    overwrite: bool = False
    experimental_calibration: bool = False
    maximum_color_distance_m: float | None = None
    cache_xyz: bool | None = None
    worker_threads: int = 1

    def __post_init__(self) -> None:
        if self.chunk_size < 1 or self.frame_batch_size < 1 or self.depth_buffer_width < 8 or self.worker_threads < 1:
            raise ValueError("Chunk size, frame batch size, and depth-buffer width must be positive")
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


ProgressCallback = Callable[[dict[str, Any]], None]


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


def _project(xyz: np.ndarray, frame: _PreparedFrame, calibration: Calibration,
             options: ColorizationOptions, *, border: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
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
    # Keep at least 2 GiB and 65% of currently available physical memory free
    # for 4K frames, projection temporaries, the UI, and other applications.
    return available is not None and needed <= 4 * 1024**3 and needed <= max(0, available - 2 * 1024**3) * .35


def _xyz_chunks(path: Path, chunk_size: int, active_offsets: set[int] | None = None,
                xyz_cache: np.ndarray | None = None) -> Iterator[tuple[int, np.ndarray]]:
    if xyz_cache is not None:
        for offset in range(0, len(xyz_cache), chunk_size):
            if active_offsets is None or offset in active_offsets:
                yield offset, xyz_cache[offset:offset + chunk_size]
        return
    with laspy.open(path) as reader:
        offset = 0
        while offset < reader.header.point_count:
            length = min(chunk_size, reader.header.point_count - offset)
            if active_offsets is not None and offset not in active_offsets:
                if offset + length < reader.header.point_count:
                    reader.seek(offset + length)
            else:
                points = reader.read_points(length)
                if len(points) != length:
                    raise ColorizationError("Source LAS is truncated or changed while reading")
                xyz = np.column_stack((points.x, points.y, points.z))
                yield offset, xyz
            offset += length


def _chunk_may_be_visible(bounds: np.ndarray, frame: _PreparedFrame,
                           calibration: Calibration, options: ColorizationOptions) -> bool:
    """Conservative 8-corner AABB/frustum rejection, never a visibility decision."""
    corners = bounds[np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)]),
                     np.array([0, 1, 2])]
    camera = (corners - frame.center) @ frame.camera_to_world
    x, y, z = camera.T
    if z.max() < options.minimum_depth_m or z.min() > options.maximum_depth_m:
        return False
    # Distorted lenses may see beyond a pinhole frustum, so only reject their depth.
    if calibration.distortion_model == "pinhole":
        horizontal = calibration.fx * x + calibration.cx * z
        vertical = calibration.fy * y + calibration.cy * z
        if (np.all(horizontal < 0) or np.all(horizontal > calibration.image_width * z)
                or np.all(vertical < 0) or np.all(vertical > calibration.image_height * z)):
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


def _assign_chunk_colors(offset: int, xyz: np.ndarray, batch: list[_PreparedFrame],
                         colors: np.ndarray, quality: np.ndarray, distances: np.ndarray,
                         calibration: Calibration,
                         options: ColorizationOptions) -> tuple[int, int]:
    """Color one disjoint LAS slice while preserving chronological frame order."""
    for frame in batch:
        selected, uv, z, camera = _project(xyz, frame, calibration, options, border=True)
        x, y = _pixel_indices(uv, frame)
        near = frame.depth[y, x]
        visible = z <= near + options.occlusion_absolute_tolerance_m + near * options.occlusion_relative_tolerance
        if not visible.any():
            continue
        selected, uv, z, camera = selected[visible], uv[visible], z[visible], camera[visible]
        rgb = _sample_rgb(frame.observation.rgb, uv)
        luminance = rgb @ np.array([0.2126, 0.7152, 0.0722])
        usable = np.ones(len(rgb), dtype=bool)
        if options.reject_exposure_extremes:
            usable &= (luminance >= options.minimum_luminance) & (luminance <= options.maximum_luminance)
        point_distance = np.linalg.norm(camera, axis=1)
        if options.maximum_color_distance_m is not None:
            usable &= point_distance <= options.maximum_color_distance_m
        cosine = z / point_distance
        border_distance = np.minimum.reduce((uv[:, 0] / calibration.image_width,
            uv[:, 1] / calibration.image_height,
            1 - uv[:, 0] / calibration.image_width, 1 - uv[:, 1] / calibration.image_height))
        edge_weight = np.clip(border_distance / 0.15, 0.05, 1)
        exposure_weight = 0.5 + 0.5 * (1 - np.abs(luminance - 127.5) / 127.5)
        score = (frame.sharpness_weight * cosine**2 * edge_weight * exposure_weight
                 / (1 + (z / 5)**2)).astype(np.float32)
        indices = selected + offset
        better = usable & (score > quality[indices])
        chosen = indices[better]
        colors[chosen] = rgb[better].astype(np.uint16) * 257
        quality[chosen] = score[better]
        distances[chosen] = point_distance[better].astype(np.float32)
    return offset, len(xyz)


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


def _output_header(source: laspy.LasHeader) -> laspy.LasHeader:
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
        ("ColorConfidence", "RGB observation confidence"),
        ("ColorDistance", "Winning camera distance (m)"),
    ):
        if name in header.point_format.dimension_names:
            if header.point_format.dimension_by_name(name).dtype != np.dtype("float32"):
                raise ColorizationError(f"Existing {name} dimension must be float32")
        else:
            header.add_extra_dim(laspy.ExtraBytesParams(name, "float32", description=description))
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
    source, destination = Path(input_path).resolve(), Path(output_path).resolve()
    if source == destination:
        raise ColorizationError("Input and output must be different files")
    if destination.suffix.lower() != ".las":
        raise ColorizationError("Output must use .las; this preserves geometry without compression dependencies")
    if not isinstance(calibration, Calibration) or (not calibration.validated and not options.experimental_calibration):
        raise ColorizationError("A validated RGB camera calibration is required")
    if destination.exists() and not options.overwrite:
        raise ColorizationError("Output already exists; choose another file name")
    _check_cancel(cancelled)
    with laspy.open(source) as reader:
        original_header = copy.deepcopy(reader.header)
    count = original_header.point_count
    if count == 0:
        raise ColorizationError("Source LAS contains no points")
    header = _output_header(original_header)
    destination.parent.mkdir(parents=True, exist_ok=True)
    use_xyz_cache = _use_xyz_cache(count, options)
    required_bytes = count * (header.point_format.size + 14) + 64 * 1024**2
    if shutil.disk_usage(destination.parent).free < required_bytes:
        raise ColorizationError(f"Insufficient output disk space; need about {required_bytes / 1024**3:.2f} GiB")
    received, used, rejected = 0, 0, 0
    with tempfile.TemporaryDirectory(prefix=".elios-colorize-", dir=destination.parent) as scratch:
        work = Path(scratch)
        colors = np.memmap(work / "rgb.u16", mode="w+", dtype=np.uint16, shape=(count, 3))
        quality = np.memmap(work / "quality.f32", mode="w+", dtype=np.float32, shape=(count,))
        distances = np.memmap(work / "distance.f32", mode="w+", dtype=np.float32, shape=(count,))
        # Freshly created mapped files are zero initialized by the operating system.
        try:
            # Chunk bounds are tiny (about 4 KB per 20M points) and let later passes
            # seek past distant/behind-camera chunks without decoding their XYZ.
            chunk_bounds: dict[int, np.ndarray] = {}
            xyz_cache = np.empty((count, 3), dtype=np.float64) if use_xyz_cache else None
            for offset, xyz in _xyz_chunks(source, options.chunk_size):
                _check_cancel(cancelled)
                if not np.isfinite(xyz).all():
                    raise ColorizationError("Source LAS contains nonfinite coordinates")
                chunk_bounds[offset] = np.array([xyz.min(axis=0), xyz.max(axis=0)])
                if xyz_cache is not None:
                    xyz_cache[offset:offset + len(xyz)] = xyz
                _emit(progress, "indexing", points_processed=offset + len(xyz), point_count=count,
                      xyz_cache_used=use_xyz_cache, worker_threads=options.worker_threads)
            stream = iter(frames)
            batch_index = 0
            pool_context = (ThreadPoolExecutor(max_workers=options.worker_threads,
                                               thread_name_prefix='elios-project')
                            if options.worker_threads > 1 else nullcontext(None))
            with pool_context as executor:
              while True:
                _check_cancel(cancelled)
                batch: list[_PreparedFrame] = []
                # islice bounds decoded-frame memory even when every frame is rejected.
                observations = list(islice(stream, options.frame_batch_size))
                if not observations:
                    break
                batch_index += 1
                for observation in observations:
                    received += 1
                    prepared = _prepare(observation, calibration, options)
                    if prepared is None:
                        rejected += 1
                    else:
                        batch.append(prepared)
                if not batch:
                    _emit(progress, "frames_rejected", frames_received=received, frames_rejected=rejected)
                    continue
                used += len(batch)
                active_offsets = {offset for offset, bounds in chunk_bounds.items()
                                  if any(_chunk_may_be_visible(bounds, frame, calibration, options) for frame in batch)}
                for offset, xyz in _xyz_chunks(source, options.chunk_size, active_offsets, xyz_cache):
                    _check_cancel(cancelled)
                    if executor is None or len(batch) == 1:
                        for frame in batch:
                            _update_visibility(xyz, frame, calibration, options)
                    else:
                        list(executor.map(lambda frame: _update_visibility(xyz, frame, calibration, options), batch))
                    _emit(progress, "visibility", batch=batch_index, points_processed=offset + len(xyz),
                          point_count=count, frames_received=received, frames_used=used)
                if options.occlusion_radius_pixels:
                    size = 2 * options.occlusion_radius_pixels + 1
                    for frame in batch:
                        frame.depth = cv2.erode(frame.depth, np.ones((size, size), np.uint8),
                                                borderType=cv2.BORDER_CONSTANT, borderValue=float("inf"))
                chunks = _xyz_chunks(source, options.chunk_size, active_offsets, xyz_cache)
                if executor is None:
                    completed_chunks = (_assign_chunk_colors(offset, xyz, batch, colors, quality, distances,
                                                             calibration, options) for offset, xyz in chunks)
                else:
                    completed_chunks = _bounded_parallel_map(
                        executor,
                        lambda item: _assign_chunk_colors(item[0], item[1], batch, colors, quality, distances,
                                                          calibration, options),
                        chunks, options.worker_threads)
                for offset, length in completed_chunks:
                    _check_cancel(cancelled)
                    _emit(progress, "colorizing", batch=batch_index, points_processed=offset + length,
                          point_count=count, frames_received=received, frames_used=used)
                # Drop the full-resolution RGB arrays before decoding the next batch.
                del batch, observations, prepared, observation
            if used == 0:
                raise ColorizationError("No usable video observations; check frame timing, image quality and calibration")
            _check_cancel(cancelled)
            temporary_output = work / "result.las"
            colored = 0
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
                    points["ColorConfidence"] = quality[offset:offset + length]
                    points["ColorDistance"] = distances[offset:offset + length]
                    colored += int(observed.sum())
                    writer.write_points(points)
                    offset += length
                    _emit(progress, "writing", points_processed=offset, point_count=count,
                          colored_point_count=colored, frames_used=used)
                if original_header.evlrs:
                    writer.write_evlrs(original_header.evlrs)
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
            result = ColorizationResult(destination, count, colored, received, used, rejected,
                                         experimental_calibration=not calibration.validated,
                                         xyz_cache_used=use_xyz_cache)
            _emit(progress, "complete", **result.to_dict())
            return result
        finally:
            # Windows cannot remove an open memory-mapped file.
            colors._mmap.close()
            quality._mmap.close()
            distances._mmap.close()
