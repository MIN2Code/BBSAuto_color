"""Intrinsic 本征分解：渲染图 → albedo（无光影本色图）。

用法：thermal-assess venv 的 python intrinsic_decompose.py <输入图> <输出albedo.png>
权重自动下载（v2.1，GitHub releases），优先 CUDA。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'third_party', 'Intrinsic'))

import numpy as np
from PIL import Image

import numpy as np
from PIL import Image as PILImage

from chrislib.general import view, invert
from intrinsic.pipeline import load_models, run_pipeline


def decompose(image_path: str, out_albedo: str, device: str = None) -> str:
    if device is None:
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'device = {device}，加载 v2.1 权重…')
    models = load_models('v2.1', device=device)
    image = np.asarray(PILImage.open(image_path).convert('RGB')).astype(np.single) / 255.0
    print('推理中…')
    results = run_pipeline(models, image, device=device)
    alb = view(results['hr_alb'])          # gamma 校正后的本色图 [0,1]
    alb8 = (np.clip(alb, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(alb8).save(out_albedo)
    print(f'albedo 已保存: {out_albedo}')
    return out_albedo


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else 'input.png'
    dst = sys.argv[2] if len(sys.argv) > 2 else 'albedo.png'
    decompose(src, dst)
