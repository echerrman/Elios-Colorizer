# Inspector 5 export audit

**Historical report:** the seven export folders have since been replaced. See [Native flight findings](native_flight/NATIVE_FLIGHT_FINDINGS.md) for the current folder and the recovered tilt/distance telemetry.

Inspected September 21, 2026. Source: all seven folders under `Flight_Data`.

**The exports provide the mapped points, position/orientation trajectory, 4K video, and promising synchronization metadata. They do not expose usable camera-head tilt or forward-distance time series, or a complete RGB camera calibration. An original Inspector flight folder is the next input needed.**

Direct projection of video colors onto the existing LAS is the recommended first implementation. The forward-distance sensor is optional for this approach; camera pose, calibration, synchronization, and visibility checks are essential.

## 1. Inventory and parameter mapping

48 files, 35.57 GB (33.13 GiB), 119,553,425 LAS points, 29,950 trajectory rows, and 48 minutes 6.15 seconds of RGB video. Flights 1–6 each have two MOV segments; Flight 7 has one. Each flight also contains a LAS, trajectory CSV, pose CSV, thermal MP4, and JSON metadata file.

| Required input | Source in these exports | Result and limitations |
|---|---|---|
| Drone/SLAM position | `*-trajectory.csv`: `pos_x[m]`, `pos_y[m]`, `pos_z[m]` | Present at approximately 10 Hz. Coordinates explicitly use meters. The exact tracked sensor/body origin still needs verification. |
| Drone/SLAM attitude | Same file: `rot_x`, `rot_y`, `rot_z`, `rot_w` | Present as unit quaternions in x/y/z/w column order. Axis conventions and transform direction are undocumented in the export. This is an orientation estimate, not raw inclinometer/IMU measurements. |
| RGB imagery | `*-video*.MOV` | All 13 segments: H.264, 3840 × 2160, 30000/1001 fps (about 29.97), 8-bit YUV 4:2:0. Can decode to RGB frames. |
| Recording camera tilt | No populated source identified | No tilt column in trajectory, no pose records, no relevant time series in JSON, and no telemetry stream exposed by ffprobe. Body attitude and MOV display rotation do not supply camera-head pitch. |
| Forward camera distance | No populated source identified | Neither CSV nor JSON contains these measurements. Distance-related flight statistics describe travel/planning, not timestamped camera-to-surface measurements. |
| Existing mapped points | `*-pointcloud.las` | Present: LAS 1.4, point format 1, 28-byte records, XYZ, intensity, and time. No RGB channels. |
| Frame/trajectory synchronization | JSON: `flight.time_sync` | Candidate video-to-map clock offset identified and checked against all seven durations; frame-level verification remains necessary. |
| RGB camera intrinsics/distortion | Calibration file references in JSON | Referenced paths are null; no corresponding files are present. |
| Camera-to-body geometry | JSON estimation statistics and absent calibration references | Some static IMU/LiDAR rotation quaternions exist. They do not constitute a calibrated RGB camera transform, tilt mechanism, and lens model. |

The filenames ending in `trajectory.csv` are **whitespace-delimited**, despite their extension. Their header is:

```text
timestamp[s] pos_x[m] pos_y[m] pos_z[m] rot_x rot_y rot_z rot_w quality
```

All timestamps increase, with a median step of 0.1 seconds and observed steps of 0.095–0.110 seconds. There are no gaps greater than 0.15 seconds. Quaternion norm error is below 0.000001 in all flights. Every trajectory quality value is `1`; the export does not define its meaning, so this is not proof of geometric accuracy.

All seven `pose.csv` files are 54 bytes and contain only:

```text
ImageName,X,Y,Z,Omega,Phi,Kappa,AccuracyXY,AccuracyZ
```

They supply no poses. Each JSON also has zero snapshots and zero exported still images.

## 2. Per-flight measurements

Times below are seconds on the exported trajectory clock, not UTC or seconds since takeoff. Video durations sum the segments in the JSON `rgb_videos` order.

