# Chunk 质量基准 v1 审计与 DeepEval 基线报告

> **永久基线声明**：本报告记录的是 **Chunk 质量基准 v1** 的完整审计与评测结果。除非特别说明，后续所有 chunk 策略、检索策略、prompt 优化的对比实验均以此报告为基准（`overall = 0.5092`）。

| 项目 | 内容 |
|------|------|
| 报告日期 | 2026-06-30 |
| 评测集 | `data/benchmark/chunk-baseline-golden-v1.yaml` |
| 知识库 | `builtin-001` / collection `hardware-docs-test` |
| Chunker | `HybridChunker`（`small_chunk_size=800`） |
| Embedding | `sentence-transformers/all-MiniLM-L6-v2`（本地 384 维） |
| 生成模型 | `oc/deepseek-v4-flash` @ `9router.zxyzx.bbroot.com/v1` |
| 裁判模型 | `qwen-turbo` @ DashScope |
| API 基线 | `http://127.0.0.1:58080/api` |

---

## 1. 目标与范围

本次基准的核心目标是：**在统一的测试知识库上，量化当前 HybridChunker + 默认检索链路对真实硬件手册问答的支持能力**，为后续优化建立可对比的数字化基线。

选取的 3 份 PDF 覆盖了 Hardware RAG Agent 最常见的三类资料：

| PDF | 类型 | 页数 | 代表挑战 |
|-----|------|------|----------|
| `ch340g_datasheet.pdf` | USB-UART 接口芯片数据手册 | 14 | 表格引脚定义、应用电路图、跨页说明 |
| `stm32f4_gpio_exti_extract.pdf` | MCU GPIO/EXTI 寄存器章节 | 44 | 寄存器表、配置流程、框图 |
| `esp32_datasheet.pdf` | Wi-Fi MCU 数据手册 | 78 | 大量表格、strapping pins、功耗/电源域 |

---

## 2. 环境清理

