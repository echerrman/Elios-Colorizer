# Multi-frame color fusion

Multi-frame fusion is the desktop application's default accuracy policy in
version 2.0. It can be disabled in **Advanced Processing Settings** when the
additional temporary disk use is undesirable.

For each visible LAS point, fusion retains up to five high-confidence RGB
observations in bounded temporary memory-mapped storage. Each slot must come
from a distinct calibrated camera-pose group: views within 15 cm and 3 degrees
of an existing pose compete for the same slot instead of contributing another
vote. A point needs at least three independent observations to be fused. The
final stage converts candidates to linear RGB, identifies repeatable
neutral-white evidence, rejects remaining luminance outliers using a median/MAD
rule, limits the influence of any single quality score, and selects the weighted color medoid. The medoid is
one of the observed RGB samples rather than an averaged synthetic color, which
reduces blur around texture and geometry boundaries.

Neutral white is protected only when at least two independent views and at
least 60% of all retained independent views agree within a 20-level luminance
range. A bright minority among consistent darker views remains an outlier,
which suppresses repeated adjacent-frame glare. Points with fewer than three
independent observations, or without a two-view color consensus, use the exact best-observation
fallback. Visibility, image-border, blur, distance, and optional
illumination-balancing checks still apply before an observation enters fusion.
Saturated RGB is retained unchanged but is excluded from illumination fitting.

Fusion mode retains camera views at smaller pose changes and at least every
0.5 seconds before the stricter per-point pose grouping is applied. The default
mode keeps the previous adaptive-view thresholds. CUDA continues to accelerate
visibility when available; pose-diverse candidate selection and robust fusion
are performed by the deterministic reference implementation. Processing reports
record whether fusion was requested and applied, fused/fallback point counts,
diverse bright-consensus points, isolated-bright rejections, other outlier
rejections, pose thresholds, and the processing backend.

Every candidate also receives an edge-confidence factor. Strong image edges
receive a small penalty, geometric depth discontinuities receive a larger one,
and coincident image/depth edges receive the strongest penalty because a small
projection error can cross from a thin object onto its background. This changes
confidence, not visibility or geometry, so a clean view can still color a real
edge or thin object.
