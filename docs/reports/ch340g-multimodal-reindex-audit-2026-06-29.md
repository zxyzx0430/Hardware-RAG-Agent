# CH340G Multimodal 重新索引审计报告

> 生成时间：2026-06-29
> 执行人：Trae
> 文档：doc_id=`32abb79e-b509-4df6-a1bf-bb3fff3d59be`，KB=`kb-96eca485`

---

## 1. 执行摘要

按用户要求删除 ch340g 旧索引，使用当前代码重新跑 multimodal chunking，并审计 chunk 质量、页码标记、表格完整性和边界。最终索引结果满足用户核心要求：

- **p5/p6 image_description 已出现** ✓
- **Markdown 表格完整保留** ✓
- **page_start 覆盖全部 14 页** ✓

---

## 2. 修复内容

### 2.1 页码标记丢失 bug（关键修复）

**现象**：重新索引后 text chunk 的 `page_start` 几乎全部等于 1（80/114），即使内容明显来自 p4/p5/p12 等页面。

**根因**：
1. `RecursiveCharacterTextSplitter` 会把 `<!-- PAGE:N -->` 标记从中间切断（如 `<!-- PAGE` 和 `:3 -->` 分到两个 chunk）。
2. `_build_chunks()` 调用 `parse_page_index(sub_text)`，但该函数在**无标记时默认返回 `[(1, 0, len(text))]`**，不是空列表。
3. 代码把"返回列表非空"当作"有有效标记"，于是所有子 chunk 的 `sub_page_nums = [1]`，page_start 被锁定为 1。

