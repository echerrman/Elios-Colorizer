"""Preparation for confidence-aware colorization of one merged point cloud."""
from __future__ import annotations

from dataclasses import dataclass, replace
import copy
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Iterable, Iterator

import laspy
import numpy as np
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from scipy.optimize import least_squares

from .colorize import FrameObservation
from .fusion import _metrics, _sample_xyz, _spacing, _transform


class MergedWorkflowError(RuntimeError):
    pass


def parse_transform_text(text: str, label: str = "entered matrix") -> np.ndarray:
    """Parse and validate a rigid homogeneous transform from pasted values."""
    rows: list[list[float]] = []
    for line in text.splitlines():
        numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", line)
        if len(numbers) == 4:
            rows.append([float(value) for value in numbers])
    if len(rows) < 4:
        numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
        if len(numbers) == 16:
            rows = [[float(value) for value in numbers[index:index + 4]] for index in range(0, 16, 4)]
    if len(rows) != 4:
        raise MergedWorkflowError(f"Expected one 4 x 4 matrix in {label}.")
    matrix = np.asarray(rows, dtype=np.float64)
    if not np.isfinite(matrix).all() or not np.allclose(matrix[3], (0, 0, 0, 1), atol=1e-5):
        raise MergedWorkflowError(f"Invalid homogeneous transformation matrix: {label}")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-3) or not math.isclose(
            float(np.linalg.det(rotation)), 1.0, abs_tol=2e-3):
        raise MergedWorkflowError(
            f"{label} contains scale, shear, or reflection; only rigid transforms are supported.")
    return matrix


def load_transform(path: str | Path | None) -> np.ndarray:
    """Load a 4x4 source-to-merged rigid transform; blank is identity."""
    if not path:
        return np.eye(4)
    matrix_path = Path(path).resolve()
    if not matrix_path.is_file():
        raise MergedWorkflowError(f"Transformation matrix was not found: {matrix_path}")
    return parse_transform_text(matrix_path.read_text(encoding="utf-8", errors="replace"), matrix_path.name)


def selection_transform(item: dict, flight_index: int | None = None) -> np.ndarray:
    values = str(item.get("transform_values") or "").strip()
    if values:
        label = f"Flight {flight_index} entered matrix" if flight_index else "entered matrix"
        return parse_transform_text(values, label)
    return load_transform(item.get("transform_path"))


def transform_observations(observations: Iterable[FrameObservation], matrix: np.ndarray,
                           source_flight: int) -> Iterator[FrameObservation]:
    """Move camera centers and orientations into the final merged coordinate frame."""
    rotation = Rotation.from_matrix(matrix[:3, :3])
    for observation in observations:
        position = _transform(np.asarray(observation.position_world_m, dtype=np.float64)[None, :], matrix)[0]
        orientation = (rotation * Rotation.from_quat(observation.orientation_xyzw)).as_quat()
        yield replace(observation, position_world_m=position, orientation_xyzw=orientation,
                      source_flight=source_flight)


def _alignment_metrics(source: Path, merged: Path, matrix: np.ndarray,
                       maximum: int = 80_000) -> tuple[float, float, float]:
    target = _sample_xyz(merged, maximum)
    sample = _transform(_sample_xyz(source, maximum), matrix)
    spacing = max(_spacing(target), .002)
    gate = float(np.clip(spacing * 8, .025, .15))
    median, _, overlap = _metrics(cKDTree(target), sample, gate)
    return median, overlap, gate


def validate_transforms(source_paths: Iterable[str | Path], merged_path: str | Path,
                        matrices: Iterable[np.ndarray]) -> list[dict]:
    """Reject a manual matrix in the wrong direction or an unrelated merged cloud."""
    merged = Path(merged_path).resolve()
    if not merged.is_file():
        raise MergedWorkflowError("Choose the already aligned and merged LAS/LAZ point cloud.")
    results: list[dict] = []
    for index, (source_value, matrix) in enumerate(zip(source_paths, matrices), 1):
        source = Path(source_value).resolve()
        median, overlap, gate = _alignment_metrics(source, merged, matrix)
        # A merged cloud may have been heavily cleaned. Require a meaningful matching subset,
        # while allowing different point sampling and partial overlap.
        if not math.isfinite(median) or overlap < .08:
            raise MergedWorkflowError(
                f"Flight {index} does not overlap the merged cloud after its transform "
                f"({overlap:.1%} within {gate:.3f} m). Check matrix direction; it must map the original flight LAS into the merged cloud.")
        results.append(dict(flight=index, median_distance_m=median, overlap=overlap,
                            validation_gate_m=gate, transform=matrix.tolist()))
    return results


