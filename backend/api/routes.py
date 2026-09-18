"""AutoColor API：多 STL 组合、渲染图吸色、件级配色、涂色 3MF 导出。"""
from __future__ import annotations

import json
import threading

import numpy as np
import io

import os

import numpy as np
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import meshpack, palette as palette_mod, threemf_out
from .. import colorize

router = APIRouter(prefix="/api")

# 本征分解推理环境（thermal-assess venv 的 torch；缺失时回退原图采样）
INFER_PYTHON = os.path.join("J:/claudebox/thermal-assess/.venv/Scripts", "python.exe")
INFER_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts", "intrinsic_decompose.py")
SAM_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "scripts", "sam_segment.py")


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


def _sam_worker(sess, seg_px, color_px, idx):
    """后台线程：SAM2 区域分割（分割=渲染图边界清晰，取色=albedo 无光影），
    完成后写回 session；失败保持 None，前端投影自动回退逐面采样。"""
    import subprocess as _sp
    import tempfile as _tf
    from PIL import Image as PILImage
    t_seg = t_col = None
    pre = None
    try:
        t_seg = _tf.NamedTemporaryFile(suffix=".seg.png", delete=False)
        PILImage.fromarray(seg_px).save(t_seg.name)
        t_seg.close()
        t_col = _tf.NamedTemporaryFile(suffix=".col.png", delete=False)
        PILImage.fromarray(color_px).save(t_col.name)
        t_col.close()
        pre = t_col.name + ".sam"
        _sp.run([INFER_PYTHON, SAM_SCRIPT, t_seg.name, pre, t_col.name],
                check=True, timeout=900, capture_output=True)
        rpx = np.asarray(PILImage.open(pre + ".labels.png").convert("RGB"), dtype=np.uint8)
        if rpx.shape[:2] != seg_px.shape[:2]:
            # 分割输出尺寸与渲染图不一致 → NEAREST 对齐回渲染图坐标系（保 id）
            rpx = np.asarray(PILImage.fromarray(rpx).resize(
                (seg_px.shape[1], seg_px.shape[0]), PILImage.NEAREST), dtype=np.uint8)
        with open(pre + ".regions.json", encoding="utf-8") as f:
            regions = json.load(f)
        sess["regionmaps"][idx] = rpx
        sess["region_data"][idx] = regions
    except Exception:
        pass
    finally:
        for _p in (t_seg and t_seg.name, t_col and t_col.name,
                   pre and pre + ".labels.png", pre and pre + ".regions.json"):
            try:
                if _p and os.path.exists(_p):
                    os.remove(_p)
            except OSError:
                pass


