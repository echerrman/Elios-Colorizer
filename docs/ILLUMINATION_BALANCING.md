# Illumination Balancing (v1.1.0 local development)

**Correct uneven illumination** is opt-in and is not persisted between sessions.
It uses repeated observations of the same source points, not histogram matching.
Insufficient or inconsistent evidence leaves RGB unchanged.

## Bounded implementation

The existing indexing pass retains at most 8,192 evenly spaced source point IDs
and XYZ positions (256 KiB). After each batch's ordinary full-geometry
visibility buffers are complete, those points are projected through the same
camera, border, occlusion, and distance checks. No extra full-cloud scan, video
decode, or retained full-resolution frame is introduced.

Each decoded batch gets one smooth exp(a*r²) radial correction and matched
per-frame exposure offsets. Separate workflows naturally fit independently.
Merged batches may share a response across their included flights; there is no
global cross-flight model or persistent frame history. Batches without enough
within-batch overlap are skipped, even if useful overlap exists elsewhere.
This conservative limitation avoids replaying frames or rescanning the cloud.

The fit uses at most 8,192 equations from adjacent and anchor frame pairs.
Training and validation contain different point IDs. Acceptance requires at
least 96 distinct matched points, radial variation, a full-rank well-conditioned
design, robust residual consistency, and at least 25% held-out RMS improvement.
Radial brightening is limited to 1.35 at the image corner, exposure offsets to
approximately ±10%, and total scalar gain to 0.75–1.35. Beyond the observed 95th
percentile radius, the gain stops increasing. The sRGB transfer function is an
assumption for decoded RGB; this is not a calibrated radiometric recovery.

Raw nearly-black pixels and pixels with a saturated channel are rejected before
correction. Gains operate on linear RGB and are reduced uniformly to avoid
channel clipping. Gain strength and model uncertainty reduce the existing
geometric observation score. Only potential winners need gain evaluation;
only winners need corrected RGB. A 64 KiB encoding table avoids per-channel
power evaluation and differs from direct conversion by at most one 8-bit code.

## Diagnostics

The result.illumination_balancing report records requested/applied flags,
accepted/skipped batch counts, matched equation count, accepted radial and
exposure gain ranges, minimum accepted confidence, fallback reason counts,
corrected observations, and raw exposure rejections. Corrected observations count
winning updates whose encoded RGB changed, not distinct final points. Rejections
count visible sampled observations; whole-frame rejections remain in the
existing frame counters. Statistics are aggregated, so report storage does not
grow with frame count. Processing details report acceptance or why a batch
was skipped.

## Local verification and performance

Run .venv/Scripts/python.exe -m pytest with a project-local temporary directory.
The optional scripts/benchmark_illumination.py uses a million synthetic points,
three overlapping views, four workers, and temporary files under .cache.
Set PYTHONPATH=src before running it.

On the development machine, three paired runs measured median 1.122 seconds
disabled versus 1.252 seconds enabled (11.6% overhead). This measures synthetic
projection and LAS I/O, not real 4K video decoding or large-cloud disk contention;
flight acceptance testing remains necessary. No broader engine redesign was
required. The disabled path does not estimate or apply illumination correction.

Final merged output omits ColorConfidence and ColorDistance, including old
copies inherited from inputs. Internal confidence/distance selection remains.
Original geometry, point order at the colorization boundary, standard LAS
attributes, and compatible unrelated extra attributes are retained. Existing
merged spatial ordering and alignment behavior are unchanged.
