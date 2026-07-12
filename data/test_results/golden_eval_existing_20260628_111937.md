# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_111937
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'kb-b01c8b92'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 82.41/100
- **Sample Count**: 2
- **Error Count**: 0
- **Recall Hit Rate**: 100.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 1.0000 | 30 | 30.0 |
| faithfulness | 0.5000 | 25 | 12.5 |
| answer_relevancy | 1.0000 | 25 | 25.0 |
| context_precision | 0.7454 | 20 | 14.91 |

## Per-Sample Results

| ID | Difficulty | recall_hit | max_sim | match | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|---------|-------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | Y | 0.912 | semantic | 1.00 | 0.00 | 1.00 | 0.73 | 69.7 | 26.9s |
| G002 | 简单 | Y | 0.848 | semantic | 1.00 | 1.00 | 1.00 | 0.76 | 95.2 | 27.8s |

## Low-Score Diagnostics (< 70)

### G001: STM32 的 GPIO 引脚可以配置为哪四种工作模式？每种模式对应的 MODER 寄存器值是什么？
- Weighted score: 69.7
- recall_hit: Y (max_sim=0.912, match=semantic)
- **context_recall** (1.00): The score is 1.00 because all five sentences of the expected output are fully supported by node(s) in retrieval context: sentence 1 (four modes, MODER control) by nodes 1 and 3; sentence 2 (input mode
- **faithfulness** (0.00): Metric error after 3 attempts: RetryError: RetryError[<Future at 0x2394c8fb070 state=finished raised InternalServerError>]
- **answer_relevancy** (1.00): The score is 1.00 because the actual output is fully relevant and contains no irrelevant statements, perfectly addressing the input.
- **context_precision** (0.73): The score is 0.73 because, while the first and most relevant nodes are ranked near the top, two irrelevant nodes (the second and fourth nodes) appear before several relevant nodes. The second node is 
