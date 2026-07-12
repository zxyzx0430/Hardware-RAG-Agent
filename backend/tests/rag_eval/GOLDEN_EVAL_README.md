# Golden Dataset RAG Evaluation Guide

本指南面向需要运行 RAG 自动化评测的 agent 或开发者。涵盖数据集格式、运行命令、报告解读。

---

## 快速开始

### 1. 仅校验数据集格式（不调用 API）

```bash
cd E:\Desktop\agent\backend
python -m tests.rag_eval.run_golden_eval --validate-only
```

### 2. 运行评测（需要后端运行中 + API Key）

```bash
# 确保后端运行：
# cd backend && python main.py --web --port 58080

# 运行评测：
python -m tests.rag_eval.run_golden_eval \
    --api-key YOUR_API_KEY \
    --model oc/deepseek-v4-flash \
    --base-url https://9router.zxyzx.bbroot.com/v1 \
    --kb-id kb-xxxxxxxx
```

### 3. 只跑特定样本（调试用）

```bash
python -m tests.rag_eval.run_golden_eval \
    --ids G001,G002,G003 \
    --api-key YOUR_API_KEY \
    --kb-id kb-xxxxxxxx
```

---

## Golden Dataset 格式

文件位置：`backend/tests/rag_eval/golden_dataset.yaml`

每条样本字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 唯一标识，格式 G001-G999 |
| `question` | 是 | 用户自然语言问题 |
| `standard_answer` | 是 | 标准参考答案（人工编写，≥10 字符） |
| `reference_chunks` | 是 | 可接受的参考 chunk 文本列表（≥1 条，检索到任一即算命中） |
| `difficulty` | 是 | 简单 / 中等 / 难 |
| `category` | 是 | 技术分类（GPIO/I2C/SPI/UART/CAN/时钟/中断/传感器/工具链/电源/启动配置） |
| `target_doc` | 是 | 目标文档文件名 |
| `tags` | 是 | 技术标签列表（≥1 个） |
| `notes` | 否 | 可选标注 |

### 新增样本（5 步）

1. 打开 `golden_dataset.yaml`
2. 在 `samples` 列表末尾追加新条目
3. 填写 `id`（如 G031）、`question`、`standard_answer`
4. 填写 `reference_chunks`（标注该问题的正确答案所在 chunk 文本，可多条）
5. 运行 `python -m tests.rag_eval.run_golden_eval --validate-only` 校验格式

### reference_chunks 说明

`reference_chunks` 是**多可接受集合**——一个列表，检索到其中任一条即算命中。

为什么用多可接受集合？因为不同 chunk 策略（hybrid-500 vs hybrid-1200 vs agent）切出的 chunk 边界不同，但内容语义相同。标注多个可接受的 chunk 文本版本，让评测对 chunk 策略无关。

匹配方式：文本相似度（SequenceMatcher）≥0.60 算命中（仅用于 `recall_hit` 诊断指标，不影响 DeepEval 的 4 个正式指标）。

---

## 评测指标 + 加权

### 4 个 DeepEval 指标（总分 100）

| 指标 | 权重 | 含义 | 低分意味着 | 生产阈值 |
|------|------|------|-----------|---------|
| `context_recall` | 30 | 检索器是否找到所有需要的 chunk | chunk 缺失，答案信息不足 | ≥0.80 |
| `faithfulness` | 25 | 答案是否忠于检索上下文（无幻觉） | 模型在编造上下文之外的内容 | ≥0.75 |
| `answer_relevancy` | 25 | 答案是否真正回答了问题 | 答案跑题或含糊 | ≥0.80 |
| `context_precision` | 20 | 检索到的上下文有多少是有用的 | 检索器拉了太多噪声 | ≥0.70 |

**加权公式**：`total = context_recall×30 + faithfulness×25 + answer_relevancy×25 + context_precision×20`

### 附加诊断指标（不纳入总分）

| 指标 | 说明 |
|------|------|
| `recall_hit` | 布尔值，检索结果是否命中任一 reference_chunk |
| `latency_seconds` | 端到端延迟 |
| `token_usage` | 总 token 消耗 |

---

## 多 Chunk 策略对比