| Flight | LAS points | Trajectory rows | Trajectory start–end (s) | RGB duration (s) | MOV segments |
|---|---:|---:|---:|---:|---:|
| 1 | 21,289,639 | 4,941 | 304.326–798.336 | 478.744933 | 2 |
| 2 | 18,082,726 | 4,622 | 185.952–648.071 | 446.579467 | 2 |
| 3 | 18,843,052 | 4,571 | 102.008–558.986 | 441.574467 | 2 |
| 4 | 16,539,920 | 4,040 | 146.328–550.215 | 388.454733 | 2 |
| 5 | 18,731,172 | 4,742 | 284.975–759.054 | 458.591467 | 2 |
| 6 | 16,667,755 | 4,393 | 249.953–689.161 | 423.689933 | 2 |
| 7 | 9,399,161 | 2,641 | 123.784–387.789 | 248.514933 | 1 |

Thermal videos are 160 × 120, approximately 8.66–8.71 fps. They are unnecessary for RGB colorization. Their similar durations are an additional timing clue, but their detailed clock relationship has not been verified.

## 3. Timing: verified relationship and remaining hypothesis

**Verified:** each LAS contains exactly as many distinct time values as its corresponding trajectory has rows. Sorted LAS times match the trajectory timestamps within 0.0005 seconds in every flight, consistent with trajectory timestamps rounded to milliseconds. LAS times are finite and nondecreasing throughout all 119.6 million records. This strongly establishes a shared LAS/trajectory clock.

Multiple points share each LAS time. These are effectively scan/group timestamps in this export, rather than unique acquisition times for every laser return.

**Strongly supported, not yet frame-validated:** JSON `flight.time_sync` values are in microseconds, and the candidate mapping is:

```text
t_map_seconds ≈ flight.time_sync.video_offset / 1_000_000
                + cumulative_duration_of_previous_RGB_segments
                + current_segment_frame_PTS_seconds
```

| Flight | Candidate RGB origin on map clock (s) | RGB origin minus first trajectory time (s) | Candidate video end minus last trajectory time (s) |
|---|---:|---:|---:|
| 1 | 319.790390 | 15.464390 | +0.199323 |
| 2 | 201.517568 | 15.565568 | +0.026035 |
| 3 | 117.565543 | 15.557543 | +0.154010 |
| 4 | 161.898497 | 15.570497 | +0.138230 |
| 5 | 300.522149 | 15.547149 | +0.059616 |
| 6 | 265.591617 | 15.638617 | +0.120550 |
| 7 | 139.346736 | 15.562736 | +0.072669 |

For example, Flight 7 video at 90 seconds corresponds provisionally to trajectory time **229.346736 seconds**, not 213.784 seconds. Simply subtracting the first trajectory timestamp would introduce about 15.56 seconds of error.

`av_offset` is approximately −6.3 seconds and appears to describe another clock relationship. Adding or subtracting it in the video mapping worsens endpoint agreement to roughly six seconds. Do not apply it again without a documented/log-verified reason. `obc_offset` and `vio_offset` are null. The JSON `duration` also differs from measured video duration; it is not a replacement for frame PTS.

All MOV segments start their local PTS at zero. Use the JSON segment ordering and exact frame timing, not filenames alone or an assumed integer 30 fps. Segment continuity and the final offset must be checked against visible motion/landmarks. Close endpoint agreement does not establish frame-accurate synchronization or exclude a small rate error.

## 4. Camera and video findings

Every RGB segment has one exposed video stream and a **−180° display matrix rotation**. FFmpeg normally honors this when decoding. Decide whether calibration describes sensor pixels or display-oriented pixels, then use that convention consistently. This static rotation tag is unrelated to dynamic camera tilt.

The videos report `yuvj420p` and full-range color. Frame decoding should preserve the declared color handling. A decoded, display-oriented example is saved as [Flight 7 at 90 seconds](frames/flight7_90s_display.jpg); it contains usable inspection imagery without a telemetry overlay in that sampled frame. It also illustrates wide-angle distortion and strong illumination variation, both relevant to projection quality.