@router.post("/sessions/{sid}/image")
async def upload_image(sid: str, file: UploadFile = File(...)):
    data = await file.read()
    try:
        pal = palette_mod.extract_palette(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"图片解析失败：{exc}") from exc
    sess = meshpack.get_session(sid)
    # 缓存降采样像素（多图列表：投影按 image_index 取用）
    import numpy as np
    from PIL import Image as PILImage
    im = PILImage.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if max(w, h) > 1024:
        sc = 1024 / max(w, h)
        im = im.resize((int(w * sc), int(h * sc)), PILImage.BILINEAR)
    px = np.asarray(im, dtype=np.uint8)
    sess.setdefault("images", []).append(px)
    sess["image_pixels"] = px                      # 兼容：默认指向最后一张
    sess["image_size"] = [im.size[0], im.size[1]]
    sess.setdefault("image_names", []).append(file.filename or f"img{len(sess['images'])}")

    # 本征分解（去光影取本色）：thermal-assess venv 的 torch 推理，不可用时回退原图
    import os as _os
    import subprocess as _sp
    import tempfile as _tf
    albedo_px = None
    if _os.path.exists(INFER_PYTHON) and _os.path.exists(INFER_SCRIPT):
        tmp_in = _tf.NamedTemporaryFile(suffix=".png", delete=False)
        PILImage.fromarray(px).save(tmp_in.name)
        tmp_in.close()
        tmp_out = tmp_in.name + ".albedo.png"
        try:
            _sp.run([INFER_PYTHON, INFER_SCRIPT, tmp_in.name, tmp_out],
                    check=True, timeout=300, capture_output=True)
            albedo_px = np.asarray(PILImage.open(tmp_out).convert("RGB"), dtype=np.uint8)
        except Exception:                          # 分解失败回退原图
            albedo_px = None
        finally:
            try:
                _os.remove(tmp_in.name)
                if _os.path.exists(tmp_out):
                    _os.remove(tmp_out)
            except OSError:
                pass
    sess.setdefault("albedos", []).append(albedo_px if albedo_px is not None else px)
    sess["albedo_has_real"] = sess.get("albedo_has_real", [])
    sess["albedo_has_real"].append(albedo_px is not None)

    # SAM2 区域分割：后台异步跑（GPU 被桌面程序共享时耗时波动大，不可阻塞上传）。
    # 完成前 regionmaps[i] 为 None → 前端投影自动回退逐面采样
    sess.setdefault("regionmaps", []).append(None)
    sess.setdefault("region_data", []).append(None)
    seg_idx = len(sess["images"]) - 1
    seg_src = px.copy()                                # 分割用渲染图（光影=边界对比）
    color_src = (albedo_px if albedo_px is not None else px).copy()
    threading.Thread(target=_sam_worker, args=(sess, seg_src, color_src, seg_idx),
                     daemon=True).start()

    # 色板从 albedo（本色图）提取——比原图更准（无光影污染）
    albedos = sess.get("albedos") or []
    pal_src = albedos[-1] if albedos else None
    if pal_src is not None:
        from PIL import Image as PILImage
        b = io.BytesIO()
        PILImage.fromarray(pal_src).save(b, "PNG")
        pal = palette_mod.extract_palette(b.getvalue())
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
    # 不重排（保持槽位稳定——已涂面的 paint_color 槽号绑定 palette 顺序）；
    # 首次建板按占比排序，后续新色 append 尾部
    if not any(c["ratio"] for c in merged):
        merged.sort(key=lambda x: -x["ratio"])
    sess["palette"] = merged
    sess["palette_colors"] = [c["hex"] for c in merged]
    sess["image_name"] = (sess["image_name"] + " + " if sess["image_name"] else "") + (file.filename or "render.png")
    img_count = len(sess.get("images") or [])
    return {"palette": merged, "image_name": sess["image_name"],
            "image_index": img_count - 1 if img_count else None,
            "region_pending": True}


@router.get("/sessions/{sid}")
def session_summary(sid: str):
    s = meshpack.session_summary(sid)
    sess = meshpack.get_session(sid)
    s["has_image"] = sess.get("image_pixels") is not None
    s["image_count"] = len(sess.get("images") or [])
    return s


@router.get("/sessions/{sid}/image.png")
def session_image(sid: str, i: int = -1):
    """恢复渲染图（?i= 选择第几张；默认最后一张）。"""
    sess = meshpack.get_session(sid)
    images = sess.get("images") or ([sess["image_pixels"]] if sess.get("image_pixels") else [])
    if not images:
        raise HTTPException(404, "无渲染图")
    px = images[ii] if 0 <= (ii := i if i >= 0 else len(images) - 1) < len(images) else images[-1]
    from PIL import Image
    im = Image.fromarray(px)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/sessions/{sid}/regionmap.png")
def session_regionmap(sid: str, i: int = -1):
    """SAM2 区域标签图（R=id低8位 G=id高8位 B=255边界；id 0=背景）。"""
    sess = meshpack.get_session(sid)
    maps = sess.get("regionmaps") or []
    ii = i if i >= 0 else len(maps) - 1
    if not maps or not (0 <= ii < len(maps)) or maps[ii] is None:
        raise HTTPException(404, "该图无区域分割结果")
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(maps[ii]).save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/sessions/{sid}/regions")
def session_regions(sid: str, i: int = -1):
    """区域中位色列表（与 regionmap.png 的 id 对应；分割未完成时 404）。"""
    sess = meshpack.get_session(sid)
    data = sess.get("region_data") or []
    ii = i if i >= 0 else len(data) - 1
    if not data or not (0 <= ii < len(data)) or data[ii] is None:
        raise HTTPException(404, "无区域数据")
    return {"image_index": ii, "regions": data[ii]}


