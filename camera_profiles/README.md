# Camera profiles

Elios Colorizer requires an RGB camera profile for the recording mode used by
the selected flights. A profile contains lens intrinsics, distortion, the
camera-to-body transform, and the camera-head tilt convention.

`elios_3_builtin_rgb_default_starting_profile.json` is the general-purpose
starting profile for the standard Elios 3 built-in RGB camera payload in its
3840 × 2160 recording mode. It is a practical default template for users who
do not yet have a calibration derived from their own drone and data.

`elios_3_builtin_rgb_developer_tested_refined_profile.json` is the refined
profile the developer found most accurate for the tested data and drone. It is
a useful alternative starting point and may improve alignment for similar
hardware, but its accuracy is not guaranteed for another drone, recording
mode, mount, or dataset. Users should verify alignment on their own data and
use the calibration workbench when systematic displacement remains.

`camera_profile_template.json` documents the required schema for a custom
profile. It contains placeholders, is intentionally marked `validated: false`,
and cannot be used for normal colorization until its values have been replaced
and independently validated.

Select the desired JSON file with the **Camera profile** browser. The selection
is shared by every flight in a workflow and is the only file path remembered
between sessions.

The calibration workbench can derive a schema-v2 profile from a schema-v1
starting profile. It saves to a new path, embeds the complete starting profile
as a fallback, and records any video-to-telemetry timing correction.
Existing profiles are never rewritten and continue to work unchanged.

See [the calibration guide](../docs/CALIBRATION.md) before creating or marking a
profile as validated.