golden dataset 与 chunk 策略**完全解耦**——问题和标准答案不随 chunk 策略变化。

### 指定策略运行

```bash
# 用 hybrid chunk 策略（需先创建对应 KB）
python -m tests.rag_eval.run_golden_eval \
    --api-key YOUR_KEY \
    --chunk-method hybrid \
    --chunk-size 800 \
    --kb-id kb-xxxxxxxx

# 用 agent chunk 策略
python -m tests.rag_eval.run_golden_eval \
    --api-key YOUR_KEY \
    --chunk-method agent \
    --kb-id kb-yyyyyyyy
```

### 报告文件命名

- JSON: `golden_eval_{chunk_method}_{timestamp}.json`
- Markdown: `golden_eval_{chunk_method}_{timestamp}.md`

输出目录：`E:\Desktop\agent\data\test_results\`

---

## 报告解读

### Markdown 报告结构

1. **汇总**：总分、样本数、错误数、召回命中率
2. **维度分数表**：4 个指标各自的分数 + 加权后分数
3. **逐题结果表**：每题的 4 个指标分数 + 加权分 + 延迟
4. **低分诊断**（<70 分的题）：每个低分指标的 reason（LLM judge 的判断理由）

### 关键指标解读

- **总分 < 70**：系统有严重问题，检查检索或生成
- **context_recall 低但 recall_hit=Y**：检索到了但排名差（context_precision 也会低）
- **faithfulness 低**：LLM 在编造检索上下文之外的内容（幻觉）
- **answer_relevancy 低**：答案跑题，检查查询改写是否正确
- **context_precision 低但 recall_hit=Y**：检索了太多无关 chunk，考虑调小 top_k 或提高 threshold

---

## LLM Judge 配置

DeepEval 的 4 个指标需要 LLM 作为 judge。默认用与生成相同的模型。

### 指定不同 judge 模型

```bash
python -m tests.rag_eval.run_golden_eval \
    --api-key YOUR_KEY \
    --model gpt-4o-mini \
    --judge-model gpt-4o-mini \
    --judge-base-url https://api.openai.com/v1 \
    --judge-api-key YOUR_OPENAI_KEY \
    --kb-id kb-xxxxxxxx
```

judge 模型通过环境变量 `OPENAI_API_KEY` / `OPENAI_API_BASE` 传给 DeepEval。

---

## 常见问题

### Q: 评测很慢怎么办？

30 样本 × 4 指标 = 120 次 LLM judge 调用。deepseek-v4-flash 约 2s/次，总计约 4 分钟。
调试时用 `--ids G001,G002,G003` 只跑 3 条。

### Q: API 报错 "No credentials for provider"?

后端未配置 API Key。确保 `--api-key` 参数传入有效的 key，且 `--base-url` 指向正确的代理。

### Q: DeepEval 报 "NaN" 分数？

本项目用 DeepEval 原生指标（非 RAGAS 包装），有 JSON-confineable 机制避免 NaN。
如仍出现，检查 judge 模型是否支持 JSON 输出，或换用 `gpt-4o-mini`。

### Q: 如何与现有规则评分（config.py）对比？

两套评测独立运行：
- 规则评分：`python -m tests.rag_eval.run_eval --api-key ...`
- DeepEval：`python -m tests.rag_eval.run_golden_eval --api-key ...`
结果各自存储在 `data/test_results/`，文件名前缀不同（`rag_eval_` vs `golden_eval_`）。

### Q: 如何从 config.py 的 15 题生成 golden 骨架？

```bash
python -m tests.rag_eval.dataset_builder
# 输出：golden_dataset_skeleton.yaml（standard_answer 需手动填写）
```

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `golden_dataset.yaml` | 黄金数据集（30 条样本，人工编写） |
| `golden_dataset_schema.json` | JSON Schema 校验 |
| `run_golden_eval.py` | DeepEval 评测运行器 |
| `dataset_builder.py` | 从 config.py 生成骨架的辅助工具 |
| `GOLDEN_EVAL_README.md` | 本文档 |

**不动的文件**：`config.py`、`run_eval.py`、`rescore.py` — 现有规则评分体系完全保留。
