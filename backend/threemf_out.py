"""多对象涂色 3MF 写出（Bambu Studio / Orca / Snapmaker Orca 项目格式）。

结构（对齐 Bambu 项目 .3mf，thermal-assess 解析经验的逆向应用）：
- 3D/3dmodel.model：多 object（每件一个 mesh）+ basematerials（每色一条）
- Metadata/Slic3r_PE_model.config：每件 volume 的 extruder 分配（1-based）
- Metadata/project_settings.config：filament_colour 等切片设置（JSON）

⚠️ 格式细节（extruder 分配是否被 BS 正确恢复为对象颜色）需 BS 实测验证，
首次导入若颜色未生效，检查 Slic3r_PE_model.config 的 id 约定（0-based
object 序号 + 1-based extruder）。
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


def _model_xml(parts: list[dict], material_names: list[str]) -> str:
    res = ['<?xml version="1.0" encoding="UTF-8"?>']
    res.append('<model unit="millimeter" xml:lang="en-US" '
               'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
               'xmlns:bambu="https://schemas.bambulab.org/package/2022/06">')
    res.append("<resources>")
    res.append('<basematerials id="1">')
    for i, name in enumerate(material_names):
        res.append(f'<base name="{escape(name)}" displaycolor="#FFFFFFFF"/>')
    res.append("</basematerials>")
    for oid, p in enumerate(parts):
        name = escape(p["name"])
        res.append(f'<object id="{oid + 2}" name="{name}" type="model">')
        res.append("<mesh>")
        res.append("<vertices>")
        for x, y, z in p["vertices"]:
            res.append(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>')
        res.append("</vertices>")
        res.append("<triangles>")
        for a, b, c in p["faces"]:
            res.append(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>')
        res.append("</triangles>")
        res.append("</mesh>")
        res.append("</object>")
    res.append("</resources>")
    res.append("<build>")
    for oid in range(len(parts)):
        res.append(f'<item objectid="{oid + 2}" printable="1"/>')
    res.append("</build>")
    res.append("</model>")
    return "\n".join(res)


def _model_config(parts: list[dict], color_to_ext: dict[str, int]) -> str:
    res = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for oi, p in enumerate(parts):
        ext = color_to_ext.get(p["color"].upper(), 1)
        res.append(f'<object id="{oi}">')
        res.append(f'<volume firstid="{oi + 1}" extruder="{ext}" '
                   f'name="{escape(p["name"])}" volume_type="1"/>')
        res.append("</object>")
    res.append("</config>")
    return "\n".join(res)


def _project_settings(colors: list[str]) -> str:
    import json
    cfg = {
        "filament_colour": [c if c.startswith("#") else f"#{c}" for c in colors],
        "filament_type": ["PLA"] * len(colors),
        "filament_diameter": ["1.75"] * len(colors),
        "printer_model": "",
    }
    return json.dumps(cfg, indent=2)


def write_project_3mf(parts: list[dict]) -> bytes:
    """parts: [{name, vertices(float32 nx3), faces(int32 tx3), color('#RRGGBB')}]

    每个唯一颜色一个 extruder/耗材槽；未分配颜色的件落到 extruder 1。
    返回 3MF 字节。
    """
    colors: list[str] = []
    for p in parts:
        c = (p.get("color") or "").upper()
        if c and c not in colors:
            colors.append(c)
    if not colors:
        colors = ["#808080"]
    color_to_ext = {c: i + 1 for i, c in enumerate(colors)}
    material_names = [f"Filament {i + 1}" for i in range(len(colors))]

    model = _model_xml(parts, material_names)
    config = _model_config(parts, color_to_ext)
    settings = _project_settings(colors)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/Slic3r_PE_model.config", config)
        z.writestr("Metadata/project_settings.config", settings)
    return buf.getvalue()
