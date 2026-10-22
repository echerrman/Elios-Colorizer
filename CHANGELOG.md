# Changelog

All notable user-facing changes to Elios Colorizer are documented here. The
project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [3.0.0] - 2026-10-22

### Changed

- Rebuilt the desktop application around a modular tool shell so colorization,
  calibration, and future tools such as Color Analysis can evolve without
  coupling their pages or state.
- Reorganized colorization into dedicated Inputs, Validation, Processing, Run,
  and Results pages. Validation now blocks progression on errors, advanced
  controls live directly in Processing, and execution progress lives in Run.
- Added lightweight, deletable recent-run summaries that retain output paths,
  coverage, elapsed time, and settings without retaining processing data.
- Introduced a shared Elios design system with coordinated light and dark
  themes, modern fields and buttons, status badges, toggle controls, dialog
  styling, focus states, sliders, menus, progress surfaces, and responsive
  layouts.
- Added typed completed-output artifacts for safe handoff from colorization to
  future analysis tools.
- Added automatic Inspector-export folder matching, inferred single/separate/
  merged workflows, and clearer optional merged-cloud guidance.
- Consolidated all processing controls into one embedded workspace and made
  scroll-wheel navigation safe around editable controls.
- Separated shared camera-profile validation from per-flight readiness and
  recategorized expected exported-trajectory behavior as a consolidated
  information note rather than repeated warnings.
- Refined Inspector export assignment into a compact automatic-match list with
  per-flight status and manual controls only for unmatched flights.
- Moved the shared Camera Profile into its own non-collapsible Inputs card,
  grouped identical multi-flight validation errors, and restored sun/moon
  cues around the theme switch.
- Reweighted run progress so preparation occupies at most the first five
  percent, compacted the percentage badge, and made the Run status distinguish
  a valid active colorization from genuinely blocked setup.
- Split merged-output finalization into clearly reported candidate comparison,
  accuracy-fusion, and LAS-writing stages so a completed comparison is never
  mistaken for a finished or stalled run.

### Compatibility

- Preserved the existing inspection, calibration, resource planning,
  colorization, fusion, illumination balancing, checkpoint, reporting,
  progress, cancellation, and output behavior behind the new interface.
- Kept existing settings and advanced-processing presets compatible across the
  2.x to 3.0 upgrade.

## [2.2.0] - 2026-10-20

### Performance

- Moved edge-aware color selection and pose-diverse fusion candidate retention
  onto CUDA, with CPU/GPU parity coverage for pinhole, OpenCV, and fisheye
  calibrations. Raw boundary depth remains separate from the expanded
  occlusion buffer so the accuracy policy is unchanged.
- Added distortion-aware conservative frustum rejection, per-tile frame masks,
  persisted spatial-tile boundaries, and a deterministic tile/frame task graph.
- Replaced repeated short video seeks with verified sequential decode/grab and
  retained keyframe seeking for long gaps.
- Precomputed frame gradient maps once, instead of recalculating image-edge
  confidence for every projected point.
- Restored exact per-flight map/reduce for multi-frame fusion by reducing
  pose-diverse top-K reservoirs before the final robust fusion pass.
- Added resumable per-flight checkpoints, selectable scratch storage, safe RAM
  state placement, work-unit instrumentation, and work-based progress estimates.
- Tuned the recommended CPU worker count for the measured memory-bandwidth
  optimum on the target workstation while retaining explicit custom overrides.
- Added a packaged native C++ CPU projection kernel for CUDA-unavailable runs,
  with the existing NumPy/OpenCV implementation retained as a safe fallback.
- Renamed and documented the bundled Elios 3 profiles as a general default
  starting profile and an optional developer-tested refined profile, with clear
  guidance that users must verify calibration accuracy on their own hardware.

## [2.1.0] - 2026-10-20

### Changed

