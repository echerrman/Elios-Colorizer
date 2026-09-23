"""Conservative alignment and confidence-aware fusion of colorized LAS files.

Inspector coordinates are treated as authoritative.  A small rigid correction is
only accepted when it improves independent validation points and remains within
tight translation/rotation limits.  Fusion streams colored observations through
a disk-backed voxel index so large flights do not have to fit in memory.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Iterable

import laspy
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation


class FusionError(RuntimeError):
    pass


class FusionCancelled(FusionError):
    pass


@dataclass(frozen=True)
class AlignmentResult:
    source_path: Path
    transform: np.ndarray
    applied: bool
    initial_median_m: float
    final_median_m: float
    initial_overlap: float
    final_overlap: float
    translation_m: float
    rotation_degrees: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_path"] = str(self.source_path)
        result["transform"] = self.transform.tolist()
        return result


@dataclass(frozen=True)
class FusionResult:
    output_path: Path
    point_count: int
    input_colored_points: int
    voxel_size_m: float
    alignment: tuple[AlignmentResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "point_count": self.point_count,
            "input_colored_points": self.input_colored_points,
            "voxel_size_m": self.voxel_size_m,
            "alignment": [item.to_dict() for item in self.alignment],
        }


ProgressCallback = Callable[[str, float, str], None]


def _check_cancel(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise FusionCancelled("Multi-flight processing cancelled; no merged output was published.")


def _transform(xyz: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return xyz @ matrix[:3, :3].T + matrix[:3, 3]


def _sample_xyz(path: Path, maximum: int = 120_000) -> np.ndarray:
    with laspy.open(path) as reader:
        count = int(reader.header.point_count)
        if not count:
            raise FusionError(f"Point cloud is empty: {path}")
        stride = max(1, math.ceil(count / maximum))
        samples: list[np.ndarray] = []
        offset = 0
        for points in reader.chunk_iterator(500_000):
            indices = np.arange(offset, offset + len(points))
            selected = (indices % stride) == 0
            if selected.any():
                xyz = np.column_stack((points.x[selected], points.y[selected], points.z[selected]))
                samples.append(xyz)
            offset += len(points)
    result = np.concatenate(samples, axis=0)[:maximum]
    return result[np.isfinite(result).all(axis=1)]


def _spacing(xyz: np.ndarray) -> float:
    if len(xyz) < 3:
        return .02
    sample = xyz[::max(1, len(xyz) // 30_000)]
    distances, _ = cKDTree(sample).query(sample, k=2, workers=-1)
    finite = distances[:, 1][np.isfinite(distances[:, 1]) & (distances[:, 1] > 1e-6)]
    return float(np.median(finite)) if len(finite) else .02


def _rigid_fit(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source_center, target_center = source.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((source - source_center).T @ (target - target_center))
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T @ u.T
    result = np.eye(4)
    result[:3, :3] = rotation
    result[:3, 3] = target_center - rotation @ source_center
    return result


def _metrics(reference_tree: cKDTree, xyz: np.ndarray, gate: float) -> tuple[float, float, float]:
    distances, _ = reference_tree.query(xyz, k=1, workers=-1)
    finite = distances[np.isfinite(distances)]
    if not len(finite):
        return math.inf, math.inf, 0.0
    return float(np.median(finite)), float(np.quantile(finite, .9)), float(np.mean(finite <= gate))


def _identity_alignment(path: Path, note: str = "Reference cloud; original Inspector coordinates retained.") -> AlignmentResult:
    return AlignmentResult(path, np.eye(4), False, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, note)


def align_clouds(paths: Iterable[str | Path], *,
                 progress: ProgressCallback | None = None,
                 cancelled: Callable[[], bool] | None = None) -> tuple[AlignmentResult, ...]:
    """Return accepted transforms; unsafe or unhelpful candidates become identity."""
    sources = tuple(Path(path).resolve() for path in paths)
    if not sources:
        raise FusionError("At least one colorized cloud is required.")
    reference = _sample_xyz(sources[0])
    spacing = _spacing(reference)
    gate = max(.08, min(.30, spacing * 8))
    reference_tree = cKDTree(reference)
    results = [_identity_alignment(sources[0])]
    for source_index, path in enumerate(sources[1:], 1):
        _check_cancel(cancelled)
        moving = _sample_xyz(path)
        # Even points drive ICP; odd points independently decide whether it is safe.
        training, validation = moving[::2], moving[1::2]
        if len(validation) < 50:
            validation = moving
        initial_median, initial_q90, initial_overlap = _metrics(reference_tree, validation, gate)
        transform = np.eye(4)
        candidate_xyz = training.copy()
        if initial_overlap >= .20:
            for _ in range(12):
                _check_cancel(cancelled)
                distances, indices = reference_tree.query(candidate_xyz, k=1, workers=-1)
                usable = np.isfinite(distances) & (distances <= gate)
                if int(usable.sum()) < 30:
                    break
                limit = float(np.quantile(distances[usable], .80))
                usable &= distances <= limit
                update = _rigid_fit(candidate_xyz[usable], reference[indices[usable]])
                transform = update @ transform
                candidate_xyz = _transform(training, transform)
                if np.linalg.norm(update[:3, 3]) < 1e-5 and Rotation.from_matrix(update[:3, :3]).magnitude() < 1e-5:
                    break
        corrected = _transform(validation, transform)
        final_median, final_q90, final_overlap = _metrics(reference_tree, corrected, gate)
        translation = float(np.linalg.norm(transform[:3, 3]))
        angle = float(np.degrees(Rotation.from_matrix(transform[:3, :3]).magnitude()))
        correction_needed = initial_median > max(.012, spacing * .75)
        improvement = (final_median <= initial_median * .88
                       and final_q90 <= initial_q90 * .97
                       and final_overlap >= initial_overlap - .005)
        bounded = translation <= min(.50, max(.12, spacing * 20)) and angle <= 5.0
        applied = bool(correction_needed and initial_overlap >= .20 and improvement and bounded)
        if applied:
            note = "Small rigid correction accepted after improving held-out overlap metrics."
            accepted = transform
        else:
            accepted = np.eye(4)
            final_median, final_overlap = initial_median, initial_overlap
            reasons = []
            if initial_overlap < .20:
                reasons.append("insufficient reliable overlap")
            if not improvement:
                reasons.append("candidate did not improve held-out metrics")
            if not bounded:
                reasons.append("candidate exceeded safe correction bounds")
            if not correction_needed:
                reasons.append("existing alignment was already within tolerance")
            note = "Original Inspector coordinates retained: " + ", ".join(reasons) + "."
        results.append(AlignmentResult(path, accepted, applied, initial_median, final_median,
                                       initial_overlap, final_overlap, translation if applied else 0.0,
                                       angle if applied else 0.0, note))
        if progress:
            progress("Checking alignment", source_index / max(len(sources) - 1, 1),
                     f"Flight {source_index + 1}: {note}")
    return tuple(results)


_RECORD_DTYPE = np.dtype([
    ("ix", "<i4"), ("iy", "<i4"), ("iz", "<i4"), ("negscore", "<f4"),
    ("x", "<f8"), ("y", "<f8"), ("z", "<f8"), ("rgb", "<u2", (3,)),
    ("confidence", "<f4"), ("distance", "<f4"), ("source", "<u2"),
])


def _colored_mask(points: laspy.ScaleAwarePointRecord) -> np.ndarray:
    names = points.point_format.dimension_names
    if "Colorized" in names:
        return np.asarray(points["Colorized"], dtype=np.uint8) == 1
    return ((np.asarray(points.red) != 0) | (np.asarray(points.green) != 0)
            | (np.asarray(points.blue) != 0))


def _confidence(points: laspy.ScaleAwarePointRecord, selected: np.ndarray) -> np.ndarray:
    if "ColorConfidence" in points.point_format.dimension_names:
        return np.maximum(np.asarray(points["ColorConfidence"])[selected], 1e-8).astype(np.float32)
    return np.ones(int(selected.sum()), dtype=np.float32)


def _distance(points: laspy.ScaleAwarePointRecord, selected: np.ndarray) -> np.ndarray:
    if "ColorDistance" in points.point_format.dimension_names:
        return np.asarray(points["ColorDistance"])[selected].astype(np.float32)
    return np.zeros(int(selected.sum()), dtype=np.float32)


def _output_header(minimum: np.ndarray) -> laspy.LasHeader:
    header = laspy.LasHeader(point_format=7, version="1.4")
    header.scales = np.array([.001, .001, .001])
    header.offsets = np.floor(minimum * 1000) / 1000
    header.add_extra_dim(laspy.ExtraBytesParams("Colorized", "uint8", description="1=RGB observed"))
    header.add_extra_dim(laspy.ExtraBytesParams("ColorConfidence", "float32", description="Selected RGB confidence"))
    header.add_extra_dim(laspy.ExtraBytesParams("ColorDistance", "float32", description="Winning camera distance (m)"))
    header.add_extra_dim(laspy.ExtraBytesParams("SourceFlight", "uint16", description="One-based source flight index"))
    return header


def _write_groups(writer: laspy.LasWriter, records: np.ndarray, header: laspy.LasHeader) -> int:
    if not len(records):
        return 0
    changes = ((records[1:]["ix"] != records[:-1]["ix"])
               | (records[1:]["iy"] != records[:-1]["iy"])
               | (records[1:]["iz"] != records[:-1]["iz"]))
    starts = np.r_[0, np.flatnonzero(changes) + 1]
    ends = np.r_[starts[1:], len(records)]
    best = records[starts]
    # Records are sorted by confidence within each voxel. Keeping the single
    # best observation is both scalable and resistant to blurred seam colors.
    rgb = best["rgb"]
    points = laspy.ScaleAwarePointRecord.zeros(len(best), header=header)
    points.x, points.y, points.z = best["x"], best["y"], best["z"]
    points.red, points.green, points.blue = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    points["Colorized"] = np.ones(len(best), dtype=np.uint8)
    points["ColorConfidence"] = best["confidence"]
    points["ColorDistance"] = best["distance"]
    points["SourceFlight"] = best["source"]
    writer.write_points(points)
    return len(best)


def fuse_clouds(paths: Iterable[str | Path], destination: str | Path, *,
                alignments: tuple[AlignmentResult, ...] | None = None,
                voxel_size_m: float | None = None,
                progress: ProgressCallback | None = None,
                cancelled: Callable[[], bool] | None = None) -> FusionResult:
    sources = tuple(Path(path).resolve() for path in paths)
    output = Path(destination).resolve()
    if len(sources) < 2:
        raise FusionError("Merged mode requires at least two flights.")
    if output.suffix.lower() != ".las":
        raise FusionError("Merged output must use the .las extension.")
    if output.exists():
        raise FusionError("Merged output already exists; choose another file name.")
    alignments = alignments or align_clouds(sources, progress=progress, cancelled=cancelled)
    if tuple(item.source_path for item in alignments) != sources:
        raise FusionError("Alignment results do not match the input clouds.")
    sample = _transform(_sample_xyz(sources[0], 50_000), alignments[0].transform)
    voxel = float(voxel_size_m or np.clip(_spacing(sample) * 1.25, .005, .04))
    if not math.isfinite(voxel) or voxel <= 0:
        raise FusionError("Fusion voxel size must be finite and positive.")
    total_colored = 0
    minimum = np.full(3, np.inf)
    maximum = np.full(3, -np.inf)
    for source_index, (path, alignment) in enumerate(zip(sources, alignments)):
        with laspy.open(path) as reader:
            if not all(name in reader.header.point_format.dimension_names for name in ("red", "green", "blue")):
                raise FusionError(f"Colorized RGB dimensions are missing: {path}")
            for points in reader.chunk_iterator(750_000):
                _check_cancel(cancelled)
                selected = _colored_mask(points)
                if selected.any():
                    xyz = _transform(np.column_stack((points.x[selected], points.y[selected], points.z[selected])),
                                     alignment.transform)
                    minimum = np.minimum(minimum, xyz.min(axis=0))
                    maximum = np.maximum(maximum, xyz.max(axis=0))
                    total_colored += len(xyz)
        if progress:
            progress("Preparing merge", (source_index + 1) / len(sources) * .2,
                     f"Found {total_colored:,} colored observations")
    if not total_colored or not np.isfinite(minimum).all():
        raise FusionError("The selected colorized clouds contain no observed RGB points.")
    span = np.ceil((maximum - minimum) / voxel)
    if np.any(span > np.iinfo(np.int32).max - 2):
        raise FusionError("Cloud coordinate span is too large for safe spatial fusion.")
    output.parent.mkdir(parents=True, exist_ok=True)
    required = total_colored * _RECORD_DTYPE.itemsize + total_colored * 40 + 64 * 1024**2
    if shutil.disk_usage(output.parent).free < required:
        raise FusionError(f"Insufficient temporary disk space; need about {required / 1024**3:.2f} GiB.")
    with tempfile.TemporaryDirectory(prefix=".elios-fusion-", dir=output.parent) as scratch:
        records = np.memmap(Path(scratch) / "observations.bin", mode="w+", dtype=_RECORD_DTYPE,
                            shape=(total_colored,))
        cursor = 0
        try:
            for source_index, (path, alignment) in enumerate(zip(sources, alignments), 1):
                with laspy.open(path) as reader:
                    for points in reader.chunk_iterator(500_000):
                        _check_cancel(cancelled)
                        selected = _colored_mask(points)
                        length = int(selected.sum())
                        if not length:
                            continue
                        xyz = _transform(np.column_stack((points.x[selected], points.y[selected], points.z[selected])),
                                         alignment.transform)
                        view = records[cursor:cursor + length]
                        indices = np.floor((xyz - minimum) / voxel).astype(np.int64)
                        view["ix"], view["iy"], view["iz"] = indices[:, 0], indices[:, 1], indices[:, 2]
                        confidence = _confidence(points, selected)
                        view["negscore"] = -confidence
                        view["x"], view["y"], view["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
                        view["rgb"] = np.column_stack((points.red[selected], points.green[selected], points.blue[selected]))
                        view["confidence"] = confidence
                        view["distance"] = _distance(points, selected)
                        view["source"] = source_index
                        cursor += length
                if progress:
                    progress("Indexing colored points", .2 + .35 * cursor / total_colored,
                             f"Indexed {cursor:,} of {total_colored:,} colored observations")
            records.flush()
            _check_cancel(cancelled)
            if progress:
                progress("Reconciling overlap", .58, "Sorting nearby observations by confidence…")
            records.sort(order=("ix", "iy", "iz", "negscore"), kind="quicksort")
            records.flush()
            temporary = Path(scratch) / "merged.las"
            header = _output_header(minimum)
            written = 0
            block_size = 1_000_000
            carry = np.empty(0, dtype=_RECORD_DTYPE)
            with laspy.open(temporary, mode="w", header=header) as writer:
                for start in range(0, total_colored, block_size):
                    _check_cancel(cancelled)
                    block = np.asarray(records[start:min(total_colored, start + block_size)])
                    data = np.concatenate((carry, block)) if len(carry) else block
                    if start + block_size < total_colored:
                        last = data[-1]
                        boundary = np.flatnonzero((data["ix"] != last["ix"]) | (data["iy"] != last["iy"])
                                                  | (data["iz"] != last["iz"]))
                        split = int(boundary[-1] + 1) if len(boundary) else 0
                        complete, carry = data[:split], data[split:].copy()
                    else:
                        complete, carry = data, np.empty(0, dtype=_RECORD_DTYPE)
                    written += _write_groups(writer, complete, header)
                    if progress:
                        progress("Writing merged LAS", .62 + .37 * min(total_colored, start + block_size) / total_colored,
                                 f"Wrote {written:,} fused colored points")
            _check_cancel(cancelled)
            os.rename(temporary, output)
            result = FusionResult(output, written, total_colored, voxel, alignments)
            if progress:
                progress("Merge complete", 1.0, f"Saved {written:,} colored points from {len(sources)} flights")
            return result
        finally:
            records._mmap.close()