在构建基准 KB 前，执行了 Phase 1 清理脚本 [`scripts/cleanup_baseline_kb.py`](file:///E:/Desktop/agent/scripts/cleanup_baseline_kb.py)：

1. 清空 ChromaDB collection `hardware-docs-test` 的全部旧 chunks；
2. 删除 SQLite `knowledge_docs` 中 `kb_id='builtin-001'` 的历史记录；
3. 将旧的 golden eval reference embedding 缓存 `data/test_results/golden_ref_embeddings.pkl` 重命名为带时间戳的 `.bak`。

该清理是幂等的，确保本次基准不受历史测试数据污染。

---

## 3. 文档入库结果

| PDF | doc_id | chunk 方法 | 总 chunks | 文本 / 图片描述 | 重复率 | 页面覆盖率 | 表格覆盖率 | 图片覆盖率 | 备注 |
|-----|--------|-----------|----------:|----------------|--------|-----------|-----------|-----------|------|
| `ch340g_datasheet.pdf` | `12f13dda-4d71-4a4a-9870-fe7e9d7e5062` | hybrid | 48 | 36 / 12 | 0.00% | 100% | 100% | 100% | 包含 12 个图片描述 chunk |
| `stm32f4_gpio_exti_extract.pdf` | `baseline-stm32f4-gpio-exti` | hybrid | 137（入库 133） | 137 / 0 | 3.65%（5/137） | 100% | 100% | — | 寄存器表覆盖 50% |
| `esp32_datasheet.pdf` | `a01cb55a-3218-4c85-8115-f153a5916881` | hybrid | 200 | 200 / 0 | 0.00% | 100% | — | — | HybridChunker 不生成图片描述 chunk |

> 说明：
> - `stm32f4` 的 5 个重复 chunk 在入库前被复合键 `(fingerprint, section_title)` 去重过滤，最终写入 133 个。
> - `esp32` 的 200 个 chunk 均为文本；引脚图/框图等内容以表格或正文形式覆盖，未单独生成图片描述。

---

## 4. 每份 PDF 的 Audit 结论

### 4.1 CH340G — 通过

- 无自动检测问题；
- 全部 14 页、关键表格、关键图片均 100% 覆盖；
- 12 个图片描述 chunk 完整覆盖 p5/p6/p7/p8/p12/p13/p14 等应用电路图。

### 4.2 STM32F4 GPIO/EXTI — 通过（有注意事项）

- 页面覆盖率 100%，普通表格覆盖率 100%；
- **寄存器表覆盖率仅 50%**：p5、p18、p23–p26、p41–p44 的 register_table 未被任何 chunk 单独捕获；
- 这些寄存器表仍可能以正文或段落形式存在于相邻文本 chunk 中，但检索时容易被更密集的表格/说明淹没。

### 4.3 ESP32 — 通过（有已知局限）

- 全部 78 页覆盖，6 大类关键内容（Pin Overview、Strapping Pins、Functional Description、Peripheral Pin Config、Electrical Characteristics、Appendix A Pin Lists）均检测到信号；
- 无 blocking issue；
- 局限：HybridChunker 对表格密集型 PDF 的图片/框图仅通过 surrounding text 覆盖，未生成独立图片描述 chunk。

---

## 5. 修复记录：HybridChunker page_range 继承问题

### 问题

HybridChunker 在 `_split_markdown` / `_split_plain_text` 拆分 section 时，遇到不含 `<!-- PAGE:N -->` 标记的子段落会 fallback 到 `(1, 1)`。对于 `esp32_datasheet.pdf` 这类表格密集的文档，`protect_structures()` 占位符在 sub-split 或 section 合并后丢失了页码标记，导致大量表格内容被错误分配到 `page_range=[1,1]`，p1 被 60+ 个 chunk 覆盖，严重影响按页过滤的检索与引用。

### 修复

在 [`backend/src/rag/chunking/hybrid_chunker.py`](file:///E:/Desktop/agent/backend/src/rag/chunking/hybrid_chunker.py) 中：

1. `_get_section_pages()` 新增 `default_range` 参数，不再硬编码 `(1, 1)`；
2. `_split_markdown()` 与 `_split_plain_text()` 维护一个 `current_page_range` 状态，拆分时把最近一次有效的页码范围传递给无标记子 section；
3. 当子 section 自身包含页码标记时，再更新 `current_page_range`。

这样，无页码标记的子段落会**继承**最近的有效页码，而不是全部回到第 1 页。

### 验证

- 重跑 `esp32` 入库 audit 后，p1 覆盖 chunk 数从异常值回落到 1 个，各页 chunk 分布与 PDF 页码对齐；
- 表格内容不再集中到 `page_range=[1,1]`；
- 本次 3 份 PDF 的 audit 均基于修复后的 chunker 生成。

---

## 6. Golden Dataset 统计

### 6.1 基本规模

- **总题数**：26
- **覆盖 PDF**：3 份
- **每份 PDF 题数**：CH340G 8 题、STM32F4 9 题、ESP32 9 题

### 6.2 问题类型分布

| 类型 | 题数 | 占比 | 说明 |
|------|------|------|------|
| `fact_extraction` | 11 | 42.3% | 从正文中提取具体参数 |
| `table_query` | 6 | 23.1% | 查表（引脚表、寄存器表、strapping pins 等） |
| `cross_page` | 5 | 19.2% | 答案分散在多页 |
| `image_description` | 4 | 15.4% | 框图/电路图理解 |

### 6.3 Retrieval Gap 分析

基于 `scripts/analyze_golden_retrieval_gap.py` 对 top-5 检索结果的分析：

| PDF | 题数 | Relevant Gaps | Source-Page Mismatches | Distance Gaps (>1.0) | Avg Top-1 Distance | Avg Top-3 Distance |
|-----|------|--------------:|-----------------------:|---------------------:|-------------------:|-------------------:|
| ch340g | 8 | 0 | 5 | 0 | 0.7475 | 0.7958 |
| stm32f4 | 9 | 0 | 6 | 1 | 0.8457 | 0.9362 |
| esp32 | 9 | 0 | 3 | 0 | 0.8169 | 0.8528 |

**关键发现**：

- **0 个 relevant gap**：所有 golden 中标记的 `relevant_chunks` 都出现在了 top-5 检索结果中；
- **14 个 source-page mismatch**：top-3 检索结果的 `page_range` 与人工标注的 `source_pages` 没有重叠。这主要说明 chunk 边界跨页或 `source_pages` 按原始 PDF 页码标注，而 chunk 的 `page_range` 可能跨页/合并；
- **1 个 distance gap**：`stm32f4-q006`（SWJ-DP 调试引脚）top-1 距离 > 1.0，语义检索置信度偏低。

---

## 7. DeepEval 评测结果

### 7.1 总体分数

**Overall Average：0.5092**（5 项指标等权平均）

| 指标 | 分数 | 说明 |
|------|------|------|
| `answer_relevancy` | 0.5138 | 答案与问题的相关度 |
| `faithfulness` | 0.9002 | 答案对检索上下文的忠实度 |
| `context_recall` | 0.4679 | 检索结果覆盖参考答案的程度 |
| `context_precision` | 0.4223 | 检索结果中相关内容的比例 |
| `context_relevancy` | 0.2416 | 检索结果与问题的语义相关度 |

**解读**：当前链路的 `faithfulness` 很高（0.90），说明 LLM 不会编造；但 `context_recall`、`context_precision`、`context_relevancy` 明显偏低，是整体分数低的主因。问题大多出在**检索阶段没有召回正确上下文**。

### 7.2 各 PDF 分数

| PDF | 题数 | Average | answer_relevancy | faithfulness | context_recall | context_precision | context_relevancy |
|-----|------|---------|-----------------|--------------|----------------|-------------------|-------------------|
| `stm32f4_gpio_exti_extract.pdf` | 9 | **0.7700** | 0.9242 | 0.8519 | 0.8333 | 0.7756 | 0.4652 |
| `ch340g_datasheet.pdf` | 8 | 0.4174 | 0.2747 | 0.9583 | 0.5208 | 0.2292 | 0.1041 |
| `esp32_datasheet.pdf` | 9 | 0.3299 | 0.3158 | 0.8968 | 0.0556 | 0.2407 | 0.1404 |

### 7.3 最低分题目分析

**Overall 最低分**：`esp32-q001`（0.2000）

| 字段 | 内容 |
|------|------|
| 问题 | ESP32 Deep-sleep 模式下的典型功耗是多少？哪些内容在 Deep-sleep 期间保持供电？ |
| 类型 | `fact_extraction` |
| 实际输出 | `知识库未找到相关文档，建议查阅官方手册。` |
| 检索到的 chunks | 0 |
| 分数 | answer_relevancy=0.00, faithfulness=1.00, context_recall=0.00, context_precision=0.00, context_relevancy=0.00 |

**原因**：该问题虽然 golden 中标注了相关 chunk（s62/s67/s72），但实际 RAG 链路没有召回任何上下文，导致系统直接返回“未找到相关文档”。这是 v1 基线中最典型的**检索失败**模式。

其他明显低分题目（avg < 0.35）：

| 题目 | PDF | 平均分 | 主要表现 |
|------|-----|--------|----------|
| `esp32-q002` | esp32 | 0.2000 | 未找到相关文档 |
| `esp32-q003` | esp32 | 0.2000 | 未找到相关文档 |
| `ch340g-q001` | ch340g | 0.2033 | 未找到相关文档 |
| `ch340g-q002` | ch340g | 0.2100 | 未找到相关文档 |
| `ch340g-q007` | ch340g | 0.2136 | 未找到相关文档（图片题） |
| `esp32-q004` | esp32 | 0.2030 | 检索到错误上下文（STM32 内容） |
| `esp32-q005` | esp32 | 0.3336 | 检索到 STM32 内容，答案不相关 |
| `ch340g-q004` | ch340g | 0.3333 | 表格查询，未召回 V3 引脚表格 |

---

## 8. 主要结论与后续优化方向

1. **STM32F4 表现最好（0.77）**：该 PDF 是普通寄存器/说明文本为主，HybridChunker 的段落拆分和页码继承能较好地支持检索。
2. **CH340G 中等（0.42）**：表格和图片虽然 audit 覆盖率高，但实际检索时大量问题未能召回对应 chunk；表格查询和图片题的 `context_recall` / `context_precision` 偏低。
3. **ESP32 最差（0.33）**：78 页密集数据手册中，很多事实性问题直接“未找到相关文档”。根因包括：
   - 表格占位符页码问题虽已修复，但检索语义匹配仍弱；
   - 部分问题（如 deep-sleep 功耗）的关键词与 chunk 文本的语义距离较大；
   - top_k=8 的检索池在 200 个 chunk 中可能遗漏关键表格行。
4. **检索是最大瓶颈**：`context_recall` 0.47、`context_precision` 0.42、`context_relevancy` 0.24 均显著低于 `faithfulness`。后续优化应优先围绕：
   - 提升表格/寄存器行的嵌入质量；
   - 尝试更大的 `top_k`、reranker、混合检索（向量 + BM25 + 页码过滤）；
   - 对密集数据手册引入更细粒度的子表格 chunk 或 caption 增强。

---

## 附录 A：关键文件清单

| 文件 | 用途 |
|------|------|
| `data/benchmark/chunk-baseline-golden-v1.yaml` | 26 题 golden Q&A 数据集，含问题、期望答案、来源页、相关 chunk ID |
| `data/benchmark/chunk-baseline-eval-v1.json` | DeepEval 完整结果（per-question / per-pdf / overall） |
| `data/benchmark/chunk-baseline-eval-v1.md` | DeepEval 结果 Markdown 摘要 |
| `data/benchmark/chunk-baseline-golden-v1-retrieval-gap-report.{md,json}` | 检索 gap 分析（relevant gap / source-page mismatch / distance gap） |
| `data/benchmark/chunk-baseline-golden-v1-validation-report.json` | 每题 top-5 检索原始结果 |
| `data/benchmark/*_chunks.json` | 各 PDF chunk 元数据快照 |
| `docs/reports/audit_baseline_ch340g_20260630.md` | CH340G 单份 audit 报告 |
| `docs/reports/audit_baseline_stm32f4_20260630.md` | STM32F4 单份 audit 报告 |
| `docs/reports/audit_baseline_esp32_20260630.md` | ESP32 单份 audit 报告 |
| `scripts/cleanup_baseline_kb.py` | Phase 1 环境清理 |
| `scripts/build_chunk_baseline_kb.py` | Phase 2/3 构建基准 KB |
| `scripts/run_baseline_deepeval.py` | Phase 5 运行 DeepEval 评测 |
| `scripts/analyze_golden_retrieval_gap.py` | Phase 4 检索 gap 分析 |

## 附录 B：重新运行基线的命令

```powershell
# 1. 确保后端已启动（或仅用于本地 embedding 时无需 LLM key）
# cd backend; python main.py --web --port 58080

# 2. 环境清理（幂等，可 dry-run）
python scripts/cleanup_baseline_kb.py

# 3. 构建基准 KB（使用本地 MiniLM 模型）
python scripts/build_chunk_baseline_kb.py

# 4. 检索 gap 分析
python scripts/analyze_golden_retrieval_gap.py

# 5. 运行 DeepEval 基线评测
python scripts/run_baseline_deepeval.py --parallel 2
```

> 注：运行 DeepEval 需要配置 `backend/.env` 中的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，并保证 judge 模型（默认 `qwen-turbo`）可访问。
