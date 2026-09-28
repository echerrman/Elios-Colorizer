# Camera profiles

Elios Colorizer requires a measured RGB camera profile for the recording mode
used by the selected flights. A profile contains lens intrinsics, distortion,
the camera-to-body transform, and the camera-head tilt convention.

`example_profile.json` documents the file format. Its placeholder values are
not an Elios calibration and it is intentionally marked `validated: false`.
The desktop application will not use it for normal colorization.

Keep a verified profile outside the application directory and select it with
the **Camera profile** browser. The selection is shared by every flight in a
workflow and is the only file path remembered between sessions.

See [the calibration guide](../docs/CALIBRATION.md) before creating or marking a
profile as validated.
