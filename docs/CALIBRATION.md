# RGB calibration required for accurate colorization

Flight 1 contains usable trajectory, attitude, camera-state tilt, and frame timing. It does **not** contain a verified calibration for the 3840×2160 recording camera. The three 640×400 camera YAMLs belong to the VIO navigation cameras; scaling their intrinsic matrices would give the wrong camera model.

The desktop therefore checks for a separate RGB profile and blocks normal final output if one is unavailable. This is a technical accuracy requirement, not a request for approval. Changing a JSON flag does not calibrate a camera.

## What would resolve the missing calibration

The most direct source is a Flyability RGB calibration for this drone/camera firmware and 4K recording mode. It needs:

- Intrinsic matrix (`fx`, `fy`, `cx`, `cy`) for the decoded, upright 3840×2160 image.
- Lens distortion model and coefficients, or confirmation that the saved video is already rectified.
- RGB optical-camera rotation and translation relative to the MCAP `body` frame at the reference camera-head angle.
- Tilt axis and pivot, reported-angle sign, and angle zero; any measured camera motion/timestamp offset.

If manufacturer values are unavailable, a calibration recording is the next reliable route. Record a dimensioned checkerboard or ChArUco board at many image positions, distances, and orientations for intrinsics; keep the video mode unchanged. Then identify corresponding static 3D LAS features and 2D video features across multiple poses and camera tilts to solve and validate the mount and pitch convention. Reserve separate frames for checking the result. A lens calibration alone does not solve the body-to-camera mount.

## Review an experimental result

The profiles in `camera_profiles` are retained for experimental comparisons. Generate a bounded diagnostic result, open it in CloudCompare with RGB enabled, and inspect recognizable columns, beams, floor chains, and winch features against the source video. Report whether colors are shifted up/down, left/right, mirrored, or attached to the wrong structures. Visual feedback guides refinement but is not by itself a measured calibration.

## Profile format

Name the file `rgb_camera_profile.json` or `colorizer_calibration.json` in the selected flight/export folder for automatic discovery, or browse to a reusable profile elsewhere. Coordinates are meters, angles degrees, and quaternions are **xyzw**. The optical frame is **right, down, forward**. Body poses are body-to-world in the original LAS local frame.

The profile schema is documented in `src/elios_colorizer/camera.py`. Fields are:

```text
schema_version: 1
profile_name: descriptive calibration provenance
validated: true only after independent reprojection validation
image_width, image_height
intrinsics: fx, fy, cx, cy
distortion: model (pinhole/opencv/fisheye), coefficients
camera_to_body:
  rotation_xyzw, translation_m
  tilt_axis_body, tilt_pivot_body_m
  tilt_sign (-1 or +1), tilt_zero_degrees
```

`rotation_xyzw` and `translation_m` describe camera-to-body at the reference angle. The rotation about the tilt axis is `tilt_sign × (reported_pitch − tilt_zero_degrees)`; the camera center rotates about `tilt_pivot_body_m`. The full body attitude then transforms camera coordinates into the LAS frame. Include calibration provenance and reprojection measurements alongside the profile.

The two profiles under `camera_profiles` are development estimates and do not supply missing manufacturer calibration. Do not promote either experimental candidate to a validated profile without measured reprojection evidence. Validate timing and camera response as well as spatial geometry; see [the synchronization audit](TIME_SYNCHRONIZATION.md).
