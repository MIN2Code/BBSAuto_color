# AutoColor 连续 RGB 视觉闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 有证据件不再显示默认灰；R2 已接受区域以连续 RGB 自动、完整地显示在 3D 画布上，预览与打印槽彻底分离。

**Architecture:** R1 件级取色改为"最佳估计色+旗帜"纪律并加同组回退；R2 新增 `compose_face_rgb` 纯函数与按件二进制预览接口（base→accepted→override 固定合成序）；前端收集完成后自动加载连续 RGB 顶点色（Uint8 归一化），提供完整颜色/件级颜色/打印槽三视图与同步清理；`face_slots` 只保留给打印映射。

**Tech Stack:** FastAPI + numpy（后端）、原生 ES module + three r160（前端）、pytest + IAB 浏览器验收。

**Spec:** `docs/superpowers/specs/2026-09-16-autocolor-continuous-preview-design.md`

## Global Constraints

- 工作区：worktree `J:\claudebox\autocolor\.worktrees\autocolor-restart-r1-r2`（分支 `feature/autocolor-restart-r1-r2`），**不动主链 8761**
- 测试命令：worktree 根目录下 `PYTHONPATH=. /j/claudebox/autocolor/.venv/Scripts/python.exe -m pytest -q`
- 验收服务：8762 端口起 uvicorn（`PYTHONPATH=. python -m uvicorn backend.app:app --host 127.0.0.1 --port 8762`），洛茜样张 `J:/baidu/Remy - Endfield - rossi 明日方舟终末地 洛茜/`（56 件 STL + Rossi01/03/05.png）
- three r160 陷阱：色彩相关一律直接赋值或用归一化 Uint8 attribute，禁用 `setRGB(..., NoColorSpace)`
- `face_slots` 保持 uint8 槽语义（1..255），连续 RGB 预览**永不写入** face_slots
- 前端 JS 改动后必须 `node --check frontend/src/app.js frontend/src/viewer.js`
- 提交信息中文简体、单段式；每任务收尾必须 commit
- 收官时重写 `proj-autocolor.md` 记忆并双向镜像（`.zcode/memory` ↔ `.claude/projects/C--Users-19186/memory`），更新 SDD 账本

---

### Task 1: R1 最佳估计色纪律与同组回退

**Files:**
- Modify: `backend/colorize.py:118-199`（recolor_session）
- Test: `tests/test_colorize.py`

**Interfaces:**
- Consumes: `meshpack._prefix(name)`（语义前缀，af1-x.stl→af，leg_l1→leg）
- Produces: `recolor_session(sess)` 行为变更——`views_disagree_hard` 时 `hex=最佳估计色`（不再回退旧色，仍 `flagged=True, conf=0.0`）；`no_visible_pixels` 件回退同组可信件色（像素数加权 Lab 中位）。返回 item 增加可选键 `"fallback": True`（仅同组回退件）。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_colorize.py`）

```python
def test_disagree_hard_keeps_best_estimate(make_color_session):
    sess = make_color_session()          # 红 vs 蓝两视角 → views_disagree_hard
    result = recolor_session(sess)
    item = result[0]
    assert item["flagged"] is True and item["conf"] == 0.0
    assert item["reason"] == "views_disagree_hard"
    assert item["hex"] != "#808080", "有证据件不得回退默认灰"


def test_no_evidence_part_falls_back_to_group_color(make_color_session):
    sess = make_color_session()
    sess["parts"] = [{"name": "af1-x.stl", "color": ""}, {"name": "af2-x.stl", "color": ""}]
    # af2 无任何可见像素：idbuf 第二视角全零不影响（按件 id 取色）——
    # af1 件 id=1 有证据；af2 件 id=2 无证据
    result = recolor_session(sess)
    assert result[1]["hex"] == result[0]["hex"]
    assert result[1].get("fallback") is True
    assert result[1]["flagged"] is True
```

- [ ] **Step 2: 运行确认失败**：`PYTHONPATH=. ...pytest tests/test_colorize.py -k "disagree_hard or falls_back" -v` → 2 failed（当前实现 hard 分支回退旧色 `#808080`；无 fallback 键）

