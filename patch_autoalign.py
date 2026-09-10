"""给 AutoColor 补丁：自动对齐（渲染图前景剪影 ↔ 模型剪影 IoU 扫描）。"""
import io

# ---------- viewer.js：renderSilhouetteAt ----------
p = 'frontend/src/viewer.js'
s = open(p, encoding='utf-8').read()
if 'renderSilhouetteAt' not in s:
    anchor = "  /** 渲染深度缓冲（RGBADepthPacking 解码为 [0,1] NDC 深度；背景≈0.996）。 */"
    add = """  /** 以指定方位角/距离渲染模型剪影（独立临时相机），返回 {mask, ratio}。 */
  renderSilhouetteAt(azDeg, dist, height, target, w = 64, h = 64) {
    const cam = new THREE.PerspectiveCamera(45, w / h, 1, 5000);
    const rad = azDeg * Math.PI / 180;
    cam.position.set(target.x + Math.sin(rad) * dist, target.y + height, target.z + Math.cos(rad) * dist);
    cam.lookAt(target.x, target.y, target.z);
    cam.updateMatrixWorld();
    const rt = new THREE.WebGLRenderTarget(w, h);
    const prevRT = this.renderer.getRenderTarget();
    const prevBg = this.scene.background;
    const prevOv = this.scene.overrideMaterial;
    this.scene.background = null;
    this.scene.overrideMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff });
    this.renderer.setRenderTarget(rt);
    this.renderer.setClearColor(0x000000, 1);
    this.renderer.clear();
    this.renderer.render(this.scene, cam);
    const px = new Uint8Array(w * h * 4);
    this.renderer.readRenderTargetPixels(rt, 0, 0, w, h, px);
    this.renderer.setRenderTarget(prevRT);
    this.scene.overrideMaterial = prevOv;
    this.scene.background = prevBg;
    rt.dispose();
    const mask = new Uint8Array(w * h);
    let on = 0;
    for (let i = 0; i < w * h; i++) {
      const row = h - 1 - Math.floor(i / w);
      const col = i % w;
      if (px[i * 4] > 32 || px[i * 4 + 1] > 32 || px[i * 4 + 2] > 32) { mask[row * w + col] = 1; on++; }
    }
    return { mask, ratio: on / (w * h), w, h };
  }

  /** 渲染深度缓冲（RGBADepthPacking 解码为 [0,1] NDC 深度；背景≈0.996）。 */"""
    assert anchor in s, 'viewer anchor missing'
    s = s.replace(anchor, add, 1)
open(p, 'w', encoding='utf-8').write(s)

# ---------- app.js：computeForegroundMask + autoAlignCamera + 接线 ----------
p = 'frontend/src/app.js'
s = open(p, encoding='utf-8').read()

# state 扩展 imageIndex
if 'imageIndex' not in s:
    s = s.replace("const state = { sid: null, session: null, sel: -1, faceSlots: {}, imageUrl: null, align: false };",
                  "const state = { sid: null, session: null, sel: -1, faceSlots: {}, imageUrl: null, imageIndex: null, align: false };")

# 暴露
if '__autoAlign' not in s:
    s = s.replace("window.__state = state;     // 同上：会话状态",
                  "window.__state = state;     // 同上：会话状态\nwindow.__autoAlign = autoAlignCamera;")

# 实现插入（投影按钮前）
anchor = "$('project').addEventListener('click'"
if 'autoAlignCamera' not in s:
    add = """// ------------------------------------------------------------ 自动对齐
async function computeForegroundMask(imageIndex, W = 64, H = 64) {
  const img = new Image();
  img.src = `/api/sessions/${state.sid}/image.png?i=${imageIndex}`;
  await img.decode();
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  const sc = Math.max(W / img.naturalWidth, H / img.naturalHeight);
  const dw = img.naturalWidth * sc, dh = img.naturalHeight * sc;
  ctx.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
  const d = ctx.getImageData(0, 0, W, H).data;
  const corners = [];
  for (const [y0, x0] of [[0, 0], [0, W - 4], [H - 4, 0], [H - 4, W - 4]]) {
    for (let y = y0; y < y0 + 4; y += 2) for (let x = x0; x < x0 + 4; x += 2) {
      const o = (y * W + x) * 4;
      corners.push([d[o], d[o + 1], d[o + 2]]);
    }
  }
  corners.sort((a, b) => (a[0] + a[1] + a[2]) - (b[0] + b[1] + b[2]));
  const bg = corners[Math.floor(corners.length / 2)];
  const mask = new Uint8Array(W * H);
  let on = 0;
  for (let i = 0; i < W * H; i++) {
    const o = i * 4;
    const dist = Math.sqrt((d[o]-bg[0])**2 + (d[o+1]-bg[1])**2 + (d[o+2]-bg[2])**2);
    if (dist > 42) { mask[i] = 1; on++; }
  }
  return { mask, ratio: on / (W * H), W, H };
}

async function autoAlignCamera(imageIndex) {
  const v = window.__viewer;
  const fg = await computeForegroundMask(imageIndex);
  const t = { x: v.controls.target.x, y: v.controls.target.y, z: v.controls.target.z };
  let best = { iou: -1, az: 0, ratio: 0 };
  for (let azd = 0; azd < 360; azd += 5) {
    const sil = v.renderSilhouetteAt(azd, 175, 55, t);
    let inter = 0, union = 0;
    for (let i = 0; i < sil.w * sil.h; i++) {
      const a = fg.mask[i], b = sil.mask[i];
      if (a && b) inter++; else if (a || b) union++;
    }
    const iou = union ? inter / union : 0;
    if (iou > best.iou) best = { iou, az: azd, ratio: sil.ratio };
  }
  const fgRatio = fg.ratio, silRatio = best.ratio || 0.35;
  let dist = 175;
  if (silRatio > 0.02) dist = Math.max(90, Math.min(320, 175 * Math.sqrt(silRatio / Math.max(fgRatio, 0.02))));
  const rad = best.az * Math.PI / 180;
  v.camera.position.set(t.x + Math.sin(rad) * dist, t.y + 55, t.z + Math.cos(rad) * dist);
  v.camera.lookAt(t.x, t.y, t.z);
  v.controls.target.copy(v.controls.target);
  v.controls.update();
  return { azimuth: best.az, iou: Number(best.iou.toFixed(3)), distance: Math.round(dist) };
}

$('project').addEventListener('click'"""
    assert anchor in s, 'app anchor missing'
    s = s.replace(anchor, add, 1)

# 投影按钮流程：先自动对齐
old_click = "$('project').addEventListener('click', () => projectPaint());"
if old_click in s:
    s = s.replace(old_click,
"""$('project').addEventListener('click', async () => {
  if (state.imageUrl) {
    try {
      const al = await window.__autoAlign(state.imageIndex);
      toast(`自动对齐：方位 ${al.azimuth}° · IoU ${al.iou} · 距离 ${al.distance}`);
    } catch { /* 对齐失败沿用当前视角 */ }
  }
  await projectPaint(state.imageIndex);
});""")

# uploadImages 记录 imageIndex（最新上传图索引）
s = s.replace('''    state.session.palette = j.palette;
    state.imageUrl = `/api/sessions/${state.sid}/image.png`;''',
'''    state.session.palette = j.palette;
    state.imageUrl = `/api/sessions/${state.sid}/image.png`;
    state.imageIndex = j.image_index !== undefined ? j.image_index : null;''')

open(p, 'w', encoding='utf-8').write(s)
print('patched')