ffprobe exposes no audio, subtitle, or telemetry stream in the RGB files. Container inspection finds one video track and standard timing/sample boxes, with no separate telemetry track or UUID metadata box. A dangling chapter reference causes ffprobe's “Referenced QT chapter track not found” warning; the sampled video still decodes successfully. This audit did not reverse-engineer proprietary data inside compressed H.264 payloads or padding, so it does not prove that every possible hidden encoding is absent.

The JSON does retain these static quaternions under `flight.stats.estimation`:

- `body_dsm_to_body_rigid_imu_extrinsics`
- `body_to_vio_imu_extrinsics`
- `lidar_to_body_imu_extrinsics`

They may help establish coordinate conventions later. They do not include the complete recording-camera translation, tilt pivot/axis/zero, dynamic tilt samples, focal lengths, principal point, and distortion coefficients. Do not assume a calibration file named `camera_0` necessarily belongs to the RGB inspection camera rather than a VIO camera.

## 5. LAS properties and geometry considerations

All seven LAS files have system identifier `OS0`, XYZ scale factors of 0.001, zero offsets, zero VLRs/EVLRs, and no bytes beyond the declared point records. The 0.001 scale specifies coordinate storage resolution, not 1 mm measurement accuracy. No CRS or extra calibration attributes are embedded.

Intensity varies, but classification/flags, return flags, user data, scan angle, and point source ID are zero throughout. There is no populated per-point reliability score and no usable camera pitch in the LAS scan-angle field. Preserve intensity as its own attribute; it is not RGB.

