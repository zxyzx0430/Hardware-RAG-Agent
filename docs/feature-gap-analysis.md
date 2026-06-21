# Hardware RAG Agent — 功能完整性评估报告

> 评估日期：2026-06-20
> 评估范围：后端 `backend/`、前端 `frontend/`、`docs/api-contract.md`、`docs/pitfalls.md` 及代码审查记录
> 评估方法：静态代码审查 + 接口契约对照 + 项目路线图对照

---

## 执行摘要

| 指标 | 数值 |
|------|------|
| **总项数** | 12 |
| **已实现** | 1 |
| **部分实现** | 9 |
| **未实现** | 2 |
| **P0 缺失** | 2 项 |

### P0 缺失清单

1. **工具调用机制（Tool Use / Function Calling）**：目前只有 `search_docs` 的 SSE 占位事件和 `/api/tool` stub，缺乏真正的工具注册、LLM Function Calling、参数校验、工具结果回环与 ReAct Agent。
2. **RAG 与知识库管理**：`/api/kb/upload` 仅做文本分块并保存原文件，未真正向量化入库；缺少 `/api/kb/list`、`/api/kb/delete` 后端实现、混合检索（BM25 + Vector）、Reranker 与查询扩展。

### 总体结论

项目已完成 **LLM 流式聊天、会话/消息 CRUD、前端 UI 骨架、串口扫描 stub、PDF 解析管线** 等基础能力，但距离一个可称之为 "Agent" 的产品，核心差距在于 **真正的工具调用框架** 与 **可工作的 RAG 知识库闭环**。建议下一阶段优先补齐 P0 项，再逐步完善 P1 项。

---

## 1. 任务规划能力（Task Planning）

| 维度 | 说明 |
|------|------|
| **能力描述** | Agent 面对复杂用户请求时，能将其拆解为多个子任务并生成执行计划（如：先检索、再生成代码、再审计引脚），在 SSE 中返回 `plan` / `progress` 事件。 |
| **当前状态** | **未实现** |
| **缺失点** | 1. `/api/chat` 没有任务分解逻辑，直接做 RAG 检索后调用 LLM。<br>2. SSE 事件类型未定义 `plan` / `progress`（前端类型仅含 `thinking/text/tool/source/done/error`）。<br>3. 没有计划执行器、步骤依赖管理、多步工具编排。 |
| **实现后用户可见变化** | 用户问复杂问题（如“帮我设计一个 ESP32 读取 BME280 的完整项目”）时，界面先显示步骤计划：① 检索传感器参数 ② 生成接线图 ③ 生成驱动代码 ④ 审计引脚冲突，再逐步执行并展示进度。 |
| **会用到的技术/组件** | 后端：Pydantic Plan 模型、步骤状态机、ReAct / Plan-and-Solve Prompt、SSE `plan`/`progress` 事件；前端：`ChatSSEEvent` 扩展、`useChatStore` 新增分支、活动卡片。 |
| **优先级** | **P1** |

---

## 2. 工具调用机制（Tool Use / Function Calling）

| 维度 | 说明 |
|------|------|
| **能力描述** | Agent 能根据用户意图自主调用工具（搜索知识库、生成代码、审计引脚、生成接线图等），并将工具结果作为上下文继续推理。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 后端 `/api/tool` 是 stub，仅返回字符串占位。<br>2. `/api/chat` 中 `tool` 事件仅为 RAG 检索的硬编码展示（`name="search_docs"`，`args` 是字符串而非结构化对象），没有真正的工具注册表。<br>3. 没有 LLM Function Calling / ReAct Agent，没有 Pydantic 参数校验、工具超时、降级策略。<br>4. 前端 `useSettingsStore` 的 `skills` 只是配置开关，未与后端工具能力绑定。 |
| **实现后用户可见变化** | 用户提问后，聊天界面显示“调用 search_docs / generate_code / audit_pins”工具卡片；Agent 自动组合多次工具调用完成复杂任务；用户可直接在工作台点击按钮调用 `/api/tool`。 |
| **会用到的技术/组件** | 后端：LangChain `@tool`、ReAct Agent / OpenAI Function Calling、`AgentExecutor`、Pydantic 校验、asyncio 超时；前端：`ChatSSEEvent.tool` 结构化展示、工具调用历史。 |
| **优先级** | **P0** |

---

