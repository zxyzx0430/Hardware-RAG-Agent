# Tasks

## P0 数据层问题

- [x] Task 1: 重复入库根因定位
  - [x] SubTask 1.1: 编写复现脚本 `scripts/audit/repro_dup.py`，对 ch340g PDF 跑 multimodal chunker（dry-run，不入库），统计生成 chunks 的 (fingerprint, section_title) 分布
  - [x] SubTask 1.2: 定位根因——是 `get_text_for_page_range` 按 page_range 重叠提取导致相邻 section 共享文本，还是 chunk 生成逻辑本身重复分配
  - [x] SubTask 1.3: 写根因定位报告到 `scripts/audit/dup_root_cause.md`

- [x] Task 2: 重复入库修复
  - [x] SubTask 2.1: 修复 section 边界重叠——`multimodal_chunker._build_chunks` 引入 `assigned_pages: set[int]` 去重分配（每页只给第一个声明它的 section）
  - [x] SubTask 2.2: 在 `kb_manager.ingest_chunks` 入库前添加 `(fingerprint, section_title)` 复合键去重（兜底）
  - [x] SubTask 2.3: agent_chunker 同步采用复合键去重；hybrid 不受影响（无 LLM section 边界问题）

- [x] Task 3: 消除纯文本表格与 markdown 表格并存
  - [x] SubTask 3.1: 在 `document_processor._parse_pymupdf_per_page` 中，新增 `_extract_text_excluding_tables` 用 bbox 排除表格区域文本
  - [x] SubTask 3.2: 验证 ch340g 表格 chunk 只剩 markdown 版，无纯文本碎片版

## HIGH 代码问题

- [x] Task 4: UnifiedPdfParser 添加 page.get_images() 检测
  - [x] SubTask 4.1: 在 `_parse_pymupdf_per_page` 中调用 `page.get_images()`，将图片数量以 `<!-- IMAGES:N -->` 标记写入页面文本头部
  - [x] SubTask 4.2: 验证 hybrid/agent chunker 能读取该标记

- [x] Task 5: prefer_docling / Fallback 路径页码标记处理
  - [x] SubTask 5.1: 在 `UnifiedPdfParser.parse()` 的 prefer_docling / Fallback 路径中，若无法按页切分，返回 `total_pages=0`
  - [x] SubTask 5.2: 验证 agent_chunker 收到 `total_pages=0` 时触发 synthetic page marker 路径

- [x] Task 6: multimodal_chunker is_whole_code_block 检测修复
  - [x] SubTask 6.1: 在 `_build_chunks` 检测 `is_whole_code_block` 前先 `strip_page_markers(section_text).strip()`
  - [x] SubTask 6.2: 验证 whole code block 快速路径能正确触发

- [x] Task 7: agent_chunker batch_text 硬截断修复
  - [x] SubTask 7.1: 在 `_split_oversized_batch` 中，新增 `_truncate_at_boundary` 回溯到最近的 `\n\n` 或 `. ` 再截断
  - [x] SubTask 7.2: 验证截断后的 batch_text 仍包含完整段落/句子（TestTruncateAtBoundary 4 个测试）

- [x] Task 8: agent_chunker fingerprint 去重修复
  - [x] SubTask 8.1: 将 fingerprint 去重键改为 `(fingerprint, section_title)` 复合键
  - [x] SubTask 8.2: 验证不同 section 的同文本 chunk 不再被误删（CASE 3: 21 → 61 chunks）

## MEDIUM 代码问题

- [x] Task 9: 表格 MD 按阅读顺序插入
  - [x] SubTask 9.1: 在 `_parse_pymupdf_per_page` 中，用 `_extract_text_excluding_tables` 排除表格区域文本，markdown 表格追加到页面文本（无重复）
  - [x] SubTask 9.2: 验证表格不再与纯文本碎片版并存

- [x] Task 10: base.py 正则覆盖修复
  - [x] SubTask 10.1: `_TABLE_ROW_RE` 放宽为 `r"(?:^|\n)(\|(?:[^\n]*\|)+)(?=\n|$)"`，允许单列表格 + 文本末尾表格
  - [x] SubTask 10.2: `_REGISTER_FIELD_RE` 增加寄存器特征约束（0x 前缀 OR BIT/REG/ADDR/MASK 关键词）
  - [x] SubTask 10.3: 新增单列表格 + 文本末尾表格 + 误匹配编号列表的单元测试（TestTableRowRegex / TestRegisterFieldRegex）