The field named `GPS Time` holds the trajectory-like seconds described above. The header has global encoding zero and a placeholder creation date of day 1, 1970. Treat its numeric values as the observed flight clock; do not infer a GPS/UTC epoch from the standard field name alone. The standard describes format 1 and RGB-bearing alternatives in the [ASPRS LAS specification](https://www.asprs.org/wp-content/uploads/2019/07/LAS_1_4_r15.pdf).

All flights report `relocalized: true`, map source `Flyaware`, and null flight/map transforms. This suggests exported alignment may already be applied, but does not establish camera/body axis conventions or prove cross-flight registration. Validate projection within one flight before blending colors across flights.

The point clouds remain the best available geometry, but they are not independently certified by this audit. Flight 7 includes points down to Z = −48.777 and X = −52.701, while its trajectory Z is approximately −0.018 to +4.713. Those may be distant returns, openings, or outliers; inspect their distribution before using them for visibility. Do not discard them based on bounds alone. Small differences between header bounds and decoded coordinate extrema are consistent with sub-millimeter/one-grid-step export rounding.

## 6. Which proposed approach to implement

**Approach 1: project the existing points into calibrated RGB frames.** Interpolate position and quaternion orientation at each selected video time, apply the calibrated body-to-camera geometry and measured camera tilt, transform LAS points into camera coordinates, and project through the calibrated lens model. Reject points behind the camera or hidden by closer surfaces. Choose/blend observations using visibility, viewing angle, image sharpness, exposure, and distance. Preserve the source geometry, intensity, and timestamps and write RGB into a compatible LAS point format, such as format 3 for the current format-1 attributes.

Point acquisition time need not restrict a point to one video frame. In a static scene, any later or earlier frame with a known camera pose and a clear view can provide color. Timestamps are essential to determine the camera pose for each image.

**Approach 2: a single distance reading is insufficient to reconstruct an entire image's depth.** Even a perfect central range constrains only the sampled surface near that direction. Assigning it to all pixels imposes a plane or another unsupported surface model. Pipes, railings, corners, and foreground/background surfaces will be misplaced; nearest-neighbor transfer then spreads those errors onto the accurate LAS. Actual multiview reconstruction would require additional geometry estimation, beyond the single range measurement.

Flyability describes the Elios 3 sensor as following camera-head pitch and measuring from the camera front to the nearest surface near the center of view. Thus the user's interpretation is broadly correct, but the measurement is not a dense depth map. The manual's distance-lock control limits are not a general sensor-accuracy specification. See [Elios 3 User Manual, section 4.9](https://www.flyability.com/hubfs/Knowledge%20Base%20Files/Documents/Manuals/E3%20and%20equipments/Elios%203%20User%20Manual%20v1.3.pdf).

If recovered, use this range as a supplementary calibration/synchronization check or to validate the central ray against the LAS. Its actual reliability cannot be evaluated without the measurements, their validity indicators, and corresponding geometry.

## 7. What to provide next

Start with the **complete original Inspector folder for Flight 7**, retaining subfolders and metadata. It is the smallest flight and avoids a video-segment join. Flight 1 can follow for testing segmented video. Existing MOV/LAS copies need not be duplicated if the original metadata and other files can be supplied with their original relationships preserved.

The highest-value candidates are the files referred to by the following currently empty JSON entries:

| Reference in `flight.files` | Purpose of inspecting the original file |
|---|---|
| `av_log`, `av_log_file`, `flight_log`, `flight_log_file` | Find timestamped camera-head pitch, forward range, validity flags, and clock definitions. Exact fields/encoding are not yet known. |
| `calib_files`, `calib_files_folder`, `calibration_camera_0_file` through `_2_file` | Identify available camera intrinsics/distortion and determine which camera each describes. The RGB calibration may be elsewhere. |
| `calibration_imu_cam_extrinsics_file`, `calibration_static_transforms_file`, `calibration_geoslam_transforms_file` | Resolve sensor/body/map axes, translations, and camera mounting/tilt geometry where available. |
| `obc_bag`, `obc_bag_file`, `lidar_metadata`, `lidar_metadata_file` | Inspect topic/schema inventories and calibration/time metadata if logs alone are insufficient. A bag is a candidate, not a guarantee that tilt/range is recorded there. |
| `av_log_debug_file` | Additional decoding/timing evidence if present; current exports mark it skipped. |

The [Inspector 5 manual](https://www.flyability.com/hubfs/Knowledge%20Base%20Files/Documents/Manuals/Inspector%205/Inspector%205.0%20-%20User%20Manual%20v1.0.pdf) distinguishes selected flight-data exports from workspace exports and describes separate storage of camera video and SSD logs. That supports inspecting the original project before attempting to infer missing telemetry from imagery. Flyability also documents an [export option for debug files](https://knowledge.flyability.com/aircraft/elios-3/elios-3-troubleshooting/elios-3-retrieve-data-for-support), if the originals lack them.

After locating these files, the next development milestone should be a small point-to-image overlay on several Flight 7 frames. That will test time offsets, quaternion conventions, tilt signs, lens distortion, and occlusion before running full-cloud colorization.

## 8. Reproducibility and inspection scope

- [Machine-readable inventory](flight_inventory.json): per-file sizes, trajectory checks, full LAS record statistics, media metadata/container boxes, and explicitly unvalidated timing hypotheses.
- [Audit script](../tools/audit_flights.py): reads the exports without modifying them. Requires Python 3.10+, NumPy, and ffprobe. Its full LAS scan intentionally supports the observed uncompressed format 1 / 28-byte records and rejects other layouts rather than silently misreading them.
- [Decoded frame](frames/flight7_90s_display.jpg): downscaled 1280 × 720 preview extracted with FFmpeg auto-rotation at local video time 90 seconds.

From the project root, run:

```powershell
python tools/audit_flights.py --ffprobe C:\ffmpeg\bin\ffprobe.exe
```

The bundled Python runtime was used for this audit because the system Python launcher reports no installed Python. The scan read every trajectory row and LAS point record and probed all 20 media files. It did not fully decode all 48 minutes of video, recover proprietary telemetry, validate spatial registration, or perform colorization. Original flight data was not changed.
