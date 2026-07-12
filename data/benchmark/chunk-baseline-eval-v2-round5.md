# Chunk-Baseline-v2 DeepEval Round 5 Report

- **Timestamp**: 20260701_030719
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.8973** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.9021** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.8571 | 30 |
| faithfulness | 0.9714 | 25 |
| answer_relevancy | 1.0000 | 25 |
| context_precision | 0.7607 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `esp32_datasheet.pdf` | 7 | 0.8973 | 0.9021 | 0.8571 | 0.9714 | 1.0000 | 0.7607 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| esp32-q001 | esp32_datasheet.pdf | fact_extraction | 0.9500 | 1.00 | 1.00 | 1.00 | 0.80 |  |
| esp32-q002 | esp32_datasheet.pdf | fact_extraction | 0.8528 | 1.00 | 1.00 | 1.00 | 0.41 |  |
| esp32-q003 | esp32_datasheet.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| esp32-q004 | esp32_datasheet.pdf | fact_extraction | 0.8274 | 1.00 | 1.00 | 1.00 | 0.31 |  |
| esp32-q005 | esp32_datasheet.pdf | table_query | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| esp32-q006 | esp32_datasheet.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q007 | esp32_datasheet.pdf | cross_page | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q008 | esp32_datasheet.pdf | cross_page | 0.6871 | 0.00 | 0.80 | 1.00 | 0.95 |  |
| esp32-q009 | esp32_datasheet.pdf | image_description | 0.9639 | 1.00 | 1.00 | 1.00 | 0.86 |  |

## Lowest-Scoring Question

- **ID**: esp32-q008
- **Query**: ESP32 进入 Deep-sleep 后如何保持 GPIO 状态？
- **Source PDF**: `data/pdfs/mcu/esp32_datasheet.pdf`
- **Average score**: 0.6871
- **Metric breakdown**:
  - context_recall: 0.0000
  - faithfulness: 0.8000
  - answer_relevancy: 1.0000
  - context_precision: 0.9484
- **Actual output preview**: 知识库未找到关于 ESP32 进入 Deep-sleep 后如何保持 GPIO 状态的具体配置步骤。参考文档片段仅提及在 Deep-sleep 模式下当 ULP 协处理器上电时，GPIO 和 RTC I2C 等外设能够操作 [src1][src2]，并且 RTC 外设处于上电状态 [src3][src4]，但未说明如何保持 GPIO 输出电平或上下拉状态的方法。建议查阅 ESP32 官方技术参考手册中关于 RTC GPIO 和低功耗管理的章节。...