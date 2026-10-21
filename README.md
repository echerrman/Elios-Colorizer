<p align="center">
  <img src="assets/social-preview.png" width="100%" alt="Elios Colorizer — Local RGB Colorization for Elios 3 LAS Point Clouds">
</p>

<p align="center">
  <a href="https://github.com/echerrman/Elios-Colorizer/actions/workflows/ci.yml?query=branch%3Amain"><img alt="Tests" src="https://github.com/echerrman/Elios-Colorizer/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/echerrman/Elios-Colorizer/releases/tag/v2.2.0"><img alt="Release v2.2.0" src="https://img.shields.io/badge/release-v2.2.0-2f6f89"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-2f6f89"></a>
  <img alt="Windows 10 and 11" src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-2f6f89">
  <img alt="Local processing" src="https://img.shields.io/badge/processing-local-187548">
</p>

<p align="center">
  <a href="https://github.com/echerrman/Elios-Colorizer/releases/latest"><strong>Download for Windows</strong></a>
  ·
  <a href="docs/INSTALLATION.md">Installation</a>
  ·
  <a href="docs/USER_GUIDE.md">User guide</a>
  ·
  <a href="https://github.com/echerrman/Elios-Colorizer/issues">Report a problem</a>
</p>

![Elios Colorizer desktop application](assets/application.png)

Elios Colorizer creates a new RGB LAS from an existing Inspector point cloud,
native flight telemetry, and recorded 4K video. It preserves unobserved geometry,
marks every point with an explicit colorization status, and performs all work on
the local computer. Flight data is not uploaded to a cloud service.

> **Version 2.1:** align video and cloud geometry manually or fit a bounded
> camera projection from matched 2D video pixels and 3D cloud points.

## Install

