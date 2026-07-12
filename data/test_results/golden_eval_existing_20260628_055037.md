# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_055037
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'kb-b01c8b92'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 89.74/100
- **Sample Count**: 3
- **Error Count**: 0
- **Recall Hit Rate**: 100.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.9524 | 30 | 28.57 |
| faithfulness | 1.0000 | 25 | 25.0 |
| answer_relevancy | 0.9722 | 25 | 24.3 |
| context_precision | 0.5933 | 20 | 11.87 |

## Per-Sample Results

| ID | Difficulty | recall_hit | max_sim | match | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|---------|-------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | Y | 0.912 | semantic | 1.00 | 1.00 | 1.00 | 0.70 | 93.9 | 18.9s |
| G002 | 简单 | Y | 0.767 | semantic | 0.86 | 1.00 | 1.00 | 0.78 | 91.3 | 21.0s |
| G003 | 中等 | Y | 0.715 | semantic | 1.00 | 1.00 | 0.92 | 0.30 | 84.0 | 20.8s |