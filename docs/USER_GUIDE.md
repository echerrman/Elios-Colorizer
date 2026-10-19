# User guide

## Prepare each flight

For every flight, keep the native Inspector flight folder intact and export its
matching LAS or LAZ point cloud from Inspector. All flights in a merged workflow
must use the same coordinate system and should already be closely aligned in
Inspector.

You also need one measured RGB camera profile compatible with the recorded video
mode. The same profile is shared by every selected flight.

## Find flights in Inspector 5

Use **Import Inspector Asset** to select an asset folder under the Inspector
workspace, for example:

`...\Inspector\Workspaces\Main Workspace\assets\<asset UUID>`

The application reads local Inspector metadata and shows the asset's named
inspections and flights. Expand an inspection, select one or more flights, and
choose **Import selected flights**. Dates, durations, and available-data hints
are shown when Inspector records them. UUID folder names remain internal.

After choosing flights, select **Continue** to optionally assign each flight's
exported data folder. Browse for a folder or drag it into the matching field.
You may fill every field, only some fields, or none of them. Flights completed
in this step are collapsed when added to the main window; flights without an
export stay expanded so their missing input is visible. All export folders must
still be supplied before the readiness checks can pass.

Discovery is local and read-only. A malformed unrelated inspection or flight is
skipped and reported as a discovery note instead of stopping the whole browse
operation. If an expected inspection or flight is missing, refresh Inspector's
local workspace and try again.

You can also add an individual flight row or drag flight folders into the main
window. Both approaches use the same validation and colorization pipeline.

## Calibrate before colorization

Calibration is optional when the selected camera profile already aligns well,
but it should be reviewed when fine details consistently land above, below, or
beside their physical points.

1. Select at least one complete flight, its matching LAS/export, and the
   existing camera profile you want to keep as the fallback.
2. Select **Calibration** at the top of the window. Use the three automatic
   views, or choose manual flight/timestamp pairs in `M:SS` when you know where
   the clearest reference features occur.
3. In **Composite**, adjust Video opacity until both the EDL-shaded cloud and
   video are readable. Make the same physical railing, pipe, platform edge,
   corner, or door frame occupy the same pixels in both layers. Prefer rigid
   geometry over paint, glare, shadows, or texture. Wheel zooms both layers,
   dragging pans them, and double-click resets the view. 3D Inspector is for
   orientation rather than judging pixel alignment and resets to the recorded
   camera view each time you return to it.
   Adjust Point size, Density, Cloud color, and Depth shading as needed. Move
   through every selected reference view rather than correcting only one frame.
4. Adjust the smallest relevant control:
   - **Video → telemetry** corrects displacement that changes with drone or
     camera-head motion.
   - **Principal X/Y** corrects a nearly constant horizontal or vertical image
     shift.
   - **Focal scale** corrects error that grows from the image center toward its
     edges.
   - **Roll/Pitch/Yaw** correct camera-mount angular error. Roll rotates about
     camera-forward, pitch about camera-right, and yaw about camera-down.
   - **Translate X/Y/Z** correct measured camera-center displacement.
   - **Tilt zero** corrects error associated with camera-head angle.
   A click makes the fine step shown by the control. Holding an arrow accelerates
   the repeated change for larger adjustments. The reset icon beside any value
   resets only that setting.
5. Review every reference view, choose **Save profile and use**, and save to a
   new JSON file. The starting profile is never overwritten.

The cloud name shown above the viewport identifies the geometry being used.
Single/separate workflows use the native flight cloud. A merged workflow uses
the supplied merged cloud plus the selected flight's alignment matrix. The
workbench loads an adaptive temporary sample of at most 1,000,000 points. It
concentrates points inside the three buffered reference-view areas and keeps
distant context sparse; this does not change the source LAS/LAZ or reduce the
final colorization density.

The saved schema-v2 profile contains the adjusted camera parameters, the timing
correction, review metadata, and a complete copy of the original schema-v1
profile. Reopen the workbench and select **Use original fallback** to start over
from that baseline. The readiness checklist identifies refined profiles and
their fallback explicitly.

Calibration corrects where video lands on geometry. It cannot create points for
details absent from the LAS, and a correction should not be trusted when it only
matches one wall, one distance, or one camera-head angle.

## Choose a workflow

### Single flight

Leave one flight row, select its native folder and point cloud, choose the camera
profile, and save to a new `.las` file. This preserves the original single-flight
workflow.

