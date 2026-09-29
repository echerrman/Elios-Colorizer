# Output format

Elios Colorizer writes LAS files. LAZ is accepted as an input, but output is
currently uncompressed LAS.

## Single and separate outputs

The application preserves source point coordinates, scale, offsets, point
format, standard attributes, and compatible VLR metadata. It adds or replaces
16-bit RGB and adds the following extra dimensions:

| Dimension | Type | Meaning |
| --- | --- | --- |
| `Colorized` | Unsigned byte | `1` when a usable RGB observation was selected; otherwise `0` |
| `ColorConfidence` (separate outputs only) | Float | Relative quality score of the selected observation |
| `ColorDistance` (separate outputs only) | Float | Camera-to-point distance in metres for the selected observation |

Unobserved points remain present with RGB `(0, 0, 0)`, `Colorized=0`, and zero
confidence and distance.

## Merged output

Merged geometry is spatially reconciled at an automatically selected voxel size.
Each occupied voxel retains one representative point. `SourceFlight` records the
one-based input flight that supplied that representative color. A colored
observation always outranks an uncolored observation in the same voxel.

The processing report records alignment decisions, source coverage, voxel size,
and output counts. Merged output is derived geometry; retain the separate source
clouds when exact source-point identity is required.
