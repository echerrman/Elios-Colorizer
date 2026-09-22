"""Read-only audit of Inspector flight exports; writes reports outside Flight_Data.

Requires Python 3.10+, NumPy, and ffprobe on PATH (or --ffprobe).
This is a format/data inventory, not a camera calibration or colorization tool.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import struct
import subprocess

import numpy as np


def cstring(value):
    return value.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def inspect_trajectory(path):
    with path.open(encoding="utf-8-sig") as handle:
        header = handle.readline().split()
    data = np.loadtxt(path, skiprows=1, ndmin=2)
    if data.shape[1] != 9 or not np.isfinite(data).all():
        raise ValueError(f"Unexpected or nonfinite trajectory: {path}")
    dt = np.diff(data[:, 0])
    norms = np.linalg.norm(data[:, 4:8], axis=1)
    values, counts = np.unique(data[:, 8], return_counts=True)
    return {
        "file": path.name, "delimiter": "whitespace", "columns": header,
        "rows": len(data), "start_s": float(data[0, 0]), "end_s": float(data[-1, 0]),
        "span_s": float(data[-1, 0] - data[0, 0]),
        "step_s_min_median_max": [float(fn(dt)) for fn in (np.min, np.median, np.max)],
        "nonpositive_steps": int(np.sum(dt <= 0)),
        "gaps_over_0_15_s": int(np.sum(dt > 0.15)),
        "position_min": data[:, 1:4].min(axis=0).tolist(),
        "position_max": data[:, 1:4].max(axis=0).tolist(),
        "path_length_m": float(np.linalg.norm(np.diff(data[:, 1:4], axis=0), axis=1).sum()),
        "quaternion_norm_min_max": [float(norms.min()), float(norms.max())],
        "quality_counts": dict(zip(map(str, values.tolist()), counts.tolist())),
        "first_row": data[0].tolist(), "last_row": data[-1].tolist(),
    }


def inspect_las(path):
    with path.open("rb") as handle:
        header = handle.read(375)
    if header[:4] != b"LASF":
        raise ValueError(f"Not LAS: {path}")
    def unpack(fmt, offset):
        result = struct.unpack_from("<" + fmt, header, offset)
        return result[0] if len(result) == 1 else list(result)
    version = list(header[24:26])
    header_size = unpack("H", 94)
    offset, vlr_count = unpack("II", 96)
    fmt = header[104]
    length = unpack("H", 105)
    count = unpack("I", 107)
    extended_count = unpack("Q", 247) if version >= [1, 4] else None
    if extended_count:
        count = extended_count
    bounds = unpack("6d", 179)
    result = {
        "file": path.name, "version": ".".join(map(str, version)),
        "system_identifier": cstring(header[26:58]),
        "generating_software": cstring(header[58:90]),
        "creation_day_year": unpack("HH", 90),
        "global_encoding": unpack("H", 6), "header_size": header_size,
        "point_data_offset": offset, "vlr_count": vlr_count,
        "evlr_offset": unpack("Q", 235) if version >= [1, 4] else 0,
        "evlr_count": unpack("I", 243) if version >= [1, 4] else 0,
        "point_format": fmt, "point_record_bytes": length,
        "point_count": count, "legacy_point_count": unpack("I", 107),
        "extended_point_count": extended_count,
        "scale": unpack("3d", 131), "offset": unpack("3d", 155),
        "header_min": [bounds[1], bounds[3], bounds[5]],
        "header_max": [bounds[0], bounds[2], bounds[4]],
        "bytes_after_point_records": path.stat().st_size - offset - count * length,
        "has_rgb": fmt in (2, 3, 5, 7, 8, 10),
    }
    # The seven supplied files are uncompressed format 1 with no extra bytes.
    # Fail explicitly on new formats instead of silently misinterpreting them.
    if fmt != 1 or length != 28:
        raise ValueError(f"Full point audit supports only format 1 / 28 bytes: {path}")
    dtype = np.dtype([
        ("xyz", "<i4", (3,)), ("intensity", "<u2"), ("return_flags", "u1"),
        ("classification_flags", "u1"), ("scan_angle", "i1"),
        ("user_data", "u1"), ("point_source_id", "<u2"), ("gps_time", "<f8"),
    ])
    points = np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(count,))
    minima = {k: float("inf") for k in ("gps_time", "intensity", "scan_angle", "point_source_id")}
    maxima = {k: float("-inf") for k in minima}
    xyz_min = np.full(3, np.iinfo(np.int32).max, dtype=np.int64)
    xyz_max = np.full(3, np.iinfo(np.int32).min, dtype=np.int64)
    histograms = {k: np.zeros(256, dtype=np.int64) for k in ("classification_flags", "user_data", "return_flags")}
    nonfinite = 0
    unique_times = set()
    backwards_steps = 0
    last_time = None
    for start in range(0, count, 1_000_000):
        chunk = points[start:start + 1_000_000]
        xyz_min = np.minimum(xyz_min, chunk["xyz"].min(axis=0))
        xyz_max = np.maximum(xyz_max, chunk["xyz"].max(axis=0))
        for key in minima:
            values = chunk[key]
            if key == "gps_time":
                valid = np.isfinite(values)
                nonfinite += int((~valid).sum())
                values = values[valid]
                unique_times.update(np.unique(values).tolist())
                backwards_steps += int(np.sum(np.diff(values) < 0))
                if last_time is not None and len(values) and values[0] < last_time:
                    backwards_steps += 1
                if len(values):
                    last_time = values[-1]
            if len(values):
                minima[key] = min(minima[key], float(values.min()))
                maxima[key] = max(maxima[key], float(values.max()))
        for key, histogram in histograms.items():
            histogram += np.bincount(chunk[key], minlength=256)
    result["full_scan"] = {
        "points_scanned": count,
        "position_min": (xyz_min * result["scale"] + result["offset"]).tolist(),
        "position_max": (xyz_max * result["scale"] + result["offset"]).tolist(),
        "nonfinite_gps_times": nonfinite,
        "distinct_gps_times": len(unique_times), "backwards_time_steps": backwards_steps,
        **{key + "_min_max": [minima[key], maxima[key]] for key in minima},
        **{key + "_counts": {str(i): int(n) for i, n in enumerate(histogram) if n}
           for key, histogram in histograms.items()},
    }
    result["time_at_record_indices"] = {
        str(i): float(points[i]["gps_time"]) for i in sorted(set([0, count // 4, count // 2, 3 * count // 4, count - 1]))
    }
    # One cloud timestamp per trajectory row is an especially useful clock check.
    trajectory_path = path.with_name(path.name.replace("-pointcloud.las", "-trajectory.csv"))
    trajectory_times = np.loadtxt(trajectory_path, skiprows=1, usecols=0)
    ordered_times = np.array(sorted(unique_times))
    if len(ordered_times) == len(trajectory_times):
        result["full_scan"]["max_abs_time_difference_from_corresponding_trajectory_row_s"] = float(
            np.max(np.abs(ordered_times - trajectory_times)))
    return result


def inspect_mov_boxes(path):
    """List container structure without reading multi-GB compressed frame payloads."""
    entries = []
    containers = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"tref", b"edts", b"dinf"}
    with path.open("rb") as handle:
        def walk(start, end, parent=""):
            cursor = start
            while cursor + 8 <= end:
                handle.seek(cursor)
                size, kind = struct.unpack(">I4s", handle.read(8))
                header_size = 8
                if size == 1:
                    size = struct.unpack(">Q", handle.read(8))[0]
                    header_size = 16
                if size == 0:
                    size = end - cursor
                if size < header_size or cursor + size > end:
                    raise ValueError(f"Invalid MOV box at {cursor}: {path}")
                name = parent + "/" + kind.decode("latin1")
                entries.append({"box": name, "offset": cursor, "bytes": size})
                if kind in containers:
                    walk(cursor + header_size, cursor + size, name)
                cursor += size
        walk(0, path.stat().st_size)
    return entries


def inspect_media(path, ffprobe):
    process = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    data = json.loads(process.stdout)
    keep = ("index", "codec_name", "codec_type", "width", "height", "pix_fmt", "r_frame_rate",
            "avg_frame_rate", "time_base", "start_time", "duration", "duration_ts", "nb_frames",
            "color_range", "color_space", "color_transfer", "color_primaries", "tags", "side_data_list")
    return {
        "file": path.name, "format_duration_s": float(data["format"]["duration"]),
        "streams": [{k: stream[k] for k in keep if k in stream} for stream in data["streams"]],
        "format_tags": data["format"].get("tags", {}), "probe_warning": process.stderr.strip(),
        "container_boxes": inspect_mov_boxes(path),
    }


def inspect_flight(folder, ffprobe):
    metadata_path, = folder.glob("*.json")
    data = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    flight = data["flight"]
    trajectory = inspect_trajectory(next(folder.glob("*-trajectory.csv")))
    with next(folder.glob("*-pose.csv")).open(encoding="utf-8-sig", newline="") as handle:
        pose_rows = list(csv.reader(handle))
    las = inspect_las(next(folder.glob("*.las")))
    rgb = [inspect_media(folder / name, ffprobe) for name in flight["files"]["rgb_videos"]]
    thermal = inspect_media(folder / flight["files"]["thermal_video"], ffprobe)
    sync = flight["time_sync"]
    duration = sum(float(m["streams"][0]["duration"]) for m in rgb)
    # Hypothesis, not a verified vendor clock convention. Store both signs of AV offset.
    offset_s = sync["video_offset"] / 1e6
    av_s = sync["av_offset"] / 1e6
    timing = {}
    for name, origin in (("video_offset_only", offset_s), ("video_plus_av_offset", offset_s + av_s),
                         ("video_minus_av_offset", offset_s - av_s)):
        timing[name] = {
            "candidate_video_start_s": origin, "candidate_video_end_s": origin + duration,
            "end_minus_trajectory_end_s": origin + duration - trajectory["end_s"],
        }
    maps = data.get("maps", [])
    if isinstance(maps, dict):
        maps = [maps]
    return {
        "folder": folder.name, "name": flight["name"], "file_inventory": [
            {"file": p.name, "bytes": p.stat().st_size} for p in sorted(folder.iterdir()) if p.is_file()
        ],
        "metadata": {
            "file": metadata_path.name, "flight_count": flight["flight_count"],
            "datetime_utc": flight["datetime_utc"], "duration_raw": flight["duration"],
            "time_sync_raw": sync, "relocalized": flight["flags"].get("relocalized"),
            "flight_transform": flight.get("transform"),
            "map_sources": [m.get("source") for m in maps],
            "map_transforms": [m.get("transform") for m in maps],
            "snapshot_count": len(data.get("snapshots", [])),
            "image_count": flight["files"].get("image_count"),
            "file_references": flight["files"],
            "estimation_stats": flight.get("stats", {}).get("estimation"),
            "inspection_stats": flight.get("stats", {}).get("inspection"),
        },
        "trajectory": trajectory, "pose": {"columns": pose_rows[0], "rows": len(pose_rows) - 1},
        "las": las, "rgb_media": rgb, "thermal_media": thermal,
        "rgb_duration_s": duration,
        "timing_hypotheses_NOT_VALIDATED": timing,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("Flight_Data"))
    parser.add_argument("--output", type=Path, default=Path("analysis/flight_inventory.json"))
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    if args.root.resolve() == args.output.resolve() or args.root.resolve() in args.output.resolve().parents:
        parser.error("Output must be outside the input data directory")
    folders = sorted(p for p in args.root.iterdir() if p.is_dir())
    if not folders:
        parser.error("No flight directories found")
    flights = []
    for folder in folders:
        print(f"Inspecting {folder.name}", flush=True)
        flights.append(inspect_flight(folder, args.ffprobe))
    report = {
        "scope": "All trajectory rows, JSON metadata, full LAS point-record scan, ffprobe stream/container metadata. No proprietary video-payload decoding.",
        "flight_count": len(flights),
        "total_bytes": sum(p["bytes"] for f in flights for p in f["file_inventory"]),
        "total_points": sum(f["las"]["point_count"] for f in flights),
        "total_rgb_duration_s": sum(f["rgb_duration_s"] for f in flights),
        "flights": flights,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}; {report['total_points']:,} LAS points across {len(flights)} flights")


if __name__ == "__main__":
    main()
