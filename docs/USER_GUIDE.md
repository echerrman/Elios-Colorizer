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
coverage statistics needed to reproduce or diagnose the result.
