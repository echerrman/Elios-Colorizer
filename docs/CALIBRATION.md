# RGB camera calibration

For the standard Elios 3 built-in RGB camera payload in 3840 × 2160 recording
mode, use `camera_profiles/elios_3_builtin_rgb_default_profile.json`. It is the
tested default baseline and is recommended unless the camera payload, recording
mode, mount geometry, or independently measured calibration differs from the
standard setup. Navigation-camera YAML files do not describe the 4K recording
camera and should not be scaled or substituted.

The application requires a separate JSON profile and blocks normal processing
when the selected profile is missing, incompatible with the video dimensions,
or marked `validated: false`. Changing that flag does not calibrate a camera.

## Calibration workbench

Version 2.0 adds a non-destructive workbench for correcting systematic
RGB-to-LAS displacement with real flight data. Select one or more complete
flights, their matching LAS/exports, and the existing camera profile, then
choose **Calibration** at the top of the window. Choose either three automatic
views from one flight or enter up to three flight/timestamp pairs as `M:SS`.
The nearest synchronized video frames are used. The workbench temporarily
subsamples the cloud without modifying it. The default high-density view uses
up to 500,000 points; Maximum uses up to 1,000,000. Sampling is adaptive: most
points come from the buffered areas visible in the selected reference views,
while the rest of the cloud remains sparse for orientation.

For a single flight or separate-output workflow, the workbench uses that
flight's native cloud. For a merged workflow with a supplied merged LAS/LAZ, it
uses the merged cloud and moves each selected flight's recorded camera pose
through that flight's source-to-merged transformation. The geometry source is
shown above the viewport so a cloud-alignment problem is not mistaken for a
camera-calibration problem.

Use multiple physical edges, depths, image regions, drone poses, and camera-head
angles. A railing or wall seam can make a useful local reference, but matching
one feature alone is not independent validation.

The controls are intentionally bounded:

- Video-to-telemetry time adjusts the pose and camera-head sample associated
  with each video frame.
- Focal scale and principal X/Y adjust the decoded image intrinsics.
- Mount roll rotates the image about camera-forward, pitch about camera-right,
  and yaw about camera-down (the vertical optical axis).
- Translation X/Y/Z adjust the camera center in the body frame.
- Tilt zero adjusts the reported camera-head reference angle.

The default Composite view renders depth-tested point splats with EDL-style
depth shading and places the synchronized video over them. Change Video opacity
to compare the same physical railing, pipe, door frame, platform, or wall
corner in both layers. The wheel zooms both layers together, dragging
pans them, and double-click resets the view. Point size, temporary sample
density, elevation/neutral color, and depth-shading strength are adjustable.
The 3D Inspector view can be orbited to understand the surrounding geometry,
but pixel alignment must be judged in Composite because it remains locked to
the recorded camera. Returning to 3D Inspector resets it to that camera view.

The former green/orange image-edge display remains available as **Edge
diagnostic**. Its score is a diagnostic, not proof of calibration; image edges
can come from glare, paint, texture, or shadows. Visually inspect every
reference view.

Click a control arrow for its fine step. Holding an arrow accelerates repeated
steps so larger corrections can be reached without sacrificing fine adjustment.
The reset icon beside a value resets only that setting.

**Save profile and use** always writes a separate schema-v2 JSON. The selected
base file is never overwritten. The derived file contains a complete schema-v1
fallback snapshot, the timing correction, the applied adjustment values, the
number of reviewed frames, and the last edge-agreement score. **Use original
fallback** restores that embedded baseline for another refinement pass.

## Required measurements

A complete profile contains:

- Intrinsic parameters (`fx`, `fy`, `cx`, `cy`) for the decoded upright image.
- The lens-distortion model and coefficients, or confirmation that the video is
  already rectified.
- RGB optical-camera rotation and translation relative to the flight body frame
  at the reference camera-head angle.
- Camera-head tilt axis, pivot, sign, and angle zero.
- Provenance identifying the camera, recording mode, calibration method, and
  independent validation results.

Manufacturer calibration is preferred. Otherwise, record a dimensioned
checkerboard or ChArUco target at varied positions, distances, and orientations
without changing the recording mode. Lens calibration alone does not determine
the camera-to-body mount or camera-head convention.

## Independent validation

Reserve observations that were not used to solve the profile. Project known LAS
features into those images across multiple body poses, camera tilts, image
regions, and distances. Record reprojection error and inspect for horizontal,
vertical, mirrored, or tilt-dependent displacement.

A profile should remain unvalidated when its values are estimates, when only a
single scene was inspected, or when timing and camera-head response have not
been checked.

## Profile schema

For a special camera setup, copy `camera_profiles/camera_profile_template.json`
and replace every placeholder with measured values. Coordinates are metres,
angles are degrees, and quaternions use `xyzw` ordering. The optical frame is
right, down, forward. Body poses transform from body coordinates into the
existing LAS coordinate frame.

Supported distortion models are `pinhole`, `opencv`, and `fisheye`. For a
camera-head angle `reported_pitch`, the profile applies:

```text
tilt_sign × (reported_pitch - tilt_zero_degrees)
```

around `tilt_axis_body` and `tilt_pivot_body_m` before applying the recorded
body pose.

Schema 1 remains supported unchanged and has an implicit zero timing offset.
Schema 2 is reserved for workbench-derived profiles and adds:

```json
{
  "schema_version": 2,
  "timing": {"video_to_telemetry_offset_s": 0.025},
  "fallback_calibration": {"schema_version": 1},
  "refinement": {
    "method": "interactive_video_point_cloud_workbench",
    "adjustments": {},
    "reviewed_reference_frames": 3
  }
}
```

The complete camera fields remain at the top level in schema 2 so projection is
deterministic and processing reports contain the exact values used.

Name the profile `rgb_camera_profile.json` or `colorizer_calibration.json` inside
a flight folder for automatic discovery, or select a reusable profile from any
location with the desktop application.
