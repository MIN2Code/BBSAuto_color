"""会话状态与多 STL 组合读取。

作者模型包：多个分件 STL（导出时保持世界坐标，合并即装配）+ 渲染图。
会话保存在内存（自用工具），记录每件的几何、名称与当前颜色分配。
"""
from __future__ import annotations

import io
import secrets
import threading

import numpy as np
import trimesh

_LOCK = threading.Lock()
SESSIONS: dict[str, dict] = {}


def new_session() -> str:
    sid = secrets.token_hex(6)
    with _LOCK:
        SESSIONS[sid] = {"parts": [], "palette": [], "palette_colors": [],
                         "up_axis": "y",  # STL 无坐标系元数据，合并 bbox 最长轴自动检测
                         "image_name": "", "image_pixels": None, "image_size": None,
                         "images": [],   # 多渲染图像素缓存（投影按 index 取用）
                         "created": __import__("time").time()}
        # 会话数控制（自用）
        if len(SESSIONS) > 8:
            oldest = min(SESSIONS, key=lambda k: SESSIONS[k]["created"])
            if oldest != sid:
                del SESSIONS[oldest]
    return sid


def get_session(sid: str) -> dict:
    with _LOCK:
        if sid not in SESSIONS:
            raise KeyError(f"session not found: {sid}")
        return SESSIONS[sid]


def add_part(sid: str, name: str, data: bytes) -> dict:
    """读取单个 STL 并入会话（保持世界坐标）。返回件摘要。"""
    mesh = trimesh.load_mesh(io.BytesIO(data), file_type="stl", process=False)
    v = np.asarray(mesh.vertices, dtype=np.float32)
    f = np.asarray(mesh.faces, dtype=np.int32)
    if v.size == 0 or f.size == 0:
        raise ValueError(f"{name}: 空网格")
    sess = get_session(sid)
    part = {
        "name": name,
        "vertices": v,
        "faces": f,
        "color": "",       # "#RRGGBB"，空=未分配
        "face_slots": None,  # 件内投影上色：每面 1-based 槽号列表（None=全件色）
    }
    sess["parts"].append(part)
    return part_summary(part, len(sess["parts"]) - 1)


def part_summary(part: dict, idx: int) -> dict:
    v = part["vertices"]
    f = part["faces"]
    tri_v = v[f]  # (T,3,3)
    area = float(np.sum(np.linalg.norm(np.cross(tri_v[:, 1] - tri_v[:, 0], tri_v[:, 2] - tri_v[:, 0]), axis=1)) / 2)
    return {
        "index": idx,
        "name": part["name"],
        "color": part["color"],
        "tris": int(f.shape[0]),
        "area_cm2": round(area / 100.0, 1),
        "bbox": [np.min(v, axis=0).tolist(), np.max(v, axis=0).tolist()],
    }


def detect_up_axis(sess: dict) -> str:
    """合并 bbox 最长轴 = 高度轴（手办通常高度最大；UI 可手动覆盖）。"""
    if not sess["parts"]:
        return sess.get("up_axis", "y")
    mn = np.full(3, np.inf)
    mx = np.full(3, -np.inf)
    for p in sess["parts"]:
        v = p["vertices"]
        mn = np.minimum(mn, v.min(axis=0))
        mx = np.maximum(mx, v.max(axis=0))
    return ["x", "y", "z"][int(np.argmax(mx - mn))]


def session_summary(sid: str) -> dict:
    sess = get_session(sid)
    return {
        "session_id": sid,
        "image_name": sess["image_name"],
        "palette": sess["palette"],
        "up_axis": sess.get("up_axis", "y"),
        "parts": [part_summary(p, i) for i, p in enumerate(sess["parts"])],
    }


def set_up_axis(sid: str, axis: str) -> str:
    if axis not in ("x", "y", "z"):
        raise ValueError("axis 必须是 x/y/z")
    sess = get_session(sid)
    sess["up_axis"] = axis
    return axis


def set_color(sid: str, part_index: int, color: str) -> None:
    sess = get_session(sid)
    if not (0 <= part_index < len(sess["parts"])):
        raise IndexError("part index out of range")
    color = color.strip().lstrip("#").upper()
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    if len(color) != 6 or any(c not in "0123456789ABCDEF" for c in color):
        raise ValueError(f"bad color: #{color}")
    sess["parts"][part_index]["color"] = f"#{color}"