- Added guided 2D-video-to-3D-cloud point matching with zoom-aware cloud
  picking, visible target/projection residuals, match inclusion controls, and
  automatic restoration of saved match sessions. Cloud picking supports
  zoomed-out buffered overscan when a miscalibrated projection places the
  matching geometry beyond the recorded video boundary.
- Added background robust camera fitting with pinhole, OpenCV
  radial/tangential, and OpenCV fisheye candidates, complexity-aware automatic
  model selection, per-match/per-view reprojection errors, outlier highlighting,
  and an explicit preview/accept/discard workflow.
- Automatic fitting now solves physical mount rotation, eligible camera-head
  zero, and depth-supported camera translation before permitting strongly
  regularized optical changes. Distortion models require substantially more
  edge coverage, and non-monotonic projections that fold or reverse viewing
  rays are rejected instead of being previewed.
- Added a shared projection-model selector and model-correct manual controls:
  K1/K2/P1/P2 for radial/tangential projection and K1–K4 for fisheye. Accepted
  automatic fits remain available for subsequent manual refinement while the
  original embedded fallback stays unchanged.
- Calibration now retains every point inside each selected reference frame's
  buffered camera frustum instead of sharing a capped sample with unrelated
  global context.
- Replaced the CPU bitmap cloud preview with a GPU point renderer using true
  depth testing, progressive interaction detail, precise point diameters, and
  screen-space eye-dome lighting.
- Simplified calibration views to Composite and Edge Diagnostic. Removed the
  unrelated 3D Inspector and manual density presets; full retained detail is
  restored automatically as soon as interaction stops.
- Added amber, elevation, and neutral cloud coloring plus adjustable EDL
  strength and radius for clearer railings, pipes, edges, and depth breaks.
- Added a press-and-hold video-only comparison button that restores the chosen
  composite opacity immediately when released.
- Pressing Enter after typing a calibration value now commits only that value
  instead of also activating Previous and returning to an earlier view.
- The loading overlay now remains visible until the OpenGL calibration window
  is exposed, eliminating the blank transition; the workbench also explicitly
  retains the Elios Colorizer application icon.
- Split the linked focal adjustment into independent Focal X and Focal Y
  controls and exposed the calibration model's full adjustment ranges,
  including ±15° camera angles.
- Added live K1/K2 radial and P1/P2 tangential distortion controls. Nonzero
  corrections safely derive an OpenCV profile from a pinhole baseline while
  preserving the unchanged pinhole fallback.

## [2.0.0] - 2026-10-19

### Added

- Added a non-destructive calibration workbench that uses three automatic views
  or user-entered flight/timestamp references and provides live,
  bounded timing, focal, principal-point, camera-mount, translation, and
  camera-head zero adjustments.
- Added an interactive calibration point-cloud viewport with depth-tested point
  splats, EDL-style depth shading, video opacity, point-size and density
  controls, elevation/neutral coloring, synchronized zoom and pan, and an
  orbitable 3D Inspector view.
- Added adaptive deterministic temporary cloud subsampling with 75k, 200k,
  500k, and one-million-point viewing levels. Selected reference frustums and a
  surrounding buffer receive most of the density while distant context remains
  sparse, without changing the source or final colorization density.
- Merged workflows now calibrate against the supplied merged cloud by moving
  each flight's camera pose through its source-to-merged transform; other
  workflows use the flight's native geometry.
- Retained the green/orange/magenta image-edge visualization as an optional
  Edge diagnostic rather than the primary calibration view.
- Calibration control arrows now accelerate when held while retaining their
  precise single-click step.
- Added a reset icon beside every calibration setting and corrected the optical
  axis mapping so Roll, Pitch, and Yaw rotate about camera-forward,
  camera-right, and camera-down respectively.
- Calibration reference preparation now runs in a background thread behind a
  faded, input-blocking progress overlay so the application remains responsive.
- Added schema-v2 refined calibration profiles with an explicit
  video-to-telemetry timing correction, refinement evidence, and a complete
  embedded schema-v1 fallback profile.
