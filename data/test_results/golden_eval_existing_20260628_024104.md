# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_024104
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'n/a'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 36.78/100
- **Sample Count**: 1
- **Error Count**: 0
- **Recall Hit Rate**: 0.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.0000 | 30 | 0.0 |
| faithfulness | 0.0000 | 25 | 0.0 |
| answer_relevancy | 1.0000 | 25 | 25.0 |
| context_precision | 0.5889 | 20 | 11.78 |

## Per-Sample Results

| ID | Difficulty | recall_hit | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | N | 0.00 | 0.00 | 1.00 | 0.59 | 36.8 | 115.8s |

## Low-Score Diagnostics (< 70)

### G001: STM32 的 GPIO 引脚可以配置为哪四种工作模式？每种模式对应的 MODER 寄存器值是什么？
- Weighted score: 36.8
- **context_recall** (0.00): Metric error: RetryError: RetryError[<Future at 0x261e822f9d0 state=finished raised InternalServerError>]
- **faithfulness** (0.00): Metric error: RetryError: RetryError[<Future at 0x261e85bd350 state=finished raised InternalServerError>]
- **answer_relevancy** (1.00): The score is 1.00 because the output is fully relevant with no irrelevant statements, achieving the highest possible score.
- **context_precision** (0.59): The score is 0.59 because the first node (rank 1) is irrelevant, as it '未提及四种工作模式或MODER寄存器值', and should be ranked lower than the relevant nodes. Relevant nodes at ranks 2 and 3 provide partial MODER 
