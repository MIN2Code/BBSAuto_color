"""件级取色管线（一期主链路）。

输入：每视角的渲染图（降采样原图）+ 件 ID 缓冲（前端 three.js 件 ID 编码渲染）。
输出：每件 连续 RGB 颜色 + 置信度 + 旗帜（人工复核提示）。

设计要点（docs/实施说明_0911.md）：
- L0 卫生层：过曝/欠曝/色域裁剪/白框边带像素不参与取色（专杀高光与水印边框）
- 每图白平衡归一：前景（模型像素）灰世界增益
- 每件×每视角：ID 掩码腐蚀 2px（杀抗锯齿渗色）→ Lab 截尾中位（杀 catchlight）
- 跨视角：件色 = 各视角件色的 Lab 逐通道中位；置信度 = 像素数因子 + 视角间一致性
- 件可见性由几何（ID 缓冲）决定，与视角顺序无关 → 前后视角互覆问题不存在
"""
import numpy as np


def hygiene_mask(img: np.ndarray) -> np.ndarray:
    """L0 卫生层：返回 bool 掩码，True=可信像素。
    过曝像素不剔除（白件/亮部大片 ≥250，剔除会让件色系统性偏暗）——
    后续钳制到 250 参与统计，孤立镜面亮峰由截尾中位排除。
    只剔欠曝死黑与边框带。"""
    h, w = img.shape[:2]
    m = np.ones((h, w), dtype=bool)
    m[img.min(axis=2) <= 4] = False            # 欠曝死黑
    bw = max(3, round(min(h, w) * 0.03))       # 白框/边框带（渲染图装饰边）
    m[:bw, :] = False
    m[-bw:, :] = False
    m[:, :bw] = False
    m[:, -bw:] = False
    return m


def white_balance(img: np.ndarray, fg: np.ndarray):
    """前景灰世界白平衡。返回 float32 校正图。"""
    f = img[fg].astype(np.float64)
    if f.shape[0] < 100:
        return img.astype(np.float32)
    means = f.mean(axis=0)
    gain = means.mean() / np.maximum(means, 1.0)
    gain = np.clip(gain, 0.6, 1.6)             # 防极端增益
    return np.clip(img.astype(np.float32) * gain.astype(np.float32), 0, 255)


def erode(mask: np.ndarray, it: int = 2) -> np.ndarray:
    m = mask.copy()
    for _ in range(it):
        p = np.pad(m, 1, constant_values=False)
        m = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m


def srgb8_to_lab(rgb: np.ndarray) -> np.ndarray:
    """uint8/float sRGB (0-255) → CIELAB (D65)。rgb: (...,3)。"""
    c = rgb.astype(np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    x = lin @ m.T                                  # XYZ = M · RGB（行向量约定）
    wp = np.array([0.95047, 1.0, 1.08883])
    t = x / wp
    f = np.where(t > 0.008856, np.cbrt(t), (7.787 * t + 16.0 / 116.0))
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_srgb8(lab) -> tuple:
    """Lab → (r, g, b) 0-255 int。"""
    L, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    def finv(fv):
        f3 = fv ** 3
        return np.where(f3 > 0.008856, f3, (fv - 16.0 / 116.0) / 7.787)
    y = finv(fy) * 1.0
    x = finv(fx) * 0.95047
    z = finv(fz) * 1.08883
    lin = np.array([
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ]) @ np.array([x, y, z])
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.clip(lin, 0, None) ** (1 / 2.4) - 0.055)
    return tuple(int(round(v * 255)) for v in np.clip(srgb, 0, 1))


def lab_hex(lab) -> str:
    return "#%02X%02X%02X" % lab_to_srgb8(lab)


def trimmed_median(lab_px: np.ndarray) -> np.ndarray:
    """截尾中位：先逐通道中位，再剔除距其最远 20% 像素后取中位（杀孤立亮/暗峰）。"""
    med = np.median(lab_px, axis=0)
    d = np.sqrt(((lab_px - med) ** 2).sum(axis=1))
    keep = d <= np.quantile(d, 0.8)
    return np.median(lab_px[keep], axis=0) if keep.any() else med


