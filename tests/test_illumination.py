"""Synthetic illumination evidence and geometry-preserving integration tests."""
from dataclasses import replace
import laspy
import numpy as np
import pytest
from elios_colorizer.illumination import (
    IlluminationModel, fit_model, linear_rgb, encode_rgb, raw_usable)
from elios_colorizer.colorize import ColorizationOptions, FrameObservation, colorize_las
from elios_colorizer.camera import Calibration


def evidence(points=1000, radial=.24, exposures=(0., .04, -.04)):
    rng = np.random.default_rng(42)
    radii = rng.uniform(0, .9, (len(exposures), points))
    surface = rng.uniform(.08, .5, points)
    light = surface * np.exp(-radial * radii - np.array(exposures)[:, None])
    return radii, light


def test_radial_darkening_and_multiflight_exposure_reduced():
    radii, light = evidence()
    model, report = fit_model(radii, light)
    assert report['accepted']
    assert model.radial == pytest.approx(.24)
    np.testing.assert_allclose(model.exposures, [0., .04, -.04], atol=1e-8)
    corrected = np.array([light[i] * model.gains(radii[i], i) for i in range(3)])
    assert np.mean(np.std(corrected, axis=0)) < np.mean(np.std(light, axis=0)) * .1
    assert report['validation_rms_after'] < report['validation_rms_before'] * .1


def test_hue_gain_limits_and_confidence_penalty():
    model = IlluminationModel(.3, np.array([.09, -.09]), .9, 1.)
    rgb = np.tile([70, 115, 160], (100, 1)).astype(np.uint8)
    radii = np.linspace(0, 1, 100)
    corrected, penalty = model.correct(rgb, radii, 0)
    ratios = linear_rgb(corrected) / linear_rgb(rgb)
    assert np.max(np.ptp(ratios, axis=1)) < .03  # 8-bit quantization
    assert np.all(penalty <= 1) and penalty[-1] < penalty[0]
    for frame in range(2):
        assert np.all((model.gains(radii, frame) >= .75) & (model.gains(radii, frame) <= 1.35))
    # Highlight headroom preserves channel ratios rather than channel clipping.
    high = np.array([[252, 150, 70]], dtype=np.uint8)
    out, _ = model.correct(high, np.array([1.]), 0)
    assert out.max() <= 255
    ratios = linear_rgb(out) / linear_rgb(high)
    assert np.ptp(ratios) < .025


@pytest.mark.parametrize('kind', ['few', 'identical_radius', 'inconsistent', 'harmful', 'too_strong', 'exposure'])
def test_unsafe_models_fall_back(kind):
    radii, light = evidence()
    if kind == 'few': radii, light = radii[:, :50], light[:, :50]
    if kind == 'identical_radius': radii[:] = radii[0]
    if kind == 'inconsistent': light *= np.random.default_rng(3).uniform(.7, 1.4, light.shape)
    if kind == 'harmful': radii, light = evidence(radial=-.2)
    if kind == 'too_strong': radii, light = evidence(radial=.7)
    if kind == 'exposure': radii, light = evidence(exposures=(0, .4, -.4))
    model, report = fit_model(radii, light)
    assert model is None and not report['accepted']


def test_raw_clipping_and_black_rejected():
    rgb = np.array([[255, 90, 80], [2, 2, 2], [254, 254, 254], [70, 100, 140]], dtype=np.uint8)
    np.testing.assert_array_equal(raw_usable(rgb), [False, False, False, True])


def scene(path, points=1600):
    camera = Calibration(200, 200, 100, 100, 100, 100, (0, 0, 0, 1), (0, 0, 0),
                         (0, 1, 0), (0, 0, 0), 1, 0, validated=True)
    side = int(np.sqrt(points))
    xx, yy = np.meshgrid(np.linspace(-1.3, 1.3, side), np.linspace(-1.3, 1.3, side))
    cloud = laspy.LasData(laspy.LasHeader(point_format=1, version='1.2'))
    cloud.header.scales = [.001] * 3
    cloud.x, cloud.y, cloud.z = xx.ravel(), yy.ravel(), np.full(xx.size, 2.)
    cloud.intensity = np.arange(xx.size) % 65535
    cloud.add_extra_dim(laspy.ExtraBytesParams('OriginalValue', 'float32'))
    cloud.OriginalValue = np.arange(xx.size)
    cloud.write(path)
    yy, xx = np.mgrid[:200, :200]
    radius = ((xx - 100) ** 2 + (yy - 100) ** 2) / 20000
    base = linear_rgb(np.array([100, 140, 180]))
    frames = [FrameObservation(encode_rgb(base * np.exp(-.25 * radius[..., None] - exposure)),
              (shift, 0, 0), (0, 0, 0, 1), 0, float(i), source_flight=i + 1)
              for i, (shift, exposure) in enumerate([(-.45, -.025), (.45, .025), (0, 0)])]
    return camera, frames


