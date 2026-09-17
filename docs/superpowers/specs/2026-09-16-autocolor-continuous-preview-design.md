# AutoColor 连续 RGB 视觉闭环设计

## 目标

解决“后台识别到颜色，但 3D 模型仍大面积默认灰”的问题。视觉预览与打印槽位彻底分离：

- **视觉预览**表达算法识别出的连续 RGB，优先保证模型颜色完整、便于判断质量。
- **打印输出**再把用户选择的区域映射到 Bambu/Orca 可用的有限耗材槽。

本轮只完成视觉闭环与状态一致性；不在本轮自动解决 16 个打印槽的区域选择策略。

## 当前根因

1. R1 在 `views_disagree_hard` 时把已经算出的最佳颜色丢弃，回退到旧色；模型初始旧色通常是默认灰 `#8A939E`。
2. `no_visible_pixels` 没有利用同前缀/左右对称件的可信颜色，仍回退默认灰。
3. R2 只有候选收集和只读单区域预览，没有把全部已接受区域合成到模型画布。
4. R2 `face_slots` 受 uint8 255 槽和 Bambu 16 耗材槽限制，不适合作为完整 RGB 预览的数据源。
5. `DELETE /phase2/auto` 清除了后端状态，但前端摘要与画布没有同步清理，造成界面显示陈旧结果。

## 总体架构

```text
R1 件级最佳颜色（连续 RGB + 置信/原因）
                 │
                 ▼
R2 已接受区域（连续 RGB）
                 │
                 ▼
人工 face override（连续 RGB，最后生效）
                 │
                 ├── 完整颜色预览：dense face RGB，不受耗材槽限制
                 │
                 └── 打印映射：用户选择区域 → ≤16 耗材槽 → face_slots → 3MF
```

完整预览不写 `face_slots`，打印映射不改变算法保存的连续 RGB 候选。

## R1 件级颜色纪律

### 有颜色证据

只要 `per_part[pi]` 非空，就始终返回计算得到的加权 Lab 中位色：

- 普通结果：`hex=best_hex`，按现有置信度规则决定是否旗帜。
- `views_disagree_hard`：仍为 `hex=best_hex`，但 `flagged=true`、`reason=views_disagree_hard`、`conf=0`。

严重分歧表示“颜色需要复核”，不再表示“隐藏最佳估计并显示灰色”。

### 完全无颜色证据

按以下确定性顺序回退：

1. 人工件级 override。
2. 已有且不是默认灰的件色。
3. 同组可信件颜色：复用现有前缀/左右对称分组规则；只采用有证据、不是 `views_disagree_hard` 的组员，按可信像素数加权 Lab 中位。
4. 无可用回退时保留默认灰，并标记 `no_visible_pixels`。

不在本轮新增 VLM 或件名语义模型，避免扩展范围。

## R2 连续 RGB 预览

### 后端合成

新增纯函数：

```python
compose_face_rgb(face_count, base_hex, accepted_regions, face_overrides) -> np.ndarray[uint8]
```

合成顺序固定：

```text
件级 base RGB → accepted region RGB → manual face override RGB
```

同一面被多个已接受区域命中时，使用稳定优先级：

1. 人工 override 永远最高。
2. 自动区域按 `confidence desc, support_views desc, region_id asc` 排序；高优先级先写，后续不得覆盖。

### 二进制预览接口

新增：

```text
GET /api/sessions/{sid}/phase2/preview/{part_index}
```

响应体为每面 3 字节 RGB：

```text
[R0,G0,B0, R1,G1,B1, ...]
```

响应头：

- `X-Face-Count`
- `X-Phase2-Revision`
- `Content-Type: application/octet-stream`

一面只传 3 字节；洛茜 882 万面总数据约 26.5MB。前端展开为每顶点 9 字节 Uint8 归一化颜色，避免 Float32 颜色属性约 317MB 的额外内存。

### 状态与恢复

会话维护 `phase2_revision`：collect、人工 override、清除候选时递增。预览接口按当前状态即时合成，不保存第二份巨型 RGB 数组。

页面通过 `?sid=` 恢复时：

- `phase2_status == ready`：自动恢复完整预览。
- `phase2_status == idle`：只显示 R1 件级颜色，摘要为空。

## 前端行为

### 收集完成

“发现件内颜色”成功后：

1. 拉取 phase2 摘要。
2. 自动为存在已接受区域的件加载二进制连续 RGB 预览。
3. 切换到“完整颜色”视图。
4. 不调用 `/phase2/apply`，不写打印 face_slots。

### 视图模式

增加分段控制：

- **完整颜色**：R1 base + R2 accepted + manual override，默认模式。
- **件级颜色**：仅显示 R1 结果，用于诊断 R2 是否改善。
- **打印槽**：仅在存在 face_slots 时可用，显示实际 3MF 槽映射结果。

### 候选摘要

不再一次创建 14487 个 DOM 行。顶层按件显示：

```text
件名 | 候选数 | 接受数 | 待复核数 | 预览色块
```

展开单件后分页显示区域，每页 50 条。点击区域仍可临时高亮，退出高亮后恢复完整 RGB 预览，而不是恢复纯件色。

### 清除自动候选

新增明确的“清除自动候选”命令；完成后同步：

- 调用 `DELETE /phase2/auto`
- 清空摘要
- 删除连续 RGB color attribute
- 恢复 R1 件级颜色
- `state.faceSlots` 不被当作连续预览来源

## 打印层边界

现有 `/phase2/apply` 与 `face_slots` 保留，但定义为打印映射操作：

- 不自动调用。
- 不作为完整预览的数据源。
- 后续单独增加“选择打印区域并映射到 ≤16 槽”的 UI。
- 当前 255 槽 Lab 吸附作为防溢出保护继续保留；3MF 仍只编码 Bambu 可识别的前 16 槽。

## 错误处理

- 单件预览接口失败：保留该件 R1 base 色，摘要标记“预览加载失败”，其他件继续加载。
- 面数与响应字节数不一致：前端拒绝应用该颜色属性并记录错误。
- phase2 revision 在加载过程中变化：放弃旧响应并重新加载。
- 内存压力：逐件串行加载预览，每完成一件立即释放临时缓冲引用。

## 测试

### 后端

1. `views_disagree_hard` 返回最佳估计色而非默认灰。
2. 有证据件的结果不得是默认灰，除非最佳估计本身就是该 RGB。
3. 无证据件采用同组可信颜色；无可信组员才保留灰色。
4. `compose_face_rgb` 严格按 base → accepted → override 合成。
5. 多区域重叠时按稳定优先级决定颜色。
6. preview 响应长度等于 `face_count * 3`，且不修改 face_slots。
7. clear auto 后 preview 回到纯 base 色。

### 前端与浏览器验收

1. 收集完成后自动出现连续 RGB 面级颜色，不需要再点应用。
2. 洛茜所有有证据件默认灰数量为 0。
3. 有已接受区域的件必须具有归一化 Uint8 color attribute。
4. “完整颜色/件级颜色/打印槽”切换结果符合各自数据源。
5. 点击候选临时高亮，再次点击恢复完整预览。
6. 清除自动候选后摘要和画布同时恢复，不留陈旧状态。
7. 页面刷新后通过 `?sid=` 恢复完整颜色预览。

## 完成标准

- 用户当前指出的大片默认灰显著消失；有证据件不再因低置信而退回默认灰。
- R2 接受区域在画布中自动、完整显示，并与打印槽数量无关。
- 前端状态与后端 phase2 状态一致，不再出现摘要仍在但面色已清的情况。
- 现有打印导出与 36 个测试不回归；新增视觉闭环测试全部通过。
