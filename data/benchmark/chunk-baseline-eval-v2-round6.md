# Chunk-Baseline-v2 DeepEval Round 6 Report

- **Timestamp**: 20260701_113844
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `oc/deepseek-v4-flash` @ `https://9router.zxyzx.bbroot.com/v1`
- **Judge model**: `oc/deepseek-v4-flash` @ `https://9router.zxyzx.bbroot.com/v1`

## Overall Average

**0.7276** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.7308** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 0.8750 | 30 |
| faithfulness | 0.3500 | 25 |
| answer_relevancy | 0.8750 | 25 |
| context_precision | 0.8103 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `stm32f4_gpio_exti_extract.pdf` | 8 | 0.7276 | 0.7308 | 0.8750 | 0.3500 | 0.8750 | 0.8103 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.8908 | 1.00 | 0.80 | 1.00 | 0.76 |  |
| stm32f4-q002 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.7083 | 1.00 | 0.00 | 1.00 | 0.83 |  |
| stm32f4-q003 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.9428 | 1.00 | 1.00 | 1.00 | 0.77 |  |
| stm32f4-q004 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.6979 | 1.00 | 0.00 | 1.00 | 0.79 |  |
| stm32f4-q005 | stm32f4_gpio_exti_extract.pdf | table_query | 0.6215 | 1.00 | 0.00 | 1.00 | 0.49 |  |
| stm32f4-q006 | stm32f4_gpio_exti_extract.pdf | table_query | 0.7500 | 1.00 | 0.00 | 1.00 | 1.00 |  |
| stm32f4-q007 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.9591 | 1.00 | 1.00 | 1.00 | 0.84 |  |
| stm32f4-q008 | stm32f4_gpio_exti_extract.pdf | cross_page | 0.2500 | 0.00 | 0.00 | 0.00 | 1.00 |  |
| stm32f4-q009 | stm32f4_gpio_exti_extract.pdf | image_description | 0.0000 | 0.00 | 0.00 | 0.00 | 0.00 | ConnectError: [WinError 10061] |

## Lowest-Scoring Question

- **ID**: stm32f4-q008
- **Query**: 使用 STM32F4 EXTI 外部中断的完整配置流程是什么？
- **Source PDF**: `data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf`
- **Average score**: 0.2500
- **Metric breakdown**:
  - context_recall: 0.0000
  - faithfulness: 0.0000
  - answer_relevancy: 0.0000
  - context_precision: 1.0000
- **Actual output preview**: 基于提供的参考文档片段，使用 STM32F4 EXTI 外部中断的配置流程需依次完成以下步骤。请注意，GPIO 的输入模式配置以及 NVIC 全局中断使能等环节在片段中未直接描述，具体操作需查阅官方手册。

1. **确定外部中断线与 GPIO 引脚的映射关系**  
   STM32F4 系列中，最多 140 个 GPIO 引脚（PA~PI）通过 16 个外部中断/事件线（EXTI0~EXTI15）连接。每个 EXTI 线对应一组编号相同的引脚（如 PA0、PB0、PC0…均映射到 EXTI0），具体映射方式可参考文档中的图示。[src1]

2. **通过 SYSCFG_EXTICR 寄存...