import json
import struct
import binascii
import numpy as np
import pytest
from elios_colorizer.flight import (FlightError, Telemetry, discover_source, parse_starnet, _read_trajectory)


def metadata(folder, identity='same-flight', native=False):
    folder.mkdir()
    data = dict(id=identity, files={}, time_sync={'video_offset': 1_000_000})
    path = folder / ('flight.json' if native else 'Flight 1.json')
    path.write_text(json.dumps(data if native else {'flight': data}))


def test_discovery_pairs_only_matching_flights(tmp_path):
    native, export, other = [tmp_path / x for x in ('native', 'export', 'other')]
    metadata(native, native=True)
    metadata(export)
    metadata(other, identity='unrelated')
    (native / 'flight.mcap').touch()
    (native / 'camera.stn').touch()
    (native / 'one.MOV').touch()
    (export / 'pointcloud.las').touch()
    (other / 'unrelated.las').touch()
    for selected in (native, export):
        source = discover_source(selected)
        assert source.root == native
        assert source.las_path == export / 'pointcloud.las'
        assert source.mcap_path == native / 'flight.mcap'
        assert len(source.warnings) >= 1
    with pytest.raises(FlightError, match='Multiple flight'):
        discover_source(tmp_path)


def make_telemetry(times=(0., .1, .2), confidence=(1, 1, 1)):
    return Telemetry(np.array(times), np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]]),
                     np.array([[0, 0, 0, 1], [0, 0, 0, -1], [0, 0, 0, 1]]),
                     np.array(confidence), np.array(times), np.array([0., 10., 20.]),
                     np.array([0, 1, 2]), np.array(times))


def test_interpolation_respects_gaps_confidence_and_quaternion_sign():
    t = make_telemetry()
    pose = t.interpolate(.05)
    np.testing.assert_allclose(pose.position_world_m, [.5, 0, 0])
    assert pose.camera_pitch_degrees == pytest.approx(5)
    assert abs(pose.orientation_xyzw[3]) == pytest.approx(1)
    assert t.interpolate(-.01) is None
    assert t.interpolate(.21) is None
    assert make_telemetry(confidence=(1, 0, 1)).interpolate(.05) is None
    assert make_telemetry(times=(0, .1, 2)).interpolate(.5) is None


def record(block):
    block += struct.pack('<H', binascii.crc_hqx(block, 0xffff))
    escaped = bytearray()
    for byte in block:
        if byte in (0xaa, 0x55, 0xf0, 0xa5):
            escaped.extend([0xa5, {0xaa: 0xab, 0x55: 0xac, 0xf0: 0xad, 0xa5: 0xaf}[byte]])
        else:
            escaped.append(byte)
    return b'\xaa' + escaped + b'\x55'


def test_starnet_signed_pitch_counter_and_schema(tmp_path):
    def contents(version):
        data = record(b'\x00\xff\xff' + json.dumps(dict(id=14, name='Camera', version=version)).encode())
        payload = bytearray(31)
        struct.pack_into('<b', payload, 9, -63)
        payload[30] = 1
        data += record(b'\x02' + (1_000_000).to_bytes(5, 'little') + bytes([14, 4]) + payload)
        data += record(b'\x02' + (1_000_000).to_bytes(5, 'little') + bytes([14, 2]) + struct.pack('<IQ', 1, 1_000_000))
        return data
    path = tmp_path / 'log.stn'
    path.write_bytes(contents(4))
    camera = parse_starnet(path)
    np.testing.assert_array_equal(camera.pitch_degrees, [-63])
    np.testing.assert_array_equal(camera.frame_indices, [0])
    np.testing.assert_array_equal(camera.frame_times_s, [1.])
    path.write_bytes(contents(5))
    with pytest.raises(FlightError, match='unsupported'):
        parse_starnet(path)


def test_export_trajectory_whitespace_and_quality(tmp_path):
    path = tmp_path / 'trajectory.csv'
    path.write_text('timestamp[s] pos_x[m] pos_y[m] pos_z[m] rot_x rot_y rot_z rot_w quality\n1 2 3 4 0 0 0 1 1\n')
    np.testing.assert_array_equal(_read_trajectory(path), [[1, 2, 3, 4, 0, 0, 0, 1, 1]])


def test_starnet_rejects_inconsistent_video_offset(tmp_path):
    schema = record(b'\x00\xff\xff' + json.dumps(dict(id=14, name='Camera', version=4)).encode())
    envelope = b'\x02' + (1_000_000).to_bytes(5, 'little')
    data = schema + record(envelope + bytes([14, 4]) + bytes(31))
    data += record(envelope + bytes([14, 2]) + struct.pack('<IQ', 1, 1_000_000))
    data += record(envelope + bytes([14, 1]) + struct.pack('<q', 7_000_000))
    path = tmp_path / 'bad-clock.stn'
    path.write_bytes(data)
    with pytest.raises(FlightError, match='VideoOffset disagrees'):
        parse_starnet(path)
