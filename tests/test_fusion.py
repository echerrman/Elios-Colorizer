"""Known-geometry checks for conservative alignment and multi-flight fusion."""
from pathlib import Path

import laspy
import numpy as np

from elios_colorizer.fusion import AlignmentResult, align_clouds, fuse_clouds


def make_colored(path: Path, xyz: np.ndarray, rgb: np.ndarray, confidence: np.ndarray,
                 colorized: np.ndarray | None = None) -> None:
    header = laspy.LasHeader(point_format=7, version="1.4")
    header.scales = [.001, .001, .001]
    data = laspy.LasData(header)
    data.add_extra_dim(laspy.ExtraBytesParams("Colorized", "uint8"))
    data.add_extra_dim(laspy.ExtraBytesParams("ColorConfidence", "float32"))
    data.add_extra_dim(laspy.ExtraBytesParams("ColorDistance", "float32"))
    data.x, data.y, data.z = xyz.T
    data.red, data.green, data.blue = rgb.T
    data.Colorized = np.ones(len(xyz), dtype=np.uint8) if colorized is None else colorized
    data.ColorConfidence = confidence.astype(np.float32)
    data.ColorDistance = np.linspace(1, 2, len(xyz), dtype=np.float32)
    data.write(path)


def identity(path: Path) -> AlignmentResult:
    return AlignmentResult(path.resolve(), np.eye(4), False, 0, 0, 1, 1, 0, 0, "test")


def test_alignment_accepts_only_verified_improvement_and_keeps_good_original(tmp_path):
    rng = np.random.default_rng(42)
    reference = rng.uniform(-1, 1, (2000, 3))
    rgb = np.tile([1000, 2000, 3000], (len(reference), 1))
    confidence = np.ones(len(reference))
    make_colored(tmp_path / "reference.las", reference, rgb, confidence)
    make_colored(tmp_path / "shifted.las", reference + [.08, -.05, .03], rgb, confidence)
    make_colored(tmp_path / "already_aligned.las", reference, rgb, confidence)
    results = align_clouds([tmp_path / "reference.las", tmp_path / "shifted.las",
                            tmp_path / "already_aligned.las"])
    assert results[1].applied
    assert results[1].final_median_m < results[1].initial_median_m * .5
    assert not results[2].applied
    np.testing.assert_array_equal(results[2].transform, np.eye(4))
    assert "original inspector coordinates retained" in results[2].note.lower()


def test_fusion_selects_best_confidence_and_removes_uncolored(tmp_path):
    first = tmp_path / "first.las"
    second = tmp_path / "second.las"
    make_colored(first, np.array([[0, 0, 0], [.1, 0, 0], [.5, 0, 0]]),
                 np.array([[60000, 0, 0], [1000, 2000, 3000], [5000, 5000, 5000]]),
                 np.array([.2, .8, 1.0]), np.array([1, 1, 0], dtype=np.uint8))
    make_colored(second, np.array([[.002, 0, 0], [.2, 0, 0]]),
                 np.array([[0, 0, 60000], [9000, 8000, 7000]]), np.array([.9, .7]))
    result = fuse_clouds([first, second], tmp_path / "merged.las",
                         alignments=(identity(first), identity(second)), voxel_size_m=.01)
    output = laspy.read(result.output_path)
    assert result.input_colored_points == 4
    assert result.point_count == 3
    overlap = np.argmin(np.abs(np.asarray(output.x)))
    assert output.blue[overlap] == 60000 and output.red[overlap] == 0
    assert output.SourceFlight[overlap] == 2
    assert np.all(output.Colorized == 1)
