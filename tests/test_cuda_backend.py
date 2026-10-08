import json
from pathlib import Path

import pytest

from elios_colorizer import cuda_backend


@pytest.fixture(autouse=True)
def clear_cuda_probe_cache():
    cuda_backend._probe_cached.cache_clear()
    yield
    cuda_backend._probe_cached.cache_clear()


def test_explicit_cuda_disable_uses_cpu_fallback(monkeypatch):
    monkeypatch.setenv('ELIOS_COLORIZER_DISABLE_CUDA', '1')
    monkeypatch.delenv('ELIOS_COLORIZER_CUDA_PROBE', raising=False)

    checkpoint = cuda_backend.cuda_checkpoint()

    assert not checkpoint.available
    assert not checkpoint.self_test_passed
    assert checkpoint.processing_backend == 'cpu_reference'
    assert not checkpoint.cuda_acceleration_active
    assert 'disabled' in checkpoint.fallback_reason.lower()


def test_missing_checkpoint_library_uses_cpu_fallback(tmp_path, monkeypatch):
    missing = tmp_path / 'missing-cuda-checkpoint.dll'
    monkeypatch.delenv('ELIOS_COLORIZER_DISABLE_CUDA', raising=False)
    monkeypatch.setenv('ELIOS_COLORIZER_CUDA_PROBE', str(missing))

    checkpoint = cuda_backend.cuda_checkpoint()

    assert not checkpoint.available
    assert checkpoint.processing_backend == 'cpu_reference'
    assert 'not installed' in checkpoint.fallback_reason.lower()


def test_standalone_diagnostic_records_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('ELIOS_COLORIZER_DISABLE_CUDA', 'true')
    destination = cuda_backend.write_cuda_checkpoint(tmp_path / 'diagnostic')

    payload = json.loads(destination.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
    assert payload['cuda']['processing_backend'] == 'cpu_reference'
    assert not payload['cuda']['available']


def test_compiled_cuda_checkpoint_executes_when_present(monkeypatch):
    library = Path(__file__).resolve().parents[1] / 'build' / 'cuda' / 'elios_cuda_probe.dll'
    if not library.is_file():
        pytest.skip('CUDA checkpoint DLL was not built on this test host')
    monkeypatch.delenv('ELIOS_COLORIZER_DISABLE_CUDA', raising=False)
    monkeypatch.setenv('ELIOS_COLORIZER_CUDA_PROBE', str(library))

    checkpoint = cuda_backend.cuda_checkpoint()

    assert checkpoint.available
    assert checkpoint.self_test_passed
    assert checkpoint.device_count >= 1
    assert checkpoint.selected_device == 0
    assert checkpoint.device_name
    assert checkpoint.total_memory_bytes > 0
    assert checkpoint.self_test_max_error == 0
    assert checkpoint.processing_backend == 'cuda_projection'
    assert checkpoint.cuda_acceleration_active


@pytest.mark.parametrize('distortion_model,coefficients', [
    ('pinhole', ()),
    ('opencv', (0., 0., 0., 0., 0.)),
    ('fisheye', (0., 0., 0., 0.)),
])
@pytest.mark.parametrize('fusion', [False, True])
def test_cuda_projection_matches_cpu_reference(tmp_path, monkeypatch,
                                               distortion_model, coefficients, fusion):
    import laspy
    import numpy as np
    from elios_colorizer.camera import Calibration
    from elios_colorizer.colorize import (ColorizationOptions, FrameObservation,
                                          colorize_las)

    library = Path(__file__).resolve().parents[1] / 'build' / 'cuda' / 'elios_cuda_probe.dll'
    if not library.is_file():
        pytest.skip('CUDA projection DLL was not built on this test host')
    header = laspy.LasHeader(point_format=1, version='1.2')
    header.scales = [.0001, .0001, .0001]
    source = laspy.LasData(header)
    xyz = np.array([(x / 20, y / 20, 2 + (x + y) % 3 / 10)
                    for x in range(-8, 9) for y in range(-6, 7)], dtype=float)
    source.x, source.y, source.z = xyz.T
    source.write(tmp_path / 'source.las')
    camera = Calibration(100, 80, 70, 69, 50, 40, (0, 0, 0, 1),
                         (0, 0, 0), (0, 1, 0), (0, 0, 0), 1, 0,
                         distortion_model=distortion_model,
                         distortion_coefficients=coefficients, validated=True)
    yy, xx = np.indices((80, 100))
    first = np.stack(((xx * 2 + yy) % 220 + 10,
                      (xx + yy * 2) % 220 + 10,
                      (xx + yy) % 220 + 10), axis=2).astype(np.uint8)
    second = np.roll(first, 3, axis=1)
    third = np.roll(first, -3, axis=1)
    frames = [
        FrameObservation(first, (0, 0, 0), (0, 0, 0, 1), 0, 0, source_flight=1),
        FrameObservation(second, (.08, 0, 0), (0, 0, 0, 1), 0, 1, source_flight=2),
        FrameObservation(third, (-.08, 0, 0), (0, 0, 0, 1), 0, 2, source_flight=3),
    ]
    options = ColorizationOptions(chunk_size=47, frame_batch_size=2,
                                  depth_buffer_width=100, minimum_sharpness=0,
                                  occlusion_radius_pixels=1, worker_threads=2,
                                  multi_frame_fusion=fusion)

    monkeypatch.setenv('ELIOS_COLORIZER_DISABLE_CUDA', '1')
    cpu = colorize_las(tmp_path / 'source.las', tmp_path / 'cpu.las', frames,
                       camera, options=options)
    monkeypatch.delenv('ELIOS_COLORIZER_DISABLE_CUDA')
    monkeypatch.setenv('ELIOS_COLORIZER_CUDA_PROBE', str(library))
    gpu = colorize_las(tmp_path / 'source.las', tmp_path / 'gpu.las', frames,
                       camera, options=options)

    cpu_las, gpu_las = laspy.read(cpu.output_path), laspy.read(gpu.output_path)
    for name in ('X', 'Y', 'Z', 'Colorized', 'SourceFlight'):
        np.testing.assert_array_equal(cpu_las[name], gpu_las[name])
    # Float32 GPU projection can place a bilinear result one 8-bit level from
    # the float64 reference at exact half-pixel rounding boundaries.
    for name in ('red', 'green', 'blue'):
        np.testing.assert_allclose(cpu_las[name], gpu_las[name], rtol=0, atol=257)
    np.testing.assert_allclose(cpu_las.ColorConfidence, gpu_las.ColorConfidence,
                               rtol=4e-3, atol=2e-6)
    np.testing.assert_allclose(cpu_las.ColorDistance, gpu_las.ColorDistance,
                               rtol=2e-6, atol=2e-6)
    assert gpu.acceleration['cuda_acceleration_active']
    assert gpu.acceleration['processing_backend'] == ('cuda_fusion' if fusion else 'cuda_projection')
    assert gpu.acceleration['color_chunks'] > 0
    assert gpu.multi_frame_fusion['requested'] == fusion
