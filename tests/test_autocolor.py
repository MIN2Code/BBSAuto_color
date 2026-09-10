"""AutoColor MVP 测试：STL 组合、色板提取、3MF 写出。"""
import io

import numpy as np
import trimesh
from PIL import Image

from backend import meshpack, threemf_out
from backend.palette import extract_palette


def _box_stl(size=20.0) -> bytes:
    m = trimesh.creation.box(extents=(size, size, size))
    buf = io.BytesIO()
    m.export(buf, file_type="stl")
    return buf.getvalue()


def _new_session_with_parts(n=2):
    sid = meshpack.new_session()
    for i in range(n):
        meshpack.add_part(sid, f"part_{i}.stl", _box_stl(20.0 + 10 * i))
    return sid


def test_multi_stl_session():
    sid = _new_session_with_parts(2)
    s = meshpack.session_summary(sid)
    assert len(s["parts"]) == 2
    assert s["parts"][1]["area_cm2"] > s["parts"][0]["area_cm2"]
    meshpack.set_color(sid, 0, "#ff3b30")
    assert meshpack.session_summary(sid)["parts"][0]["color"] == "#FF3B30"


def test_auto_assign_by_area():
    sid = _new_session_with_parts(2)
    meshpack.get_session(sid)["palette"] = [
        {"hex": "#FF3B30", "ratio": 0.7}, {"hex": "#4CD964", "ratio": 0.3}]
    n = meshpack.auto_assign(sid)
    assert n == 2
    s = meshpack.session_summary(sid)
    big = max(s["parts"], key=lambda p: p["area_cm2"])
    assert big["color"] == "#FF3B30", "最大件应配占比最高的颜色"


def test_auto_assign_prefix_grouping():
    """同前缀件（切件/左右对称）应同色：af1/af2 同色，arm_l/arm_r 同色。"""
    sid = meshpack.new_session()
    for name in ("af1-a.stl", "af2-b.stl", "af3-c.stl", "arm_l-d.stl", "arm_r-e.stl"):
        meshpack.add_part(sid, name, _box_stl(20.0))
    meshpack.get_session(sid)["palette"] = [
        {"hex": "#FF3B30", "ratio": 0.7}, {"hex": "#4CD964", "ratio": 0.3}]
    n = meshpack.auto_assign(sid)
    assert n == 5, "所有件都应被配色（循环分配）"
    s = meshpack.session_summary(sid)
    by = {p["name"].split("-")[0]: p["color"] for p in s["parts"]}
    assert by["af1"] == by["af2"] == by["af3"], "同前缀切件应同色"
    assert by["arm_l"] == by["arm_r"], "左右对称件应同色"
    assert by["af1"] != by["arm_l"], "不同部件组应区分"


def test_palette_extraction():
    img = np.full((64, 64, 3), (30, 18, 18), dtype=np.uint8)  # 暗色背景（渲染图）
    img[16:32, 16:48] = (240, 200, 160)   # 肤色块
    img[32:48, 16:48] = (48, 80, 160)     # 蓝色块
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    pal = extract_palette(buf.getvalue(), k=4)
    assert len(pal) >= 2
    hexes = {p["hex"] for p in pal}
    assert any(h.startswith("#F0C8") for h in hexes), f"肤色应入板：{hexes}"
    assert any(h.startswith("#3050") for h in hexes), f"蓝色应入板：{hexes}"
    assert pal[0]["ratio"] >= pal[-1]["ratio"]


def test_write_project_3mf_structure():
    sid = _new_session_with_parts(2)
    meshpack.set_color(sid, 0, "#FF3B30")
    meshpack.set_color(sid, 1, "#4CD964")
    sess = meshpack.get_session(sid)
    data = threemf_out.write_project_3mf(sess["parts"], ["#FF3B30", "#4CD964"])
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert "3D/3dmodel.model" in names
    assert "Metadata/Slic3r_PE_model.config" in names
    assert "Metadata/project_settings.config" in names
    nested = [n for n in names if n.startswith("3D/Objects/")]
    assert len(nested) == 2, "Bambu 嵌套结构：每件一个 Objects/*.model"
    main = z.read("3D/3dmodel.model").decode()
    assert main.count("p:path=") == 2, "主模型引用嵌套对象"
    import json
    cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert set(cfg["filament_colour"]) == {"#FF3B30", "#4CD964"}
    mcfg = z.read("Metadata/Slic3r_PE_model.config").decode()
    assert mcfg.count("<object id=") == 2
    assert 'extruder="1"' in mcfg and 'extruder="2"' in mcfg
    n0 = z.read(nested[0]).decode()
    assert "<mesh>" in n0 and "<triangle" in n0


def test_face_slots_paint_color():
    """件内 face_slots → 嵌套 model 的 paint_color 槽位码。"""
    sid = _new_session_with_parts(1)
    sess = meshpack.get_session(sid)
    p = sess["parts"][0]
    p["color"] = "#FF3B30"
    p["face_slots"] = [2, 0, 2, 3]   # 槽2/未涂/槽2/槽3
    data = threemf_out.write_project_3mf(sess["parts"], ["#FF3B30", "#4CD964", "#00C853"])
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    nested = [n for n in z.namelist() if n.startswith("3D/Objects/")][0]
    m = z.read(nested).decode()
    assert 'paint_color="8"' in m, "槽2 槽位码=8"
    assert 'paint_color="0C"' in m, "槽3 槽位码=0C"
    assert m.count("<triangle") >= 4


def test_unassigned_parts_fall_to_ext1():
    sid = _new_session_with_parts(1)
    sess = meshpack.get_session(sid)
    data = threemf_out.write_project_3mf(sess["parts"], [])
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    mcfg = z.read("Metadata/Slic3r_PE_model.config").decode()
    assert 'extruder="1"' in mcfg
    import json
    cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert cfg["filament_colour"] == ["#808080"]
