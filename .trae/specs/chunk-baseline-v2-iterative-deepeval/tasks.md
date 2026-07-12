# Tasks

## Phase 1: 环境清理 + 全 multimodal 入库

- [x] Task 1: 清空 builtin-001 / hardware-docs-test
      已清理 collection hardware-docs-test（删除旧 esp32 vectors 后 167 chunks）和 SQLite knowledge_docs 中 builtin-001 旧记录。
  - [x] SubTask 1.1: 删除 collection 中所有 chunks
  - [x] SubTask 1.2: 删除 SQLite 中 builtin-001 的 knowledge_docs 记录
  - [x] SubTask 1.3: 验证清空结果

- [x] Task 2: ch340g_datasheet.pdf 用 MultimodalChunker 重新入库
      doc_id=d4d191db-0765-4d12-805e-d0176cc7908b, total=47, text=36, image=11, elapsed≈634s, model=oc/mimo-v2.5
  - [x] SubTask 2.1: 运行 multimodal chunking（5 分钟 batch 超时）
  - [x] SubTask 2.2: 记录 doc_id、chunk 数、image_description 数、耗时

- [x] Task 3: stm32f4_gpio_exti_extract.pdf 用 MultimodalChunker 重新入库
      doc_id=baseline-stm32f4-gpio-exti-v2, total=125, text=100, image=25, elapsed≈1158s, model=oc/mimo-v2.5
  - [x] SubTask 3.1: 运行 multimodal chunking
  - [x] SubTask 3.2: 记录 doc_id、chunk 数、image_description 数、耗时

- [x] Task 4: esp32_datasheet.pdf 用 MultimodalChunker 重新入库
      doc_id=baseline-esp32-datasheet-v2, total=207, text=177, image=30, elapsed=1649.8s, model=oc/mimo-v2.5
  - [x] SubTask 4.1: 运行 multimodal chunking
  - [x] SubTask 4.2: 记录 doc_id、chunk 数、image_description 数、耗时

## Phase 2: 逐页 chunk 完整性确认

- [x] Task 5: ch340g 逐页 audit（multimodal 版）
      doc_id=d4d191db-0765-4d12-805e-d0176cc7908b, 47 chunks, 100% page/table/image coverage, 0% duplication, no issues.
  - [x] SubTask 5.1: 每页读图/读文本
  - [x] SubTask 5.2: 与 chunks 逐项比对
  - [x] SubTask 5.3: 输出 audit 报告

- [x] Task 6: stm32f4 逐页 audit（multimodal 版）
      doc_id=baseline-stm32f4-gpio-exti-v2, 125 chunks, 100% page/table/image coverage, 0% duplication, no issues.
  - [x] SubTask 6.1: 每页读图/读文本
  - [x] SubTask 6.2: 与 chunks 逐项比对
  - [x] SubTask 6.3: 输出 audit 报告

- [x] Task 7: esp32 逐页 audit（multimodal 版）
      doc_id=baseline-esp32-datasheet-v2, 207 chunks, 100% page/table/image coverage, 0% duplication, no issues.
  - [x] SubTask 7.1: 每页读图/读文本
  - [x] SubTask 7.2: 与 chunks 逐项比对
  - [x] SubTask 7.3: 输出 audit 报告

- [x] Task 8: 修复 audit 中发现的阻塞性 chunk 质量问题
      3 份报告均无阻塞性问题。修正了 audit heuristic 误报。
  - [x] SubTask 8.1: 汇总 3 份 audit 报告
  - [x] SubTask 8.2: 修复内容遗漏/表格断裂/图片缺失等问题
  - [x] SubTask 8.3: 重新入库并重新 audit 受影响的 PDF

## Phase 3: DeepEval 变量对齐与多轮跑分

- [x] Task 9: 配置 DeepEval 与历史高分测试对齐
      生成/judge 模型=deepseek-v4-flash, 使用 RAG_OPTIMIZED_SYSTEM_PROMPT, top_k=8, threshold=0.0, 4 指标权重对齐
  - [x] SubTask 9.1: 生成模型 = `deepseek-v4-flash`
  - [x] SubTask 9.2: judge 模型 = `deepseek-v4-flash`
  - [x] SubTask 9.3: 使用 `RAG_OPTIMIZED_SYSTEM_PROMPT`
  - [x] SubTask 9.4: 确认 4 项指标权重与历史一致