### Multiple separate outputs

Add a row for each flight and choose **Separate outputs**. Select an
output directory. Every flight is processed independently and retains its own
geometry.

### One merged cloud

Add two or more flights and leave the default **One merged cloud** selection.
Provide the LAS/LAZ that you have already aligned and merged, along with each
flight's source-to-merged transform when it is not identity, then select a new
`.las` output. The application validates the supplied geometry and transforms;
it does not perform automatic alignment.

Overlapping observations are reconciled spatially. The highest-confidence color
wins rather than averaging visibly different colors.

## Distance filtering

Enable **Limit colorization distance** to prevent distant background geometry
from receiving low-confidence RGB. Those points remain in the output with
`Colorized=0`, allowing another closer flight to color them during merging.

## Advanced processing settings

Open **Advanced processing settings** to adjust settings for the current session:

- **Frame sampling frequency** considers 0.25–30 RGB frames per second; the
  default is 1 frame per second. The Elios 3 records at 30 fps, so the maximum
  setting considers every available RGB frame. Higher rates significantly
  increase runtime.
- **Image edge exclusion** ignores a 0–15% border around every frame; the
  default is 2%.
- **Blur rejection** can be Off, Normal, or Strong; the default is Normal.
- **Accuracy fusion and true-white preservation** retains observations from up
  to five distinct camera poses per point. Views within 15 cm and 3 degrees
  compete for one slot, so adjacent 30-fps frames cannot cast repeated votes.
  Neutral white is protected only when at least two independent poses and a 60%
  majority agree. Image/depth-boundary confidence also reduces color bleeding
  near railings and silhouettes. At least three independent observations are
  required; otherwise the point uses the normal best-observation result.
  Accuracy fusion is On by default and uses additional temporary disk space.
- **Process only a time range** limits RGB observations to start/end times
  elapsed from each flight's first synchronized video frame. Leave **Through
  end of video** selected to adapt automatically to each flight's duration.

Each setting has its own reset button, and **Reset all defaults** restores the
standard processing behavior. Use **Save current…** to store a named preset;
selecting it later restores every advanced setting, including its time-range
choice. Saved presets remain available in future desktop sessions. The selected
values are recorded in the report.

## Readiness checklist

The run button becomes available only after all selected flights contain usable
metadata, video, pose, camera-state timing, point-cloud geometry, and compatible
calibration. Missing requirements and warnings remain visible. Successful checks
are summarized behind **Show completed checks** and can be expanded when needed.

## Reviewing output

Open the LAS in CloudCompare or another LAS reader with RGB enabled. Inspect
recognizable edges and structures from multiple viewpoints. Use the `Colorized`
attribute to distinguish observed points; filter `Colorized = 0` only when you
want a display or derivative cloud without uncolored geometry.

Keep the JSON processing report beside the LAS. It records source files,
calibration identity, timing checks, processing configuration, warnings, and
coverage statistics needed to reproduce or diagnose the result. A compact
`summary` section appears first with the output, coverage, runtime, frame counts,
and user-selected processing settings.

During multi-flight processing, expand **Flight progress** to see the current
stage and percentage for each flight while the main bar shows weighted overall
progress. On wide windows, the application automatically places flight setup and
readiness beside processing controls; narrower windows retain the vertical
workflow. No setting is required when resizing the window.

## Desktop workflow conveniences

- Drag flight folders onto the window or add flights individually. Flight cards
  can be collapsed, reordered, or duplicated, and the complete Flights section
  can be collapsed once setup is finished.
- Missing readiness items provide **Go to input** actions that expand and focus
  the related control.
- The processing section shows the active advanced settings, estimated sampled
  frames, point count, recommended free disk space, detected CPU/CUDA resources,
  and intended parallel strategy before a run.
- Existing output conflicts are identified before processing can start.
- During processing, setup sections collapse into a focused view while the Run,
  Cancel, overall progress, elapsed time, and ETA controls remain visible in the
  bottom bar. In separate-output mode, an individual completed flight can be
  opened while other flights continue.
- On completion, the results card summarizes coverage, runtime, frame usage,
  strategy, and warnings, with direct output and report actions.

Keyboard shortcuts include `Ctrl+Shift+A` to add a flight, `Ctrl+Shift+C` to
open the calibration workbench, `Ctrl+R` to refresh checks, `Ctrl+,` for Advanced
Processing Settings, and `Ctrl+Enter` to start an available run.