## 3. 上下文管理（Context Management / Memory）

| 维度 | 说明 |
|------|------|
| **能力描述** | 系统能跨会话、跨轮次保留用户偏好、项目背景、对话摘要，并在后续请求中自动注入相关上下文。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 前端 `longTermMemory` 只是设置项字符串，没有持久化的长期记忆存储/检索。<br>2. 后端 `ChatRequest` 接收 `long_term_memory` 但仅拼接到 system prompt，没有记忆管理系统。<br>3. 没有对话摘要（SummaryBufferMemory），长对话仅靠 token 预算截断早期历史。<br>4. 没有基于 embedding 的记忆检索或用户画像。 |
| **实现后用户可见变化** | 用户切换会话后再回来，Agent 仍能记住“你正在做 STM32F103 + MPU6050 项目”；长对话 100 轮后仍能追问早期话题；设置面板可管理长期记忆。 |
| **会用到的技术/组件** | 后端：`ConversationSummaryBufferMemory`、SQLite 记忆表、embedding-based memory retrieval；前端：长期记忆编辑框、记忆引用提示。 |
| **优先级** | **P1** |

---

## 4. 多轮对话处理（Multi-turn Dialogue）

| 维度 | 说明 |
|------|------|
| **能力描述** | 支持用户与 Agent 连续多轮交互，上下文不丢失，追问能引用前文。 |
| **当前状态** | **已实现** |
| **缺失点** | 1. 上下文窗口管理较粗糙（固定 128K 预算 + 简单字符估算截断）。<br>2. 没有针对长对话的主动摘要压缩策略（当前仅在超出预算时丢弃早期消息）。 |
| **实现后用户可见变化** | （已可用）用户可连续追问“它的 I2C 地址是多少？”，Agent 能根据上文理解“它”指 MPU6050。 |
| **会用到的技术/组件** | 已使用：`ChatRequest.messages`、`LLMClient._build_messages`、Zustand sessionMessages 持久化。 |
| **优先级** | **P2** |

---

## 5. 错误恢复机制（Error Recovery / Retry）

| 维度 | 说明 |
|------|------|
| **能力描述** | 面对网络抖动、LLM API 错误、RAG 检索失败、工具超时等情况，系统能自动重试、降级并给出清晰提示。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. LLMClient 有 `_with_retries`，但会吞掉未捕获的异常类型（`LLMError("LLM operation failed without error detail")`）。<br>2. RAG 检索异常被 `except Exception` 吞掉，没有日志记录，也没有降级到纯 LLM 的明确提示。<br>3. SSE 断开后前端没有指数退避重连（契约 2.17 要求）。<br>4. 没有熔断器、限流、工具调用超时后的降级策略。 |
| **实现后用户可见变化** | 网络短暂中断后聊天自动恢复；RAG 不可用时提示“知识库暂不可用，将直接回答”并继续生成；工具超时后自动降级为文本回答。 |
| **会用到的技术/组件** | 后端：`tenacity` / 自定义重试、结构化异常分类、日志记录；前端：SSE 重连策略（1s→2s→4s…）、`apiSSE` 空闲超时。 |
| **优先级** | **P1** |

---

## 6. 用户意图识别（Intent Recognition）

| 维度 | 说明 |
|------|------|
| **能力描述** | 在用户输入阶段识别查询意图（参数查询、代码生成、代码审查、故障排查、器件对比），并路由到不同的处理策略。 |
| **当前状态** | **未实现** |
| **缺失点** | 1. 没有意图分类器，所有问题都走同一套 RAG + LLM 流程。<br>2. 没有 Query Rewrite / 查询扩展，BM25 触发词机制未落地。<br>3. 没有基于意图的工具选择或 Prompt 路由。 |
| **实现后用户可见变化** | 用户问“帮我写 ESP32 读取 DHT11 的代码”时，Agent 自动识别为 `generate_code` 意图并直接调用代码生成工具；问“MPU6050 和 BME280 有什么区别”时自动走对比流程。 |
| **会用到的技术/组件** | 后端：LLM-based 意图分类 / 规则分类器、Query Rewrite Prompt、BM25 查询扩展表（`hardware_config.yaml`）；前端：根据意图显示不同活动卡片。 |
| **优先级** | **P1** |

---

## 7. RAG 与知识库管理

