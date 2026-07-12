# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_104758
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'kb-b01c8b92'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 95.18/100
- **Sample Count**: 5
- **Error Count**: 2
- **Recall Hit Rate**: 60.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 1.0000 | 30 | 30.0 |
| faithfulness | 0.9333 | 25 | 23.33 |
| answer_relevancy | 0.9762 | 25 | 24.4 |
| context_precision | 0.8722 | 20 | 17.44 |

## Per-Sample Results

| ID | Difficulty | recall_hit | max_sim | match | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|---------|-------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | N | - | - | 0.00 | 0.00 | 0.00 | 0.00 | 0.0 | 0.0s |
| G002 | 简单 | N | - | - | 0.00 | 0.00 | 0.00 | 0.00 | 0.0 | 0.0s |
| G003 | 中等 | Y | 0.914 | semantic | 1.00 | 0.80 | 1.00 | 1.00 | 95.0 | 236.9s |
| G004 | 中等 | Y | 0.795 | semantic | 1.00 | 1.00 | 0.93 | 0.69 | 91.9 | 31.0s |
| G005 | 中等 | Y | 0.918 | semantic | 1.00 | 1.00 | 1.00 | 0.93 | 98.6 | 20.8s |