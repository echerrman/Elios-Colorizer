# Time synchronization

Elios Colorizer does not assume that the MCAP recording and RGB video begin at
the same time. Each decoded RGB frame is mapped to its recorded StarNet
`Camera.VideoFrameSync` flight-clock timestamp. Body pose and reported camera
pitch are then interpolated on that same clock.

## Sources and clock mapping

- RGB frame timing: `Camera.VideoFrameSync`
- Video origin cross-check: flight metadata and `Camera.VideoOffset`
- Body pose: MCAP `/kalman_scan2map_node/odometry` using `header.stamp`
- Camera pitch: StarNet `Camera.CameraState.cameraPitch`

MCAP `log_time` is an arrival or recording time and is not substituted for the
pose sensor timestamp. Only the common interval containing valid video timing,
pose, and camera state is eligible for projection.

## Safeguards

The application:

- Checks metadata and StarNet video origins for agreement.
- Rejects unfamiliar clock offsets instead of guessing their sign.
- Preserves the recorded frame-counter mapping across video segments.
- Accepts only small trailing counter/video mismatches consistent with normal
  recording finalization.
- Rejects a large overrun as a possible missing or truncated video segment.
- Refuses pose extrapolation and rejects low-confidence poses or large gaps.
- Records clock origins, delay statistics, usable intervals, video segment
  counts, and skipped frames in the processing report.

These checks prevent a constant start-time assumption from silently shifting
colors. They do not replace physical calibration of exposure timing, rolling
shutter, or camera-head mechanical response.
