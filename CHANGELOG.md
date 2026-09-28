# Changelog

All notable user-facing changes to Elios Colorizer are documented here. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-09-28

### Added

- Multi-flight input with a shared RGB camera calibration.
- Independent batch colorization with separate LAS outputs.
- Manual and automatic alignment workflows for merged point clouds.
- Confidence-aware multi-pass colorization of a user-provided or automatically
  aligned merged cloud.
- Optional maximum colorization distance.
- Per-point color confidence, camera distance, and source-flight attributes.
- Adaptive view selection, spatial indexing, and grouped projection passes for
  large merged clouds.
- Editable, unique flight names and human-readable output filenames.
- Live elapsed time, progress, and dynamically updated remaining-time estimates.
- Disk-space checks and CloudCompare availability checks when required.

### Changed

- Merged outputs retain uncolored points with `Colorized=0`.
- Only the selected camera calibration persists between desktop sessions.
- Short, normal FrameSync/video tail mismatches are handled without shifting
  earlier frames.
- Exported Inspector trajectories are preferred when needed to keep flight poses
  in the LAS coordinate frame.
- Desktop controls, warnings, long paths, workflow selection, and light/dark
  theme persistence were refined for production use.

## [0.2.1] - 2026-09-22

### Fixed

- Accepted small trailing FrameSync mismatches produced by normal Inspector
  recording finalization.
- Kept long file paths within the desktop layout.

## [0.2.0] - 2026-09-22

### Added

- Initial public Windows desktop release.
- Geometry-preserving RGB projection onto an existing LAS point cloud.
- Native Inspector telemetry, camera tilt, video synchronization, and segmented
  RGB-video ingestion.
- Explicit `Colorized` output attribute and deterministic packaged self-test.

[Unreleased]: https://github.com/echerrman/Elios-Colorizer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/echerrman/Elios-Colorizer/compare/v0.2.1...v1.0.0
[0.2.1]: https://github.com/echerrman/Elios-Colorizer/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/echerrman/Elios-Colorizer/releases/tag/v0.2.0
