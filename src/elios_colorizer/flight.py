"""Read-only Inspector flight discovery, telemetry and synchronized RGB frames.

The StarNet reader deliberately supports the embedded Camera version 4 schema
and the timestamped, CRC-protected envelopes verified against the test flight.
It is not a general StarNet decoder. Unknown schemas fail closed.
"""
from __future__ import annotations

import binascii
import csv
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import tempfile
from typing import Callable, Iterator

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

CACHE_VERSION = 3
POSE_TOPIC = "/kalman_scan2map_node/odometry"
SERVO_TOPIC = "/sensors/servo_angle"
ESCAPES = {0xAB: 0xAA, 0xAC: 0x55, 0xAD: 0xF0, 0xAF: 0xA5}


class FlightError(ValueError):
    """Source data is incomplete, unsupported, or internally inconsistent."""


class CancelledError(RuntimeError):
    pass


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError("Colorization cancelled.")


def _progress(callback, text, fraction=None):
    if callback is not None:
        callback(text, fraction)


@dataclass
class FlightSource:
    root: Path
    metadata_path: Path | None = None
    metadata: dict = field(default_factory=dict)
    videos: tuple[Path, ...] = ()
    mcap_path: Path | None = None
    starnet_path: Path | None = None
    trajectory_path: Path | None = None
    las_path: Path | None = None
    calibration_path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def missing_inputs(self) -> list[str]:
        missing = []
        if not self.videos:
            missing.append("RGB video (.MOV or .MP4)")
        if not self.mcap_path and not self.trajectory_path:
            missing.append("MCAP or exported trajectory CSV")
        if not self.starnet_path:
            missing.append("StarNet camera tilt and video synchronization log")
        if not self.las_path:
            missing.append("Inspector exported point cloud (.LAS or .LAZ)")
        if not self.calibration_path:
            missing.append("Verified RGB camera calibration profile")
        return missing


def _natural_key(path):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", str(path))]


def _one(paths, description):
    found = sorted(set(paths), key=_natural_key)
    if len(found) > 1:
        raise FlightError(f"Multiple {description} found. Select a single flight folder or choose the file explicitly.")
    return found[0] if found else None


