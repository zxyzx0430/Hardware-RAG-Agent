# Checklist

## Phase 1: 环境清理 + 全 multimodal 入库

- [x] `hardware-docs-test` collection 已清空
- [x] SQLite `knowledge_docs` 中 builtin-001 记录已清空
- [x] ch340g_datasheet.pdf 使用 MultimodalChunker 成功入库
- [x] stm32f4_gpio_exti_extract.pdf 使用 MultimodalChunker 成功入库
- [x] esp32_datasheet.pdf 使用 MultimodalChunker 成功入库
- [x] 每个 PDF 的 doc_id、chunk 数、image_description 数、耗时已记录

## Phase 2: 逐页 chunk 完整性确认

- [x] ch340g 每页均有 chunk 覆盖，关键表格/图片无遗漏
- [x] stm32f4 每页均有 chunk 覆盖，寄存器表/代码块/框图无遗漏
- [x] esp32 每页均有 chunk 覆盖，引脚图/电气特性表/功能框图无遗漏
- [x] 3 个 PDF 重复率均 < 5%
- [x] audit 中发现的阻塞性问题已修复并重新验证

## Phase 3: DeepEval 变量对齐与多轮跑分

- [x] 生成模型配置为 `deepseek-v4-flash`
- [x] judge 模型配置为 `deepseek-v4-flash`
- [x] 使用 `RAG_OPTIMIZED_SYSTEM_PROMPT`
- [x] 4 项指标权重与历史一致
- [x] Round 4 (STM32F4) 跑分完成：0.8432 avg
- [x] Round 5 (ESP32) 跑分完成：0.8973 avg
- [x] faithfulness 超时问题已修复（openai timeout 300s + max_retries=0）

## Phase 4: Round 6 检索参数优化

- [x] Round 4/5 所有 ≤ 0.80 的题目已完成根因分析
- [x] 根因已归类：检索失败 / chunk 缺失 / LLM 编造 / judge 误判 / query 歧义
- [x] 检索管线配置已全量审计（top_k/RRF/BM25/reranker/rewrite）
- [x] eval top_k 从 8 提升到 12，验证 context_recall 提升
- [x] BM25 分词逻辑已修复（_tokenize_for_bm25 正则方法），复合技术术语整词保留
- [x] relevance_threshold 从 0.0 调整到 0.15，验证 context_precision 提升
- [x] reranker max_length=512 截断问题已评估（bge-reranker-base 硬上限，保持不变）
- [x] BM25-only penalty 已评估（0.85 保持，平衡精确匹配与 CP）
- [x] esp32-q008 golden dataset 已修正（GPIO hold 在 page 37 确认，relevant_chunks s62→s88）
- [x] stm32f4-q005/q006 golden relevant_chunks 已修正（q005 指向 page 15 MODER，q006 指向 page 5 Table 36）
- [~] Round 6 STM32F4 checkpoint 有 6 题真实分数（CR 全 1.0，BM25 修复有效），但最终 json 因 504 超时 + 后端 LLM 失败归零
- [ ] Round 6 跑分完成，结果保存到 `chunk-baseline-eval-v2-round6.json`（需用 checkpoint 真实结果覆盖或重跑）

## Phase 4.5: Round 7 优化点验证（chinese-code-review 输出）

- [x] [必须修复] judge 504 超时重试增强：MAX_METRIC_RETRIES 3→5，METRIC_RETRY_DELAYS 增加指数退避，504 特定更长退避
- [x] [必须修复] reranker filter 阈值修复：_RERANKER_MIN_SCORE 0.1→-2.0（logit 量纲），新增 _RERANKER_MIN_KEEP=2 兜底
- [x] [必须修复] eval 脚本 use_agent=True：RAGChatClient payload 增加 "use_agent": True
- [x] [必须修复] Round 7 重跑后 FA=0 题目数量减少（目标：504 超时导致的 FA=0 为 0）
      完成: Round 7 FA=0.9722（9 题中 8 题 FA=1.0，仅 q001 FA=0.75），OpenCode 端点彻底消除 504
- [x] [建议修改] reranker score-based 过滤：reranker raw score < 0.1 的 chunk 剔除，验证 q005 CP 提升（已实现但阈值需调）
- [-] [建议修改] RRF constant_k=30 A/B 测试：跳过（15.2 已解决 CP 问题，改 constant_k 风险大于收益）
- [-] [仅供参考] BM25-only penalty 表格查询调整评估：跳过（q006 CP=1.0 已满分，penalty 影响 display score 非排序）
- [-] [仅供参考] query rewrite 关键术语保留检查评估：跳过（Round 6 AR 全 1.0，rewrite 正常）
- [ ] 满足以下任一条件：
  - [ ] overall ≥ 0.90 且单题最低分 > 0.80
  - [ ] 连续两轮提升 < 0.01，已记录剩余 gaps

## Phase 4.6: Round 7+ 优化点验证（chinese-code-review 第二轮深度审查输出）

- [x] [必须修复][P0] judge 端点已切换到 OpenCode 直连（https://opencode.ai/zen/go/v1），DEFAULT_JUDGE_BASE_URL 已改
- [x] [建议修改][P0] q009 后端崩溃已记录清晰日志（[BACKEND_CRASH] 标签 + 时间/题目id/错误堆栈）
- [x] [必须修复][P1] run_golden_eval.py 重复函数定义已删除（_truncate_context/_measure_with_timeout 各保留一份）
- [x] [建议修改][P1] 两套评估脚本常量已统一（MAX_CONTEXT_CHARS=3000, METRIC_TIMEOUT=300s）
- [ ] [建议修改][P2] 4 个 DeepEval metric 改为并行执行（asyncio.gather）
- [ ] [建议修改][P2] retrieval_context 去重 + image_description 降权已实现
- [ ] [仅供参考][P3] 自定义 faithfulness metric（GEval）已评估或实现
- [ ] [仅供参考][P3] time.sleep→asyncio.sleep 已改为异步

## Phase 5: v2 基线封存

- [ ] `data/benchmark/chunk-baseline-golden-v2.yaml` 已保存
- [ ] `data/benchmark/chunk-baseline-eval-v2.json` 已保存（最佳一轮）
- [ ] 最终审计报告已生成
- [ ] 报告末尾标注"v2 永久基线"
- [ ] `docs/completed.md` 已更新
- [ ] `docs/pitfalls.md` 已更新
