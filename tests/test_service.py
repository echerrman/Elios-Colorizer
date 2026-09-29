from elios_colorizer.selftest import create_fixture
from elios_colorizer.service import (find_cloudcompare_executable, inspect_source, inspect_sources,
                                     merged_processing_tuning, processing_tuning,
                                     run_colorization, run_workflow)
import laspy
import numpy as np
import pytest
from pathlib import Path


def test_merged_processing_tuning_uses_safe_larger_batches():
    gib = 1024**3
    large = merged_processing_tuning(145_000_000, 20 * gib, 32)
    assert large == {'worker_threads': 12, 'frame_batch_size': 24}
    constrained = merged_processing_tuning(145_000_000, 4 * gib, 8)
    assert constrained == {'worker_threads': 4, 'frame_batch_size': 4}
    modest = merged_processing_tuning(20_000_000, 10 * gib, 12)
    assert modest == {'worker_threads': 6, 'frame_batch_size': 12}
    assert processing_tuning(20_000_000, 10 * gib, 12) == modest


@pytest.mark.parametrize('balancing', [False, True])
def test_complete_service_and_source_immutability(tmp_path, monkeypatch, balancing):
    source = create_fixture(tmp_path / 'source')
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in source.iterdir()}
    monkeypatch.setenv('ELIOS_COLORIZER_CACHE', str(tmp_path / 'cache'))
    assert inspect_source(str(source))['ready']
    updates = []
    result = run_colorization(str(source), str(tmp_path / 'colored.las'),
                               progress=lambda *args: updates.append(args), illumination_balancing=balancing)
    assert result['total_points'] == 4 and result['colored_points'] == 3
    assert updates[-1][1] == 1
    original, colored = laspy.read(source / 'test.las'), laspy.read(result['output'])
    for name in original.point_format.dimension_names:
        np.testing.assert_array_equal(original[name], colored[name])
    assert list(colored.Colorized) == [1, 1, 1, 0]
    import json
    report = json.loads(Path(result['report']).read_text())
    configuration = report['processing_configuration']
    assert configuration['frame_batch_size'] == processing_tuning(4)['frame_batch_size']
    assert configuration['adaptive_view_selection']['candidate_views'] >= 1
    assert configuration['adaptive_view_selection']['retained_views'] >= 1
    assert configuration['view_grouping']['groups'] >= 1
    assert configuration['spatial_index']['point_order_preserved']
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
    discovered = discover_source(source)
    discovered.trajectory_path = None  # Exercise the native MCAP timestamp path explicitly.
    telemetry = load_telemetry(discovered, tmp_path / 'cache')
    assert telemetry.pose_times_s[0] == 1.0  # Arrival timestamp was deliberately 1.07.
    assert telemetry.timing_diagnostics['pose_sensor_to_recording_delay_quantiles_s'][1] == pytest.approx(.07)
    metadata = json.loads((source / 'flight.json').read_text())
    metadata['time_sync']['video_offset'] = 7_350_207
    (source / 'flight.json').write_text(json.dumps(metadata))
    with pytest.raises(FlightError, match='metadata video offset disagrees'):
        discovered = discover_source(source)
        discovered.trajectory_path = None
        load_telemetry(discovered, tmp_path / 'cache')


@pytest.mark.parametrize('balancing', [False, True])
@pytest.mark.parametrize('method', ['manual', 'automatic'])
def test_complete_two_flight_merged_workflow(tmp_path, monkeypatch, balancing, method):
    import json
    first = create_fixture(tmp_path / 'first')
    second = create_fixture(tmp_path / 'second')
    metadata = json.loads((second / 'flight.json').read_text())
    metadata['id'] = 'synthetic-second-flight'
    (second / 'flight.json').write_text(json.dumps(metadata))
    monkeypatch.setenv('ELIOS_COLORIZER_CACHE', str(tmp_path / 'cache'))
    from elios_colorizer.merged import merge_geometry
    source_merged = merge_geometry([first / 'test.las', second / 'test.las'],
                                   [np.eye(4), np.eye(4)], tmp_path / 'source_merged.las')
    updates = []
    executable = tmp_path / 'synthetic-cloudcompare.exe'
    executable.touch()
    if method == 'automatic':
        monkeypatch.setattr('elios_colorizer.service.find_cloudcompare_executable', lambda *_: executable)
        monkeypatch.setattr('elios_colorizer.service.cloudcompare_cli_available', lambda *_: True)
        monkeypatch.setattr('elios_colorizer.merged.automatic_align',
                            lambda *a, **kw: ([np.eye(4), np.eye(4)], {'synthetic': True}))
    result = run_workflow([{'folder': str(first), 'name': 'Upper Ring'},
                           {'folder': str(second), 'name': 'Lower Ring'}],
                          str(tmp_path / 'merged.las'), mode='merge',
                          alignment_method=method, merged_source=str(source_merged),
                          progress=lambda *args: updates.append(args), illumination_balancing=balancing)
    merged = laspy.read(result['output'])
    assert result['colored_points'] == 6
    assert result['total_points'] == 8
    assert list(merged.Colorized).count(0) == 2
    assert set(merged.point_format.extra_dimension_names) >= {
        'Colorized', 'SourceFlight'}
    assert not {'ColorConfidence', 'ColorDistance'} & set(merged.point_format.extra_dimension_names)
    report = json.loads(Path(result['report']).read_text())
    assert report['result']['illumination_balancing']['requested'] == balancing
    assert len(report['flights']) == 2
    assert set(merged.SourceFlight) >= {0, 1}
    assert any(stage == 'Upper Ring: Reading telemetry' for stage, _, _ in updates)
    assert any(stage == 'Lower Ring: Reading telemetry' for stage, _, _ in updates)


