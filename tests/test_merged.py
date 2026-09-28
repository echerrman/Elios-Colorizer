from pathlib import Path

import laspy
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from elios_colorizer.colorize import FrameObservation
from elios_colorizer.merged import (MergedWorkflowError, load_transform, merge_geometry,
                                     optimize_pose_graph, PoseGraphEdge, selection_transform,
                                     spatially_index_geometry, transform_observations,
                                     validate_transforms)


def make_cloud(path: Path, xyz) -> Path:
    header = laspy.LasHeader(point_format=1, version="1.2")
    header.scales = [.001, .001, .001]
    cloud = laspy.LasData(header)
    values = np.asarray(xyz, dtype=float)
    cloud.x, cloud.y, cloud.z = values[:, 0], values[:, 1], values[:, 2]
    cloud.write(path)
    return path


def test_transform_moves_camera_position_and_orientation(tmp_path):
    matrix_path = tmp_path / "matrix.txt"
    matrix_path.write_text("0 -1 0 10\n1 0 0 20\n0 0 1 30\n0 0 0 1\n")
    matrix = load_transform(matrix_path)
    observation = FrameObservation(np.zeros((2, 2, 3), np.uint8), (1, 0, 0), (0, 0, 0, 1), 0, 0)
    moved = next(transform_observations([observation], matrix, 3))
    np.testing.assert_allclose(moved.position_world_m, [10, 21, 30])
    np.testing.assert_allclose(Rotation.from_quat(moved.orientation_xyzw).as_matrix(), matrix[:3, :3])
    assert moved.source_flight == 3


def test_merge_geometry_preserves_points_and_manual_validation(tmp_path):
    first = make_cloud(tmp_path / "first.las", [(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    second = make_cloud(tmp_path / "second.las", [(2, 0, 0), (3, 0, 0), (2, 1, 0)])
    shift = np.eye(4); shift[0, 3] = -2
    merged = merge_geometry([first, second], [np.eye(4), shift], tmp_path / "merged.las")
    assert laspy.read(merged).header.point_count == 6
    result = validate_transforms([first, second], merged, [np.eye(4), shift])
    assert len(result) == 2


def test_spatial_index_preserves_records_and_orders_geometry(tmp_path):
    xyz = [(float(index % 2) * 10, index * .01, 0) for index in range(20)]
    source = make_cloud(tmp_path / 'interleaved.las', xyz)
    cloud = laspy.read(source)
    cloud.intensity = np.arange(20, dtype=np.uint16) + 100
    cloud.write(source)
    indexed, report = spatially_index_geometry(source, tmp_path / 'indexed.las',
                                                axis_cells=16, chunk_size=7)
    original, ordered = laspy.read(source), laspy.read(indexed)
    original_rows = sorted(zip(original.x, original.y, original.z, original.intensity))
    ordered_rows = sorted(zip(ordered.x, ordered.y, ordered.z, ordered.intensity))
    assert original_rows == ordered_rows
    assert np.count_nonzero(np.diff(np.asarray(ordered.x)) != 0) == 1
    assert report['attributes_preserved'] and report['point_count'] == 20


def test_matrix_rejects_scale(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("2 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n")
    with pytest.raises(MergedWorkflowError, match="scale"):
        load_transform(path)


def test_matrix_can_be_entered_directly():
    matrix = selection_transform({'transform_values':
        '1 0 0 2.5\n0 1 0 -1\n0 0 1 0.25\n0 0 0 1'}, 2)
    np.testing.assert_allclose(matrix[:3, 3], [2.5, -1, .25])


def translation(x):
    matrix = np.eye(4); matrix[0, 3] = x
    return matrix


def test_pose_graph_uses_indirect_links_and_loop_optimization():
    edges = [
        PoseGraphEdge(0, 1, translation(.10), .8, .04, .01, .05, 'test'),
        PoseGraphEdge(1, 2, translation(.10), .8, .04, .01, .05, 'test'),
        PoseGraphEdge(0, 2, translation(.20), .7, .04, .01, .05, 'test'),
    ]
    transforms, report = optimize_pose_graph(3, edges)
    np.testing.assert_allclose([matrix[0, 3] for matrix in transforms], [0, .1, .2], atol=2e-3)
    assert report['redundant_loop_edges'] == 1
    assert report['maximum_edge_translation_residual_m'] < .002


def test_pose_graph_stops_when_overlap_network_is_disconnected():
    edge = PoseGraphEdge(0, 1, np.eye(4), .8, .01, .01, .05, 'test')
    with pytest.raises(MergedWorkflowError, match='disconnected'):
        optimize_pose_graph(3, [edge])
