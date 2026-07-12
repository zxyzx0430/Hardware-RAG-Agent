# Checklist

## P0 数据层问题

- [x] 重复入库根因定位报告 `scripts/audit/dup_root_cause.md` 已写明根因（section 边界重叠 / chunk 生成重复分配）
- [x] ch340g 重新索引后重复率 < 5%（修复前 56% → 修复后 **0.0%**）
- [x] STM32 GPIO 重新索引后重复率 < 5%（**0.0%**）
- [x] 06-chaotic-embedded-notes 重新索引后重复率 < 5%（**0.0%**）
- [x] `kb_manager.ingest_chunks` 入库前有 `(fingerprint, section_title)` 复合键去重逻辑
- [x] ch340g p3/p4/p12 的表格 chunk 只剩 markdown 版，无纯文本碎片版（`_extract_text_excluding_tables` 用 bbox 排除表格区域文本）
- [x] 同一表格不再同时存在"切碎纯文本版"和"完整 markdown 版"两种格式

## HIGH 代码问题

- [x] `UnifiedPdfParser._parse_pymupdf_per_page` 调用 `page.get_images()`，页面文本头部含 `<!-- IMAGES:N -->` 标记
- [x] `UnifiedPdfParser.parse()` 的 prefer_docling / Fallback 路径返回 `total_pages=0`（当无法按页切分时）
- [x] agent_chunker 收到 `total_pages=0` 时触发 synthetic page marker 路径
- [x] `multimodal_chunker._build_chunks` 检测 `is_whole_code_block` 前调用 `strip_page_markers(section_text).strip()`
- [x] whole code block 快速路径能正确触发（含 ``` 代码块的 section 不被 sub-split）
- [x] `agent_chunker._split_oversized_batch` 截断时回溯到 `\n\n` 或 `. ` 边界，不再硬截断（`_truncate_at_boundary`）
- [x] agent_chunker fingerprint 去重键改为 `(fingerprint, section_title)` 复合键
- [x] 不同 section 的同文本 chunk 不再被误删（CASE 3: 21 → 61 chunks）

## MEDIUM 代码问题

- [x] 表格 MD 按阅读顺序插入（用 bbox 排除表格区域文本，避免与 markdown 版重复）
- [x] `_TABLE_ROW_RE` 能匹配单列表格 `| value |`
- [x] `_TABLE_ROW_RE` 能匹配文本末尾表格（无尾随换行）
- [x] `_REGISTER_FIELD_RE` 不再误匹配普通编号列表（如 `01 1 第一章`）
- [x] `_REGISTER_FIELD_RE` 仍能匹配真实寄存器字段块（含 BIT/REG/ADDR/MASK 关键词）
- [x] `parse_page_index()` 无 marker 时返回空列表 `[]`（BREAKING）
- [x] agent_chunker 显式处理 `parse_page_index` 返回空列表的情况
- [x] hybrid_chunker 显式处理 `parse_page_index` 返回空列表的情况
- [x] multimodal_chunker 显式处理 `parse_page_index` 返回空列表的情况
- [x] 无 marker 文档不再被默认为 page 1
- [x] multimodal_chunker 跨页表格合并——表格行之间的页码标记占位符被删除（`_merge_cross_page_tables`）
- [x] 跨页表格以完整形式保留在单一 chunk 中
- [x] multimodal_chunker `_merge_tiny_chunks` Pass 2 cross-section 阈值收紧到 `small_chunk_size // 2`
- [x] hybrid_chunker `_merge_tiny_chunks` Pass 2 同上
- [x] agent_chunker `_merge_tiny_chunks` Pass 2 添加 cross-section merge（仅 orphan paragraph）
- [x] 三个 chunker 的 cross-section merge 行为一致
- [x] hybrid_chunker 保留页码标记到 small_chunk 切分完成
- [x] agent_chunker 保留页码标记到 sub-split 完成
- [x] 跨多页 section 的 sub-chunk 的 page_start/page_end 精确到具体页（CASE 1 page_start 分布覆盖 1-14）

## LOW 代码问题

- [x] `multimodal_chunker._needs_image_description` 的 `avg_text < 300` 提取为 `low_text_density_threshold` 构造函数参数
- [x] `low_text_density_threshold` 可通过构造函数配置
- [x] `document_processor._extract_tables_markdown` 对每个表格校验有效数据行 ≥ 2
- [x] ch340g p6 电路图 ASCII 文本不再被误检为表格（无 `|Col1|R5|Col3|` 垃圾 markdown）

## 验证

- [~] ch340g (multimodal) 重新索引后审计：重复率 < 5% ✓、表格完整性 ≥ 95% ✓、mid-sentence 切断率 < 10% ✗（25%，已记为 deferred 优化）、page_start 覆盖 1-14 ✓
- [x] STM32 GPIO (hybrid) 重新索引后审计：重复率 < 5% ✓、表格完整性 ≥ 95% ✓、mid-sentence 切断率 < 10% ✓（0%）
- [x] 06-chaotic-embedded-notes (agent) 重新索引后审计：重复率 < 5% ✓、mid-sentence 切断率 < 10% ✓（0%）
- [x] `tests/test_chunking.py` 全部通过（10 → 29 测试全部 PASS）
- [x] 新增测试用例覆盖：单列表格保护、文本末尾表格保护、跨页表格合并、fingerprint 复合键去重、batch 边界对齐截断、parse_page_index 空 marker、_REGISTER_FIELD_RE 不误匹配编号列表
- [x] 最终审计报告 `docs/reports/chunk-integrity-fullchain-fix-audit-2026-06-29.md` 已生成

## 文档更新

- [x] `docs/pitfalls.md` 追加本次修复的踩坑记录（section 边界重叠 / parse_page_index BREAKING）
- [x] `docs/completed.md` 追加本次修复的完成记录

## 备注

- `[~]` 表示部分达标：CASE 1 mid-sentence start rate 25% > 10% target。根因是 ch340g pinout 章节为密集短行（`"5\nUD+\nAnalog\nUSB D+ signal."`），`RecursiveCharacterTextSplitter` 降级到空格切。sub-chunk overlap=200 提供上下文兜底，检索质量不受影响。详见审计报告 "Deferred Optimizations" 章节。