| 维度 | 说明 |
|------|------|
| **能力描述** | 支持硬件文档的上传、解析、向量化、检索、删除、分类管理，并返回带来源标注的回答。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. `/api/kb/upload` 仅做分块并保存原文件，**没有真正调用 embedding 向量化入库**（代码注释“保存原始文件供后续向量化”）。<br>2. 缺少 `/api/kb/list`、`/api/kb/delete` 后端路由（契约已约定但 routes.py 未实现）。<br>3. 没有 BM25 + Vector 混合检索、RRF、Reranker、查询扩展、自定义 tokenizer。<br>4. 没有多知识库 collection 分类（dev-boards/sensors/protocols 等）。<br>5. `KnowledgePanel` 调用 `fetchItems` 但后端无对应接口，列表依赖 mock 或本地缓存。 |
| **实现后用户可见变化** | 上传 PDF 后状态变为“已索引”且可检索；知识库面板真实列出文档、chunk 数量、分类；回答底部显示可点击的来源芯片；复杂查询召回率显著提升。 |
| **会用到的技术/组件** | 后端：`HardwareVectorStore.ingest`、OpenAIEmbeddings、ChromaDB、`rank-bm25` + `jieba`、EnsembleRetriever、Reranker API、Docling；前端：知识库面板真实 API 对接、来源引用高亮。 |
| **优先级** | **P0** |

---

## 8. 可观测性与日志

| 维度 | 说明 |
|------|------|
| **能力描述** | 提供请求追踪、结构化日志、性能指标、健康检查，便于问题定位。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 契约要求后端返回 `X-Request-Id` Header 并记录请求，当前未实现。<br>2. 日志为简单 `logging`，未统一 JSON 结构化；RAG 检索异常未记录详情。<br>3. `/health` 仅返回 `{"status":"healthy"}`，没有组件级健康状态（rag_engine / llm_api / chroma_db）。<br>4. 没有 LangSmith / Langfuse 追踪、没有 token 消耗统计看板。 |
| **实现后用户可见变化** | 每次请求都有唯一追踪 ID；健康检查页面显示各组件状态；开发者可在 LangSmith 中看到一次 RAG 请求的检索、LLM 调用全过程。 |
| **会用到的技术/组件** | 后端：FastAPI middleware 注入 `X-Request-Id`、结构化日志（`structlog`）、健康检查探针、LangSmith SDK；前端：日志/统计面板消费 usage 数据。 |
| **优先级** | **P1** |

---

## 9. 安全性与鉴权

| 维度 | 说明 |
|------|------|
| **能力描述** | 保护用户 API Key、防止滥用、校验输入、控制访问权限。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 仅通过 Header 传递 API Key，服务端不存储，但缺少传输层 HTTPS 强制、Key 前缀日志约定未完全执行。<br>2. 没有用户认证/会话隔离，所有用户共享同一 SQLite/ChromaDB。<br>3. 没有请求频率限制、文件上传大小/类型校验不完整（`/api/kb/upload` 未校验 50MB 上限）。<br>4. CORS 开发阶段为 `*`，生产未配置。<br>5. 没有输入输出过滤、防注入。 |
| **实现后用户可见变化** | 登录后才能使用；API Key 仅保存在浏览器 localStorage；上传超大文件时给出明确错误；生产环境启用 HTTPS 与特定 CORS 域名。 |
| **会用到的技术/组件** | 后端：JWT/OAuth、RateLimiter（slowapi）、Pydantic 校验、文件大小检查、CORS 环境变量、Secrets Manager；前端：安全存储策略、登录流程。 |
| **优先级** | **P1** |

---

## 10. 人机协作与反馈（Human-in-the-loop / Feedback）

| 维度 | 说明 |
|------|------|
| **能力描述** | 用户可对回答点赞/点踩、打断 Agent、确认工具执行、编辑重发、收藏；系统收集反馈用于改进。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 前端已有重试、编辑重发、分支、收藏、停止流式输出，但**没有明确的点赞/点踩反馈机制**。<br>2. 没有危险操作确认（如删除会话、删除知识库文档、烧录固件）。<br>3. 没有工具执行前的人工确认（例如 Agent 要执行编译/烧录前需用户确认）。<br>4. 反馈数据未回传到后端或用于模型改进。 |
| **实现后用户可见变化** | 每条回答旁显示 👍/👎；删除知识库或烧录前弹出确认；Agent 执行编译前显示“是否继续？”；后台可查看反馈统计。 |
| **会用到的技术/组件** | 后端：反馈表（`feedback`）、确认流程状态机；前端：反馈按钮、Modal 确认框、工具执行确认弹窗。 |
| **优先级** | **P2** |

