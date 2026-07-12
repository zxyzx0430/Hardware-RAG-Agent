# Chunk 全链路完整性修复 Spec

## Why

通过 3 个 subagent 对全链路代码 + 已入库 chunk 数据的并行审计，发现 chunk 质量存在系统性问题：

- **数据层 P0**：ch340g 案例中 56% 的 text chunk 是重复入库（同一内容块被分配到 2-5 个 section_title 下）；同一表格同时以"切碎纯文本版"和"完整 markdown 版"两种格式存在。
- **代码层**：17 个问题（5 HIGH / 10 MEDIUM / 2 LOW），涵盖内容丢失、边界切断、语义割裂三类。
- **案例覆盖**：ch340g (multimodal) + STM32 GPIO (hybrid) + 06-chaotic-embedded-notes (agent) 三种 chunker 各一例。

不修复会导致：检索召回率虚高（重复 chunk）、表格信息丢失、chunk 边界语义断裂、跨 chunk 上下文错乱。

## What Changes

### P0 数据层问题（必须修复）

- **重复入库根因定位 + 修复**：先复现 + 定位是 section 边界重叠导致 `get_text_for_page_range` 返回重叠文本，还是 chunk 生成逻辑本身重复分配，再修去重逻辑。
- **消除纯文本表格与 markdown 表格并存**：text chunk 中的表格统一转为 markdown 格式（已有 `find_tables()` 产出），删除纯文本碎片版。

### HIGH 代码问题（5 个）

- **document_processor.py**：`UnifiedPdfParser` 添加 `page.get_images()` 检测，将图片数量信息传给下游 chunker 决策。
- **document_processor.py**：`prefer_docling` / Fallback 路径返回无页码标记的 text 时，返回 `total_pages=0` 触发 agent_chunker 的 synthetic page marker 路径，或显式告知下游"无页码标记"。
- **multimodal_chunker.py**：`is_whole_code_block` 检测前先 `strip_page_markers(section_text).strip()`，与 agent_chunker 保持一致。
- **agent_chunker.py**：`batch_text` 硬截断改为边界对齐截断（向前回溯到 `\n\n` 或 `. `），不再丢弃末尾内容。
- **agent_chunker.py**：fingerprint 去重改为复合键 `(fingerprint, section_title)`，或只在同一 section 内去重，避免误删不同 section 的 chunk。

### MEDIUM 代码问题（10 个）

- **document_processor.py**：表格 MD 按阅读顺序插入（用 `page.get_text("dict")` + bbox 合并排序），不再追加末尾。
- **base.py**：`_TABLE_ROW_RE` 放宽为 `r"(?:^|\n)(\|(?:[^\n]*\|)+\n)"`，允许单列表格；结尾改 `(?=\n|$)` lookahead，允许文本末尾表格。
- **base.py**：`_REGISTER_FIELD_RE` 增加寄存器特征约束（行首 `0x` 前缀，或字段名含 BIT/REG/ADDR/MASK 关键词），避免误匹配普通编号列表。
- **base.py**：`parse_page_index()` 无 marker 时返回空列表 `[]`，让调用方显式处理，不再默认 `[(1, 0, len)]`。
- **multimodal_chunker.py**：跨页表格合并——在 protect_structures 之后、sub_split 之前，合并相邻的表格占位符（删除表格行之间的页码标记占位符）。
- **multimodal_chunker.py**：`_merge_tiny_chunks` Pass 2 的 cross-section merge 收紧——对有明确 section_title 的 chunk 禁止 cross-section merge，仅允许空 section_title 的 orphan paragraph 跨 section。
- **hybrid_chunker.py**：保留页码标记到 small_chunk 切分完成，然后从每个 small_chunk 用 `PAGE_MARKER_RE.findall` 提取页码（参考 multimodal_chunker.py L1278-1285）。
- **hybrid_chunker.py**：同 multimodal_chunker，cross-section tiny merge 收紧。
- **agent_chunker.py**：保留页码标记到 sub-split 完成，per-sub-chunk 提取页码。
- **agent_chunker.py**：Pass 2 添加 cross-section merge 逻辑，与 hybrid/multimodal 保持一致（仅 orphan paragraph）。

### LOW 代码问题（2 个）

