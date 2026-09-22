"""Independent video motion / recorded camera motion timing audit.

Image-pair relative rotation is estimated from feature matches. Its magnitude
is compared with recorded body attitude + reported camera-head angle over a
range of temporal shifts. This is a diagnostic, not an absolute sync certificate.
"""
import json
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scipy.stats import spearmanr
from elios_colorizer.flight import discover_source, load_telemetry


def main():
    out = Path('outputs/timing-audit')
    out.mkdir(parents=True, exist_ok=True)
    source = discover_source('Flight_Data')
    telemetry = load_telemetry(source, '.cache')
    path = out / 'image_rotations.npz'
    if not path.exists():
        captures = [cv2.VideoCapture(str(p)) for p in source.videos]
        counts = np.array([int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) for cap in captures])
        starts = np.r_[0, np.cumsum(counts)]
        for cap in captures:
            cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
        def frame(index):
            segment = int(np.searchsorted(starts[1:], index, side='right'))
            cap = captures[segment]
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(index - starts[segment]))
            ok, bgr = cap.read()
            if not ok:
                raise RuntimeError(f'Cannot read global frame {index}')
            # 180-degree display rotation cancels in relative-angle magnitude.
            return cv2.cvtColor(cv2.resize(bgr, (640, 360)), cv2.COLOR_BGR2GRAY)
        sift = cv2.SIFT_create(nfeatures=1200)
        matcher = cv2.BFMatcher()
        matrices = [np.array([[f, 0, 320], [0, f, 180], [0, 0, 1.]], float) for f in (240, 300, 360)]
        observations = []
        try:
            for i, elapsed in enumerate(np.arange(25., 451., 2.)):
                origin = telemetry.frame_times_s[0]
                first = np.searchsorted(telemetry.frame_times_s, origin + elapsed)
                second = first + 12
                im1, im2 = frame(telemetry.frame_indices[first]), frame(telemetry.frame_indices[second])
                kp1, d1 = sift.detectAndCompute(im1, None)
                kp2, d2 = sift.detectAndCompute(im2, None)
                if d1 is None or d2 is None:
                    continue
                matches = [a for pair in matcher.knnMatch(d1, d2, k=2) if len(pair) == 2
                           for a, b in [pair] if a.distance < .72 * b.distance]
                if len(matches) < 45:
                    continue
                p1 = np.float32([kp1[m.queryIdx].pt for m in matches])
                p2 = np.float32([kp2[m.trainIdx].pt for m in matches])
                angles, inliers = [], []
                for matrix in matrices:
                    essential, mask = cv2.findEssentialMat(p1, p2, matrix, cv2.RANSAC, .999, .8)
                    if essential is None or essential.shape != (3, 3):
                        angles.append(np.nan); inliers.append(0); continue
                    count, rotation, _, _ = cv2.recoverPose(essential, p1, p2, matrix, mask=mask)
                    angles.append(float(Rotation.from_matrix(rotation).magnitude()))
                    inliers.append(int(count))
                observations.append([telemetry.frame_times_s[first], telemetry.frame_times_s[second],
                                     len(matches), *angles, *inliers])
                if i % 20 == 0:
                    print(f'Image motion {i}/213 pairs', flush=True)
        finally:
            for cap in captures:
                cap.release()
        np.savez_compressed(path, observations=np.array(observations))
    rows = np.load(path)['observations']
    slerp = Slerp(telemetry.pose_times_s, Rotation.from_quat(telemetry.quaternions_xyzw))
    shifts = np.arange(-20., 20.001, .05)
    reports = []
    for lens in range(3):
        # Keep geometrically supported estimates with actual rotation signal.
        valid = np.isfinite(rows[:, 3 + lens]) & (rows[:, 6 + lens] >= 25) & (rows[:, 3 + lens] < np.deg2rad(35))
        observed = rows[valid, 3 + lens]
        time0, time1 = rows[valid, 0], rows[valid, 1]
        for tilt_sign in (-1, 1):
            metrics = []
            for shift in shifts:
                a, b = time0 + shift, time1 + shift
                pitch0 = np.deg2rad(np.interp(a, telemetry.pitch_times_s, telemetry.pitch_degrees)) * tilt_sign
                pitch1 = np.deg2rad(np.interp(b, telemetry.pitch_times_s, telemetry.pitch_degrees)) * tilt_sign
                ra = slerp(a) * Rotation.from_rotvec(np.c_[np.zeros(len(a)), pitch0, np.zeros(len(a))])
                rb = slerp(b) * Rotation.from_rotvec(np.c_[np.zeros(len(b)), pitch1, np.zeros(len(b))])
                expected = (ra.inv() * rb).magnitude()
                correlation = float(spearmanr(observed, expected).statistic)
                residual = float(np.median(np.abs(observed - expected)))
                metrics.append([float(shift), correlation, float(np.rad2deg(residual))])
            metrics = np.array(metrics)
            best = int(np.nanargmax(metrics[:, 1]))
            zero = int(np.argmin(np.abs(metrics[:, 0])))
            reports.append(dict(focal_length_at_640px=(240, 300, 360)[lens], tilt_sign=tilt_sign,
                                valid_pairs=int(valid.sum()), best_shift_s=float(metrics[best, 0]),
                                best_spearman=float(metrics[best, 1]), zero_spearman=float(metrics[zero, 1]),
                                zero_median_rotation_error_deg=float(metrics[zero, 2])))
            np.savetxt(out / f'shift_scan_f{lens}_sign{tilt_sign}.csv', metrics, delimiter=',',
                       header='shift_added_to_recorded_frame_time_s,spearman_rotation_correlation,median_angle_error_deg')
    (out / 'motion_timing_report.json').write_text(json.dumps(reports, indent=2))
    print(json.dumps(reports, indent=2), flush=True)


if __name__ == '__main__':
    main()
