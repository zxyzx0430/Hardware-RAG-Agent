# Chunk-Baseline-v2 DeepEval Round 1 Report

- **Timestamp**: 20260630_220102
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.9506** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.9559** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 1.0000 | 30 |
| faithfulness | 1.0000 | 25 |
| answer_relevancy | 0.9100 | 25 |
| context_precision | 0.8922 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `ch340g_datasheet.pdf` | 5 | 0.9506 | 0.9559 | 1.0000 | 1.0000 | 0.9100 | 0.8922 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| ch340g-q001 | ch340g_datasheet.pdf | fact_extraction | 0.9545 | 1.00 | 1.00 | 1.00 | 0.82 |  |
| ch340g-q002 | ch340g_datasheet.pdf | fact_extraction | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |
| ch340g-q003 | ch340g_datasheet.pdf | fact_extraction | 0.8638 | 1.00 | 1.00 | 0.75 | 0.71 |  |
| ch340g-q004 | ch340g_datasheet.pdf | table_query | 0.9345 | 1.00 | 1.00 | 0.80 | 0.94 |  |
| ch340g-q005 | ch340g_datasheet.pdf | table_query | 1.0000 | 1.00 | 1.00 | 1.00 | 1.00 |  |

## Lowest-Scoring Question

- **ID**: ch340g-q003
- **Query**: CH340G 支持哪些硬件流控信号？
- **Source PDF**: `data/pdfs/interface/ch340g_datasheet.pdf`
- **Average score**: 0.8638
- **Metric breakdown**:
  - context_recall: 1.0000
  - faithfulness: 1.0000
  - answer_relevancy: 0.7500
  - context_precision: 0.7052
- **Actual output preview**: 根据 CH340G 数据手册，其支持的硬件流控信号（MODEM 联络信号）包括：

- **CTS#**（Clear to Send，清除发送）[src1]
- **DSR#**（Data Set Ready，数据设备就绪）[src1]
- **RI#**（Ring Indicator，振铃指示）[src1]
- **DCD#**（Data Carrier Detect，数据载波检测）[src1]
- **DTR#**（Data Terminal Ready，数据终端就绪）[src1]
- **RTS#**（Request to Send，请求发送）[src1]

这些信号均为低电平有效，其功...