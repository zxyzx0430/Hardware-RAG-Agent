# Chunk 质量基准 v2 — 全 multimodal chunker + DeepEval 迭代优化 Spec

## Why

v1 基线中，stm32f4 和 esp32 使用 HybridChunker 分块，ch340g 使用 MultimodalChunker，最终 DeepEval overall 仅 0.5092，显著低于历史 0.94 基线。用户要求：
1. 所有 PDF 统一使用 MultimodalChunker 分块，确保 chunk 完整性（特别是引脚图、表格、封装图无遗漏）。
2. 对齐 DeepEval 测试变量（生成模型、judge 模型、system prompt）与历史高分测试保持一致，避免变量不一致导致分数不可比。
3. 深入分析每轮低分题目的输入/输出、检索链路、chunk 内容，定位根因并修复。
4. 迭代跑分直到 overall ≥ 0.90 且单题最低分 > 0.80，或确认已触及当前配置极限。

本 spec 定义 v2 基线的建立流程和迭代终止条件。

## What Changes

- 清空内置硬件手册库（`builtin-001` / `hardware-docs-test`），重新用 MultimodalChunker 入库 3 个 PDF：ch340g、stm32f4_gpio_exti_extract、esp32_datasheet。
- 逐页读取 PDF 图片/文本，与 MultimodalChunker 产出的 chunks 逐项对比，确认文本、表格、引脚图/封装图/框图完整无遗漏。
- 对齐 DeepEval 变量：
  - 生成模型使用 `deepseek-v4-flash`（通过 opencode 端点）
  - judge 模型使用 `deepseek-v4-flash`
  - 使用 `RAG_OPTIMIZED_SYSTEM_PROMPT`（禁止编造寄存器值、禁止 meta-commentary）
- 基于 v1 golden dataset（26 题）跑多轮 DeepEval，建立 v2 起始分数。
- 对每轮低于 0.80 的题目做根因分析：
  - 检索 top-k chunks 是否包含答案
  - chunk 文本是否完整保留答案来源
  - LLM 生成是否编造/偏离/加无关内容
  - judge 评分是否合理
- 修复发现的问题后重新跑分，迭代直到达标或连续两轮提升 < 0.01。
- 保存 v2 永久基线：`data/benchmark/chunk-baseline-golden-v2.yaml`、`data/benchmark/chunk-baseline-eval-v2.json`、最终报告。

## Impact

- Affected specs: `.trae/specs/chunk-baseline-v1/`（v2 在 v1 基础上迭代）
- Affected code:
  - `backend/src/rag/chunking/multimodal_chunker.py`（可能调整参数）
  - `backend/src/rag/kb_manager.py`（检索策略：top_k / RRF / BM25 权重）
  - `backend/src/rag/reranker.py`（reranker max_length / 应用时机）
  - `backend/app/api/chat_helpers.py`（query rewrite 策略）
  - `backend/tests/rag_eval/run_golden_eval.py`（对齐生成/judge 配置）
- Affected data:
  - ChromaDB collection `hardware-docs-test`
  - `data/benchmark/chunk-baseline-golden-v2.yaml`
  - `data/benchmark/chunk-baseline-eval-v2.json`
  - 最终审计报告

## ADDED Requirements

### Requirement: 3 个 PDF 全部使用 MultimodalChunker 重新入库

The system SHALL ingest all 3 benchmark PDFs into `builtin-001` using `MultimodalChunker`, regardless of document type.

#### Scenario: Success case

- **WHEN** the re-index scripts run for ch340g / stm32f4 / esp32
- **THEN** each PDF produces text chunks and `image_description` chunks for pages containing diagrams/tables/schematics.
- **THEN** the assistant audits each page and confirms no critical pinout table, package diagram, or functional block diagram is missing.

### Requirement: DeepEval 变量对齐

The system SHALL configure DeepEval evaluation to match the historical high-score setup as closely as possible.