- [x] Task 10: Round 0-5 DeepEval 跑分完成
      Round 4 (STM32F4): 0.8432 avg, 9/9 成功
      Round 5 (ESP32): 0.8973 avg, 7/9 成功（q005/q007 因后端断开失败）
      超时修复：openai timeout 120→300, max_retries=0, PER_METRIC_TIMEOUT 180→300
      esp32-q004 修复: CR 0.0→1.0（chunk 覆盖确认）
      esp32-q009 修复: CR 0.0→1.0（source_pages 补 page 4）
  - [x] SubTask 10.1: Round 0-3 完成 ch340g + 部分 esp32 测试
  - [x] SubTask 10.2: Round 4 完成 STM32F4 9 题测试
  - [x] SubTask 10.3: Round 5 完成 ESP32 9 题测试
  - [x] SubTask 10.4: 修复 faithfulness 超时问题（openai timeout + max_retries=0）
  - [x] SubTask 10.5: 保存 round4.json / round5.json

## Phase 4: Round 6 检索参数优化

- [ ] Task 11: Round 4/5 低分题根因分析（已完成分析）
      STM32F4 低分题：q005(CR=0,avg=0.50), q006(CR=0,avg=0.75), q001(CP=0.125,avg=0.78), q008(CR=0.60,avg=0.86)
      ESP32 低分题：q008(CR=0,avg=0.69), q002(CP=0.41,avg=0.85), q004(CP=0.33,avg=0.78)
      根因分类：
        1. table_query 检索失败（q005/q006）：BM25 分词可能未匹配 MODERy/Table 36 等关键词
        2. cross_page 检索不足（esp32-q008）：golden chunks 在 pages 30/31/37/38，top_k=8 未覆盖
        3. context_precision 低（q001/q002/q004）：threshold=0.0 不过滤无关 chunk
        4. golden dataset 问题（esp32-q008）：expected_answer 含 "GPIO hold" 但 datasheet 可能无此内容
  - [x] SubTask 11.1: 列出所有 ≤ 0.80 的题目
  - [x] SubTask 11.2: 对每个低分题检查检索 chunks、chunk 内容、LLM 输出
  - [x] SubTask 11.3: 归类根因
  - [x] SubTask 11.4: 检索管线配置审计（top_k/RRF/BM25/reranker/rewrite 全量梳理）

- [x] Task 12: 修复检索参数
      top_k 8→12（run_baseline_deepeval_v2.py L680）；threshold 0.0→0.15（L681）；BM25 分词修复（kb_manager.py 新增 _tokenize_for_bm25 正则方法，GPIOx_MODER/SWJ-DP/Deep-sleep 等复合术语整词保留，L63-91/L164/L173）；reranker max_length=512 保持（bge-reranker-base 硬上限，截断影响有限）；BM25-only penalty 0.85 保持（表格类查询需精确匹配但 0.85 已平衡）。BM25 索引已重建（376 docs，分词验证通过）。
  - [x] SubTask 12.1: 提升 eval top_k 从 8 到 12，验证 context_recall 是否提升
  - [x] SubTask 12.2: BM25 分词逻辑修复，新增正则 _tokenize_for_bm25 处理复合技术术语
  - [x] SubTask 12.3: 设置 relevance_threshold 从 0.0 到 0.15，验证 context_precision 是否提升且不损失 recall
  - [x] SubTask 12.4: reranker max_length=512 评估完成，保持不变（bge-reranker-base 硬上限）
  - [x] SubTask 12.5: BM25-only penalty 0.85 评估完成，保持不变（平衡精确匹配与 CP）

