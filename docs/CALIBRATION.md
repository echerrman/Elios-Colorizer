# RGB camera calibration

Accurate colorization requires a measured profile for the Elios RGB camera and
recording mode. Navigation-camera YAML files do not describe the 4K recording
camera and should not be scaled or substituted.

The application requires a separate JSON profile and blocks normal processing
when the selected profile is missing, incompatible with the video dimensions,
or marked `validated: false`. Changing that flag does not calibrate a camera.

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

Copy `camera_profiles/example_profile.json` and replace every placeholder with
measured values. Coordinates are metres, angles are degrees, and quaternions use
`xyzw` ordering. The optical frame is right, down, forward. Body poses transform
from body coordinates into the existing LAS coordinate frame.

Supported distortion models are `pinhole`, `opencv`, and `fisheye`. For a
camera-head angle `reported_pitch`, the profile applies:

```text
tilt_sign × (reported_pitch - tilt_zero_degrees)
```

around `tilt_axis_body` and `tilt_pivot_body_m` before applying the recorded
body pose.

Name the profile `rgb_camera_profile.json` or `colorizer_calibration.json` inside
a flight folder for automatic discovery, or select a reusable profile from any
location with the desktop application.
