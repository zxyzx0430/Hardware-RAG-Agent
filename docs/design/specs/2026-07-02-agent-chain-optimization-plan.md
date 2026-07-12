# Agent 链条优化计划

> 计划日期：2026-07-02
> 配套报告：`2026-07-02-agent-chain-optimization-research.md`
> 状态：**待用户审批** — 用户通过后按本计划执行
> 执行线程：Trae T2（Agent 全链路）
> 文件边界：`backend/src/agent/*` + `backend/src/agent/core/toolkit/*`

---

## 执行原则

1. **每个优化项独立可交付**：一项做完即可验证，不依赖后续项
2. **向后兼容**：所有优化加配置开关，默认关闭，验证后开启
3. **不破坏现有 SSE schema**：T5/T6 消费的事件格式不变
4. **复用优先**：每个优化项实现前先去 GitHub 搜 Python + LangGraph 的成熟实现
5. **每项完成后**：重启后端 + agent-browser 测试 + 更新 pitfalls.md / completed.md

---

## P0 — 立即做（预计 4-5 天）

### 任务 1：循环检测 + Prompt 注入自愈

**目标**：LoopGuard 检测到死循环后，不只终止，而是注入反思提示让 LLM 换方法。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/core/toolkit/loop_guard.py` | 新增 `build_self_heal_prompt()` 方法，返回反思提示 |
| `backend/src/agent/sse_helpers.py` | `build_loop_detected_sse` 增加 `self_heal_prompt` 字段 |
| `backend/src/agent/hitl_handler.py` | resume 时，若用户选"换方法"，注入 `self_heal_prompt` 到消息历史 |
| `backend/src/agent/sse_adapter.py` | loop_triggered 时 yield loop_detected 后不立即 break，等用户选择 |

**实现步骤**：
1. 在 `LoopGuard` 新增 `build_self_heal_prompt(signal, call_history)` → 返回中文反思提示
2. `build_loop_detected_sse` payload 增加 `self_heal_prompt` 字段
3. 前端 `LoopDetectedDialog` 的"换方法"按钮点击时，把 `self_heal_prompt` 作为用户消息发回
4. 后端 resume 路径收到 `self_heal_prompt` 后，注入为 HumanMessage，让 Agent 换方法

**验证**：
- 触发死循环（连续问同一问题 3 次）→ 点击"换方法" → Agent 应换一种工具调用
- 不点击"换方法"而点"停止" → Agent 正常终止

**风险**：低。纯逻辑增强，不涉及接口变更。

---

### 任务 2：StuckDetector 五模式增强

**目标**：LoopGuard 增加 action-observation 配对哈希 + 滑动窗口检测。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/core/toolkit/loop_guard.py` | 新增 `_detect_action_error_loop()` 和 `_detect_repeated_observation()` 方法 |

**实现步骤**：
1. 新增 `action_error_history: Deque[tuple[str, str]]`（最近 4 轮 action+observation 对）
2. `_detect_action_error_loop()`：检查最近 4 组 action-observation 对是否完全一致
3. `_detect_repeated_observation()`：检查最近 3 次 tool_result 是否相同（即使 action 不同）
4. `check()` 方法调用顺序：cooldown → repeat → no_progress → action_error_loop → repeated_observation → max_calls

**验证**：
- 构造测试：连续 4 次相同 search_docs 调用 → 应触发 action_error_loop
- 构造测试：3 次不同工具但相同输出 → 应触发 repeated_observation

**风险**：低。纯逻辑增强。

---

### 任务 3：Prompt 分段 token 预算

**目标**：按段（system/tools/memory/history）分配 token 上限，超限段单独截断。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/context_guard.py` | 新增 `TokenBudget` dataclass + `enforce_budget()` 方法 |
| `backend/src/agent/prompts.py` | 新增分段预算配置常量 |
| `backend/src/agent/agent_factory.py` | 组装 prompt 时调用 `enforce_budget()` |

**实现步骤**：
1. 定义 `TokenBudget`：system=2000 / tools=3000 / history=8000 / rag_results=4000（可配置）
2. `enforce_budget(segment, budget)` → 超限时截断最旧内容 + 记录 warning
3. agent_factory 组装消息时，按段计算 token，超限段截断
4. RAG 检索结果（search_docs 返回）单独预算，防止吃满

**验证**：
- 构造长对话（50+ 轮）→ history 段超限时应截断最旧消息，不报错
- search_docs 返回大量结果 → rag_results 段超限时应截断低分结果

**风险**：中。截断可能丢失关键信息，需配置合理的预算比例。

---

### 任务 4：FTS5 会话搜索

**目标**：SQLite + FTS5 全文本搜索，用户可问"上次你帮我生成的代码呢"。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/session_search.py`（新增） | SQLite + FTS5 表定义 + 索引 + 搜索 |
| `backend/app/api/chat_routes.py` | 会话结束 hook：存消息到 FTS5 表 |
| `backend/src/agent/tools/groups/retrieval/search_history.py`（新增） | `search_history` 工具 |
| `backend/src/agent/agent_factory.py` | 注入 `search_history` 工具（第 13 个工具） |