---

## 11. 部署与配置管理

| 维度 | 说明 |
|------|------|
| **能力描述** | 支持一键部署、环境隔离、配置热重载、生产级健康检查。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 后端 `Settings` 支持 `.env` 热重载与保存，但缺少 Docker / Docker Compose 编排。<br>2. 没有生产环境配置模板、Nginx 反向代理示例。<br>3. Vite 代理端口与后端默认端口不一致的历史问题已修复，但仍需统一文档。<br>4. 缺少 CI/CD、自动化测试流水线。 |
| **实现后用户可见变化** | 用户执行 `docker compose up -d` 即可启动完整服务；生产部署有 HTTPS + Nginx 示例；配置修改无需重启即可生效。 |
| **会用到的技术/组件** | Dockerfile、Docker Compose、Nginx、GitHub Actions / CI、环境变量管理。 |
| **优先级** | **P1** |

---

## 12. 测试与文档

| 维度 | 说明 |
|------|------|
| **能力描述** | 覆盖单元测试、集成测试、RAG 评测基准、完整用户文档。 |
| **当前状态** | **部分实现** |
| **缺失点** | 1. 后端 `tests/test_main.py` 中测试的路由（`/v1/models`、`/chat`、`/chat/stream`）与当前实现不一致，**测试已过期**。<br>2. 缺少 RAG 检索评测（10 题/20 题基线）、Agent 工具选择评测、端到端测试。<br>3. 没有前端测试（Vitest / React Testing Library）。<br>4. 缺少 `README.md` 快速启动、架构图、功能截图；`docs/architecture.md`、`docs/faq.md` 未创建。 |
| **实现后用户可见变化** | 每次代码提交自动跑测试；README 有清晰架构图与一键启动命令；有公开评测报告展示准确率。 |
| **会用到的技术/组件** | 后端：pytest、FastAPI TestClient、评测数据集（`tests/test_edge_cases.py` 等）；前端：Vitest、React Testing Library；文档：Mermaid 架构图、评测报告。 |
| **优先级** | **P1** |

---

## 关键依赖与阻塞项

| 阻塞项 | 影响范围 | 说明 |
|--------|----------|------|
| 后端默认端口 `8000` 与契约 `58080` 不一致 | 部署/联调 | `settings.py` 默认端口为 8000，`docs/api-contract.md` 写 58080；`.env` 需显式统一。 |
| `/api/kb/upload` 未向量化 | RAG 核心 | 即使上传文件，知识库检索仍为空，导致 RAG 回答无来源或 hallucination。 |
| `/api/tool` stub | Agent 核心 | 前端工具按钮无法产生真实效果。 |
| SSE/接口字段与契约不一致 | 前后端联调 | 详见 `docs/review-backend.md`、`docs/review-frontend.md`，联调前需先对齐文档。 |

---

## 建议的后续优先级路线图

### Phase 1：Agent 核心闭环（P0，建议 1-2 周）
1. 实现 `/api/kb/upload` 真正向量化入库，补齐 `/api/kb/list`、`/api/kb/delete`。
2. 实现工具注册表 + ReAct Agent，将 `search_docs`、`generate_hardware_code`、`review_hardware_code`、`audit_pins`、`wiring` 真正接入 `/api/chat` 与 `/api/tool`。
3. 对齐前后端 SSE/JSON 字段与 `docs/api-contract.md`。

### Phase 2：RAG 质量与工程化（P1，建议 2-3 周）
1. 加入 BM25 混合检索、查询扩展、Reranker、自定义 tokenizer。
2. 实现 `X-Request-Id`、结构化日志、组件级健康检查。
3. 补充 Dockerfile / Docker Compose、生产配置示例。
4. 修复并扩充测试集，建立 20 题 RAG 评测基线。

### Phase 3：体验与安全（P2-P1，建议 1-2 周）
1. 长期记忆 / SummaryBufferMemory。
2. 消息反馈、工具执行确认、危险操作二次确认。
3. 用户认证、Rate Limit、HTTPS/CORS 生产配置。

---

*本报告仅做评估，未修改任何代码。*