- [ ] **Step 3: 实现**（colorize.py）
  - hard 分支：删去 `item.update({"hex": p.get("color") or ...})` 的回退，仅保留 `reason/conf/flagged`（med 已在 item["hex"]）。
  - recolor_session 末尾、override 生效之前：对 `reason == "no_visible_pixels"` 的件，`groups.setdefault(meshpack._prefix(p["name"]), ...)` 收集全件；组内取 `views > 0 and reason != "views_disagree_hard"` 成员，按 `pixels` 加权 `weighted_median(labs)`（需在第一轮把每个有证据件的 `med_lab` 存进临时列表 `meds[pi]`）；无可用组员保持原样，有则 `item["hex"] = lab_hex(med)`、`item["fallback"] = True`。顶部 `from .meshpack import _prefix`（或 `from .. import meshpack`——colorize 已被 routes 以 `colorize.xxx` 引用，直接 `from . import meshpack` 需查现有导入风格，跟随现状）。

- [ ] **Step 4: 全量测试**：`PYTHONPATH=. ...pytest -q` → 全过（注意 test_recolor_contract 的无可见分支：单件无组员 → 行为不变）

- [ ] **Step 5: Commit** `git commit -m "feat: R1 有证据件保留最佳估计色并支持同组回退"`

---

### Task 2: compose_face_rgb 纯函数

**Files:**
- Modify: `backend/face_candidates.py`（CandidateRegion 所在文件，追加函数）
- Test: `tests/test_face_candidates.py`

**Interfaces:**
- Produces: `compose_face_rgb(face_count: int, base_hex: str, accepted_regions: Iterable[CandidateRegion], face_overrides: Mapping[int, str]) -> np.ndarray`——shape `(face_count, 3)`、dtype uint8。区域优先级：`(-confidence, -support_views, region_id)` 升序遍历，先写者赢；`face_overrides`（face→hex 字符串）最后写入永远赢。空 regions/overrides 时全 base。

- [ ] **Step 1: 写失败测试**

```python
def test_compose_face_rgb_priority_and_override():
    from backend.face_candidates import CandidateRegion, compose_face_rgb
    lo = CandidateRegion(0, frozenset({0, 1}), "#FF0000", 3, 0.9, True, "accepted")
    hi = CandidateRegion(1, frozenset({1}), "#00FF00", 3, 0.95, True, "accepted")
    rgb = compose_face_rgb(4, "#FFFFFF", [lo, hi], {})
    assert rgb[0].tolist() == [255, 0, 0]
    assert rgb[1].tolist() == [0, 255, 0]      # hi 置信先写，lo 不得覆盖
    assert (rgb[2] == 255).all() and (rgb[3] == 255).all()
    rgb2 = compose_face_rgb(4, "#FFFFFF", [lo], {1: "#0000FF"})
    assert rgb2[1].tolist() == [0, 0, 255]


def test_compose_face_rgb_empty_is_base():
    from backend.face_candidates import compose_face_rgb
    rgb = compose_face_rgb(3, "#123456", [], {})
    assert (rgb == np.array([0x12, 0x34, 0x56], dtype=np.uint8)).all()
```

- [ ] **Step 2: 确认失败**（ImportError）

- [ ] **Step 3: 实现**：文件内加 `_hex_to_rgb8(hex_color) -> np.ndarray(uint8,3)`（`int(h[1:3],16)` 等，非法输入返回灰 `#808080`）；`compose_face_rgb` 按 spec 合成。区域未 accepted 或 slot==0 无关紧要——本函数只看颜色与 faces，accepted 由调用方过滤。

- [ ] **Step 4: 全量测试过 → Commit** `git commit -m "feat: 添加连续 RGB 面色合成函数"`

---

### Task 3: 预览接口与 phase2_revision

**Files:**
- Modify: `backend/meshpack.py:26`（new_session 加 `"phase2_revision": 0`）
- Modify: `backend/api/routes.py`（phase2_collect 成功尾部、phase2_clear、set_part_color 各 +1 revision；新增 preview 端点）
- Test: `tests/test_autocolor.py`

