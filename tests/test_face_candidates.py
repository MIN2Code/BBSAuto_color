import numpy as np

from backend.colorize import srgb8_to_lab
from backend.face_candidates import (
    FaceEvidence,
    collect_face_candidates,
    discover_regions,
)


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


def _lab_of(hex_color):
    rgb = np.array([[int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]], dtype=np.uint8)
    return tuple(float(v) for v in srgb8_to_lab(rgb)[0])


def _fan_part(n_faces=7):
    """n_faces 个共面三角形共享边 (v0,v1)：邻接图完全连通、法线一致。"""
    vertices = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0)]
    faces = []
    for k in range(n_faces):
        vertices.append((-0.8 + 0.3 * k, 1.0, 0.0))
        faces.append([0, 1, 2 + k])
    return {
        "vertices": np.array(vertices, dtype=np.float64),
        "faces": np.array(faces, dtype=np.int32),
        "face_slots": [0] * n_faces,
    }


def _make_evidence(secondary_faces, base_hex="#FFFFFF", secondary_hex="#D22A1F"):
    evidence = {}
    for face_index in range(7):
        lab = _lab_of(secondary_hex if face_index in secondary_faces else base_hex)
        evidence[face_index] = [FaceEvidence(0, lab, 1.0, 100)]
    return evidence


def test_discovers_connected_secondary_color():
    part = _fan_part()
    evidence = _make_evidence({4, 5, 6})

    regions = discover_regions(part, evidence, _lab_of("#FFFFFF"),
                               min_delta_e=10, min_faces=3)

    assert len(regions) == 1
    assert regions[0].faces == {4, 5, 6}


def test_rejects_isolated_single_face_noise():
    part = _fan_part()
    evidence = _make_evidence({6})

    regions = discover_regions(part, evidence, _lab_of("#FFFFFF"),
                               min_delta_e=10, min_faces=3)

    assert regions == []


def _region(faces, hex_color="#D22A1F", confidence=0.9, views=3, slot=5, rid=0):
    from backend.face_candidates import CandidateRegion
    return CandidateRegion(region_id=rid, faces=frozenset(faces), color_hex=hex_color,
                           support_views=views, confidence=confidence,
                           accepted=False, reason="discovered", slot=slot)


def test_decision_is_independent_of_region_order():
    import copy
    from backend.face_candidates import decide_regions

    regions = [_region({4, 5, 6}, confidence=0.9, views=3),
               _region({0, 1}, hex_color="#2A46D2", confidence=0.5, views=1, rid=1)]
    shuffled = copy.deepcopy(regions)
    shuffled.reverse()

    a = decide_regions(regions)
    b = decide_regions(shuffled)
    key = lambda rs: sorted((tuple(sorted(r.faces)), r.color_hex, r.accepted) for r in rs)
    assert key(a) == key(b)
    assert [r.accepted for r in a] == [True, False]


def test_low_confidence_region_keeps_base_color():
    from backend.face_candidates import apply_face_layers, decide_regions

    low = decide_regions([_region({4, 5}, confidence=0.3, views=1)])[0]
    assert not low.accepted
    slots = apply_face_layers(np.zeros(10, dtype=np.uint8), [low], {})
    assert np.all(slots == 0)


def test_manual_override_wins_over_accepted_region():
    from backend.face_candidates import apply_face_layers, decide_regions

    accepted = decide_regions([_region({2, 3, 4})])[0]
    assert accepted.accepted
    slots = apply_face_layers(np.zeros(10, dtype=np.uint8), [accepted], {2: 7})
    assert slots[2] == 7
    assert slots[3] == 5
    assert slots.dtype == np.uint8
