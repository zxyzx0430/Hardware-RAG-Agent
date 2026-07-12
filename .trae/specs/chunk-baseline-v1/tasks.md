# Tasks

## Phase 1: 环境清理

- [ ] Task 1: 清理内置硬件手册库及测试残留数据
  - [ ] SubTask 1.1: 删除 ChromaDB collection `hardware-docs-test` 中的所有 chunks
  - [ ] SubTask 1.2: 删除 SQLite `knowledge_docs` 表中 `kb_id='builtin-001'` 的历史记录
  - [ ] SubTask 1.3: 清理冲突的 golden eval 缓存（`golden_ref_embeddings.pkl` 等旧缓存，保留历史报告仅作归档）
  - [ ] SubTask 1.4: 验证清理结果：collection count == 0，SQLite 中 builtin-001 无旧 doc 记录

## Phase 2: 3 个代表 PDF 重新入库

- [x] Task 2: ch340g_datasheet.pdf 重新入库（MultimodalChunker）— doc_id=`12f13dda-4d71-4a4a-9870-fe7e9d7e5062`，总 chunks=48，text=36，image_description=12，耗时≈882.8s
  - [x] SubTask 2.1: 编写/复用入库脚本，指定 chunker=multimodal，kb=built-in-001 → `scripts/reindex_baseline_ch340g.py`
  - [x] SubTask 2.2: 运行入库，设置 5 分钟 batch 超时保护 → `MultimodalChunker` 传入 `timeout=300.0`，脚本总超时 1800.0
  - [x] SubTask 2.3: 记录入库结果 → `data/benchmark/ch340g_audit_summary.json`

- [x] Task 3: stm32f4_gpio_exti_extract.pdf 重新入库（HybridChunker）
  - [x] SubTask 3.1: 编写/复用入库脚本，指定 chunker=hybrid，kb=built-in-001
  - [x] SubTask 3.2: 运行入库
  - [x] SubTask 3.3: 记录入库结果（doc_id=baseline-stm32f4-gpio-exti，总 chunks=137，text chunks=137，ingested=133，耗时 62.11s）

- [ ] Task 4: esp32_datasheet.pdf 重新入库（HybridChunker）
  - [ ] SubTask 4.1: 编写/复用入库脚本，指定 chunker=hybrid，kb=built-in-001
  - [ ] SubTask 4.2: 运行入库
  - [ ] SubTask 4.3: 记录入库结果

## Phase 3: 逐页 chunk 完整性确认

- [x] Task 5: ch340g 逐页 audit — 14 页 100% 覆盖，无自动检测问题
  - [x] SubTask 5.1: 将 PDF 每页渲染为图片 → `data/benchmark/ch340g_audit_pages/page_*.png`
  - [x] SubTask 5.2: 对每个 page 检索覆盖 chunks 并核对文本/表格/图片 → 页面/表格/图片覆盖率均 100%
  - [x] SubTask 5.3: 输出 audit 报告 → `docs/reports/audit_baseline_ch340g_20260630.md`

- [x] Task 6: stm32f4_gpio_exti_extract 逐页 audit
  - [x] SubTask 6.1: 将 PDF 每页渲染为图片/文本，按页读取内容
  - [x] SubTask 6.2: 对每个 page，核对寄存器表、代码块、框图是否完整 chunk
  - [x] SubTask 6.3: 输出 audit 报告（docs/reports/audit_baseline_stm32f4_20260630.md，44 页 100% 覆盖，但检出 50 处 table/register_table 页范围归属问题）

- [ ] Task 7: esp32_datasheet 逐页 audit
  - [ ] SubTask 7.1: 将 PDF 每页渲染为图片/文本，按页读取内容
  - [ ] SubTask 7.2: 对每个 page，核对引脚图、电气特性表、功能框图是否完整 chunk
  - [ ] SubTask 7.3: 输出 audit 报告

- [ ] Task 8: 汇总 audit 结果并修复阻塞问题
  - [ ] SubTask 8.1: 汇总 3 份 audit 报告，计算重复率、覆盖率、mid-sentence 率
  - [ ] SubTask 8.2: 如发现阻塞问题（重复率 >5%、关键页遗漏、表格严重切碎），修复后重新入库并重新 audit
  - [ ] SubTask 8.3: 确认 3 个 PDF 均达到可进入 golden answer 阶段的质量门槛

## Phase 4: 构造全新 Golden Answers

- [x] Task 9: ch340g golden Q&A 构造 — 生成 8 题（fact_extraction×3 / table_query×2 / cross_page×1 / image_description×2），保存 `data/benchmark/ch340g_golden_v1.json`
  - [x] SubTask 9.1: 从 PDF 中提炼 5–10 个问题，覆盖引脚功能、电气特性、封装图、应用电路
  - [x] SubTask 9.2: 为每个问题写 expected_answer，标明来源 page/chunk
  - [x] SubTask 9.3: 通过检索验证 relevant_chunks 能找到答案依据