- [x] Task 11: base.py parse_page_index 默认行为修改
  - [x] SubTask 11.1: `parse_page_index()` 无 marker 时返回空列表 `[]`（BREAKING）
  - [x] SubTask 11.2: 更新所有调用方（agent_chunker, hybrid_chunker, multimodal_chunker, get_text_for_page_range, get_page_for_char）显式处理空列表情况
  - [x] SubTask 11.3: 验证无 marker 文档不再被默认为 page 1（TestParsePageIndex 3 个测试）

- [x] Task 12: multimodal_chunker 跨页表格合并
  - [x] SubTask 12.1: 新增 `_merge_cross_page_tables` 函数 + `_CROSS_PAGE_TABLE_RE`，合并相邻表格占位符
  - [x] SubTask 12.2: 验证跨页表格以完整形式保留在单一 chunk 中（TestMergeCrossPageTables 3 个测试）

- [x] Task 13: cross-section tiny merge 收紧（三个 chunker）
  - [x] SubTask 13.1: multimodal_chunker `_merge_tiny_chunks` Pass 2 阈值收紧到 `small_chunk_size // 2`
  - [x] SubTask 13.2: hybrid_chunker 同步修改
  - [x] SubTask 13.3: agent_chunker 添加 cross-section merge 逻辑（仅 orphan paragraph）

- [x] Task 14: hybrid/agent chunker 保留页码标记到 sub-split 后
  - [x] SubTask 14.1: hybrid_chunker 保留标记到 small_chunk 切分完成，per-small-chunk 提取页码
  - [x] SubTask 14.2: agent_chunker 保留标记到 sub-split 完成，per-sub-chunk 提取页码
  - [x] SubTask 14.3: 验证跨多页 section 的 sub-chunk 的 page_start/page_end 精确到具体页

## LOW 代码问题

- [x] Task 15: _needs_image_description 魔法数字提取
  - [x] SubTask 15.1: 将 `avg_text < 300` 提取为 `low_text_density_threshold` 构造函数参数（默认 300）

- [x] Task 16: find_tables 校验
  - [x] SubTask 16.1: 在 `_extract_tables_markdown` 中对每个表格校验有效数据行 ≥ 2
  - [x] SubTask 16.2: 验证 ch340g p6 电路图 ASCII 文本不再被误检为表格

## 验证

- [x] Task 17: 三个案例重新索引 + 审计
  - [x] SubTask 17.1: 重新索引 ch340g (multimodal)、STM32 GPIO (hybrid)、06-chaotic-embedded-notes (agent)
  - [x] SubTask 17.2: 跑 `scripts/audit_all_cases.py` 审计三个案例
    - CASE 1 ch340g: 48 chunks (36 text + 12 img)，重复率 0%，p5/p6 齐全，page 1-14 全覆盖
    - CASE 2 STM32 GPIO: 203 chunks，重复率 0%，58 个表格 chunks
    - CASE 3 06-chaotic: 61 chunks，重复率 0%
  - [x] SubTask 17.3: 生成最终审计报告 `docs/reports/chunk-integrity-fullchain-fix-audit-2026-06-29.md`

- [x] Task 18: 单元测试
  - [x] SubTask 18.1: `tests/test_chunking.py` 全部通过（10 → 29 测试）
  - [x] SubTask 18.2: 新增 19 个针对修复点的测试用例

## Task Dependencies

- Task 2 依赖 Task 1（根因定位后才能修）✓
- Task 3 依赖 Task 9（表格按阅读顺序插入后才能消除纯文本版）✓
- Task 17 依赖 Task 2-16 全部完成 ✓
- Task 18 依赖 Task 2-16 全部完成 ✓
- Task 11 (parse_page_index BREAKING) 必须在 Task 14 之前完成 ✓
- Task 13 (cross-section merge 三个 chunker) 三个 SubTask 已并行完成 ✓
- Task 14 (hybrid/agent 页码标记保留) 两个 SubTask 已并行完成 ✓
