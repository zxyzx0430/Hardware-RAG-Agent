# Chunk-Baseline-v2 DeepEval Round 9 Report

- **Timestamp**: 20260701_184748
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `oc/deepseek-v4-flash` @ `https://9router.zxyzx.bbroot.com/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.7977** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.8307** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 1.0000 | 30 |
| faithfulness | 0.9412 | 25 |
| answer_relevancy | 0.9091 | 25 |
| context_precision | 0.3407 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `stm32f4_gpio_exti_extract.pdf` | 1 | 0.7977 | 0.8307 | 1.0000 | 0.9412 | 0.9091 | 0.3407 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| stm32f4-q001 | stm32f4_gpio_exti_extract.pdf | fact_extraction | 0.7978 | 1.00 | 0.94 | 0.91 | 0.34 |  |

## Lowest-Scoring Question

- **ID**: stm32f4-q001
- **Query**: STM32F4 GPIO 端口复位后的默认模式是什么？
- **Source PDF**: `data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf`
- **Average score**: 0.7978
- **Metric breakdown**:
  - context_recall: 1.0000
  - faithfulness: 0.9412
  - answer_relevancy: 0.9091
  - context_precision: 0.3407
- **Actual output preview**: ## STM32F4 GPIO 复位后默认模式

根据 STM32F4 参考手册（RM0090），GPIO 端口复位后的默认模式由 **GPIOx_MODER**（端口模式寄存器）的复位值决定，编码规则如下[src9][src10]：

| MODER[1:0] | 模式       |
|------------|------------|
| 00         | **输入**（Input，复位状态） |
| 01         | 通用输出   |
| 10         | 复用功能   |
| 11         | 模拟模式   |

### 各端口复位模式详情

| 端...