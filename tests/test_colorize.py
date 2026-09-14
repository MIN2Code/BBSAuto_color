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
    assert [(x["index"], x["hex"]) for x in a] == [(x["index"], x["hex"]) for x in b]


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
    assert (a["conf"], a["flagged"], a["reason"], a["pixels"], a["views"], a["override"]) == (
        b["conf"], b["flagged"], b["reason"], b["pixels"], b["views"], b["override"]
    )
    assert a["hex"] == b["hex"]
