"""AutoColor API：多 STL 组合、渲染图吸色、件级配色、涂色 3MF 导出。"""
from __future__ import annotations

import numpy as np
import io

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
    # 缓存降采样像素（投影上色的采样源：后端做逐面采样）
    import numpy as np
    from PIL import Image as PILImage
    im = PILImage.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if max(w, h) > 1024:
        sc = 1024 / max(w, h)
        im = im.resize((int(w * sc), int(h * sc)), PILImage.BILINEAR)
    sess["image_pixels"] = np.asarray(im, dtype=np.uint8)
    sess["image_size"] = [im.size[0], im.size[1]]
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
    sess["palette_colors"] = [c["hex"] for c in merged]
    sess["image_name"] = (sess["image_name"] + " + " if sess["image_name"] else "") + (file.filename or "render.png")
    return {"palette": merged, "image_name": sess["image_name"]}


@router.get("/sessions/{sid}")
def session_summary(sid: str):
    return meshpack.session_summary(sid)


@router.post("/sessions/{sid}/upaxis")
async def set_up_axis(sid: str, request: Request):
    body = await request.json()
    try:
        axis = meshpack.set_up_axis(sid, body.get("axis", "y"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "up_axis": axis}


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


@router.get("/sessions/{sid}/facecolors/{part_index}")
def get_face_colors(sid: str, part_index: int):
    sess = meshpack.get_session(sid)
    if not (0 <= part_index < len(sess["parts"])):
        raise HTTPException(404, "part not found")
    slots = sess["parts"][part_index].get("face_slots") or []
    import base64
    b = bytes(min(int(x), 255) for x in slots)
    return {"slots_b64": base64.b64encode(b).decode()}


@router.post("/sessions/{sid}/facecolors/{part_index}")
async def set_face_colors(sid: str, part_index: int, request: Request):
    """接收前端投影采样结果：slots_b64 = 每面 1-based 槽号的 base64(uint8)，0=未涂。"""
    import base64
    body = await request.json()
    sess = meshpack.get_session(sid)
    if not (0 <= part_index < len(sess["parts"])):
        raise HTTPException(404, "part not found")
    raw = base64.b64decode(body.get("slots_b64", ""))
    sess["parts"][part_index]["face_slots"] = list(raw)
    return {"ok": True, "painted": sum(1 for x in raw if x)}


@router.post("/sessions/{sid}/project")
def project_paint(sid: str, body: dict):
    """投影上色：按前端对齐后的相机参数，把渲染图颜色采样到面向相机的面。

    body: {camera: {eye, target, up?, fov, w, h}, parts?: [idx]}
    返回每件 slots_b64（uint8 槽号，0=未采样），前端做顶点色预览。
    """
    import base64
    import math

    sess = meshpack.get_session(sid)
    px_img = sess.get("image_pixels")
    if px_img is None:
        raise HTTPException(409, "先上传渲染图")
    ih, iw = px_img.shape[:2]
    pal_hex = sess.get("palette_colors") or []
    if not pal_hex:
        raise HTTPException(409, "色板为空")
    pal = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in pal_hex],
                   dtype=np.float64)

    cam = body.get("camera", {})
    eye = np.array(cam["eye"], dtype=np.float64)
    target = np.array(cam["target"], dtype=np.float64)
    up = np.array(cam.get("up") or [0, 1, 0], dtype=np.float64)
    fov = float(cam.get("fov", 45))
    cw, ch = float(cam["w"]), float(cam["h"])
    scale = max(iw / cw, ih / ch)          # 渲染图 cover 映射到视口
    dw, dh = iw / scale, ih / scale
    ox, oy = (cw - dw) / 2, (ch - dh) / 2

    fwd = target - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, up)
    right /= np.linalg.norm(right)
    upv = np.cross(right, fwd)
    tanf = math.tan(math.radians(fov / 2))
    aspect = cw / ch

    out = []
    targets = body.get("parts") or list(range(len(sess["parts"])))
    for pi in targets:
        p = sess["parts"][pi]
        v = p["vertices"].astype(np.float64)
        fc = p["faces"]
        tri = v[fc]
        cen = tri.mean(axis=1)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        nn = np.linalg.norm(n, axis=1)
        nn[nn == 0] = 1
        n /= nn[:, None]
        facing = np.einsum("ij,ij->i", n, eye - cen) > 0
        rel = cen - eye
        x = rel @ right
        y = rel @ upv
        zc = -(rel @ fwd)                     # three.js 相机空间：前方为负
        vis = facing & (zc < -0.1)
        out_item = {"index": pi, "slots_b64": ""}
        if vis.any():
            depth_pos = -zc[vis]              # 正深度
            ndx = (x[vis] / depth_pos) / (tanf * aspect)
            ndy = (y[vis] / depth_pos) / tanf
            cxs = (ndx * 0.5 + 0.5) * cw
            cys = (1 - (ndy * 0.5 + 0.5)) * ch
            inview = (cxs >= ox) & (cxs < ox + dw) & (cys >= oy) & (cys < oy + dh)
            vi = np.flatnonzero(vis)[inview]
            if vi.size:
                ixs = np.clip(((cxs[inview] - ox) / scale).astype(np.int64), 0, iw - 1)
                iys = np.clip(((cys[inview] - oy) / scale).astype(np.int64), 0, ih - 1)
                depth = depth_pos[inview]
                pix = iys * iw + ixs
                # 同像素多面 → 深度最近者胜（lexsort：像素主键、深度次键）
                order = np.lexsort((depth, pix))
                ps = pix[order]
                first = np.ones(ps.size, bool)
                first[1:] = ps[1:] != ps[:-1]
                sel = vi[order[first]]
                rgb = px_img[iys[order[first]], ixs[order[first]]].astype(np.float64)
                d2 = ((rgb[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
                slot = d2.argmin(axis=1) + 1
                slots = np.zeros(fc.shape[0], dtype=np.uint8)
                slots[sel] = slot.astype(np.uint8)
                p["face_slots"] = slots.tolist()
                out_item["slots_b64"] = base64.b64encode(slots.tobytes()).decode()
                out_item["painted"] = int((slots > 0).sum())
        out.append(out_item)
    return {"parts": out, "palette": pal_hex}


@router.get("/sessions/{sid}/export.3mf")
def export_3mf(sid: str):
    sess = meshpack.get_session(sid)
    if not sess["parts"]:
        raise HTTPException(409, "会话内没有模型件")
    data = threemf_out.write_project_3mf(sess["parts"], sess.get("palette_colors") or [])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="autocolor_project.3mf"'},
    )