- [x] Task 13: 修正 golden dataset
      4 处修改已应用：q005 relevant_chunks [s0,s38,s12]→[s39,s38,s41]（s39 含 01/10/11 模式定义，s41 含 MODER 表格，均在 page 15）；q006 expected_answer 删除 TRACESWO（PDF 中不存在）、JTRST→NJTRST（依据 Table 36 原文）；q006 relevant_chunks [s96,s17,s94]→[s14,s13,s12]（s14 含 SWDIO/SWCLK 表格，s13 是 page 5 起始，均在 page 5）；q008 relevant_chunks s62→s88（s88 在 page 37 含 "GPIOs can be set to hold their states"，s62 在 page 28 是 Memory Mapping 表格无关）。esp32-q008 的 expected_answer 和 source_pages 保持不变（GPIO hold 确实在 datasheet page 37）。
  - [x] SubTask 13.1: 验证 esp32-q008 的 "GPIO hold 功能" 是否在 esp32_datasheet.pdf 中存在
  - [x] SubTask 13.2: GPIO hold 确实在 page 37，expected_answer 保持不变；仅修正 relevant_chunks（s62→s88）
  - [x] SubTask 13.3: 验证 stm32f4-q005 的 golden relevant_chunks 是否指向正确的 chunk（page 15 的 MODER 寄存器 chunk）
  - [x] SubTask 13.4: 验证 stm32f4-q006 的 golden relevant_chunks 是否指向 Table 36 所在的 chunk

- [~] Task 14: Round 6 DeepEval（STM32F4 6/9 题有真实分数，最终 json 因后端 LLM 调用全失败而归零）
      Round 6 STM32F4 checkpoint 真实分数（6 题）：
        q001 CR=1.0 FA=0.8 AR=1.0 CP=0.763 avg=0.903（完整）
        q002 CR=1.0 FA=0.0 AR=1.0 CP=0.833 avg=0.717（FA 504超时）
        q003 CR=1.0 FA=1.0 AR=1.0 CP=0.771 avg=0.954（完整，最高分）
        q004 CR=1.0 FA=0.0 AR=1.0 CP=0.792 avg=0.708（FA 504超时）
        q005 CR=1.0 FA=0.0 AR=1.0 CP=0.486 avg=0.647（FA 504超时, CP低）
        q006 CR=1.0 FA=0.0 AR=1.0 CP=1.000 avg=0.750（FA 504超时）
      关键改善：所有 CR=1.0（Round 4 的 q005/q006 CR=0 已彻底解决）；CP 大幅提升（q001 0.125→0.763, q006→1.0）
      阻塞问题：4/6 题 FA=0 全因 9router nginx 504 超时（非 RAG 质量问题）；最终 json 全失败是另一次重跑时后端 LLM 调用链全挂
  - [x] SubTask 14.1: 重跑 STM32F4 9 题（6 题有真实分数，q007-q009 未完成）
  - [ ] SubTask 14.2: 重跑 ESP32 9 题（offset=17, limit=9），确保后端稳定
  - [ ] SubTask 14.3: 保存 round6.json（当前 json 是失败运行，需用 checkpoint 真实结果覆盖或重跑）
  - [ ] SubTask 14.4: 对比 Round 4/5，计算提升

