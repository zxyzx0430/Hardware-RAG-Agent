# Golden Dataset Evaluation Report

- **Timestamp**: 20260628_135847
- **Strategy**: {'chunk_method': 'existing', 'chunk_size': 'n/a', 'kb_id': 'builtin-001'}
- **LLM Judge**: {'model': 'oc/deepseek-v4-flash', 'base_url': 'https://9router.zxyzx.bbroot.com/v1'}
- **Total Score**: 74.14/100
- **Sample Count**: 5
- **Error Count**: 0
- **Recall Hit Rate**: 100.0%

## Dimension Scores

| Metric | Score | Weight | Weighted |
|--------|-------|--------|----------|
| context_recall | 0.4540 | 30 | 13.62 |
| faithfulness | 0.8500 | 25 | 21.25 |
| answer_relevancy | 0.9333 | 25 | 23.33 |
| context_precision | 0.7969 | 20 | 15.94 |

## Per-Sample Results

| ID | Difficulty | recall_hit | max_sim | match | context_recall | faithfulness | answer_relevancy | context_precision | Weighted | Latency |
|----|-----------|------------|---------|-------|----------------|-------------|-----------------|-------------------|----------|---------|
| G001 | 简单 | Y | 0.777 | semantic | 1.00 | 1.00 | 1.00 | 0.72 | 94.3 | 29.3s |
| G002 | 简单 | Y | 0.882 | semantic | 0.71 | 0.75 | 1.00 | 1.00 | 85.2 | 26.0s |
| G003 | 中等 | Y | 0.911 | semantic | 0.33 | 1.00 | 1.00 | 0.62 | 72.5 | 22.3s |
| G004 | 中等 | Y | 0.715 | semantic | 0.22 | 0.50 | 1.00 | 0.64 | 57.0 | 20.0s |
| G005 | 中等 | Y | 0.753 | semantic | 0.00 | 1.00 | 0.67 | 1.00 | 61.7 | 28.5s |

## Low-Score Diagnostics (< 70)

### G004: STM32 的外部中断 EXTI 如何配置？EXTI5-9 为什么共享同一个中断向量？
- Weighted score: 57.0
- recall_hit: Y (max_sim=0.715, match=semantic)
- **context_recall** (0.22): The score is 0.22 because only the second sentence (GPIO input configuration) is partially supported by node 1 in the retrieval context, and the seventh sentence (NVIC configuration) is supported by n
- **faithfulness** (0.50): The score is 0.50 because the actual output incorrectly claims no relevant documents were found, but the retrieval context contains detailed technical information about STM32 peripherals, directly con
- **answer_relevancy** (1.00): The score is 1.00 because the actual output directly and fully answers the user's questions about STM32 EXTI configuration and the shared interrupt vector for EXTI5-9, with no irrelevant statements at
- **context_precision** (0.64): The score is 0.64 because the first retrieval node is relevant ('可用作外部中断/事件输入'), but irrelevant nodes are interspersed: the second ('UART通信问题排查') and third ('仅描述GPIO模块架构') are irrelevant but ranked be

### G005: STM32 GPIO 的锁定机制 LCKR 如何配置？写出 5 步写序列。
- Weighted score: 61.7
- recall_hit: Y (max_sim=0.753, match=semantic)
- **context_recall** (0.00): Metric error after 3 attempts: RetryError: RetryError[<Future at 0x15c0825add0 state=finished raised InternalServerError>]
- **faithfulness** (1.00): The score is 1.00 because there are no contradictions, meaning the actual output is completely faithful to the retrieval context. Well done!
- **answer_relevancy** (0.67): The score is 0.67 because the actual output does not provide the requested LCKR configuration and only states lack of documents, which is irrelevant to the user's request. It is not higher because the
- **context_precision** (1.00): The score is 1.00 because the first node, stating '文档中直接提到了 LCKR 寄存器及其功能'配置锁定（写入序列解锁）'，这暗示了存在写入序列', is the only relevant node and is ranked highest, while nodes 2-8 are irrelevant and correctly follow