- **multimodal_chunker.py**：`_needs_image_description` 的 `avg_text < 300` 提取为命名常量 `LOW_TEXT_DENSITY_THRESHOLD`，允许构造函数配置。
- **document_processor.py**：`find_tables()` 增加校验——表格至少需有 2 行有效数据行，排除电路图 ASCII 文本误检（对应 ch340g chunk 25 的 `|Col1|R5|Col3|` 垃圾表格）。

### 验证

- 三个案例（ch340g / STM32 GPIO / 06-chaotic-embedded-notes）重新索引后审计：重复率 < 5%、表格完整性 ≥ 95%、mid-sentence 切断率 < 10%。
- 单元测试：`tests/test_chunking.py` 全部通过 + 新增针对上述修复的测试用例。

## Impact

- **Affected specs**: chunking 模块、document_processor 模块、RAG 检索质量
- **Affected code**:
  - `backend/src/rag/document_processor.py` — 图片检测、页码标记、表格阅读顺序、find_tables 校验
  - `backend/src/rag/chunking/base.py` — 正则覆盖、parse_page_index 默认行为
  - `backend/src/rag/chunking/multimodal_chunker.py` — is_whole_code_block、跨页表格、cross-section merge、魔法数字
  - `backend/src/rag/chunking/hybrid_chunker.py` — 页码标记保留、cross-section merge
  - `backend/src/rag/chunking/agent_chunker.py` — 硬截断、fingerprint 去重、页码标记保留、cross-section merge
  - `backend/src/rag/kb_manager.py` — `ingest_chunks` 入库前 fingerprint 去重（如果根因定位后发现需要）
  - `tests/test_chunking.py` — 新增测试用例

## ADDED Requirements

### Requirement: Chunk 全局唯一性保证

系统 SHALL 在入库前对所有 chunk 按 `(fingerprint, section_title)` 复合键去重，确保同一内容块在同一 section 下只入库一次。跨 section 的重复内容块由 section 边界修复逻辑处理（不让相邻 section 共享文本）。

#### Scenario: 同一内容块被分配到多个 section

- **WHEN** chunk 生成逻辑将同一文本块分配到 2 个以上 section_title 下
- **THEN** 入库前去重逻辑保留每个 section_title 下的第一个实例，删除后续重复
- **AND** 重复率（重复 chunk 数 / 总 chunk 数）< 5%

#### Scenario: section 边界重叠导致文本重叠

- **WHEN** LLM 返回的相邻 section 的 page_range 重叠（如 section A: page 1-3, section B: page 2-4）
- **THEN** `get_text_for_page_range` 按 section 的 char offset 范围提取文本，而非按 page_range 重叠提取
- **AND** 相邻 section 不共享同一文本块

### Requirement: 表格完整性保证

系统 SHALL 保证 Markdown 表格在 chunk 中以完整形式存在（表头 + 分隔行 + 数据行），不被 `RecursiveCharacterTextSplitter` 切碎，也不与纯文本碎片版并存。

#### Scenario: 表格位于 section 文本末尾

- **WHEN** section 文本以表格结尾且无尾随换行
- **THEN** `protect_structures` 的 `_TABLE_ROW_RE` 仍能匹配并保护该表格
- **AND** 表格以完整 markdown 形式保留在最终 chunk 中

#### Scenario: 单列表格

- **WHEN** PDF 页面包含单列表格（如 `| value |`）
- **THEN** `protect_structures` 保护该表格不被切断

#### Scenario: 跨页表格

- **WHEN** 同一表格跨越多页（页码标记 `<!-- PAGE:N -->` 出现在表格行之间）
- **THEN** 跨页表格合并逻辑删除表格行之间的页码标记占位符
- **AND** 表格以完整形式保留在单一 chunk 中

#### Scenario: 电路图 ASCII 文本误检为表格

- **WHEN** `find_tables()` 将电路图 ASCII 文本误识别为表格（如 `|Col1|R5|Col3|` + 空单元格）
- **THEN** 校验逻辑拒绝该表格（有效数据行 < 2）
- **AND** 不生成垃圾 markdown 表格 chunk

### Requirement: 页码标记保留到 sub-split 后

系统 SHALL 在 hybrid_chunker 和 agent_chunker 中保留 `<!-- PAGE:N -->` 页码标记到 sub-split 完成，然后从每个 sub-chunk 用 `PAGE_MARKER_RE.findall` 提取页码，确保 chunk 级页码粒度精确。

