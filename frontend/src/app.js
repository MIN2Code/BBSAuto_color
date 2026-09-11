import { Viewer } from './viewer.js?v=1';

const $ = (id) => document.getElementById(id);
const state = { sid: null, session: null, sel: -1, faceSlots: {}, imageUrl: null, align: false };

const viewer = new Viewer($('view'));
window.__viewer = viewer;   // 调试/自动化可直接操控相机
window.__state = state;     // 同上：会话状态
window.__autoAlign = autoAlignCamera;

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('on'), ms);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

function hexOf(c) { return (c || '').toUpperCase(); }

// ------------------------------------------------------------ 会话与部件
async function newSession() {
  const j = await api('/api/sessions', { method: 'POST' });
  state.sid = j.session_id;
  return j.session_id;
}

async function refreshSession() {
  state.session = await api(`/api/sessions/${state.sid}`);
  renderParts();
  renderPalette();
}

function renderParts() {
  const s = state.session;
  const el = $('parts');
  $('pcount').textContent = s.parts.length ? `（${s.parts.length} 件）` : '';
  if (!s.parts.length) { el.innerHTML = '<span class="hint">尚未导入模型件</span>'; return; }
  el.innerHTML = '';
  for (const p of s.parts) {
    const d = document.createElement('div');
    d.className = 'part' + (p.index === state.sel ? ' sel' : '');
    d.innerHTML = `<span class="sw" style="background:${p.color || '#8a939e'}"></span>`
      + `<span class="nm">${p.name}</span><div class="meta">${p.tris.toLocaleString()} 面 · ${p.area_cm2} cm²</div>`;
    d.addEventListener('click', () => { state.sel = p.index; renderParts(); renderPalette(); });
    el.appendChild(d);
  }
}

