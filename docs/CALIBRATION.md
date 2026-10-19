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
The nearest synchronized video frames are used. The workbench streams the cloud
once and retains every point inside each selected camera frustum plus a generous
buffer. Geometry outside those calibration areas is not loaded, and the source
cloud is never modified. During motion it draws a representative GPU preview;
full retained detail returns automatically when interaction stops.

For a single flight or separate-output workflow, the workbench uses that
flight's native cloud. For a merged workflow with a supplied merged LAS/LAZ, it
uses the merged cloud and moves each selected flight's recorded camera pose
through that flight's source-to-merged transformation. The geometry source is
shown above the viewport so a cloud-alignment problem is not mistaken for a
camera-calibration problem.

The right panel provides two complementary workflows. **Point Matching** fits a
camera from user-confirmed 2D video pixels and 3D cloud points. **Manual
Adjustments** retains every direct control for inspection and refinement. Both
operate on the same temporary derived profile.

For point matching, choose **Add point match**, click a rigid feature in the
unobstructed video, then click that same physical feature in the shaded cloud.
The viewport draws the video target as a circle and the current projection of
the chosen 3D point as a cross. Prefer corners, rail ends, pipe intersections,
and depth breaks; do not match paint, glare, shadows, or texture that has no
distinct 3D location. Add at least eight enabled matches across two views. For
distortion estimation, 20 or more matches distributed across the center, edges,
corners, depths, and camera poses are strongly recommended.

**Calculate best fit** runs outside the UI thread and proposes a calibration
without changing the working profile. Automatic mode compares pinhole,
radial/tangential, and fisheye projection families while penalizing unnecessary
parameters. Review the RMS, median, per-view, and per-match errors; unusually
large residuals are highlighted. Hold **Previous** to compare the starting
projection, then explicitly accept or discard the proposal. An accepted fit can
still be refined under Manual Adjustments.

The solver first adjusts the physical relationship between camera and recorded
trajectory: mount roll/pitch/yaw, depth-supported XYZ position, and camera-head
zero when the references contain enough tilt diversity. Focal, principal-point,
and distortion changes carry stronger priors and are introduced afterward.
Automatic mode uses pinhole for a small set; radial/tangential requires at least
18 broadly distributed matches and fisheye at least 24. Every distorted result
must remain monotonic across the buffered frame, so a polynomial that folds
viewing rays into a pinwheel-like projection is rejected.

The controls are intentionally bounded:

- Video-to-telemetry time adjusts the pose and camera-head sample associated
  with each video frame.
- Focal X and Focal Y independently adjust horizontal and vertical image
  scaling; principal X/Y apply constant pixel offsets.
- The Projection selector starts from pinhole, radial/tangential, fisheye, or
  automatic/current behavior. Radial/tangential uses K1/K2/P1/P2; fisheye uses
  its distinct K1–K4 angular coefficients.
- Mount roll rotates the image about camera-forward, pitch about camera-right,
  and yaw about camera-down (the vertical optical axis).
- Translation X/Y/Z adjust the camera center in the body frame.
- Tilt zero adjusts the reported camera-head reference angle.

The default Composite view renders GPU depth-tested point splats with
screen-space eye-dome lighting and places the synchronized video over them. Change Video opacity
to compare the same physical railing, pipe, door frame, platform, or wall
corner in both layers. The wheel zooms both layers together, dragging
pans them, and double-click resets the view. Point diameter, amber/elevation/
neutral color, and depth-shading strength and radius are adjustable. Point size
is a true screen-space diameter; it no longer expands into the heavy outlined
discs produced by the older CPU preview.

Press and hold **Hold: video only** for an unobstructed video frame, then
release it to return immediately to the opacity selected by the slider. This
provides a quick before/after alignment check without changing the slider.

The former green/orange image-edge display remains available as **Edge
diagnostic**. Its score is a diagnostic, not proof of calibration; image edges
can come from glare, paint, texture, or shadows. Visually inspect every
reference view.

Click a control arrow for its fine step. Holding an arrow accelerates repeated
steps so larger corrections can be reached without sacrificing fine adjustment.
The reset icon beside a value resets only that setting.

The workbench exposes the calibration model's full diagnostic ranges: focal
scales 0.80–1.20, principal offsets ±500 px, mount and tilt angles ±15°,
camera translation ±0.30 m, and timing ±2 seconds. Large values are useful for
diagnosis but should only be saved when they remain correct in every view.

For a pinhole base profile, selecting radial/tangential and entering nonzero
K1/K2/P1/P2 values derives an OpenCV profile while the untouched fallback
remains pinhole. Fisheye exposes K1–K4 with model-correct semantics; its values
are intentionally not reused as radial/tangential coefficients. Begin with the
simplest model and only retain extra distortion terms when multi-view residuals
and visual inspection support them.

**Save profile and use** always writes a separate schema-v2 JSON. The selected
base file is never overwritten. The derived file contains a complete schema-v1
fallback snapshot, the timing correction, the applied adjustment values, the
number of reviewed frames, and the last edge-agreement score. **Use original
fallback** restores that embedded baseline for another refinement pass.
When point matches exist, a neighboring `.matches.json` session file is also
written. Reopening that derived profile with the same reference labels restores
the matches for further review; a missing or stale optional session never
prevents the profile from loading.

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
