"""Benchmark execution-only settings and verify byte-identical color fields.

The camera model, visibility rules, quality scoring, and frame observations are
held constant. Only LAS chunk and frame-batch sizes vary.
"""
from dataclasses import replace
import json
from pathlib import Path
import statistics
import tempfile
import time

import laspy
import numpy as np

from elios_colorizer.camera import Calibration
from elios_colorizer.colorize import ColorizationOptions, colorize_las
from elios_colorizer.flight import discover_source, iter_observations, load_telemetry


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'outputs' / 'performance-benchmark'
    output.mkdir(parents=True, exist_ok=True)
    source = discover_source(root / 'Flight_Data')
    source.las_path = root / 'outputs' / 'diagnostics' / 'flight1_sample_source.las'
    camera = Calibration.load(root / 'outputs' / 'diagnostics' / 'camera-candidates' /
                              '01_baseline_1600_square_pinhole.json', require_validated=False)
    telemetry = load_telemetry(source, root / '.cache')
    print('Decoding a fixed set of sixteen real 4K observations...', flush=True)
    frames = list(iter_observations(source, telemetry, start_s=15, end_s=175,
                                   sample_interval_s=10, max_frames=16))
    configurations = [
        ('baseline', 250_000, 4, False, 1),
        ('streamed_2_workers', 250_000, 4, False, 2),
        ('streamed_4_workers', 250_000, 4, False, 4),
        ('streamed_8_workers', 250_000, 4, False, 8),
        ('cached_4_workers', 250_000, 4, True, 4),
    ]
    measurements = {item[0]: [] for item in configurations}
    metadata = {item[0]: item[1:] for item in configurations}
    reference = None
    # Rotate order on each round to reduce thermal, filesystem-cache, and
    # background-load bias. Scratch output stays outside a synced workspace.
    with tempfile.TemporaryDirectory(prefix='elios-benchmark-') as scratch:
      scratch = Path(scratch)
      for round_index in range(3):
        order = configurations[round_index:] + configurations[:round_index]
        for name, chunk, batch, cache, workers in order:
            path = scratch / f'{round_index}-{name}.las'
            started = time.perf_counter()
            result = colorize_las(source.las_path, path, frames, camera,
                options=ColorizationOptions(chunk_size=chunk, frame_batch_size=batch,
                                            experimental_calibration=True, cache_xyz=cache,
                                            worker_threads=workers))
            elapsed = time.perf_counter() - started
            cloud = laspy.read(path)
            fields = np.column_stack([cloud.red, cloud.green, cloud.blue, cloud.Colorized])
            if reference is None:
                reference = fields.copy()
            identical = bool(np.array_equal(fields, reference))
            measurements[name].append(elapsed)
            print(json.dumps(dict(round=round_index + 1, name=name, seconds=round(elapsed, 3),
                                  identical=identical)), flush=True)
            if not identical:
                raise RuntimeError(f'{name} changed colorization output')
    results = []
    for name, values in measurements.items():
        chunk, batch, cache, workers = metadata[name]
        results.append(dict(name=name, chunk_size=chunk, frame_batch_size=batch,
                            cache_xyz=cache, worker_threads=workers,
                            seconds=round(statistics.median(values), 3),
                            trial_seconds=[round(value, 3) for value in values],
                            colored_points=int(np.count_nonzero(reference[:, 3])),
                            identical_color_fields_to_baseline=True))
    baseline = results[0]['seconds']
    for item in results:
        item['speedup_vs_baseline'] = round(baseline / item['seconds'], 3)
    (output / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2), flush=True)


if __name__ == '__main__':
    main()
