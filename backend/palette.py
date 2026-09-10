"""渲染图吸色板：从作者渲染图提取主色（中位切分量化 + 占比过滤）。"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

MAX_SIDE = 1024  # 大图先降采样，加速量化


def _bg_mask(img: np.ndarray) -> np.ndarray:
    """估计渲染图背景（渐变纯色）：四角/边缘采样均值，返回前景掩码。"""
    h, w = img.shape[:2]
    corners = np.concatenate([
        img[: h // 20, : w // 20].reshape(-1, 3),
        img[: h // 20, -w // 20 :].reshape(-1, 3),
        img[-h // 20 :, : w // 20].reshape(-1, 3),
        img[-h // 20 :, -w // 20 :].reshape(-1, 3),
        img[:, :3].reshape(-1, 3), img[:, -3:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)
    dist = np.linalg.norm(img.reshape(-1, 3).astype(np.float64) - bg, axis=1)
    return (dist > 40.0).reshape(h, w)   # 阈值：与背景色距 >40 视为前景


def extract_palette(image_bytes: bytes, k: int = 16, min_ratio: float = 0.006) -> list[dict]:
    """返回 [{hex, ratio}]，按占比降序。自动剔除背景（渐变纯色）后再量化。"""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_SIDE:
        sc = MAX_SIDE / max(w, h)
        img = img.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.BILINEAR)
    arr = np.asarray(img)
    fg = _bg_mask(arr)
    if fg.mean() < 0.05:   # 背景估计失败（几乎全被剔）→ 放弃剔除
        fg = np.ones(arr.shape[:2], dtype=bool)
    px = arr[fg]
    # 只对前景像素做中位切分
    im2 = Image.fromarray(px.reshape(-1, 1, 3))
    q = im2.quantize(colors=max(2, min(k, 32)), method=Image.Quantize.MEDIANCUT)
    labels = np.asarray(q).ravel()
    counts = np.bincount(labels, minlength=len(q.getpalette()) // 3)
    pal = np.asarray(q.getpalette(), dtype=np.float64).reshape(-1, 3)
    total = float(counts.sum())
    px_f = px.astype(np.float64)
    v_arr = px_f.max(axis=1)   # 亮度（去阴影用）
    out = []
    for ci in np.argsort(-counts):
        c = counts[ci]
        if c <= 0:
            continue
        ratio = c / total * float(fg.mean())   # 折算回全图占比
        # 簇代表色：取簇内最亮 30% 像素的均值（去渲染阴影——
        # 同一材质的暗面是光影不是本色）
        mask = labels == ci
        if mask.sum() > 8:
            vv = v_arr[mask]
            bright = px_f[mask][vv >= np.percentile(vv, 70)]
            r, g, b = bright.mean(axis=0)
        else:
            r, g, b = pal[ci]
        # 高饱和小簇（蓝色绑带等细节色）适度放宽占比门槛
        import colorsys
        hh, ss, vv2 = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        thr = 0.003 if ss > 0.4 else min_ratio
        if ratio < thr:
            continue
        out.append({"hex": f"#{int(r):02X}{int(g):02X}{int(b):02X}", "ratio": round(ratio, 3)})
    return out