**Interfaces:**
- Produces: `GET /api/sessions/{sid}/phase2/preview/{part_index}` → body 为 `face_count*3` 字节 RGB；headers `X-Face-Count`（str(int)）、`X-Phase2-Revision`（str(int)）、`Content-Type: application/octet-stream`。part 不存在 → 404；status 不限（idle 时返回纯 base 色）。GET `/phase2` 响应增加 `"revision"`。
- Revision 语义：collect 成功、clear auto、人工改件色（`/color/{pi}`）各 +1；前端据此丢弃过期加载。

- [ ] **Step 1: 写失败测试**（tests/test_autocolor.py 追加）

```python
def test_phase2_preview_returns_base_when_idle(client, ready_session):
    r = client.get(f"/api/sessions/{ready_session}/phase2/preview/0")
    assert r.status_code == 200
    faces = len(meshpack.get_session(ready_session)["parts"][0]["faces"])
    assert len(r.content) == faces * 3
    assert r.headers["X-Face-Count"] == str(faces)
    assert int(r.headers["X-Phase2-Revision"]) >= 0


def test_phase2_preview_composes_accepted_regions(client, ready_session):
    sess = meshpack.get_session(ready_session)
    n = len(sess["parts"][0]["faces"])
    sess["phase2_regions"] = [
        {"part_index": 0, "region_id": 0, "faces": list(range(4)),
         "color_hex": "#D22A1F", "support_views": 2, "confidence": 0.9,
         "accepted": True, "reason": "accepted", "slot": 0}]
    sess["phase2_status"] = "ready"
    r = client.get(f"/api/sessions/{ready_session}/phase2/preview/0")
    body = r.content
    assert body[0:3] == bytes([0xD2, 0x2A, 0x1F])          # 面0 区域色
    assert body[12:15] != bytes([0xD2, 0x2A, 0x1F])        # 面4 仍 base
    assert all(p.get("face_slots") is None for p in sess["parts"])


def test_phase2_preview_override_wins_and_clear_resets(client, ready_session, phase2_session):
    sess = meshpack.get_session(ready_session)
    sess["phase2_regions"] = [
        {"part_index": 0, "region_id": 0, "faces": [1], "color_hex": "#D22A1F",
         "support_views": 2, "confidence": 0.9, "accepted": True,
         "reason": "accepted", "slot": 0}]
    sess["phase2_status"] = "ready"
    sess["face_overrides"] = {"0": {"1": "#2A46D2"}}
    body = client.get(f"/api/sessions/{ready_session}/phase2/preview/0").content
    assert body[3:6] == bytes([0x2A, 0x46, 0xD2])
    client.delete(f"/api/sessions/{ready_session}/phase2/auto")
    body2 = client.get(f"/api/sessions/{ready_session}/phase2/preview/0").content
    base = _hex_rgb(meshpack.get_session(ready_session)["parts"][0]["color"] or "#FFFFFF")
    assert body2[3:6] == bytes(base[0].tolist())
```

- [ ] **Step 2: 确认失败**（404 Not Found）

- [ ] **Step 3: 实现**：preview 端点从 `sess["phase2_regions"]` 过滤 `part_index==pi and accepted`，构造 `CandidateRegion(region_id, frozenset(faces), color_hex, support_views, confidence, True, reason)` 列表（slot 无用传 0），调用 `compose_face_rgb`；`Response(content=body.tobytes(), media_type="application/octet-stream", headers={...})`。collect/clear/color 三处 `sess["phase2_revision"] = sess.get("phase2_revision", 0) + 1`（clear 里在置 idle 前）。`/phase2` GET 返回加 `"revision": sess.get("phase2_revision", 0)`。

- [ ] **Step 4: 全量测试过 → Commit** `git commit -m "feat: 添加二期连续 RGB 预览接口"`

---

### Task 4: 前端连续 RGB 预览（viewer + 自动加载 + 恢复）

