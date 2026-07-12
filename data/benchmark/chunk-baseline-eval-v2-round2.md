# Chunk-Baseline-v2 DeepEval Round 2 Report

- **Timestamp**: 20260630_232002
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.9338** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.9370** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.9333 | 30 |
| faithfulness | 1.0000 | 25 |
| answer_relevancy | 0.9318 | 25 |
| context_precision | 0.8701 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `stm32f4_gpio_exti_extract.pdf` | 9 | 0.9338 | 0.9370 | 0.9333 | 1.0000 | 0.9318 | 0.8701 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.9688 | 1.00 | 1.00 | 1.00 | 0.88 |  |
| stm32f4-q002 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| stm32f4-q003 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| stm32f4-q004 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.9688 | 1.00 | 1.00 | 1.00 | 0.88 |  |
| stm32f4-q005 | stm32f4_gpio_exti_extract.pdf | table_query | 0.9583 | 1.00 | 1.00 | 1.00 | 0.83 |  |
| stm32f4-q006 | stm32f4_gpio_exti_extract.pdf | table_query | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| stm32f4-q007 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.9411 | 1.00 | 1.00 | 0.91 | 0.86 |  |
| stm32f4-q008 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.6774 | 0.40 | 1.00 | 0.75 | 0.56 |  |
| stm32f4-q009 | stm32f4_gpio_exti_extract.pdf | image_description | 0.8902 | 1.00 | 1.00 | 0.73 | 0.83 |  |

## Lowest-Scoring Question

- **ID**: stm32f4-q008
- **Query**: 使用 STM32F4 EXTI 外部中断的完整配置流程是什么？
- **Source PDF**: `data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf`
- **Average score**: 0.6774
- **Metric breakdown**:
  - context_recall: 0.4000
  - faithfulness: 1.0000
  - answer_relevancy: 0.7500
  - context_precision: 0.5595
- **Actual output preview**: 基于 STM32F4 参考手册（RM0090）的相关章节，EXTI 外部中断的配置流程包括以下步骤。部分步骤所需寄存器（如 GPIO 模式、EXTI 掩码、NVIC 配置）未包含在提供的参考文档片段中，因此仅列出有据可查的部分，缺失的配置需参考官方手册 GPI/O、EXTI 和 NVIC 章节。

### 1. GPIO 引脚模式配置
将目标 GPIO 引脚配置为**输入模式**（`MODER[1:0]=00`）。  
**⚠️ 知识库未提供 GPIO 模式配置寄存器（如 MODER、PUPDR）的详细信息，建议查阅 《RM0090》第 8 章“GPIO”完成此步骤。**

### 2. 使能...