- Added repeatable neutral-white consensus diagnostics to processing reports,
  including preserved bright points and rejected isolated bright observations.
- Added per-point pose-diverse candidate groups (15 cm / 3 degrees), a 60%
  independent-view white majority, and image/depth-boundary confidence so
  adjacent video frames cannot independently validate glare or edge bleed.

### Changed

- Bright pixels are no longer rejected solely for exceeding luminance 252.
  Pose-diverse majority neutral-white observations are preserved, while a
  bright minority remains an outlier during accuracy fusion.
- Illumination balancing now excludes saturated observations from fitting and
  gain correction without removing their original RGB from color selection.
- Accuracy fusion and true-white preservation are enabled by default in the
  desktop workflow; users can still disable fusion in Advanced Processing.
- Both CPU and CUDA color selection use the same bright-surface behavior.

### Compatibility

- Existing schema-v1 camera profiles continue to load without modification and
  remain the fallback source for every workbench-derived profile.

## [1.6.0] - 2026-10-15

### Changed

- Compacted collapsed flight rows into a single control-and-summary line and
  added a control to collapse the complete Flights section.
- Elided long selected paths in the middle while retaining their complete value
  and tooltip.
- Hid successful readiness checks by default behind an expandable completed
  checks control so warnings and missing requirements remain prominent.
- Made **One merged cloud** the default multi-flight workflow and clarified that
  it uses an already aligned, user-supplied merged LAS/LAZ.
- Restored detailed per-flight projection stages, including visibility and RGB
  assignment, to the live processing log during parallel merged processing.

### Removed

- Removed the redundant **Import Flight Folders** button; Asset import,
  individual flight entry, and drag-and-drop remain available.
- Removed automatic CloudCompare alignment and its executable selection,
  instructions, and processing path. Multi-flight processing now offers only
  separate outputs or colorization of a supplied merged cloud.
- Removed the proposed-output filename line; existing-output conflicts remain
  visible and continue to block processing.

## [1.5.1] - 2026-10-15

### Fixed

- Conservatively handles short, fully recovered StarNet `VideoFrameSync`
  timestamp glitches by excluding only the affected frame timestamps without
  renumbering or shifting video frames. Persistent clock changes, counter
  resets, and unbounded timestamp reversals continue to fail closed.

## [1.5.0] - 2026-10-13

### Added

- Added **Import Inspector Asset**, which discovers an asset's inspections and
  flights from Inspector 5 metadata and presents them as a named, selectable
  hierarchy instead of exposing UUID folder names.
- Added flight date, duration, and available-data hints to the Inspector Asset
  browser, plus inspection-level and whole-asset selection controls.
- Added an optional second Asset-import step for browsing or dropping each
  selected flight's exported data folder before adding flights to the main
  window; completed flight cards start collapsed while incomplete cards remain
  open for input.
- Added a reusable, ID-based Inspector discovery module that scans inspection
  and flight metadata once and is structured for a future workspace browser.
- Added tolerant handling and visible discovery notes for malformed, stale, or
  missing Inspector metadata and folders.

### Changed

- Renamed the existing manual action to **Import Flight Folders** and retained
  manual browsing, drag-and-drop, and individual flight entry unchanged.
- Inspector Asset selections feed the same established flight validation and
  colorization pipeline as manually selected flight folders.

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

[Unreleased]: https://github.com/echerrman/Elios-Colorizer/compare/v1.5.1...HEAD
[1.5.1]: https://github.com/echerrman/Elios-Colorizer/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/echerrman/Elios-Colorizer/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/echerrman/Elios-Colorizer/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/echerrman/Elios-Colorizer/compare/v0.2.1...v1.0.0
[0.2.1]: https://github.com/echerrman/Elios-Colorizer/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/echerrman/Elios-Colorizer/releases/tag/v0.2.0