- [x] Task 10: stm32f4_gpio_exti_extract golden Q&A 构造 — 生成 9 题（fact_extraction×4 / table_query×2 / cross_page×2 / image_description×1），保存 `data/benchmark/stm32f4_golden_v1.json`
  - [x] SubTask 10.1: 提炼 5–10 个问题，覆盖 GPIO 模式、MODER/OTYPER/OSPEEDR/PUPDR 寄存器、EXTI 配置
  - [x] SubTask 10.2: 写 expected_answer 并标明来源
  - [x] SubTask 10.3: 检索验证 relevant_chunks

- [x] Task 11: esp32_datasheet golden Q&A 构造 — 生成 9 题（fact_extraction×4 / table_query×2 / cross_page×2 / image_description×1），保存 `data/benchmark/esp32_golden_v1.json`
  - [x] SubTask 11.1: 提炼 5–10 个问题，覆盖 strapping pins、GPIO 限制、功耗、封装、外设接口
  - [x] SubTask 11.2: 写 expected_answer 并标明来源
  - [x] SubTask 11.3: 检索验证 relevant_chunks

- [x] Task 12: 合并并保存 golden dataset，并输出 retrieval gap 报告
  - [x] SubTask 12.1: 将 3 个 PDF 的 golden Q&A 合并为单一 YAML
  - [x] SubTask 12.2: 保存到 `data/benchmark/chunk-baseline-golden-v1.yaml`
  - [x] SubTask 12.3: 校验 YAML 格式与 schema
  - [x] SubTask 12.4: 编写 `scripts/analyze_golden_retrieval_gap.py`，生成 `data/benchmark/chunk-baseline-golden-v1-retrieval-gap-report.{json,md}`

## Phase 5: DeepEval 基线评估

- [x] Task 13: 配置 DeepEval 运行环境
  - [x] SubTask 13.1: 确认 `backend/tests/rag_eval/run_golden_eval.py` 支持自定义 golden dataset 路径（支持 `--dataset`；包装脚本 `scripts/run_baseline_deepeval.py` 已创建并指定默认路径）
  - [x] SubTask 13.2: 配置模型参数，使用 `.env` 中的 LLM/Embedding 配置（Judge 使用 DashScope `qwen-turbo`；生成模型按 `.env` 使用 `oc/deepseek-v4-flash@9router`）

- [x] Task 14: 跑 DeepEval
  - [x] SubTask 14.1: 运行评估，整体耗时 1862s（约 31min），在 30min 整体超时附近完成；26/26 样本成功，无失败
  - [x] SubTask 14.2: 收集 per-question 和 overall 指标（5 项 DeepEval 指标）
  - [x] SubTask 14.3: 保存原始结果到 `data/benchmark/chunk-baseline-eval-v1.json`

- [x] Task 15: 生成评估报告
  - [x] SubTask 15.1: 汇总 DeepEval 分数、per-PDF 分数、最低分题目及分析
  - [x] SubTask 15.2: 写入 `data/benchmark/chunk-baseline-eval-v1.md`
  - [x] SubTask 15.3: 报告末尾包含 overall / per-PDF / 最低分等关键结论

## Phase 6: 文档与基线封存

- [ ] Task 16: 更新项目文档
  - [ ] SubTask 16.1: 在 `docs/completed.md` 追加 chunk 基准建立记录
  - [ ] SubTask 16.2: 在 `docs/pitfalls.md` 记录本次遇到的任何新坑
  - [ ] SubTask 16.3: 更新 `docs/architecture-map.md`（若触发维护条件）和桌面副本

- [ ] Task 17: 基线封存
  - [ ] SubTask 17.1: 确认 `data/benchmark/` 下 golden + eval 文件存在且不被 .gitignore 忽略
  - [ ] SubTask 17.2: 将本次运行的关键脚本/命令记录到 `data/benchmark/README.md`

## Phase 7: 全量入库准备（待用户确认）

- [ ] Task 18: 全量入库方案预备
  - [ ] SubTask 18.1: 列出 `data/pdfs/` 下所有待入库 PDF 清单
  - [ ] SubTask 18.2: 按文档类型分类，为每类选择 chunker 策略
  - [ ] SubTask 18.3: 等待用户确认质量后执行全量入库

## Task Dependencies

- Task 2 / 3 / 4 可并行执行（3 个 PDF 独立入库）
- Task 5 / 6 / 7 依赖对应的 Task 2 / 3 / 4 完成
- Task 8 依赖 Task 5 / 6 / 7 全部完成
- Task 9 / 10 / 11 依赖 Task 8 完成
- Task 12 依赖 Task 9 / 10 / 11
- Task 14 依赖 Task 12
- Task 15 依赖 Task 8 / 14
- Task 16 / 17 依赖 Task 15
- Task 18 依赖用户确认