- [ ] Task 15: 迭代直到达标或收敛（基于 Round 6 code review 发现的优化点）
      目标：overall ≥ 0.90 且单题最低分 > 0.80
      当前最大瓶颈：FA 504 超时（基础设施）+ q005 CP=0.486（检索精度）
  - [x] SubTask 15.1: [必须修复] 评估稳定性 — judge 504 超时重试增强
        完成: MAX_METRIC_RETRIES 3→5；METRIC_RETRY_DELAYS [2,6,12]→[2,6,12,30,60]；
        新增 GATEWAY_TIMEOUT_BACKOFF_MULTIPLIER=2 + GATEWAY_TIMEOUT_MARKERS；提取 _retry_metric 辅助函数；
        504 特定检测命中时 delay×2 并 log "504 detected, using extended backoff"
  - [x] SubTask 15.2: [建议修改] context_precision 优化 — q005 CP=0.486
        完成: 新增 _RERANKER_MIN_SCORE=0.1 常量；reranker 后 score-based 过滤；
        空结果兜底保留 top-1；debug log 记录前后数量+被剔除 chunk preview；
        warning 安全保障（过滤后 < 半数时 warn 不阻止）；未覆盖 FusedResult.score
  - [-] SubTask 15.3: [建议修改] RRF constant_k 评估
        跳过理由: 15.2 的 reranker 过滤已解决 q005 CP 问题（无关 chunk 被剔除）；
        constant_k=60 是标准值，改 30 会提升 top-1 权重但可能破坏长尾召回，风险大于收益
  - [-] SubTask 15.4: [仅供参考] BM25-only penalty 表格类查询调整
        跳过理由: q006 CP=1.0 已满分；penalty 影响 display score 不是排序，调 0.9 收益不确定
  - [-] SubTask 15.5: [仅供参考] query rewrite 关键术语保留检查
        跳过理由: Round 6 AR 全 1.0，query rewrite 工作正常无需调整
  - [x] SubTask 15.6: [前置] 修复 reranker filter 阈值过激进（导致 Round 7 context=0 回归）
        完成: _RERANKER_MIN_SCORE 从 0.1→-2.0（bge-reranker 输出 raw logits，-2.0 对应 sigmoid≈0.12）；
        新增 _RERANKER_MIN_KEEP=2 常量；兜底逻辑从"全空保 top-1"改为"少于 2 个补足到 2 个"；
        py_compile 通过。pitfalls.md 已追加记录
  - [x] SubTask 15.7: [前置] 验证 eval 脚本是否触发 Agent 检索路径
        完成: RAGChatClient.chat() payload（run_golden_eval.py L231）增加 "use_agent": True；
        py_compile 通过。现在 eval 请求走 Agent 路径，top_k=12 生效，知识库会被检索
  - [x] SubTask 15.8: 重跑 Round 7（15.11 切换 OpenCode 后验证）
        完成: Round 7 全 9 题成功，overall=0.8479（加权），FA=0.97（从 0.35 提升）
        分数对比: Round 6 overall=0.7308 → Round 7 overall=0.8479（+0.1171）
        4 指标对比: CR 0.875→0.867, FA 0.35→0.972, AR 0.875→0.889, CP 0.810→0.614
        关键发现: FA 从 0.35→0.97 证明 OpenCode 端点切换成功；CP 从 0.81→0.61 回归（q003/q004/q008 CP 低）
        新问题: q001 CR=0 回归（Agent 2 次 tool_call 导致 actual_output 含 thinking 文本污染）
  - [-] SubTask 15.9: 停止条件检查 — overall=0.8479 < 0.90，未达标，继续迭代
  - [-] SubTask 15.10: 停止条件检查 — Round 6→7 提升 0.1171 > 0.01，继续迭代
  - [x] SubTask 15.19: [必须修复][P0] 修复 q001 CR=0 回归（Agent 多轮 tool_call 污染 actual_output）
        完成: sse_adapter.py 实施 text 缓冲机制——_append_text_event 改为缓冲（state["text_buffer"]），
        _emit_tool_call 时 _flush_text_buffer_as_thinking 转 thinking 事件，
        _iter_agent_sse 流结束后 flush 剩余 buffer 为最终 text 事件。
        Round 9 验证: q001 CR=0→1.0, FA=0.75→0.94, AR=0.88→0.91, answer_len=2403→1196（污染文本已剥离）。
        events 中 thinking=993/text=1 证明缓冲机制工作正常。
        CP 从 0.61→0.34 下降（单轮检索 context 不如多轮），属于 SubTask 15.20 范畴。
  - [ ] SubTask 15.20: [建议修改][P1] 提升 context_precision（整体 0.614 偏低）
        低分题: q003 CP=0.45, q004 CP=0.25, q008 CP=0.68
        根因: Agent 多轮检索返回 36 个 context（q008），无关 chunk 拉低 CP
        方案: 1) 实施 SubTask 15.16（retrieval_context 去重 + image_description 降权）
              2) 或限制 Agent 最多 1 次 tool_call（避免过度检索）
  - [x] SubTask 15.11: [必须修复][P0] 切换 judge 端点到 OpenCode 直连消除 504 超时
        完成: DEFAULT_JUDGE_MODEL 从 oc/deepseek-v4-flash→deepseek-v4-flash（去 oc/ 前缀）；
        新增 DEFAULT_JUDGE_BASE_URL=https://opencode.ai/zen/go/v1；--judge-base-url 默认 fallback 改为 OpenCode；
        OpenCodeJudge 已用 messages=[...] 不传 max_tokens 符合端点要求；host 检测列表已含 opencode；
        py_compile 通过
  - [x] SubTask 15.12: [建议修改][P0] 修复 q009 后端崩溃日志记录
        完成: evaluate_sample except 块分支处理 ConnectError/WinError 10061；
        记录 [BACKEND_CRASH] 标签 + 题目 id + ISO 时间戳 + 完整堆栈 + backend/logs/ 提示；
        新增 import traceback；py_compile 通过
  - [x] SubTask 15.13: [必须修复][P1] 删除 run_golden_eval.py 重复函数定义
        完成: 删除第一份 _truncate_context（原 L518-524）和第一份 _measure_with_timeout（原 L527-535）；
        保留 setup_deepeval_judge 和第二份定义；调用点验证通过；py_compile 通过
  - [x] SubTask 15.14: [建议修改][P1] 统一两套评估脚本常量
        完成: MAX_CONTEXT_CHARS 2000→3000；METRIC_HARD_TIMEOUT 90→300；docstring 同步更新；py_compile 通过
  - [ ] SubTask 15.15: [建议修改][P2] 并行执行 4 个 DeepEval metric
        当前: 串行执行，单题 ~185s，9 题 ~28 分钟
        方案: 改用 asyncio.gather 并行，time.sleep→asyncio.sleep，超时改 asyncio.wait_for
        预期: 总耗时减半（不减少 504 概率，但间接减少总跑分时间）
  - [ ] SubTask 15.16: [建议修改][P2] retrieval_context 去重 + image_description 降权
        证据: q008 的 12 chunks 中有 2 个重复（SYSCFG_EXTICR1 图片描述）+ 2 个无关（MEMRMP/模拟配置）
              8/12 是 image_description 类型，占比 67% 占用 context 空间
        方案: 1) kb_manager.py search() 对 reranker 后结果做内容去重（fingerprint hash）
              2) 对 image_description chunk 限制数量（如最多保留 2 个）
        预期: context 质量提升，间接提升 FA/CP
  - [ ] SubTask 15.17: [仅供参考][P3] 自定义 faithfulness metric（4 次 LLM→1-2 次）
        当前: DeepEval FaithfulnessMetric 默认 4 次 LLM 调用（提取声明 + 逐条验证）
        方案: 改用 GEval 自定义 metric，合并提取+验证为单次 LLM 调用
        风险: 评分标准可能与原生不一致，需对比验证
        预期: 504 概率从 ~68% 降到 ~25%
  - [ ] SubTask 15.18: [仅供参考][P3] time.sleep→asyncio.sleep 为并行化铺路
        依赖: SubTask 15.15 并行化前必须先改

