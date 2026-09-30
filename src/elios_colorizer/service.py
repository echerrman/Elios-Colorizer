"""Application boundary shared by the GUI and command-line tools.

Keep third-party imports lazy so an incomplete development installation can
still explain which dependencies are missing. Flight files are never edited.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Iterable

from . import __version__


def _flight_names(selections: list[dict[str, str | None]]) -> list[str]:
    return [str(item.get('name') or '').strip() or f'Flight {index}'
            for index, item in enumerate(selections, 1)]


def _duplicate_flight_names(selections: list[dict[str, str | None]]) -> set[str]:
    names = _flight_names(selections)
    normalized = [' '.join(name.split()).casefold() for name in names]
    return {names[index] for index, value in enumerate(normalized) if normalized.count(value) > 1}


def _name_flight_references(value: str, names: list[str]) -> str:
    def replace(match):
        index = int(match.group(1)) - 1
        return names[index] if 0 <= index < len(names) else match.group(0)
    return re.sub(r'\bFlight (\d+)\b', replace, value)


def _compact_count(value: int) -> str:
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    if value >= 10_000:
        return f'{value / 1_000:.1f}K'
    return f'{value:,}'


def _projection_progress_detail(frames_used: int, estimated_views: int, candidates: int,
                                planned: int, points: int, point_count: int) -> str:
    return (f'{frames_used:,}/~{estimated_views:,} views · '
            f'{candidates:,}/{planned:,} reviewed · '
            f'{_compact_count(points)}/{_compact_count(point_count)} points')


def processing_tuning(point_count: int, available_memory_bytes: int | None = None,
                      logical_cpus: int | None = None,
                      cuda_acceleration: bool = False) -> dict[str, int]:
    """Choose conservative projection parallelism without exposing UI settings."""
    logical = max(1, logical_cpus if logical_cpus is not None else (os.cpu_count() or 1))
    # Larger batches reuse each expensive pass over merged XYZ for more camera views.
    # Bound 4K frame storage when physical-memory information is unavailable or tight.
    if available_memory_bytes is None:
        try:
            from .colorize import _available_memory_bytes
            available_memory_bytes = _available_memory_bytes()
        except Exception:
            available_memory_bytes = None
    available_gib = available_memory_bytes / 1024**3 if available_memory_bytes is not None else 0
    high_resource = logical >= 24 and available_gib >= 16
    workers = max(1, min(12 if high_resource else 8, logical // 2))
    memory_cap = (24 if available_gib >= 20 else
                  (16 if available_gib >= 12 else (12 if available_gib >= 8 else
                                                   (8 if available_gib >= 5 else 4))))
    desired_batch = (24 if cuda_acceleration and point_count >= 10_000_000 else
                     (24 if point_count >= 50_000_000 else
                      (12 if point_count >= 10_000_000 else 8)))
    return {'worker_threads': workers, 'frame_batch_size': min(memory_cap, desired_batch)}


# Public compatibility for integrations which imported the original merged-only name.
merged_processing_tuning = processing_tuning


def find_cloudcompare_executable(selected: str | None = None) -> Path | None:
    """Resolve CloudCompare without persisting a machine-specific path."""
    if selected:
        path = Path(selected).expanduser().resolve()
        return path if path.is_file() else None
    from_path = shutil.which('CloudCompare.exe') or shutil.which('CloudCompare')
    if from_path:
        return Path(from_path).resolve()
    candidates: list[Path] = []
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        root = os.environ.get(variable)
        if root:
            candidates.extend((Path(root) / 'CloudCompare' / 'CloudCompare.exe',
                               Path(root) / 'Programs' / 'CloudCompare' / 'CloudCompare.exe'))
    candidates.append(Path(r'C:\Program Files\CloudCompare\CloudCompare.exe'))
    return next((path.resolve() for path in candidates if path.is_file()), None)


@lru_cache(maxsize=8)
def cloudcompare_cli_available(executable: str) -> bool:
    """Confirm the selected binary exposes the CLI operation required by the app."""
    try:
        completed = subprocess.run([executable, '-SILENT', '-HELP'], capture_output=True, text=True,
                                   timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = completed.stdout + completed.stderr
        return completed.returncode == 0 and '-ICP' in output and '-MERGE_CLOUDS' in output
    except (OSError, subprocess.TimeoutExpired):
        return False


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
    check('LAS-frame trajectory', bool(source.trajectory_path),
          str(source.trajectory_path or
              'The selected Inspector export must include the matching *-trajectory.csv. '
              'A native MCAP trajectory may use a different takeoff origin and cannot safely position an aligned LAS.'))
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
    estimated_views = 0
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
        duration_s = 0.0
        for video in videos:
            probe = cv2.VideoCapture(str(video))
            try:
                frames = float(probe.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = float(probe.get(cv2.CAP_PROP_FPS))
                if frames > 0 and fps > 0:
                    duration_s += frames / fps
            finally:
                probe.release()
        estimated_views = max(1, round(duration_s))
    for warning in source.warnings:
        rows.append(dict(label='Source note', status='warning', detail=warning))
    return dict(ready=all(row['status'] != 'missing' for row in rows), checklist=rows,
                dependencies=dependencies, summary=f'{point_count:,} source points. '
                'Coordinates and original attributes are retained; points without a usable view remain uncolored.',
                point_count=point_count, estimated_views=estimated_views)


def inspect_sources(flights: Iterable[dict[str, str | None]],
                    calibration_override: str | None = None, *, mode: str = 'separate',
                    alignment_method: str = 'manual', merged_source: str | None = None,
                    cloudcompare_executable: str | None = None) -> dict:
    """Inspect every flight and collapse the result into one readable checklist."""
    selections = list(flights)
    if not selections:
        return dict(ready=False, checklist=[dict(label='Flights', status='missing',
                    detail='Add at least one flight.')], dependencies=dependency_status(),
                    summary='Add a flight to begin.', flights=[])
    duplicate_names = _duplicate_flight_names(selections)
    if duplicate_names:
        names = ', '.join(sorted(duplicate_names, key=str.casefold))
        return dict(ready=False, checklist=[dict(label='Flight names', status='missing',
                    detail=f'Each flight needs a unique name. Rename: {names}.')],
                    dependencies=dependency_status(), summary='Duplicate flight names must be renamed.', flights=[])
    flight_names = _flight_names(selections)
    reports = [inspect_source(str(item.get('folder') or ''), item.get('las_override'),
                              calibration_override) for item in selections]
    labels = ('Flight metadata', 'RGB video', 'LAS-frame trajectory',
              'Camera tilt and video timing', 'Existing point cloud', 'RGB camera calibration',
              'Video decoding')
    checklist: list[dict] = []
    for label in labels:
        entries = [next((row for row in report['checklist'] if row['label'] == label), None)
                   for report in reports]
        failures = [(index + 1, row) for index, row in enumerate(entries)
                    if row is None or row.get('status') == 'missing']
        warnings = [(index + 1, row) for index, row in enumerate(entries)
                    if row is not None and row.get('status') == 'warning']
        if failures:
            detail = '; '.join(f"Flight {index}: {row['detail'] if row else 'not found'}"
                               for index, row in failures)
            status = 'missing'
        elif warnings:
            detail = '; '.join(f"Flight {index}: {row['detail']}" for index, row in warnings)
            status = 'warning'
        else:
            detail = f'{len(reports)} of {len(reports)} flights ready'
            status = 'ok'
        checklist.append(dict(label=label, status=status, detail=detail))
    source_notes = []
    for flight_index, report in enumerate(reports, 1):
        for row in report['checklist']:
            if row['label'] == 'Source note':
                source_notes.append(f"{flight_names[flight_index - 1]}: {row['detail']}")
            elif row['label'] in ('Flight identification', 'Flight folder', 'Local tools'):
                checklist.append(dict(label=f"{flight_names[flight_index - 1]}: {row['label']}",
                                      status=row['status'], detail=row['detail']))
    if source_notes:
        checklist.append(dict(label='Warnings', status='warning',
                              detail=f'{len(source_notes)} source notes across {len(reports)} flights.',
                              details=source_notes))
    ready = all(report['ready'] for report in reports)
    if ready:
        from .flight import discover_source
        from .camera import Calibration
        calibration_hashes = []
        for item in selections:
            source = discover_source(str(item.get('folder') or ''), item.get('las_override'),
                                     calibration_override)
            if source.calibration_path:
                canonical = json.dumps(Calibration.load(source.calibration_path).to_dict(),
                                       sort_keys=True, separators=(',', ':')).encode()
                calibration_hashes.append(hashlib.sha256(canonical).hexdigest())
        shared = len(set(calibration_hashes)) <= 1
        checklist.append(dict(label='Shared RGB camera profile', status='ok' if shared else 'missing',
            detail=('The same calibration is used for every flight.' if shared else
                    'Flights resolved to different camera profiles. Select one shared profile explicitly.')))
        ready = ready and shared
    if mode == 'merge' and len(selections) >= 2:
        if alignment_method == 'manual':
            merged = Path(merged_source).resolve() if merged_source else None
            good = bool(merged and merged.is_file() and merged.suffix.lower() in ('.las', '.laz'))
            checklist.append(dict(label='Aligned merged point cloud', status='ok' if good else 'missing',
                detail=str(merged) if good else 'Choose the LAS/LAZ merged in CloudCompare.'))
            ready = ready and good
            from .merged import selection_transform, MergedWorkflowError
            errors = []
            for index, item in enumerate(selections, 1):
                try:
                    selection_transform(item, index)
                except (OSError, MergedWorkflowError) as exc:
                    errors.append(f'Flight {index}: {exc}')
            checklist.append(dict(label='Source-to-merged transforms', status='ok' if not errors else 'missing',
                detail=('Blank entries use identity; supplied matrices are valid.' if not errors else '; '.join(errors))))
            ready = ready and not errors
        elif alignment_method == 'automatic':
            executable = find_cloudcompare_executable(cloudcompare_executable)
            good = bool(executable and cloudcompare_cli_available(str(executable)))
            if good:
                detail = f'CloudCompare CLI ready: {executable}'
            elif executable:
                detail = (f'Found {executable}, but it did not expose the required ICP and merge CLI commands. '
                          'Install a current CloudCompare release from cloudcompare.org or choose another executable.')
            elif cloudcompare_executable:
                detail = ('The selected CloudCompare executable was not found. Install CloudCompare from '
                          'cloudcompare.org or browse to CloudCompare.exe.')
            else:
                detail = ('CloudCompare CLI was not found on PATH or in a standard Windows install location. '
                          'Install it from cloudcompare.org, then browse to CloudCompare.exe.')
            checklist.append(dict(label='CloudCompare CLI', status='ok' if good else 'missing', detail=detail))
            ready = ready and good
        else:
            checklist.append(dict(label='Alignment method', status='missing', detail='Choose manual or automatic alignment.'))
            ready = False
    return dict(ready=ready, checklist=checklist, dependencies=reports[0]['dependencies'],
                summary=(f"{len(reports)} flight{'s' if len(reports) != 1 else ''} checked. "
                         + ('All required inputs are ready.' if ready else 'Resolve the required items above.')),
                flights=reports,
                cloudcompare_executable=(str(find_cloudcompare_executable(cloudcompare_executable))
                                         if mode == 'merge' and alignment_method == 'automatic'
                                         and find_cloudcompare_executable(cloudcompare_executable)
                                         and cloudcompare_cli_available(str(find_cloudcompare_executable(cloudcompare_executable))) else None))


def cache_directory() -> Path:
    return Path(os.environ.get('ELIOS_COLORIZER_CACHE', str(Path(tempfile.gettempdir()) / 'EliosColorizer' / 'cache')))


def run_colorization(folder: str, output: str, las_override: str | None = None,
                     calibration_override: str | None = None,
                     progress: Callable[[str, float, str], None] | None = None,
                     cancelled: Callable[[], bool] | None = None,
                     *, sample_interval_s: float = 1.0, max_frames: int | None = None,
                     start_s: float | None = None, end_s: float | None = None,
                     experimental: bool = False,
                     maximum_color_distance_m: float | None = None,
                     illumination_balancing: bool = False) -> dict:
    from .flight import discover_source, load_telemetry, iter_observations, inspect_video_coverage
    from .camera import Calibration
    from .colorize import (adaptively_select_observations, colorize_las,
                           ColorizationOptions, ColorizationCancelled,
                           group_nearby_observations)

    import math
    if not math.isfinite(sample_interval_s) or sample_interval_s <= 0:
        raise ValueError('Frame interval must be finite and positive.')
    if max_frames is not None and max_frames < 1:
        raise ValueError('Frame limit must be positive.')
    if any(t is not None and (not math.isfinite(t) or t < 0) for t in (start_s, end_s)):
        raise ValueError('Time window must contain nonnegative video elapsed seconds.')
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError('End time must follow start time.')
    if maximum_color_distance_m is not None and (not math.isfinite(maximum_color_distance_m)
                                                  or not .15 < maximum_color_distance_m <= 40):
        raise ValueError('Maximum colorization distance must be greater than 0.15 m and no more than 40 m.')
    started = time.monotonic()
    source = discover_source(folder, las_override=las_override, calibration_override=calibration_override)
    if (not source.las_path or not source.trajectory_path or not source.calibration_path
            or not source.starnet_path or not source.videos):
        raise ValueError('LAS, matching exported trajectory, RGB videos, native StarNet log, and RGB calibration are all required. Refresh the source checklist.')
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
    emit('Reading telemetry', .003, 'Extracting pose, reported camera pitch, and video frame timing…', force=True)
    telemetry = load_telemetry(source, cache_directory(), cancelled=cancelled,
                              progress=lambda message, fraction: emit('Reading telemetry', .007, message))
    ensure_running()
    if not len(telemetry.frame_times_s):
        raise ValueError('No recorded video frame timing was found.')
    emit('Checking video', .01, 'Comparing encoded RGB frames with recorded FrameSync counters…', force=True)
    video_coverage = inspect_video_coverage(source, telemetry, cancelled)
    ensure_running()
    origin = float(telemetry.frame_times_s[0])
    first = origin + (start_s or 0)
    available_end = (float(telemetry.frame_times_s[-1])
                     if video_coverage.last_available_sync_time_s is None
                     else video_coverage.last_available_sync_time_s)
    last = available_end if end_s is None else min(origin + end_s, available_end)
    if first >= last:
        raise ValueError('Time window does not overlap recorded video timing.')
    expected_frames = max(1, int((last - first) / sample_interval_s) + 1)
    if max_frames is not None:
        expected_frames = min(expected_frames, max_frames)
    emit('Colorizing', .03, f'Projecting sampled RGB frames onto the existing LAS ({expected_frames} planned views)…', force=True)
    raw_frames = iter_observations(source, telemetry, sample_interval_s=sample_interval_s,
                                   max_frames=max_frames, start_s=start_s, end_s=end_s,
                                   cancelled=cancelled, video_coverage=video_coverage)
    logical_cpus = os.cpu_count() or 1
    import laspy
    with laspy.open(source.las_path) as source_reader:
        source_point_count = int(source_reader.header.point_count)
    from .cuda_backend import cuda_checkpoint
    cuda_ready = cuda_checkpoint().available
    tuning = processing_tuning(source_point_count, logical_cpus=logical_cpus,
                               cuda_acceleration=cuda_ready)
    options = ColorizationOptions(experimental_calibration=experimental,
                                  worker_threads=tuning['worker_threads'],
                                  frame_batch_size=tuning['frame_batch_size'],
                                  maximum_color_distance_m=maximum_color_distance_m,
                                  illumination_balancing=illumination_balancing)
    adaptive_stats: dict[str, int] = {}
    selected_frames = adaptively_select_observations(raw_frames, stats=adaptive_stats)
    grouping_stats: dict[str, int] = {}
    frames = group_nearby_observations(selected_frames, options.frame_batch_size,
                                       lookahead_batches=2, stats=grouping_stats)
    cuda_active = False

    def engine_progress(info: dict):
        nonlocal cuda_active
        stage = info['stage']
        if stage == 'acceleration':
            cuda_active = bool(info['cuda_acceleration_active'])
            emit('NVIDIA CUDA', high_water, info['summary'], force=True)
        elif stage == 'illumination':
            emit('Illumination Balancing', high_water, info['reason'], force=True)
        elif stage == 'indexing':
            fraction = info['points_processed'] / max(info['point_count'], 1)
            method = ('staging bounded chunks for CUDA projection' if cuda_active else
                      (f"caching coordinates in memory for {info['worker_threads']} CPU workers"
                       if info['xyz_cache_used'] else 'using the bounded-memory CPU streaming path'))
            emit('Preparing point cloud', .01 + .02 * fraction,
                 f"Reading {info['points_processed']:,} of {info['point_count']:,} points; {method}")
        elif stage == 'writing':
            fraction = info['points_processed'] / max(info['point_count'], 1)
            emit('Saving LAS', .95 + .049 * fraction,
                 f"Writing {info['points_processed']:,} of {info['point_count']:,} points")
        elif stage in ('visibility', 'colorizing'):
            local = info['points_processed'] / max(info['point_count'], 1)
            within_batch = (.5 if stage == 'colorizing' else 0) + .5 * local
            completed = max(0, info['frames_received'] - options.frame_batch_size)
            candidates = adaptive_stats.get('candidates', 0)
            retained = adaptive_stats.get('retained', 0)
            retention = retained / candidates if candidates else 1.
            estimated_selected = max(info['frames_received'], round(expected_frames * retention))
            fraction = min(1., (completed + options.frame_batch_size * within_batch)
                           / max(estimated_selected, 1))
            emit('Checking visibility' if stage == 'visibility' else 'Assigning RGB', .03 + .92 * fraction,
                 _projection_progress_detail(info['frames_used'], estimated_selected,
                                             candidates, expected_frames,
                                             info['points_processed'], info['point_count']))

    result = colorize_las(source.las_path, destination, frames, calibration,
                          options=options, progress=engine_progress, cancelled=cancelled)
    # Report only the inputs needed to reproduce processing; never copy flight
    # metadata wholesale (it can include aircraft authentication information).
    report = {
        'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'experimental_calibration': not calibration.validated,
        'source_las': str(source.las_path), 'videos': [str(p) for p in source.videos],
        'pose_source': str(source.trajectory_path or source.mcap_path),
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
            'maximum_color_distance_m': maximum_color_distance_m,
            'acceleration': result.acceleration,
            'optimization': ('Adaptive view selection and grouping, resource-aware frame batches, bounded CUDA '
                             'projection with automatic CPU fallback, source-order chunk/frustum indexing, and an '
                             'automatic in-memory XYZ cache when memory permits'),
            'adaptive_view_selection': {
                'candidate_views': adaptive_stats.get('candidates', 0),
                'retained_views': adaptive_stats.get('retained', 0),
                'skipped_views': adaptive_stats.get('skipped', 0),
                'translation_threshold_m': .03,
                'rotation_threshold_degrees': 2.0,
                'camera_pitch_threshold_degrees': 1.5,
                'maximum_gap_seconds': 2.0,
            },
            'view_grouping': grouping_stats,
            'spatial_index': {
                'method': 'lossless source-order chunk bounds with conservative camera-frustum rejection',
                'chunk_count': math.ceil(source_point_count / options.chunk_size),
                'point_order_preserved': True,
            },
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
                    total_points=result.point_count, acceleration=result.acceleration,
                    warning=str(exc))
    emit('Complete', 1, f'{result.colored_point_count:,} of {result.point_count:,} points colored '
         f'({result.coverage_fraction:.1%}).', force=True)
    return dict(output=str(destination), report=str(report_path), colored_points=result.colored_point_count,
                total_points=result.point_count, acceleration=result.acceleration)


def _safe_name(value: str, fallback: str) -> str:
    import re
    result = re.sub(r'[^A-Za-z0-9._-]+', '_', value).strip('._-')
    return result[:80] or fallback


def _human_output_stem(value: str) -> str:
    import re
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', value)
    value = re.sub(r'\s+', ' ', value).strip(' .-')
    return value[:70]


def _run_merged_workflow(selections: list[dict[str, str | None]], destination: Path,
                         calibration_override: str | None, *, alignment_method: str,
                         merged_source: str | None, cloudcompare_executable: str | None,
                         maximum_color_distance_m: float | None, progress, cancelled,
                         started: float, flight_checks: list[dict], illumination_balancing: bool = False) -> dict[str, Any]:
    """Color one final geometry with globally competing observations from all flights."""
    from itertools import chain
    from .camera import Calibration
    from .colorize import (adaptively_select_observations, colorize_las, ColorizationOptions,
                           group_nearby_observations)
    from .flight import discover_source, load_telemetry, iter_observations, inspect_video_coverage
    from .merged import (MergedWorkflowError, automatic_align, merge_geometry, selection_transform,
                         spatially_index_geometry, transform_observations, validate_transforms)

    report_path = destination.with_suffix('.report.json')
    if destination.suffix.lower() != '.las':
        raise ValueError('Choose a .las file for merged output.')
    if destination.exists() or report_path.exists():
        raise ValueError('Merged output or its report already exists. Choose a new file name.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    sources = [discover_source(str(item.get('folder') or ''), item.get('las_override'),
                               calibration_override) for item in selections]
    source_las = [source.las_path for source in sources]
    if any(path is None for path in source_las):
        raise ValueError('Every flight needs its matching Inspector export LAS/LAZ.')
    calibration_path = sources[0].calibration_path
    if not calibration_path:
        raise ValueError('Choose a shared camera calibration.')
    calibration = Calibration.load(calibration_path)
    flight_names = _flight_names(selections)

    with tempfile.TemporaryDirectory(prefix='.elios-merged-', dir=destination.parent) as scratch_name:
        scratch = Path(scratch_name)
        if alignment_method == 'manual':
            geometry = Path(merged_source or '').resolve()
            matrices = [selection_transform(item, index) for index, item in enumerate(selections, 1)]
            if progress:
                progress('Validating alignment', .01, 'Checking every flight against the supplied merged geometry…')
            alignment = validate_transforms(source_las, geometry, matrices)
            preparation_share = .05
        elif alignment_method == 'automatic':
            resolved_cloudcompare = find_cloudcompare_executable(cloudcompare_executable)
            if not resolved_cloudcompare or not cloudcompare_cli_available(str(resolved_cloudcompare)):
                raise MergedWorkflowError('CloudCompare CLI is unavailable. Install CloudCompare or browse to CloudCompare.exe.')
            if progress:
                progress('Automatic alignment', .01, 'Discovering pairwise overlaps for a global registration network…')
            def alignment_progress(stage, fraction, message):
                if progress:
                    progress(_name_flight_references(stage, flight_names), .01 + .14 * fraction,
                             _name_flight_references(message, flight_names))
            matrices, alignment = automatic_align(source_las, resolved_cloudcompare, scratch,
                                                   progress=alignment_progress, cancelled=cancelled)
            geometry = scratch / 'automatically_merged_geometry.las'
            def merge_progress(stage, fraction, message):
                if progress:
                    progress(_name_flight_references(stage, flight_names), .15 + .10 * fraction,
                             _name_flight_references(message, flight_names))
            merge_geometry(source_las, matrices, geometry, progress=merge_progress, cancelled=cancelled)
            preparation_share = .25
        else:
            raise ValueError('Merged alignment method must be manual or automatic.')

        indexed_geometry = scratch / 'spatially_indexed_geometry.las'
        index_base = preparation_share
        index_share = .08
        def index_progress(stage, fraction, message):
            if progress:progress(stage, index_base + index_share * fraction, message)
        geometry, spatial_index = spatially_index_geometry(
            geometry, indexed_geometry, progress=index_progress, cancelled=cancelled)
        preparation_share += index_share

        telemetry_rows = []
        expected_views = []
        streams = []
        adaptive_rows = []
        for index, (source, matrix) in enumerate(zip(sources, matrices), 1):
            if cancelled and cancelled():
                raise MergedWorkflowError('Processing cancelled.')
            flight_name = flight_names[index - 1]
            if progress:
                progress(f'{flight_name}: Reading telemetry', preparation_share,
                         'Loading trajectory, camera pitch, and synchronized RGB frame timing…')
            telemetry = load_telemetry(source, cache_directory(), cancelled=cancelled)
            coverage = inspect_video_coverage(source, telemetry, cancelled)
            end = (float(telemetry.frame_times_s[-1]) if coverage.last_available_sync_time_s is None
                   else coverage.last_available_sync_time_s)
            begin = float(telemetry.frame_times_s[0])
            expected_views.append(max(1, int(end - begin) + 1))
            raw = iter_observations(source, telemetry, sample_interval_s=1.0, cancelled=cancelled,
                                    video_coverage=coverage)
            adaptive_stats: dict[str, int] = {}
            transformed = transform_observations(raw, matrix, index)
            streams.append(adaptively_select_observations(transformed, stats=adaptive_stats))
            adaptive_rows.append(adaptive_stats)
            telemetry_rows.append({
                'flight': index, 'folder': str(selections[index - 1].get('folder') or ''),
                'name': flight_name,
                'source_las': str(source.las_path), 'trajectory': str(source.trajectory_path),
                'transform_file': selections[index - 1].get('transform_path'),
                'transform_input': ('entered_values' if selections[index - 1].get('transform_values') else
                                    ('file' if selections[index - 1].get('transform_path') else 'identity')),
                'transform_source_to_merged': matrix.tolist(),
                'time_synchronization': telemetry.timing_diagnostics,
                'warnings': list(dict.fromkeys([*source.warnings, *telemetry.warnings])),
            })
        planned = sum(expected_views)
        import laspy
        with laspy.open(geometry) as geometry_reader:
            merged_point_count = int(geometry_reader.header.point_count)
        from .cuda_backend import cuda_checkpoint
        tuning = processing_tuning(merged_point_count,
                                   cuda_acceleration=cuda_checkpoint().available)
        options = ColorizationOptions(worker_threads=tuning['worker_threads'],
                                      frame_batch_size=tuning['frame_batch_size'], merged_output=True,
                                      maximum_color_distance_m=maximum_color_distance_m,
                                  illumination_balancing=illumination_balancing)
        grouping_stats: dict[str, int] = {}
        grouped_stream = group_nearby_observations(chain.from_iterable(streams),
                                                   options.frame_batch_size,
                                                   lookahead_batches=2,
                                                   stats=grouping_stats)
        color_share = .95 - preparation_share

        engine_high_water = preparation_share
        cuda_active = False

        def engine_emit(stage, fraction, message):
            nonlocal engine_high_water
            engine_high_water = max(engine_high_water, fraction)
            progress(stage, engine_high_water, message)

        def engine_progress(info: dict) -> None:
            nonlocal cuda_active
            stage = info['stage']
            if not progress:
                return
            if stage == 'acceleration':
                cuda_active = bool(info['cuda_acceleration_active'])
                engine_emit('NVIDIA CUDA', preparation_share, info['summary'])
            elif stage == 'illumination':
                engine_emit('Illumination Balancing', preparation_share, info['reason'])
            elif stage == 'indexing':
                local = info['points_processed'] / max(info['point_count'], 1)
                engine_emit('Preparing final geometry', preparation_share + color_share * .02 * local,
                         f"Reading {info['points_processed']:,} / {info['point_count']:,} merged points · "
                         f"{options.frame_batch_size}-view batches · "
                         f"{'CUDA projection' if cuda_active else f'{options.worker_threads} CPU workers'}")
            elif stage in ('visibility', 'colorizing'):
                local = info['points_processed'] / max(info['point_count'], 1)
                within = (.5 if stage == 'colorizing' else 0) + .5 * local
                completed = max(0, info['frames_received'] - options.frame_batch_size)
                candidates = sum(row.get('candidates', 0) for row in adaptive_rows)
                retained = sum(row.get('retained', 0) for row in adaptive_rows)
                retention = retained / candidates if candidates else 1.
                estimated_selected = max(info['frames_received'], round(planned * retention))
                view_fraction = min(1., (completed + options.frame_batch_size * within)
                                    / max(estimated_selected, 1))
                engine_emit('Checking visibility' if stage == 'visibility' else 'Assigning RGB',
                         preparation_share + color_share * (.02 + .93 * view_fraction),
                         _projection_progress_detail(info['frames_used'], estimated_selected,
                                                     candidates, planned,
                                                     info['points_processed'], info['point_count']))
            elif stage == 'writing':
                local = info['points_processed'] / max(info['point_count'], 1)
                engine_emit('Saving merged LAS', .95 + .049 * local,
                         f"Writing {info['points_processed']:,} / {info['point_count']:,} points")

        result = colorize_las(geometry, destination, grouped_stream, calibration,
                              options=options, progress=engine_progress, cancelled=cancelled)

    payload = {
        'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'merge', 'alignment_method': alignment_method,
        'merged_geometry_source': str(merged_source) if alignment_method == 'manual' else 'generated locally',
        'alignment': alignment, 'flights': telemetry_rows, 'result': result.to_dict(),
        'maximum_color_distance_m': maximum_color_distance_m,
        'processing_configuration': {
            'point_count': merged_point_count,
            'frame_batch_size': options.frame_batch_size,
            'worker_threads': options.worker_threads,
            'acceleration': result.acceleration,
            'optimization': ('Merged-cloud batches reuse each XYZ pass across more views; projection, occlusion, '
                             'RGB sampling, and observation scoring run in bounded CUDA batches with automatic '
                             'CPU fallback.'),
            'adaptive_view_selection': {
                'candidate_views': sum(row.get('candidates', 0) for row in adaptive_rows),
                'retained_views': sum(row.get('retained', 0) for row in adaptive_rows),
                'skipped_views': sum(row.get('skipped', 0) for row in adaptive_rows),
                'translation_threshold_m': .03, 'rotation_threshold_degrees': 2.0,
                'camera_pitch_threshold_degrees': 1.5, 'maximum_gap_seconds': 2.0,
            },
            'view_grouping': grouping_stats,
            'spatial_index': spatial_index,
        },
        'selection_policy': ('All flight observations compete on the same final points. A color is replaced only '
                             'when the new observation has a higher projection confidence score.'),
        'uncolored_points': 'Retained with RGB=(0,0,0), Colorized=0.',
        'elapsed_seconds': round(time.monotonic() - started, 2),
    }
    with report_path.open('x', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)
    if progress:
        progress('Complete', 1., f'{result.colored_point_count:,} of {result.point_count:,} points colored.')
    return dict(output=str(destination), report=str(report_path),
                colored_points=result.colored_point_count, total_points=result.point_count,
                acceleration=result.acceleration)


def run_workflow(flights: Iterable[dict[str, str | None]], output: str,
                 calibration_override: str | None = None, *, mode: str = 'separate',
                 alignment_method: str = 'manual', merged_source: str | None = None,
                 cloudcompare_executable: str | None = None,
                 maximum_color_distance_m: float | None = None,
                 illumination_balancing: bool = False,
                 progress: Callable[[str, float, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Colorize one or more flights, optionally align and fuse colored results."""
    selections = list(flights)
    if not selections:
        raise ValueError('Add at least one flight.')
    duplicate_names = _duplicate_flight_names(selections)
    if duplicate_names:
        raise ValueError('Every flight needs a unique name before processing can begin.')
    flight_names = _flight_names(selections)
    if mode not in ('separate', 'merge'):
        raise ValueError('Processing mode must be separate or merge.')
    if mode == 'merge' and len(selections) < 2:
        raise ValueError('Merged mode requires at least two flights.')
    inspection = inspect_sources(selections, calibration_override, mode=mode,
                                 alignment_method=alignment_method, merged_source=merged_source,
                                 cloudcompare_executable=cloudcompare_executable)
    if not inspection['ready']:
        raise ValueError('One or more flights are missing required data. Refresh the checklist for details.')
    destination = Path(output).resolve()
    flight_checks = inspection.get('flights') or []
    work_weights = [max(1, int(item.get('point_count') or 0))
                    * max(1, int(item.get('estimated_views') or 0))
                    for item in flight_checks]
    if len(work_weights) != len(selections):
        work_weights = [1] * len(selections)
    total_work = sum(work_weights)
    work_bases = [sum(work_weights[:index]) / total_work for index in range(len(work_weights))]
    work_scales = [weight / total_work for weight in work_weights]

    # The familiar single-flight path is intentionally unchanged.
    if mode == 'separate' and len(selections) == 1:
        item = selections[0]
        return run_colorization(str(item.get('folder') or ''), str(destination),
            item.get('las_override'), calibration_override, progress, cancelled,
            maximum_color_distance_m=maximum_color_distance_m,
                                  illumination_balancing=illumination_balancing)

    started = time.monotonic()
    if mode == 'separate':
        if destination.exists() and not destination.is_dir():
            raise ValueError('Choose an output folder for separate multi-flight results.')
        destination.mkdir(parents=True, exist_ok=True)
        reports: list[dict[str, Any]] = []
        names: set[str] = set()
        planned: list[Path] = []
        for index, item in enumerate(selections, 1):
            custom_name = _human_output_stem(str(item.get('name') or ''))
            if custom_name.casefold() == f'flight {index}'.casefold():
                custom_name = ''
            stem = f'Flight {index:02d}' + (f' - {custom_name}' if custom_name else '')
            name = f'{stem} - Colorized.las'
            while name.lower() in names:
                name = f'{stem} ({len(names) + 1}) - Colorized.las'
            names.add(name.lower())
            planned.append(destination / name)
        workflow_report = destination / 'multi_flight_report.json'
        conflicts = [path for path in [*planned, workflow_report] if path.exists()]
        if conflicts:
            raise ValueError(f'Output already exists: {conflicts[0].name}. Choose another output folder.')
        for index, (item, flight_output) in enumerate(zip(selections, planned), 1):
            base = work_bases[index - 1]
            scale = work_scales[index - 1]
            def flight_progress(stage: str, fraction: float, message: str, *, _name=flight_names[index - 1],
                                _base=base, _scale=scale) -> None:
                if progress:
                    progress(f'{_name}: {stage}', _base + _scale * fraction, message)
            result = run_colorization(str(item.get('folder') or ''), str(flight_output),
                item.get('las_override'), calibration_override, flight_progress, cancelled,
                maximum_color_distance_m=maximum_color_distance_m,
                                  illumination_balancing=illumination_balancing)
            reports.append(result)
        total = sum(int(result['total_points']) for result in reports)
        colored = sum(int(result['colored_points']) for result in reports)
        payload = {
            'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'mode': mode, 'maximum_color_distance_m': maximum_color_distance_m,
            'elapsed_seconds': round(time.monotonic() - started, 2), 'results': reports,
        }
        with workflow_report.open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2)
        return dict(output=str(destination), outputs=[item['output'] for item in reports],
                    report=str(workflow_report), colored_points=colored, total_points=total)

    return _run_merged_workflow(selections, destination, calibration_override,
        alignment_method=alignment_method, merged_source=merged_source,
        cloudcompare_executable=cloudcompare_executable,
        maximum_color_distance_m=maximum_color_distance_m, progress=progress,
        cancelled=cancelled, started=started, flight_checks=flight_checks,
        illumination_balancing=illumination_balancing)
