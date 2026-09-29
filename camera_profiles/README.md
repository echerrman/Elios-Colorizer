# Camera profiles

Elios Colorizer requires an RGB camera profile for the recording mode used by
the selected flights. A profile contains lens intrinsics, distortion, the
camera-to-body transform, and the camera-head tilt convention.

`elios_3_builtin_rgb_default_profile.json` is the tested, reliable baseline for
the standard Elios 3 built-in RGB camera payload in its 3840 × 2160 recording
mode. This is the recommended profile for normal Elios 3 colorization. Use a
different profile only when the camera payload, recording mode, mount geometry,
or independently measured calibration differs from this standard setup.

`camera_profile_template.json` documents the required schema for a custom
profile. It contains placeholders, is intentionally marked `validated: false`,
and cannot be used for normal colorization until its values have been replaced
and independently validated.

Select the desired JSON file with the **Camera profile** browser. The selection
is shared by every flight in a workflow and is the only file path remembered
between sessions.

See [the calibration guide](../docs/CALIBRATION.md) before creating or marking a
profile as validated.
