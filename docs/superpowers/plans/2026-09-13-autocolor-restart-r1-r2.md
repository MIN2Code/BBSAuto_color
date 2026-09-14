# AutoColor Restart R1-R2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重建稳定的件级涂装基线，并实现不依赖新语义模型、先收集后决策的件内多色候选数据层。

**Architecture:** R1 将旧顺序投影彻底移出主流程，固定件 ID 缓冲、件级取色、连续 RGB、旗帜和 override 接口。R2 在件级底色之上收集每视角面级候选，通过 Lab 聚类、面邻接“按边缘”区域生长和跨视角统一决策生成候选区域；候选默认不直接应用。

**Tech Stack:** Python 3.10、FastAPI、NumPy、trimesh、three.js r160、pytest、Bambu/Orca 3MF 扩展。

## Global Constraints

- 不训练新的语义模型。
- SAM2、Depth Anything 和扩散模型不能成为主决策器。
- 自动流程必须先收集所有视角候选，再统一决策，一次应用。
- 不可靠区域保持件级主色并进入旗帜列表。
- 人工 override 永远高于自动结果，重跑不能覆盖。
- 输出连续 RGB；色板吸附默认关闭。
- 0.4mm 喷嘴下不可打印的微小色块不进入自动接受结果。

---

## File Map

- `backend/colorize.py`: R1 件级颜色统计、置信度和回退规则。
- `backend/face_candidates.py`: R2 面级候选收集、Lab 聚类、区域合并和统一决策。
- `backend/mesh_edges.py`: 面邻接、二面角和受约束区域生长。
- `backend/api/routes.py`: R1/R2 API、状态和候选序列化。
- `backend/meshpack.py`: 会话中件级色、候选和 override 数据模型。
- `backend/threemf_out.py`: 连续 RGB 对象色与已接受 face paint 输出。
- `frontend/src/viewer.js`: 件 ID/深度缓冲和候选区域预览。
- `frontend/src/app.js`: 新主流程、候选采集、统一决策触发和结果应用。
- `frontend/index.html`: 新流程按钮与候选摘要 UI。
- `tests/test_colorize.py`: 件级颜色和顺序不变性测试。
- `tests/test_mesh_edges.py`: 按边缘区域生长测试。
- `tests/test_face_candidates.py`: 候选收集/聚类/统一决策测试。
- `tests/test_threemf_phase2.py`: 对象级 RGB 与 per-face 编码结构测试。

---

### Task 1: 固化 R1 件级结果与回退契约

**Files:**
- Modify: `backend/colorize.py`
- Modify: `backend/meshpack.py`
- Create: `tests/test_colorize.py`

**Interfaces:**
- Consumes: `sess["images"]`, `sess["idbufs"]`, `sess["parts"]`, `sess["overrides"]`。
- Produces: `recolor_session(sess) -> list[dict]`，每项固定包含 `index,name,hex,conf,flagged,reason,pixels,views`。

- [ ] **Step 1: 写件级顺序不变性失败测试**

```python
def test_recolor_is_independent_of_view_order(make_color_session):
    sess = make_color_session()
    a = recolor_session(sess)
    sess["images"].reverse()
    sess["idbufs"].reverse()
    b = recolor_session(sess)
    assert [(x["index"], x["hex"]) for x in a] == [(x["index"], x["hex"]) for x in b]
```

- [ ] **Step 2: 写 override 最后生效失败测试**

```python
def test_override_wins_after_recolor(make_color_session):
    sess = make_color_session()
    sess["overrides"] = {"0": "#123456"}
    result = recolor_session(sess)
    assert result[0]["hex"] == "#123456"
    assert result[0]["override"] is True
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `pytest tests/test_colorize.py -v`
Expected: FAIL，暴露字段不稳定或顺序依赖。

- [ ] **Step 4: 统一返回字段与候选排序**

在 `recolor_session` 末尾按 part index 输出，所有分支都补齐 `pixels`、`views`、`override`；跨视角样本只按 `(view_index, lab, pixels)` 收集，最终统计不使用输入列表位置作为优先级。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/test_colorize.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/colorize.py backend/meshpack.py tests/test_colorize.py
git commit -m "refactor: 固化件级取色与回退契约"
```

---

### Task 2: 建立面邻接与“按边缘”区域生长

**Files:**
- Create: `backend/mesh_edges.py`
- Create: `tests/test_mesh_edges.py`

**Interfaces:**
- Produces: `build_face_adjacency(faces: np.ndarray) -> list[list[int]]`。
- Produces: `grow_region(seed_faces, adjacency, normals, colors_lab, max_normal_deg, max_delta_e) -> np.ndarray[bool]`。

- [ ] **Step 1: 写邻接测试**