**修复**（[backend/src/rag/chunking/multimodal_chunker.py](file:///E:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py)）：
1. 在 sub-split 前用 `PAGE_MARKER_RE.sub(_stash, ...)` 把页码标记替换为占位符，防止被切断。
2. 子 chunk 恢复占位符后，改用 `PAGE_MARKER_RE.findall(sub_text)` 直接检查真实标记存在性。
3. 无真实标记时回退到 section 级 `start_page`/`end_page`（LLM 返回的 section 边界），避免默认 page=1。
4. 补导 `PAGE_MARKER_RE`（第一次修复漏了导入，已修正）。

**验证**：
- hybrid chunker 快速验证：page_start 分布覆盖 1-14 全部页面。
- pytest `tests/test_chunking.py`：10 passed。

---

## 3. 重新索引过程

```powershell
cd E:\Desktop\agent\backend
python ..\scripts\reindex_ch340g.py
```

- 模型：`oc/mimo-v2.5`（KB 级配置）
- 耗时：~773 秒
- 结果：90 chunks（80 text + 10 image_description）
- doc_id：`32abb79e-b509-4df6-a1bf-bb3fff3d59be`
- 清理：删除 ChromaDB/SQLite 中所有旧 ch340g 记录（2 docs / 190 chunks）

> 注：索引过程中 1/14 batches 因模型返回 VISION_NOT_SUPPORTED 失败，但 chunker 的容错机制继续处理剩余批次完成索引。p10/p11 为英文版 CH340 数据手册的纯文本页（`page.get_images() == 0`），因此没有 image_description 是合理的。

---

## 4. chunk 质量审计

审计脚本：[scripts/audit_ch340g_v2.py](file:///E:/Desktop/agent/scripts/audit_ch340g_v2.py)

### 4.1 image_description 覆盖

| source_page | section_title | 状态 |
|-------------|---------------|------|
| p3 | 3. Specifications | ✓ |
| p4 | 4. Pinout | ✓ |
| **p5** | **5.1. Example: USB RS232 adapter** | **✓** |
| **p6** | **5.2. Example: Optically isolated USB to UART adapter** | **✓** |
| p7 | 1 Introduction | ✓ |
| p8 | 2 Features | ✓ |
| p9 | 5. Function Description | ✓ |
| p12 | Parameter | ✓ |
| p13 | 7.1.2 USB to RS232 Converter Configuration using CH340B | ✓ |
| p14 | 7.3 USB to RS232 Converter Configuration (Simplified) | ✓ |

**p5 描述摘要**：USB RS232 适配器电路图，包含 CH340G 芯片文本特性说明、UART 参数、FIFO 缓冲区、通信能力等。

**p6 描述摘要**：光隔离 USB 转 UART 适配器电路原理图，分 USB 接口模块、CH340G 模块、PC817 光耦隔离模块、74LVC1G06 缓冲模块、外部接口模块，实现电气隔离通信。

### 4.2 页码范围（page_start / page_end）

- page_start 分布：`{1:6, 2:2, 3:2, 4:10, 5:4, 6:2, 7:5, 8:8, 9:8, 10:1, 11:5, 12:9, 13:12, 14:6}`
- page_end 分布：`{1:6, 2:2, 3:2, 4:5, 5:9, 6:2, 7:3, 8:6, 9:6, 10:7, 11:1, 12:13, 13:12, 14:6}`
- 覆盖页数：1-14 全部覆盖
- 状态：**✓ PASS**

> 注：最终 chunk 文本中不保留 `<!-- PAGE:N -->` 标记（被 `strip_page_markers()` 移除），这是设计行为。审计改为检查 `page_start` 准确性。

### 4.3 Markdown 表格完整性

- 含表格的 chunks：16 个
- 表格总行数：289 行
- 关键表格示例：
  - p3 Specifications：`|Symbol|Name|Minimum|Maximum|Unit|`
  - p4 Pinout：`|Pin #|Name|Direction|Comment|`
  - p8 Package：`|Package shape|Width of plastic|...|Pitch of Pin|...|Instruction of package|Ordering type|`
  - p9 Function Description：`|Col1|Col2|Col3|Col4|Col5|pull-up resistor|`
  - p12 Parameter：`|Name|Parameter Description|Min.|Typical|Max.|Units|`

状态：**✓ PASS — 表格以 Markdown 形式完整保留，未被 RecursiveCharacterTextSplitter 切碎。**

### 4.4 页面覆盖

- text chunks 覆盖：1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14
- 无缺失页面
- 状态：**✓ PASS**

### 4.5 chunk 边界质量

| 指标 | 数值 | 说明 |
|------|------|------|
| Broken/split 页码标记 | 0 | 占位符保护生效 |
| 小写开头的 chunk | 20 | 多为列表项/项目符号续行，可接受 |
| 无句末标点的 chunk | 59 | 多为列表/表格续段，可接受 |
| 最小 chunk | 211 chars | 合理 |
| 最大 chunk | 2680 chars | 合理 |
| 平均 chunk | 1055 chars | 合理 |
| <100 chars | 0 | 无碎片化 |
| 100-300 chars | 6 | 少量短 chunk |
| ≥300 chars | 74 | 主体 |

状态：**✓ PASS — 无异常碎片化，边界质量可接受。**

---

## 5. 问题与说明

### 5.1 p10/p11 无 image_description（非 bug）

p10/p11 来自英文版 CH340 数据手册的纯文本页，`page.get_images() == 0`，且页面无表格/图片/电路图。因此不生成 image_description 是正确的，符合 `_needs_image_description()` 的判定逻辑。

### 5.2 索引过程中 1/14 batch 失败

失败原因：`VISION_NOT_SUPPORTED`（模型 `oc/mimo-v2.5` 在部分请求中返回 multimodal 数据无法处理）。chunker 的容错机制已处理：失败 batch 被跳过，其余 13/14 batches 成功完成索引。这导致该失败 batch 对应的页面可能缺少 image_description，但 p5/p6 不在失败批次中，因此不影响用户核心要求。

---

## 6. 结论

| 检查项 | 结果 |
|--------|------|
| p5/p6 image_description | ✓ 通过 |
| Markdown 表格完整保留 | ✓ 通过 |
| page_start 覆盖 1-14 页 | ✓ 通过 |
| 全页面覆盖 | ✓ 通过 |
| 无 broken 页码标记 | ✓ 通过 |
| chunk 边界合理 | ✓ 通过 |

**ch340g multimodal 重新索引成功，chunk 质量满足要求。**

---

## 7. 后续建议

1. **模型稳定性**：`oc/mimo-v2.5` 偶发 VISION_NOT_SUPPORTED，若需更稳定的 multimodal chunking，可考虑切换为明确支持 vision 的模型（如 gpt-4o / gpt-4o-mini）。
2. **审计脚本复用**：`scripts/audit_ch340g_v2.py` 可泛化为通用 multimodal chunk 审计工具，支持任意 doc_id 前缀。
3. **页码保护推广**：本次页码标记保护逻辑已在 multimodal_chunker 中修复，可检查 agent_chunker / hybrid_chunker 是否也需要类似保护（当前二者未使用 `<!-- PAGE:N -->` 标记，暂不需要）。