**实现步骤**：
1. 新建 `data/agent_sessions.db`，表 `sessions_fts`（FTS5：session_id/user_msg/assistant_msg/timestamp）
2. 会话每轮结束，存 user_msg + assistant_msg 到 FTS5 表
3. `search_history` 工具：输入 query → FTS5 MATCH 查询 → 返回匹配的历史消息
4. Agent system prompt 增加：用户问"上次"相关问题时，先调 `search_history`

**验证**：
- 会话 A 问"STM32 LED 接线" → 会话 B 问"上次你帮我生成的 STM32 代码呢" → Agent 应调 search_history 找到

**风险**：中低。SQLite + FTS5 是 Python 标准库支持，无外部依赖。注意 db 文件路径保护。

---

## P1 — 短期做（预计 7-9 天）

### 任务 5：分层 Compact 机制

**目标**：先做 microcompact（每轮删旧 tool_result）+ autocompact（token 超阈值调 LLM 压缩）。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/compact/__init__.py`（新增） | 模块入口 |
| `backend/src/agent/compact/microcompact.py`（新增） | 每轮检查，删旧 tool_result |
| `backend/src/agent/compact/autocompact.py`（新增） | token 超阈值调 LLM 压缩历史 |
| `backend/src/agent/sse_adapter.py` | `_iter_agent_sse` 每轮后调用 microcompact |
| `backend/src/agent/context_guard.py` | 超阈值时触发 autocompact 而非直接抛异常 |

**实现步骤**：
1. **microcompact**：每轮工具调用后，检查历史 tool_result，超过 5 轮的旧 tool_result 替换为"[已压缩：tool_name, 摘要]"
2. **autocompact**：token > `AUTOCOMPACT_BUFFER_TOKENS`（13000）时，调 LLM 对早期消息做摘要，替换为 SystemMessage("[历史摘要]: ...")
3. **熔断**：连续 3 次 autocompact 失败 → 放弃压缩，直接截断
4. 复用 LangGraph 的 `SummarizeMessages` 或自写 summarizer 节点（先去 GitHub 搜成熟实现）

**验证**：
- 50+ 轮对话 → token 接近上限时自动压缩，对话继续不报错
- 检查压缩后 Agent 仍记得早期关键信息（如芯片型号）

**风险**：中。压缩质量依赖 LLM，需设计好的 summarizer prompt。额外 LLM 调用有成本。

---

### 任务 6：错误扣留 + 恢复 + 模型 fallback

**目标**：API 错误（token 超限）不直接断流，先尝试压缩恢复。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/sse_adapter.py` | `stream_agent_to_sse` 外包 try/except 捕获 ContextLimitError |
| `backend/src/agent/context_guard.py` | `ContextLimitError` 触发时先尝试 autocompact |
| `backend/src/agent/agent_factory.py` | 支持配置备用模型 |
| `backend/app/api/chat_routes.py` | 读取 `FALLBACK_MODEL` 环境变量 |

**实现步骤**：
1. `stream_agent_to_sse` 捕获 `ContextLimitError` → 触发 autocompact → 重试一次
2. 重试仍失败 → 检查是否配了 `FALLBACK_MODEL` → 切换模型重试
3. 都失败 → 才 yield error SSE
4. 前端显示"上下文超长，已自动压缩并重试"提示

**验证**：
- 构造超长对话触发 ContextLimitError → 应自动压缩重试，不报错
- 配置 FALLBACK_MODEL → 主模型失败时切换备用模型

**风险**：中。重试逻辑要防止无限循环，最多重试 1 次。

---

### 任务 7：细粒度权限模式匹配

