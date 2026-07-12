# Chunk-Baseline-v2 DeepEval Round 4 Report

- **Timestamp**: 20260701_032413
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.8432** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.8424** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.7333 | 30 |
| faithfulness | 0.9630 | 25 |
| answer_relevancy | 0.9259 | 25 |
| context_precision | 0.7507 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `stm32f4_gpio_exti_extract.pdf` | 9 | 0.8432 | 0.8424 | 0.7333 | 0.9630 | 0.9259 | 0.7507 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.7812 | 1.00 | 1.00 | 1.00 | 0.12 |  |
| stm32f4-q002 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| stm32f4-q003 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| stm32f4-q004 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8021 | 1.00 | 1.00 | 1.00 | 0.21 |  |
| stm32f4-q005 | stm32f4_gpio_exti_extract.pdf | table_query | 0.5000 | 0.00 | 0.67 | 0.33 | 1.00 |  |
| stm32f4-q006 | stm32f4_gpio_exti_extract.pdf | table_query | 0.7500 | 0.00 | 1.00 | 1.00 | 1.00 |  |
| stm32f4-q007 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.9638 | 1.00 | 1.00 | 1.00 | 0.86 |  |
| stm32f4-q008 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.8565 | 0.60 | 1.00 | 1.00 | 0.83 |  |
| stm32f4-q009 | stm32f4_gpio_exti_extract.pdf | image_description | 0.9354 | 1.00 | 1.00 | 1.00 | 0.74 |  |

## Lowest-Scoring Question

- **ID**: stm32f4-q005
- **Query**: STM32F4 GPIOx_MODER 寄存器中，MODERy[1:0] 取 01、10、11 时分别对应什么 I/O 模式？
- **Source PDF**: `data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf`
- **Average score**: 0.5000
- **Metric breakdown**:
  - context_recall: 0.0000
  - faithfulness: 0.6667
  - answer_relevancy: 0.3333
  - context_precision: 1.0000
- **Actual output preview**: 知识库未找到相关文档，无法提供GPIOx_MODER寄存器中MODERy[1:0]取01、10、11时对应的I/O模式。建议查阅STM32F4参考手册（RM0090）中关于GPIOx_MODER寄存器的详细描述。...