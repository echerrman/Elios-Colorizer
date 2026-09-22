"""Small deterministic integration check available inside the portable build.

Creates only synthetic data under the explicitly supplied output directory.
This validates shipping dependencies and the complete service boundary.
"""
from pathlib import Path
import binascii
import json
import os
import struct


def _record(block):
    block += struct.pack('<H', binascii.crc_hqx(block, 0xffff))
    replacements = {0xaa: 0xab, 0x55: 0xac, 0xf0: 0xad, 0xa5: 0xaf}
    escaped = bytearray()
    for byte in block:
        escaped.extend([0xa5, replacements[byte]] if byte in replacements else [byte])
    return b'\xaa' + escaped + b'\x55'


def create_fixture(directory):
    import cv2
    import laspy
    import numpy as np
    from .camera import Calibration
    from mcap_ros2.writer import Writer as RosWriter
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    profile = Calibration(160, 120, 80., 80., 80., 60., (0., 0., 0., 1.), (0., 0., 0.),
                          (0., 1., 0.), (0., 0., 0.), 1., 0., profile_name='Synthetic test camera', validated=True)
    (directory / 'rgb_camera_profile.json').write_text(json.dumps(profile.to_dict()))
    (directory / 'flight.json').write_text(json.dumps(dict(id='synthetic-integration-test',
        files={'rgb_videos': ['test.mp4']}, time_sync={'video_offset': 1_000_000})))
    rows = ['timestamp[s] pos_x[m] pos_y[m] pos_z[m] rot_x rot_y rot_z rot_w quality']
    stream = _record(b'\x00\xff\xff' + json.dumps(dict(id=14, name='Camera', version=4)).encode())
    payload = bytearray(31)
    writer = cv2.VideoWriter(str(directory / 'test.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 10, (160, 120))
    if not writer.isOpened():
        raise RuntimeError('Test video encoder unavailable')
    try:
        for index in range(21):
            micros = 1_000_000 + index * 100_000
            payload[11] = 1
            struct.pack_into('<H', payload, 13, index // 10)
            rows.append(f'{micros / 1e6} 0 0 0 0 0 0 1 1')
            envelope = b'\x02' + micros.to_bytes(5, 'little')
            stream += _record(envelope + bytes([14, 4]) + payload)
            stream += _record(envelope + bytes([14, 2]) + struct.pack('<IQ', index + 1, micros))
            image = np.full((120, 160, 3), (30, 80, 180), np.uint8)
            image[::8] = (50, 150, 220)
            writer.write(image)
    finally:
        writer.release()
    (directory / 'test.stn').write_bytes(stream)
    (directory / 'test-trajectory.csv').write_text('\n'.join(rows))
    schema_text = '''std_msgs/Header header
string child_frame_id
geometry_msgs/Pose pose
bool confidence
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
================================================================================
MSG: geometry_msgs/Pose
geometry_msgs/Point position
geometry_msgs/Quaternion orientation
================================================================================
MSG: geometry_msgs/Point
float64 x
float64 y
float64 z
================================================================================
MSG: geometry_msgs/Quaternion
float64 x
float64 y
float64 z
float64 w
'''
    with (directory / 'test.mcap').open('wb') as handle, RosWriter(handle) as mcap:
        schema = mcap.register_msgdef('colorizer_test/msg/Odometry', schema_text)
        for index in range(21):
            timestamp = 1_000_000_000 + index * 100_000_000
            message = dict(header=dict(stamp=dict(sec=timestamp // 1_000_000_000, nanosec=timestamp % 1_000_000_000),
                                       frame_id='local_lidar'), child_frame_id='body',
                           pose=dict(position=dict(x=0., y=0., z=0.), orientation=dict(x=0., y=0., z=0., w=1.)),
                           confidence=True)
            mcap.write_message('/kalman_scan2map_node/odometry', schema, message,
                               log_time=timestamp + 70_000_000, publish_time=timestamp)
    header = laspy.LasHeader(point_format=1, version='1.4')
    header.scales = [.001] * 3
    cloud = laspy.LasData(header)
    cloud.x = [-.5, 0, .5, 0]
    cloud.y = [0, 0, 0, 0]
    cloud.z = [2, 2, 2, -2]
    cloud.intensity = [11, 22, 33, 44]
    cloud.gps_time = [1, 1, 1, 1]
    cloud.write(directory / 'test.las')
    return directory


def run(directory):
    import laspy
    import numpy as np
    from .service import inspect_source, run_colorization
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = create_fixture(directory / 'synthetic-flight')
    os.environ['ELIOS_COLORIZER_CACHE'] = str(directory / 'cache')
    inspected = inspect_source(str(source))
    (directory / 'readiness.json').write_text(json.dumps(inspected, indent=2))
    if not inspected['ready']:
        raise RuntimeError(f'Bundled dependency/source check failed: {inspected}')
    result = run_colorization(str(source), str(directory / 'synthetic_colorized.las'))
    original, colored = laspy.read(source / 'test.las'), laspy.read(result['output'])
    for name in original.point_format.dimension_names:
        np.testing.assert_array_equal(original[name], colored[name])
    assert list(colored.Colorized) == [1, 1, 1, 0], colored.Colorized
    assert np.all(colored.red[:3] > colored.blue[:3])
    # Render the real Qt widget tree as a packaging/layout smoke test.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    from .ui import MainWindow
    app = QApplication.instance() or QApplication([])
    # The offscreen Qt platform has no native Windows font discovery.
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        font = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts' / name
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Segoe UI', 10))
    window = MainWindow(restore_settings=False)
    window.resize(1100, 1000)
    window.checklist.set_rows(inspected['checklist'])
    window.dependencies.set_rows(inspected['dependencies'])
    window.summary.setText(inspected['summary'])
    window.source_edit.blockSignals(True)
    window.source_edit.setText(str(source))
    window.source_edit.blockSignals(False)
    window.show()
    app.processEvents()
    window.grab().save(str(directory / 'desktop_smoke.png'))
    window.close()
    result['status'] = 'passed'
    (directory / 'selftest.json').write_text(json.dumps(result, indent=2))
    return result
