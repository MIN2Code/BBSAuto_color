"""AutoColor API：多 STL 组合、渲染图吸色、件级配色、涂色 3MF 导出。"""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import meshpack, palette as palette_mod, threemf_out

router = APIRouter(prefix="/api")


def _sid(request: Request) -> str:
    sid = request.query_params.get("sid") or request.headers.get("X-Session")
    if not sid:
        raise HTTPException(400, "缺少 sid")
    try:
        meshpack.get_session(sid)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return sid


@router.post("/sessions")
def create_session():
    return {"session_id": meshpack.new_session()}


@router.post("/sessions/{sid}/parts")
async def upload_parts(sid: str, files: list[UploadFile] = File(...)):
    try:
        meshpack.get_session(sid)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    added, errors = [], []
    for f in files:
        data = await f.read()
        if not data:
            continue
        name = f.filename or f"part_{len(added)}.stl"
        try:
            added.append(meshpack.add_part(sid, name, data))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    return {"added": added, "errors": errors, "session": meshpack.session_summary(sid)}


@router.post("/sessions/{sid}/image")
async def upload_image(sid: str, file: UploadFile = File(...)):
    data = await file.read()
    try:
        pal = palette_mod.extract_palette(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"图片解析失败：{exc}") from exc
    sess = meshpack.get_session(sid)
    # 多图色板合并：近色（欧氏 <40）占比加权保留，新图色补入
    merged = list(sess["palette"])
    for c in pal:
        h = bytes.fromhex(c["hex"].lstrip("#"))
        near = None
        for m in merged:
            g = bytes.fromhex(m["hex"].lstrip("#"))
            if sum((a - b) ** 2 for a, b in zip(h, g)) ** 0.5 < 40:
                near = m
                break
        if near:
            w1, w2 = near["ratio"], c["ratio"]
            mix = [round((a * w1 + b * w2) / (w1 + w2)) for a, b in
                   zip(bytes.fromhex(near["hex"].lstrip("#")), h)]
            near["hex"] = "#%02X%02X%02X" % tuple(mix)
            near["ratio"] = round(w1 + w2, 3)
        else:
            merged.append(dict(c))
    merged.sort(key=lambda x: -x["ratio"])
    sess["palette"] = merged
    sess["image_name"] = (sess["image_name"] + " + " if sess["image_name"] else "") + (file.filename or "render.png")
    return {"palette": merged, "image_name": sess["image_name"]}


@router.get("/sessions/{sid}")
def session_summary(sid: str):
    return meshpack.session_summary(sid)


@router.post("/sessions/{sid}/color/{part_index}")
async def set_part_color(sid: str, part_index: int, request: Request):
    body = await request.json()
    color = body.get("color", "")
    try:
        meshpack.set_color(sid, part_index, color)
    except (IndexError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "color": meshpack.get_session(sid)["parts"][part_index]["color"]}


@router.post("/sessions/{sid}/auto")
def auto_assign(sid: str):
    try:
        n = meshpack.auto_assign(sid)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "assigned": n, "session": meshpack.session_summary(sid)}


@router.get("/sessions/{sid}/geometry/{part_index}")
def geometry(sid: str, part_index: int):
    sess = meshpack.get_session(sid)
    if not (0 <= part_index < len(sess["parts"])):
        raise HTTPException(404, "part not found")
    p = sess["parts"][part_index]
    v = np.ascontiguousarray(p["vertices"], dtype=np.float32)
    f = np.ascontiguousarray(p["faces"], dtype=np.uint32)
    head = np.array([v.shape[0], f.shape[0]], dtype=np.uint32)
    return Response(
        content=head.tobytes() + v.tobytes() + f.tobytes(),
        media_type="application/octet-stream",
        headers={"X-Part-Name": p["name"], "Access-Control-Expose-Headers": "X-Part-Name"},
    )


@router.get("/sessions/{sid}/export.3mf")
def export_3mf(sid: str):
    sess = meshpack.get_session(sid)
    if not sess["parts"]:
        raise HTTPException(409, "会话内没有模型件")
    data = threemf_out.write_project_3mf(sess["parts"])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="autocolor_project.3mf"'},
    )
