# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_095058
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'kb-b01c8b92'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 83.01/100
- **Sample Count**: 2
- **Error Count**: 0
- **Recall Hit Rate**: 100.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.5000 | 30 | 15.0 |
| faithfulness | 1.0000 | 25 | 25.0 |
| answer_relevancy | 1.0000 | 25 | 25.0 |
| context_precision | 0.9005 | 20 | 18.01 |

## Per-Sample Results

| ID | Difficulty | recall_hit | max_sim | match | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|---------|-------|----------------|-------------|-----------------|-------------------|----------|---------|
| G004 | 中等 | Y | 0.782 | semantic | 0.00 | 1.00 | 1.00 | 0.84 | 66.8 | 23.6s |
| G005 | 中等 | Y | 0.918 | semantic | 1.00 | 1.00 | 1.00 | 0.96 | 99.2 | 20.2s |

## Low-Score Diagnostics (< 70)

### G004: STM32 的外部中断 EXTI 如何配置？EXTI5-9 为什么共享同一个中断向量？
- Weighted score: 66.8
- recall_hit: Y (max_sim=0.782, match=semantic)
- **context_recall** (0.00): Metric error after 3 attempts: RetryError: RetryError[<Future at 0x1d44c69d910 state=finished raised InternalServerError>]
- **faithfulness** (1.00): The score is 1.00 because there are no contradictions; the actual output fully aligns with the retrieval context, demonstrating perfect faithfulness.
- **answer_relevancy** (1.00): The score is 1.00 because the output directly addresses both questions in the input with no irrelevant statements. Excellent job!
- **context_precision** (0.84): The score is 0.84 because the first three nodes (ranks 1–3) are relevant and correctly ranked high, covering EXTI configuration and vector sharing. However, three irrelevant nodes at ranks 4–6—which, 
