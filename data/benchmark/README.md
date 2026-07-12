# Chunk 质量基准 v1（data/benchmark/）

本目录保存 Hardware RAG Agent 的 **Chunk 质量基准 v1** 数据集、评测结果与相关审计产物。

> 综合报告见：`docs/reports/chunk-baseline-audit-2026-06-30.md`

---

## 文件用途

### 核心基线文件（必须保留）

| 文件 | 用途 |
|------|------|
| `chunk-baseline-golden-v1.yaml` | 26 题 golden Q&A 数据集。每题含问题、期望答案、来源 PDF、来源页、相关 chunk ID、问题类型。 |
| `chunk-baseline-eval-v1.json` | DeepEval 完整评测结果，包含 overall、per_pdf、per_question 三级分数。 |
| `chunk-baseline-eval-v1.md` | DeepEval 结果摘要（Markdown 版）。 |
| `chunk-baseline-golden-v1-retrieval-gap-report.md` | 检索 gap 分析摘要：relevant gap、source-page mismatch、distance gap。 |
| `chunk-baseline-golden-v1-retrieval-gap-report.json` | 检索 gap 分析的 JSON 详细数据。 |
| `chunk-baseline-golden-v1-validation-report.json` | 每道题 top-5 检索的原始结果（chunk id、distance、page_range 等）。 |

### 单份 PDF 产物

| 文件 | 用途 |
|------|------|
| `ch340g_golden_v1.json` / `stm32f4_golden_v1.json` / `esp32_golden_v1.json` | 各 PDF 的 golden 子集（从 YAML 拆分）。 |
| `ch340g_chunks.json` / `stm32f4_chunks.json` / `esp32_chunks.json` | 各 PDF chunk 元数据快照。 |
| `ch340g_chunks_summary.txt` / `stm32f4_chunks_summary.txt` / `esp32_chunks_summary.txt` | chunk 分布摘要。 |
| `ch340g_audit_summary.json` | CH340G audit 的 JSON 汇总。 |
| `esp32_pages_summary.json` | ESP32 每页 chunk 分布汇总。 |

### 调试与可视化产物

| 目录/文件 | 用途 |
|-----------|------|
| `ch340g_audit_pages/` | CH340G 每页截图，用于人工 audit。 |
| `pdf_extracts/` | PDF 解析产物：每页 PNG、表格/图片提取结果、结构化文本 JSON。 |

---

## 重新运行基线

```powershell
# 工作目录：项目根目录 e:\Desktop\agent

# 1. 启动后端（如需要调用真实 LLM API）
# cd backend
# python main.py --web --port 58080

# 2. 环境清理（删除 hardware-docs-test collection 与 builtin-001 旧记录）
python scripts/cleanup_baseline_kb.py

# 3. 构建基准 KB（使用本地 sentence-transformers/all-MiniLM-L6-v2）
python scripts/build_chunk_baseline_kb.py

# 4. 检索 gap 分析
python scripts/analyze_golden_retrieval_gap.py

# 5. 运行 DeepEval 基线评测
python scripts/run_baseline_deepeval.py --parallel 2
```

> 运行 DeepEval 前请确保 `backend/.env` 中已配置 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，且 judge 模型（默认 `qwen-turbo`）可访问。

---

## 基线指标速查

- **Overall Average**：0.5092
- **Faithfulness**：0.9002
- **Context Recall**：0.4679
- **Context Precision**：0.4223
- **Context Relevancy**：0.2416

各 PDF 平均分：

- `stm32f4_gpio_exti_extract.pdf`：0.7700
- `ch340g_datasheet.pdf`：0.4174
- `esp32_datasheet.pdf`：0.3299

---

## 维护说明

- 本目录中的 `.yaml`、`.json`、`.md` 基线文件**应当进入 Git**（已在 `.gitignore` 中排除忽略）。
- 中间调试产物（如 `_tmp_*.json`、临时脚本输出）可视情况清理，不强制提交。