```python
def test_build_face_adjacency_two_triangles_share_edge():
    faces = np.array([[0, 1, 2], [2, 1, 3]])
    assert build_face_adjacency(faces) == [[1], [0]]
```

- [ ] **Step 2: 写法向边界停止测试**

```python
def test_region_growth_stops_at_sharp_edge():
    adjacency = [[1], [0, 2], [1]]
    normals = np.array([[0, 0, 1], [0, 0, 1], [1, 0, 0]], dtype=float)
    colors = np.array([[50, 20, 10], [51, 20, 10], [51, 20, 10]], dtype=float)
    mask = grow_region([0], adjacency, normals, colors, max_normal_deg=35, max_delta_e=8)
    assert mask.tolist() == [True, True, False]
```

- [ ] **Step 3: 写颜色边界停止测试**

```python
def test_region_growth_stops_at_color_edge():
    adjacency = [[1], [0, 2], [1]]
    normals = np.array([[0, 0, 1]] * 3, dtype=float)
    colors = np.array([[50, 0, 0], [52, 0, 0], [70, 30, 20]], dtype=float)
    mask = grow_region([0], adjacency, normals, colors, max_normal_deg=35, max_delta_e=8)
    assert mask.tolist() == [True, True, False]
```

- [ ] **Step 4: 运行测试确认失败**

Run: `pytest tests/test_mesh_edges.py -v`
Expected: FAIL，模块不存在。

- [ ] **Step 5: 实现邻接与区域生长**

使用排序顶点边 `(min(v1,v2), max(v1,v2))` 构建共享边邻接；BFS 扩张时同时检查种子代表色 ΔE、当前面与邻面二面角，任一超阈值则停止。

- [ ] **Step 6: 运行测试并提交**

Run: `pytest tests/test_mesh_edges.py -v`
Expected: PASS。

```bash
git add backend/mesh_edges.py tests/test_mesh_edges.py
git commit -m "feat: 添加按边缘面区域生长"
```

---

### Task 3: 建立面级候选数据结构与收集器

**Files:**
- Create: `backend/face_candidates.py`
- Modify: `backend/meshpack.py`
- Create: `tests/test_face_candidates.py`

**Interfaces:**
- Consumes: part faces/vertices、每视角相机参数、渲染图、件 ID/深度缓冲。
- Produces: `collect_face_candidates(part, views, base_color) -> dict[int, list[FaceEvidence]]`。
- Produces: `FaceEvidence(view_index: int, lab: tuple, visibility: float, pixel_count: int)`。

- [ ] **Step 1: 写“收集不应用”失败测试**

```python
def test_collection_does_not_change_face_slots(sample_part, sample_views):
    original = sample_part.get("face_slots")
    result = collect_face_candidates(sample_part, sample_views, "#FFFFFF")
    assert result
    assert sample_part.get("face_slots") == original
```

- [ ] **Step 2: 写背景/遮挡像素不进入证据测试**

```python
def test_hidden_face_has_no_evidence(sample_part, occluded_views):
    result = collect_face_candidates(sample_part, occluded_views, "#FFFFFF")
    assert result.get(3, []) == []
```

- [ ] **Step 3: 运行失败测试**

Run: `pytest tests/test_face_candidates.py::test_collection_does_not_change_face_slots tests/test_face_candidates.py::test_hidden_face_has_no_evidence -v`
Expected: FAIL，模块或接口不存在。

- [ ] **Step 4: 实现候选证据类型和采集器**

候选只保存 Lab、来源视角、可见性、像素数；采集过程禁止写 `face_slots`。使用面质心投影、背面剔除和深度容差确认可见性；颜色取质心周围可信像素的截尾中位。

- [ ] **Step 5: 运行测试并提交**

Run: `pytest tests/test_face_candidates.py -v`
Expected: PASS。

```bash
git add backend/face_candidates.py backend/meshpack.py tests/test_face_candidates.py
git commit -m "feat: 收集面级多视角颜色证据"
```

---

### Task 4: 件内颜色簇与候选区域发现

**Files:**
- Modify: `backend/face_candidates.py`
- Modify: `tests/test_face_candidates.py`

**Interfaces:**
- Produces: `discover_regions(part, evidence_by_face, base_lab, min_delta_e=10, min_faces=8) -> list[CandidateRegion]`。
- `CandidateRegion` 包含 `region_id, faces, color_hex, support_views, confidence, accepted, reason`。

- [ ] **Step 1: 写第二色带发现测试**

```python
def test_discovers_connected_secondary_color(sample_grid_part):
    evidence = make_evidence(base_faces="#FFFFFF", secondary_faces="#D22A1F")
    regions = discover_regions(sample_grid_part, evidence, rgb_hex_to_lab("#FFFFFF"), min_delta_e=10, min_faces=3)
    assert len(regions) == 1
    assert regions[0].faces == {4, 5, 6}
```