function renderPalette() {
  const s = state.session;
  const el = $('palette');
  if (!s.palette.length) { el.innerHTML = '<span class="hint">色板将显示在这里</span>'; return; }
  el.innerHTML = '';
  for (const c of s.palette) {
    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = c.hex;
    sw.title = `${c.hex}（占比 ${(c.ratio * 100).toFixed(1)}%）` + (state.sel >= 0 ? ' — 点击涂给选中件' : '（先选中一个件）');
    sw.addEventListener('click', async () => {
      if (state.sel < 0) { toast('先在件列表里选中一个件'); return; }
      await api(`/api/sessions/${state.sid}/color/${state.sel}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ color: c.hex }),
      });
      viewer.setPartColor(state.sel, c.hex);
      await refreshSession();
      toast(`${state.session.parts[state.sel].name} → ${c.hex}`);
    });
    el.appendChild(sw);
  }
}

// ------------------------------------------------------------ 上传
async function uploadImages(files) {
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f);
    const j = await api(`/api/sessions/${state.sid}/image`, { method: 'POST', body: fd });
    $('imgname').textContent = j.image_name;
    state.session.palette = j.palette;
    state.imageUrl = `/api/sessions/${state.sid}/image.png`;
    state.imageIndex = j.image_index !== undefined ? j.image_index : null;
    state.imageAngle[state.imageIndex] = parseFloat(document.getElementById('imgangle').value) || 0;
    renderPalette();
    toast(`「${f.name}」提取到 ${j.palette.length} 个主色`);
    if (j.region_pending) pollRegionReady(state.imageIndex);
  }
  $('alignmode').disabled = !state.imageUrl;
  $('project').disabled = !state.imageUrl;
}

// SAM2 后台分割完成提示：投影将自动切到区域模式（更稳的色块+更全的细节）
function pollRegionReady(idx, tried = 0) {
  if (tried > 120 || idx === null || idx === undefined) return;   // 最多等 10 分钟
  setTimeout(async () => {
    try {
      const r = await fetch(`/api/sessions/${state.sid}/regions?i=${idx}`);
      if (r.ok) { toast('✓ 区域分割完成，投影上色将使用区域模式', 4000); return; }
    } catch { /* 继续 */ }
    pollRegionReady(idx, tried + 1);
  }, 5000);
}

$('imgfile').addEventListener('change', async (ev) => {
  const files = [...ev.target.files];
  ev.target.value = '';
  if (files.length) await uploadImages(files);
});

async function uploadSTLs(files) {
  if (!files.length) return;
  if (!state.sid) await newSession();
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  toast('解析与合并中…');
  const j = await api(`/api/sessions/${state.sid}/parts`, { method: 'POST', body: fd });
  state.session = j.session;
  for (const e of j.errors) toast(e, 5000);
  await refreshSession();
  for (const p of j.added) await viewer.loadPart(state.sid, p.index, p.color);
  viewer.frameAll();
  toast(`已导入 ${j.added.length} 件`);
}

// 全局拖放接管：防止浏览器把拖入的 STL 当下载打开
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  const files = [...(e.dataTransfer ? e.dataTransfer.files : [])];
  if (!files.length) return;
  const stls = files.filter((f) => /\.stl$/i.test(f.name));
  const imgs = files.filter((f) => /\.(png|jpe?g|webp)$/i.test(f.name));
  if (stls.length) uploadSTLs(stls);
  if (imgs.length) uploadImages(imgs);
});

// ------------------------------------------------------------ 配色与导出
// ------------------------------------------------------------ 投影上色（L1）
$('alignmode').addEventListener('click', () => {
  state.align = !state.align;
  let ov = document.getElementById('imgoverlay');
  if (!ov) {
    ov = document.createElement('img');
    ov.id = 'imgoverlay';
    ov.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;pointer-events:none;opacity:.45;';
    $('view').appendChild(ov);
  }
  ov.src = state.imageUrl || '';
  ov.style.display = state.align ? 'block' : 'none';
  $('alignhint').style.display = state.align ? 'block' : 'none';
  $('alignmode').classList.toggle('primary', state.align);
});

// ------------------------------------------------------------ 自动对齐
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
  const userAz = (state.imageAngle || {})[imageIndex];
  const azList = (userAz !== undefined && userAz !== null)
    ? Array.from({ length: 7 }, (_, i) => userAz + (i - 3) * 5)   // 用户角度 ±15° 精调
    : Array.from({ length: 72 }, (_, i) => i * 5);                // 全扫描
  for (const azd of azList) {
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
  v.controls.target.set(t.x, t.y, t.z);
  v.controls.update();
  return { azimuth: best.az, iou: Number(best.iou.toFixed(3)), distance: Math.round(dist) };
}

async function projectPaint(imageIndex) {
  $('project').addEventListener = $('project').addEventListener; // no-op
  if (!state.session.parts.length) return;
  const cam = viewer.camera;
  const w = viewer.container.clientWidth, h = viewer.container.clientHeight;
  if (!state.imageUrl) { toast('先上传渲染图'); return; }
  toast('投影采样中（含遮挡测试）…');

  // 1) 渲染图像素（cover 到视口尺寸，与叠加层一致）
  const img = new Image();
  img.src = imageIndex === undefined
    ? state.imageUrl
    : `/api/sessions/${state.sid}/image.png?i=${imageIndex}`;
  await img.decode();
  const ic = document.createElement('canvas');
  ic.width = w; ic.height = h;
  const ictx = ic.getContext('2d', { willReadFrequently: true });
  const sc = Math.max(w / img.naturalWidth, h / img.naturalHeight);
  const dw = img.naturalWidth * sc, dh = img.naturalHeight * sc;
  ictx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
  const imgData = ictx.getImageData(0, 0, w, h).data;
  // 背景色估计（渲染图四角中值）：投影时跳过背景像素（不涂，保留既有色）
  // 背景色估计（渲染图四角中值）：投影时跳过背景像素（不涂，保留既有色）
  const corners = [];
  for (const [y0, x0] of [[0, 0], [0, w - 9], [h - 9, 0], [h - 9, w - 9]]) {
    for (let y = y0; y < y0 + 9; y += 4) for (let x = x0; x < x0 + 9; x += 4) {
      const o = (y * w + x) * 4;
      corners.push([imgData[o], imgData[o + 1], imgData[o + 2]]);
    }
  }
  corners.sort((a, b) => (a[0] + a[1] + a[2]) - (b[0] + b[1] + b[2]));
  const bgCol = corners[Math.floor(corners.length / 2)];
  const isBgPixel = (o) => {
    const dr = imgData[o] - bgCol[0], dg = imgData[o + 1] - bgCol[1], db = imgData[o + 2] - bgCol[2];
    return Math.sqrt(dr * dr + dg * dg + db * db) < 42;
  };

  // 2) 当前对齐位姿的深度缓冲（遮挡测试）
  const depth = viewer.projectDepthBuffer(cam, w, h);

  // 3) 色板
  const pal = state.session.palette_colors || state.session.palette.map((c) => c.hex);
  const palRGB = pal.map((hx) => [
    parseInt(hx.slice(1, 3), 16), parseInt(hx.slice(3, 5), 16), parseInt(hx.slice(5, 7), 16)]);
  const palHSV = palRGB.map(rgb2hsv);
  function rgb2hsv(r, g, b) {   // 0-255 → [h, s, v]
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d > 0) {
      if (mx === r) h = ((g - b) / d) % 6;
      else if (mx === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6;
      if (h < 0) h += 1;
    }
    return [h, mx ? d / mx : 0, mx];
  }

  // 4) 逐面：质心投影 → 背面剔除 → 深度遮挡 → 采样渲染图 → 最近色板
  const targets = state.sel >= 0 ? [state.sel] : state.session.parts.map((p) => p.index);
  const eye = cam.position;
  // 件色先验：采样色偏离本件颜色过远 = 对齐错位串色 → 保留件色不涂
  const baseHSVof = {};
  for (const p of state.session.parts) {
    if (p.color) baseHSVof[p.index] = rgb2hsv(
      parseInt(p.color.slice(1, 3), 16), parseInt(p.color.slice(3, 5), 16), parseInt(p.color.slice(5, 7), 16));
  }
  const distHSV = (a, b) => {
    let dh = Math.abs(a[0] - b[0]);
    if (dh > 0.5) dh = 1 - dh;
    const sw = Math.min(a[1], b[1]);
    return Math.sqrt((dh * 2 * sw * sw) ** 2 + ((a[1] - b[1])) ** 2 + ((a[2] - b[2]) * 0.6) ** 2);
  };
  const SAME_TONE = 0.22;     // 新采样与现有色 HSV 距离 < 此值 = 同色系光影差异 → 保留现有

  // 3.5) SAM2 区域分割数据（该图有分割结果则走区域查表：区域中位色稳定，
  //      抗渐变/抗锯齿；无结果则回退旧逐面最近色板路径）
  let regMap = null, regionSlot = null;
  try {
    const ridx = imageIndex === undefined ? -1 : imageIndex;
    const [rmR, rgR] = await Promise.all([
      fetch(`/api/sessions/${state.sid}/regionmap.png?i=${ridx}`),
      fetch(`/api/sessions/${state.sid}/regions?i=${ridx}`),
    ]);
    if (rmR.ok && rgR.ok) {
      const rimg = new Image();
      rimg.src = URL.createObjectURL(await rmR.blob());
      await rimg.decode();
      const rc = document.createElement('canvas');
      rc.width = rimg.naturalWidth; rc.height = rimg.naturalHeight;
      const rctx = rc.getContext('2d', { willReadFrequently: true });
      rctx.imageSmoothingEnabled = false;   // id 不能被插值污染
      rctx.drawImage(rimg, 0, 0);
      regMap = { data: rctx.getImageData(0, 0, rc.width, rc.height).data, w: rc.width, h: rc.height };
      const regions = (await rgR.json()).regions;
      // 区域中位色 → 最近色板槽号（HSV 量化，与逐面路径同公式）
      regionSlot = new Uint8Array(65536);
      for (const r of regions) {
        const hv = rgb2hsv(parseInt(r.hex.slice(1, 3), 16),
          parseInt(r.hex.slice(3, 5), 16), parseInt(r.hex.slice(5, 7), 16));
        let bi = 0, bd = 1e9;
        for (let k = 0; k < palHSV.length; k++) {
          const ps = palHSV[k];
          let dh = Math.abs(hv[0] - ps[0]);
          if (dh > 0.5) dh = 1 - dh;
          const sw = Math.min(hv[1], ps[1]);
          const d = (dh * 2 * sw * sw) ** 2 + ((hv[1] - ps[1]) * 1.0) ** 2
                  + ((hv[2] - ps[2]) * 0.35) ** 2;
          if (d < bd) { bd = d; bi = k; }
        }
        regionSlot[r.id] = bi + 1;
      }
    }
  } catch { /* 区域数据不可用 → 回退逐面采样 */ }

  // 阴影灰拒绝：低饱和中亮度像素多为渲染图阴影（非本色），跳过该面
  const vm = cam.matrixWorldInverse.elements;
  const pm = cam.projectionMatrix.elements;
  const TOL = 0.0025;
  let paintedTotal = 0;
  for (const pi of targets) {
    const mesh = viewer.meshes.find((m) => m.userData.partIndex === pi);
    if (!mesh) continue;
    let g = mesh.geometry;
    if (g.index) { g = g.toNonIndexed(); mesh.geometry.dispose(); mesh.geometry = g; }
    const pos = g.getAttribute('position');
    const nF = pos.count / 3;
    const slots = new Uint8Array(nF);
    const old = state.faceSlots[pi];   // 之前角度的累积成果
    for (let fi = 0; fi < nF; fi++) {
      const o = fi * 9;
      const ax = pos.array[o], ay = pos.array[o + 1], az = pos.array[o + 2];
      const bx = pos.array[o + 3], by = pos.array[o + 4], bz = pos.array[o + 5];
      const cx = pos.array[o + 6], cy = pos.array[o + 7], cz = pos.array[o + 8];
      // 质心
      const gx = (ax + bx + cx) / 3, gy = (ay + by + cy) / 3, gz = (az + bz + cz) / 3;
      // 法向（未归一，符号判断够用）：n · (eye - g) > 0
      const e1x = bx - ax, e1y = by - ay, e1z = bz - az;
      const e2x = cx - ax, e2y = cy - ay, e2z = cz - az;
      const nx = e1y * e2z - e1z * e2y;
      const ny = e1z * e2x - e1x * e2z;
      const nz = e1x * e2y - e1y * e2x;
      if (nx * (eye.x - gx) + ny * (eye.y - gy) + nz * (eye.z - gz) <= 0) continue;
      // 世界 → 相机空间（three.js：前方为 -Z）
      const rx = gx - eye.x, ry = gy - eye.y, rz = gz - eye.z;
      const vx = vm[0] * rx + vm[4] * ry + vm[8] * rz;
      const vy = vm[1] * rx + vm[5] * ry + vm[9] * rz;
      const vz = vm[2] * rx + vm[6] * ry + vm[10] * rz + vm[14];
      if (vz >= -0.1) continue;                       // 相机后方
      // → 透视 NDC 深度（与 MeshDepthMaterial 打包标度一致）
      const fragZ = ((pm[10] * vz + pm[14]) / -vz) * 0.5 + 0.5;
      // → NDC → 像素
      const ndx = (vx / -vz) / (pm[0]);
      const ndy = (vy / -vz) / (pm[5]);
      const px = Math.floor((ndx * 0.5 + 0.5) * w);
      const py = Math.floor((1 - (ndy * 0.5 + 0.5)) * h);
      if (px < 0 || px >= w || py < 0 || py >= h) continue;
      const bufZ = depth[(h - 1 - py) * w + px];
      if (fragZ > bufZ + TOL) continue;               // 被更近的几何遮挡
      const oldSlot = old ? old[fi] : 0;
      if (regionSlot) {
        // 区域查表路径：视口 → 渲染图原始坐标（逆 cover）→ 区域 id → 色板槽。
        // 命中区域 → 区域色（稳定，抗渐变/抗锯齿）；背景 → 不涂；
        // 边界/未知区域 → 落回下方逐面采样兜底
        const ix = Math.floor((px - (w - dw) / 2) / sc);
        const iy = Math.floor((py - (h - dh) / 2) / sc);
        if (ix >= 0 && iy >= 0 && ix < regMap.w && iy < regMap.h) {
          const ro = (iy * regMap.w + ix) * 4;
          const rid = regMap.data[ro] + regMap.data[ro + 1] * 256;
          if (!rid) continue;                         // 背景：永不采样
          if (regMap.data[ro + 2] !== 255) {
            const ns = regionSlot[rid];
            if (ns) {
              // 已涂面：同色系（光影差异）保留现有，不同色系才覆盖
              if (oldSlot) {
                const oh = palHSV[oldSlot - 1], nh = palHSV[ns - 1];
                if (oh && nh && distHSV(oh, nh) < SAME_TONE) continue;
              }
              slots[fi] = ns;
              continue;
            }
          }
        }
      }
      // 逐面采样渲染图 → 最近色板（HSV：色相主导，弱化光影明暗）
      const io = (py * w + px) * 4;
      if (isBgPixel(io)) continue;                   // 背景像素永不采样
      // 3×3 均值采样（抗单像素噪点）
      let sr = 0, sg = 0, sb = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const o2 = io + (dy * w + dx) * 4;
        sr += imgData[o2]; sg += imgData[o2 + 1]; sb += imgData[o2 + 2];
      }
      sr /= 9; sg /= 9; sb /= 9;
      const s0raw = rgb2hsv(sr, sg, sb);
      // 阴影灰拒绝：低饱和中亮灰 = 渲染阴影（非本色），保留该面现值
      // （收紧：只拦明显中灰；黑材质 v<0.15、暗红等不拦）
      if (s0raw[1] < 0.15 && s0raw[2] > 0.15 && s0raw[2] < 0.55) continue;
      const s0 = s0raw;
      // 已涂面：同色系（光影差异）保留现有；不同色系才覆盖
      if (oldSlot) {
        const oh = palHSV[oldSlot - 1];
        if (oh && distHSV(s0, oh) < SAME_TONE) continue;
      }
      let bi = 0, bd = 1e9;
      for (let k = 0; k < palHSV.length; k++) {
        const ps = palHSV[k];
        let dh = Math.abs(s0[0] - ps[0]);
        if (dh > 0.5) dh = 1 - dh;
        const sw = Math.min(s0[1], ps[1]);           // 低饱和时色相权重退化
        const d = (dh * 2 * sw * sw) ** 2 + ((s0[1] - ps[1]) * 1.0) ** 2
                + ((s0[2] - ps[2]) * 0.35) ** 2;
        if (d < bd) { bd = d; bi = k; }
      }
      slots[fi] = bi + 1;
    }
    paintedTotal += [...slots].filter((x) => x).length;
    state.faceSlots[pi] = slots;
    viewer.setFaceColors(pi, slots, pal,
      (state.session.parts.find((p) => p.index === pi) || {}).color);
    // 存后端（导出用）
    let b64 = '';
    for (let i = 0; i < slots.length; i++) b64 += String.fromCharCode(slots[i]);
    await fetch(`/api/sessions/${state.sid}/facecolors/${pi}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slots_b64: btoa(b64) }),
    });
  }
  // 投影完成：自动关闭叠加层，露出模型真实的面级颜色
  state.align = false;
  const ov2 = document.getElementById('imgoverlay');
  if (ov2) ov2.style.display = 'none';
  $('alignhint').style.display = 'none';
  $('alignmode').classList.remove('primary');
  toast(regionSlot
    ? `已投影上色 ${paintedTotal.toLocaleString()} 面（SAM2 区域模式 + 遮挡剔除）`
    : `已投影上色 ${paintedTotal.toLocaleString()} 面（逐面采样模式；多角度重复可覆盖全表面）`, 4500);
}
window.__project = projectPaint;
$('project').addEventListener('click', async () => {
  if (state.imageUrl) {
    try {
      const al = await window.__autoAlign(state.imageIndex);
      toast(`自动对齐：方位 ${al.azimuth}° · IoU ${al.iou} · 距离 ${al.distance}`);
    } catch { /* 对齐失败沿用当前视角 */ }
  }
  await projectPaint(state.imageIndex);
});

