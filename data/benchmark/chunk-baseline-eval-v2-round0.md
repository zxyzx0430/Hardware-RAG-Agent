# Chunk-Baseline-v2 DeepEval Round 0 Report

- **Timestamp**: 20260630_204032
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.0000** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.0000** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.0000 | 30 |
| faithfulness | 0.0000 | 25 |
| answer_relevancy | 0.0000 | 25 |
| context_precision | 0.0000 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `ch340g_datasheet.pdf` | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `esp32_datasheet.pdf` | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| `stm32f4_gpio_exti_extract.pdf` | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| ch340g-q001 | ch340g_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q002 | ch340g_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q003 | ch340g_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q004 | ch340g_datasheet.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q005 | ch340g_datasheet.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q006 | ch340g_datasheet.pdf | cross_page | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q007 | ch340g_datasheet.pdf | image_description | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| ch340g-q008 | ch340g_datasheet.pdf | image_description | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q001 | esp32_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q002 | esp32_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q003 | esp32_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q004 | esp32_datasheet.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q005 | esp32_datasheet.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q006 | esp32_datasheet.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q007 | esp32_datasheet.pdf | cross_page | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q008 | esp32_datasheet.pdf | cross_page | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| esp32-q009 | esp32_datasheet.pdf | image_description | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q002 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q003 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q004 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q005 | stm32f4_gpio_exti_extract.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q006 | stm32f4_gpio_exti_extract.pdf | table_query | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q007 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q008 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |
| stm32f4-q009 | stm32f4_gpio_exti_extract.pdf | image_description | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |