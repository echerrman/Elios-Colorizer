# Performance design and benchmark

The colorizer is accelerated on the CPU while preserving the existing projection result. CUDA is not required and is not bundled.

## Why low CPU usage occurred

The original implementation processed one frame and point chunk at a time. On a 32-logical-processor computer, one fully active core appears as only about 3% total CPU. LAS reading, video decoding, final LAS writing, and memory movement can also wait on storage or memory bandwidth, so 100% CPU is neither expected nor a useful target by itself.

Simply increasing the work unit did not help. With eight fixed real frames and the 1,064,482-point Flight 1 sample:

| Configuration | Projection seconds | Relative to original |
|---|---:|---:|
| 250k points / 4 views | 6.250 | 1.00× |
| 500k points / 8 views | 8.583 | 0.73× |
| 1M points / 8 views | 9.342 | 0.67× |
| 1M points / 12 views | 9.705 | 0.64× |

Larger spatial chunks have looser bounding boxes, so visibility pruning retains more points. Larger view batches combine more camera frusta, making still more chunks active. Both changes increased projection work.

## Implemented acceleration

- Retain 250,000-point chunks and four-view batches.
- Decode source XYZ once into a float64 in-memory array when current available physical memory provides safe headroom.
- Run per-frame visibility updates concurrently because each frame owns its depth buffer.
- Run color assignment for different LAS chunks concurrently because their output point ranges do not overlap.
- Preserve chronological frame order within each chunk, so equal-quality tie behavior stays unchanged.
- Cap concurrency at four workers. Eight streamed workers improved only marginally over four, while the four-worker coordinate cache was fastest and used fewer concurrent work buffers.
- Submit at most one worker-width group at a time, keeping cancellation responsive and preventing a full flight of pending chunks from accumulating.
- Fall back automatically to the existing streamed path if caching would use more than 35% of safely available physical memory, leave less than 2 GiB headroom, or require more than 4 GiB for XYZ alone.

After the coordinate cache and worker implementation, a 16-frame benchmark was run three times per configuration with rotated execution order. The table reports the median:

| Configuration | Projection seconds | Speedup |
|---|---:|---:|
| Streamed / 1 worker | 15.874 | 1.00× |
| Streamed / 2 workers | 10.894 | 1.46× |
| Streamed / 4 workers | 8.072 | 1.97× |
| Streamed / 8 workers | 7.845 | 2.02× |
| Cached / 4 workers | 7.147 | 2.22× |

The benchmark reused the same decoded observations, rotated configuration order to reduce cache/order bias, and verified every output after every run. `tools/benchmark_performance.py` reproduces the benchmark when fresh measurements are needed.

## Output equivalence and limits

The benchmark compares `red`, `green`, `blue`, and `Colorized` for every point. All configurations were identical. A 16-frame, 1,064,482-point diagnostic result was also rerun through the accelerated service path: all raw X/Y/Z, RGB, and `Colorized` values matched the pre-acceleration LAS exactly; both contained 653,972 colored points.

The acceleration does not change frame sampling, occlusion thresholds, projection math, exposure filtering, quality scores, or camera calibration. It does not make the unvalidated camera profile more accurate.

End-to-end processing contains serial sections. A cold telemetry cache requires an MCAP scan, individual video frames must be sought and decoded, LAS is indexed once, and output is written sequentially. CPU use will rise during projection and fall during those stages. The processing report records whether the XYZ cache was used, the worker count, logical CPUs detected, and the unchanged chunk/batch settings.
