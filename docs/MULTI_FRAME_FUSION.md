# Multi-frame color fusion

Multi-frame fusion is an optional alternative to the default best-observation
colorization policy. It is disabled by default, so existing workflows retain
their established output unless the user explicitly enables it in **Advanced
Processing Settings**.

For each visible LAS point, fusion retains up to five high-confidence RGB
observations in bounded temporary memory-mapped storage. A point needs at least
three observations to be fused. The final stage converts candidates to linear
RGB, rejects luminance outliers using a median/MAD rule, limits the influence of
any single quality score, and selects the weighted color medoid. The medoid is
one of the observed RGB samples rather than an averaged synthetic color, which
reduces blur around texture and geometry boundaries.

Points with fewer than three observations, or without a two-view consensus,
use the exact best-observation fallback. Saturation, visibility, image-border,
blur, distance, and optional illumination-balancing checks still apply before
an observation enters fusion.

Fusion mode retains camera views at smaller pose changes and at least every
0.5 seconds. The default mode keeps the previous adaptive-view thresholds.
CUDA continues to accelerate visibility when available; robust fusion is
performed by the deterministic reference implementation. Processing reports
record whether fusion was requested and applied, fused/fallback point counts,
outlier rejections, and the processing backend.