@router.post("/sessions/{sid}/idbuf")
async def upload_idbuf(sid: str, i: int = -1, file: UploadFile = File(...)):
    """上传某视角的件 ID 缓冲 PNG（R=partIndex低8位 G=高8位 B=255 仅模型像素）。"""
    import numpy as np
    from PIL import Image as PILImage
    data = await file.read()
    sess = meshpack.get_session(sid)
    idb = np.asarray(PILImage.open(io.BytesIO(data)).convert("RGB"), dtype=np.uint8)
    sess.setdefault("idbufs", [])
    idx = i if i >= 0 else len(sess["images"]) - 1
    while len(sess["idbufs"]) < idx + 1:
        sess["idbufs"].append(None)
    sess["idbufs"][idx] = idb
    return {"ok": True, "index": idx, "size": [idb.shape[1], idb.shape[0]]}


@router.get("/sessions/{sid}/idbuf.png")
def session_idbuf(sid: str, i: int = -1):
    """诊断：查看某视角的件 ID 缓冲（对齐调试用）。"""
    sess = meshpack.get_session(sid)
    idbufs = sess.get("idbufs") or []
    ii = i if i >= 0 else len(idbufs) - 1
    if not idbufs or not (0 <= ii < len(idbufs)) or idbufs[ii] is None:
        raise HTTPException(404, "无 ID 缓冲")
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(idbufs[ii]).save(buf, "PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.post("/sessions/{sid}/autocolor")
def autocolor(sid: str):
    """一期件级取色：L0 卫生层 + 白平衡 + 腐蚀 + 截尾中位 + 跨视角中位 + 置信度/旗帜。"""
    sess = meshpack.get_session(sid)
    if not sess.get("idbufs") or all(b is None for b in sess["idbufs"]):
        raise HTTPException(409, "无件 ID 缓冲（先在前端完成转台拟合与采集）")
    results = colorize.recolor_session(sess)
    # 取色结果写回件色（override 已在 colorize 内最后生效）；
    # 件级架构下面级色（face_slots，二期子件层）整体作废
    for item in results:
        pi = item["index"]
        if 0 <= pi < len(sess["parts"]) and item.get("hex"):
            meshpack.set_color(sid, pi, item["hex"])
            sess["parts"][pi]["conf"] = item.get("conf")
            sess["parts"][pi]["flagged"] = item.get("flagged", False)
            sess["parts"][pi]["reason"] = item.get("reason", "")
            sess["parts"][pi]["face_slots"] = None
    return {"parts": results}


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
    # 人工改色 = override 持久层：重跑自动取色不冲掉
    sess = meshpack.get_session(sid)
    sess.setdefault("overrides", {})[str(part_index)] = color
    sess["phase2_revision"] = sess.get("phase2_revision", 0) + 1   # base 色变 → 预览过期
    return {"ok": True, "color": sess["parts"][part_index]["color"]}


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
    images = sess.get("images") or ([sess["image_pixels"]] if sess.get("image_pixels") else [])
    albedos = sess.get("albedos") or []
    ii = int(body.get("image_index", len(images) - 1))
    if not images or not (0 <= ii < len(images)):
        raise HTTPException(409, "先上传渲染图")
    alb = albedos[ii] if ii < len(albedos) else None
    px_img = alb if alb is not None else images[ii]   # 采样本色图（albedo）
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
    data = threemf_out.write_project_3mf(
        sess["parts"], sess.get("palette_colors") or [],
        face_slot_colors=sess.get("phase2_palette") or [])
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="autocolor_project.3mf"'},
    )


# ------------------------------------------------------------ 二期：件内候选区域

def _phase2_region_json(region, part_index: int) -> dict:
    return {"part_index": part_index, "region_id": region.region_id,
            "faces": sorted(int(f) for f in region.faces),
            "color_hex": region.color_hex, "support_views": region.support_views,
            "confidence": region.confidence, "accepted": region.accepted,
            "reason": region.reason, "slot": region.slot}


_FACE_SLOT_MAX = 255      # face_slots 为 uint8 槽编码；Bambu paint_color 更只认前 16 槽


def _phase2_slot_of(sess: dict, hex_color: str) -> int:
    """区域色/人工色入二期色表（1-based 槽号，与 face_slots 编码一致）。

    槽表满 255 后新色吸附到 Lab 距离最近的既有槽（uint8 硬上限；实际可打印
    数还受切片器耗材槽数约束，超出的槽由用户在切片软件里取舍）。
    """
    pal = sess.setdefault("phase2_palette", [])
    h = (hex_color or "").upper()
    if not h.startswith("#") or len(h) != 7:
        return 0
    if h in pal:
        return pal.index(h) + 1
    if len(pal) < _FACE_SLOT_MAX:
        pal.append(h)
        return len(pal)
    target = colorize.srgb8_to_lab(_hex_rgb(h))[0]
    labs = colorize.srgb8_to_lab(np.array(
        [[int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)] for c in pal],
        dtype=np.uint8))
    nearest = int(np.argmin(np.linalg.norm(labs - target, axis=1)))
    return nearest + 1


