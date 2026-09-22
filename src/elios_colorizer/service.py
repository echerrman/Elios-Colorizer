"""Application boundary shared by the GUI and command-line tools.

Keep third-party imports lazy so an incomplete development installation can
still explain which dependencies are missing. Flight files are never edited.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

from . import __version__


def dependency_status() -> list[dict]:
    rows = []
    for label, module, distribution in (
        ('Desktop interface', 'PySide6.QtWidgets', 'PySide6-Essentials'),
        ('Numerical processing', 'numpy', 'numpy'),
        ('Pose interpolation', 'scipy.spatial.transform', 'scipy'),
        ('Video decoder', 'cv2', 'opencv-python-headless'),
        ('LAS / LAZ reader', 'laspy', 'laspy'),
        ('LAZ decompression', 'lazrs', 'lazrs'),
        ('MCAP reader', 'mcap.reader', 'mcap'),
        ('ROS message decoder', 'mcap_ros2.decoder', 'mcap-ros2-support'),
    ):
        try:
            importlib.import_module(module)
            try:
                installed_version = version(distribution)
            except PackageNotFoundError:
                installed_version = 'bundled'
            rows.append(dict(label=label, status='ok', detail=installed_version))
        except (ImportError, OSError) as exc:
            rows.append(dict(label=label, status='missing',
                             detail=f'{distribution}: {exc}. Use the portable build or scripts/setup.ps1.'))
    return rows


def inspect_source(folder: str, las_override: str | None = None,
                   calibration_override: str | None = None) -> dict:
    dependencies = dependency_status()
    rows: list[dict] = []

    def check(label, good, detail):
        rows.append(dict(label=label, status='ok' if good else 'missing', detail=detail))

    if not folder or not Path(folder).is_dir():
        check('Flight folder', False, 'Choose the native flight folder, or its parent containing the matching export.')
        return dict(ready=False, checklist=rows, dependencies=dependencies, summary='Select a flight to begin.')
    if any(row['status'] == 'missing' for row in dependencies):
        check('Local tools', False, 'Restore the missing dependencies listed below, then refresh.')
        return dict(ready=False, checklist=rows, dependencies=dependencies, summary='Local setup is incomplete.')

    from .flight import discover_source
    from .camera import Calibration
    try:
        source = discover_source(folder, las_override=las_override, calibration_override=calibration_override)
    except (ValueError, OSError, RuntimeError) as exc:
        check('Flight identification', False, str(exc))
        return dict(ready=False, checklist=rows, dependencies=dependencies, summary='The source could not be identified.')

    check('Flight metadata', bool(source.metadata_path),
          str(source.metadata_path or 'A native flight.json or Inspector export JSON is needed.'))
    videos = source.videos
    check('RGB video', bool(videos), ', '.join(p.name for p in videos) if videos else 'No RGB MOV segments found.')
    check('Position and orientation', bool(source.mcap_path or source.trajectory_path),
          str(source.mcap_path or source.trajectory_path or 'MCAP odometry or an exported trajectory is needed.'))
    check('Camera tilt and video timing', bool(source.starnet_path),
          str(source.starnet_path or 'The native StarNet .stn log provides reported camera pitch and per-frame timing.'))

    point_count = 0
    if source.las_path:
        try:
            import laspy
            with laspy.open(source.las_path) as reader:
                header = reader.header
                point_count = header.point_count
                if header.point_format.id in (4, 5, 9, 10):
                    raise ValueError('Waveform LAS is unsupported. Export the ordinary Inspector LAS.')
                if not point_count:
                    raise ValueError('LAS contains no points.')
            check('Existing point cloud', True, f'{source.las_path.name} · {point_count:,} points')
        except Exception as exc:
            check('Existing point cloud', False, f'Cannot read LAS: {exc}')
    else:
        check('Existing point cloud', False, 'Export the uncolored LAS from Inspector and place it in the matching export folder, or select it below.')

    calibration = None
    if source.calibration_path:
        try:
            calibration = Calibration.load(source.calibration_path)
            check('RGB camera calibration', True, calibration.profile_name)
        except Exception as exc:
            check('RGB camera calibration', False, str(exc))
    else:
        check('RGB camera calibration', False,
              'A calibrated 4K RGB lens and camera mount profile is required once per camera/recording mode. '
              'Navigation-camera YAMLs cannot be used. See docs/CALIBRATION.md.')
    if videos:
        import cv2
        cap = cv2.VideoCapture(str(videos[0]))
        try:
            ok, frame = cap.read()
            if not ok:
                raise ValueError('First RGB segment could not be decoded by the bundled video reader.')
            height, width = frame.shape[:2]
            if calibration and (width, height) != (calibration.image_width, calibration.image_height):
                raise ValueError(f'Video is {width} × {height}, but calibration expects '
                                 f'{calibration.image_width} × {calibration.image_height}.')
            check('Video decoding', True, f'{width} × {height}; local decoder available')
        except Exception as exc:
            check('Video decoding', False, str(exc))
        finally:
            cap.release()
    for warning in source.warnings:
        rows.append(dict(label='Source note', status='warning', detail=warning))
    return dict(ready=all(row['status'] != 'missing' for row in rows), checklist=rows,
                dependencies=dependencies, summary=f'{point_count:,} source points. '
                'Coordinates and original attributes are retained; points without a usable view remain uncolored.')


def cache_directory() -> Path:
    return Path(os.environ.get('ELIOS_COLORIZER_CACHE', str(Path(tempfile.gettempdir()) / 'EliosColorizer' / 'cache')))


def run_colorization(folder: str, output: str, las_override: str | None = None,
                     calibration_override: str | None = None,
                     progress: Callable[[str, float, str], None] | None = None,
                     cancelled: Callable[[], bool] | None = None,
                     *, sample_interval_s: float = 1.0, max_frames: int | None = None,
                     start_s: float | None = None, end_s: float | None = None,
                     experimental: bool = False) -> dict:
    from .flight import discover_source, load_telemetry, iter_observations
    from .camera import Calibration
    from .colorize import colorize_las, ColorizationOptions, ColorizationCancelled

    import math
    if not math.isfinite(sample_interval_s) or sample_interval_s <= 0:
        raise ValueError('Frame interval must be finite and positive.')
    if max_frames is not None and max_frames < 1:
        raise ValueError('Frame limit must be positive.')
    if any(t is not None and (not math.isfinite(t) or t < 0) for t in (start_s, end_s)):
        raise ValueError('Time window must contain nonnegative video elapsed seconds.')
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError('End time must follow start time.')
    started = time.monotonic()
    source = discover_source(folder, las_override=las_override, calibration_override=calibration_override)
    if not source.las_path or not source.calibration_path or not source.starnet_path or not source.videos:
        raise ValueError('LAS, RGB videos, native StarNet log, and RGB calibration are all required. Refresh the source checklist.')
    destination = Path(output).resolve()
    report_path = destination.with_suffix('.report.json')
    if destination.exists() or report_path.exists():
        raise ValueError('Output or its report already exists. Choose a new output file name.')
    if destination.suffix.lower() != '.las':
        raise ValueError('Choose a .las output file.')
    if destination == source.las_path.resolve():
        raise ValueError('Output must differ from the source LAS.')
    calibration = Calibration.load(source.calibration_path, require_validated=not experimental)
    high_water = 0.0
    last_message_time = 0.0

    def emit(stage: str, fraction: float, message: str, *, force=False):
        nonlocal high_water, last_message_time
        high_water = max(high_water, min(1.0, fraction))
        now = time.monotonic()
        if progress and (force or now - last_message_time >= .25):
            progress(stage, high_water, message)
            last_message_time = now

    def ensure_running():
        if cancelled and cancelled():
            raise ColorizationCancelled('Colorization cancelled.')

    ensure_running()
    emit('Reading telemetry', .01, 'Extracting pose, reported camera pitch, and video frame timing…', force=True)
    telemetry = load_telemetry(source, cache_directory(), cancelled=cancelled,
                              progress=lambda message, fraction: emit('Reading telemetry', .02, message))
    ensure_running()
    if not len(telemetry.frame_times_s):
        raise ValueError('No recorded video frame timing was found.')
    origin = float(telemetry.frame_times_s[0])
    first = origin + (start_s or 0)
    last = float(telemetry.frame_times_s[-1]) if end_s is None else min(origin + end_s, float(telemetry.frame_times_s[-1]))
    if first >= last:
        raise ValueError('Time window does not overlap recorded video timing.')
    expected_frames = max(1, int((last - first) / sample_interval_s) + 1)
    if max_frames is not None:
        expected_frames = min(expected_frames, max_frames)
    emit('Colorizing', .08, f'Projecting sampled RGB frames onto the existing LAS ({expected_frames} planned views)…', force=True)
    frames = iter_observations(source, telemetry, sample_interval_s=sample_interval_s,
                               max_frames=max_frames, start_s=start_s, end_s=end_s, cancelled=cancelled)
    logical_cpus = os.cpu_count() or 1
    # Projection work is memory-bandwidth heavy. Four workers gives useful CPU
    # concurrency without oversubscribing NumPy/OpenCV or inflating temporaries.
    workers = max(1, min(4, logical_cpus // 4))
    options = ColorizationOptions(experimental_calibration=experimental, worker_threads=workers)

    def engine_progress(info: dict):
        stage = info['stage']
        if stage == 'indexing':
            fraction = info['points_processed'] / max(info['point_count'], 1)
            method = (f"caching coordinates in memory for {info['worker_threads']} CPU workers"
                      if info['xyz_cache_used'] else 'using the bounded-memory streaming path')
            emit('Preparing point cloud', .02 + .05 * fraction,
                 f"Reading {info['points_processed']:,} of {info['point_count']:,} points; {method}")
        elif stage == 'writing':
            fraction = info['points_processed'] / max(info['point_count'], 1)
            emit('Saving LAS', .9 + .09 * fraction,
                 f"Writing {info['points_processed']:,} of {info['point_count']:,} points")
        elif stage in ('visibility', 'colorizing'):
            local = info['points_processed'] / max(info['point_count'], 1)
            within_batch = (.5 if stage == 'colorizing' else 0) + .5 * local
            completed = max(0, info['frames_received'] - options.frame_batch_size)
            fraction = min(1., (completed + options.frame_batch_size * within_batch) / expected_frames)
            emit('Checking visibility' if stage == 'visibility' else 'Assigning RGB', .08 + .81 * fraction,
                 f"{info['frames_used']} views · {info['points_processed']:,} / {info['point_count']:,} points in this pass")

    result = colorize_las(source.las_path, destination, frames, calibration,
                          options=options, progress=engine_progress, cancelled=cancelled)
    # Report only the inputs needed to reproduce processing; never copy flight
    # metadata wholesale (it can include aircraft authentication information).
    report = {
        'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'experimental_calibration': not calibration.validated,
        'source_las': str(source.las_path), 'videos': [str(p) for p in source.videos],
        'pose_source': str(source.mcap_path or source.trajectory_path),
        'tilt_and_timing_source': str(source.starnet_path),
        'calibration_file': str(source.calibration_path),
        'calibration_sha256': hashlib.sha256(source.calibration_path.read_bytes()).hexdigest(),
        'calibration': calibration.to_dict(), 'result': result.to_dict(),
        'processing_configuration': {
            'chunk_size': options.chunk_size,
            'frame_batch_size': options.frame_batch_size,
            'depth_buffer_width': options.depth_buffer_width,
            'xyz_cache_used': result.xyz_cache_used,
            'worker_threads': options.worker_threads,
            'logical_cpus_detected': logical_cpus,
            'acceleration': 'CPU vectorization, bounded worker threads, and an automatic in-memory XYZ cache when memory permits',
        },
        'time_synchronization': telemetry.timing_diagnostics,
        'sample_interval_seconds': sample_interval_s,
        'time_window_s': [first, last],
        'warnings': list(dict.fromkeys([*source.warnings, *telemetry.warnings])),
        'uncolored_points': 'RGB=(0,0,0), Colorized=0. No nearest-neighbor color fill.',
    }
    # Publish report using exclusive creation; an unrelated report is never overwritten.
    try:
        with report_path.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2)
    except OSError as exc:
        emit('LAS saved', 1, f'LAS is complete, but report could not be saved: {exc}', force=True)
        return dict(output=str(destination), report=None, colored_points=result.colored_point_count,
                    total_points=result.point_count, warning=str(exc))
    emit('Complete', 1, f'{result.colored_point_count:,} of {result.point_count:,} points colored '
         f'({result.coverage_fraction:.1%}).', force=True)
    return dict(output=str(destination), report=str(report_path), colored_points=result.colored_point_count,
                total_points=result.point_count)
