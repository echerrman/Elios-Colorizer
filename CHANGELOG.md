# Changelog

All notable user-facing changes to Elios Colorizer are documented here. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.4.0] - 2026-10-08

### Added

- Added optional robust multi-frame color fusion, disabled by default, which
  retains up to five strong observations per point, rejects luminance outliers,
  and selects a weighted observed-color medoid.
- Added fusion diagnostics, fallback counts, and outlier statistics to every
  processing report.
- Added CUDA fusion candidate collection with verified CPU fallback.
- Added resource-aware CPU scaling, bounded CPU/GPU prefetch, concurrent flight
  scheduling, resource plans, and per-stage performance timings.
- Added deterministic per-flight map/reduce colorization for merged best-view
  workflows, including adaptive CPU, single-GPU, and multi-GPU scheduling.
- Added live GUI feedback for hardware planning, concurrent flight jobs, and
  final cross-flight color selection.
- Added an expandable per-flight progress panel with independently tracked
  stages and percentages during parallel processing.
- Added a responsive desktop layout: compact windows use the familiar vertical
  workflow, while wide windows use a centered two-column workspace.
- Added a persistent processing footer, processing focus mode, visible setting
  chips, preflight workload/resource estimates, output filename previews, and a
  concise post-run results card.
- Added collapsible, reorderable, and duplicable flight cards; multi-folder and
  drag-and-drop flight entry; clickable readiness fixes; and per-flight output
  actions as parallel jobs complete.
- Reorganized Advanced Processing Settings into clear Frame Selection, Color
  Quality, and Time Range groups, and improved keyboard navigation, focus
  indicators, accessible names, and shortcuts.
- Added persistent hardware-resource settings with recommended adaptive mode,
  manual CPU/RAM/GPU/VRAM/concurrency budgets, hardware refresh, and high-load
  warnings.
- Added a deliberate staged startup screen while the fully laid-out main window
  remains hidden, preventing partially rendered interface flashes at launch.
- Added targeted folder and merged-LAS drag-and-drop feedback, an Import Flights
  action, compact reordering controls, and clearer processing-setting summaries.

### Changed

- Fusion mode retains views more frequently so useful overlapping color
  evidence reaches the fusion stage. The existing best-observation mode keeps
  its previous view-selection and colorization behavior unchanged.
- Refined the light and dark themes across the main window and settings dialogs,
  including higher contrast, responsive alignment, compact dialog layouts, and
  consistent controls.

## [1.3.1] - 2026-10-05

### Added

- Added named, reusable presets for all Advanced Processing Settings.
- Added optional start/end controls for processing only a selected elapsed-time
  range of each flight, including an adaptive **Through end of video** choice.
- Added time-range details to single-flight, separate-output, and merged reports.

## [1.3.0] - 2026-10-05

### Added

- Added desktop **Advanced Processing Settings** for RGB frame sampling from
  0.25 to 30 frames per second, image-edge exclusion, and blur rejection.
- Added clear step controls, per-setting reset buttons, and a global reset for
  advanced processing values.
- Added a concise user-focused summary at the top of every processing report.

## [1.2.0] - 2026-09-30

### Added

- Added bounded NVIDIA CUDA acceleration for camera projection, lens distortion,
  depth-buffer visibility, bilinear RGB sampling, illumination correction,
  confidence scoring, and winning-observation selection.
- Added automatic runtime validation and CPU fallback when CUDA is unavailable,
  unsupported by the selected calibration, or fails safely during processing.
- Added CUDA device, backend, fallback, and accelerated-point diagnostics to
  live processing details and JSON reports.

## [1.1.0] - 2026-09-29

### Added

- Added optional **Correct uneven illumination**, using visibility-checked overlap
  evidence, bounded batch-local radial fits, modest exposure offsets, and
  point-disjoint validation. Unsafe fits leave RGB unchanged.
- Added illumination diagnostics to processing details and JSON reports.
- Added the tested default Elios 3 built-in camera profile and a bare camera
  profile template for custom calibrations.

### Changed

- Final merged LAS retains Colorized and SourceFlight; confidence and
  distance remain internal. Separate outputs retain their existing attributes.
- Applied adaptive resource tuning, spatial indexing, and grouped projection
  passes to single-flight and separate-output workflows.
- Condensed live projection progress details for easier scanning.
- Added synthetic correction, fallback, workflow, and performance checks.

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

[Unreleased]: https://github.com/echerrman/Elios-Colorizer/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/echerrman/Elios-Colorizer/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/echerrman/Elios-Colorizer/compare/v0.2.1...v1.0.0
[0.2.1]: https://github.com/echerrman/Elios-Colorizer/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/echerrman/Elios-Colorizer/releases/tag/v0.2.0
