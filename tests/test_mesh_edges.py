import numpy as np

from backend.mesh_edges import build_face_adjacency, grow_region


def test_build_face_adjacency_two_triangles_share_edge():
    faces = np.array([[0, 1, 2], [2, 1, 3]])
    assert build_face_adjacency(faces) == [[1], [0]]


def test_region_growth_stops_at_sharp_edge():
    adjacency = [[1], [0, 2], [1]]
    normals = np.array([[0, 0, 1], [0, 0, 1], [1, 0, 0]], dtype=float)
    colors = np.array([[50, 20, 10], [51, 20, 10], [51, 20, 10]], dtype=float)
    mask = grow_region([0], adjacency, normals, colors, max_normal_deg=35, max_delta_e=8)
    assert mask.tolist() == [True, True, False]


def test_region_growth_stops_at_color_edge():
    adjacency = [[1], [0, 2], [1]]
    normals = np.array([[0, 0, 1]] * 3, dtype=float)
    colors = np.array([[50, 0, 0], [52, 0, 0], [70, 30, 20]], dtype=float)
    mask = grow_region([0], adjacency, normals, colors, max_normal_deg=35, max_delta_e=8)
    assert mask.tolist() == [True, True, False]


def test_adjacency_welds_stl_vertex_soup():
    """STL 每个三角形自带顶点副本：按坐标焊接后共享边才存在。"""
    from backend.mesh_edges import build_face_adjacency, vertex_weld_map

    # 两个三角形共享一条边，但顶点全部独立（索引 0..5 互不相同）
    vertices = np.array([
        [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],   # tri A
        [1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 1.0, 0.0],   # tri B（共享边 (0,0)-(1,0)）
    ], dtype=np.float32)
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    weld = vertex_weld_map(vertices)
    assert weld[0] == weld[4] and weld[1] == weld[3]
    assert len(set(weld.tolist())) == 4                     # 6 顶点焊成 4 个
    adjacency = build_face_adjacency(weld[faces.astype(np.int64)])
    assert adjacency[0] == [1] and adjacency[1] == [0]
