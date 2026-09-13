import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export class Viewer {
  constructor(container) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0b141d);
    const w = container.clientWidth, h = container.clientHeight;
    this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 5000);
    // 性能优先：关 AA/关保留缓冲（截图改为主动渲染后读），像素比自适应
    this.renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: false });
    this._prMax = Math.min(window.devicePixelRatio, 1.5);
    this._pr = Math.min(window.devicePixelRatio, 1.0);
    this.renderer.setPixelRatio(this._pr);
    this.renderer.setSize(w, h);
    container.appendChild(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 1.1));
    const dir = new THREE.DirectionalLight(0xffffff, 1.2);
    dir.position.set(1, 2, 1.5);
    this.scene.add(dir);
    const grid = new THREE.GridHelper(400, 40, 0x1c2c3b, 0x142230);
    this.scene.add(grid);
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.meshes = [];
    window.addEventListener('resize', () => this._resize());
    this._lastActive = performance.now();
    this.controls.addEventListener('change', () => this.invalidate());
    this._frames = 0;
    this._frameAcc = 0;
    this._lastFpsT = performance.now();
    this._loop();
  }

  _resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
  }

  _loop = () => {
    requestAnimationFrame(() => this._loop());
    const now = performance.now();
    const dt = now - (this._prevT || now);
    this._prevT = now;
    this.controls.update();
    const active = now - this._lastActive < 2000;
    if (active || this.needsRender) {
      this.renderer.render(this.scene, this.camera);
      this.needsRender = false;
    }
    // FPS 自适应像素比：卡则降（至 0.6），流畅则升（至 1.5）
    this._frames++;
    this._frameAcc += dt;
    if (now - this._lastFpsT > 2000 && this._frames > 20) {
      const avg = this._frameAcc / this._frames;
      if (avg > 28 && this._pr > 0.6) {
        this._pr = Math.max(0.6, this._pr - 0.25);
        this.renderer.setPixelRatio(this._pr);
        this.invalidate();
      } else if (avg < 13 && this._pr < this._prMax) {
        this._pr = Math.min(this._prMax, this._pr + 0.25);
        this.renderer.setPixelRatio(this._pr);
        this.invalidate();
      }
      this._frames = 0; this._frameAcc = 0; this._lastFpsT = now;
    }
  };

  invalidate() {
    this.needsRender = true;
    this._lastActive = performance.now();
  }

  /** 主动渲染一帧并返回画布（截图用，无需 preserveDrawingBuffer）。 */
  renderOnce() {
    this.renderer.render(this.scene, this.camera);
    return this.renderer.domElement;
  }

  clear() {
    for (const m of this.meshes) {
      this.group.remove(m);
      m.geometry.dispose();
      m.material.dispose();
    }
    this.meshes = [];
    this.invalidate();
  }

  /** meta: [{index, color}]；几何异步经 loadPart 追加。 */
  frameAll() {
    const box = new THREE.Box3().setFromObject(this.group);
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    this.controls.target.copy(c);
    this.camera.position.set(c.x + size * 0.8, c.y + size * 0.55, c.z + size * 0.9);
    this.controls.update();
  }

  loadPart(sid, idx, color, upAxis = 'y') {
    return fetch(`/api/sessions/${sid}/geometry/${idx}`)
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        const head = new Uint32Array(buf, 0, 2);
        const nv = head[0], nf = head[1];
        const pos = new Float32Array(buf, 8, nv * 3);
        const idxs = new Uint32Array(buf, 8 + nv * 12, nf * 3);
        const geo = new THREE.BufferGeometry();
        // STL 无坐标系元数据：按会话检测的 up 轴转到 three.js 的 Y-up
        const rot = new Float32Array(nv * 3);
        for (let i = 0; i < nv; i++) {
          const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
          if (upAxis === 'z') {          // Z-up：绕 X -90°
            rot[i * 3] = x; rot[i * 3 + 1] = z; rot[i * 3 + 2] = -y;
          } else if (upAxis === 'x') {   // X-up：绕 Z +90°
            rot[i * 3] = -y; rot[i * 3 + 1] = x; rot[i * 3 + 2] = z;
          } else {                       // Y-up：three.js 原生，恒等
            rot[i * 3] = x; rot[i * 3 + 1] = y; rot[i * 3 + 2] = z;
          }
        }
        geo.setAttribute('position', new THREE.BufferAttribute(rot, 3));
        geo.setIndex(new THREE.BufferAttribute(idxs, 1));
        geo.computeVertexNormals();
        const mat = new THREE.MeshLambertMaterial({
          color: new THREE.Color(color || '#8a939e'),
          side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.userData.partIndex = idx;
        this.group.add(mesh);
        this.meshes.push(mesh);
        this.invalidate();
        return mesh;
      });
  }

  setPartColor(idx, color) {
    const m = this.meshes.find((x) => x.userData.partIndex === idx);
    if (m) m.material.color.set(color || '#8a939e');
    this.invalidate();
  }

  /** 渲染模型剪影：小尺寸 RT，返回 {mask: Uint8Array(64*64), ratio}（1=模型）。 */
  renderSilhouette(camera, w = 64, h = 64) {
    const rt = new THREE.WebGLRenderTarget(w, h);
    const prevRT = this.renderer.getRenderTarget();
    const prevBg = this.scene.background;
    const prevOv = this.scene.overrideMaterial;
    this.scene.background = null;
    this.scene.overrideMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff });
    this.renderer.setRenderTarget(rt);
    this.renderer.setClearColor(0x000000, 1);
    this.renderer.clear();
    this.renderer.render(this.scene, camera);
    const px = new Uint8Array(w * h * 4);
    this.renderer.readRenderTargetPixels(rt, 0, 0, w, h, px);
    this.renderer.setRenderTarget(prevRT);
    this.scene.overrideMaterial = prevOv;
    this.scene.background = prevBg;
    rt.dispose();
    const mask = new Uint8Array(w * h);
    let on = 0;
    for (let i = 0; i < w * h; i++) {
      // 行序翻转（WebGL 底部起）后写入：mask 按屏幕方向（上起）
      const row = h - 1 - Math.floor(i / w);
      const col = i % w;
      const onPix = px[i * 4] > 32 || px[i * 4 + 1] > 32 || px[i * 4 + 2] > 32;
      if (onPix) { mask[row * w + col] = 1; on++; }
    }
    return { mask, ratio: on / (w * h), w, h };
  }

  /** 以指定方位角/距离/FOV 渲染模型剪影（独立临时相机），dx/dy 为构图偏移
   * （64 格坐标，模拟作者后期裁剪导致的模型不居中），返回 {mask, ratio}。 */
  renderSilhouetteAt(azDeg, dist, height, target, w = 64, h = 64, fov = 45, dx = 0, dy = 0) {
    const cam = new THREE.PerspectiveCamera(fov, w / h, 1, 5000);
    const rad = azDeg * Math.PI / 180;
    const pos = new THREE.Vector3(
      target.x + Math.sin(rad) * dist,
      target.y + height,
      target.z + Math.cos(rad) * dist);
    const look = new THREE.Vector3(target.x, target.y, target.z);
    if (dx || dy) {
      const fwd = look.clone().sub(pos).normalize();
      const up0 = new THREE.Vector3(0, 1, 0);
      const right = new THREE.Vector3().crossVectors(fwd, up0).normalize();
      const up2 = new THREE.Vector3().crossVectors(right, fwd).normalize();
      const worldPerPx = 2 * dist * Math.tan(fov * Math.PI / 360) / h;
      look.add(right.multiplyScalar(dx * worldPerPx));
      look.add(up2.multiplyScalar(-dy * worldPerPx));
    }
    cam.position.copy(pos);
    cam.lookAt(look);
    cam.updateMatrixWorld();
    const rt = new THREE.WebGLRenderTarget(w, h);
    const prevRT = this.renderer.getRenderTarget();
    const prevBg = this.scene.background;
    const prevOv = this.scene.overrideMaterial;
    this.scene.background = null;
    this.scene.overrideMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff });
    for (const obj of this.scene.children) {   // 网格线不进剪影
      if (obj.isGridHelper || obj.isLine || obj.isLineSegments) obj.userData._silHidden = obj.visible, obj.visible = false;
    }
    this.renderer.setRenderTarget(rt);
    this.renderer.setClearColor(0x000000, 1);
    this.renderer.clear();
    this.renderer.render(this.scene, cam);
    const px = new Uint8Array(w * h * 4);
    this.renderer.readRenderTargetPixels(rt, 0, 0, w, h, px);
    this.renderer.setRenderTarget(prevRT);
    this.scene.overrideMaterial = prevOv;
    this.scene.background = prevBg;
    for (const obj of this.scene.children) {
      if (obj.userData && obj.userData._silHidden !== undefined) {
        obj.visible = obj.userData._silHidden;
        delete obj.userData._silHidden;
      }
    }
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

  /**
   * 件 ID 缓冲：每件唯一颜色（R=partIndex低8位, G=高8位, B=255）渲染一次读回。
   * 行序已翻转为上起（与渲染图 PNG 一致）。AA 已全局关闭，边缘无混色。
   * NoColorSpace 直写编码，避免 sRGB 变换吃掉小编码值。
   */
  renderIdBuffer(camera, w, h) {
    const rt = new THREE.WebGLRenderTarget(w, h);
    const prevRT = this.renderer.getRenderTarget();
    const prevBg = this.scene.background;
    this.scene.background = null;
    for (const obj of this.scene.children) {
      if (obj.isGridHelper || obj.isLine || obj.isLineSegments) obj.userData._idHidden = obj.visible, obj.visible = false;
    }
    const prevMats = [];
    const tempMats = [];
    for (const m of this.meshes) {
      prevMats.push([m, m.material]);
      const id = (m.userData.partIndex || 0) + 1;
      const mat = new THREE.MeshBasicMaterial({ side: THREE.DoubleSide });
      // 直接赋线性分量（绕过 ColorManagement）——setRGB 带 colorSpace 在 r160
      // 会做 sRGB→linear 转换，小编码值塌缩导致 ID 混叠
      mat.color.r = (id & 255) / 255;
      mat.color.g = ((id >> 8) & 255) / 255;
      mat.color.b = 1;
      tempMats.push(mat);
      m.material = mat;
    }
    this.renderer.setRenderTarget(rt);
    this.renderer.setClearColor(0x000000, 1);
    this.renderer.clear();
    this.renderer.render(this.scene, camera);
    const px = new Uint8Array(w * h * 4);
    this.renderer.readRenderTargetPixels(rt, 0, 0, w, h, px);
    this.renderer.setRenderTarget(prevRT);
    this.scene.background = prevBg;
    for (const [m, mat] of prevMats) m.material = mat;
    for (const mat of tempMats) mat.dispose();
    for (const obj of this.scene.children) {
      if (obj.userData && obj.userData._idHidden !== undefined) {
        obj.visible = obj.userData._idHidden;
        delete obj.userData._idHidden;
      }
    }
    rt.dispose();
    const data = new Uint8Array(w * h * 4);
    for (let i = 0; i < w * h; i++) {
      const row = h - 1 - Math.floor(i / w);
      const col = i % w;
      const o = (row * w + col) * 4, s = i * 4;
      data[o] = px[s]; data[o + 1] = px[s + 1]; data[o + 2] = px[s + 2]; data[o + 3] = 255;
    }
    return { data, w, h };
  }


  /** 渲染深度缓冲（RGBADepthPacking 解码为 [0,1] NDC 深度；背景≈0.996）。 */
  projectDepthBuffer(camera, w, h) {
    const rt = new THREE.WebGLRenderTarget(w, h);
    rt.depthTexture = new THREE.DepthTexture(w, h);
    rt.depthTexture.type = THREE.UnsignedIntType;
    const prevRT = this.renderer.getRenderTarget();
    const prevBg = this.scene.background;
    const prevOv = this.scene.overrideMaterial;
    this.scene.background = null;
    this.scene.overrideMaterial = new THREE.MeshDepthMaterial({
      depthPacking: THREE.RGBADepthPacking,
    });
    this.renderer.setRenderTarget(rt);
    this.renderer.setClearColor(0x000000, 1);
    this.renderer.clear();
    this.renderer.render(this.scene, camera);
    const rgba = new Uint8Array(w * h * 4);
    this.renderer.readRenderTargetPixels(rt, 0, 0, w, h, rgba);
    this.renderer.setRenderTarget(prevRT);
    this.scene.overrideMaterial = prevOv;
    this.scene.background = prevBg;
    rt.dispose();
    // three.js unpackRGBAToDepth：UnpackFactors = (255/256)/[256^3, 256^2, 256, 1]
    const depth = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) {
      const o = i * 4;
      depth[i] = rgba[o] * 5.937181e-8 + rgba[o + 1] * 1.519911e-5
               + rgba[o + 2] * 3.890991e-3 + rgba[o + 3] * 9.960937e-1;
    }
    return depth;
  }

  /** 清除件的面级顶点色，恢复整件单色（件级取色后调用；face_slots 属二期子件层）。 */
  clearFaceColors(idx, partColor) {
    const m = this.meshes.find((x) => x.userData.partIndex === idx);
    if (!m) return;
    if (m.geometry.getAttribute('color')) m.geometry.deleteAttribute('color');
    m.material.vertexColors = false;
    m.material.color.set(partColor || '#8a939e');
    m.material.needsUpdate = true;
    this.invalidate();
  }

  /** 件内逐面上色：slots[i]=槽号（0=未涂→件色），paletteHex 为色板色。 */
  setFaceColors(idx, slots, paletteHex, partColor) {
    const m = this.meshes.find((x) => x.userData.partIndex === idx);
    if (!m) return;
    const g = m.geometry.index ? m.geometry.toNonIndexed() : m.geometry;
    if (m.geometry !== g) { m.geometry.dispose(); m.geometry = g; }
    const pos = g.getAttribute('position');
    const col = new Float32Array(pos.count * 3);
    const base = new THREE.Color(partColor || '#8a939e');
    for (let fi = 0; fi < slots.length; fi++) {
      let c = base;
      if (slots[fi]) {
        const hex = paletteHex[slots[fi] - 1];
        if (hex) c = new THREE.Color(hex);
      }
      for (let k = 0; k < 3; k++) {
        col[fi * 9 + k * 3] = c.r;
        col[fi * 9 + k * 3 + 1] = c.g;
        col[fi * 9 + k * 3 + 2] = c.b;
      }
    }
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    g.computeVertexNormals();
    m.material.vertexColors = true;
    m.material.color.set('#ffffff');   // 顶点色与材质色相乘：置白让顶点色独立表达
    m.material.needsUpdate = true;
    this.invalidate();
  }
}
