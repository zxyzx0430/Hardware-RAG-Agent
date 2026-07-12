# Chunk-Baseline-v1 DeepEval Baseline Report

- **Timestamp**: 20260630_052345
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `oc/deepseek-v4-flash` @ `https://9router.zxyzx.bbroot.com/v1`
- **Judge model**: `qwen-turbo` @ `https://dashscope.aliyuncs.com/compatible-mode/v1`

## Overall Average

**0.5092** (average of 5 DeepEval metrics, 0-1 scale)

| Metric | Score |
|--------|-------|
| answer_relevancy | 0.5138 |
| faithfulness | 0.9002 |
| context_recall | 0.4679 |
| context_precision | 0.4223 |
| context_relevancy | 0.2416 |

## Per-PDF Average

| PDF | Count | Average | answer_relevancy | faithfulness | context_recall | context_precision | context_relevancy |
|-----|-------|---------|-------|-------|-------|-------|-------|
| `ch340g_datasheet.pdf` | 8 | 0.4174 | 0.2747 | 0.9583 | 0.5208 | 0.2292 | 0.1041 |
| `esp32_datasheet.pdf` | 9 | 0.3299 | 0.3158 | 0.8968 | 0.0556 | 0.2407 | 0.1404 |
| `stm32f4_gpio_exti_extract.pdf` | 9 | 0.7700 | 0.9242 | 0.8519 | 0.8333 | 0.7756 | 0.4652 |

## Per-Question Scores

| ID | PDF | Type | Avg | AR | FA | CR | CP | CRel | Error |
|----|-----|------|-----|----|----|----|----|------|-------|
| ch340g-q001 | ch340g_datasheet.pdf | fact_extraction | 0.2033 | 0.00 | 1.00 | 0.00 | 0.00 | 0.02 |  |
| ch340g-q002 | ch340g_datasheet.pdf | fact_extraction | 0.2100 | 0.00 | 1.00 | 0.00 | 0.00 | 0.05 |  |
| ch340g-q003 | ch340g_datasheet.pdf | fact_extraction | 0.6293 | 0.00 | 1.00 | 1.00 | 1.00 | 0.15 |  |
| ch340g-q004 | ch340g_datasheet.pdf | table_query | 0.3333 | 0.50 | 0.67 | 0.50 | 0.00 | 0.00 |  |
| ch340g-q005 | ch340g_datasheet.pdf | table_query | 0.4054 | 0.00 | 1.00 | 1.00 | 0.00 | 0.03 |  |
| ch340g-q006 | ch340g_datasheet.pdf | cross_page | 0.5500 | 0.75 | 1.00 | 1.00 | 0.00 | 0.00 |  |
| ch340g-q007 | ch340g_datasheet.pdf | image_description | 0.2136 | 0.00 | 1.00 | 0.00 | 0.00 | 0.07 |  |
| ch340g-q008 | ch340g_datasheet.pdf | image_description | 0.7945 | 0.95 | 1.00 | 0.67 | 0.83 | 0.53 |  |
| esp32-q001 | esp32_datasheet.pdf | fact_extraction | 0.2000 | 0.00 | 1.00 | 0.00 | 0.00 | 0.00 |  |
| esp32-q002 | esp32_datasheet.pdf | fact_extraction | 0.2000 | 0.00 | 1.00 | 0.00 | 0.00 | 0.00 |  |
| esp32-q003 | esp32_datasheet.pdf | fact_extraction | 0.2000 | 0.00 | 1.00 | 0.00 | 0.00 | 0.00 |  |
| esp32-q004 | esp32_datasheet.pdf | fact_extraction | 0.2030 | 0.43 | 0.50 | 0.00 | 0.00 | 0.09 |  |
| esp32-q005 | esp32_datasheet.pdf | table_query | 0.3336 | 0.43 | 0.86 | 0.00 | 0.00 | 0.38 |  |
| esp32-q006 | esp32_datasheet.pdf | table_query | 0.4579 | 0.50 | 0.71 | 0.00 | 1.00 | 0.07 |  |
| esp32-q007 | esp32_datasheet.pdf | cross_page | 0.3333 | 0.67 | 1.00 | 0.00 | 0.00 | 0.00 |  |
| esp32-q008 | esp32_datasheet.pdf | cross_page | 0.5809 | 0.00 | 1.00 | 0.50 | 1.00 | 0.40 |  |
| esp32-q009 | esp32_datasheet.pdf | image_description | 0.4601 | 0.82 | 1.00 | 0.00 | 0.17 | 0.32 |  |
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8255 | 0.82 | 1.00 | 1.00 | 0.81 | 0.50 |  |
| stm32f4-q002 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.9236 | 1.00 | 1.00 | 1.00 | 1.00 | 0.62 |  |
| stm32f4-q003 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.3932 | 0.75 | 1.00 | 0.00 | 0.00 | 0.22 |  |
| stm32f4-q004 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8744 | 1.00 | 1.00 | 1.00 | 0.97 | 0.41 |  |
| stm32f4-q005 | stm32f4_gpio_exti_extract.pdf | table_query | 0.6753 | 1.00 | 0.00 | 1.00 | 0.84 | 0.54 |  |
| stm32f4-q006 | stm32f4_gpio_exti_extract.pdf | table_query | 0.5756 | 0.80 | 0.80 | 0.50 | 0.67 | 0.11 |  |
| stm32f4-q007 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.9038 | 0.95 | 1.00 | 1.00 | 1.00 | 0.57 |  |
| stm32f4-q008 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.8456 | 1.00 | 1.00 | 1.00 | 0.70 | 0.53 |  |
| stm32f4-q009 | stm32f4_gpio_exti_extract.pdf | image_description | 0.9133 | 1.00 | 0.87 | 1.00 | 1.00 | 0.70 |  |

## Lowest-Scoring Question

- **ID**: esp32-q001
- **Query**: ESP32 Deep-sleep 模式下的典型功耗是多少？哪些内容在 Deep-sleep 期间保持供电？
- **Source PDF**: `data/pdfs/mcu/esp32_datasheet.pdf`
- **Average score**: 0.2000
- **Metric breakdown**:
  - answer_relevancy: 0.0000
  - faithfulness: 1.0000
  - context_recall: 0.0000
  - context_precision: 0.0000
  - context_relevancy: 0.0000
- **Actual output preview**: 知识库未找到相关文档，建议查阅官方手册。...