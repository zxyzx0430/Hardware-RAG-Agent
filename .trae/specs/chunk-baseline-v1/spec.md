# Chunk 质量基准数据集建立 Spec

## Why

当前项目的 RAG 评估依赖历史 golden dataset 和临时测试数据，缺乏一个稳定、可复现、经过人工逐页验证的 chunk 质量基准。随着 chunker 策略持续迭代，需要一个永久基线来衡量 chunk 完整性、边界质量和检索效果，避免"用工具证明自己"的循环。

本 spec 定义如何建立一个由 3 个代表性 PDF 构成的 chunk 质量基准：清空内置硬件手册库，重新入库，由我（多模态模型）逐页读 PDF 对比已入库 chunks 确认完整性，构造全新 golden answers，跑 DeepEval，并将结果永久保存为后续优化基准。

## What Changes

- 清空内置硬件手册库（`hardware-docs-test` / `builtin-001`）及关联测试残留数据，建立干净测试环境。
- 选定 3 个代表性 PDF 重新入库：ch340g（接口/USB 转串口）、stm32f4_gpio_exti_extract（MCU 寄存器/GPIO）、esp32_datasheet（无线 MCU）。
- 对每个 PDF 逐页读取 PDF 内容，与已入库 chunks 逐项比对，确认文本、表格、引脚图/封装图/电路图等无遗漏、无重复、边界自然。
- 为每个 PDF 手工构造 5–10 个全新的 golden Q&A，覆盖事实提取、表格查询、跨页内容和图像描述。
- 使用 DeepEval 对 golden Q&A 跑 RAG 评估，记录总分与分项指标。
- 将 golden dataset、评估结果、chunk audit report 永久保存到 `data/benchmark/` 和 `docs/reports/`，作为后续优化基线。
- 完成质量验证后，再与用户确认是否将 `data/pdfs/` 下所有 PDF 全量入库到内置硬件手册库。

## Impact

- Affected specs: `.trae/specs/chunk-integrity-fullchain-fix/`（本次基准会复用其修复成果）
- Affected code:
  - `backend/src/rag/chunking/*`（chunker 行为）
  - `backend/src/rag/kb_manager.py`（入库与去重）
  - `backend/src/rag/document_processor.py`（PDF 解析）
  - `backend/tests/rag_eval/run_golden_eval.py`（评估入口）
- Affected data:
  - ChromaDB collection `hardware-docs-test`
  - SQLite `knowledge_docs` 表中 builtin-001 相关记录
  - 新增 `data/benchmark/chunk-baseline-golden-v1.yaml`
  - 新增 `data/benchmark/chunk-baseline-eval-v1.json`
  - 新增 `docs/reports/chunk-baseline-audit-2026-06-30.md`

## ADDED Requirements

### Requirement: 内置硬件手册库清理

The system SHALL provide a one-time cleanup that removes all existing chunks and metadata from the built-in hardware manual KB before re-indexing the 3 benchmark PDFs.

#### Scenario: Success case

- **WHEN** the cleanup script runs
- **THEN** the `hardware-docs-test` ChromaDB collection contains 0 documents, and SQLite `knowledge_docs` has no builtin-001 records related to previous test PDFs.

### Requirement: 3 个代表性 PDF 重新入库

The system SHALL ingest exactly 3 PDFs into the built-in hardware manual KB, one per representative category, using the chunker strategy best suited for the document type.

#### Scenario: Success case

- **WHEN** the re-index script runs for `ch340g_datasheet.pdf`
- **THEN** it uses `MultimodalChunker` and produces text chunks plus `image_description` chunks for pages containing pinout/package/schematic diagrams.
- **WHEN** the re-index scripts run for `stm32f4_gpio_exti_extract.pdf` and `esp32_datasheet.pdf`
- **THEN** they use `HybridChunker` and preserve register tables, electrical characteristic tables, and pinout descriptions.

### Requirement: 逐页 chunk 完整性人工确认

The assistant SHALL read each PDF page (rendered as image and/or extracted text) and compare it against the ingested chunks to verify completeness, boundary quality, and absence of duplication.

#### Scenario: Success case

- **WHEN** auditing a page that contains a pinout table
- **THEN** at least one chunk contains the complete Markdown table with the same rows and values.
- **WHEN** auditing a page that contains a pinout/package diagram
- **THEN** at least one `image_description` chunk describes the diagram accurately.
- **WHEN** auditing the full PDF
- **THEN** every page is covered by at least one text or image chunk, duplication rate < 5%, and no page has conflicting content assignments.

### Requirement: 构造全新 Golden Answers

The system SHALL generate a new golden dataset with 5–10 questions per PDF. Questions must be derived directly from the PDF content and not reuse any previous golden dataset entries.

#### Scenario: Success case

- **WHEN** a question asks "CH340G 的 VCC 引脚可以接 5V 还是 3.3V？"
- **THEN** the expected_answer contains the exact voltage range from the datasheet, and the `relevant_chunks` point to chunks that contain that information.
- **WHEN** a question asks "ESP32 的 GPIO12 有什么限制？"
- **THEN** the expected_answer references strapping pin behavior and relevant chunk/page sources.

### Requirement: DeepEval 基线评估

The system SHALL run DeepEval on the new golden dataset and produce a reproducible score report.

#### Scenario: Success case

- **WHEN** evaluation completes
- **THEN** the report contains `answer_relevancy`, `faithfulness`, `context_recall`, `context_precision`, `context_relevancy` per question and overall averages.

### Requirement: 永久基线存储

The system SHALL persist the golden dataset, evaluation results, and audit report under versioned file names so future optimization runs can compare against this baseline.

#### Scenario: Success case

- **WHEN** the baseline run finishes
- **THEN** `data/benchmark/chunk-baseline-golden-v1.yaml`, `data/benchmark/chunk-baseline-eval-v1.json`, and `docs/reports/chunk-baseline-audit-2026-06-30.md` exist and are checked into the project history.

### Requirement: 超时与失败恢复

All long-running commands (indexing, evaluation) SHALL have explicit timeout handling and retry/abort logic to avoid hanging for more than 30 minutes without output.

#### Scenario: Success case

- **WHEN** a multimodal indexing batch exceeds 5 minutes without response
- **THEN** the script logs the timeout, skips or retries the batch, and continues with the remaining pages.

## MODIFIED Requirements

### Requirement: 内置硬件手册库作为测试/基准容器

Previously the built-in KB (`builtin-001`) mixed production hardware docs with ad-hoc test data. This spec temporarily dedicates it to the benchmark run; after the user approves quality, the remaining PDFs will be ingested into the same KB.

#### Scenario: Success case

- **WHEN** the user confirms the 3-PDF benchmark quality is acceptable
- **THEN** all PDFs under `data/pdfs/` are ingested into the same built-in KB, using the validated chunker configuration.

## REMOVED Requirements

None.
