# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_020910
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'n/a'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 0.0/100
- **Sample Count**: 3
- **Error Count**: 0
- **Recall Hit Rate**: 0.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.0000 | 30 | 0.0 |
| faithfulness | 0.0000 | 25 | 0.0 |
| answer_relevancy | 0.0000 | 25 | 0.0 |
| context_precision | 0.0000 | 20 | 0.0 |

## Per-Sample Results

| ID | Difficulty | recall_hit | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | N | 0.00 | 0.00 | 0.00 | 0.00 | 0.0 | 149.9s |
| G002 | 简单 | N | 0.00 | 0.00 | 0.00 | 0.00 | 0.0 | 52.6s |
| G003 | 中等 | N | 0.00 | 0.00 | 0.00 | 0.00 | 0.0 | 34.5s |

## Low-Score Diagnostics (< 70)

### G001: STM32 的 GPIO 引脚可以配置为哪四种工作模式？每种模式对应的 MODER 寄存器值是什么？
- Weighted score: 0.0
- **context_recall** (0.00): Metric error: AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-ff971***********************7399. You can find your API key at https://platform.openai.com/ac
- **faithfulness** (0.00): Metric error: MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Faithfulness' metric
- **answer_relevancy** (0.00): Metric error: MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Answer Relevancy' metric
- **context_precision** (0.00): Metric error: AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-ff971***********************7399. You can find your API key at https://platform.openai.com/ac

### G002: STM32 GPIO 的输出速度有哪几个等级？在什么场景下应该选择不同的速度？
- Weighted score: 0.0
- **context_recall** (0.00): Metric error: AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-ff971***********************7399. You can find your API key at https://platform.openai.com/ac
- **faithfulness** (0.00): Metric error: MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Faithfulness' metric
- **answer_relevancy** (0.00): Metric error: MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Answer Relevancy' metric
- **context_precision** (0.00): Metric error: AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-ff971***********************7399. You can find your API key at https://platform.openai.com/ac

### G003: STM32 不同系列的 GPIO 时钟使能寄存器有什么差异？配置 GPIO 前必须先做什么？
- Weighted score: 0.0
- **context_recall** (0.00): Metric error: AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-ff971***********************7399. You can find your API key at https://platform.openai.com/ac
- **faithfulness** (0.00): Metric error: MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Faithfulness' metric
- **answer_relevancy** (0.00): Metric error: MissingTestCaseParamsError: 'actual_output' cannot be empty for the 'Answer Relevancy' metric
- **context_precision** (0.00): Metric error: AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-ff971***********************7399. You can find your API key at https://platform.openai.com/ac