**Files:**
- Modify: `frontend/src/viewer.js`（追加 setFaceRGB）
- Modify: `frontend/src/app.js`（phase2collect 尾部、init 恢复链）
- Modify: `frontend/index.html`（无改动；视图按钮 Task 5 加）

**Interfaces:**
- Produces: `viewer.setFaceRGB(idx, rgbUint8, partColor)`——rgbUint8 长度 = faceCount*3；toNonIndexed 后建 `new THREE.BufferAttribute(new Uint8Array(pos.count*3), 3, true)` 归一化顶点色，`material.vertexColors=true; material.color.set('#ffffff')`。app.js `state.previewRGB[partIndex]` 缓存 Uint8Array；`state.viewMode`（Task 5 用）。

- [ ] **Step 1: viewer.setFaceRGB**（对照 setFaceColors 的 toNonIndexed 模式实现；不调 computeVertexNormals——颜色不需重算法线，但 toNonIndexed 后法线属性缺失需 `g.computeVertexNormals()` 保留）

- [ ] **Step 2: `node --check` 过**

- [ ] **Step 3: app.js 加载器**

```javascript
async function loadPhase2Preview() {
  const j = await api(`/api/sessions/${state.sid}/phase2`);
  if (j.status !== 'ready') return;
  const rev = j.revision;
  state.previewRGB = {};
  for (const p of j.parts) {
    if (!p.regions.some((r) => r.accepted)) continue;
    const r = await fetch(`/api/sessions/${state.sid}/phase2/preview/${p.index}`);
    if (!r.ok) continue;
    const buf = new Uint8Array(await r.arrayBuffer());
    const mesh = window.__viewer.meshes.find((x) => x.userData.partIndex === p.index);
    if (!mesh) continue;
    const faces = Math.floor(mesh.geometry.getAttribute('position').count / 3);
    if (buf.length !== faces * 3) { console.warn('preview size mismatch', p.index); continue; }
    if ((await api(`/api/sessions/${state.sid}/phase2`)).revision !== rev) return;  // 过期放弃
    state.previewRGB[p.index] = buf;
    viewer.setFaceRGB(p.index, buf, p.color);
  }
}
```

（优化：revision 只查一次存局部，循环后复查一次即可——实现时避免逐件再 GET。）

- [ ] **Step 4: 接线**：phase2collect 成功分支 `await renderPhase2Summary(); await loadPhase2Preview();`。init 恢复链 `refreshSession()` 后追加：

```javascript
try {
  const j2 = await api(`/api/sessions/${state.sid}/phase2`);
  if (j2.status === 'ready') { await renderPhase2Summary(); await loadPhase2Preview(); }
} catch { /* 二期状态缺失忽略 */ }
```

- [ ] **Step 5: `node --check` → Commit** `git commit -m "feat: 前端自动加载连续 RGB 完整预览"`

---

### Task 5: 三视图模式、摘要折叠分页与同步清理

**Files:**
- Modify: `frontend/index.html`（phase2 行加按钮 `viewfull/viewpart/viewslots/phase2clear`）
- Modify: `frontend/src/app.js`（renderPhase2Summary 重写、setViewMode、clearAuto）
- Modify: `frontend/src/viewer.js`（无新函数；复用 setFaceRGB/clearFaceColors/setFaceColors）

**Interfaces:**
- Consumes: Task 3/4 全部产物；`state.previewRGB`、`state.phase2Palette`（GET /phase2 的 palette）。
- Produces: `setViewMode(mode)` `'full'|'part'|'slots'`；clearAuto 同步清摘要+预览+恢复件色。

- [ ] **Step 1: index.html**：phase2 row 内追加

```html
<button class="btn" id="phase2clear" disabled title="清除自动候选：恢复件级颜色，保留人工改动">清除自动候选</button>
<div class="row" style="margin-top:4px" id="viewmodes" style="display:none">
  <button class="btn" id="viewfull">完整颜色</button>
  <button class="btn" id="viewpart">件级颜色</button>
  <button class="btn" id="viewslots">打印槽</button>
</div>
```