@pytest.mark.parametrize('workers', [1, 2])
def test_engine_corrects_and_preserves_geometry(tmp_path, workers):
    source = tmp_path / 'source.las'
    camera, frames = scene(source)
    opts = ColorizationOptions(chunk_size=301, frame_batch_size=3, minimum_sharpness=0,
        depth_buffer_width=200, occlusion_radius_pixels=0, worker_threads=workers)
    off = colorize_las(source, tmp_path / 'off.las', frames, camera, options=opts)
    events = []
    on = colorize_las(source, tmp_path / 'on.las', frames, camera,
                     options=replace(opts, illumination_balancing=True, merged_output=True),
                     progress=events.append)
    assert on.illumination_balancing['applied']
    assert on.illumination_balancing['accepted_batches'] == 1
    assert not off.illumination_balancing['requested']
    raw, corrected, original = laspy.read(off.output_path), laspy.read(on.output_path), laspy.read(source)
    for name in original.point_format.dimension_names:
        np.testing.assert_array_equal(corrected[name], original[name])
    assert {'Colorized', 'SourceFlight'} <= set(corrected.point_format.extra_dimension_names)
    assert not {'ColorConfidence', 'ColorDistance'} & set(corrected.point_format.extra_dimension_names)
    assert {'ColorConfidence', 'ColorDistance'} <= set(raw.point_format.extra_dimension_names)
    assert np.std(corrected.red.astype(float)) < np.std(raw.red.astype(float)) * .65
    assert any(e['stage'] == 'illumination' and e['accepted'] for e in events)


def test_fallback_unchanged_and_disabled_does_not_fit(tmp_path, monkeypatch):
    source = tmp_path / 'source.las'
    camera, frames = scene(source, 25)
    opts = ColorizationOptions(minimum_sharpness=0, occlusion_radius_pixels=0)
    off = colorize_las(source, tmp_path / 'off.las', frames, camera, options=opts)
    on = colorize_las(source, tmp_path / 'on.las', frames, camera,
                     options=replace(opts, illumination_balancing=True))
    np.testing.assert_array_equal(laspy.read(off.output_path).points.array, laspy.read(on.output_path).points.array)
    assert not on.illumination_balancing['applied']
    assert on.illumination_balancing['fallback_reasons']
    def fail(*args): raise AssertionError('Disabled path must not estimate illumination')
    monkeypatch.setattr('elios_colorizer.colorize._estimate_illumination', fail)
    colorize_las(source, tmp_path / 'disabled.las', frames, camera, options=opts)


def test_engine_rejects_unusable_raw_pixels_even_without_fit(tmp_path):
    source = tmp_path / 'source.las'
    camera, frames = scene(source, 25)
    frame = frames[0]
    frame.rgb[:] = [100, 140, 180]
    frame.rgb[:, :100] = [255, 100, 100]
    frame.rgb[:100, 100:] = [1, 1, 1]
    result = colorize_las(source, tmp_path / 'out.las', [frame], camera,
        options=ColorizationOptions(minimum_sharpness=0, illumination_balancing=True))
    report = result.illumination_balancing
    assert report['rejected_exposure_observations'] > 0
    assert result.colored_point_count < result.point_count

def test_equation_budget_and_point_disjoint_validation():
    radii, light = evidence(points=20000, exposures=(0, .02, -.02, 0))
    model, report = fit_model(radii, light)
    assert model is not None and report['overlap_sample_count'] <= 8192
    radii, light = evidence()
    light[1, ::5] *= 1.5
    assert fit_model(radii, light)[0] is None


def test_merged_removes_inherited_diagnostics_preserving_other_attributes(tmp_path):
    source = tmp_path / 'source.las'
    camera, frames = scene(source, 25)
    cloud = laspy.read(source)
    for name in ['ColorConfidence', 'ColorDistance']:
        cloud.add_extra_dim(laspy.ExtraBytesParams(name, 'float64'))
    cloud.write(source)
    result = colorize_las(source, tmp_path / 'merged.las', frames, camera,
        options=ColorizationOptions(minimum_sharpness=0, merged_output=True))
    output = laspy.read(result.output_path)
    assert set(output.point_format.extra_dimension_names) == {'OriginalValue', 'Colorized', 'SourceFlight'}
    np.testing.assert_array_equal(output.OriginalValue, cloud.OriginalValue)