def _hex_rgb(hex_color: str) -> np.ndarray:
    h = (hex_color or "#FFFFFF").upper()
    return np.array([[int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)]], dtype=np.uint8)


@router.post("/sessions/{sid}/phase2/collect")
def phase2_collect(sid: str, body: dict = None):
    """同步候选收集与决策：只发现与裁决，不写任何 face_slots（先收集后决策）。"""
    from ..face_candidates import (collect_face_candidates, decide_regions,
                                   discover_regions)
    sess = meshpack.get_session(sid)
    images = sess.get("images") or []
    idbufs = sess.get("idbufs") or []
    cameras = ((body or {}).get("cameras") or [])
    if not images or all(b is None for b in idbufs) or len(cameras) != len(images):
        raise HTTPException(409, "二期候选收集需要渲染图、ID 缓冲与每视角相机参数（先完成转台拟合采集）")

    sess["phase2_status"] = "collecting"
    sess["phase2_cameras"] = cameras       # 诊断留存：最近一次收集使用的相机
    try:
        views = []
        for i in range(len(images)):
            idb = idbufs[i] if i < len(idbufs) else None
            if idb is None or cameras[i] is None:
                continue
            img = images[i]
            if idb.shape[:2] != img.shape[:2]:
                from PIL import Image as PILImage
                idb = np.asarray(PILImage.fromarray(idb).resize(
                    (img.shape[1], img.shape[0]), PILImage.NEAREST), dtype=np.uint8)
            # 相机 w/h 对齐渲染图尺寸：idbuf 按图像纵横比渲染，cover 退化为纯缩放
            cam = dict(cameras[i])
            cam["w"], cam["h"] = int(img.shape[1]), int(img.shape[0])
            views.append({"camera": cam, "image": img, "id_buffer": idb, "depth": None})

        all_regions = []
        _dbg = {"views": len(views)}
        for pi, part in enumerate(sess["parts"]):
            evidence = collect_face_candidates({**part, "index": pi}, views,
                                               part.get("color") or "#FFFFFF")
            base_lab = tuple(float(v) for v in colorize.srgb8_to_lab(
                _hex_rgb(part.get("color") or "#FFFFFF"))[0])
            regions = decide_regions(discover_regions(part, evidence, base_lab))
            if pi == 0:
                hits = sum(len(v) for v in evidence.values())
                des = [np.linalg.norm(np.array(next(iter(v)).lab) - base_lab)
                       for v in evidence.values() if v]
                _dbg.update(part0_hits=hits,
                            part0_ge10=int(sum(1 for d in des if d >= 10)) if des else 0,
                            part0_regions=len(regions),
                            part0_color=part.get("color"))
            all_regions.extend(_phase2_region_json(r, pi) for r in regions)
        sess["phase2_regions"] = all_regions
        sess["phase2_status"] = "ready"
        sess["phase2_revision"] = sess.get("phase2_revision", 0) + 1
        sess["phase2_debug"] = _dbg
        sess.pop("phase2_error", None)
    except HTTPException:
        sess["phase2_status"] = "failed"
        raise
    except Exception as exc:                     # 件级颜色与人工 override 不动
        sess["phase2_status"] = "failed"
        sess["phase2_error"] = str(exc)
        raise HTTPException(500, f"候选收集失败：{exc}")

    accepted = sum(1 for r in all_regions if r["accepted"])
    return {"status": "ready", "parts": len(sess["parts"]),
            "regions": len(all_regions), "accepted": accepted}


