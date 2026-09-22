# Flight 1 time synchronization audit

The application does not align streams by making each file start at zero. It maps each decoded RGB frame to its recorded StarNet `VideoFrameSync` flight-clock timestamp, then queries body pose and camera-state pitch on that clock. Different recordings have different start/end times; only their common interval is usable.

## Confirmed clock mapping

| Reference | Flight/OBC clock seconds |
|---|---:|
| MCAP recording begins | 304.349104896 |
| Video elapsed 0:00 / first FrameSync | 319.790390 |
| Last pose sensor timestamp | 798.335606 |
| Last FrameSync timestamp | 798.415919 |
| MCAP recording ends | 798.468746304 |

**Video 0:00 is 15.441285104 seconds after the MCAP recording begins.** If Foxglove displays time relative to the start of this MCAP, a video event at elapsed `v` occurs at approximately `v + 15.441285104` on that display. If Foxglove displays the raw flight timestamp, it occurs approximately at `v + 319.790390`. The application uses each recorded frame timestamp instead of these approximate constant-offset formulas, preserving the measured clock drift.

The native metadata's `video_offset` and all **239** StarNet `Camera.VideoOffset` records agree exactly at **319790390 microseconds**. The embedded flight schema states that log and OBC time are aligned. `av_offset = −6.350207 s` describes avionics-clock origin and is **not applied to the OBC/StarNet timestamps used here**.

The camera's own recording elapsed-time counter provides a separate coarse check: 4,725 recording-state samples agree with video elapsed time within their integer-second quantization. Counter-minus-elapsed ranges from −1.005458 to +0.001302 seconds, with median −0.500879 seconds, as expected for a seconds counter that advances once per second.

## MCAP arrival time is not the sensor timestamp

Pose uses `header.stamp`, not MCAP `log_time`. The median difference is **71.05 ms**, with a observed maximum of 157.18 ms. Using recording/arrival time for pose would introduce a real error.

The servo message is a headerless Float32. After negating its value, **52,198 consecutive value changes match the StarNet camera-command changes exactly and in the same order**. MCAP arrival minus StarNet change time has minimum 0.219 ms, median **1.380 ms**, 95th percentile 5.848 ms, and maximum 73.430 ms. This demonstrates a shared clock with recording delay; it does not prove that the command equals the physical camera angle at that instant. The application uses reported `CameraState.cameraPitch` as its tilt reference instead.

## Independent image-motion check

`tools/audit_video_timing.py` compares feature-derived relative image rotation with recorded body-plus-camera-head rotation. It sampled image pairs from 25–451 seconds of video and tested additional shifts of ±20 seconds, three assumed focal lengths, and both head-angle signs. Between 170 and 178 geometrically supported image pairs remained per lens hypothesis.

All six coarse scans favored zero additional shift on a 50 ms search grid, with rank correlations of approximately 0.85–0.90. This supports the recorded mapping and argues against a seconds-long residual offset.

**This is not frame-perfect verification.** A finer 5 ms grid with one assumed camera model favored −60 ms globally, but different time windows favored −225 ms, −65 ms, and +10 ms, with only small improvements over zero. Those inconsistent results can be affected by uncalibrated optics, translational motion, quantized head angles, mechanical response, and image-estimation error. They do not justify applying a universal −60 ms correction. No such correction was applied.

FrameSync has **14,346** records while the video has **14,348** encoded frames. The current convention maps counter 1 to decoded global frame 0. The two frames without timestamps are skipped. Three additional timestamped tail frames fall beyond the last valid pose and are also skipped. The common recorded interval is **319.790390–798.335606 s**. Missing tails are never stretched or extrapolated to force equal durations.

## Application safeguards added

- Reject metadata video offset versus first FrameSync disagreement above 10 ms.
- Cross-check StarNet VideoOffset records against the same origin.
- Reject a large disagreement between the recording seconds counter and that origin.
- Reject an unfamiliar nonzero OBC/log offset rather than guessing its sign.
- Reject frame counters beyond available footage and skip untimestamped frames.
- Use sensor timestamps, validate confidence and gaps, and refuse extrapolation.
- Record clock origins, ignored avionics offset, arrival-delay statistics, shared interval, and skipped-frame counts in each processing report.

The known clock-origin offsets are handled and supported by independent data. Remaining work is physical exposure/frame-index and camera-response validation alongside RGB calibration. A calibrated target or clearly identifiable tilt event with times recorded in Inspector and Foxglove can provide a stronger check. Accurate final colorization should not be claimed until this remaining uncertainty is resolved.

The application records the relevant clock origins, delay statistics, common interval, and skipped-frame counts in every new processing report. The summarized Flight 1 measurements above are retained here so generated audit caches are not required by the project.
