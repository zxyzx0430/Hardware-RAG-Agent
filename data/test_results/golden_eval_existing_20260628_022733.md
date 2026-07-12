# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_022733
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'n/a'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 28.39/100
- **Sample Count**: 3
- **Error Count**: 1
- **Recall Hit Rate**: 0.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.0000 | 30 | 0.0 |
| faithfulness | 0.0000 | 25 | 0.0 |
| answer_relevancy | 0.9444 | 25 | 23.61 |
| context_precision | 0.2389 | 20 | 4.78 |

## Per-Sample Results

| ID | Difficulty | recall_hit | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | N | 0.00 | 0.00 | 0.89 | 0.00 | 22.2 | 95.2s |
| G002 | 简单 | N | 0.00 | 0.00 | 1.00 | 0.48 | 34.6 | 52.1s |
| G003 | 中等 | N | 0.00 | 0.00 | 0.00 | 0.00 | 0.0 | 0.0s |

## Low-Score Diagnostics (< 70)

### G001: STM32 的 GPIO 引脚可以配置为哪四种工作模式？每种模式对应的 MODER 寄存器值是什么？
- Weighted score: 22.2
- **context_recall** (0.00): Metric error: RetryError: RetryError[<Future at 0x2693e69e350 state=finished raised InternalServerError>]
- **faithfulness** (0.00): Metric error: RetryError: RetryError[<Future at 0x2693f6e10f0 state=finished raised InternalServerError>]
- **answer_relevancy** (0.89): The score is 0.89 because the actual output contains a statement about configuring unused pins to analog mode, which is not directly relevant to listing the four working modes and their MODER register
- **context_precision** (0.00): Metric error: RetryError: RetryError[<Future at 0x2693f714e50 state=finished raised InternalServerError>]

### G002: STM32 GPIO 的输出速度有哪几个等级？在什么场景下应该选择不同的速度？
- Weighted score: 34.6
- **context_recall** (0.00): Metric error: RetryError: RetryError[<Future at 0x2693f70c410 state=finished raised InternalServerError>]
- **faithfulness** (0.00): Metric error: RetryError: RetryError[<Future at 0x2693e6819d0 state=finished raised InternalServerError>]
- **answer_relevancy** (1.00): The score is 1.00 because the answer is fully relevant, directly addressing the question about STM32 GPIO output speed levels and their appropriate use cases, with no irrelevant statements.
- **context_precision** (0.48): The score is 0.48 because the first two nodes are irrelevant: the first node discusses '上下拉电阻 (GPIOx_PUPDR)' and scenarios for pull-up/pull-down, but does not mention GPIO output speed levels; the sec