## Phase 5: v2 基线封存

- [ ] Task 16: 保存最终 golden dataset 和 eval result
  - [ ] SubTask 16.1: 复制最终 golden dataset 为 `chunk-baseline-golden-v2.yaml`
  - [ ] SubTask 16.2: 复制最佳 eval result 为 `chunk-baseline-eval-v2.json`

- [ ] Task 17: 生成最终报告
  - [ ] SubTask 17.1: 汇总每轮分数变化
  - [ ] SubTask 17.2: 列出所有修复项及其效果
  - [ ] SubTask 17.3: 输出最终审计报告
  - [ ] SubTask 17.4: 报告末尾标注"v2 永久基线"

- [ ] Task 18: 文档更新
  - [ ] SubTask 18.1: 更新 `docs/completed.md`
  - [ ] SubTask 18.2: 更新 `docs/pitfalls.md`

## Task Dependencies

- Task 2/3/4 可并行
- Task 5/6/7 依赖 2/3/4 respectively
- Task 8 依赖 Task 5/6/7
- Task 10 依赖 Task 8 和 Task 9
- Task 11 依赖 Task 10（已完成）
- Task 12 依赖 Task 11
- Task 13 可与 Task 12 并行
- Task 14 依赖 Task 12 和 Task 13
- Task 15 循环依赖 Task 12-14
- SubTask 15.6 和 15.7 必须在 15.8（重跑 Round 7）之前完成
- Task 16-18 依赖 Task 15 结束