- [ ] **Step 2: 写孤立椒盐面拒绝测试**

```python
def test_rejects_isolated_single_face_noise(sample_grid_part):
    evidence = make_evidence(base_faces="#FFFFFF", secondary_faces="#D22A1F", isolated=True)
    assert discover_regions(sample_grid_part, evidence, rgb_hex_to_lab("#FFFFFF"), min_delta_e=10, min_faces=3) == []
```

- [ ] **Step 3: 运行失败测试**

Run: `pytest tests/test_face_candidates.py -k "discovers or isolated" -v`
Expected: FAIL。

- [ ] **Step 4: 实现聚类与按边缘区域生长**

将每面多视角证据先合并为加权 Lab 代表色；用固定 K=2..4 的小规模迭代聚类或色带直方图主峰产生种子；只对 ΔE≥10 的次色种子调用 `grow_region`；少于 `min_faces` 或物理宽度小于 0.8mm 的区域拒绝。

- [ ] **Step 5: 运行测试并提交**

Run: `pytest tests/test_face_candidates.py -v`
Expected: PASS。

```bash
git add backend/face_candidates.py tests/test_face_candidates.py
git commit -m "feat: 发现件内多色候选区域"
```

---

### Task 5: 跨视角统一决策与 override 优先级

**Files:**
- Modify: `backend/face_candidates.py`
- Modify: `tests/test_face_candidates.py`

**Interfaces:**
- Produces: `decide_regions(regions, min_confidence=0.75, min_views=2) -> list[CandidateRegion]`。
- Produces: `apply_face_layers(base_slots, accepted_regions, face_overrides) -> np.ndarray[np.uint8]`。

- [ ] **Step 1: 写视角顺序不变测试**

```python
def test_decision_is_independent_of_view_order(sample_regions):
    a = decide_regions(sample_regions)
    shuffled = copy.deepcopy(sample_regions)
    for r in shuffled:
        r.evidence.reverse()
    b = decide_regions(shuffled)
    assert [(r.faces, r.color_hex, r.accepted) for r in a] == [(r.faces, r.color_hex, r.accepted) for r in b]
```

- [ ] **Step 2: 写低置信候选不应用测试**

```python
def test_low_confidence_region_keeps_base_color(low_confidence_region):
    slots = apply_face_layers(np.zeros(10, dtype=np.uint8), [low_confidence_region], {})
    assert np.all(slots == 0)
```

- [ ] **Step 3: 写人工 override 最后生效测试**

```python
def test_manual_override_wins_over_accepted_region(accepted_region):
    slots = apply_face_layers(np.zeros(10, dtype=np.uint8), [accepted_region], {2: 7})
    assert slots[2] == 7
```

- [ ] **Step 4: 实现统一决策与应用层**

置信度由视角支持数、颜色方差、可见像素和边缘一致性组成；低于 0.75 或只有一个弱视角的候选保持 `accepted=False`。应用顺序固定为 base→accepted→manual override。

- [ ] **Step 5: 运行测试并提交**

Run: `pytest tests/test_face_candidates.py -v`
Expected: PASS。

```bash
git add backend/face_candidates.py tests/test_face_candidates.py
git commit -m "feat: 添加件内候选全局决策"
```

---

### Task 6: R2 API 与会话状态

**Files:**
- Modify: `backend/api/routes.py`
- Modify: `backend/meshpack.py`
- Modify: `tests/test_autocolor.py`

**Interfaces:**
- `POST /api/sessions/{sid}/phase2/collect`：启动同步候选收集与决策，返回 summary。
- `GET /api/sessions/{sid}/phase2`：返回状态、候选区域和旗帜。
- `POST /api/sessions/{sid}/phase2/apply`：仅应用 `accepted=True` 区域。
- `DELETE /api/sessions/{sid}/phase2/auto`：清自动候选，保留人工 override。

- [ ] **Step 1: 写 API 状态测试**

```python
def test_phase2_collect_does_not_apply_candidates(client, ready_session):
    r = client.post(f"/api/sessions/{ready_session}/phase2/collect")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert all(p.get("face_slots") is None for p in get_session(ready_session)["parts"])
```

- [ ] **Step 2: 写清除自动候选保留人工 override 测试**

```python
def test_clear_auto_keeps_manual_overrides(client, phase2_session):
    client.delete(f"/api/sessions/{phase2_session}/phase2/auto")
    sess = get_session(phase2_session)
    assert sess["phase2_regions"] == []
    assert sess["face_overrides"]
```

- [ ] **Step 3: 运行失败测试**

Run: `pytest tests/test_autocolor.py -k phase2 -v`
Expected: FAIL，端点不存在。

- [ ] **Step 4: 实现状态和端点**

