# Chunk-Baseline-v2 DeepEval Round 8 Report

- **Timestamp**: 20260701_132038
- **Dataset**: `E:\Desktop\agent\data\benchmark\chunk-baseline-golden-v1.yaml`
- **KB ID**: builtin-001
- **Collection**: hardware-docs-test
- **Generation model**: `oc/deepseek-v4-flash` @ `https://9router.zxyzx.bbroot.com/v1`
- **Judge model**: `deepseek-v4-flash` @ `https://opencode.ai/zen/go/v1`

## Overall Average

**0.8906** (simple average of 4 DeepEval metrics, 0-1 scale)
**0.9125** (weighted: CR 30 / FA 25 / AR 25 / CP 20)

| Metric | Score | Weight |
|--------|-------|--------|
| context_recall | 1.0000 | 30 |
| faithfulness | 1.0000 | 25 |
| answer_relevancy | 1.0000 | 25 |
| context_precision | 0.5624 | 20 |

## Per-PDF Average

| PDF | Count | Avg | Weighted | context_recall | faithfulness | answer_relevancy | context_precision |
|-----|-------|-----|----------|-------|-------|-------|-------|
| `ch340g_datasheet.pdf` | 1 | 0.8906 | 0.9125 | 1.0000 | 1.0000 | 1.0000 | 0.5624 |

## Per-Question Scores

| ID | PDF | Type | Avg | CR | FA | AR | CP | Error |
|----|-----|------|-----|----|----|----|----|-------|
| ch340g-q003 | ch340g_datasheet.pdf | fact_extraction | 0.8906 | 1.00 | 1.00 | 1.00 | 0.56 |  |

## Lowest-Scoring Question

- **ID**: ch340g-q003
- **Query**: CH340G 支持哪些硬件流控信号？
- **Source PDF**: `data/pdfs/interface/ch340g_datasheet.pdf`
- **Average score**: 0.8906
- **Metric breakdown**:
  - context_recall: 1.0000
  - faithfulness: 1.0000
  - answer_relevancy: 1.0000
  - context_precision: 0.5624
- **Actual output preview**: # CH340G 硬件流控信号

根据 CH340G 数据手册，CH340G 支持完整的 **MODEM 联络信号（硬件流控信号）**，共 **6 个**，均为 **低电平有效**（用 `#` 标示）：

| 信号名 | 引脚号 | 方向 | 说明 |
|--------|:------:|:----:|------|
| **CTS#** | 9 | Input | Clear to Send — 允许发送（清除发送） |
| **DSR#** | 10 | Input | Data Set Ready — 数据设备就绪 |
| **RI#** | 11 | Input | Ring Ind...