def _prefix(name: str) -> str:
    """语义前缀：af1-hhbM.stl→af，b21-x.stl→b，leg_l1-x.stl→leg。

    规则：去路径/hash 后缀/扩展名 → 去尾部数字 → 去左右后缀 _l/_r
    （arm_l/arm_r 同为 arm）。编号件（af1/af2/…）是同部件切件，同前缀=同色。
    """
    import re
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    base = base.split("-")[0]
    base = re.sub(r"\.stl$", "", base, flags=re.I)
    base = re.sub(r"\d+$", "", base)
    return re.sub(r"_(?:l|r)$", "", base, flags=re.I)


def _hsv(hexc: str):
    import colorsys
    h = hexc.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return colorsys.rgb_to_hsv(r, g, b)


_SEM = (
    # (关键词元组, (hue 范围, s 范围, v 范围), 排序键)
    (("hood", "cape", "cloak"), ((0.92, 0.03), (0.45, 1.0), (0.35, 0.95)),
     lambda h, s, v: s),                                                            # 红斗篷系
    (("hair",), ((0.02, 0.20), (0.18, 1.0), (0.45, 1.0)), lambda h, s, v: v * s),   # 发色：饱和暖（金/棕）
    (("gem", "crystal", "jewel"), (None, None, (0.8, 1.0)), lambda h, s, v: v),      # 宝石高亮
    (("boot", "shoe", "belt", "glove", "sock", "sole", "base", "rock", "ground", "stand", "stone"),
     (None, None, (0.0, 0.45)), lambda h, s, v: -v),                                # 暗色件/底座
    (("skirt", "dress"), (None, None, (0.7, 1.0)), lambda h, s, v: -s),              # 裙装偏亮
    (("head", "arm", "leg", "hand", "body", "face", "neck", "torso", "skin", "hip", "thigh", "calf"),
     ((0.01, 0.14), (0.15, 0.75), (0.5, 1.0)), lambda h, s, v: v - s * 0.5),        # 肤色：中饱和暖（排除暖白）
)


def _pick_semantic(prefix: str, palette: list[dict]) -> str | None:
    """件名语义 → 在色板里挑最合适的颜色（无匹配返回 None 走循环分配）。"""
    import re as _re
    p = _re.sub(r"\d+$", "", _prefix(prefix))
    for keys, (hr, sr, vr), key in _SEM:
        if not any(k in p for k in keys):
            continue
        best, best_score = None, -1e9
        for c in palette:
            h, sv, v = _hsv(c["hex"])
            if hr and not ((hr[0] <= h <= hr[1]) if hr[0] < hr[1]
                           else (h >= hr[0] or h <= hr[1])):
                continue
            if sr and not (sr[0] <= sv <= sr[1]):
                continue
            if vr and not (vr[0] <= v <= vr[1]):
                continue
            score = key(h, sv, v)
            if score > best_score:
                best, best_score = c["hex"], score
        return best
    return None


def auto_assign(sid: str) -> int:
    """自动配色：件按语义前缀分组（同前缀=同色），组按总面积降序 ↔
    色板按占比降序一一对应。返回配色件数。"""
    import re
    sess = get_session(sid)
    if not sess["palette"]:
        raise RuntimeError("先上传渲染图提取色板")
    groups: dict[str, list[int]] = {}
    for i, p in enumerate(sess["parts"]):
        groups.setdefault(_prefix(p["name"]), []).append(i)
    garea = []
    for pref, idxs in groups.items():
        a = sum(part_summary(sess["parts"][i], i)["area_cm2"] for i in idxs)
        garea.append((pref, a))
    garea.sort(key=lambda x: -x[1])
    n = len(garea)
    pal = sess["palette"]
    # 两阶段：①语义组（件名可判断色的，如 hair/gem/boot）按语义挑色；
    # ②其余组按面积降序循环分配色板（保证每件都有色，前端可微调）
    used_hues: set = set()
    for pref, idxs in groups.items():
        hexc = _pick_semantic(pref, pal)
        if hexc:
            for i in idxs:
                sess["parts"][i]["color"] = hexc
            used_hues.add(pref)
    rank = 0
    for pref, _ in garea:
        if pref in used_hues:
            continue
        hexc = pal[rank % len(pal)]["hex"]
        for i in groups[pref]:
            sess["parts"][i]["color"] = hexc
        rank += 1
    return sum(len(v) for v in groups.values())