#### Scenario: Success case

- **WHEN** running DeepEval
- **THEN** the generation model is `deepseek-v4-flash`.
- **THEN** the judge model is `deepseek-v4-flash`.
- **THEN** the RAG system prompt includes the optimized instructions (no fabrication of register values, no meta-commentary).
- **THEN** the same 4 metrics are computed with the same weights: context_recall 30, faithfulness 25, answer_relevancy 25, context_precision 20.

### Requirement: 低分题目根因分析

For every question scoring ≤ 0.80, the assistant SHALL inspect the retrieval context, chunk content, LLM output, and judge reasoning to identify the root cause.

#### Scenario: Success case

- **WHEN** a question scores 0.40
- **THEN** the analysis report lists: top retrieved chunks, whether the correct chunk was retrieved, whether the chunk content contains the answer, whether the LLM output is faithful, and whether the judge score is reasonable.

### Requirement: 迭代优化直到达标

The system SHALL iteratively fix root causes and re-run DeepEval until either the target is reached or improvement stalls.

#### Scenario: Success case

- **WHEN** a round finishes below target
- **THEN** at least one root cause is fixed and a new round is run.
- **WHEN** overall ≥ 0.90 and every question > 0.80
- **THEN** iteration stops and the final baseline is saved.
- **WHEN** two consecutive rounds improve by < 0.01
- **THEN** iteration stops, the best result is saved, and remaining gaps are documented as future work.

### Requirement: v2 永久基线保存

The final golden dataset, evaluation result, and iteration log SHALL be persisted under `data/benchmark/` with the `v2` prefix.

#### Scenario: Success case

- **WHEN** iteration completes
- **THEN** `data/benchmark/chunk-baseline-golden-v2.yaml`, `data/benchmark/chunk-baseline-eval-v2.json`, and the audit report exist.

## MODIFIED Requirements

### Requirement: 检索参数调优（Round 6 新增）

基于 Round 4/5 低分题根因分析，系统 SHALL 调整检索参数以提升 context_recall 和 context_precision。

#### 当前检索管线配置

| 参数 | 当前值 | 位置 |
|------|--------|------|
| top_k（eval） | 8 | `run_baseline_deepeval_v2.py` |
| top_k（生产默认） | 5 | `chat_routes.py:76` |
| relevance_threshold | 0.0 | 多处 |
| RRF constant_k | 60 | `kb_manager.py:195` |
| BM25-only penalty | 0.85 | `kb_manager.py:290` |
| BM25 norm factor | 1.15 | `kb_manager.py:958` |
| Reranker | bge-reranker-base, max_length=512 | `reranker.py:23,44` |
| Reranker 应用时机 | RRF 融合 + 阈值过滤之后 | `kb_manager.py:744-770` |
| Query rewrite | LLM 改写，temperature=0，8s 超时 | `chat_helpers.py:156-260` |

#### Scenario: 检索参数优化

- **WHEN** table_query 类型题目 context_recall = 0
- **THEN** 检查 BM25 分词是否正确匹配技术术语（MODERy、AFRL、strapping pin 等）
- **THEN** 考虑提升 top_k 到 12 或对 table_query 类型使用更大 top_k
- **THEN** 考虑降低 BM25-only penalty 以提升关键词精确匹配的召回

- **WHEN** context_precision < 0.5
- **THEN** 考虑设置 threshold > 0.0 过滤低分 chunk
- **THEN** 检查 reranker max_length=512 是否截断了长 chunk

#### Scenario: Golden dataset 修正

- **WHEN** expected_answer 中的信息在 source_pdf 中不存在（如 esp32-q008 的 "GPIO hold 功能" 可能在 Technical Reference Manual 而非 datasheet）
- **THEN** 更新 expected_answer 为 datasheet 中实际可检索到的内容
- **THEN** 或更新 source_pages 包含实际包含答案的页面

## REMOVED Requirements

None.
