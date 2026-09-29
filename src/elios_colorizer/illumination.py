"""Conservative overlap-only illumination fits, local to a bounded frame batch."""
from dataclasses import dataclass
import numpy as np


def linear_rgb(rgb):
    value = np.asarray(rgb, dtype=np.float64) / 255
    return np.where(value <= .04045, value / 12.92, ((value + .055) / 1.055) ** 2.4)


LINEAR = linear_rgb(np.arange(256))


def encode_rgb(value):
    value = np.clip(value, 0, 1)
    return np.rint(255 * np.where(value <= .0031308, value * 12.92,
                                 1.055 * value ** (1 / 2.4) - .055)).astype(np.uint8)



_ENCODE = encode_rgb(np.linspace(0., 1., 65536))


def apply_gain(rgb, gains):
    indices = np.rint(LINEAR[rgb] * gains[:, None] * 65535).astype(np.int32)
    return _ENCODE[np.clip(indices, 0, 65535)]

def radius_squared(uv, calibration):
    scale = max(calibration.cx, calibration.image_width - calibration.cx) ** 2
    scale += max(calibration.cy, calibration.image_height - calibration.cy) ** 2
    return np.clip(np.sum((uv - [calibration.cx, calibration.cy]) ** 2, axis=1) / scale, 0, 1)


def raw_usable(rgb, minimum=3., maximum=252., luminance=None):
    if luminance is None:
        luminance = rgb @ np.array([.2126, .7152, .0722])
    return (luminance >= max(3., minimum)) & (luminance <= min(252., maximum)) & (rgb.max(axis=1) < 253)


@dataclass(frozen=True)
class IlluminationModel:
    radial: float
    exposures: np.ndarray
    confidence: float
    radius_limit: float

    def gains(self, radii, frame):
        return np.clip(np.exp(self.radial * np.minimum(radii, self.radius_limit)
                              + self.exposures[frame]), .75, 1.35)

    def adjustment(self, rgb, radii, frame):
        gains = self.gains(radii, frame)
        # Reduce the scalar uniformly instead of clipping individual channels.
        gains = np.minimum(gains, 1. / np.maximum(LINEAR[rgb.max(axis=1)], 1e-9))
        penalty = np.minimum(gains, 1. / gains) ** 2 * (.85 + .15 * self.confidence)
        return gains, penalty

    def correct(self, rgb, radii, frame):
        gains, penalty = self.adjustment(rgb, radii, frame)
        return apply_gain(rgb, gains), penalty


def fit_model(radii, luminance):
    """Fit log gain = a*r^2 + exposure from identical point IDs.

    Arrays are (frames, sampled points), with NaN for missing observations.
    Point-disjoint holdout prevents leakage. Equations are capped at 8192.
    """
    frames, points = radii.shape
    report = dict(accepted=False, overlap_sample_count=0, radial_gain_range=[1., 1.],
                  exposure_gain_range=[1., 1.], confidence=0., reason='Insufficient overlap')
    if frames < 2:
        return None, report
    rows, targets, ids = [], [], []
    pairs = sorted({(0, j) for j in range(1, frames)} | {(j - 1, j) for j in range(1, frames)})
    limit = max(1, 8192 // len(pairs))
    for first, second in pairs:
        valid = np.flatnonzero(np.isfinite(luminance[first]) & np.isfinite(luminance[second]))
        if len(valid) > limit:
            valid = valid[np.linspace(0, len(valid) - 1, limit, dtype=int)]
        design = np.zeros((len(valid), frames))
        design[:, 0] = radii[first, valid] - radii[second, valid]
        if first: design[:, first] = 1
        if second: design[:, second] = -1
        rows.append(design)
        targets.append(np.log(luminance[second, valid] / luminance[first, valid]))
        ids.append(valid)
    x, y, point_ids = np.concatenate(rows), np.concatenate(targets), np.concatenate(ids)
    report['overlap_sample_count'] = len(y)
    if len(np.unique(point_ids)) < 96:
        return None, report
    train = point_ids % 5 != 0
    test = ~train
    if test.sum() < 24 or train.sum() < 64:
        return None, report
    if np.ptp(x[:, 0]) < .2 or np.linalg.matrix_rank(x[train]) < frames or np.linalg.cond(x[train]) > 100:
        report['reason'] = 'Radial response and exposure are not independently identifiable'
        return None, report
    weights = np.ones(train.sum())
    for _ in range(4):
        root = np.sqrt(weights)
        coef = np.linalg.lstsq(x[train] * root[:, None], y[train] * root, rcond=None)[0]
        residual = x[train] @ coef - y[train]
        weights = np.minimum(1., .04 / np.maximum(np.abs(residual), 1e-9))
    exposure = np.r_[0., coef[1:]]
    exposure -= np.mean(exposure)
    before = float(np.sqrt(np.mean(y[test] ** 2)))
    after = float(np.sqrt(np.mean((x[test] @ coef - y[test]) ** 2)))
    radius_limit = float(np.nanquantile(radii, .95))
    if (not np.isfinite(coef).all() or not .015 <= coef[0] <= np.log(1.35)
            or np.max(np.abs(exposure)) > np.log(1.10)
            or after > .06 or after > before * .75):
        report['reason'] = 'Fit failed held-out improvement, consistency, or conservative gain bounds'
        return None, report
    confidence = float(np.clip(1 - after / max(before, 1e-9), 0, 1))
    model = IlluminationModel(float(coef[0]), exposure, confidence, radius_limit)
    report.update(accepted=True, radial_gain_range=[1., float(np.exp(coef[0] * radius_limit))],
                  exposure_gain_range=[float(np.exp(exposure.min())), float(np.exp(exposure.max()))],
                  confidence=confidence, reason='Accepted on held-out geometry',
                  validation_rms_before=before, validation_rms_after=after)
    return model, report
