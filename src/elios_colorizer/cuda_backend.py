"""NVIDIA CUDA availability and packaged-kernel checkpoint.

The v1.2 checkpoint deliberately keeps colorization on the v1.1 CPU reference
path. A verified native CUDA probe establishes the runtime, packaging, transfer,
kernel-launch, and fallback boundary needed by the future projection backend.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np


ABI_VERSION = 4


class _NativeProbe(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('abi_version', ctypes.c_uint32),
        ('status', ctypes.c_int32),
        ('device_count', ctypes.c_int32),
        ('selected_device', ctypes.c_int32),
        ('compute_major', ctypes.c_int32),
        ('compute_minor', ctypes.c_int32),
        ('multiprocessor_count', ctypes.c_int32),
        ('driver_version', ctypes.c_int32),
        ('runtime_version', ctypes.c_int32),
        ('total_memory_bytes', ctypes.c_uint64),
        ('free_memory_bytes', ctypes.c_uint64),
        ('self_test_max_error', ctypes.c_float),
        ('device_name', ctypes.c_char * 256),
        ('error', ctypes.c_char * 512),
    ]


@dataclass(frozen=True)
class CudaCheckpoint:
    checkpoint_version: int
    available: bool
    self_test_passed: bool
    processing_backend: str
    cuda_acceleration_active: bool
    device_count: int = 0
    selected_device: int | None = None
    device_name: str | None = None
    compute_capability: str | None = None
    multiprocessor_count: int | None = None
    driver_version: str | None = None
    runtime_version: str | None = None
    total_memory_bytes: int | None = None
    free_memory_bytes: int | None = None
    self_test_max_error: float | None = None
    library_path: str | None = None
    fallback_reason: str | None = None
    summary: str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _version(value: int) -> str | None:
    if value <= 0:
        return None
    return f'{value // 1000}.{(value % 1000) // 10}'


def _decode(value: bytes) -> str:
    return value.split(b'\0', 1)[0].decode('utf-8', errors='replace')


def _disabled(value: str | None) -> bool:
    return bool(value and value.strip().lower() not in ('0', 'false', 'no', 'off'))


def _candidate_libraries(override: str | None = None):
    if override:
        yield Path(override).expanduser().resolve()
        return
    module = Path(__file__).resolve()
    yield module.parent / 'native' / 'elios_cuda_probe.dll'
    if getattr(sys, '_MEIPASS', None):
        yield Path(sys._MEIPASS) / 'elios_colorizer' / 'native' / 'elios_cuda_probe.dll'
    yield Path(sys.executable).resolve().parent / '_internal' / 'elios_colorizer' / 'native' / 'elios_cuda_probe.dll'
    if len(module.parents) >= 3:
        yield module.parents[2] / 'build' / 'cuda' / 'elios_cuda_probe.dll'


def _fallback(reason: str, library_path: Path | None = None) -> CudaCheckpoint:
    return CudaCheckpoint(
        checkpoint_version=ABI_VERSION, available=False, self_test_passed=False,
        processing_backend='cpu_reference', cuda_acceleration_active=False,
        library_path=str(library_path) if library_path else None,
        fallback_reason=reason, summary=f'CPU fallback: {reason}')


@lru_cache(maxsize=8)
def _probe_cached(disable_value: str | None, override: str | None) -> CudaCheckpoint:
    if _disabled(disable_value):
        return _fallback('NVIDIA CUDA was disabled by ELIOS_COLORIZER_DISABLE_CUDA')
    library_path = next((path for path in _candidate_libraries(override) if path.is_file()), None)
    if library_path is None:
        return _fallback('CUDA checkpoint library is not installed in this build')
    try:
        library = ctypes.WinDLL(str(library_path)) if os.name == 'nt' else ctypes.CDLL(str(library_path))
        function = library.elios_cuda_probe
        function.argtypes = [ctypes.POINTER(_NativeProbe), ctypes.c_uint32]
        function.restype = ctypes.c_int
        native = _NativeProbe()
        returned = int(function(ctypes.byref(native), ctypes.sizeof(native)))
    except (OSError, AttributeError) as exc:
        return _fallback(f'CUDA checkpoint library could not be loaded: {exc}', library_path)
    error = _decode(bytes(native.error))
    if native.abi_version != ABI_VERSION:
        return _fallback(
            f'CUDA checkpoint ABI {native.abi_version} does not match application ABI {ABI_VERSION}',
            library_path)
    if returned != 0 or native.status != 0:
        return _fallback(error or f'CUDA checkpoint failed with status {returned or native.status}', library_path)
    name = _decode(bytes(native.device_name))
    summary = (f'CUDA projection ready on {name} · compute {native.compute_major}.{native.compute_minor} · '
               f'{native.total_memory_bytes / 1024**3:.1f} GiB VRAM')
    return CudaCheckpoint(
        checkpoint_version=ABI_VERSION, available=True, self_test_passed=True,
        processing_backend='cuda_projection', cuda_acceleration_active=True,
        device_count=native.device_count, selected_device=native.selected_device,
        device_name=name, compute_capability=f'{native.compute_major}.{native.compute_minor}',
        multiprocessor_count=native.multiprocessor_count,
        driver_version=_version(native.driver_version), runtime_version=_version(native.runtime_version),
        total_memory_bytes=native.total_memory_bytes, free_memory_bytes=native.free_memory_bytes,
        self_test_max_error=float(native.self_test_max_error), library_path=str(library_path),
        fallback_reason=None, summary=summary)


class CudaBackendError(RuntimeError):
    """A recoverable native CUDA failure; callers should continue on CPU."""


class _ProjectionConfig(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('abi_version', ctypes.c_uint32),
        ('selected_device', ctypes.c_int32),
        ('image_width', ctypes.c_int32), ('image_height', ctypes.c_int32),
        ('depth_width', ctypes.c_int32), ('depth_height', ctypes.c_int32),
        ('distortion_model', ctypes.c_int32), ('coefficient_count', ctypes.c_int32),
        ('fx', ctypes.c_double), ('fy', ctypes.c_double),
        ('cx', ctypes.c_double), ('cy', ctypes.c_double),
        ('coefficients', ctypes.c_double * 12),
        ('minimum_depth', ctypes.c_double), ('maximum_depth', ctypes.c_double),
        ('border_fraction', ctypes.c_double),
        ('minimum_luminance', ctypes.c_double), ('maximum_luminance', ctypes.c_double),
        ('maximum_distance', ctypes.c_double),
        ('occlusion_absolute', ctypes.c_double), ('occlusion_relative', ctypes.c_double),
        ('reject_exposure_extremes', ctypes.c_int32),
        ('illumination_balancing', ctypes.c_int32),
    ]


class _ProjectionFrame(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('center', ctypes.c_double * 3),
        ('rotation', ctypes.c_double * 9),
        ('sharpness_weight', ctypes.c_double),
        ('source_flight', ctypes.c_uint16),
    ]


class _IlluminationInput(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('enabled', ctypes.c_int32),
        ('radial', ctypes.c_double), ('exposure', ctypes.c_double),
        ('confidence', ctypes.c_double), ('radius_limit', ctypes.c_double),
    ]


_ERROR_POINTER = ctypes.POINTER(ctypes.c_char)


def _bind_projection_api(library: Any) -> None:
    byte_pointer = ctypes.POINTER(ctypes.c_uint8)
    library.elios_cuda_batch_create.argtypes = [
        ctypes.POINTER(_ProjectionConfig), ctypes.POINTER(_ProjectionFrame),
        ctypes.POINTER(byte_pointer), ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
        _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_batch_create.restype = ctypes.c_int
    library.elios_cuda_visibility.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.c_uint64,
        _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_visibility.restype = ctypes.c_int
    library.elios_cuda_finish_visibility.argtypes = [
        ctypes.c_void_p, ctypes.c_int, _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_finish_visibility.restype = ctypes.c_int
    library.elios_cuda_get_depth.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float),
        _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_get_depth.restype = ctypes.c_int
    library.elios_cuda_set_illumination.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_IlluminationInput), ctypes.c_int,
        _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_set_illumination.restype = ctypes.c_int
    library.elios_cuda_assign.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_assign.restype = ctypes.c_int
    library.elios_cuda_fusion_assign.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_uint16), ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64),
        _ERROR_POINTER, ctypes.c_uint32]
    library.elios_cuda_fusion_assign.restype = ctypes.c_int
    library.elios_cuda_batch_destroy.argtypes = [ctypes.c_void_p]
    library.elios_cuda_batch_destroy.restype = None


def _native_call(function: Any, *arguments: Any) -> None:
    error = ctypes.create_string_buffer(1024)
    status = int(function(*arguments, error, len(error)))
    if status:
        detail = error.value.decode('utf-8', errors='replace')
        raise CudaBackendError(detail or f'CUDA backend failed with status {status}')


class CudaProjectionBackend:
    """Creates bounded native batches while retaining the Python CPU fallback."""

    def __init__(self, library_path: str | Path):
        self.library_path = Path(library_path)
        try:
            self.library = (ctypes.WinDLL(str(self.library_path)) if os.name == 'nt'
                            else ctypes.CDLL(str(self.library_path)))
            _bind_projection_api(self.library)
        except (OSError, AttributeError) as exc:
            raise CudaBackendError(f'CUDA projection library could not be loaded: {exc}') from exc

    def begin_batch(self, frames: list[Any], calibration: Any, options: Any) -> 'CudaProjectionBatch':
        return CudaProjectionBatch(self.library, frames, calibration, options)


class CudaProjectionBatch:
    def __init__(self, library: Any, frames: list[Any], calibration: Any, options: Any):
        self.library = library
        self.frames = frames
        self.context = ctypes.c_void_p()
        if not frames:
            raise CudaBackendError('CUDA projection batch is empty')
        model = {'pinhole': 0, 'opencv': 1, 'fisheye': 2}[calibration.distortion_model]
        coefficients = list(calibration.distortion_coefficients)
        padded = coefficients + [0.] * (12 - len(coefficients))
        depth_height, depth_width = frames[0].depth.shape
        config = _ProjectionConfig(
            ABI_VERSION, options.cuda_device_index, calibration.image_width, calibration.image_height,
            depth_width, depth_height, model, len(coefficients),
            calibration.fx, calibration.fy, calibration.cx, calibration.cy,
            (ctypes.c_double * 12)(*padded),
            options.minimum_depth_m, options.maximum_depth_m,
            options.image_border_fraction, options.minimum_luminance,
            options.maximum_luminance,
            options.maximum_color_distance_m if options.maximum_color_distance_m is not None else -1.,
            options.occlusion_absolute_tolerance_m, options.occlusion_relative_tolerance,
            int(options.reject_exposure_extremes), int(options.illumination_balancing))
        native_frames = (_ProjectionFrame * len(frames))()
        images = []
        image_pointers = (ctypes.POINTER(ctypes.c_uint8) * len(frames))()
        for index, frame in enumerate(frames):
            image = np.ascontiguousarray(frame.observation.rgb, dtype=np.uint8)
            images.append(image)
            image_pointers[index] = image.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
            native_frames[index].center = (ctypes.c_double * 3)(*np.asarray(frame.center, dtype=np.float64))
            native_frames[index].rotation = (ctypes.c_double * 9)(
                *np.asarray(frame.camera_to_world, dtype=np.float64).ravel())
            native_frames[index].sharpness_weight = frame.sharpness_weight
            native_frames[index].source_flight = frame.observation.source_flight
        _native_call(self.library.elios_cuda_batch_create, ctypes.byref(config), native_frames,
                     image_pointers, len(frames), ctypes.byref(self.context))

    @staticmethod
    def _xyz(xyz: np.ndarray) -> np.ndarray:
        return np.ascontiguousarray(xyz, dtype=np.float64)

    def visibility(self, xyz: np.ndarray) -> None:
        points = self._xyz(xyz)
        _native_call(self.library.elios_cuda_visibility, self.context,
                     points.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(points))

    def finish_visibility(self, radius: int) -> None:
        _native_call(self.library.elios_cuda_finish_visibility, self.context, radius)
        for index, frame in enumerate(self.frames):
            if not frame.depth.flags.c_contiguous or frame.depth.dtype != np.float32:
                frame.depth = np.ascontiguousarray(frame.depth, dtype=np.float32)
            _native_call(self.library.elios_cuda_get_depth, self.context, index,
                         frame.depth.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))

    def set_illumination(self) -> None:
        inputs = (_IlluminationInput * len(self.frames))()
        for index, frame in enumerate(self.frames):
            model = frame.illumination_model
            if model is not None:
                inputs[index] = _IlluminationInput(
                    1, model.radial, model.exposures[frame.illumination_frame],
                    model.confidence, model.radius_limit)
        _native_call(self.library.elios_cuda_set_illumination, self.context,
                     inputs, len(self.frames))

    def assign(self, xyz: np.ndarray, colors: np.ndarray, quality: np.ndarray,
               distances: np.ndarray, sources: np.ndarray) -> tuple[int, int]:
        points = self._xyz(xyz)
        arrays = (colors, quality, distances, sources)
        if not all(array.flags.c_contiguous for array in arrays):
            raise CudaBackendError('CUDA output slices must be contiguous')
        corrected = ctypes.c_uint64()
        rejected = ctypes.c_uint64()
        _native_call(
            self.library.elios_cuda_assign, self.context,
            points.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(points),
            colors.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
            quality.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            distances.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            sources.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
            ctypes.byref(corrected), ctypes.byref(rejected))
        return int(corrected.value), int(rejected.value)

    def assign_fusion(self, xyz: np.ndarray, colors: np.ndarray, quality: np.ndarray,
                      distances: np.ndarray, sources: np.ndarray,
                      fusion_colors: np.ndarray, fusion_weights: np.ndarray,
                      fusion_distances: np.ndarray,
                      fusion_sources: np.ndarray) -> tuple[int, int]:
        points = self._xyz(xyz)
        arrays = (colors, quality, distances, sources, fusion_colors,
                  fusion_weights, fusion_distances, fusion_sources)
        if not all(array.flags.c_contiguous for array in arrays):
            raise CudaBackendError('CUDA fusion output slices must be contiguous')
        slots = fusion_weights.shape[1]
        if fusion_colors.shape != (len(points), slots, 3):
            raise CudaBackendError('CUDA fusion candidate arrays have incompatible shapes')
        corrected = ctypes.c_uint64()
        rejected = ctypes.c_uint64()
        _native_call(
            self.library.elios_cuda_fusion_assign, self.context,
            points.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(points),
            colors.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
            quality.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            distances.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            sources.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)),
            fusion_colors.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            fusion_weights.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            fusion_distances.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            fusion_sources.ctypes.data_as(ctypes.POINTER(ctypes.c_uint16)), slots,
            ctypes.byref(corrected), ctypes.byref(rejected))
        return int(corrected.value), int(rejected.value)

    def close(self) -> None:
        if self.context:
            self.library.elios_cuda_batch_destroy(self.context)
            self.context = ctypes.c_void_p()

    def __enter__(self) -> 'CudaProjectionBatch':
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def cuda_projection_backend(calibration: Any) -> tuple[CudaProjectionBackend | None, str | None]:
    checkpoint = cuda_checkpoint()
    if not checkpoint.available or not checkpoint.self_test_passed:
        return None, checkpoint.fallback_reason or 'CUDA checkpoint did not pass'
    if calibration.distortion_model == 'opencv' and len(calibration.distortion_coefficients) > 12:
        return None, 'CUDA projection does not support 14-coefficient tilted-sensor calibration'
    try:
        return CudaProjectionBackend(checkpoint.library_path), None
    except CudaBackendError as exc:
        return None, str(exc)


def cuda_checkpoint() -> CudaCheckpoint:
    """Run once per process for the current override/disable configuration."""
    return _probe_cached(os.environ.get('ELIOS_COLORIZER_DISABLE_CUDA'),
                         os.environ.get('ELIOS_COLORIZER_CUDA_PROBE'))


def refresh_cuda_checkpoint() -> CudaCheckpoint:
    _probe_cached.cache_clear()
    return cuda_checkpoint()


def write_cuda_checkpoint(directory: str | Path) -> Path:
    """Write a standalone diagnostic that works in the windowed portable app."""
    from datetime import datetime, timezone
    from . import __version__
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    payload = {
        'application_version': __version__,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'cuda': cuda_checkpoint().to_dict(),
        'status': 'passed',
    }
    destination = directory / 'cuda-checkpoint.json'
    destination.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return destination