def _header_corners(header: laspy.LasHeader) -> np.ndarray:
    lo, hi = np.asarray(header.mins), np.asarray(header.maxs)
    return np.asarray([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def merge_geometry(source_paths: Iterable[str | Path], matrices: Iterable[np.ndarray],
                   destination: str | Path, *, progress=None, cancelled=None) -> Path:
    """Stream transformed source clouds into one geometry without removing any points."""
    sources = [Path(value).resolve() for value in source_paths]
    transforms = list(matrices)
    output = Path(destination).resolve()
    if len(sources) != len(transforms) or not sources:
        raise MergedWorkflowError("Every source cloud needs exactly one transformation.")
    minimum = np.full(3, np.inf)
    maximum = np.full(3, -np.inf)
    total = 0
    headers: list[laspy.LasHeader] = []
    for source, matrix in zip(sources, transforms):
        with laspy.open(source) as reader:
            if reader.header.point_format.id in (4, 5, 9, 10):
                raise MergedWorkflowError("Waveform LAS point formats are not supported for automatic merging.")
            headers.append(copy.deepcopy(reader.header))
            corners = _transform(_header_corners(reader.header), matrix)
            minimum = np.minimum(minimum, corners.min(axis=0))
            maximum = np.maximum(maximum, corners.max(axis=0))
            total += int(reader.header.point_count)
    if total == 0:
        raise MergedWorkflowError("The selected point clouds contain no points.")
    header = headers[0]
    header.offsets = np.floor(minimum / header.scales) * header.scales
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.geometry.tmp")
    if temporary.exists():
        temporary.unlink()
    written = 0
    try:
        with laspy.open(temporary, mode="w", header=header) as writer:
            for flight, (source, matrix) in enumerate(zip(sources, transforms), 1):
                with laspy.open(source) as reader:
                    for records in reader.chunk_iterator(500_000):
                        if cancelled and cancelled():
                            raise MergedWorkflowError("Processing cancelled; no merged output was published.")
                        points = laspy.PackedPointRecord.from_point_record(records, header.point_format)
                        xyz = _transform(np.column_stack((records.x, records.y, records.z)), matrix)
                        quantized = np.rint((xyz - header.offsets) / header.scales)
                        if np.any(np.abs(quantized) > np.iinfo(np.int32).max):
                            raise MergedWorkflowError("Transformed coordinates exceed LAS integer storage range.")
                        points.X, points.Y, points.Z = (quantized[:, 0].astype(np.int32),
                                                        quantized[:, 1].astype(np.int32),
                                                        quantized[:, 2].astype(np.int32))
                        writer.write_points(points)
                        written += len(points)
                        if progress:
                            progress("Building merged geometry", written / total,
                                     f"Flight {flight}: {written:,} / {total:,} points")
                if headers[flight - 1].evlrs and flight == 1:
                    writer.write_evlrs(headers[0].evlrs)
        os.replace(temporary, output)
        return output
    finally:
        if temporary.exists():
            temporary.unlink()


def spatially_index_geometry(source_path: str | Path, destination: str | Path, *,
                             progress=None, cancelled=None, axis_cells: int = 64,
                             chunk_size: int = 500_000) -> tuple[Path, dict]:
    """Create a lossless spatially ordered LAS so bounded projection can skip distant tiles."""
    source = Path(source_path).resolve()
    output = Path(destination).resolve()
    if source == output or output.suffix.lower() != '.las':
        raise MergedWorkflowError('Spatial index output must be a different .las file.')
    with laspy.open(source) as reader:
        header = copy.deepcopy(reader.header)
        count = int(header.point_count)
    if count == 0:
        raise MergedWorkflowError('Cannot spatially index an empty point cloud.')
    axis_cells = int(np.clip(axis_cells, 4, 128))
    minimum, maximum = np.asarray(header.mins, dtype=np.float64), np.asarray(header.maxs, dtype=np.float64)
    extent = np.maximum(maximum - minimum, np.asarray(header.scales, dtype=np.float64))
    cell_size = max(float(extent.max()) / axis_cells, float(np.max(header.scales)))
    dimensions = np.maximum(1, np.ceil(extent / cell_size).astype(np.int64) + 1)
    cell_count = int(np.prod(dimensions))
    counts = np.zeros(cell_count, dtype=np.int64)

    def cell_ids(records) -> np.ndarray:
        xyz = np.column_stack((records.x, records.y, records.z))
        cells = np.floor((xyz - minimum) / cell_size).astype(np.int64)
        cells = np.minimum(np.maximum(cells, 0), dimensions - 1)
        return (cells[:, 0] * dimensions[1] + cells[:, 1]) * dimensions[2] + cells[:, 2]

    processed = 0
    with laspy.open(source) as reader:
        for records in reader.chunk_iterator(chunk_size):
            if cancelled and cancelled():
                raise MergedWorkflowError('Processing cancelled while building the spatial index.')
            counts += np.bincount(cell_ids(records), minlength=cell_count)
            processed += len(records)
            if progress:progress('Building spatial index', .20 * processed / count,
                                  f'Counting spatial tiles: {processed:,} / {count:,} points')
    starts = np.empty(cell_count, dtype=np.int64)
    starts[0] = 0
    if cell_count > 1:np.cumsum(counts[:-1], out=starts[1:])
    cursors = starts.copy()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output.with_suffix('.spatial-index.tmp')
    temporary = output.with_name(f'.{output.name}.tmp')
    required = count * header.point_format.size * 2 + 256 * 1024**2
    if shutil.disk_usage(output.parent).free < required:
        raise MergedWorkflowError(f'Insufficient temporary disk space for spatial indexing; '
                                  f'about {required / 1024**3:.1f} GiB is required.')
    raw_path.unlink(missing_ok=True);temporary.unlink(missing_ok=True)
    ordered = np.memmap(raw_path, mode='w+', dtype=header.point_format.dtype(), shape=(count,))
    processed = 0
    try:
        with laspy.open(source) as reader:
            for records in reader.chunk_iterator(chunk_size):
                if cancelled and cancelled():
                    raise MergedWorkflowError('Processing cancelled while building the spatial index.')
                ids = cell_ids(records)
                order = np.argsort(ids, kind='stable')
                sorted_ids = ids[order]
                positions = np.arange(len(order), dtype=np.int64)
                group_starts = np.maximum.accumulate(np.where(
                    np.r_[True, sorted_ids[1:] != sorted_ids[:-1]], positions, 0))
                destinations = cursors[sorted_ids] + positions - group_starts
                ordered[destinations] = records.array[order]
                cursors += np.bincount(ids, minlength=cell_count)
                processed += len(records)
                if progress:progress('Building spatial index', .20 + .50 * processed / count,
                                      f'Ordering spatial tiles: {processed:,} / {count:,} points')
        ordered.flush()
        processed = 0
        with laspy.open(temporary, mode='w', header=header) as writer:
            for offset in range(0, count, chunk_size):
                if cancelled and cancelled():
                    raise MergedWorkflowError('Processing cancelled while building the spatial index.')
                length = min(chunk_size, count - offset)
                writer.write_points(laspy.PackedPointRecord(ordered[offset:offset + length], header.point_format))
                processed += length
                if progress:progress('Building spatial index', .70 + .30 * processed / count,
                                      f'Writing indexed geometry: {processed:,} / {count:,} points')
            if header.evlrs:writer.write_evlrs(header.evlrs)
        os.replace(temporary, output)
        occupied = int(np.count_nonzero(counts))
        return output, dict(method='lossless uniform-grid spatial ordering', point_count=count,
                            cell_size_m=cell_size, grid_dimensions=dimensions.tolist(),
                            occupied_cells=occupied, total_cells=cell_count,
                            attributes_preserved=True)
    finally:
        del ordered
        raw_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def _find_cloudcompare_matrix(folder: Path, before: dict[Path, int]) -> Path:
    candidates = [path for path in folder.glob("*.txt")
                  if path not in before or path.stat().st_mtime_ns != before[path]]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            load_transform(path)
            return path
        except (OSError, MergedWorkflowError):
            continue
    raise MergedWorkflowError("CloudCompare did not produce a readable 4 x 4 ICP transformation matrix.")


def _write_xyz_las(path: Path, xyz: np.ndarray) -> None:
    header = laspy.LasHeader(point_format=1, version="1.2")
    header.scales = np.full(3, .001)
    header.offsets = np.floor(xyz.min(axis=0))
    cloud = laspy.LasData(header)
    cloud.x, cloud.y, cloud.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    cloud.write(path)


def _overlap_metrics(tree: cKDTree, xyz: np.ndarray, gate: float) -> tuple[float, float, float]:
    """Measure alignment quality on the overlapping subset, plus its full-cloud fraction."""
    distances, _ = tree.query(xyz, k=1, workers=-1)
    usable = np.isfinite(distances) & (distances <= gate)
    if not usable.any():
        return math.inf, math.inf, 0.0
    inliers = distances[usable]
    return float(np.median(inliers)), float(np.quantile(inliers, .9)), float(np.mean(usable))


@dataclass(frozen=True)
class PoseGraphEdge:
    fixed: int
    moving: int
    moving_to_fixed: np.ndarray
    overlap: float
    initial_median_m: float
    final_median_m: float
    gate_m: float
    method: str

    @property
    def weight(self) -> float:
        return float(np.clip(self.overlap, .05, 1.0) / max(self.final_median_m, .005))

    def to_dict(self) -> dict:
        return dict(fixed_flight=self.fixed + 1, moving_flight=self.moving + 1,
                    method=self.method, overlap=self.overlap,
                    initial_median_m=self.initial_median_m, final_median_m=self.final_median_m,
                    validation_gate_m=self.gate_m, moving_to_fixed=self.moving_to_fixed.tolist())


def _pose_matrix(values: np.ndarray) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_rotvec(values[3:6]).as_matrix()
    matrix[:3, 3] = values[:3]
    return matrix


def _pose_values(matrix: np.ndarray) -> np.ndarray:
    return np.r_[matrix[:3, 3], Rotation.from_matrix(matrix[:3, :3]).as_rotvec()]


def _edge_error(global_transforms: list[np.ndarray], edge: PoseGraphEdge) -> tuple[float, float]:
    predicted_moving = global_transforms[edge.fixed] @ edge.moving_to_fixed
    error = np.linalg.inv(predicted_moving) @ global_transforms[edge.moving]
    return (float(np.linalg.norm(error[:3, 3])),
            float(np.degrees(Rotation.from_matrix(error[:3, :3]).magnitude())))


def optimize_pose_graph(point_count: int, edges: list[PoseGraphEdge]) -> tuple[list[np.ndarray], dict]:
    """Jointly solve source-to-reference poses with Flight 1 fixed at identity."""
    if point_count < 2:
        raise MergedWorkflowError("A pose graph requires at least two flights.")
    ordered = sorted(edges, key=lambda edge: edge.weight, reverse=True)
    transforms: list[np.ndarray | None] = [None] * point_count
    transforms[0] = np.eye(4)
    # Maximum-quality spanning tree supplies a stable initial solution, including
    # flights that overlap an intermediate flight but not Flight 1.
    while True:
        changed = False
        for edge in ordered:
            fixed, moving = edge.fixed, edge.moving
            if transforms[fixed] is not None and transforms[moving] is None:
                transforms[moving] = transforms[fixed] @ edge.moving_to_fixed
                changed = True
            elif transforms[moving] is not None and transforms[fixed] is None:
                transforms[fixed] = transforms[moving] @ np.linalg.inv(edge.moving_to_fixed)
                changed = True
        if not changed:
            break
    missing = [index + 1 for index, matrix in enumerate(transforms) if matrix is None]
    if missing:
        raise MergedWorkflowError(
            f"Automatic alignment graph is disconnected; flights {missing} have no reliable overlap path. "
            "Align all clouds manually with a network registration.")
    initial = [matrix for matrix in transforms if matrix is not None]
    x0 = np.concatenate([_pose_values(matrix) for matrix in initial[1:]])

    def unpack(values: np.ndarray) -> list[np.ndarray]:
        return [np.eye(4), *[_pose_matrix(values[index:index + 6])
                            for index in range(0, len(values), 6)]]

    def residual(values: np.ndarray) -> np.ndarray:
        current = unpack(values)
        parts: list[np.ndarray] = []
        for edge in edges:
            predicted = current[edge.fixed] @ edge.moving_to_fixed
            error = np.linalg.inv(predicted) @ current[edge.moving]
            confidence = math.sqrt(float(np.clip(edge.overlap, .08, 1.0)))
            translation_scale = float(np.clip(edge.final_median_m * 2.0, .01, .06))
            parts.append(error[:3, 3] / translation_scale * confidence)
            parts.append(Rotation.from_matrix(error[:3, :3]).as_rotvec()
                         / math.radians(1.0) * confidence)
        # Weak priors retain Inspector's already-close coordinate system and
        # prevent a sparse tree from taking an unnecessarily large correction.
        for matrix in current[1:]:
            parts.append(matrix[:3, 3] / .50 * .05)
            parts.append(Rotation.from_matrix(matrix[:3, :3]).as_rotvec()
                         / math.radians(10.0) * .05)
        return np.concatenate(parts)

    solved = least_squares(residual, x0, loss="soft_l1", f_scale=1.0, max_nfev=250)
    if not solved.success or not np.isfinite(solved.x).all():
        raise MergedWorkflowError("Global alignment optimization did not converge. Align the clouds manually.")
    result = unpack(solved.x)
    edge_errors = [_edge_error(result, edge) for edge in edges]
    bad_edges = [(edge, error) for edge, error in zip(edges, edge_errors)
                 if error[0] > max(.04, edge.gate_m * .40) or error[1] > 1.5]
    if bad_edges:
        edge, error = max(bad_edges, key=lambda item: item[1][0] + item[1][1] / 20)
        raise MergedWorkflowError(
            f"Automatic alignment network is inconsistent between flights {edge.fixed + 1} and "
            f"{edge.moving + 1} ({error[0]:.3f} m, {error[1]:.2f}° loop residual). "
            "Align the clouds manually with a network registration.")
    for index, matrix in enumerate(result[1:], 2):
        translation = float(np.linalg.norm(matrix[:3, 3]))
        angle = float(np.degrees(Rotation.from_matrix(matrix[:3, :3]).magnitude()))
        if translation > .75 or angle > 8.0:
            raise MergedWorkflowError(
                f"Flight {index} requires an unsafe global correction ({translation:.3f} m, {angle:.2f}°). "
                "Align the clouds manually.")
    loop_count = max(0, len(edges) - (point_count - 1))
    return result, dict(converged=True, edge_count=len(edges), redundant_loop_edges=loop_count,
                        initial_cost=float(np.sum(residual(x0) ** 2)), final_cost=float(np.sum(residual(solved.x) ** 2)),
                        maximum_edge_translation_residual_m=max((item[0] for item in edge_errors), default=0.0),
                        maximum_edge_rotation_residual_degrees=max((item[1] for item in edge_errors), default=0.0))


def _run_pair_icp(executable: Path, folder: Path, fixed_path: Path, moving_path: Path,
                  expected_overlap: float) -> np.ndarray:
    before = {path: path.stat().st_mtime_ns for path in folder.glob("*.txt")}
    command = [str(executable), "-SILENT", "-AUTO_SAVE", "OFF", "-O", str(moving_path),
               "-O", str(fixed_path), "-ICP", "-OVERLAP", str(int(np.clip(expected_overlap * 100, 20, 90))),
               "-RANDOM_SAMPLING_LIMIT", "100000", "-FARTHEST_REMOVAL"]
    try:
        completed = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=1800,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MergedWorkflowError(f"CloudCompare ICP could not complete: {exc}") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "unknown CloudCompare error").strip()[-600:]
        raise MergedWorkflowError(f"CloudCompare ICP failed: {message}")
    return load_transform(_find_cloudcompare_matrix(folder, before))


def automatic_align(source_paths: Iterable[str | Path], cloudcompare: str | Path,
                    scratch: str | Path, *, progress=None, cancelled=None) -> tuple[list[np.ndarray], dict]:
    """Build and globally optimize a validated overlap graph of pairwise ICP constraints."""
    sources = [Path(value).resolve() for value in source_paths]
    executable = Path(cloudcompare).resolve()
    if not executable.is_file():
        raise MergedWorkflowError("Choose the CloudCompare executable (CloudCompare.exe).")
    if len(sources) < 2:
        raise MergedWorkflowError("Automatic alignment requires at least two clouds.")
    folder = Path(scratch).resolve()
    training: list[np.ndarray] = []
    validation: list[np.ndarray] = []
    sample_paths: list[Path] = []
    spacings: list[float] = []
    for index, source in enumerate(sources):
        if cancelled and cancelled():
            raise MergedWorkflowError("Processing cancelled before automatic alignment completed.")
        sampled = _sample_xyz(source, 120_000)
        training.append(sampled[::2])
        validation.append(sampled[1::2])
        spacings.append(max(_spacing(validation[-1]), .002))
        sample_path = folder / f"flight_{index + 1:03d}_icp_sample.las"
        _write_xyz_las(sample_path, training[-1])
        sample_paths.append(sample_path)
    pair_count = len(sources) * (len(sources) - 1) // 2
    edges: list[PoseGraphEdge] = []
    rejected: list[dict] = []
    pair_number = 0
    for fixed in range(len(sources) - 1):
        fixed_tree = cKDTree(validation[fixed])
        for moving in range(fixed + 1, len(sources)):
            pair_number += 1
            if cancelled and cancelled():
                raise MergedWorkflowError("Processing cancelled during automatic alignment.")
            spacing = max(spacings[fixed], spacings[moving])
            gate = float(np.clip(spacing * 8, .025, .15))
            broad_gate = float(np.clip(gate * 3, .12, .40))
            initial_median, initial_q90, initial_overlap = _overlap_metrics(fixed_tree, validation[moving], gate)
            _, _, broad_forward = _metrics(fixed_tree, validation[moving], broad_gate)
            _, _, broad_reverse = _metrics(cKDTree(validation[moving]), validation[fixed], broad_gate)
            overlap_hint = min(broad_forward, broad_reverse)
            if progress:
                progress("Building alignment network", (pair_number - .75) / pair_count,
                         f"Checking overlap: Flight {fixed + 1} ↔ Flight {moving + 1}")
            if max(broad_forward, broad_reverse) < .10 or overlap_hint < .025:
                rejected.append(dict(fixed_flight=fixed + 1, moving_flight=moving + 1,
                                     reason="insufficient spatial overlap", broad_overlap=overlap_hint))
                continue
            if initial_overlap >= .30 and initial_median <= max(.025, spacing * 1.50):
                matrix = np.eye(4)
                final_median, final_q90, final_overlap = initial_median, initial_q90, initial_overlap
                method = "existing coordinates"
            else:
                try:
                    matrix = _run_pair_icp(executable, folder, sample_paths[fixed], sample_paths[moving],
                                           max(broad_forward, .20))
                except MergedWorkflowError as exc:
                    rejected.append(dict(fixed_flight=fixed + 1, moving_flight=moving + 1,
                                         reason=str(exc), broad_overlap=overlap_hint))
                    continue
                corrected = _transform(validation[moving], matrix)
                final_median, final_q90, final_overlap = _overlap_metrics(fixed_tree, corrected, gate)
                translation = float(np.linalg.norm(matrix[:3, 3]))
                angle = float(np.degrees(Rotation.from_matrix(matrix[:3, :3]).magnitude()))
                improved = (final_median <= initial_median * .90 and final_q90 <= initial_q90
                            and final_overlap >= max(.08, initial_overlap - .01))
                accurate = final_median <= max(.025, spacing * .80)
                bounded = translation <= .50 and angle <= 5.0
                if not improved or not accurate or not bounded:
                    rejected.append(dict(fixed_flight=fixed + 1, moving_flight=moving + 1,
                        reason="ICP failed held-out accuracy, improvement, or correction bounds",
                        initial_median_m=initial_median, final_median_m=final_median,
                        initial_overlap=initial_overlap, final_overlap=final_overlap))
                    continue
                method = "CloudCompare ICP"
            edges.append(PoseGraphEdge(fixed, moving, matrix, final_overlap,
                                       initial_median, final_median, gate, method))
            if progress:
                progress("Building alignment network", pair_number / pair_count,
                         f"Accepted Flight {fixed + 1} ↔ Flight {moving + 1} ({method})")
    transforms, optimization = optimize_pose_graph(len(sources), edges)
    flight_details = []
    for index, matrix in enumerate(transforms):
        translation = float(np.linalg.norm(matrix[:3, 3]))
        angle = float(np.degrees(Rotation.from_matrix(matrix[:3, :3]).magnitude()))
        flight_details.append(dict(flight=index + 1, reference=index == 0,
                                   translation_m=translation, rotation_degrees=angle,
                                   transform=matrix.tolist()))
    report = dict(policy="validated pairwise overlap graph with robust global pose optimization",
                  reference_flight=1, flights=flight_details,
                  accepted_edges=[edge.to_dict() for edge in edges], rejected_edges=rejected,
                  optimization=optimization)
    if progress:
        progress("Global alignment", 1.0,
                 f"Optimized {len(sources)} flights using {len(edges)} validated links "
                 f"and {optimization['redundant_loop_edges']} loop constraints")
    return transforms, report
