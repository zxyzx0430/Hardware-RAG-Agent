# Checklist

## Phase 1: 环境清理

- [ ] `hardware-docs-test` ChromaDB collection 中 chunks 数量为 0
- [ ] SQLite `knowledge_docs` 表中 `kb_id='builtin-001'` 无旧 doc 记录
- [ ] 冲突缓存（golden_ref_embeddings.pkl 等）已清理或重命名归档
- [ ] 清理脚本可重复运行且幂等

## Phase 2: 3 个代表 PDF 重新入库

- [x] ch340g_datasheet.pdf 使用 MultimodalChunker 成功入库（doc_id=`12f13dda-4d71-4a4a-9870-fe7e9d7e5062`）
- [ ] stm32f4_gpio_exti_extract.pdf 使用 HybridChunker 成功入库
- [ ] esp32_datasheet.pdf 使用 HybridChunker 成功入库
- [x] ch340g 的 doc_id、chunk 总数（48）、text/img 分布（36/12）、耗时（≈882.8s）已记录
- [ ] stm32f4 / esp32 的入库结果待记录
- [x] ch340g 入库脚本对 batch/call 设置了 5 分钟超时保护（`timeout=300.0`）
- [ ] stm32f4 / esp32 入库超时保护待验证

## Phase 3: 逐页 chunk 完整性确认

- [x] ch340g 每页都有至少一个 text 或 image_description chunk 覆盖（覆盖率 100%）
- [x] ch340g 引脚表、电气特性表、封装图、应用电路图无遗漏（表格/图片覆盖率 100%）
- [x] ch340g 重复率 < 5%（实际 0.00%）
- [ ] stm32f4_gpio_exti_extract 每页都有至少一个 chunk 覆盖
- [ ] stm32f4_gpio_exti_extract 寄存器表、代码示例、框图无遗漏
- [ ] stm32f4_gpio_exti_extract 重复率 < 5%
- [ ] esp32_datasheet 每页都有至少一个 chunk 覆盖
- [ ] esp32_datasheet 引脚图、电气特性表、功能框图无遗漏
- [ ] esp32_datasheet 重复率 < 5%
- [ ] ch340g 逐页 audit 报告已生成（`docs/reports/audit_baseline_ch340g_20260630.md`）
- [ ] stm32f4 / esp32 逐页 audit 报告待生成

## Phase 4: 构造全新 Golden Answers

- [x] ch340g 有 5–10 个全新的 golden Q&A（实际 8 题）
- [x] stm32f4_gpio_exti_extract 有 5–10 个全新的 golden Q&A（实际 9 题）
- [x] esp32_datasheet 有 5–10 个全新的 golden Q&A（实际 9 题）
- [x] 每个 golden answer 都有明确的 PDF 来源 page 和 chunk 依据
- [x] `data/benchmark/chunk-baseline-golden-v1.yaml` 已保存并通过格式校验
- [x] 未复用任何历史 golden dataset 的问题/答案
- [x] 检索验证报告 `chunk-baseline-golden-v1-retrieval-gap-report.{json,md}` 已生成

## Phase 5: DeepEval 基线评估

- [x] DeepEval 运行环境配置正确（模型参数、API key 从 `.env` 读取；Judge 使用 DashScope `qwen-turbo`，生成模型按 `.env` 使用 `oc/deepseek-v4-flash@9router`，已在报告中注明）
- [x] DeepEval 成功跑完所有 26 道 golden questions（成功 26 / 总 26，无失败）
- [x] 评估结果包含 per-question 和 overall 指标
- [x] `data/benchmark/chunk-baseline-eval-v1.json` 已保存
- [x] 报告包含 answer_relevancy / faithfulness / context_recall / context_precision / context_relevancy

## Phase 6: 文档与基线封存

- [ ] `docs/reports/chunk-baseline-audit-2026-06-30.md` 已生成
- [ ] 报告末尾明确标注"此为 v1 永久基线"
- [ ] `docs/completed.md` 已追加 chunk 基准建立记录
- [ ] `docs/pitfalls.md` 已记录本次新坑
- [ ] `data/benchmark/README.md` 记录了关键脚本/命令
- [ ] benchmark 文件不在 `.gitignore` 中，可被提交

## Phase 7: 全量入库准备（待用户确认）

- [ ] `data/pdfs/` 下所有 PDF 清单已列出
- [ ] 每类文档的 chunker 策略已确定
- [ ] 等待用户在 3-PDF 质量确认后批准全量入库
