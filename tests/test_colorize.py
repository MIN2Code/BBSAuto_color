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