@router.get("/sessions/{sid}/phase2")
def phase2_get(sid: str):
    sess = meshpack.get_session(sid)
    regions = sess.get("phase2_regions") or []
    accepted = sum(1 for r in regions if r.get("accepted"))
    return {"status": sess.get("phase2_status", "idle"),
            "revision": sess.get("phase2_revision", 0),
            "error": sess.get("phase2_error"),
            "debug": sess.get("phase2_debug"),
            "palette": sess.get("phase2_palette") or [],
            "parts": [{"index": pi, "name": p.get("name"), "color": p.get("color"),
                       "flagged": p.get("flagged", False),
                       "regions": [r for r in regions if r.get("part_index") == pi]}
                      for pi, p in enumerate(sess["parts"])],
            "summary": {"regions": len(regions), "accepted": accepted,
                        "review": len(regions) - accepted}}


@router.post("/sessions/{sid}/phase2/apply")
def phase2_apply(sid: str):
    """仅应用 accepted 区域与人工 face override → 写 face_slots（件级底色不动）。"""
    from ..face_candidates import CandidateRegion, apply_face_layers
    sess = meshpack.get_session(sid)
    if sess.get("phase2_status") != "ready":
        raise HTTPException(409, "先运行候选收集（POST /phase2/collect）")
    regions = sess.get("phase2_regions") or []
    by_part = {}
    for r in regions:
        if r.get("accepted"):
            r["slot"] = _phase2_slot_of(sess, r.get("color_hex", ""))
            by_part.setdefault(r["part_index"], []).append(
                CandidateRegion(region_id=r["region_id"],
                                faces=frozenset(r["faces"]),
                                color_hex=r["color_hex"],
                                support_views=r["support_views"],
                                confidence=r["confidence"],
                                accepted=True, reason=r["reason"], slot=r["slot"]))
    painted = 0
    for pi, part in enumerate(sess["parts"]):
        overrides = (sess.get("face_overrides") or {}).get(str(pi)) or {}
        override_slots = {int(f): _phase2_slot_of(sess, h) for f, h in overrides.items()}
        if pi not in by_part and not override_slots:
            continue
        base = np.zeros(len(part["faces"]), dtype=np.uint8)
        slots = apply_face_layers(base, by_part.get(pi, []), override_slots)
        part["face_slots"] = slots.tolist()
        painted += 1
    sess["phase2_status"] = "ready"
    return {"ok": True, "painted_parts": painted,
            "palette": sess.get("phase2_palette") or []}


@router.delete("/sessions/{sid}/phase2/auto")
def phase2_clear(sid: str):
    """清自动候选与面级色，保留人工 override（重跑自动不冲掉手改）。"""
    sess = meshpack.get_session(sid)
    sess["phase2_regions"] = []
    sess["phase2_status"] = "idle"
    sess["phase2_revision"] = sess.get("phase2_revision", 0) + 1
    sess["phase2_palette"] = []
    sess.pop("phase2_error", None)
    for part in sess["parts"]:
        part["face_slots"] = None
    return {"ok": True}


@router.get("/sessions/{sid}/phase2/preview/{part_index}")
def phase2_preview(sid: str, part_index: int):
    """连续 RGB 面级预览：每面 3 字节（face_count*3），完整视觉预览专用。

    合成序 = 件级 base → 已接受区域（稳定优先级先写者赢）→ 人工 face
    override。不写 face_slots、不受耗材槽限制；idle 时返回纯 base 色。
    """
    from ..face_candidates import CandidateRegion, compose_face_rgb
    sess = meshpack.get_session(sid)
    if not (0 <= part_index < len(sess["parts"])):
        raise HTTPException(404, "part not found")
    part = sess["parts"][part_index]
    accepted = []
    for r in sess.get("phase2_regions") or []:
        if r.get("part_index") != part_index or not r.get("accepted"):
            continue
        accepted.append(CandidateRegion(
            region_id=int(r.get("region_id", 0)),
            faces=frozenset(int(f) for f in r.get("faces") or []),
            color_hex=r.get("color_hex", ""),
            support_views=int(r.get("support_views", 0)),
            confidence=float(r.get("confidence", 0.0)),
            accepted=True, reason=r.get("reason", "")))
    overrides = {int(f): h for f, h in
                 ((sess.get("face_overrides") or {}).get(str(part_index)) or {}).items()}
    rgb = compose_face_rgb(len(part["faces"]),
                           part.get("color") or "#FFFFFF", accepted, overrides)
    return Response(
        content=rgb.tobytes(),
        media_type="application/octet-stream",
        headers={"X-Face-Count": str(len(part["faces"])),
                 "X-Phase2-Revision": str(sess.get("phase2_revision", 0))})