def weighted_median(labs: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """逐通道加权中位：权重=该视角的可信像素数（大可见区视角更可信）。"""
    order = np.argsort(labs, axis=0)
    out = np.zeros(labs.shape[1])
    half = weights.sum() / 2.0
    for ch in range(labs.shape[1]):
        cum = np.cumsum(weights[order[:, ch]])
        idx = int(np.searchsorted(cum, half))
        out[ch] = labs[order[idx, ch], ch]
    return out


MIN_PIX = 40          # 全部视角可信像素低于此 → 旗帜（不可瞎猜）
PIX_FULL = 400        # 像素数置信度满分阈值
DISAGREE_DE = 6.0     # 视角间件色 ΔE 标准差超此 → 旗帜


def recolor_session(sess: dict) -> list:
    """对会话执行件级取色。返回 per-part 结果列表（与 sess['parts'] 对齐）。"""
    images = sess.get("images") or []
    idbufs = sess.get("idbufs") or []
    parts = sess["parts"]
    n = len(parts)

    views = []                                    # (校正图float, ids int数组, 卫生掩码)
    for i in range(len(images)):
        idb = idbufs[i] if i < len(idbufs) else None
        if idb is None:
            continue
        img = images[i]
        if idb.shape[:2] != img.shape[:2]:
            from PIL import Image as PILImage
            idb = np.asarray(PILImage.fromarray(idb).resize(
                (img.shape[1], img.shape[0]), PILImage.NEAREST), dtype=np.uint8)
        fg = idb[..., 2] > 200                    # ID 编码 B=255 仅模型像素
        imgc = np.minimum(white_balance(img, fg), 250.0)   # 过曝钳制（亮部参与统计）
        ids = idb[..., 0].astype(np.int64) + idb[..., 1].astype(np.int64) * 256
        views.append((imgc, ids, hygiene_mask(img)))

    per_part = [[] for _ in range(n)]             # pid-1 → [(lab_med, npix)]
    for imgc, ids, hm in views:
        # 逐件掩码取色
        for pid in range(1, n + 1):
            m = (ids == pid)
            if not m.any():
                continue
            m &= hm
            if not m.any():
                continue
            m = erode(m, 2)
            if m.sum() < 8:
                continue
            lab = srgb8_to_lab(imgc[m])
            per_part[pid - 1].append((trimmed_median(lab), int(m.sum())))

    overrides = sess.get("overrides") or {}
    meds = [None] * n                             # 每件最佳估计 Lab（供同组回退）
    out = []
    for pi, pv in enumerate(per_part):
        p = parts[pi]
        item = {"index": pi, "name": p.get("name", f"part{pi}"),
                "hex": p.get("color") or "#8A939E", "conf": 0.0,
                "flagged": False, "reason": "", "pixels": 0, "views": 0,
                "override": False}
        if not pv:
            item.update({"hex": p.get("color") or "#8A939E", "flagged": True,
                         "reason": "no_visible_pixels", "pixels": 0})
        else:
            pv.sort(key=lambda x: (tuple(x[0]), x[1]))
            pix_total = sum(x[1] for x in pv)
            labs = np.stack([x[0] for x in pv])
            wts = np.array([x[1] for x in pv], dtype=np.float64)
            # 取亮视角：同灯转台图同件跨视角明暗差大（受光面/背光面），
            # 人眼认知的"件色"是受光面色——按 L 降序取前 60% 视角加权中位
            keep = max(2, int(np.ceil(len(pv) * 0.6)))
            bright = np.argsort(-labs[:, 0])[:keep]
            labs_k = labs[bright]
            wts_k = wts[bright]
            med = weighted_median(labs_k, wts_k)
            meds[pi] = med
            dE = np.sqrt(((labs - med) ** 2).sum(axis=1))
            pix_factor = min(1.0, pix_total / PIX_FULL)
            agree = max(0.0, 1.0 - float(dE.std()) / DISAGREE_DE)
            conf = round(0.6 * pix_factor + 0.4 * agree, 3)
            item.update({"hex": lab_hex(med), "pixels": pix_total,
                         "views": len(pv), "conf": conf})
            if pix_total < MIN_PIX:
                item.update({"flagged": True, "reason": "visible_too_few"})
            elif len(pv) >= 3 and float(dE.std()) > DISAGREE_DE:
                item.update({"flagged": True, "reason": "views_disagree"})
                if float(dE.std()) > DISAGREE_DE * 2.2:
                    # 跨视角严重不一致（典型：渲染图姿势≠STL装配姿势，如兜帽佩戴/
                    # 垂落状态差）——置信归零+旗帜交人工，但仍显示最佳估计色：
                    # 错误自信比无色有害，而"明显错误的大面积默认灰"更不可用
                    item.update({"reason": "views_disagree_hard", "conf": 0.0})
        if str(pi) in overrides:                  # override 持久层最后生效
            item.update({"hex": overrides[str(pi)], "override": True,
                         "flagged": False, "reason": ""})
        out.append(item)

    # 同组回退：无证据件按前缀/左右对称分组借用可信组员的最佳估计色
    # （作者切件 af1/af2、arm_l/arm_r 同色是强先验）。已有非默认灰旧色或
    # override 的件不动；严重分歧件（views_disagree_hard）不作供体。
    from .meshpack import _prefix
    donors: dict[str, tuple[list, list]] = {}
    for pi, item in enumerate(out):
        if meds[pi] is not None and item["reason"] != "views_disagree_hard":
            key = _prefix(parts[pi]["name"])
            labs, wts = donors.setdefault(key, ([], []))
            labs.append(meds[pi])
            wts.append(max(1, int(item["pixels"])))
    for pi, item in enumerate(out):
        if item["reason"] != "no_visible_pixels" or str(pi) in overrides:
            continue
        old = (parts[pi].get("color") or "").upper()
        if old and old != "#8A939E":              # 已有非默认灰旧色：保留
            continue
        group = donors.get(_prefix(parts[pi]["name"]))
        if not group:
            continue
        med = weighted_median(np.stack(group[0]), np.array(group[1], dtype=float))
        item.update({"hex": lab_hex(med), "fallback": True})
    return out
