"""Optional native C++ camera projection used by the CPU fallback."""
from __future__ import annotations

import ctypes
from functools import lru_cache
import os
from pathlib import Path
import sys

import numpy as np


def _candidates():
    module = Path(__file__).resolve()
    override = os.environ.get('ELIOS_COLORIZER_CPU_BACKEND')
    if override:
        yield Path(override)
    yield module.parent / 'native' / 'elios_cpu_projection.dll'
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        yield Path(sys._MEIPASS) / 'elios_colorizer' / 'native' / 'elios_cpu_projection.dll'
    yield module.parents[2] / 'build' / 'cpu' / 'elios_cpu_projection.dll'


@lru_cache(maxsize=1)
def _library():
    path = next((item for item in _candidates() if item.is_file()), None)
    if path is None:
        return None
    try:
        library = ctypes.WinDLL(str(path)) if os.name == 'nt' else ctypes.CDLL(str(path))
        library.elios_cpu_projection_abi.restype = ctypes.c_uint32
        if library.elios_cpu_projection_abi() != 1:
            return None
        double_pointer = ctypes.POINTER(ctypes.c_double)
        library.elios_cpu_project.argtypes = [
            double_pointer, ctypes.c_uint64, double_pointer, double_pointer,
            ctypes.c_int, double_pointer, ctypes.c_int,
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.POINTER(ctypes.c_uint64), double_pointer,
            double_pointer, double_pointer, ctypes.POINTER(ctypes.c_uint64)]
        library.elios_cpu_project.restype = ctypes.c_int
        return library
    except (OSError, AttributeError):
        return None


def available(calibration=None) -> bool:
    return (_library() is not None
            and not (calibration is not None and calibration.distortion_model == 'opencv'
                     and len(calibration.distortion_coefficients) > 12))


def project(xyz, center, rotation, calibration, minimum_depth, maximum_depth, border_fraction):
    library = _library()
    if library is None or (calibration.distortion_model == 'opencv'
                           and len(calibration.distortion_coefficients) > 12):
        return None
    points = np.ascontiguousarray(xyz, dtype=np.float64)
    center = np.ascontiguousarray(center, dtype=np.float64)
    rotation = np.ascontiguousarray(rotation, dtype=np.float64)
    coefficients = np.zeros(12, dtype=np.float64)
    coefficients[:len(calibration.distortion_coefficients)] = calibration.distortion_coefficients
    selected = np.empty(len(points), dtype=np.uint64)
    uv = np.empty((len(points), 2), dtype=np.float64)
    depth = np.empty(len(points), dtype=np.float64)
    camera = np.empty((len(points), 3), dtype=np.float64)
    count = ctypes.c_uint64()
    pointer = ctypes.POINTER(ctypes.c_double)
    status = library.elios_cpu_project(
        points.ctypes.data_as(pointer), len(points), center.ctypes.data_as(pointer),
        rotation.ctypes.data_as(pointer), {'pinhole': 0, 'opencv': 1, 'fisheye': 2}[calibration.distortion_model],
        coefficients.ctypes.data_as(pointer), len(calibration.distortion_coefficients),
        calibration.fx, calibration.fy, calibration.cx, calibration.cy,
        calibration.image_width, calibration.image_height, minimum_depth, maximum_depth,
        border_fraction, selected.ctypes.data_as(ctypes.POINTER(ctypes.c_uint64)),
        uv.ctypes.data_as(pointer), depth.ctypes.data_as(pointer),
        camera.ctypes.data_as(pointer), ctypes.byref(count))
    if status:
        return None
    length = int(count.value)
    return selected[:length].astype(np.intp, copy=False), uv[:length], depth[:length], camera[:length]