$('autoassign').addEventListener('click', async () => {
  const j = await api(`/api/sessions/${state.sid}/auto`, { method: 'POST' });
  state.session = j.session;
  for (const p of state.session.parts) viewer.setPartColor(p.index, p.color);
  renderParts();
  toast(`已自动配色 ${j.assigned} 件`);
});

$('export').addEventListener('click', () => {
  window.location.href = `/api/sessions/${state.sid}/export.3mf`;
});

$('upaxis').addEventListener('change', async (ev) => {
  await api(`/api/sessions/${state.sid}/upaxis`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ axis: ev.target.value }),
  });
  location.reload();   // 重新按新 up 轴加载
});

// 启动：?sid=xxx 恢复既有会话（服务重启后用脚本重建会话再引用），
// 否则新建空会话；加载全部件几何
const urlSid = new URLSearchParams(location.search).get('sid');
const init = urlSid ? Promise.resolve(urlSid) : newSession();
init.then((sid) => { state.sid = sid; return refreshSession(); }).then(async () => {
  $('autoassign').disabled = false;
  $('export').disabled = false;
  $('upaxis').value = state.session.up_axis || 'y';
  if (state.session.has_image) {
    state.imageUrl = `/api/sessions/${state.sid}/image.png`;
    $('imgname').textContent = state.session.image_name || '渲染图';
    $('alignmode').disabled = false;
    $('project').disabled = false;
  }
  const palHex = state.session.palette_colors
    || state.session.palette.map((c) => c.hex);
  for (const p of state.session.parts) {
    await viewer.loadPart(state.sid, p.index, p.color, state.session.up_axis);
    try {
      const fc = await fetch(`/api/sessions/${state.sid}/facecolors/${p.index}`).then((x) => x.json());
      if (fc.slots_b64) {
        const bin = atob(fc.slots_b64);
        const slots = new Uint8Array(bin.length);
        let any = 0;
        for (let i = 0; i < bin.length; i++) { slots[i] = bin.charCodeAt(i); any += slots[i] ? 1 : 0; }
        if (any) {
          state.faceSlots[p.index] = slots;
          viewer.setFaceColors(p.index, slots, palHex, p.color);
        }
      }
    } catch { /* 无面级颜色忽略 */ }
  }
  viewer.frameAll();
  if (state.session.parts.length) toast(`已加载 ${state.session.parts.length} 件`);
}).catch((e) => toast(`初始化失败：${e.message}`));
