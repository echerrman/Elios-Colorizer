from elios_colorizer.selftest import create_fixture
from elios_colorizer.service import inspect_source, run_colorization
import laspy
import numpy as np
import pytest


def test_complete_service_and_source_immutability(tmp_path, monkeypatch):
    source = create_fixture(tmp_path / 'source')
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    monkeypatch.setenv('ELIOS_COLORIZER_CACHE', str(tmp_path / 'cache'))
    assert inspect_source(str(source))['ready']
    updates = []
    result = run_colorization(str(source), str(tmp_path / 'colored.las'),
                               progress=lambda *args: updates.append(args))
    assert result['total_points'] == 4 and result['colored_points'] == 3
    assert updates[-1][1] == 1
    original, colored = laspy.read(source / 'test.las'), laspy.read(result['output'])
    for name in original.point_format.dimension_names:
        np.testing.assert_array_equal(original[name], colored[name])
    assert list(colored.Colorized) == [1, 1, 1, 0]
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    with pytest.raises(ValueError, match='exists'):
        run_colorization(str(source), result['output'])


def test_missing_calibration_and_invalid_time_window(tmp_path):
    source = create_fixture(tmp_path / 'source')
    (source / 'rgb_camera_profile.json').unlink()
    report = inspect_source(str(source))
    assert not report['ready']
    assert any('calibration' in row['label'].lower() and row['status'] == 'missing' for row in report['checklist'])
    with pytest.raises(ValueError, match='End time'):
        run_colorization(str(source), str(tmp_path / 'result.las'), start_s=5, end_s=2)


def test_header_timestamp_not_arrival_and_wrong_clock_offset_rejected(tmp_path):
    import json
    from elios_colorizer.flight import discover_source, load_telemetry, FlightError
    source = create_fixture(tmp_path / 'source')
    telemetry = load_telemetry(discover_source(source), tmp_path / 'cache')
    assert telemetry.pose_times_s[0] == 1.0  # Arrival timestamp was deliberately 1.07.
    assert telemetry.timing_diagnostics['pose_sensor_to_recording_delay_quantiles_s'][1] == pytest.approx(.07)
    metadata = json.loads((source / 'flight.json').read_text())
    metadata['time_sync']['video_offset'] = 7_350_207
    (source / 'flight.json').write_text(json.dumps(metadata))
    with pytest.raises(FlightError, match='metadata video offset disagrees'):
        load_telemetry(discover_source(source), tmp_path / 'cache')
