"""SAM2 区域分割：分割用渲染原图（光影提供边界对比），取色用 albedo（本色）。

用法：python sam_segment.py <seg_input_png> <out_prefix> [color_input_png]
  seg_input_png   用于分割的图（渲染原图，边界清晰；缺省时用取色图分割）
  out_prefix      输出前缀
  color_input_png 用于取色的图（albedo 本色图；缺省时用分割图取色）
输出：
  <out_prefix>.labels.png   RGB 打包：R=id低8位 G=id高8位 B=255表示区域边界像素；id 0=背景
  <out_prefix>.regions.json [{"id":1,"hex":"#AABBCC","px":1234}, ...]

区域色取掩码内中位数（抗边缘混色）；AMG 小区域后写入（覆盖大区域）保细节；
接触图像边缘且颜色贴近背景估计的掩码判为背景（id 0，永不采样）。
"""
import json
import os
import sys

import numpy as np
from PIL import Image


def bg_color_of(arr: np.ndarray) -> np.ndarray:
    h, w = arr.shape[:2]
    corners = []
    for y0, x0 in ((0, 0), (0, w - 9), (h - 9, 0), (h - 9, w - 9)):
        corners.append(arr[y0:y0 + 9:4, x0:x0 + 9:4].reshape(-1, 3))
    c = np.concatenate(corners)
    med = np.median(c, axis=0)
    # 四角各取一个中值再取整体中值附近最暗代表（渲染背景通常均匀）
    return med


def main(inp: str, out_prefix: str, color_inp: str = None) -> int:
    import time
    import torch
    from sam2.build_sam import build_sam2
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

    t0 = time.time()
    img = Image.open(inp).convert("RGB")
    arr = np.asarray(img, dtype=np.uint8)
    oh, ow = arr.shape[:2]
    # SAM2 内部统一按 1024 长边推理，超出部分纯属后处理开销 → 先降到 1024
    if max(oh, ow) > 1024:
        sc = 1024 / max(oh, ow)
        arr = np.asarray(img.resize((round(ow * sc), round(oh * sc)), Image.BILINEAR),
                         dtype=np.uint8)
    h, w = arr.shape[:2]
    # 取色图（albedo）：与分割图对齐到同一分辨率
    if color_inp:
        cimg = Image.open(color_inp).convert("RGB").resize((w, h), Image.BILINEAR)
        carr = np.asarray(cimg, dtype=np.uint8)
    else:
        carr = arr

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ckpt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "third_party", "sam2", "checkpoints",
                        "sam2.1_hiera_base_plus.pt")
    # 自动分段一切模式；6GB 显存下 base_plus 稳妥
    sam = build_sam2("configs/sam2.1/sam2.1_hiera_b+.yaml", os.path.abspath(ckpt),
                     device=device)
    gen = SAM2AutomaticMaskGenerator(
        sam,
        points_per_side=48,
        points_per_batch=64,
        pred_iou_thresh=0.86,
        stability_score_thresh=0.90,
        min_mask_region_area=80,
        crop_n_layers=0,
    )
    print(f"[sam] model ready {time.time()-t0:.1f}s, device={device}", file=sys.stderr)
    with torch.inference_mode():
        masks = gen.generate(arr)
    print(f"[sam] {len(masks)} masks, generate {time.time()-t0:.1f}s", file=sys.stderr)

    bg = bg_color_of(carr).astype(np.float64)
    cflat = carr.reshape(-1, 3).astype(np.float64)

    # 面积降序：大区域先写入，小区域后写覆盖（细节优先）
    masks.sort(key=lambda m: -m["area"])
    ids = np.zeros((h, w), dtype=np.int64)
    regions = []
    nid = 0
    for m in masks:
        seg = m["segmentation"]
        if seg.all() or not seg.any():
            continue
        pix = cflat[seg.reshape(-1)]
        # 背景 mask 剔除：必须接触图像边缘（真背景一定贴边）且颜色贴近背景估计。
        # 只看颜色会误杀白裙/浅色主体——背景常与浅色衣物同色
        if np.median(np.abs(pix - bg).sum(axis=1)) < 90:
            edge_touch = bool(seg[0, :].any() or seg[-1, :].any()
                              or seg[:, 0].any() or seg[:, -1].any())
            if edge_touch:
                continue
        nid += 1
        ids[seg] = nid
        med = np.median(pix, axis=0).astype(int)
        regions.append({"id": nid, "hex": "#%02X%02X%02X" % tuple(med),
                        "px": int(m["area"])})
    if not regions:
        print("[sam] no foreground region found", file=sys.stderr)

    # 边界标记：4 邻域 id 不一致的内点（含与背景交界的外轮廓）
    b = np.zeros((h, w), dtype=bool)
    b[1:, :] |= ids[1:, :] != ids[:-1, :]
    b[:, 1:] |= ids[:, 1:] != ids[:, :-1]
    out = np.zeros((h, w, 3), dtype=np.uint8)
    out[..., 0] = (ids & 255).astype(np.uint8)
    out[..., 1] = (ids >> 8).astype(np.uint8)
    out[..., 2] = b.astype(np.uint8) * 255
    Image.fromarray(out, "RGB").save(out_prefix + ".labels.png")
    with open(out_prefix + ".regions.json", "w", encoding="utf-8") as f:
        json.dump(regions, f, ensure_ascii=False)
    print(f"[sam] {len(regions)} regions kept", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None))
