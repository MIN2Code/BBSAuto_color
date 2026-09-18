import numpy as np
import pytest

from backend.colorize import recolor_session


@pytest.fixture
def make_color_session():
    def factory():
        shape = (32, 32)
        image_a = np.full((*shape, 3), (210, 80, 60), dtype=np.uint8)
        image_b = np.full((*shape, 3), (80, 100, 220), dtype=np.uint8)
        idbuf = np.zeros((*shape, 3), dtype=np.uint8)
        idbuf[4:-4, 4:-4] = (1, 0, 255)
        return {
            "images": [image_a, image_b],
            "idbufs": [idbuf.copy(), idbuf.copy()],
            "parts": [{"name": "part0", "color": "#808080"}],
            "overrides": {},
        }
    return factory


def test_recolor_is_independent_of_view_order(make_color_session):
    sess = make_color_session()
    a = recolor_session(sess)
    sess["images"].reverse()
    sess["idbufs"].reverse()
    b = recolor_session(sess)
    fields = ("index", "name", "hex", "conf", "flagged", "reason", "pixels", "views", "override")
    assert [tuple(x[field] for field in fields) for x in a] == [
        tuple(x[field] for field in fields) for x in b
    ]


def test_override_wins_after_recolor(make_color_session):
    sess = make_color_session()
    sess["overrides"] = {"0": "#123456"}
    result = recolor_session(sess)
    assert result[0]["hex"] == "#123456"
    assert result[0]["override"] is True


def test_recolor_contract_is_complete_for_all_branches(make_color_session):
    required = {"index", "name", "hex", "conf", "flagged", "reason", "pixels", "views", "override"}
    for overrides, visible in (({}, True), ({"0": "#123456"}, True), ({}, False)):
        sess = make_color_session()
        sess["overrides"] = overrides
        if not visible:
            sess["idbufs"] = [np.zeros_like(sess["idbufs"][0])]
        result = recolor_session(sess)
        assert required <= result[0].keys()


def test_same_brightness_candidates_are_order_independent(make_color_session, monkeypatch):
    sess = make_color_session()
    idbuf = sess["idbufs"][0]
    sess["images"].append(np.full((*idbuf.shape[:2], 3), (120, 180, 40), dtype=np.uint8))
    sess["idbufs"].append(idbuf.copy())
    labs_by_rgb = {
        (210, 80, 60): np.array([50.0, 60.0, 20.0]),
        (80, 100, 220): np.array([50.0, -20.0, -60.0]),
        (120, 180, 40): np.array([50.0, 10.0, 70.0]),
    }

    def controlled_lab(rgb):
        return np.repeat(labs_by_rgb[tuple(rgb[0].astype(int))][None, :], len(rgb), axis=0)

    monkeypatch.setattr("backend.colorize.white_balance", lambda img, fg: img.astype(np.float32))
    monkeypatch.setattr("backend.colorize.srgb8_to_lab", controlled_lab)
    a = recolor_session(sess)[0]
    sess["images"].reverse()
    sess["idbufs"].reverse()
    b = recolor_session(sess)[0]
    fields = ("index", "name", "hex", "conf", "flagged", "reason", "pixels", "views", "override")
    assert tuple(a[field] for field in fields) == tuple(b[field] for field in fields)


def test_disagree_hard_keeps_best_estimate():
    """views_disagree_hard：显示最佳估计色+旗帜，不再回退默认灰。

    纯色前景会被白平衡灰世界假设洗成灰（增益钳制后色相消失），
    夹具须带中性灰锚件稳住白平衡，跨视角色相分歧才能真实触发。
    """
    shape = (32, 32)

    def make_view(color):
        img = np.full((*shape, 3), (120, 120, 120), dtype=np.uint8)
        img[4:-4, 4:-4] = color                     # 件1（红/蓝/红）
        img[4:-4, 20:-4] = (120, 120, 120)          # 件2 灰锚
        idb = np.zeros((*shape, 3), dtype=np.uint8)
        idb[4:-4, 4:-4] = (1, 0, 255)
        idb[4:-4, 20:-4] = (2, 0, 255)
        return img, idb

    views = [make_view((220, 40, 30)), make_view((30, 60, 220)), make_view((220, 40, 30))]
    sess = {"images": [v[0] for v in views], "idbufs": [v[1] for v in views],
            "parts": [{"name": "p1.stl", "color": "#808080"},
                      {"name": "p2.stl", "color": "#808080"}],
            "overrides": {}}
    item = recolor_session(sess)[0]
    assert item["flagged"] is True and item["conf"] == 0.0
    assert item["reason"] == "views_disagree_hard"
    assert item["hex"] != "#808080", "有证据件不得回退默认灰"


def test_no_evidence_part_falls_back_to_group_color():
    """无证据件回退同前缀可信件色（af1/af2 切件同色先验）。"""
    shape = (32, 32)
    img = np.full((*shape, 3), (210, 80, 60), dtype=np.uint8)
    idbuf = np.zeros((*shape, 3), dtype=np.uint8)
    idbuf[4:-4, 4:-4] = (1, 0, 255)      # 仅件1（af1）可见，件2（af2）无证据
    sess = {"images": [img, img.copy()], "idbufs": [idbuf.copy(), idbuf.copy()],
            "parts": [{"name": "af1-x.stl", "color": ""},
                      {"name": "af2-x.stl", "color": ""}],
            "overrides": {}}
    result = recolor_session(sess)
    assert result[0]["views"] == 2 and not result[0]["flagged"]
    assert result[1]["hex"] == result[0]["hex"]
    assert result[1].get("fallback") is True
    assert result[1]["flagged"] is True


def test_no_evidence_part_keeps_existing_nongray_color():
    """已有非默认灰旧色的件不做同组回退；默认灰件借供体的计算色。"""
    shape = (32, 32)
    img = np.full((*shape, 3), (210, 80, 60), dtype=np.uint8)
    idbuf = np.zeros((*shape, 3), dtype=np.uint8)
    idbuf[4:-4, 4:-4] = (1, 0, 255)
    sess = {"images": [img], "idbufs": [idbuf],
            "parts": [{"name": "af1-x.stl", "color": ""},
                      {"name": "af2-x.stl", "color": "#8A939E"},
                      {"name": "zz9-x.stl", "color": "#336699"}],   # 无同组供体
            "overrides": {}}
    result = recolor_session(sess)
    assert result[1]["hex"] == result[0]["hex"]   # af2 默认灰 → 借 af1 计算色
    assert result[1].get("fallback") is True
    assert result[2]["hex"] == "#336699"          # zz9 非灰旧色保留，无供体
    assert result[2].get("fallback") is None
