# Elios Colorizer

A local Windows desktop application that projects synchronized RGB video onto an existing Inspector LAS point cloud. It preserves the original geometry and attributes, adds 16-bit RGB, and writes a new LAS plus a processing report. No cloud account, ROS installation, or external FFmpeg installation is required by the portable build.

## Open the application

Double-click **Launch Elios Colorizer.cmd**, or open **dist/EliosColorizer/EliosColorizer.exe**. When copying to another Windows computer, copy the entire `dist/EliosColorizer` folder, including `_internal`; the EXE alone is not sufficient. The development launcher uses `.venv` when a portable build is unavailable.

1. Select a native Inspector flight folder, its matching export folder, or their common parent. Native/export folders are paired only when their metadata contains the same flight UUID.
2. Check the input and dependency panels. If the LAS is stored elsewhere, select it using the point-cloud browse button.
3. Select a measured and verified **RGB camera calibration profile**, once per camera/recording mode. Later launches remember the selected profile. Confirm that it applies to the selected drone.
4. Choose a new `.las` output filename, then select **Colorize point cloud**. Progress and cancellation remain available while processing. Existing files are not overwritten.

**Current Flight 1 limitation:** all flight inputs are available, but a validated 4K RGB lens/mount/tilt calibration is not present. Normal processing intentionally reports this as a missing input. The included YAMLs are for the navigation cameras. The desktop build is ready for interface and input-discovery testing; accurate final-flight colorization still needs RGB calibration. See [the calibration guide](docs/CALIBRATION.md).

## Camera profiles

The two experimental Flight 1 profiles are retained in `camera_profiles`. The baseline profile is the original development estimate; neither profile is a measured production calibration. Select one explicitly when performing an experimental comparison.

## Inputs and processing

| Input | Source used |
|---|---|
| Point positions and original attributes | Inspector-exported LAS / LAZ in the original local flight coordinate system |
| Body position and attitude | MCAP `/kalman_scan2map_node/odometry`, using `header.stamp`; exported trajectory is a fallback |
| Camera tilt | StarNet `Camera.CameraState.cameraPitch`, in degrees |
| RGB | Ordered native video segments, with display rotation applied once |
| Synchronization | StarNet `Camera.VideoFrameSync`; frames without recorded timestamps are skipped |
| Camera optics and mounting | Reusable RGB calibration JSON; pinhole, OpenCV distortion, or fisheye models |
| Forward range | Not used for geometry or color placement |

The app samples video at approximately one-second intervals. It interpolates position and camera-state pitch and uses quaternion SLERP for attitude. It rejects low-confidence poses and gaps over 0.35 seconds rather than extrapolating. No smoothing is applied that would delay real camera motion; high-rate pitch commands are retained for diagnostics instead of being assumed to be physical angle measurements.

Clock checks compare the metadata video origin, StarNet video-offset records, per-frame timing, and the camera's recording seconds counter. The MCAP and video are not assumed to begin together. For Flight 1, video zero is 15.441285 seconds after MCAP start. See [the synchronization audit](docs/TIME_SYNCHRONIZATION.md) for evidence and remaining subframe uncertainty.

For each small frame batch it builds depth buffers from the LAS, rejects occluded, blurred, or severely exposed observations, and selects the best usable color per point based on image sharpness, distance, image position, and exposure. RGB channels use the LAS 16-bit range. Unobserved points remain RGB zero with an explicit `Colorized=0` attribute. Source points are neither moved nor removed, and color is not filled across unseen regions.

Processing uses LAS chunks and disk-backed color buffers. Temporary working files reside beside the output; allow roughly **1 GB of free space for this 21.3-million-point flight**, plus headroom. Full-flight runtime depends on point count, frame count, disk speed, and CPU. Telemetry is cached outside flight folders under the system temporary directory. Set `ELIOS_COLORIZER_CACHE` to choose a cache directory in development.

Version 0.2 adds automatic CPU acceleration without changing projection or scoring. The app keeps 250,000-point chunks and four-view batches because larger values were slower on the real Flight 1 sample. When safe memory headroom is available, it decodes XYZ once into an in-memory cache and uses up to four CPU workers for independent visibility frames and disjoint point chunks. It automatically falls back to the original bounded-memory streaming path on smaller-memory systems. There is no user setting to tune and no CUDA dependency.

The repeatable benchmark in `tools/benchmark_performance.py` used 16 fixed 4K Flight 1 observations and 1,064,482 uniformly sampled source points. Across three rotated trials, the cached four-worker configuration completed the projection portion in a median 7.147 seconds versus 15.874 seconds for the single-worker streamed baseline, a **2.22× speedup**. Eight streamed workers reached only 2.02×, so the application caps concurrency at four to avoid extra memory pressure for negligible benefit. Every RGB channel and `Colorized` flag was identical across all configurations. The longer end-to-end runtime also includes video seeking/decoding, telemetry extraction on a cold cache, LAS indexing, and final LAS writing, so overall speedup varies.

## Limits of this version

- Supports the observed Inspector native layout and StarNet Camera schema version 4. Unknown camera schemas are rejected with an explanation.
- Requires video-time and pose-time overlap, and LAS coordinates in the same local frame as the trajectory. A georeferenced or externally transformed cloud needs an explicit transform before use.
- The FrameSync index-to-decoded-frame convention and camera mechanical response require validation across motion and the video segment boundary. Flight 1 has two more encoded frames than synchronization records; these are reported and skipped.
- The renderer approximates visibility with a point-cloud depth buffer. Thin structures, sparse surfaces, moving objects, rolling-shutter effects, lighting changes, and view seams can still affect appearance.
- No automatic camera self-calibration or interactive point-cloud viewer is included yet. `validated: true` is a profile provenance assertion, not an automatic optical accuracy certificate.
- Output is LAS. LAZ input is supported. E57 and waveform LAS are not supported.

## Development

Use Python 3.12 x64. Run `scripts/setup.ps1`, then `python -m pytest` inside `.venv`. Build the portable folder with `scripts/build.ps1`. Exact installed versions are in `requirements-lock.txt`. The application modules are under `src/elios_colorizer`; meaningful tests are under `tests`.

```powershell
$env:PYTHONPATH = 'src'
& .\.venv\Scripts\python.exe -m elios_colorizer.cli inspect Flight_Data
& .\.venv\Scripts\python.exe -m elios_colorizer.cli colorize Flight_Data outputs\result.las --calibration measured_rgb_profile.json
```

For bounded development runs, use `--start`, `--end` (video elapsed seconds), `--interval`, and `--max-frames`. `--experimental` permits an explicitly unvalidated profile only for diagnostics and marks the report accordingly. It is deliberately not the normal desktop workflow.

`tools/flight_smoke.py` reproduces the real-flight sample and alignment images. `EliosColorizer.exe --self-test <new-output-directory>` runs a deterministic synthetic flight through the packaged application and writes `selftest.json`, a tiny colored LAS, and a Qt-rendered screenshot. Synthetic geometry is known, so this validates the projection implementation and packaging without claiming that real-camera calibration is solved.

The original data audit is in [docs/FLIGHT_DATA_FINDINGS.md](docs/FLIGHT_DATA_FINDINGS.md); its scripts are in `tools/research/`. User flight files remain under `Flight_Data/` and are never modified. See [third-party notices](THIRD_PARTY_NOTICES.md) for bundled components.
