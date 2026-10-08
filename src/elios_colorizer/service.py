"""Application boundary shared by the GUI and command-line tools.

Keep third-party imports lazy so an incomplete development installation can
still explain which dependencies are missing. Flight files are never edited.
"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
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
import threading
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
    """Compatibility wrapper around the centralized resource planner."""
    if available_memory_bytes is None:
        try:
            from .colorize import _available_memory_bytes
            available_memory_bytes = _available_memory_bytes()
        except Exception:
            available_memory_bytes = None
    from .runtime import plan_resources
    plan = plan_resources(point_count, available_memory_bytes=available_memory_bytes,
                          logical_cpus=logical_cpus, cuda_available=cuda_acceleration,
                          cuda_devices=1 if cuda_acceleration else 0)
    return {'worker_threads': plan.worker_threads, 'frame_batch_size': plan.frame_batch_size}


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
    duration_s = 0.0
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
                point_count=point_count, estimated_views=estimated_views,
                video_duration_s=duration_s)


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
                     illumination_balancing: bool = False,
                     multi_frame_fusion: bool = False,
                     image_border_fraction: float = .02,
                     minimum_sharpness: float = 2.0,
                     resource_settings: dict | None = None,
                     _worker_threads: int | None = None,
                     _memory_budget_bytes: int | None = None,
                     _cuda_gate: threading.Lock | None = None,
                     _cuda_device_index: int = 0) -> dict:
    from .flight import discover_source, load_telemetry, iter_observations, inspect_video_coverage
    from .camera import Calibration
    from .colorize import (adaptively_select_observations, colorize_las,
                           ColorizationOptions, ColorizationCancelled,
                           group_nearby_observations)

    import math
    if not math.isfinite(sample_interval_s) or not 1 / 30 <= sample_interval_s <= 4.0:
        raise ValueError('Frame sampling frequency must be between 0.25 and 30 frames per second.')
    if not math.isfinite(image_border_fraction) or not 0 <= image_border_fraction <= .15:
        raise ValueError('Image edge exclusion must be between 0 and 15 percent.')
    if not math.isfinite(minimum_sharpness) or not 0 <= minimum_sharpness <= 50:
        raise ValueError('Minimum frame sharpness must be between 0 and 50.')
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
    checkpoint = cuda_checkpoint()
    cuda_ready = checkpoint.available
    from .colorize import _available_memory_bytes
    from .runtime import plan_resources
    resource_plan = plan_resources(
        source_point_count, available_memory_bytes=_available_memory_bytes(),
        logical_cpus=logical_cpus, cuda_available=cuda_ready,
        cuda_devices=checkpoint.device_count if cuda_ready else 0,
        fusion=multi_frame_fusion,available_vram_bytes=getattr(checkpoint,'free_memory_bytes',None),preferences=resource_settings)
    cuda_ready=resource_plan.cuda_available
    tuning = {'worker_threads': resource_plan.worker_threads,
              'frame_batch_size': resource_plan.frame_batch_size}
    if _worker_threads is not None:
        tuning['worker_threads'] = max(1, int(_worker_threads))
    options = ColorizationOptions(experimental_calibration=experimental,
                                  worker_threads=tuning['worker_threads'],
                                  frame_batch_size=tuning['frame_batch_size'],
                                  maximum_color_distance_m=maximum_color_distance_m,
                                  illumination_balancing=illumination_balancing,
                                  multi_frame_fusion=multi_frame_fusion,
                                  image_border_fraction=image_border_fraction,
                                  minimum_sharpness=minimum_sharpness,
                                  cuda_device_index=_cuda_device_index,
                                  cuda_enabled=resource_plan.cuda_available,
                                  memory_budget_bytes=(min(resource_plan.available_memory_bytes,_memory_budget_bytes)
                                                       if resource_plan.available_memory_bytes is not None and _memory_budget_bytes is not None
                                                       else _memory_budget_bytes or resource_plan.available_memory_bytes))
    adaptive_stats: dict[str, int] = {}
    adaptive_thresholds = ({'translation_m': .015, 'rotation_degrees': 1.0,
                            'pitch_degrees': .75, 'maximum_gap_s': .5}
                           if multi_frame_fusion else
                           {'translation_m': .03, 'rotation_degrees': 2.0,
                            'pitch_degrees': 1.5, 'maximum_gap_s': 2.0})
    selected_frames = adaptively_select_observations(raw_frames, stats=adaptive_stats,
                                                      **adaptive_thresholds)
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
            method = (('staging bounded chunks for CUDA visibility and CPU fusion'
                       if multi_frame_fusion else 'staging bounded chunks for CUDA projection') if cuda_active else
                      (f"caching coordinates in memory for {info['worker_threads']} CPU workers"
                       if info['xyz_cache_used'] else 'using the bounded-memory CPU streaming path'))
            emit('Preparing point cloud', .01 + .02 * fraction,
                 f"Reading {info['points_processed']:,} of {info['point_count']:,} points; {method}")
        elif stage == 'fusion':
            fraction = info['points_processed'] / max(info['point_count'], 1)
            emit('Fusing RGB observations', .945 + .005 * fraction,
                 'Rejecting color outliers and selecting robust multi-frame colors')
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

    # Independent flights may prepare concurrently, but one projection job owns
    # a single CUDA device at a time. CPU-only jobs do not take this gate.
    gate = _cuda_gate if cuda_ready and _cuda_gate is not None else nullcontext()
    from .runtime import EagerPrefetch
    prepared_frames = EagerPrefetch(frames, depth=2)
    try:
        with gate:
            result = colorize_las(source.las_path, destination, prepared_frames, calibration,
                                  options=options, progress=engine_progress, cancelled=cancelled)
    finally:
        prepared_frames.close()
    elapsed_seconds = round(time.monotonic() - started, 2)
    illumination = result.illumination_balancing or {}
    # Report only the inputs needed to reproduce processing; never copy flight
    # metadata wholesale (it can include aircraft authentication information).
    report = {
        'summary': {
            'application_version': __version__, 'mode': 'single_flight',
            'output_las': str(destination), 'elapsed_seconds': elapsed_seconds,
            'coverage': {'colored_points': result.colored_point_count, 'total_points': result.point_count,
                         'percent': round(result.coverage_fraction * 100, 2)},
            'frames': {'sampling_frequency_hz': 1 / sample_interval_s,
                       'received': result.frames_received, 'used': result.frames_used,
                       'rejected': result.frames_rejected},
            'user_settings': {'image_edge_exclusion_percent': image_border_fraction * 100,
                              'blur_rejection': ('Off' if minimum_sharpness == 0 else
                                                 ('Normal' if minimum_sharpness <= 2 else 'Strong')),
                              'illumination_balancing_requested': illumination_balancing,
                              'illumination_balancing_applied': bool(illumination.get('applied')),
                              'multi_frame_fusion_requested': multi_frame_fusion,
                              'multi_frame_fusion_applied': bool((result.multi_frame_fusion or {}).get('applied')),
                              'maximum_color_distance_m': maximum_color_distance_m,
                              'time_range_relative_seconds': {'enabled': start_s is not None or end_s is not None,
                                                              'start': start_s or 0., 'end': end_s}},
            'warnings_count': len(set([*source.warnings, *telemetry.warnings])),
        },
        'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': elapsed_seconds,
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
            'sample_frequency_hz': 1 / sample_interval_s,
            'image_edge_exclusion_percent': image_border_fraction * 100,
            'minimum_sharpness': minimum_sharpness,
            'multi_frame_fusion': multi_frame_fusion,
            'time_range_relative_seconds': {'enabled': start_s is not None or end_s is not None,
                                            'start': start_s or 0., 'end': end_s},
            'time_range_applied_seconds': {'start': first-origin, 'end': last-origin},
            'xyz_cache_used': result.xyz_cache_used,
            'worker_threads': options.worker_threads,
            'logical_cpus_detected': logical_cpus,
            'maximum_color_distance_m': maximum_color_distance_m,
            'acceleration': result.acceleration,
            'resource_plan': {**resource_plan.to_dict(),
                              'workers_for_this_job': options.worker_threads},
            'resource_settings': resource_settings or {'use_recommended': True},
            'performance': result.performance,
            'optimization': ('Adaptive view selection and grouping, resource-aware frame batches, bounded CUDA '
                             'projection with automatic CPU fallback, source-order chunk/frustum indexing, and an '
                             'automatic in-memory XYZ cache when memory permits'),
            'adaptive_view_selection': {
                'candidate_views': adaptive_stats.get('candidates', 0),
                'retained_views': adaptive_stats.get('retained', 0),
                'skipped_views': adaptive_stats.get('skipped', 0),
                'translation_threshold_m': adaptive_thresholds['translation_m'],
                'rotation_threshold_degrees': adaptive_thresholds['rotation_degrees'],
                'camera_pitch_threshold_degrees': adaptive_thresholds['pitch_degrees'],
                'maximum_gap_seconds': adaptive_thresholds['maximum_gap_s'],
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
                    multi_frame_fusion=result.multi_frame_fusion,
                    processing_strategy='single_flight', concurrent_flight_jobs=1,
                    cuda_devices_detected=resource_plan.cuda_devices,
                    warning=str(exc))
    emit('Complete', 1, f'{result.colored_point_count:,} of {result.point_count:,} points colored '
         f'({result.coverage_fraction:.1%}).', force=True)
    return dict(output=str(destination), report=str(report_path), colored_points=result.colored_point_count,
                total_points=result.point_count, acceleration=result.acceleration,
                multi_frame_fusion=result.multi_frame_fusion,
                processing_strategy='single_flight', concurrent_flight_jobs=1,
                cuda_devices_detected=resource_plan.cuda_devices)


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
                         started: float, flight_checks: list[dict], illumination_balancing: bool = False,
                         multi_frame_fusion: bool = False,
                         sample_interval_s: float = 1.0, image_border_fraction: float = .02,
                         minimum_sharpness: float = 2.0,
                         start_s: float | None = None, end_s: float | None = None,
                         resource_settings: dict | None = None) -> dict[str, Any]:
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
    with ThreadPoolExecutor(max_workers=min(len(selections), 8),
                            thread_name_prefix='elios-merged-source') as pool:
        sources = list(pool.map(
            lambda item: discover_source(str(item.get('folder') or ''), item.get('las_override'),
                                         calibration_override), selections))
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
        adaptive_thresholds = ({'translation_m': .015, 'rotation_degrees': 1.0,
                                'pitch_degrees': .75, 'maximum_gap_s': .5}
                               if multi_frame_fusion else
                           {'translation_m': .03, 'rotation_degrees': 2.0,
                            'pitch_degrees': 1.5, 'maximum_gap_s': 2.0})

        def prepare_flight(index_source):
            index, source = index_source
            flight_name = flight_names[index - 1]
            if progress:
                progress(f'{flight_name}: Reading telemetry', preparation_share,
                         'Loading trajectory, camera pitch, and synchronized RGB frame timing…')
            telemetry = load_telemetry(source, cache_directory(), cancelled=cancelled)
            return telemetry, inspect_video_coverage(source, telemetry, cancelled)

        with ThreadPoolExecutor(max_workers=min(len(sources), 8),
                                thread_name_prefix='elios-merged-prepare') as pool:
            prepared_flights = list(pool.map(prepare_flight, enumerate(sources, 1)))

        from .runtime import prefetch
        for index, ((source, matrix), prepared_flight) in enumerate(
                zip(zip(sources, matrices), prepared_flights), 1):
            if cancelled and cancelled():
                raise MergedWorkflowError('Processing cancelled.')
            flight_name = flight_names[index - 1]
            telemetry, coverage = prepared_flight
            end = (float(telemetry.frame_times_s[-1]) if coverage.last_available_sync_time_s is None
                   else coverage.last_available_sync_time_s)
            begin = float(telemetry.frame_times_s[0])
            selected_begin=begin+(start_s or 0);selected_end=end if end_s is None else min(begin+end_s,end)
            if selected_begin>=selected_end:
                raise ValueError(f'Time range does not overlap recorded video timing for {flight_name}.')
            expected_views.append(max(1, int((selected_end - selected_begin) / sample_interval_s) + 1))
            raw = iter_observations(source, telemetry, sample_interval_s=sample_interval_s, cancelled=cancelled,
                                    start_s=start_s,end_s=end_s,video_coverage=coverage)
            adaptive_stats: dict[str, int] = {}
            transformed = transform_observations(raw, matrix, index)
            streams.append(prefetch(
                adaptively_select_observations(transformed, stats=adaptive_stats,
                                                **adaptive_thresholds), 2))
            adaptive_rows.append(adaptive_stats)
            telemetry_rows.append({
                'flight': index, 'folder': str(selections[index - 1].get('folder') or ''),
                'name': flight_name,
                'source_las': str(source.las_path), 'trajectory': str(source.trajectory_path),
                'transform_file': selections[index - 1].get('transform_path'),
                'transform_input': ('entered_values' if selections[index - 1].get('transform_values') else
                                    ('file' if selections[index - 1].get('transform_path') else 'identity')),
                'transform_source_to_merged': matrix.tolist(),
                'time_range_relative_seconds': {'enabled': start_s is not None or end_s is not None,
                                                'start': start_s or 0., 'end': end_s},
                'time_range_applied_seconds': {'start': selected_begin-begin, 'end': selected_end-begin},
                'time_synchronization': telemetry.timing_diagnostics,
                'warnings': list(dict.fromkeys([*source.warnings, *telemetry.warnings])),
            })
        planned = sum(expected_views)
        import laspy
        with laspy.open(geometry) as geometry_reader:
            merged_point_count = int(geometry_reader.header.point_count)
        from .cuda_backend import cuda_checkpoint
        checkpoint = cuda_checkpoint()
        from .colorize import _available_memory_bytes
        from .runtime import plan_resources
        resource_plan = plan_resources(
            merged_point_count, available_memory_bytes=_available_memory_bytes(),
            cuda_available=checkpoint.available,
            cuda_devices=checkpoint.device_count if checkpoint.available else 0,
            flight_count=len(selections), fusion=multi_frame_fusion,
            available_vram_bytes=getattr(checkpoint,'free_memory_bytes',None),preferences=resource_settings)
        tuning = {'worker_threads': resource_plan.worker_threads,
                  'frame_batch_size': resource_plan.frame_batch_size}
        options = ColorizationOptions(worker_threads=tuning['worker_threads'],
                                      frame_batch_size=tuning['frame_batch_size'], merged_output=True,
                                      maximum_color_distance_m=maximum_color_distance_m,
                                      illumination_balancing=illumination_balancing,
                                      multi_frame_fusion=multi_frame_fusion,
                                      image_border_fraction=image_border_fraction,
                                      minimum_sharpness=minimum_sharpness,
                                      cuda_enabled=resource_plan.cuda_available,
                                      memory_budget_bytes=resource_plan.available_memory_bytes)
        grouping_stats: dict[str, int] = {}
        # Keep the historical flight-order tie policy for the shared path. The
        # map/reduce path below uses the same order when qualities are equal.
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
                         f"{('CUDA visibility + CPU fusion' if multi_frame_fusion else 'CUDA projection') if cuda_active else f'{options.worker_threads} CPU workers'}")
            elif stage == 'fusion':
                local = info['points_processed'] / max(info['point_count'], 1)
                engine_emit('Fusing RGB observations', .945 + .005 * local,
                            'Rejecting color outliers and selecting robust multi-frame colors')
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

        parallel_strategy = 'global_observation_stream'
        parallel_reason = None
        map_results = []
        map_grouping_rows: list[dict[str, int]] = [{} for _ in streams]
        candidate_bytes = merged_point_count * 16 * len(streams)
        can_map_reduce = (not multi_frame_fusion and not illumination_balancing
                          and shutil.disk_usage(scratch).free >= candidate_bytes + 512 * 1024**2)
        if multi_frame_fusion:
            parallel_reason = ('Multi-frame fusion requires shared cross-flight observations; used the global '
                               'observation stream to preserve identical fusion quality.')
        elif illumination_balancing:
            parallel_reason = ('Illumination Balancing requires cross-flight overlap evidence; used the global '
                               'observation stream to preserve identical correction quality.')
        elif not multi_frame_fusion and not can_map_reduce:
            parallel_reason = (f'Insufficient temporary disk space for {candidate_bytes / 1024**3:.1f} GiB '
                               'of deterministic flight candidates; used the global streaming path.')

        if can_map_reduce:
            from .runtime import EagerPrefetch
            from .colorize import reduce_candidate_shards
            parallel_strategy = 'parallel_flight_map_reduce'
            device_count = resource_plan.cuda_devices if resource_plan.cuda_available else 0
            device_gates = [threading.Lock() for _ in range(max(1, device_count))]
            job_progress = [0.] * len(streams)
            progress_lock = threading.Lock()
            progress_high_water = preparation_share

            def map_one(index_stream):
                index, stream = index_stream
                device = index % device_count if device_count else 0
                grouping: dict[str, int] = {}
                map_grouping_rows[index] = grouping
                grouped = group_nearby_observations(stream, options.frame_batch_size,
                                                    lookahead_batches=2, stats=grouping)
                prepared = EagerPrefetch(grouped, 2)

                def map_progress(info):
                    nonlocal progress_high_water
                    stage = info['stage']
                    if stage == 'indexing':
                        local = .02 * info['points_processed'] / max(info['point_count'], 1)
                    elif stage in ('visibility', 'colorizing'):
                        point_fraction = info['points_processed'] / max(info['point_count'], 1)
                        within = (.5 if stage == 'colorizing' else 0.) + .5 * point_fraction
                        completed_views = max(0, info['frames_received'] - options.frame_batch_size)
                        local = .02 + .97 * min(1., (completed_views + options.frame_batch_size * within)
                                                / max(expected_views[index], 1))
                    elif stage == 'candidate_complete':
                        local = 1.
                    else:
                        return
                    with progress_lock:
                        job_progress[index] = max(job_progress[index], local)
                        aggregate = sum(job_progress[i] * expected_views[i] for i in range(len(streams))) / max(planned, 1)
                        fraction = preparation_share + (.90 - preparation_share) * aggregate
                        progress_high_water = max(progress_high_water, fraction)
                        if progress:
                            hardware = (f'CUDA GPU {device + 1}' if resource_plan.cuda_available
                                        else f'{resource_plan.workers_per_flight} CPU workers')
                            unfinished = sum(value < 1 for value in job_progress)
                            progress(f'{flight_names[index]}: Parallel flight colorization', progress_high_water,
                                     f'{hardware} · {unfinished} flights unfinished · flight {local * 100:.1f}%')

                job_options = replace(
                    options, worker_threads=resource_plan.workers_per_flight,
                    cuda_device_index=device)
                shard = scratch / 'candidates' / f'flight-{index + 1:03d}'
                unused = scratch / f'flight-{index + 1:03d}-unused.las'
                gate = device_gates[device] if resource_plan.cuda_available else nullcontext()
                try:
                    with gate:
                        return colorize_las(geometry, unused, prepared, calibration,
                                            options=job_options, progress=map_progress,
                                            cancelled=cancelled, candidate_directory=shard)
                finally:
                    prepared.close()

            if progress:
                mode_text = (f'{device_count} CUDA GPUs' if device_count > 1 else
                             ('CUDA with concurrent CPU preparation' if device_count == 1 else
                              f'{resource_plan.concurrent_flights} concurrent CPU jobs'))
                progress('Planning parallel colorization', preparation_share,
                         f'Creating {len(streams)} independent flight candidate jobs using {mode_text}.')
            with ThreadPoolExecutor(max_workers=resource_plan.concurrent_flights,
                                    thread_name_prefix='elios-merged-flight') as pool:
                futures = [pool.submit(map_one, item) for item in enumerate(streams)]
                try:
                    map_results = [future.result() for future in futures]
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise

            def reduction_progress(info):
                if progress and info['stage'] == 'reducing_candidates':
                    local = info['points_processed'] / max(info['point_count'], 1)
                    progress('Selecting final colors', .90 + .099 * local,
                             f"Comparing all flights for {info['points_processed']:,} / {info['point_count']:,} points")

            result = reduce_candidate_shards(
                geometry, destination, [item.output_path for item in map_results],
                chunk_size=options.chunk_size, progress=reduction_progress, cancelled=cancelled)
            illumination = {
                'requested': illumination_balancing,
                'applied': any(bool((item.illumination_balancing or {}).get('applied')) for item in map_results),
                'accepted_batches': sum(int((item.illumination_balancing or {}).get('accepted_batches', 0))
                                        for item in map_results),
                'skipped_batches': sum(int((item.illumination_balancing or {}).get('skipped_batches', 0))
                                       for item in map_results),
            }
            result = replace(result, illumination_balancing=illumination,
                             multi_frame_fusion={'requested': False, 'applied': False})
        else:
            result = colorize_las(geometry, destination, grouped_stream, calibration,
                                  options=options, progress=engine_progress, cancelled=cancelled)

    elapsed_seconds = round(time.monotonic() - started, 2)
    illumination = result.illumination_balancing or {}
    parallel_details = {
        'strategy': parallel_strategy,
        'reason': parallel_reason,
        'concurrent_flight_jobs': (resource_plan.concurrent_flights
                                   if parallel_strategy == 'parallel_flight_map_reduce' else 1),
        'cuda_devices_detected': resource_plan.cuda_devices,
        'candidate_shards': len(map_results),
        'temporary_candidate_gib': round(candidate_bytes / 1024**3, 2) if map_results else 0.,
        'per_flight': [
            {'flight': index + 1,
             'frames_used': item.frames_used,
             'colored_points': item.colored_point_count,
             'acceleration': item.acceleration,
             'performance': item.performance}
            for index, item in enumerate(map_results)
        ],
    }
    payload = {
        'summary': {
            'application_version': __version__, 'mode': 'merged', 'flight_count': len(selections),
            'output_las': str(destination), 'elapsed_seconds': elapsed_seconds,
            'coverage': {'colored_points': result.colored_point_count, 'total_points': result.point_count,
                         'percent': round(result.coverage_fraction * 100, 2)},
            'frames': {'sampling_frequency_hz': 1 / sample_interval_s,
                       'received': result.frames_received, 'used': result.frames_used,
                       'rejected': result.frames_rejected},
            'user_settings': {'image_edge_exclusion_percent': image_border_fraction * 100,
                              'blur_rejection': ('Off' if minimum_sharpness == 0 else
                                                 ('Normal' if minimum_sharpness <= 2 else 'Strong')),
                              'illumination_balancing_requested': illumination_balancing,
                              'illumination_balancing_applied': bool(illumination.get('applied')),
                              'multi_frame_fusion_requested': multi_frame_fusion,
                              'multi_frame_fusion_applied': bool((result.multi_frame_fusion or {}).get('applied')),
                              'maximum_color_distance_m': maximum_color_distance_m,
                              'time_range_relative_seconds': {'enabled': start_s is not None or end_s is not None,
                                                              'start': start_s or 0., 'end': end_s}},
            'warnings_count': sum(len(row.get('warnings', [])) for row in telemetry_rows),
            'processing_strategy': parallel_strategy,
        },
        'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'merge', 'alignment_method': alignment_method,
        'merged_geometry_source': str(merged_source) if alignment_method == 'manual' else 'generated locally',
        'alignment': alignment, 'flights': telemetry_rows, 'result': result.to_dict(),
        'maximum_color_distance_m': maximum_color_distance_m,
        'processing_configuration': {
            'point_count': merged_point_count,
            'sample_interval_seconds': sample_interval_s,
            'sample_frequency_hz': 1 / sample_interval_s,
            'image_edge_exclusion_percent': image_border_fraction * 100,
            'minimum_sharpness': minimum_sharpness,
            'multi_frame_fusion': multi_frame_fusion,
            'time_range_relative_seconds': {'enabled': start_s is not None or end_s is not None,
                                            'start': start_s or 0., 'end': end_s},
            'frame_batch_size': options.frame_batch_size,
            'worker_threads': options.worker_threads,
            'acceleration': result.acceleration,
            'resource_plan': resource_plan.to_dict(),
            'resource_settings': resource_settings or {'use_recommended': True},
            'performance': result.performance,
            'parallel_execution': parallel_details,
            'optimization': (('Flights independently map their best observations to the same immutable merged '
                              'point indices; a deterministic reducer selects the global winner.')
                             if parallel_strategy == 'parallel_flight_map_reduce' else
                             ('Merged-cloud batches reuse each XYZ pass across more views; projection, occlusion, '
                              'RGB sampling, and observation scoring run in bounded CUDA batches with automatic '
                              'CPU fallback.')),
            'adaptive_view_selection': {
                'candidate_views': sum(row.get('candidates', 0) for row in adaptive_rows),
                'retained_views': sum(row.get('retained', 0) for row in adaptive_rows),
                'skipped_views': sum(row.get('skipped', 0) for row in adaptive_rows),
                'translation_threshold_m': adaptive_thresholds['translation_m'],
                'rotation_threshold_degrees': adaptive_thresholds['rotation_degrees'],
                'camera_pitch_threshold_degrees': adaptive_thresholds['pitch_degrees'],
                'maximum_gap_seconds': adaptive_thresholds['maximum_gap_s'],
            },
            'view_grouping': ({'per_flight': map_grouping_rows,
                               'groups': sum(row.get('groups', 0) for row in map_grouping_rows),
                               'reordered_views': sum(row.get('reordered_views', 0) for row in map_grouping_rows)}
                              if map_grouping_rows else grouping_stats),
            'spatial_index': spatial_index,
        },
        'selection_policy': (('Up to five strong observations per point are fused with luminance-outlier rejection '
                              'and a weighted observed-color medoid; insufficient observations use the best view.')
                             if multi_frame_fusion else
                             ('All flight observations compete on the same final points. A color is replaced only '
                              'when the new observation has a higher projection confidence score.')),
        'uncolored_points': 'Retained with RGB=(0,0,0), Colorized=0.',
        'elapsed_seconds': elapsed_seconds,
    }
    with report_path.open('x', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)
    if progress:
        progress('Complete', 1., f'{result.colored_point_count:,} of {result.point_count:,} points colored.')
    return dict(output=str(destination), report=str(report_path),
                colored_points=result.colored_point_count, total_points=result.point_count,
                acceleration=result.acceleration,multi_frame_fusion=result.multi_frame_fusion,
                processing_strategy=parallel_strategy,
                concurrent_flight_jobs=parallel_details['concurrent_flight_jobs'],
                cuda_devices_detected=resource_plan.cuda_devices)


def run_workflow(flights: Iterable[dict[str, str | None]], output: str,
                 calibration_override: str | None = None, *, mode: str = 'separate',
                 alignment_method: str = 'manual', merged_source: str | None = None,
                 cloudcompare_executable: str | None = None,
                 maximum_color_distance_m: float | None = None,
                 illumination_balancing: bool = False,
                 multi_frame_fusion: bool = False,
                 sample_interval_s: float = 1.0,
                 image_border_fraction: float = .02,
                 minimum_sharpness: float = 2.0,
                 start_s: float | None = None,
                 end_s: float | None = None,
                 resource_settings: dict | None = None,
                 progress: Callable[[str, float, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Colorize one or more flights, optionally align and fuse colored results."""
    selections = list(flights)
    import math
    if not math.isfinite(sample_interval_s) or not 1 / 30 <= sample_interval_s <= 4.0:
        raise ValueError('Frame sampling frequency must be between 0.25 and 30 frames per second.')
    if not math.isfinite(image_border_fraction) or not 0 <= image_border_fraction <= .15:
        raise ValueError('Image edge exclusion must be between 0 and 15 percent.')
    if not math.isfinite(minimum_sharpness) or not 0 <= minimum_sharpness <= 50:
        raise ValueError('Minimum frame sharpness must be between 0 and 50.')
    if any(t is not None and (not math.isfinite(t) or t < 0) for t in (start_s, end_s)):
        raise ValueError('Time window must contain nonnegative video elapsed seconds.')
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise ValueError('End time must follow start time.')
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

    # The familiar single-flight path is intentionally unchanged.
    if mode == 'separate' and len(selections) == 1:
        item = selections[0]
        return run_colorization(str(item.get('folder') or ''), str(destination),
            item.get('las_override'), calibration_override, progress, cancelled,
            maximum_color_distance_m=maximum_color_distance_m,
            illumination_balancing=illumination_balancing,sample_interval_s=sample_interval_s,
            multi_frame_fusion=multi_frame_fusion,
            image_border_fraction=image_border_fraction,minimum_sharpness=minimum_sharpness,
            start_s=start_s,end_s=end_s,resource_settings=resource_settings)

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
        from .colorize import _available_memory_bytes
        from .cuda_backend import cuda_checkpoint
        from .runtime import plan_resources
        checkpoint = cuda_checkpoint()
        scheduler = plan_resources(
            max((int(item.get('point_count') or 0) for item in flight_checks), default=0),
            available_memory_bytes=_available_memory_bytes(),
            cuda_available=checkpoint.available,
            cuda_devices=checkpoint.device_count if checkpoint.available else 0,
            flight_count=len(selections), fusion=multi_frame_fusion,
            available_vram_bytes=getattr(checkpoint,'free_memory_bytes',None),preferences=resource_settings)
        cuda_device_count = scheduler.cuda_devices if scheduler.cuda_available else 0
        cuda_gates = [threading.Lock() for _ in range(max(1, cuda_device_count))]
        job_progress = [0.] * len(selections)
        progress_lock = threading.Lock()
        progress_high_water = 0.

        def process_flight(index: int, item: dict, flight_output: Path) -> dict:
            device = (index - 1) % cuda_device_count if cuda_device_count else 0
            def flight_progress(stage: str, fraction: float, message: str, *, _name=flight_names[index - 1],
                                _slot=index - 1) -> None:
                nonlocal progress_high_water
                if progress:
                    with progress_lock:
                        job_progress[_slot] = max(job_progress[_slot], fraction)
                        aggregate = sum(value * weight for value, weight in zip(job_progress, work_weights)) / total_work
                        progress_high_water = max(progress_high_water, aggregate)
                        progress(f'{_name}: {stage}', progress_high_water,
                                 f'{message} · flight {job_progress[_slot] * 100:.1f}%')
            return run_colorization(str(item.get('folder') or ''), str(flight_output),
                item.get('las_override'), calibration_override, flight_progress, cancelled,
                maximum_color_distance_m=maximum_color_distance_m,
                illumination_balancing=illumination_balancing,sample_interval_s=sample_interval_s,
                multi_frame_fusion=multi_frame_fusion,
                image_border_fraction=image_border_fraction,minimum_sharpness=minimum_sharpness,
                start_s=start_s,end_s=end_s,
                resource_settings=resource_settings,
                _worker_threads=scheduler.workers_per_flight,
                _memory_budget_bytes=(scheduler.available_memory_bytes//scheduler.concurrent_flights
                                      if scheduler.available_memory_bytes is not None else None),
                _cuda_gate=cuda_gates[device],
                _cuda_device_index=device)

        if progress:
            hardware = (f'{cuda_device_count} CUDA GPU(s)' if cuda_device_count else
                        f'{scheduler.concurrent_flights} concurrent CPU job(s)')
            progress('Planning parallel colorization', 0.,
                     f'Scheduling {len(selections)} flights with {hardware}.')
        with ThreadPoolExecutor(max_workers=scheduler.concurrent_flights,
                                thread_name_prefix='elios-flight') as flight_pool:
            futures = [flight_pool.submit(process_flight, index, item, flight_output)
                       for index, (item, flight_output) in enumerate(zip(selections, planned), 1)]
            try:
                reports = [future.result() for future in futures]
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        total = sum(int(result['total_points']) for result in reports)
        colored = sum(int(result['colored_points']) for result in reports)
        elapsed_seconds = round(time.monotonic() - started, 2)
        payload = {
            'summary': {
                'application_version': __version__, 'mode': 'separate_outputs',
                'flight_count': len(selections), 'output_directory': str(destination),
                'elapsed_seconds': elapsed_seconds,
                'coverage': {'colored_points': colored, 'total_points': total,
                             'percent': round(colored / total * 100, 2) if total else 0.0},
                'sampling_frequency_hz': 1 / sample_interval_s,
                'user_settings': {'image_edge_exclusion_percent': image_border_fraction * 100,
                                  'blur_rejection': ('Off' if minimum_sharpness == 0 else
                                                     ('Normal' if minimum_sharpness <= 2 else 'Strong')),
                                  'illumination_balancing_requested': illumination_balancing,
                                  'multi_frame_fusion_requested': multi_frame_fusion,
                                  'multi_frame_fusion_applied': any(bool((item.get('multi_frame_fusion') or {}).get('applied')) for item in reports),
                                  'maximum_color_distance_m': maximum_color_distance_m,
                                  'time_range_relative_seconds': {'enabled': start_s is not None or end_s is not None,
                                                                  'start': start_s or 0., 'end': end_s}},
                'outputs': [item['output'] for item in reports],
                'parallel_processing': scheduler.to_dict(),
            },
            'application_version': __version__, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'mode': mode, 'maximum_color_distance_m': maximum_color_distance_m,
            'elapsed_seconds': elapsed_seconds, 'results': reports,
            'parallel_processing': scheduler.to_dict(),
            'resource_settings': resource_settings or {'use_recommended': True},
        }
        with workflow_report.open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2)
        return dict(output=str(destination), outputs=[item['output'] for item in reports],
                    report=str(workflow_report), colored_points=colored, total_points=total,
                    processing_strategy='parallel_separate_outputs',
                    concurrent_flight_jobs=scheduler.concurrent_flights,
                    cuda_devices_detected=scheduler.cuda_devices)

    return _run_merged_workflow(selections, destination, calibration_override,
        alignment_method=alignment_method, merged_source=merged_source,
        cloudcompare_executable=cloudcompare_executable,
        maximum_color_distance_m=maximum_color_distance_m, progress=progress,
        cancelled=cancelled, started=started, flight_checks=flight_checks,
        illumination_balancing=illumination_balancing,sample_interval_s=sample_interval_s,
        multi_frame_fusion=multi_frame_fusion,
        image_border_fraction=image_border_fraction,minimum_sharpness=minimum_sharpness,
        start_s=start_s,end_s=end_s,resource_settings=resource_settings)
