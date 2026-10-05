# User guide

## Prepare each flight

For every flight, keep the native Inspector flight folder intact and export its
matching LAS or LAZ point cloud from Inspector. All flights in a merged workflow
must use the same coordinate system and should already be closely aligned in
Inspector.

You also need one measured RGB camera profile compatible with the recorded video
mode. The same profile is shared by every selected flight.

## Choose a workflow

### Single flight

Leave one flight row, select its native folder and point cloud, choose the camera
profile, and save to a new `.las` file. This preserves the original single-flight
workflow.

### Multiple separate outputs

Add a row for each flight and choose **Separate colorized LAS files**. Select an
output directory. Every flight is processed independently and retains its own
geometry.

### Align and merge

Add two or more flights and choose **Align and merge into one cloud**. Select a
new `.las` output. The first cloud is the coordinate reference. A bounded rigid
correction is accepted only when held-out overlap improves within conservative
movement limits; otherwise the original Inspector coordinates are retained.

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
calibration. Resolve every required checklist item before processing.

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
