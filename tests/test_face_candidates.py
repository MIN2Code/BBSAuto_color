import numpy as np

from backend.face_candidates import FaceEvidence, collect_face_candidates


def _part(face_slots=None, reverse=False):
    faces = np.array([[0, 1, 2]], dtype=np.int32)
    if reverse:
        faces = faces[:, [0, 2, 1]]
    part = {
        "vertices": np.array(
            [[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [0.0, 1.0, 0.0]],
            dtype=np.float64,
        ),
        "faces": faces,
        "face_slots": face_slots if face_slots is not None else [7],
    }
    return part


def _view(color=(220, 40, 30), ids=1, depth=5.0):
    image = np.full((100, 100, 3), color, dtype=np.uint8)
    id_buffer = np.full((100, 100), ids, dtype=np.int32)
    depth_buffer = np.full((100, 100), depth, dtype=np.float64)
    return {
        "camera": {
            "eye": [0.0, 0.0, 5.0],
            "target": [0.0, 0.0, 0.0],
            "up": [0.0, 1.0, 0.0],
            "fov": 90.0,
            "w": 100,
            "h": 100,
        },
        "image": image,
        "id_buffer": id_buffer,
        "depth": depth_buffer,
    }


def test_collection_does_not_change_face_slots():
    part = _part(face_slots=[7])
    original = part.get("face_slots").copy()

    result = collect_face_candidates(part, [_view()], "#FFFFFF")

    assert result
    assert part.get("face_slots") == original
    assert isinstance(result[0][0], FaceEvidence)


def test_hidden_face_has_no_evidence():
    part = _part()
    view = _view(ids=0)

    result = collect_face_candidates(part, [view], "#FFFFFF")

    assert result.get(0, []) == []


def test_back_facing_face_has_no_evidence():
    result = collect_face_candidates(_part(reverse=True), [_view()], "#FFFFFF")

    assert result.get(0, []) == []


def test_depth_occluded_face_has_no_evidence():
    result = collect_face_candidates(_part(), [_view(depth=4.4)], "#FFFFFF")

    assert result.get(0, []) == []


def test_depth_tolerance_keeps_nearly_matching_face():
    result = collect_face_candidates(_part(), [_view(depth=5.01)], "#FFFFFF")

    evidence = result[0]
    assert len(evidence) == 1
    assert evidence[0].view_index == 0
    assert evidence[0].pixel_count > 0
    assert evidence[0].visibility > 0
    assert len(evidence[0].lab) == 3


def test_cover_mapping_samples_render_image_coordinates():
    part = _part()
    part["vertices"] += np.array([1.0, 0.0, 0.0])
    view = _view()
    view["image"] = np.full((200, 100, 3), (220, 40, 30), dtype=np.uint8)
    view["id_buffer"] = np.zeros((200, 100), dtype=np.int32)
    view["depth"] = np.full((200, 100), 5.0, dtype=np.float64)
    # 质心 (1, -1/3, 0) 在 fov=90 / eye=(0,0,5) 下投影到 100×100 视口的
    # (60, 53.33)；200×100 渲染图按 cover 盖住视口时上下各裁 50 图像像素，
    # 逆映射回图像坐标 (col 60, row 103.33)。ID 块须罩住 5×5 采样窗。
    view["id_buffer"][101:106, 58:64] = 1

    result = collect_face_candidates(part, [view], "#FFFFFF")

    assert len(result[0]) == 1
