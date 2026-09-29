"""Optional synthetic benchmark; writes temporary data only under project .cache."""
from dataclasses import replace
from pathlib import Path
import json
import runpy
import statistics
import tempfile
import time

from elios_colorizer.colorize import ColorizationOptions, colorize_las


def main():
    root = Path(__file__).resolve().parents[1]
    scene = runpy.run_path(str(root / 'tests/test_illumination.py'))['scene']
    cache = root / '.cache'
    cache.mkdir(exist_ok=True)
    times = {False: [], True: []}
    with tempfile.TemporaryDirectory(dir=cache) as folder:
        work = Path(folder)
        camera, frames = scene(work / 'source.las', 1_000_000)
        options = ColorizationOptions(minimum_sharpness=0, frame_batch_size=3,
            depth_buffer_width=200, occlusion_radius_pixels=0, worker_threads=4)
        for index, enabled in enumerate([False, True, True, False, False, True]):
            output = work / 'result.las'
            start = time.perf_counter()
            result = colorize_las(work / 'source.las', output, frames, camera,
                options=replace(options, illumination_balancing=enabled))
            elapsed = time.perf_counter() - start
            times[enabled].append(elapsed)
            output.unlink()
            print(json.dumps(dict(enabled=enabled, seconds=elapsed,
                                  applied=result.illumination_balancing['applied'])), flush=True)
    off, on = statistics.median(times[False]), statistics.median(times[True])
    print(json.dumps(dict(disabled_median=off, enabled_median=on, overhead_percent=100 * (on / off - 1))))


if __name__ == '__main__':
    main()
