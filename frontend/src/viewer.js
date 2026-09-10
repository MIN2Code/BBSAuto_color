import * as THREE from '../vendor/three.module.js';
import { OrbitControls } from '../vendor/addons/controls/OrbitControls.js';

export class Viewer {
  constructor(container) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0b141d);
    const w = container.clientWidth, h = container.clientHeight;
    this.camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 5000);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
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
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  };

  clear() {
    for (const m of this.meshes) {
      this.group.remove(m);
      m.geometry.dispose();
      m.material.dispose();
    }
    this.meshes = [];
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

  loadPart(sid, idx, color) {
    return fetch(`/api/sessions/${sid}/geometry/${idx}`)
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        const head = new Uint32Array(buf, 0, 2);
        const nv = head[0], nf = head[1];
        const pos = new Float32Array(buf, 8, nv * 3);
        const idxs = new Uint32Array(buf, 8 + nv * 12, nf * 3);
        const geo = new THREE.BufferGeometry();
        // STL 是 Z-up，three.js Y-up：绕 X 轴 -90°（与 thermal-assess 一致）
        const rot = new Float32Array(nv * 3);
        for (let i = 0; i < nv; i++) {
          rot[i * 3] = pos[i * 3];
          rot[i * 3 + 1] = pos[i * 3 + 2];
          rot[i * 3 + 2] = -pos[i * 3 + 1];
        }
        geo.setAttribute('position', new THREE.BufferAttribute(rot, 3));
        geo.setIndex(new THREE.BufferAttribute(idxs, 1));
        geo.computeVertexNormals();
        const mat = new THREE.MeshStandardMaterial({
          color: new THREE.Color(color || '#8a939e'),
          roughness: 0.85, metalness: 0.0, side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.userData.partIndex = idx;
        this.group.add(mesh);
        this.meshes.push(mesh);
        return mesh;
      });
  }

  setPartColor(idx, color) {
    const m = this.meshes.find((x) => x.userData.partIndex === idx);
    if (m) m.material.color.set(color || '#8a939e');
  }
}