def discover_source(folder, las_override=None, calibration_override=None) -> FlightSource:
    """Discover one flight, at most three directory levels below the selection.

    Native metadata dictates video ordering. A LAS or camera profile can be
    selected outside that folder; original inputs are never modified.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FlightError(f"Flight folder does not exist: {root}")
    files = []
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        for path in directory.iterdir():
            if path.name.startswith(".") or path.is_symlink():
                continue
            if path.is_file():
                files.append(path)
            elif path.is_dir() and depth < 3:
                pending.append((path, depth + 1))
    def read_metadata(path):
        if path.suffix.lower() != ".json" or path.stat().st_size > 5_000_000:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data = data.get("flight", data)
                if isinstance(data, dict) and isinstance(data.get("files"), dict) and "time_sync" in data:
                    return path, data
        except (ValueError, OSError):
            pass
        return None

    metadata_candidates = [value for path in files if (value := read_metadata(path)) is not None]
    if len(metadata_candidates) == 1 and metadata_candidates[0][1].get("id"):
        # A native folder and its matching Inspector export commonly sit beside
        # each other. Only an exact flight UUID permits this automatic pairing.
        identity = metadata_candidates[0][1]["id"]
        adjacent_parent = metadata_candidates[0][0].parent.parent
        for directory in adjacent_parent.iterdir():
            if not directory.is_dir() or directory.is_symlink() or directory.name.startswith("."):
                continue
            for candidate_path in directory.glob("*.json"):
                if any(candidate_path == item[0] for item in metadata_candidates):
                    continue
                value = read_metadata(candidate_path)
                if value is not None and value[1].get("id") == identity:
                    metadata_candidates.append(value)
                    files.extend(p for p in directory.iterdir() if p.is_file() and not p.is_symlink())
    if len(metadata_candidates) > 1:
        identities = {data.get("id") for _, data in metadata_candidates}
        if len(identities) != 1 or None in identities:
            raise FlightError("Multiple flight metadata files found. Select one flight folder to avoid mixing flights.")
    # Prefer the native manifest; exports reference copies of video and calibration.
    metadata_candidates.sort(key=lambda item: (item[0].name.lower() != "flight.json", str(item[0])))
    meta_path, metadata = metadata_candidates[0] if metadata_candidates else (None, {})
    scope = meta_path.parent if meta_path else root
    # References are portable by basename, but must never escape the selected tree.
    scoped = [p for p in files if p == scope or scope in p.parents]
    companion_roots = {p.parent for p, _ in metadata_candidates}
    combined = list(set(p for p in files if any(d == p.parent or d in p.parents for d in companion_roots))) if companion_roots else scoped
    warnings = []
    if len(metadata_candidates) > 1:
        warnings.append("Native and exported data paired by exact flight UUID; native videos/telemetry and exported LAS are used.")

    def reference(value):
        if isinstance(value, dict):
            value = value.get("path")
        if not value:
            return None
        name = str(value).replace("\\", "/").rsplit("/", 1)[-1]
        candidate = _one([p for p in scoped if p.name.lower() == name.lower()], f"files named {name}")
        return candidate

    refs = metadata.get("files", {})
    video_refs = refs.get("rgb_videos_files") or refs.get("rgb_videos") or []
    videos = []
    if video_refs:
        for value in video_refs:
            found = reference(value)
            if found is None:
                raise FlightError(f"Metadata lists an RGB video that is missing: {value}")
            if found in videos:
                raise FlightError("Metadata lists the same RGB video segment more than once.")
            videos.append(found)
    else:
        videos = sorted([p for p in scoped if p.suffix.lower() in (".mov", ".mp4")
                         and "thermal" not in p.name.lower()], key=_natural_key)
        if len(videos) > 1:
            warnings.append("Video segment order inferred from filenames because no RGB manifest was found.")
    mcap = reference(refs.get("obc_bag_file") or refs.get("obc_bag"))
    if mcap is None:
        mcap = _one([p for p in scoped if p.suffix.lower() == ".mcap"], "MCAP files")
    starnet = reference(refs.get("flight_log_file") or refs.get("flight_log"))
    if starnet is None:
        starnet = _one([p for p in scoped if p.suffix.lower() == ".stn"
                        and "thermal" not in p.name.lower()], "StarNet logs")
    trajectory = _one([p for p in combined if p.name.lower().endswith("trajectory.csv")], "trajectory CSV files")

    def override_or_find(override, candidates, label):
        if override:
            path = Path(override).expanduser().resolve()
            if not path.is_file():
                raise FlightError(f"Selected {label} does not exist: {path}")
            return path
        return _one(candidates, label)

    las = override_or_find(las_override, [p for p in combined if p.suffix.lower() in (".las", ".laz")], "point clouds")
    profile = override_or_find(calibration_override, [p for p in combined if p.name.lower() in
                               ("rgb_camera_profile.json", "colorizer_calibration.json")], "RGB camera profiles")
    if not starnet and mcap:
        warnings.append("MCAP servo fallback has receipt timestamps and an inferred opposite sign; StarNet is preferred.")
    if trajectory and not mcap:
        warnings.append("Export trajectory quality=1 is treated as usable; the export does not define its confidence semantics.")
    return FlightSource(scope, meta_path, metadata, tuple(videos), mcap, starnet, trajectory, las, profile, warnings)


@dataclass
class StarNetData:
    pitch_times_s: np.ndarray
    pitch_degrees: np.ndarray
    frame_indices: np.ndarray
    frame_times_s: np.ndarray
    command_times_s: np.ndarray
    command_degrees: np.ndarray
    image_orientation: np.ndarray
    warnings: list[str]
    valid_records: int = 0
    excluded_records: int = 0
    timing_diagnostics: dict = field(default_factory=dict)


def _unescape(data):
    out = bytearray()
    cursor = 0
    while cursor < len(data):
        value = data[cursor]
        if value == 0xA5:
            cursor += 1
            value = ESCAPES[data[cursor]]
        out.append(value)
        cursor += 1
    return bytes(out)


def parse_starnet(path, cancelled=None) -> StarNetData:
    """Parse schema-v4 camera messages; corrupt/opaque envelopes are excluded."""
    data = Path(path).read_bytes()
    if not data.startswith(b"\xaa") or not data.endswith(b"\x55"):
        raise FlightError("StarNet framing is unsupported or the file is truncated.")
    pitch, sync, commands, offsets, recording, schema = [], [], [], [], [], None
    valid = bad = opaque = 0
    for index, escaped in enumerate(data[1:-1].split(b"\x55\xaa")):
        if index % 10000 == 0:
            _check_cancel(cancelled)
        try:
            block = _unescape(escaped)
        except (KeyError, IndexError):
            bad += 1
            continue
        if len(block) < 5 or binascii.crc_hqx(block[:-2], 0xFFFF) != int.from_bytes(block[-2:], "little"):
            bad += 1
            continue
        valid += 1
        flags = block[0]
        if flags == 0 and block[1:3] == b"\xff\xff":
            try:
                value = json.loads(block[3:-2])
            except ValueError:
                bad += 1
                continue
            if value.get("id") == 14:
                schema = value
            continue
        if flags not in (2, 6):
            opaque += 1
            continue
        cursor = 6 + (3 if flags & 4 else 0)
        if len(block) < cursor + 4:
            bad += 1
            continue
        family, message = block[cursor:cursor + 2]
        if family != 14 or message not in (1, 2, 4, 9):
            continue
        if not schema or schema.get("name") != "Camera" or schema.get("version") != 4:
            raise FlightError("This StarNet camera schema is unsupported. Camera version 4 is required; no tilt values were guessed.")
        payload = block[cursor + 2:-2]
        time_s = int.from_bytes(block[1:6], "little") / 1_000_000.0
        if message == 4 and len(payload) == 31:
            pitch.append((time_s, struct.unpack_from("<b", payload, 9)[0], payload[30]))
            recording.append((time_s, struct.unpack_from('<H', payload, 13)[0], payload[11] & 15))
        elif message == 1 and len(payload) == 8:
            offsets.append(struct.unpack('<q', payload)[0] / 1e6)
        elif message == 2 and len(payload) == 12:
            frame, time_us = struct.unpack("<IQ", payload)
            sync.append((frame, time_us / 1_000_000.0))
        elif message == 9 and len(payload) == 4:
            commands.append((time_s, struct.unpack("<f", payload)[0]))
        else:
            raise FlightError(f"Unexpected Camera message {message} layout; refusing to decode camera telemetry.")
    if not pitch or not sync:
        raise FlightError("StarNet has no usable camera-state tilt or frame synchronization records.")
    p = np.asarray(pitch, dtype=np.float64)
    f = np.asarray(sync, dtype=np.float64)
    c = np.asarray(commands, dtype=np.float64).reshape(-1, 2)
    if f[0, 0] != 1 or np.any(np.diff(f[:, 0]) <= 0) or np.any(np.diff(f[:, 1]) <= 0):
        raise FlightError("Video frame counters do not form one monotonic recording starting at 1. Multiple recordings/resets require explicit mapping.")
    warnings = ["FrameSync counter 1 is mapped to decoded global frame 0; validate this convention in the calibration overlay."]
    origin = float(f[0, 1])
    clock_checks = dict(video_origin_flight_clock_s=origin, frame_sync_first_counter=int(f[0, 0]),
                        frame_sync_last_counter=int(f[-1, 0]), frame_sync_last_time_s=float(f[-1, 1]))
    if offsets:
        if np.max(np.abs(np.asarray(offsets) - origin)) > .01:
            raise FlightError('Camera VideoOffset disagrees with first FrameSync timestamp by more than 10 ms. Clock mapping must be investigated.')
        clock_checks['starnet_video_offset_records'] = len(offsets)
        clock_checks['starnet_video_offset_max_difference_s'] = float(np.max(np.abs(np.asarray(offsets) - origin)))
    r = np.asarray(recording)
    active = (r[:, 2] == 1) & (r[:, 0] >= origin) & (r[:, 0] <= f[-1, 1])
    if int(active.sum()) >= 10:
        # Camera recording time is integer seconds: its expected error is [-1,0]
        # plus the small reporting latency. Large shifts or a reset are unsafe.
        errors = r[active, 1] - (r[active, 0] - origin)
        clock_checks['recording_counter_error_quantiles_s'] = np.quantile(errors, [0, .5, 1]).tolist()
        clock_checks['recording_counter_samples'] = int(active.sum())
        if np.median(errors) < -1.25 or np.median(errors) > .25 or np.mean((errors < -1.5) | (errors > .5)) > .01:
            raise FlightError('Camera recording elapsed-time counter disagrees with the FrameSync clock origin. Refusing potentially shifted colorization.')
    if bad or opaque:
        warnings.append(f"StarNet excluded {bad:,} CRC/framing failures and {opaque:,} unsupported transport envelopes.")
    if np.any(np.diff(f[:, 0]) > 1):
        warnings.append("Frame synchronization has missing counters; frames without an exact timestamp are skipped.")
    return StarNetData(p[:, 0], p[:, 1], f[:, 0].astype(np.int64) - 1, f[:, 1],
                       c[:, 0], c[:, 1], p[:, 2].astype(np.int8), warnings, valid, bad + opaque, clock_checks)


@dataclass
class PoseSample:
    position_world_m: np.ndarray
    orientation_xyzw: np.ndarray
    camera_pitch_degrees: float


@dataclass
class Telemetry:
    pose_times_s: np.ndarray
    positions_m: np.ndarray
    quaternions_xyzw: np.ndarray
    pose_confidence: np.ndarray
    pitch_times_s: np.ndarray
    pitch_degrees: np.ndarray
    frame_indices: np.ndarray
    frame_times_s: np.ndarray
    command_times_s: np.ndarray = field(default_factory=lambda: np.empty(0))
    command_degrees: np.ndarray = field(default_factory=lambda: np.empty(0))
    warnings: list[str] = field(default_factory=list)
    pitch_source: str = "StarNet CameraState (degrees)"
    pose_frame: str = "local_lidar"
    body_frame: str = "body"
    video_offset_s: float | None = None
    timing_diagnostics: dict = field(default_factory=dict)
    _slerp: Slerp = field(init=False, repr=False)

    def __post_init__(self):
        # Deduplicate clock samples deterministically, preserving invalid confidence.
        for times_name, names in [("pose_times_s", ("positions_m", "quaternions_xyzw", "pose_confidence")),
                                  ("pitch_times_s", ("pitch_degrees",))]:
            times = np.asarray(getattr(self, times_name), dtype=np.float64)
            if times.ndim != 1 or len(times) < 2 or not np.all(np.isfinite(times)):
                raise FlightError(f"{times_name} needs at least two finite samples.")
            order = np.argsort(times, kind="stable")
            order = order[np.r_[True, np.diff(times[order]) > 0]]
            setattr(self, times_name, times[order])
            for name in names:
                values = np.asarray(getattr(self, name))
                if len(values) != len(times):
                    raise FlightError(f"Mismatched telemetry array: {name}")
                setattr(self, name, values[order])
        norms = np.linalg.norm(self.quaternions_xyzw, axis=1)
        if (len(self.pose_times_s) < 2 or len(self.pitch_times_s) < 2
                or self.positions_m.shape != (len(norms), 3)
                or self.quaternions_xyzw.shape != (len(norms), 4)
                or not np.all(np.isfinite(self.positions_m)) or not np.all(np.isfinite(norms))
                or np.any(norms < 1e-8) or not np.all(np.isfinite(self.pitch_degrees))
                or np.any(np.abs(self.pitch_degrees) > 180)):
            raise FlightError("Invalid pose/quaternion or camera pitch telemetry.")
        self.quaternions_xyzw = self.quaternions_xyzw / norms[:, None]
        self._slerp = Slerp(self.pose_times_s, Rotation.from_quat(self.quaternions_xyzw))

    @staticmethod
    def _bracket(times, time_s, maximum_gap):
        if not np.isfinite(time_s) or time_s < times[0] or time_s > times[-1]:
            return None
        right = int(np.searchsorted(times, time_s, side="left"))
        if right < len(times) and abs(times[right] - time_s) < 1e-9:
            return right, right
        if right == 0 or right == len(times) or times[right] - times[right - 1] > maximum_gap:
            return None
        return right - 1, right

    def interpolate(self, time_s: float, maximum_pose_gap_s=.35, maximum_pitch_gap_s=.35) -> PoseSample | None:
        """SLERP attitude + linear position/pitch; never extrapolate or bridge dropout.

        Camera-state quantization is interpolated without smoothing actual turns.
        High-rate pitch commands are retained for diagnostics, never substituted
        for the reported camera state or presumed to measure mechanical motion.
        """
        pose = self._bracket(self.pose_times_s, time_s, maximum_pose_gap_s)
        pitch = self._bracket(self.pitch_times_s, time_s, maximum_pitch_gap_s)
        if pose is None or pitch is None or not np.all(self.pose_confidence[list(pose)] == 1):
            return None
        left, right = pose
        if left == right:
            position = self.positions_m[left].copy()
        else:
            fraction = (time_s - self.pose_times_s[left]) / (self.pose_times_s[right] - self.pose_times_s[left])
            position = self.positions_m[left] * (1 - fraction) + self.positions_m[right] * fraction
        angle = float(np.interp(time_s, self.pitch_times_s, self.pitch_degrees))
        return PoseSample(position, self._slerp([time_s]).as_quat()[0], angle)


@dataclass(frozen=True)
class VideoSegment:
    path: Path
    frame_count: int
    fps: float


@dataclass(frozen=True)
class VideoCoverage:
    segments: tuple[VideoSegment, ...]
    total_frames: int
    synchronized_frames_available: int
    frame_sync_records_beyond_video: int
    last_available_sync_time_s: float | None


def inspect_video_coverage(source: FlightSource, telemetry: Telemetry,
                           cancelled=None) -> VideoCoverage:
    """Validate video containers before expensive LAS indexing.

    Inspector can write a few final FrameSync records after the encoded movie
    ends. Those unavailable tail records are safe to skip because every earlier
    counter keeps its exact decoded-frame mapping. A large overrun still fails
    closed because it can indicate a missing or seriously truncated segment.
    """
    import cv2

    if not source.videos:
        raise FlightError("No RGB videos were found.")
    segments = []
    total_frames = 0
    for video in source.videos:
        _check_cancel(cancelled)
        capture = cv2.VideoCapture(str(video))
        try:
            if not capture.isOpened():
                raise FlightError(f"Cannot decode RGB video: {video.name}")
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            if count <= 0 or not np.isfinite(fps) or fps <= 0:
                raise FlightError(f"Video has no usable frame count/rate: {video.name}")
            segments.append(VideoSegment(video, count, fps))
            total_frames += count
        finally:
            capture.release()

    if not len(telemetry.frame_indices):
        return VideoCoverage(tuple(segments), total_frames, 0, 0, None)
    in_range = (telemetry.frame_indices >= 0) & (telemetry.frame_indices < total_frames)
    synchronized = int(in_range.sum())
    if synchronized == 0:
        raise FlightError("No FrameSync records correspond to available RGB footage.")
    beyond = int((telemetry.frame_indices >= total_frames).sum())
    overflow_span = max(0, int(telemetry.frame_indices[-1]) + 1 - total_frames)
    # Ten seconds or 0.5% of the recording, whichever is larger, tolerates
    # ordinary encoder-finalization tails while still detecting a missing clip.
    maximum_tail = max(int(math.ceil(max(segment.fps for segment in segments) * 10)),
                       int(math.ceil(total_frames * .005)))
    if overflow_span > maximum_tail:
        raise FlightError(
            f"FrameSync extends {overflow_span:,} frames beyond available RGB footage; "
            "this is too large to treat as a recording tail and a segment may be missing."
        )

    telemetry.timing_diagnostics['encoded_video_frames'] = total_frames
    telemetry.timing_diagnostics['frame_sync_records'] = int(len(telemetry.frame_indices))
    telemetry.timing_diagnostics['frame_sync_records_beyond_video'] = beyond
    telemetry.timing_diagnostics['frame_sync_overflow_span_frames'] = overflow_span
    telemetry.timing_diagnostics['untimestamped_video_frames_skipped'] = total_frames - synchronized
    telemetry.timing_diagnostics['video_segments'] = [
        {'name': segment.path.name, 'frame_count': segment.frame_count, 'fps': segment.fps}
        for segment in segments
    ]
    if beyond:
        warning = (
            f"{beyond:,} trailing FrameSync records have no encoded RGB frame and are skipped; "
            f"the preceding {synchronized:,} synchronized frames retain their exact mapping."
        )
        if warning not in telemetry.warnings:
            telemetry.warnings.append(warning)
    unmatched = total_frames - synchronized
    if unmatched:
        warning = (f'{unmatched:,} encoded frames have no FrameSync timestamp and are skipped. '
                   'Counter-to-decoded-frame alignment still requires visual calibration checks.')
        if warning not in telemetry.warnings:
            telemetry.warnings.append(warning)
    last_time = float(telemetry.frame_times_s[np.flatnonzero(in_range)[-1]])
    return VideoCoverage(tuple(segments), total_frames, synchronized, beyond, last_time)


def telemetry_cache_key(source: FlightSource) -> str:
    """Invalidate extraction on source replacement, edit, or parser change."""
    items = []
    for path in (source.metadata_path, source.mcap_path, source.starnet_path, source.trajectory_path):
        if path is not None:
            stat = path.stat()
            # Include head/tail content to catch replacement with preserved file dates.
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                digest.update(handle.read(65536))
                handle.seek(max(0, stat.st_size - 65536))
                digest.update(handle.read(65536))
            items.append((str(path.resolve()), stat.st_size, stat.st_mtime_ns, digest.hexdigest()))
    serialized = json.dumps([CACHE_VERSION, items, source.metadata.get("time_sync", {})], sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:24]


def _read_mcap(source, progress, cancelled):
    from mcap.reader import make_reader
    from mcap_ros2.decoder import DecoderFactory
    poses, servo, pose_delays = [], [], []
    clock_checks = {}
    parent_frames, body_frames = set(), set()
    factory = DecoderFactory()
    with source.mcap_path.open("rb") as handle:
        reader = make_reader(handle, validate_crcs=True)
        summary = reader.get_summary()
        if summary is not None and summary.statistics is not None:
            clock_checks['mcap_log_start_s'] = summary.statistics.message_start_time / 1e9
            clock_checks['mcap_log_end_s'] = summary.statistics.message_end_time / 1e9
        channels = summary.channels.values() if summary is not None else ()
        if summary is not None and not any(c.topic == POSE_TOPIC for c in channels):
            raise FlightError(f"MCAP does not contain {POSE_TOPIC}.")
        # Read only necessary channels, one pass; no image/LiDAR decompression in Python.
        for index, (schema, channel, message) in enumerate(reader.iter_messages(
                topics=[POSE_TOPIC, SERVO_TOPIC], log_time_order=False)):
            if index % 3000 == 0:
                _check_cancel(cancelled)
                _progress(progress, f"Reading native telemetry ({index:,} records)…", None)
            decoder = factory.decoder_for(channel.message_encoding, schema)
            if decoder is None:
                raise FlightError(f"Cannot decode MCAP encoding for {channel.topic}.")
            decoded = decoder(message.data)
            if channel.topic == SERVO_TOPIC:
                # std_msgs/Float32 lacks a header; receipt time is explicitly approximate.
                servo.append((message.log_time / 1e9, -float(decoded.data)))
            else:
                stamp = decoded.header.stamp
                pose_delays.append(message.log_time / 1e9 - (stamp.sec + stamp.nanosec / 1e9))
                p, q = decoded.pose.position, decoded.pose.orientation
                parent_frames.add(decoded.header.frame_id)
                body_frames.add(decoded.child_frame_id)
                poses.append((stamp.sec + stamp.nanosec / 1e9, p.x, p.y, p.z,
                              q.x, q.y, q.z, q.w, int(decoded.confidence)))
    if len(poses) < 2:
        raise FlightError("MCAP has no usable odometry samples.")
    if len(parent_frames) != 1 or len(body_frames) != 1:
        raise FlightError("MCAP odometry changes coordinate frames during the flight.")
    clock_checks['pose_sensor_to_recording_delay_quantiles_s'] = np.quantile(pose_delays, [0, .5, .95, 1]).tolist()
    return np.asarray(poses), np.asarray(servo).reshape(-1, 2), next(iter(parent_frames)), next(iter(body_frames)), clock_checks


def _read_trajectory(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(2048)
        handle.seek(0)
        if "," in sample.splitlines()[0]:
            rows = list(csv.DictReader(handle))
        else:
            lines = [line.split() for line in handle if line.strip()]
            rows = [dict(zip(lines[0], line)) for line in lines[1:]]
    required = ("timestamp[s]", "pos_x[m]", "pos_y[m]", "pos_z[m]", "rot_x", "rot_y", "rot_z", "rot_w")
    try:
        return np.asarray([[float(row[key]) for key in required] + [float(row.get("quality", 1))] for row in rows])
    except (KeyError, ValueError) as error:
        raise FlightError("Trajectory CSV does not match the Inspector export format.") from error


def load_telemetry(source: FlightSource, cache_dir, progress=None, cancelled=None) -> Telemetry:
    """Load native telemetry (or exported poses), caching outside the source tree."""
    _check_cancel(cancelled)
    cache = Path(cache_dir).expanduser().resolve()
    if cache == source.root or source.root in cache.parents:
        raise FlightError("The telemetry cache must be outside the original flight folder.")
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"telemetry-{telemetry_cache_key(source)}.npz"
    if path.exists():
        try:
            with np.load(path, allow_pickle=False) as arrays:
                metadata = json.loads(str(arrays["metadata"]))
                kwargs = {key: arrays[key] for key in ("pose_times_s", "positions_m", "quaternions_xyzw", "pose_confidence",
                          "pitch_times_s", "pitch_degrees", "frame_indices", "frame_times_s", "command_times_s", "command_degrees")}
                result = Telemetry(**kwargs, **metadata)
            _progress(progress, "Loaded cached flight telemetry.", 1.0)
            return result
        except (ValueError, KeyError, OSError):
            pass  # Interrupted/corrupt cache is safely regenerated from originals.
    warnings = list(source.warnings)
    clock_checks = {}
    if source.mcap_path:
        poses, servo, pose_frame, body_frame, clock_checks = _read_mcap(source, progress, cancelled)
    elif source.trajectory_path:
        poses, servo, pose_frame, body_frame = _read_trajectory(source.trajectory_path), np.empty((0, 2)), "export", "body"
    else:
        raise FlightError("No MCAP or exported trajectory CSV was found.")
    _check_cancel(cancelled)
    if source.starnet_path:
        _progress(progress, "Reading camera tilt and video synchronization…", None)
        camera = parse_starnet(source.starnet_path, cancelled)
        pitch_t, pitch_d = camera.pitch_times_s, camera.pitch_degrees
        frame_i, frame_t = camera.frame_indices, camera.frame_times_s
        command_t, command_d = camera.command_times_s, camera.command_degrees
        warnings.extend(camera.warnings)
        clock_checks.update(camera.timing_diagnostics)
        pitch_source = "StarNet CameraState (degrees)"
    elif len(servo) > 1:
        pitch_t, pitch_d = servo[:, 0], servo[:, 1]
        frame_i, frame_t, command_t, command_d = np.empty(0, dtype=np.int64), np.empty(0), np.empty(0), np.empty(0)
        pitch_source = "MCAP servo_angle, sign inverted; approximate receipt timestamps"
        warnings.append("MCAP tilt fallback uses negated servo_angle and message receipt times; validate mounting and timing before final export.")
    else:
        raise FlightError("No reported camera tilt telemetry is available. Exported trajectory alone cannot determine camera-head tilt.")
    offset = source.metadata.get("time_sync", {}).get("video_offset")
    offset = float(offset) / 1e6 if offset is not None else None
    if len(frame_t) and offset is not None and abs(offset - float(frame_t[0])) > .01:
        raise FlightError('Flight metadata video offset disagrees with StarNet FrameSync by more than 10 ms. Resolve the clock mapping before colorization.')
    clock_checks['metadata_video_offset_s'] = offset
    clock_checks['avionics_offset_s_not_applied'] = source.metadata.get('time_sync', {}).get('av_offset', 0) / 1e6 if source.metadata.get('time_sync', {}).get('av_offset') is not None else None
    obc_offset = source.metadata.get('time_sync', {}).get('obc_offset')
    if obc_offset is not None and abs(obc_offset) > 1000:
        raise FlightError('This flight declares a nonzero OBC/log clock offset. Its convention must be verified before these streams can be paired.')
    if len(frame_t):
        if 'mcap_log_start_s' in clock_checks:
            clock_checks['video_start_relative_to_mcap_recording_s'] = float(frame_t[0] - clock_checks['mcap_log_start_s'])
        clock_checks['usable_clock_interval_s'] = [float(max(frame_t[0], poses[0, 0], pitch_t[0])),
                                                    float(min(frame_t[-1], poses[-1, 0], pitch_t[-1]))]
        if clock_checks['usable_clock_interval_s'][0] >= clock_checks['usable_clock_interval_s'][1]:
            raise FlightError('Video, pose, and camera-state clocks do not overlap.')
    if not len(frame_t) and offset is None:
        raise FlightError("Neither exact video frame synchronization nor a video clock offset is available.")
    result = Telemetry(poses[:, 0], poses[:, 1:4], poses[:, 4:8], poses[:, 8], pitch_t, pitch_d,
                       frame_i, frame_t, command_t, command_d, warnings, pitch_source, pose_frame, body_frame, offset, clock_checks)
    metadata = {key: getattr(result, key) for key in ("warnings", "pitch_source", "pose_frame", "body_frame", "video_offset_s", "timing_diagnostics")}
    arrays = {key: getattr(result, key) for key in ("pose_times_s", "positions_m", "quaternions_xyzw", "pose_confidence",
              "pitch_times_s", "pitch_degrees", "frame_indices", "frame_times_s", "command_times_s", "command_degrees")}
    with tempfile.NamedTemporaryFile(suffix='.npz', prefix='telemetry-', dir=cache, delete=False) as handle:
        temp = Path(handle.name)
    try:
        np.savez_compressed(temp, metadata=json.dumps(metadata), **arrays)
        _check_cancel(cancelled)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    _progress(progress, f"Loaded {len(result.pose_times_s):,} poses and {len(result.frame_indices):,} frame timestamps.", 1.0)
    return result


def iter_observations(source: FlightSource, telemetry: Telemetry, sample_interval_s=1.0,
                      max_frames=None, start_s=None, end_s=None, progress=None, cancelled=None,
                      video_coverage: VideoCoverage | None = None) -> Iterator:
    """Yield upright RGB observations with exact global frame-counter matching.

    start_s/end_s are seconds relative to the first frame synchronization time.
    Decoding seeks to each chosen frame, checks the returned index and uses the
    decoder PTS only for the explicitly lower-quality metadata-offset fallback.
    Tail frames missing FrameSync are never extrapolated.
    """
    import cv2
    from .colorize import FrameObservation
    if sample_interval_s <= 0:
        raise ValueError("Frame sampling interval must be positive.")
    if max_frames is not None and max_frames <= 0:
        return
    coverage = video_coverage or inspect_video_coverage(source, telemetry, cancelled)
    has_sync = len(telemetry.frame_indices) > 0
    origin = float(telemetry.frame_times_s[0]) if has_sync else telemetry.video_offset_s
    lower = origin + (start_s or 0)
    upper = origin + end_s if end_s is not None else np.inf
    last_sample = -np.inf
    emitted = global_start = 0
    fallback_segment_offset = 0.0
    total_frames = coverage.total_frames
    if has_sync:
        usable_start = max(telemetry.pose_times_s[0], telemetry.pitch_times_s[0])
        usable_end = min(telemetry.pose_times_s[-1], telemetry.pitch_times_s[-1])
        telemetry.timing_diagnostics['timestamped_frames_outside_pose_tilt_interval'] = int(
            np.sum((telemetry.frame_times_s < usable_start) | (telemetry.frame_times_s > usable_end)))
    for segment, segment_info in enumerate(coverage.segments):
        video = segment_info.path
        _check_cancel(cancelled)
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise FlightError(f"Cannot decode RGB video: {video.name}")
        try:
            # Never depend on a backend's default auto-rotation behavior.
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
            rotation = int(round(capture.get(cv2.CAP_PROP_ORIENTATION_META))) % 360
            if rotation not in (0, 90, 180, 270):
                raise FlightError(f"Unsupported video display rotation {rotation} degrees.")
            count = segment_info.frame_count
            fps = segment_info.fps
            if has_sync:
                mask = ((telemetry.frame_indices >= global_start) & (telemetry.frame_indices < global_start + count)
                        & (telemetry.frame_times_s >= lower) & (telemetry.frame_times_s <= upper))
                candidates = zip(telemetry.frame_indices[mask] - global_start, telemetry.frame_times_s[mask])
            else:
                # Deliberately mark this path approximate: segment duration lacks
                # a timestamp at the following frame boundary when FrameSync is absent.
                candidates = ((index, origin + fallback_segment_offset + index / fps)
                              for index in range(0, count, max(1, int(round(fps * sample_interval_s)))))
            for frame_index, timestamp in candidates:
                _check_cancel(cancelled)
                if timestamp < lower or timestamp > upper or timestamp - last_sample < sample_interval_s - 1e-6:
                    continue
                pose = telemetry.interpolate(float(timestamp))
                if pose is None:
                    continue
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
                ok, bgr = capture.read()
                if not ok:
                    raise FlightError(f"Cannot decode frame {frame_index} from {video.name}.")
                actual_index = int(round(capture.get(cv2.CAP_PROP_POS_FRAMES))) - 1
                if actual_index != int(frame_index):
                    raise FlightError(f"Decoder returned frame {actual_index}, requested {frame_index}; accurate frame synchronization is unavailable.")
                if not has_sync:
                    pts = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
                    if not np.isfinite(pts) or (frame_index > 0 and pts <= 0):
                        raise FlightError("Video decoder does not expose usable presentation timestamps.")
                    timestamp = origin + fallback_segment_offset + pts
                    pose = telemetry.interpolate(float(timestamp))
                    if pose is None:
                        continue
                if rotation == 180:
                    bgr = cv2.rotate(bgr, cv2.ROTATE_180)
                elif rotation == 90:
                    bgr = cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
                elif rotation == 270:
                    bgr = cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                last_sample = float(timestamp)
                emitted += 1
                _progress(progress, f"Processing video frame {emitted} ({video.name}, {int(frame_index)})…",
                          float((global_start + int(frame_index)) / max(1, int(telemetry.frame_indices[-1]))) if has_sync else None)
                yield FrameObservation(rgb=rgb, position_world_m=pose.position_world_m,
                                       orientation_xyzw=pose.orientation_xyzw,
                                       camera_pitch_degrees=pose.camera_pitch_degrees,
                                       timestamp_s=float(timestamp), frame_id=f"{video.name}:{int(frame_index)}")
                if max_frames is not None and emitted >= max_frames:
                    return
            global_start += count
            fallback_segment_offset += count / fps
        finally:
            capture.release()
    if emitted == 0:
        raise FlightError("No RGB frame has valid overlapping pose, tilt and synchronization telemetry.")
