# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_032201
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'n/a'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 69.78/100
- **Sample Count**: 3
- **Error Count**: 0
- **Recall Hit Rate**: 0.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.3397 | 30 | 10.19 |
| faithfulness | 0.8667 | 25 | 21.67 |
| answer_relevancy | 0.8879 | 25 | 22.2 |
| context_precision | 0.7861 | 20 | 15.72 |

## Per-Sample Results

| ID | Difficulty | recall_hit | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | N | 0.40 | 0.60 | 0.80 | 0.68 | 60.6 | 80.9s |
| G002 | 简单 | N | 0.29 | 1.00 | 1.00 | 0.68 | 72.2 | 37.7s |
| G003 | 中等 | N | 0.33 | 1.00 | 0.86 | 1.00 | 76.6 | 39.9s |

## Low-Score Diagnostics (< 70)

### G001: STM32 的 GPIO 引脚可以配置为哪四种工作模式？每种模式对应的 MODER 寄存器值是什么？
- Weighted score: 60.6
- **context_recall** (0.40): The score is 0.40 because out of the four mode descriptions in the expected output, only the input mode (item 1) and analog mode (item 4) are supported by node(s) in retrieval context (node 2), while 
- **faithfulness** (0.60): The score is 0.60 because the actual output contains key inaccuracies regarding MODER register assignments, directly contradicting the retrieval context: it mistakenly assigns MODER=01 to output mode 
- **answer_relevancy** (0.80): The score is 0.80 because the answer provides the required information about the four GPIO modes and MODER register values, but also includes irrelevant statements such as specific configuration examp
- **context_precision** (0.68): The score is 0.68 because the first-ranked node is irrelevant since it 'only describes GPIO module architecture and does not mention the four working modes or their MODER register values' and should b
