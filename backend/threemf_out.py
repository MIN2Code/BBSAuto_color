"""多对象涂色 3MF 写出（Bambu Studio / Orca / Snapmaker Orca 项目格式）。

结构（对齐 Bambu 项目 .3mf，逆向自 OrcaSlicer 3mf.cpp 与 printago 格式逆向）：
- 3D/3dmodel.model：引用型 object（p:path 指向嵌套文件）+ build
- 3D/Objects/part_i.model：每件一个嵌套模型文件，mesh 的 triangle 携带
  paint_color（耗材槽位码，非颜色！槽1="4"、槽2="8"、槽3="0C"…16 槽）
- Metadata/Slic3r_PE_model.config：每件 volume 的基础 extruder（1-based）
- Metadata/project_settings.config：filament_colour（0-based 数组，颜色权威来源）

颜色模型：件级 color（整件单色 → 基础 extruder）+ 件内 face_slots
（逐面槽号 1-based，未指定面回落基础 extruder）。
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_CT = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openpackaging.org/content-types/2006/06">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
<Default Extension="config" ContentType="application/xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openpackaging.org/relationships/2006/06">
<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

# paint_color 槽位码（1-based 槽 → 代码），逆向自 OrcaSlicer/Bambu 涂色 3MF
SLOT_CODES = ["4", "8", "0C", "1C", "2C", "3C", "4C", "5C",
              "6C", "7C", "8C", "9C", "AC", "BC", "CC", "DC"]


def _esc(s: str) -> str:
    return escape(s)


def _head() -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<model unit="millimeter" xml:lang="en-US" '
            'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/2013/01" '
            'xmlns:bambu="https://schemas.bambulab.org/package/2022/06">')


def _mesh_xml(part: dict, face_codes: list[str | None] | None = None) -> str:
    res = ["<mesh>", "<vertices>"]
    for x, y, z in part["vertices"]:
        res.append(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>')
    res.append("</vertices><triangles>")
    if face_codes is None:                   # 无合并层：槽号直指耗材位（旧行为）
        slots = part.get("face_slots")
        f_slots = slots if slots is not None else []
        face_codes = [SLOT_CODES[s - 1] if 1 <= s <= len(SLOT_CODES) else None
                      for s in f_slots]
    for i, (a, b, c) in enumerate(part["faces"]):
        pc = f' paint_color="{face_codes[i]}"' if i < len(face_codes) and face_codes[i] else ""
        res.append(f'<triangle v1="{a}" v2="{b}" v3="{c}"{pc}/>')
    res.append("</triangles></mesh>")
    return "\n".join(res)


def write_project_3mf(parts: list[dict], palette_colors: list[str] | None = None,
                      face_slot_colors: list[str] | None = None) -> bytes:
    """parts: [{name, vertices, faces, color('#RRGGBB'), face_slots?}]

    连续 RGB 输出（一期默认）：filament_colour = 件色去重列表（每件基础 extruder），
    实际耗材映射由用户在切片软件中自行设置。palette_colors 仅在显式传入时
    并入耗材表（可选"吸附到已有色板"，默认关）。

    face_slot_colors（二期）：face_slots 的槽色表（phase2_palette）。提供时
    face_slots 解释为槽色表下标（1-based），区域色按首现顺序并入耗材表，
    逐面 paint_color 重映射到合并后位置；超出 16 耗材上限的色不写 paint_color。
    """
    colors: list[str] = []
    for p in parts:
        c = (p.get("color") or "").upper()
        if c and c not in colors:
            colors.append(c)
    for c in (c.upper() for c in (face_slot_colors or [])):
        if c not in colors:
            colors.append(c)
    for c in (c.upper() for c in (palette_colors or [])):
        if c not in colors:
            colors.append(c)
    if not colors:
        colors = ["#808080"]
    color_to_ext = {c: i + 1 for i, c in enumerate(colors)}
    slot_colors = [c.upper() for c in (face_slot_colors or [])]

    # ---- 主模型（引用型）----
    main = [_head()]
    main.append("<resources>")
    for oi, p in enumerate(parts):
        nm = _esc(p["name"])
        main.append(f'<object id="{oi + 2}" name="{nm}" type="model" '
                    f'p:path="Objects/part_{oi}.model"/>')
    main.append("</resources><build>")
    for oi in range(len(parts)):
        main.append(f'<item objectid="{oi + 2}" printable="1"/>')
    main.append("</build></model>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", "\n".join(main))
        for oi, p in enumerate(parts):
            nm = _esc(p["name"])
            codes = None
            slots = p.get("face_slots")
            if slot_colors and slots:
                codes = []
                for s in slots:
                    hex_color = slot_colors[s - 1] if 1 <= s <= len(slot_colors) else None
                    pos = color_to_ext.get(hex_color) if hex_color else None
                    codes.append(SLOT_CODES[pos - 1] if pos and pos <= len(SLOT_CODES) else None)
            nest = [_head(), "<resources>",
                    f'<object id="1" name="{nm}" type="model">',
                    _mesh_xml(p, codes), "</object>", "</resources>", "<build/>",
                    "</model>"]
            z.writestr(f"3D/Objects/part_{oi}.model", "\n".join(nest))

        cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
        for oi, p in enumerate(parts):
            c = (p.get("color") or "").upper()
            ext = color_to_ext.get(c, 1)
            cfg.append(f'<object id="{oi}">')
            cfg.append(f'<volume firstid="{oi + 1}" extruder="{ext}" '
                       f'name="{_esc(p["name"])}" volume_type="1"/>')
            cfg.append("</object>")
        cfg.append("</config>")
        z.writestr("Metadata/Slic3r_PE_model.config", "\n".join(cfg))

        import json
        z.writestr("Metadata/project_settings.config", json.dumps({
            "filament_colour": list(colors),
            "filament_type": ["PLA"] * len(colors),
            "filament_diameter": ["1.75"] * len(colors),
        }, indent=2))
    return buf.getvalue()
