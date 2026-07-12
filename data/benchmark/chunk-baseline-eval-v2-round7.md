# Chunk-Baseline-v2 DeepEval Round 7 Report

- **Timestamp**: 20260701_144757
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `oc/deepseek-v4-flash` @ `https://9router.zxyzx.bbroot.com/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.8352** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.8479** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.8667 | 30 |
| faithfulness | 0.9722 | 25 |
| answer_relevancy | 0.8885 | 25 |
| context_precision | 0.6135 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `stm32f4_gpio_exti_extract.pdf` | 9 | 0.8352 | 0.8479 | 0.8667 | 0.9722 | 0.8885 | 0.6135 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.5587 | 0.00 | 0.75 | 0.88 | 0.61 |  |
| stm32f4-q002 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8630 | 1.00 | 1.00 | 0.88 | 0.58 |  |
| stm32f4-q003 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8393 | 1.00 | 1.00 | 0.90 | 0.45 |  |
| stm32f4-q004 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8125 | 1.00 | 1.00 | 1.00 | 0.25 |  |
| stm32f4-q005 | stm32f4_gpio_exti_extract.pdf | table_query | 0.8139 | 1.00 | 1.00 | 0.67 | 0.59 |  |
| stm32f4-q006 | stm32f4_gpio_exti_extract.pdf | table_query | 0.9688 | 1.00 | 1.00 | 0.88 | 1.00 |  |
| stm32f4-q007 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.9140 | 1.00 | 1.00 | 0.93 | 0.72 |  |
| stm32f4-q008 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.8704 | 0.80 | 1.00 | 1.00 | 0.68 |  |
| stm32f4-q009 | stm32f4_gpio_exti_extract.pdf | image_description | 0.8765 | 1.00 | 1.00 | 0.87 | 0.64 |  |

## Lowest-Scoring Question

- **ID**: stm32f4-q001
- **Query**: STM32F4 GPIO 端口复位后的默认模式是什么？
- **Source PDF**: `data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf`
- **Average score**: 0.5587
- **Metric breakdown**:
  - context_recall: 0.0000
  - faithfulness: 0.7500
  - answer_relevancy: 0.8750
  - context_precision: 0.6098
- **Actual output preview**: 我来查询 STM32F4 系列参考手册中关于 GPIO 复位状态的描述。让我再查一下更详细的寄存器复位值信息。根据 STM32F4 参考手册（RM0090 Rev 18）的搜索结果，以下是详细的 GPIO 端口复位默认状态分析。

---

## STM32F4 GPIO 复位后的默认模式

### 1. 核心结论：大部分引脚 → **输入浮空（Input Floating）**

复位后，所有 GPIO 端口（GPIOA ~ GPIOK）的默认配置如下：

| 寄存器 | 默认值 | 含义 |
|--------|--------|------|
| **GPIOx_MODER**（模式） ...