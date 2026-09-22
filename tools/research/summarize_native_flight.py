"""Summarize and cross-check the CSVs produced by the two native-flight readers."""
from collections import Counter
import csv
import json
from pathlib import Path
import numpy as np

OUT = Path('analysis/native_flight')


def read(name):
    with (OUT / (name + '.csv')).open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def pct(values):
    return np.percentile(values, [0, 50, 95, 99, 100]).tolist()


result = {'percentile_order': [0, 50, 95, 99, 100]}
for name, clock, value in [('servo_angle', 'log_time_ns', 'servo_angle_value'),
                            ('tof', 'sensor_time_ns', 'range_mm'),
                            ('lidar_odometry', 'pose_time_ns', 'x_m'),
                            ('lidar_odometry_fast', 'pose_time_ns', 'x_m')]:
    rows = read(name)
    t = np.array([int(r[clock]) for r in rows], dtype=np.int64) / 1e9
    v = np.array([float(r[value]) for r in rows])
    stats = {'count': len(rows), 'time_s': [t[0], t[-1]],
             'mean_hz': (len(t) - 1) / (t[-1] - t[0]),
             'dt_s_percentiles': pct(np.diff(t)), 'value_percentiles': pct(v)}
    if clock != 'log_time_ns':
        lag = np.array([int(r['log_time_ns']) - int(r[clock]) for r in rows]) / 1e6
        stats['recording_lag_ms_percentiles'] = pct(lag)
    if name == 'tof':
        good = np.array([int(r['range_status']) == 0 and int(r['range_mm']) > 0 for r in rows])
        video = t >= 319.790390
        stats.update({'status_counts': dict(Counter(r['range_status'] for r in rows)),
                      'good_positive_count': int(good.sum()), 'normal_fraction': float(good.mean()),
                      'rgb_period_count': int(video.sum()), 'rgb_period_normal_count': int((good & video).sum()),
                      'rgb_period_normal_fraction': float(good[video].mean()),
                      'max_gap_between_normal_readings_in_video_s': float(np.diff(t[good & video]).max()),
                      'normal_range_mm_percentiles': pct(v[good]),
                      'normal_sigma_mm_percentiles': pct([int(r['range_sigma_mm']) for r, g in zip(rows, good) if g])})
    result[name] = stats

tof, stn_tof = read('tof'), read('stn_tof')
fields = [('range_mm', 'range_mm'), ('range_sigma_mm', 'sigma_mm'), ('range_status', 'range_status')]
assert len(tof) == len(stn_tof)
assert all(int(a['sensor_time_ns']) == int(b['sensor_time_us']) * 1000 and
           all(a[k] == b[j] for k, j in fields) for a, b in zip(tof, stn_tof))
result['tof_crosscheck'] = {'all_mcap_and_starnet_samples_identical': True, 'count': len(tof),
                          'starnet_qualified_status_counts': dict(Counter(r['qualified_status'] for r in stn_tof))}

servo, pitch = read('servo_angle'), read('stn_camera_pitch')
at = np.array([int(r['log_time_ns']) for r in servo]) / 1e9
av = np.array([float(r['servo_angle_value']) for r in servo])
bt = np.array([int(r['log_time_us']) for r in pitch]) / 1e6
bv = np.array([float(r['camPitch']) for r in pitch])
matches = 0
for t, v in zip(at, av):
    lo, hi = np.searchsorted(bt, [t - .1, t + .005])
    matches += int(np.any(bv[lo:hi] == -v))
states = read('stn_camera_state')
ct = np.array([int(r['log_time_us']) for r in states]) / 1e6
cv = np.array([int(r['cameraPitch_deg']) for r in states])
result['pitch_crosscheck'] = {
    'servo_samples_with_exact_negative_starnet_command_nearby': matches,
    'servo_sample_count': len(servo), 'search_window_s': [-.1, .005],
    'note': 'Value matching is not unique when values repeat; this is not a physical encoder or latency calibration.',
    'starnet_command_count': len(pitch), 'starnet_command_range': [float(bv.min()), float(bv.max())],
    'camera_state_count': len(states), 'camera_state_range_deg': [int(cv.min()), int(cv.max())],
    'camera_state_mean_hz': (len(ct) - 1) / (ct[-1] - ct[0]),
    'abs_state_minus_interpolated_command_deg_percentiles': pct(abs(cv - np.interp(ct, bt, bv))),
}
sync = read('stn_video_frame_sync')
indices = np.array([int(r['frame_index']) for r in sync])
ft = np.array([int(r['frame_time_us']) for r in sync]) / 1e6
result['video_sync'] = {'count': len(sync), 'first_index': int(indices[0]), 'last_index': int(indices[-1]),
                        'index_step_counts': {str(k): v for k, v in Counter(np.diff(indices).tolist()).items()},
                        'first_last_time_s': [float(ft[0]), float(ft[-1])],
                        'residual_vs_nominal_2997fps_ms_percentiles': pct((ft - (319.790390 + (indices - 1) * 1001 / 30000)) * 1000),
                        'offset_values_us': sorted({int(r['offset_us']) for r in read('stn_video_offset')}),
                        'encoded_mov_frame_count': 14348,
                        'note': 'Counter-to-encoded-frame alignment needs visual validation; 2 more frames exist in MOV than sync records.'}
old = json.loads(Path('analysis/flight_inventory.json').read_text())['flights'][0]['trajectory']
odom = read('lidar_odometry')
result['previous_export_crosscheck'] = {
    'trajectory_count_matches': len(odom) == old['rows'],
    'first_pose_time_s': int(odom[0]['pose_time_ns']) / 1e9,
    'first_xyz_m': [float(odom[0][k]) for k in ['x_m', 'y_m', 'z_m']],
    'previous_export_first_row': old['first_row'],
    'note': 'Original export CSV was replaced; only saved audit metadata remains for comparison.'}
(OUT / 'telemetry_statistics.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({k: result[k] for k in ['tof', 'tof_crosscheck', 'pitch_crosscheck', 'video_sync']}, indent=2))