#### Scenario: section 跨 5 页

- **WHEN** LLM 返回的 section 跨 5 页（page 1-5）
- **THEN** 该 section 的每个 sub-chunk 的 `page_start` / `page_end` 精确到具体页（如 chunk A: page 1-2, chunk B: page 3, chunk C: page 4-5）
- **AND** 不再所有 sub-chunk 都标记为 page 1-5

### Requirement: 图片检测信息传递

系统 SHALL 在 `UnifiedPdfParser` 中调用 `page.get_images()` 检测每页图片数量，并将信息以 `<!-- IMAGES:N -->` 标记写入页面文本头部，供下游 chunker 决策是否生成 image_description。

#### Scenario: hybrid/agent chunker 处理 PDF

- **WHEN** hybrid 或 agent chunker 处理图片密集页（如引脚图、封装图）
- **THEN** 页面文本头部包含 `<!-- IMAGES:N -->` 标记
- **AND** chunker 可基于该标记决定是否触发图片描述生成（或至少不把该页当作 tiny chunk 吞掉）

## MODIFIED Requirements

### Requirement: PDF 解析页码标记

`UnifiedPdfParser.parse()` SHALL 在所有返回路径（PyMuPDF per-page、prefer_docling、Fallback Docling、Fallback PyMuPDF whole-doc）中保持页码标记一致性：

- PyMuPDF per-page 路径：每页文本前插入 `<!-- PAGE:N -->`（现有行为）
- prefer_docling / Fallback 路径：若无法按页切分，返回 `total_pages=0` 触发 agent_chunker 的 synthetic page marker 路径，而非返回 `total_pages > 0` 但无标记导致 `parse_page_index` 默认 page=1

### Requirement: protect_structures 正则覆盖

`protect_structures` 的正则 SHALL 覆盖以下表格形态：

- 多列表格（现有行为）
- 单列表格（`| value |`）
- 文本末尾表格（无尾随换行）
- 寄存器字段块（含 BIT/REG/ADDR/MASK 关键词的连续行）

`_REGISTER_FIELD_RE` SHALL 增加寄存器特征约束，避免误匹配普通编号列表（如 `01 1 第一章`）。

### Requirement: parse_page_index 默认行为

`parse_page_index()` SHALL 在无页码标记时返回空列表 `[]`，让调用方显式处理无标记情况，而非默认 `[(1, 0, len)]` 导致所有文本被当作 page 1。

**BREAKING**：调用方（agent_chunker, hybrid_chunker, multimodal_chunker）需更新为显式处理空列表情况。

### Requirement: agent_chunker batch 截断

`agent_chunker._split_oversized_batch` SHALL 在 batch_text 超过 `max_batch_chars` 时向前回溯到最近的段落边界（`\n\n`）或句子边界（`. `）再截断，不再硬截断丢弃末尾内容。

### Requirement: multimodal_chunker is_whole_code_block 检测

`multimodal_chunker._build_chunks` SHALL 在检测 `is_whole_code_block` 前先 `strip_page_markers(section_text).strip()`，使 `section_text.startswith("```")` 能正确触发 whole code block 快速路径。

### Requirement: cross-section tiny merge 一致性

三个 chunker（hybrid / agent / multimodal）的 `_merge_tiny_chunks` Pass 2 SHALL 保持一致的 cross-section merge 行为：

- 对有明确 section_title 的 chunk 禁止 cross-section merge
- 仅允许空 section_title 的 orphan paragraph 跨 section merge
- cross-section 阈值收紧到 `small_chunk_size / 2`

### Requirement: find_tables 校验

`document_processor._extract_tables_markdown` SHALL 对 `page.find_tables()` 返回的每个表格校验：有效数据行（非空单元格）≥ 2 行才采纳，否则丢弃，避免电路图 ASCII 文本误检。

## REMOVED Requirements

### Requirement: parse_page_index 无 marker 默认 [(1, 0, len)]

**Reason**: 默认返回 `[(1, 0, len)]` 让调用方无法区分"真实单页文档"和"多页但无 marker"，导致 batch 切分和 page_range 计算错误。

**Migration**: 调用方显式处理空列表情况——agent_chunker 触发 synthetic page marker 路径，hybrid/multimodal chunker 回退到 section 级 page_range。