状态流转限定为 `idle→collecting→deciding→ready` 或 `failed`；异常时保存错误文本，件级颜色和人工 override 不变。

- [ ] **Step 5: 运行测试并提交**

Run: `pytest tests/test_autocolor.py -k phase2 -v`
Expected: PASS。

```bash
git add backend/api/routes.py backend/meshpack.py tests/test_autocolor.py
git commit -m "feat: 添加二期候选 API 与状态"
```

---

### Task 7: 前端候选摘要与只读预览

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/app.js`
- Modify: `frontend/src/viewer.js`

**Interfaces:**
- Consumes: `GET /phase2` 候选区域 JSON。
- Produces: 候选列表、置信度/支持视角/面数显示；预览不得修改后端 face_slots。

- [ ] **Step 1: 添加“发现件内颜色”按钮和候选摘要容器**

```html
<button class="btn" id="phase2collect">发现件内颜色</button>
<div id="phase2summary" aria-live="polite"></div>
```

- [ ] **Step 2: 实现候选获取与展示**

按 `flagged desc, confidence desc` 排序；显示代表色、面数、支持视角和“候选/已接受/需复核”状态。

- [ ] **Step 3: 实现只读候选预览**

`viewer.previewFaceRegion(partIndex, faceIndices, color)` 仅创建临时 color attribute；关闭预览后调用 `clearFaceColors` 恢复件级底色，不调用 `/facecolors`。

- [ ] **Step 4: 浏览器黑盒验证**

打开洛茜会话，点击“发现件内颜色”，确认候选摘要出现；选择候选只改变临时预览，刷新页面后件级颜色不变。

- [ ] **Step 5: 提交**

```bash
git add frontend/index.html frontend/src/app.js frontend/src/viewer.js
git commit -m "feat: 添加二期候选摘要与预览"
```

---

### Task 8: 3MF 件级基线和接受候选编码回归

**Files:**
- Modify: `backend/threemf_out.py`
- Create: `tests/test_threemf_phase2.py`

**Interfaces:**
- Consumes: part `base_color`、accepted face slots、manual face overrides。
- Produces: Bambu 项目 3MF；无 accepted/override 时不写 paint_color。

- [ ] **Step 1: 写纯件级不含 paint_color 测试**

```python
def test_object_only_color_has_no_face_paint(sample_parts):
    blob = write_project_3mf(sample_parts)
    assert b"paint_color=" not in blob
```

- [ ] **Step 2: 写接受候选才写 paint_color 测试**

```python
def test_only_accepted_faces_are_painted(sample_parts_with_phase2):
    blob = write_project_3mf(sample_parts_with_phase2)
    xml = read_part_xml(blob, 0)
    assert count_painted_triangles(xml) == 3
```

- [ ] **Step 3: 运行失败测试**

Run: `pytest tests/test_threemf_phase2.py -v`
Expected: FAIL。

- [ ] **Step 4: 实现导出合并层**

导出前按 base→accepted→manual override 生成最终 slot 数组；没有面级结果时 `face_slots=None`，不输出 paint_color。连续 RGB 颜色表按首次出现顺序稳定去重。

- [ ] **Step 5: 运行全套测试**

Run: `pytest -q`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/threemf_out.py tests/test_threemf_phase2.py
git commit -m "test: 回归二期 per-face 3MF 编码"
```

---

### Task 9: 洛茜回归与失败回退验收

**Files:**
- Modify: `README.md`
- Modify: `docs/实施说明_0911.md`

**Interfaces:**
- Consumes: R1/R2 API 和前端候选预览。
- Produces: 回归记录、已知限制和 Bambu Studio 手动验证步骤。

- [ ] **Step 1: 重建洛茜会话并运行 R1**

Run: `python demo_rossi.py`
Expected: 56 件加载完成，件级颜色无黑色污染、无旧 face_slots 残留。

- [ ] **Step 2: 运行 R2 候选收集**

调用 `POST /phase2/collect`，记录候选区域数量、自动接受数量、旗帜数量；确认收集阶段没有修改 face_slots。

- [ ] **Step 3: 验证顺序不变性**

用反向参考图顺序重建同一会话，比较候选 `(part_index, faces, color)`；预期等价。

- [ ] **Step 4: 验证回退**

清除自动候选并刷新页面；预期模型恢复件级纯色，人工 override 保持。

- [ ] **Step 5: 输出 3MF 并做结构验证**

检查 ZIP 内 `filament_colour`、对象 extruder 和 accepted face paint 数量；记录 Bambu Studio 实测仍需用户完成。

- [ ] **Step 6: 更新文档并提交**

```bash
git add README.md docs/实施说明_0911.md
git commit -m "docs: 记录重启方案 R1-R2 回归结果"
```