（注意 html 不能同元素两个 style——合并写。）

- [ ] **Step 2: setViewMode 实现**：`part` → 逐件 clearFaceColors(件色)；`full` → 有 `state.previewRGB` 的件 setFaceRGB，无则 clearFaceColors；`slots` → 逐件 GET /facecolors，全零槽 clearFaceColors，否则 setFaceColors(slots, state.phase2Palette, 件色)。按钮 active 态切换 `classList`。

- [ ] **Step 3: renderPhase2Summary 重写**：顶层每件一行 `件名 | N 候选 | N 接受 | N 待复核` + 首区域色块，点击展开；展开区每页 50 条（区域行沿用现 data-part/data-faces 结构），页码 prev/next。区域点击预览改为：选中 `viewer.previewFaceRegion`，取消时**恢复完整预览**（`state.previewRGB[pi] ? viewer.setFaceRGB(...) : viewer.clearFaceColors(...)`）而非件色。摘要顶部数据存 `state.phase2Palette = j.palette`、`state.phase2Regions = j.parts`。

- [ ] **Step 4: clearAuto**：

```javascript
$('phase2clear').addEventListener('click', async () => {
  await api(`/api/sessions/${state.sid}/phase2/auto`, { method: 'DELETE' });
  state.previewRGB = {}; state.phase2Regions = [];
  for (const p of state.session.parts)
    viewer.clearFaceColors(p.index, p.color);
  $('phase2summary').innerHTML = '<div class="hint">已清除自动候选</div>';
  setViewMode('part'); toast('已清除自动候选，恢复件级颜色');
});
```

按钮启用：phase2collect 点击后启用；viewmodes 同理（init 恢复 ready 时也启用）。

- [ ] **Step 5: `node --check` → Commit** `git commit -m "feat: 三视图模式与候选摘要折叠分页"`

---

### Task 6: 洛茜浏览器验收 + 文档 + 记忆收官

**Files:**
- Modify: `README.md`、`docs/实施说明_0911.md`（§8 视觉闭环回归）
- Modify: SDD 账本 `progress.md`、`proj-autocolor.md`（记忆 + 镜像）

**Interfaces:** 无代码接口；产出验收证据与文档。

- [ ] **Step 1: 全量 pytest 过**（36+新增）
- [ ] **Step 2: 起 8762 + 重建洛茜会话 + 浏览器跑 R1→R2**（复用本会话已验证的流程：56 件导入、3 图上传、autocolor、phase2collect）
- [ ] **Step 3: 浏览器断言**（evaluate）：
  - 有证据件材质色 ≠ `#8A939E` 的数量：灰色件数只允许 `no_visible_pixels` 且无组员回退的件
  - 有 accepted 区域的件 `geometry.getAttribute('color')` 存在且为 Uint8 normalized
  - 三视图切换：part 模式全件纯色；full 模式恢复 preview；slots 模式显示槽色或纯色
  - 清除自动候选：摘要清空、颜色恢复件级、再点发现可重跑
  - 刷新页面（`?sid=`）：preview 自动恢复
- [ ] **Step 4: 截图留证 → 更新文档与账本 → 记忆重写 + 镜像同步 → Commit** `git commit -m "docs: 连续 RGB 视觉闭环验收记录"`

---

## Self-Review

- Spec 覆盖：R1 纪律（T1）、同组回退（T1）、compose（T2）、preview+revision（T3）、自动预览+恢复（T4）、三视图+折叠分页+清理（T5）、验收+文档（T6）。打印映射 UI 明确不在本轮（spec 边界）。
- 占位符：无 TBD/TODO；所有代码块为真实实现。
- 类型一致：`compose_face_rgb(face_count:int, base_hex:str, accepted_regions:Iterable[CandidateRegion], face_overrides:Mapping[int,str])`；T3 调用与 T2 签名一致；`viewer.setFaceRGB(idx, rgbUint8, partColor)` 与 T4/T5 调用一致；`state.previewRGB` T4 产生、T5 消费一致。
