"""二期 3MF 编码回归：件级基线无 paint_color，接受候选才写面级色。"""
import io
import zipfile

import numpy as np
import trimesh

from backend.threemf_out import write_project_3mf


def _box_part(color="#FF3B30", face_slots=None):
    m = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
    return {"name": "p.stl", "color": color,
            "vertices": np.asarray(m.vertices, dtype=np.float64),
            "faces": np.asarray(m.faces, dtype=np.int32),
            "face_slots": face_slots}


def read_part_xml(blob: bytes, idx: int) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read(f"3D/Objects/part_{idx}.model").decode()


def count_painted_triangles(xml: str) -> int:
    return xml.count("paint_color=")


def filament_colors(blob: bytes) -> list:
    import json
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return json.loads(z.read("Metadata/project_settings.config"))["filament_colour"]


def test_object_only_color_has_no_face_paint():
    parts = [_box_part(color="#FF3B30")]
    blob = write_project_3mf(parts)
    assert b"paint_color=" not in blob


def test_all_zero_slots_have_no_face_paint():
    parts = [_box_part(color="#FF3B30", face_slots=[0] * 12)]
    blob = write_project_3mf(parts, face_slot_colors=["#2A46D2"])
    assert b"paint_color=" not in blob


def test_only_accepted_faces_are_painted():
    slots = [0] * 12
    for f in (2, 5, 9):
        slots[f] = 1
    parts = [_box_part(color="#FF3B30", face_slots=slots)]
    blob = write_project_3mf(parts, face_slot_colors=["#2A46D2"])
    xml = read_part_xml(blob, 0)
    assert count_painted_triangles(xml) == 3


def test_region_colors_extend_filament_table_stably():
    slots = [0] * 12
    slots[2] = 1
    parts = [_box_part(color="#FF3B30", face_slots=slots),
             _box_part(color="#4CD964")]
    blob = write_project_3mf(parts, face_slot_colors=["#2A46D2"])
    colors = filament_colors(blob)
    assert colors == ["#FF3B30", "#4CD964", "#2A46D2"], "件色在前、区域色按首现去重追加"
    xml = read_part_xml(blob, 0)
    assert count_painted_triangles(xml) == 1
    # 槽 1（#2A46D2）合并后位于第 3 号耗材 → SLOT_CODES[2] = "0C"
    assert 'paint_color="0C"' in xml