**目标**：`run_command` 按命令内容模式匹配审批，`ls`/`pwd` 自动放行，`rm -rf` 拦截。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/permission_matcher.py`（新增） | `fnmatch` 模式匹配引擎 |
| `backend/src/agent/hitl_handler.py` | 审批前调用 `permission_matcher.check()` |
| `backend/src/agent/risk_classifier.py` | 整合模式匹配规则 |
| `frontend/src/components/chat/PermissionDialog.tsx` | 增加"always 记住"选项 |

**实现步骤**：
1. 定义权限规则格式：`{"run_command": {"ls *": "allow", "cat *": "allow", "rm *": "ask", "format *": "deny"}}`
2. `permission_matcher.check(tool, input)` → 返回 allow/ask/deny，last match wins
3. HITL 审批前先查 matcher，allow 直接放行，deny 直接拒绝，ask 才弹审批
4. 审批 UI 增加"always 记住"按钮，把当前 pattern 存入 session 配置
5. 默认规则：`ls/cat/pwd/git status/grep` 自动放行，`rm/format/del/sudo` 必审

**验证**：
- Agent 调 `run_command ls` → 自动放行不弹窗
- Agent 调 `run_command rm -rf /tmp/test` → 弹审批
- 点"always 记住" → 后续 `rm -rf /tmp/*` 不再弹窗

**风险**：中。模式匹配要防止绕过（如 `ls; rm -rf /`），需先做命令拆分再匹配。

---

### 任务 8：插件钩子系统

**目标**：工具执行器外包一层 dispatcher，支持 `pre_tool_call` / `post_tool_result` 钩子。先做 `transform_tool_result` 脱敏。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/plugins/__init__.py`（新增） | 模块入口 |
| `backend/src/agent/plugins/base.py`（新增） | `Plugin` 抽象类 + 钩子接口 |
| `backend/src/agent/plugins/manager.py`（新增） | 插件注册 + 调度 |
| `backend/src/agent/plugins/redact_plugin.py`（新增） | API Key 脱敏插件 |
| `backend/src/agent/tools/router.py` | 工具执行前后调用钩子 |

**实现步骤**：
1. 定义 `Plugin` 抽象类：`pre_tool_call(tool, args) -> args | None`（None 表示 veto）、`post_tool_result(tool, result) -> result`
2. `PluginManager`：注册/注销插件，按优先级调度
3. `RedactPlugin`：`post_tool_result` 检查结果中的 API Key 模式（正则），替换为 `[REDACTED]`
4. 工具 router 执行前调 `pre_tool_call`，执行后调 `post_tool_result`
5. 配置文件 `data/agent_plugins.json` 启用/禁用插件

**验证**：
- 工具结果包含 `sk-xxx` → 前端应显示 `[REDACTED]`
- `pre_tool_call` 返回 None → 工具不执行，返回"被插件拒绝"

**风险**：中。钩子执行顺序和错误处理需谨慎，单个插件失败不应影响其他插件。

---

## P2 — 中期做（预计 8-12 天）

### 任务 9：Subagent 隔离重上下文工具

**目标**：用 LangGraph subgraph 定义 `doc_search_subagent`，隔离 PDF 检索中间结果。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/subagents/__init__.py`（新增） | 模块入口 |
| `backend/src/agent/subagents/doc_search.py`（新增） | 文档检索子 Agent |
| `backend/src/agent/agent_factory.py` | 主 graph 加 subagent 路由节点 |

**实现步骤**：
1. 定义 `doc_search_subagent`：独立 state + 独立 context window + 只用 search_docs/list_kb_docs 工具
2. 主 graph 新增 `should_delegate` 路由节点：判断用户消息是否需要文档检索
3. 通过 LangGraph `Send` API 委派子 Agent，子 Agent 完成后只返回结论（不返回中间结果）
4. 子 Agent 用更便宜的模型（可配置）

**验证**：
- 用户问"STM32 GPIO 配置" → 主 Agent 委派 doc_search_subagent → 子 Agent 检索 → 只返回结论
- 检查主会话 history 不包含 search_docs 的中间结果

**风险**：中高。LangGraph subgraph 状态管理复杂，需参考官方示例。

---

### 任务 10：Skills 三层渐进披露

**目标**：工具拆成元数据层 + 详细 schema 层 + 实现层，省 30-50% token。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/tools/registry.py`（新增） | 工具注册中心，支持三层定义 |
| `backend/src/agent/agent_factory.py` | `bind_tools` 改为两阶段绑定 |
| `backend/src/agent/prompts.py` | system prompt 只注入元数据层 |

**实现步骤**：
1. 每个工具定义三层：`metadata`（name + 一句话描述，~20 token）、`schema`（完整 JSON schema，~200 token）、`impl`（实现代码）
2. system prompt 只注入 metadata 层（12 工具 × 20 token = 240 token，vs 现在 12 × 200 = 2400 token）
3. LLM 选定工具后，动态注入该工具的完整 schema
4. LangGraph 的 `bind_tools` 改为动态绑定

**验证**：
- 检查 system prompt token 数：应从 ~2400 降到 ~240
- Agent 仍能正确选择工具（metadata 描述足够清晰）

**风险**：中。LLM 可能因信息不足选错工具，metadata 描述需精心设计。LangGraph 动态 bind_tools 需验证可行性。

---

### 任务 11：跨会话长期记忆

**目标**：会话结束抽取关键事实存入向量库，下次会话开始 hybrid search 注入。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/long_term_memory.py`（新增） | 长期记忆管理 |
| `backend/app/api/chat_routes.py` | 会话开始/结束 hook |
| `backend/src/agent/agent_factory.py` | 注入长期记忆到 system prompt |

**实现步骤**：
1. 会话结束：调 LLM 抽取关键事实（芯片型号、接线方案、用户偏好）→ 存入 ChromaDB 专用 collection
2. 会话开始：hybrid search（关键词 + 向量）检索相关记忆 → 注入 system prompt（上限 2k token）
3. 记忆衰减：30 天未访问的记忆自动降权
4. 用户可查看/删除记忆（`/api/memory/list` 接口）

**验证**：
- 会话 A 讨论 STM32 LED → 会话 B 问 STM32 → 应注入 A 的关键事实
- 检查 system prompt 包含"[历史记忆]: 用户上次问 STM32 LED 接线，方案是 PA5 → LED → GND"

**风险**：中。关键事实抽取质量依赖 LLM，需设计好的抽取 prompt。隐私需考虑（用户可删除）。

---

## P3 — 长期做（预计 10-15 天）

### 任务 12：自学习 Skill 闭环（简化版 FAQ 缓存）

**目标**：高频问答对存 SQLite，下次命中直接返回不走 Agent。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/skills/__init__.py`（新增） | 模块入口 |
| `backend/src/agent/skills/faq_cache.py`（新增） | FAQ 缓存 |
| `backend/app/api/chat_routes.py` | Agent 调用前先查 FAQ 缓存 |

**实现步骤**：
1. Agent 回答后，调 LLM 判断该问答是否值得缓存（通用性问题 + 高质量答案）
2. 值得缓存 → 存入 `data/faq_cache.db`（question 向量化 + answer + hit_count）
3. 下次用户提问 → 先向量检索 FAQ 缓存 → 相似度 > 0.92 → 直接返回缓存答案 + 标注"[缓存]"
4. hit_count > 5 的 FAQ 自动晋升为"热门"

**验证**：
- 问"STM32 LED 接线" → Agent 回答 → 缓存
- 再问"STM32 LED 怎么接" → 命中缓存，秒回

**风险**：高。缓存命中判断要准，错误缓存会误导用户。需提供"刷新"按钮跳过缓存。

---

### 任务 13：apply_patch diff 编辑

**目标**：edit_file 支持 diff 格式，token 省 60%+。

**文件改动**：
| 文件 | 改动 |
|------|------|
| `backend/src/agent/tools/groups/file_ops/edit_file.py` | 增加 diff 解析 |
| `backend/src/agent/prompts.py` | system prompt 教 LLM 用 diff 格式 |
| `frontend/src/components/chat/ToolResult.tsx` | diff 渲染 |

**实现步骤**：
1. edit_file 工具增加 `patch` 参数（diff 格式）
2. 用 Python `difflib` 解析 patch → 应用到原文件
3. system prompt 教 LLM：大文件编辑用 patch 格式，小文件用全量
4. 前端 tool_result 渲染 diff（红绿高亮）

**验证**：
- Agent 编辑大文件 → 应生成 diff 而非全量
- 检查 token 消耗：应降 60%+

**风险**：中。diff 解析有边界情况（空行/特殊字符），需充分测试。

---

## 执行时间线

| 阶段 | 任务 | 预计工时 | 依赖 |
|------|------|---------|------|
| **P0** | 任务 1-4 | 4-5 天 | 无 |
| **P1** | 任务 5-8 | 7-9 天 | P0 完成 |
| **P2** | 任务 9-11 | 8-12 天 | P1 完成 |
| **P3** | 任务 12-13 | 10-15 天 | P2 完成 |

**总计**：29-41 天（可按优先级分批落地，P0+P1 共 11-14 天即可覆盖核心痛点）

---

## 验证与回滚

### 每项验证流程
1. 重启后端（`python main.py --web --port 58080`）
2. 用 agent-browser 测试相关场景
3. 检查后端日志无 ERROR
4. 更新 `docs/pitfalls.md`（如有踩坑）
5. 更新 `docs/completed.md`（完成后）

### 回滚方案
- 每个优化项加配置开关（环境变量 / settings.json），默认关闭
- 出问题 → 关开关 → 重启后端 → 回到原状态
- 代码改动遵循"新增文件优先，改动现有文件谨慎"原则

---

## 待用户审批

请用户审阅本计划后回复：
- **通过** → 从 P0 任务 1 开始执行
- **调整** → 告诉我要调整哪些任务/优先级
- **部分通过** → 告诉我先做哪几项

执行过程中如遇技术阻碍或设计变更，会及时同步。
