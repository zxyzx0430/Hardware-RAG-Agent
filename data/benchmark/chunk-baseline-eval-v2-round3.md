# Chunk-Baseline-v2 DeepEval Round 3 Report

- **Timestamp**: 20260701_002251
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.8298** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.8205** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.6111 | 30 |
| faithfulness | 1.0000 | 25 |
| answer_relevancy | 0.9111 | 25 |
| context_precision | 0.7972 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `esp32_datasheet.pdf` | 9 | 0.8298 | 0.8205 | 0.6111 | 1.0000 | 0.9111 | 0.7972 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| esp32-q001 | esp32_datasheet.pdf | fact_extraction | 0.9500 | 1.00 | 1.00 | 1.00 | 0.80 |  |
| esp32-q002 | esp32_datasheet.pdf | fact_extraction | 0.7528 | 1.00 | 1.00 | 0.60 | 0.41 |  |
| esp32-q003 | esp32_datasheet.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| esp32-q004 | esp32_datasheet.pdf | fact_extraction | 0.5328 | 0.00 | 1.00 | 0.80 | 0.33 |  |
| esp32-q005 | esp32_datasheet.pdf | table_query | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| esp32-q006 | esp32_datasheet.pdf | table_query | 0.9875 | 1.00 | 1.00 | 1.00 | 0.95 |  |
| esp32-q007 | esp32_datasheet.pdf | cross_page | 0.8000 | 0.50 | 1.00 | 1.00 | 0.70 |  |
| esp32-q008 | esp32_datasheet.pdf | cross_page | 0.6955 | 0.00 | 1.00 | 0.80 | 0.98 |  |
| esp32-q009 | esp32_datasheet.pdf | image_description | 0.7500 | 0.00 | 1.00 | 1.00 | 1.00 |  |

## Lowest-Scoring Question

- **ID**: esp32-q004
- **Query**: ESP32 有多少个可编程 GPIO？其中包含哪些特殊类型？
- **Source PDF**: `data/pdfs/mcu/esp32_datasheet.pdf`
- **Average score**: 0.5328
- **Metric breakdown**:
  - context_recall: 0.0000
  - faithfulness: 1.0000
  - answer_relevancy: 0.8000
  - context_precision: 0.3313
- **Actual output preview**: 根据参考文档片段，ESP32 可编程 GPIO 的具体总数未明确说明。关于其中包含的特殊类型，文档提及以下内容：

- **模拟功能**：最多 18 个 ADC 输入引脚 [src6]；两个 DAC 输出引脚（GPIO25、GPIO26）[src6]；10 个触摸传感 GPIO [src6]。
- **自举配置（Strapping Pins）**：GPIO0、GPIO2、MTDI、MTDO、GPIO5 用于芯片上电/复位时的启动模式配置 [src3]。
- **数字功能类型**：在 IO 功能描述中定义了多种信号类型，包括 I（仅输入）、I1（未分配时输入固定为 1）、IO（未分配时输入固定为...