def test_duplicate_names_are_rejected_and_source_notes_are_collapsed(monkeypatch):
    with pytest.raises(ValueError, match='unique name'):
        run_workflow([{'folder': 'one', 'name': 'Tank'}, {'folder': 'two', 'name': ' tank '}],
                     'output', mode='separate')

    def fake_inspect(folder, *_args, **_kwargs):
        return {'ready': False, 'checklist': [
            {'label': 'Source note', 'status': 'warning', 'detail': f'{folder} note A'},
            {'label': 'Source note', 'status': 'warning', 'detail': f'{folder} note B'},
        ], 'dependencies': [], 'summary': folder, 'point_count': 1, 'estimated_views': 1}

    monkeypatch.setattr('elios_colorizer.service.inspect_source', fake_inspect)
    report = inspect_sources([{'folder': 'one', 'name': 'Upper'},
                              {'folder': 'two', 'name': 'Lower'}])
    warnings = [row for row in report['checklist'] if row['label'] == 'Warnings']
    assert len(warnings) == 1
    assert warnings[0]['detail'] == '4 source notes across 2 flights.'
    assert warnings[0]['details'][0].startswith('Upper:')


def test_cloudcompare_is_discovered_from_path(tmp_path, monkeypatch):
    executable = tmp_path / 'CloudCompare.exe'
    executable.touch()
    monkeypatch.setattr('elios_colorizer.service.shutil.which', lambda _: str(executable))
    assert find_cloudcompare_executable() == executable.resolve()


@pytest.mark.parametrize('balancing', [False, True])
def test_separate_outputs_use_flight_numbers_and_names(tmp_path, monkeypatch, balancing):
    import json
    first = create_fixture(tmp_path / 'first')
    second = create_fixture(tmp_path / 'second')
    metadata = json.loads((second / 'flight.json').read_text())
    metadata['id'] = 'separate-second-flight'
    (second / 'flight.json').write_text(json.dumps(metadata))
    monkeypatch.setenv('ELIOS_COLORIZER_CACHE', str(tmp_path / 'cache'))
    output = tmp_path / 'outputs'
    result = run_workflow([
        {'folder': str(first), 'name': 'Upper Wall'},
        {'folder': str(second), 'name': 'Bottom Cap'},
    ], str(output), mode='separate', illumination_balancing=balancing)
    assert [Path(path).name for path in result['outputs']] == [
        'Flight 01 - Upper Wall - Colorized.las',
        'Flight 02 - Bottom Cap - Colorized.las',
    ]


@pytest.mark.parametrize('balancing', [False, True])
def test_default_editable_names_do_not_duplicate_flight_number(tmp_path, monkeypatch, balancing):
    import json
    first = create_fixture(tmp_path / 'first-default')
    second = create_fixture(tmp_path / 'second-default')
    metadata = json.loads((second / 'flight.json').read_text())
    metadata['id'] = 'default-named-second-flight'
    (second / 'flight.json').write_text(json.dumps(metadata))
    monkeypatch.setenv('ELIOS_COLORIZER_CACHE', str(tmp_path / 'cache-default'))
    output = tmp_path / 'default-outputs'
    result = run_workflow([
        {'folder': str(first), 'name': 'Flight 1'},
        {'folder': str(second), 'name': 'Flight 2'},
    ], str(output), mode='separate', illumination_balancing=balancing)
    assert [Path(path).name for path in result['outputs']] == [
        'Flight 01 - Colorized.las',
        'Flight 02 - Colorized.las',
    ]