1. Download the Windows x64 portable ZIP from the
   [latest release](https://github.com/echerrman/Elios-Colorizer/releases/latest).
2. Extract the complete ZIP.
3. Run `EliosColorizer.exe` inside the extracted `EliosColorizer` folder.

The portable build includes its runtime and libraries. Users do not need Python,
ROS, a Flyability cloud account, or a separate FFmpeg installation. Keep the
bundled `_internal` directory beside the executable. See the
[installation guide](docs/INSTALLATION.md) for updating and source builds.

## Required inputs

| Input | Purpose |
| --- | --- |
| Native Inspector flight folder | RGB segments, pose telemetry, camera state, and recorded frame timing |
| Matching Inspector LAS or LAZ | Reliable source geometry and original point attributes |
| Measured RGB camera profile | Lens, camera mount, and camera-head tilt calibration |

Navigation-camera calibration files do not describe the 4K recording camera. A
profile marked as validated must be backed by measured calibration and independent
reprojection checks. Read the [camera calibration guide](docs/CALIBRATION.md).

The desktop calibration workbench starts from that selected profile and uses
either automatic reference views or user-selected flight timestamps. Its
GPU-rendered, EDL-shaded point-cloud viewport retains full detail inside each
buffered reference view and provides video opacity, precise point size,
synchronized zoom and pan, and automatic progressive rendering. Users can
either refine values manually or match video pixels to cloud points and fit a
bounded pinhole, radial/tangential, or fisheye projection with measurable
reprojection error. It saves corrections as a separate schema-v2 file, never
overwrites the starting profile, and embeds that complete schema-v1 profile as
its fallback.

## Workflows

Select **Import Inspector Asset** to browse a local Inspector 5 asset as a
human-readable Asset → Inspection → Flight hierarchy. Select any combination of
flights and add them to the existing workflow without finding their UUID folders
manually. Individual flight entry and drag-and-drop remain available.

| Workflow | Result |
| --- | --- |
| Single flight | One geometry-preserving colorized LAS |
| Multiple flights, separate | One independent colorized LAS per flight |
| Multiple flights, merged | Confidence-aware colorization of one aligned merged LAS |

Merged output requires an already aligned, user-supplied merged LAS/LAZ plus any
needed per-flight source-to-merged transforms. The application validates those
inputs but does not modify their alignment.

An optional distance limit prevents distant background points from receiving
low-confidence RGB. Uncolored points are retained in every workflow so the user
can inspect or filter them later.

[Read the workflow guide →](docs/USER_GUIDE.md)

## How colorization works

The application maps decoded frames to recorded StarNet `VideoFrameSync`
timestamps instead of assuming that video and MCAP files start together. It
interpolates body pose and camera pitch on the flight clock, projects visible LAS
points into sampled video frames, rejects poor observations, and keeps the
highest-confidence usable color for each point.

Visibility uses a point-cloud depth buffer. Observation quality considers image
position, distance, sharpness, and exposure. On a compatible NVIDIA GPU, CUDA
performs projection, depth-buffer visibility, RGB sampling, illumination
correction, and observation scoring in bounded batches. The application falls
back automatically to the CPU implementation and independently chooses between
an in-memory coordinate cache and a bounded-memory streaming path.

Default [multi-frame fusion](docs/MULTI_FRAME_FUSION.md) keeps CUDA-accelerated
visibility when available, then performs deterministic robust fusion on the CPU.
Only a majority of pose-diverse neutral-white observations is treated as
persistent surface color; correlated adjacent frames share one vote and a
bright minority remains a glare candidate. Image/depth-edge confidence reduces
bleeding near thin geometry. Saturated samples are not used to fit illumination
gains, but their original RGB remains eligible for colorization.
Ordinary multi-flight merged processing can colorize flights concurrently against
the same immutable merged point indices, then deterministically reduce their
candidates into one final LAS. CPU-only, single-GPU, and multi-GPU machines use
the same selection rules; only the execution plan changes.

[Read the synchronization design →](docs/TIME_SYNCHRONIZATION.md)
[Read the parallel processing design →](docs/PARALLEL_PROCESSING.md)

## Output

RGB uses the LAS 16-bit color range. Single and separate outputs retain source
coordinates and compatible attributes. The application adds:

| Attribute | Meaning |
| --- | --- |
| `Colorized` | `1` if a usable RGB observation was selected; otherwise `0` |
| `ColorConfidence` (separate outputs only) | Relative score of the selected observation |
| `ColorDistance` (separate outputs only) | Camera-to-point distance in metres |
| `SourceFlight` | One-based source-flight index in merged output |

Each output includes a JSON processing report with calibration identity, timing
checks, warnings, processing configuration, and coverage statistics.

[Read the output specification →](docs/OUTPUT_FORMAT.md)

## Privacy and safety

- Flight data is processed locally and is never modified in place.
- Existing output files are not overwritten.
- Missing or inconsistent timing, pose, video, or calibration data stops the run
  instead of being silently guessed.
- Diagnostic reports can contain local paths and flight metadata. Sanitize them
  before sharing publicly.
- Do not upload customer footage, facility names, locations, or proprietary
  Inspector data to GitHub issues.

Security concerns should be reported privately according to
[SECURITY.md](SECURITY.md).

## Compatibility and limitations

- Windows 10 or 11, 64-bit.
- NVIDIA CUDA acceleration supports NVIDIA GPUs with compute capability 7.5 or
  newer and a current driver; the CPU fallback remains available on other
  systems.
- Observed Inspector native layout with StarNet Camera schema version 4.
- LAS output; LAS and LAZ input.
- Point-cloud visibility is approximate around thin or sparse structures.
- Moving objects, rolling shutter, lighting changes, and calibration error can
  create seams or misplaced color.
- E57 and waveform LAS are not currently supported.

See the [changelog](CHANGELOG.md) for release history.

## Development

The complete source, tests, and reproducible Windows build scripts are public.
Use 64-bit Python 3.12. CUDA-enabled development builds also require the
NVIDIA CUDA Toolkit and the Visual Studio 2022 **Desktop development with
C++** workload; builds without them retain the CPU fallback:

```powershell
./scripts/setup.ps1
& ./.venv/Scripts/python.exe -m pytest
./scripts/build.ps1
```

The packaged application provides a deterministic synthetic integration check:

```powershell
./dist/EliosColorizer/EliosColorizer.exe --self-test ./selftest-output
./dist/EliosColorizer/EliosColorizer.exe --cuda-check ./cuda-checkpoint
```

Contributions are welcome through the process in
[CONTRIBUTING.md](CONTRIBUTING.md). Dependency licenses and notices are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License and attribution

Elios Colorizer is available under the [MIT License](LICENSE).

Elios, Elios 3, Inspector, and Flyability are trademarks of their respective
owners. This independent open-source project is not affiliated with, sponsored
by, or endorsed by Flyability.

### v2.1.0

Version 2.1.0 adds guided 2D-video-to-3D-cloud point matching and robust,
bounded fitting for pinhole, radial/tangential, and fisheye camera models. The
calibration viewport now retains full detail inside each buffered reference
view, uses GPU rendering with screen-space EDL, and provides clearer manual
projection controls, comparison tools, and fit diagnostics.

### v2.0.0

Version 2.0.0 adds a non-destructive synchronized video/LAS calibration
workbench and schema-v2 derived profiles with embedded schema-v1 fallbacks. It
also replaces the saturated-white cutoff with multi-view true-white
preservation: a majority of pose-diverse neutral-white evidence is kept,
correlated glare is rejected, image/depth-edge confidence protects thin
geometry, and illumination balancing no longer removes genuine saturated RGB.

### v1.6.0

Version 1.6.0 compacts flight and readiness sections, defaults multi-flight
setup to a supplied merged cloud, removes automatic CloudCompare alignment,
and restores detailed per-flight RGB-assignment progress.

### v1.5.1

Version 1.5.1 conservatively tolerates brief, fully recovered StarNet frame
timestamp glitches. Only timestamps inside the proven disturbance are skipped;
video frame numbering is preserved, and ambiguous or persistent clock changes
still stop processing.

### v1.5.0

Version 1.5.0 adds a local Inspector 5 Asset browser that discovers named
inspections and flights without exposing UUID folders. Selected flights can be
imported across inspections, and their optional Inspector export folders can be
assigned by browsing or drag-and-drop in the same guided workflow. Malformed or
stale unrelated metadata is skipped with visible notes, while the established
manual folder import and colorization pipeline remain unchanged.

### v1.4.0

Version 1.4.0 adds optional robust multi-frame color fusion. It retains several
strong observations per point, rejects luminance outliers such as glare, and
selects an actual observed color supported by overlapping views. Fusion is Off
by default, leaving the established best-observation process unchanged.
The engine also adds resource-aware CPU scaling, CUDA fusion, bounded CPU/GPU
prefetch, and concurrent multi-flight processing for separate and merged
workflows. The refreshed responsive desktop interface includes per-flight
progress, persistent adaptive or manual hardware budgets, improved drag and
drop, refined light and dark themes, and a staged startup screen. See
[Parallel processing](docs/PARALLEL_PROCESSING.md).

### v1.3.1

Version 1.3.1 adds reusable named presets and optional start/end controls for
processing only a selected elapsed-time range of every flight.

### v1.3.0

Version 1.3.0 adds an **Advanced Processing Settings** window with adjustable
RGB sampling up to the Elios 3 recording rate of 30 fps, image-edge exclusion,
and blur rejection. Each control can be reset independently, and processing
reports now begin with a concise user-focused summary.

### v1.2.0

Version 1.2.0 adds bounded NVIDIA CUDA acceleration for the most expensive
projection, visibility, sampling, correction, and scoring work. Runtime checks
select CUDA when supported and safely retain the complete CPU processing path.

### v1.1.0

The optional **Illumination Balancing** control uses matched geometry to
reduce radial lighting differences conservatively. See
[Illumination Balancing](docs/ILLUMINATION_BALANCING.md) for validation, fallback,
and synthetic performance measurements. Version 1.1.0 also applies adaptive
resource tuning and indexing to all processing workflows.
