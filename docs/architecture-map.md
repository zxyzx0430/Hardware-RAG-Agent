# Hardware RAG Agent 项目全景图

> 面向小白的架构 + 传导链 + 能力地图
> 落盘位置：桌面 / 项目内 docs/architecture-map.md（双份同步）
> 维护规则：见项目根目录 AGENTS.md「项目全景图维护」章节
> Last reviewed: 2026-07-01（v3.1：补全 LangGraph ReAct Agent 链路/ToolAudit 表/agent-sandbox 路由/Agent 模块/参数/bug 修复）

---

## 一句话理解这个项目

**用户问硬件问题 → 系统去芯片手册里找相关段落 → 把段落+问题一起发给 AI → AI 流式吐字回答 → 回答标注来源**。同时还能生成接线图、审查引脚冲突、在沙箱跑代码、监视串口、扩展 MCP 工具。

**定位**：本地自部署（监听 127.0.0.1，不暴露公网），开源到 GitHub。用户自配 API Key + 自选模型。

---

## 目录

- [一、整体架构（三层蛋糕）](#一整体架构三层蛋糕)
- [二、用户能力地图（用户能做什么）](#二用户能力地图用户能做什么)
- [三、工程能力地图（开发者怎么用）](#三工程能力地图开发者怎么用)
- [四、核心数据流（关键链路图）](#四核心数据流关键链路图)
- [五、状态管理（前端 10 个 Store）](#五状态管理前端-10-个-store)
- [六、数据库表清单（8 张表）](#六数据库表清单8-张表)
- [七、API 路由总表（11 个模块 60+ 端点）](#七api-路由总表11-个模块-60-端点)
- [八、前端组件清单（32 个组件）](#八前端组件清单32-个组件)
- [九、关键参数与限制汇总](#九关键参数与限制汇总)
- [十、文件目录速查](#十文件目录速查)
- [十一、已修复的 bug 在哪条链上](#十一已修复的-bug-在哪条链上)
- [十二、给小白的"看代码从哪开始"](#十二给小白的看代码从哪开始)
- [十三、长期迭代维护规则](#十三长期迭代维护规则)

---

## 一、整体架构（三层蛋糕）

```
┌─────────────────────────────────────────────────────────┐
│  浏览器（前端）  React 19 + TypeScript + Vite 6 +        │
│                  Tailwind 4 + Zustand 5 + React Query 5  │
│  你看到的所有界面：会话列表、聊天框、知识库管理、设置、    │
│  硬件工作台（串口/烧录/预览/接线图/引脚审查）              │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP 请求 (/api/*)
                         │ SSE 流式（吐字）
                         │ WebSocket（串口）
                         ▼
┌─────────────────────────────────────────────────────────┐
│  后端服务  FastAPI (Python 3.11)  监听 127.0.0.1:58080   │
│  接请求 → 调 RAG 检索 → 调 AI 接口 → 存数据库 → 返回      │
│  11 个路由模块 + 中间件 + Prometheus 指标                 │
└────────────────────────┬────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   ┌─────────┐     ┌──────────┐     ┌──────────┐
   │ SQLite  │     │ ChromaDB │     │ 外部 AI  │
   │ 会话/消息│     │ 向量库   │     │ OpenAI等│
   │ 书签/设置│     │ (手册切片)│     │ 流式回答 │
   │ 反馈/Token│    │ + BM25   │     │          │
   └─────────┘     └──────────┘     └──────────┘
                         │
                         ▼
                   ┌──────────┐
                   │  Docker  │  沙箱跑用户代码
                   │ python/  │  (python/c/cpp/
                   │ gcc/node/│  js/arduino)
                   │ platformio│
                   └──────────┘
```

**小白类比**：
- 前端 = 餐厅前台（你点菜的地方）
- 后端 = 厨房（接单、协调、出菜）
- SQLite = 订单本（记会话/消息/设置）
- ChromaDB = 菜谱柜子（手册切片+向量）
- 外部 AI = 大厨（真正做菜）
- Docker = 试菜区（先在隔离环境跑一遍）

---

## 二、用户能力地图（用户能做什么）

> 这一节回答："作为终端用户，我能用这套系统做什么？"

### 能力 1：和 AI 聊硬件问题（核心）

**用户操作**：在输入框打字 → 回车发送 → 看回答一个个字蹦出来 → 回答带 [src1][src2] 来源角标

**背后链路**：
```
InputBar.tsx 输入 → useChatStore.sendMessage()
  → apiSSE("/api/chat", {messages, model, kb_ids, ...})
  → 后端 chat_routes.py:chat_sse()
    → _rewrite_query_for_rag()   LLM 改写查询（24h LRU 缓存）
    → _run_rag_retrieval()       RAG 检索（thinking 事件实时推送）
        → search_docs_core()
        → KnowledgeBaseManager.search()  BM25 + 向量 + RRF + Reranker
    → _build_system_prompt()     把检索到的段落塞进提示词
    → LLMClient.chat_stream()    调外部 AI
        → 一边吐字 → SSE event: {type:"token", content:"P"}
        → 一边推来源 → SSE event: {type:"source", docs:[...]}
        → 结束 → SSE event: {type:"done", usage:{...}}
  → 前端 onToken/onSource/onDone 回调
  → onDone 时固化 Message + persistLastTurn() 落库
```

**关键特性**：
- ✅ 流式吐字（SSE，5 分钟空闲超时）
- ✅ 思考卡片（"正在改写查询..."/"正在检索文档..." 完成后保留可见）
- ✅ 来源角标 [srcN]（强制绑定 source，禁止 LLM 自由编造）
- ✅ 多模态（图片附件走 vision 模型）
- ✅ 上下文摘要（超窗口时自动截断+摘要早期对话）
- ✅ 推理模型支持（DeepSeek-R1/o1 的 thinking 字段单独渲染）
- ✅ 查询改写（展开缩写、解析代词、提取术语，8s 超时降级）
- ✅ 工具调用步骤展示（tool 事件 → ActivityBlock）

**关键文件**：
- 前端：[useChatStore.ts](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.ts)、[ChatArea.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ChatArea.tsx)、[InputBar.tsx](file:///e:/Desktop/agent/frontend/src/components/input/InputBar.tsx)
- 后端：[chat_routes.py](file:///e:/Desktop/agent/backend/app/api/chat_routes.py)、[chat_helpers.py](file:///e:/Desktop/agent/backend/app/api/chat_helpers.py)、[client.py](file:///e:/Desktop/agent/backend/src/llm/client.py)

---

### 能力 2：管理知识库（RAG 入库）

**用户操作**：
1. 建知识库（命名、选切分策略、选 embedding 模型）
2. 上传 PDF/DOCX/XLSX/CSV/JSON/HTML/MD 文件
3. 看切片详情（每个 chunk 的内容、页码、来源）
4. 启用/禁用知识库（搜索时按 KB 多选）
5. 导出/导入 KB（跨机器迁移）

**三种切分策略对比**：

| 策略 | 速度 | 质量 | 适用场景 | 关键参数 |
|------|------|------|----------|----------|
| `hybrid` | 快 | 中 | 结构化文档（有标题/代码块） | chunk_size=1000/overlap=200/small=800 |
| `agent` | 慢 | 高 | 长文档、需要 LLM 语义判断 | num_rounds=3/max_batch=80000/small=500 |
| `multimodal` | 最慢 | 最高 | PDF 含图片/表格/电路图 | batch_size=5 页/dpi=200/vision_concurrency=4 |

**入库链路**：
```
kb_routes.py:kb_upload()
  ├─► magic bytes 校验文件类型
  ├─► _parse_file()                 选解析器
  │     ├─► PDF    → UnifiedPdfParser (PyMuPDF→Docling→PyMuPDF 三段 fallback)
  │     ├─► DOCX   → DocxParser (python-docx，保留标题/表格/代码块)
  │     ├─► XLSX   → XlsxParser (openpyxl → Markdown 表格)
  │     ├─► CSV    → CsvParser (chardet 自动检测编码)
  │     ├─► JSON   → JsonParser (递归格式化)
  │     ├─► HTML   → HtmlParser (BeautifulSoup+lxml)
  │     └─► 图片   → PaddleOcrParser (300 DPI，需 OCR_ENABLED=true)
  │
  ├─► _get_kb_chunker()             选切分器
  │     └─► chunking/factory.py
  │           ├─► HybridChunker     按段落+大小切，small-to-big
  │           ├─► AgentChunker      LLM 三轮投票，bilingual prompt
  │           └─► MultimodalChunker 视觉 LLM 看页面图，跨页表格合并
  │
  ├─► HardwareVectorStore.add()     入向量库
  │     ├─► embedding API 批量转向量（diskcache 缓存）
  │     ├─► ChromaDB 5000 一批（绕过 5461 Rust 限制）
  │     └─► 维度严格校验
  │
  ├─► BM25 索引即时重建             jieba 分词 + 80+ 硬件术语字典
  └─► 写 KnowledgeDoc 记录到 SQLite
```

**关键特性**：
- ✅ 13 字段来源归因（page_start/page_end/section_title/source_url/category/chunk_method/kb_id/kb_name 等）
- ✅ 重复上传检测（同 KB 同名文件返回 DUPLICATE_FILE）
- ✅ 删除一致性（先删向量失败保留 DB 记录以便重试）
- ✅ 内置 KB（`builtin-001` "硬件手册库"，启动自动创建，不可删）
- ✅ 跨 KB 并行搜索（ThreadPoolExecutor max 8 workers）
- ✅ chunk_method 切换（同一文档可重新切分）
- ✅ agent_trace 透明度（LLM 决策过程存 metadata）

**关键文件**：
- 前端：[KnowledgePanel.tsx](file:///e:/Desktop/agent/frontend/src/components/knowledge/KnowledgePanel.tsx)、[KbCollectionManager.tsx](file:///e:/Desktop/agent/frontend/src/components/knowledge/KbCollectionManager.tsx)、[useKnowledgeStore.ts](file:///e:/Desktop/agent/frontend/src/stores/useKnowledgeStore.ts)
- 后端：[kb_routes.py](file:///e:/Desktop/agent/backend/app/api/kb_routes.py)、[kb_manager.py](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py)、[chunking/](file:///e:/Desktop/agent/backend/src/rag/chunking/)

---

### 能力 3：硬件工作台（5 个 Tab）

**用户操作**：点右侧面板的工作台，可见 5 个 Tab：

#### Tab 1: SerialPane（串口监视器）
- 扫描串口（`GET /api/devices`）
- WebSocket 双向桥接（`WS /api/monitor/{port}?baud=115200`）
- DTR/RTS 控制
- ANSI 颜色解析（`\x1b[31m` → 红色）
- 日志过滤、自动滚动、导出

#### Tab 2: FlashPane（编译烧录）
- 选芯片（esp32-s3 / esp32-c3 / esp32 / stm32f407）
- 选端口
- `POST /api/build` SSE 编译进度
- `POST /api/upload` SSE 烧录进度
- **当前状态**：v2 mock（返回模拟进度）

#### Tab 3: PreviewPane（代码预览）
- 多 tab 编辑器（textarea + 行号同步滚动）
- 复制 / 推送到 Flash
- 代码诊断（`POST /api/diagnose`）
- 默认 Arduino 模板代码

#### Tab 4: WiringPane（接线图）
- 填组件（name/type/pins）+ 连线（from/to/color/label）
- `POST /api/wiring` 生成 SVG + BOM
- 鼠标拖拽平移、滚轮缩放（以鼠标为中心）
- DOMPurify 清洗 SVG（防 XSS）
- 节点 > 100 警告
- 6 种组件类型颜色：mcu=橙 / sensor=蓝 / actuator=红 / display=紫 / power=绿 / module=黄

#### Tab 5: SafetyPane（引脚安全审计）
- 从代码自动正则提取 `#define`/`pinMode`/`digitalRead`/`digitalWrite`
- `POST /api/audit_pins` 审查
- 显示引脚分配表 + Strapping 冲突详情
- 审查规则：
  - GPIO 重复使用 → critical
  - Strapping 引脚（esp32: {0,2,4,5,12,15} / esp32-s3: {0,3,45,46}）→ warning
  - 解析失败的引脚名 → warning

**关键文件**：
- 前端：[WorkbenchPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/workbench/WorkbenchPanel.tsx)（1153 行，5 个 Pane）
- 后端：[hardware_routes.py](file:///e:/Desktop/agent/backend/app/api/hardware_routes.py)、[audit.py](file:///e:/Desktop/agent/backend/app/hardware/audit.py)、[svg_generator.py](file:///e:/Desktop/agent/backend/src/hardware/svg_generator.py)、[tool_routes.py](file:///e:/Desktop/agent/backend/app/api/tool_routes.py)（串口 WS）

---

### 能力 4：代码沙箱（在隔离环境跑代码）

**用户操作**：在聊天里贴代码，AI 可以调 `code_executor` 工具执行，或前端 PreviewPane 推送过来执行

**支持 5 种语言**：

| 语言 | Docker 镜像 | 执行方式 |
|------|------------|----------|
| `python` | `python:3.11-slim` | `python -c code` |
| `c` | `gcc:latest` | 写 .c 文件 → gcc 编译 → 运行 |
| `cpp` | `gcc:latest` | 写 .cpp 文件 → g++ 编译 → 运行 |
| `javascript` | `node:20-slim` | `node -e code` |
| `arduino` | `platformio/platformio-core:latest` | 写 main.ino → `platformio ci --board=esp32dev` |

**安全限制**：
- CPU 超时 30s（超时 kill）
- 内存 256m（含 swap，禁 swap）
- 1 个 CPU
- 无网络
- /tmp 限 50m tmpfs
- 只读根文件系统
- 非 root（user=nobody）
- stdout 截断最后 10000 字符 / stderr 5000 字符
- 后端并发 Semaphore(4)
- code_executor 工具超时 15s

**关键文件**：
- 后端：~~已废弃~~ Docker 沙箱已于 2026-06-30 移除，替代方案为 Agent `run_command` 工具（权限门控 + 审计日志，详见 [能力 4](#能力-4代码沙箱在隔离环境跑代码) 已废弃说明）

---

### 能力 5：MCP 工具扩展（接外部工具服务器）

**用户操作**：设置页 → MCP tab → 添加 MCP Server → 启动 → AI 自动发现并调用其工具

**支持协议**：JSON-RPC 2.0 over stdio（Protocol Version 2024-11-05）

**典型用法**：
```
用户添加 MCP Server：
  name: "github"
  command: "npx"
  args: ["-y", "@modelcontextprotocol/server-github"]

→ 后端 mcp_routes.py 注册配置
→ 用户点"启动"
→ MCPServerManager.start() 启动子进程
→ MCPClient.initialize() 握手
→ MCPClient.list_tools() 发现工具
→ register_mcp_tools() 自动注册到 tool_router
  工具命名：mcp_{server_id}_{tool_name}
→ AI 在聊天中可调用这些工具
```

**关键特性**：
- ✅ 多 Server 并行管理
- ✅ 进程崩溃自动重连
- ✅ 30s 请求超时
- ✅ 工具 Schema 自动校验
- ✅ 错误信息脱敏（mask `sk-xxx`、URL 中的 key/secret/token）

**关键文件**：
- 后端：[mcp_routes.py](file:///e:/Desktop/agent/backend/app/api/mcp_routes.py)、[manager.py](file:///e:/Desktop/agent/backend/src/mcp/manager.py)、[client.py](file:///e:/Desktop/agent/backend/src/mcp/client.py)、[tool_router.py](file:///e:/Desktop/agent/backend/src/agent/tool_router.py)

---

### 能力 6：多模型管理（自选 AI）

**用户操作**：设置页 → API tab → 选 provider（OpenAI/DeepSeek/Qwen/Claude/Ollama 等 OpenAI-compatible）→ 填 API Key → 选模型

**内置 12 个 chat 模型注册表**：

| 模型 | 上下文窗口 | 最大输出 |
|------|-----------|----------|
| gpt-4o / gpt-4o-mini | 128K | 16384 |
| gpt-4.1 / gpt-4.1-mini | **1M** | 65536 |
| deepseek-v4 / deepseek-v4-flash | 256K | 8192 |
| deepseek-chat | 65536 | 8192 |
| qwen3-235b | 256K | 8192 |
| qwen-plus | 131072 | 8192 |
| qwen-max | 32768 | 8192 |
| claude-3-5-sonnet / claude-3-5-haiku | 200K | 8192 |

**默认值**：未知模型回退 `context_window=128000` / `max_tokens=4096`

**关键特性**：
- ✅ API Key 加密存储（Fernet 对称加密，存 `keys_store.json`）
- ✅ 多 provider 同时配置，每个 provider 独立 Key
- ✅ 运行时覆盖（每次请求可临时带 api_key/base_url/model，Web 场景每人用自己的 Key）
- ✅ Ollama 自动检测（base_url 含 `ollama` 或 `11434` 时启用 `extra_body={"think": True}`）
- ✅ 推理模型字段提取（`reasoning_content` / `reasoning` / `thinking`）
- ✅ 分类重试（AuthError 不重试，RateLimit/5xx/网络错误指数退避 3 次）
- ✅ 模型列表代理（`POST /api/models` 拉取上游）

**关键文件**：
- 后端：[client.py](file:///e:/Desktop/agent/backend/src/llm/client.py)、[model_registry.py](file:///e:/Desktop/agent/backend/src/llm/model_registry.py)、[auth.py](file:///e:/Desktop/agent/backend/app/api/auth.py)
- 前端：[SettingsPage.tsx](file:///e:/Desktop/agent/frontend/src/components/settings/SettingsPage.tsx)、[useSettingsStore.ts](file:///e:/Desktop/agent/frontend/src/stores/useSettingsStore.ts)

---

### 能力 7：会话/书签/快照/搜索

#### 会话管理
- 创建/重命名/删除/置顶（最多 5 个）
- 项目分组（chips，颜色哈希）
- 按时间分组（today/yesterday/thisWeek/earlier）
- **对话分支**：从某条消息分叉出新会话（branch_from_session_id + branch_from_message_id）
- 右键菜单：重命名/置顶/移动到项目/删除

#### 书签
- 收藏任意消息到文件夹
- 文件夹 CRUD（删除文件夹时书签移到 default）
- 跨会话跳转（点书签 → 切到对应会话 → 滚动到消息）

#### 对话快照
- 保存当前消息+KB 配置到 localStorage
- 恢复快照（confirm 后替换）
- 对比快照（按行 diff：add/remove/change/same）

#### 搜索
- 本地搜索（当前会话+会话列表，纯前端过滤）
- 全局搜索（`POST /api/search` FTS5 全文索引，失败降级 LIKE）

#### 模板
- 4 个默认模板（代码审查/问题分析/知识检索/总结）
- 保存当前输入为模板
- 输入 `/` 触发模板面板

**关键文件**：
- 前端：[SessionPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/session/SessionPanel.tsx)、[BookmarkPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/bookmarks/BookmarkPanel.tsx)、[SnapshotPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/SnapshotPanel.tsx)、[SearchModal.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/SearchModal.tsx)、[TemplatePanel.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/TemplatePanel.tsx)

---

### 能力 8：其他用户功能

| 功能 | 入口 | 说明 |
|------|------|------|
| 主题切换 | 汉堡菜单 / 设置 | light/dark/auto（auto 跟随系统） |
| 中英文切换 | 设置 | i18n，zh/en 两种语言 |
| 聊天字号 | 设置 | appearance tab，可调 |
| 快捷键 | Ctrl+/ | 8 个快捷键（Ctrl+K 搜索 / Ctrl+, 设置 / Ctrl+N 新建 等） |
| 聊天统计 | 汉堡菜单 / 浮层 | 9 项指标 + SVG 折线图 |
| Token 用量 | 设置 → usage tab | 30 天每日 input/output 折线图 + 按模型分布 |
| 消息反馈 | 👍/👎 | 写入 feedback 表 |
| 导出对话 | 汉堡菜单 | markdown / json |
| 引用消息 | 消息操作栏 | 引用上一条再回复 |
| 重试/编辑重发 | 消息操作栏 | 后端截断 keep_count=N 保证一致性 |
| 多附件 | 输入框 | 最多 3 个，10MB 限制，支持粘贴/拖拽 |
| 错误友好展示 | ErrorBlock | 7 种错误码对应不同图标和文案 |

---

## 三、工程能力地图（开发者怎么用）

> 这一节回答："作为开发者/贡献者，我怎么启动、测试、调试、扩展这个项目？"

### 3.1 启动部署

#### 第一次启动（5 步）

```powershell
# 1. 克隆 + 配置环境变量
cd e:\Desktop\agent
cp .env.example .env
# 编辑 .env：填 LLM_API_KEY、EMBEDDING_API_KEY（阿里云 dashscope）

# 2. 后端依赖 + 建表
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head                          # 建表

# 3. 构建内置 KB（首次必须，否则启动报错）
# 把 PDF 放到 backend/data/pdfs/
python ..\scripts\build_builtin_kb.py

# 4. 前端依赖
cd ..\frontend
npm install

# 5. 启动（两个终端）
# 终端 1：后端（端口必须 58080，否则前端代理调不通）
cd backend
python main.py --web --port 58080

# 终端 2：前端
cd frontend
npx vite --port 5173
```

#### 访问地址
- 前端：http://127.0.0.1:5173
- 后端 API 文档：http://127.0.0.1:58080/docs
- 健康检查：http://127.0.0.1:58080/health

#### 一键启动脚本
- [scripts/dev.ps1](file:///e:/Desktop/agent/scripts/dev.ps1) — 自动检查环境、端口、启动前后端、轮询健康
- **⚠️ 坑**：dev.ps1 默认后端端口 8000，但 vite.config.ts 代理目标是 58080，**两者不一致**。要么改 dev.ps1 的 `$BE_PORT=58080`，要么改 vite.config.ts 的 target

#### CLI 模式（不用前端）
```powershell
cd backend
python main.py                    # 默认 CLI 模式，终端交互
# 支持 /help /quit /clear /model /history /system
```

#### 关键约束
- **监听 127.0.0.1**（安全立场：不暴露公网，不防网络攻击）
- **无 Dockerfile**（项目本身不容器化，但沙箱用 Docker 跑用户代码）
- **SSE 代理禁缓冲**：[vite.config.ts](file:///e:/Desktop/agent/frontend/vite.config.ts) 显式设 `x-accel-buffering: no`，保证流式响应不被中间层缓冲

---

### 3.2 测试体系

#### 后端单测（140+ 用例）

```powershell
cd e:\Desktop\agent\backend
python -m pytest                              # 跑全部
python -m pytest tests/test_routes_chat.py -v # 单文件
python -m pytest tests/test_rag_edge_cases.py -v
```

**测试隔离设计**：[conftest.py](file:///e:/Desktop/agent/backend/tests/conftest.py) 有 `autouse=True` fixture patch `app.api.auth._load_store`，让所有测试**绕过鉴权**且不依赖磁盘 `keys_store.json`。

**测试文件清单**：

| 文件 | 测什么 |
|------|--------|
| [test_main.py](file:///e:/Desktop/agent/backend/tests/test_main.py) | FastAPI 入口、/health、/api/models 透传动态请求头 |
| [test_routes_chat.py](file:///e:/Desktop/agent/backend/tests/test_routes_chat.py) | /api/chat SSE 事件序列：thinking→text→done；error 后必须 done |
| [test_routes_kb.py](file:///e:/Desktop/agent/backend/tests/test_routes_kb.py) | /api/kb/upload 异步索引、文件超限 FILE_TOO_LARGE |
| [test_routes_wiring.py](file:///e:/Desktop/agent/backend/tests/test_routes_wiring.py) | /api/wiring 返回 SVG + BOM |
| [test_routes_diagnose.py](file:///e:/Desktop/agent/backend/tests/test_routes_diagnose.py) | /api/diagnose 5 类诊断、GPIO0 Strapping、同引脚 INPUT/OUTPUT FAIL |
| [test_routes_audit_pins.py](file:///e:/Desktop/agent/backend/tests/test_routes_audit_pins.py) | /api/audit_pins 标准格式 |
| [test_routes_tool.py](file:///e:/Desktop/agent/backend/tests/test_routes_tool.py) | /api/tool 已知工具 success+data，未知 TOOL_NOT_FOUND |
| [test_chunking.py](file:///e:/Desktop/agent/backend/tests/test_chunking.py) | HybridChunker/AgentChunker 纯逻辑（不依赖 DB） |
| [test_rag_edge_cases.py](file:///e:/Desktop/agent/backend/tests/test_rag_edge_cases.py) | RAG P0/P1 bug 回归：RRF key 哈希、constant_k 除零、clamp |
| [test_bm25_rag_verify.py](file:///e:/Desktop/agent/backend/tests/test_bm25_rag_verify.py) | BM25+RRF 12 篇语料精确匹配验证 |
| [test_llm.py](file:///e:/Desktop/agent/backend/tests/test_llm.py) | LLMClient 流式/非流式、重试、reasoning_content 提取 |
| [test_llm_list_models_error.py](file:///e:/Desktop/agent/backend/tests/test_llm_list_models_error.py) | list_models 失败抛 LLMError |
| [test_settings.py](file:///e:/Desktop/agent/backend/tests/test_settings.py) | Settings 默认值、.env 覆盖、reload、save_to_env |
| [test_day1_config.py](file:///e:/Desktop/agent/backend/tests/test_day1_config.py) | Week 1 Day 1 学习项目（保留） |
| [test_html_ingest.py](file:///e:/Desktop/agent/backend/tests/test_html_ingest.py) | HtmlParser script/style 过滤、table→md |
| [test_ocr_parser.py](file:///e:/Desktop/agent/backend/tests/test_ocr_parser.py) | PaddleOcrParser OCR_ENABLED=False 跳过、懒加载 |

#### RAG Golden Eval（双轨评测）

**位置**：[backend/tests/rag_eval/](file:///e:/Desktop/agent/backend/tests/rag_eval/)

**两套并行评测，互不干扰**：

| 评测 | 工具 | 评分维度 | 题数 | 输出 |
|------|------|----------|------|------|
| 规则评分 | [run_eval.py](file:///e:/Desktop/agent/backend/tests/rag_eval/run_eval.py) | 召回命中(30)+关键词覆盖(25)+chunk完整性(25)+边界(10)+跨章节(10) | 15 | `data/test_results/rag_eval_*.{json,md}` |
| DeepEval LLM Judge | [run_golden_eval.py](file:///e:/Desktop/agent/backend/tests/rag_eval/run_golden_eval.py) | context_recall(30)+faithfulness(25)+answer_relevancy(25)+context_precision(20) | 30 | `data/test_results/golden_eval_*.{json,md}` |

**运行**：
```powershell
cd backend
# 校验数据集格式
python -m tests.rag_eval.run_golden_eval --validate-only

# 跑 DeepEval（需后端运行 + API Key + kb_id）
python -m tests.rag_eval.run_golden_eval `
    --api-key YOUR_KEY --model gpt-4o-mini `
    --base-url https://api.openai.com/v1 --kb-id kb-xxxxxxxx

# 调试特定题
python -m tests.rag_eval.run_golden_eval --ids G001,G002 --api-key ...

# 规则评分
python -m tests.rag_eval.run_eval --api-key ... --model ...
```

#### 前端测试

```powershell
cd e:\Desktop\agent\frontend
npx vitest run           # 单次跑
npx vitest               # watch 模式
npm run lint             # eslint
npx tsc --noEmit         # 类型检查
```

**测试文件**：
- [client.test.ts](file:///e:/Desktop/agent/frontend/src/api/client.test.ts) — apiPost/apiGet 解包 `{success,data}`、`{success:false,error}` 抛 ApiError
- [useChatStore.test.ts](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.test.ts) — SSE 事件流、stopStreaming 清理、**切会话后 SSE 内容仍写入发起请求的会话**（关键正确性）

---

### 3.3 调试工具

#### 日志
- 后端日志：`backend/logs/` 或 `backend_dev.log`
- 前端日志：`useLogStore`（buffer 2000 条，5 级 error/warn/info/ok/debug）→ 设置 → logs tab 查看
- 设置页 logs tab：按级别过滤、复制、清除

#### Prometheus 指标
- 端点：`GET /metrics`（需 `METRICS_ENABLED=true`）
- 指标：
  - `_RAG_REQUESTS_TOTAL` — RAG 请求总数
  - `_RAG_RETRIEVAL_SECONDS` — RAG 检索耗时
  - `_LLM_TOKENS_TOTAL` — LLM token 用量
  - `_RAG_RERANKER_SECONDS` — Reranker 耗时

#### 开发辅助脚本

| 脚本 | 用途 |
|------|------|
| [scripts/list_models.py](file:///e:/Desktop/agent/scripts/list_models.py) | 列出 LLM_BASE_URL 可用模型 |
| [scripts/build_builtin_kb.py](file:///e:/Desktop/agent/scripts/build_builtin_kb.py) | 构建内置硬件手册 KB（`--force` 清空重建） |
| [scripts/dev.ps1](file:///e:/Desktop/agent/scripts/dev.ps1) | 一键启动前后端 |
| [scripts/audit/audit_ch340g_v2.py](file:///e:/Desktop/agent/scripts/audit/audit_ch340g_v2.py) | 审计 CH340G chunks 质量（image_description 覆盖率、page_start 准确性、表格保留） |
| [scripts/audit/export_chroma_chunks.py](file:///e:/Desktop/agent/scripts/audit/export_chroma_chunks.py) | 导出 ChromaDB chunks 到 JSONL |
| [scripts/audit/render_pdf_pages.py](file:///e:/Desktop/agent/scripts/audit/render_pdf_pages.py) | 渲染 PDF 每页为 PNG+TXT+表格MD |
| [scripts/reindex_ch340g.py](file:///e:/Desktop/agent/scripts/reindex_ch340g.py) | 重新索引 CH340G（multimodal） |
| [scripts/reindex_ch340g_hybrid.py](file:///e:/Desktop/agent/scripts/reindex_ch340g_hybrid.py) | 重新索引 CH340G（hybrid） |

---

### 3.4 配置管理

#### 环境变量（[.env.example](file:///e:/Desktop/agent/.env.example)）

| 变量 | 默认 | 用途 |
|------|------|------|
| `LLM_API_KEY` | sk-your-key | LLM API Key |
| `LLM_BASE_URL` | https://api.openai.com/v1 | LLM URL |
| `LLM_MODEL` | gpt-4o-mini | 默认模型 |
| `LLM_TEMPERATURE` | 0.7 | 温度 |
| `LLM_MAX_TOKENS` | 4096 | 最大 token |
| `CHROMA_PERSIST_DIR` | data/chroma | ChromaDB 目录 |
| `CHROMA_MODE` | persistent | persistent / http |
| `CHROMA_HOST` / `CHROMA_PORT` | localhost/8000 | http 模式用 |
| `SQLITE_DB_PATH` | data/chat_history.db | SQLite 路径 |
| `EMBEDDING_API_KEY` | 空 | 阿里云 dashscope（空跳过向量化） |
| `EMBEDDING_BASE_URL` | https://dashscope.aliyuncs.com/compatible-mode/v1 | Embedding URL |
| `EMBEDDING_MODEL` | text-embedding-v4 | Embedding 模型 |
| `HOST` / `PORT` | 127.0.0.1 / 8000 | 监听（vite 代理要 58080） |
| `LOG_LEVEL` | INFO | 日志级别 |
| `OCR_ENABLED` | False | OCR 开关 |
| `OCR_LANG` | ch | OCR 语言 |
| `BM25_K1` / `BM25_B` | 1.5 / 0.75 | BM25 参数 |
| `HNSW_EF_SEARCH` | 200 | HNSW 搜索参数 |
| `MAX_ATTACHMENT_CHARS` | 12000 | 附件最大字符 |
| `METRICS_ENABLED` | False | Prometheus 开关 |

#### 配置热重载
- [settings.py](file:///e:/Desktop/agent/backend/src/config/settings.py) `reload()` 运行时重载
- `save_to_env()` 智能写入 .env（保留注释）
- `@lru_cache get_settings()` 全局单例

---

### 3.5 数据库迁移（Alembic）

```powershell
cd e:\Desktop\agent\backend
alembic upgrade head                          # 应用所有迁移
alembic current                               # 查看当前版本
alembic revision --autogenerate -m "描述"     # 生成新迁移
alembic downgrade -1                          # 回退一步
```

**配置**：[alembic.ini](file:///e:/Desktop/agent/backend/alembic.ini) + [alembic/env.py](file:///e:/Desktop/agent/backend/alembic/env.py)
- `sqlalchemy.url = sqlite:///%(here)s/data/hardware_rag.db`
- 初始迁移 [9467f02f4f43_init.py](file:///e:/Desktop/agent/backend/alembic/versions/9467f02f4f43_init.py)（2026-06-19）创建 6 张表

---

### 3.6 代码规范（防屎山）

来自 [AGENTS.md](file:///e:/Desktop/agent/AGENTS.md)：

| 维度 | 规则 |
|------|------|
| 函数行数 | ≤ 10 行，过长必须拆 |
| 圈复杂度 | ≤ 10 |
| 函数参数 | ≤ 3，超过封装 dataclass/dict |
| 文件行数 | ≤ 300，超过拆模块/组件 |
| 魔法数字 | 禁止，必须用命名常量 |
| 错误处理 | 所有外部调用必须 try/except，不静默吞异常 |
| 类型注解 | Python 函数必须有，TypeScript strict 模式 |

---

## 四、核心数据流（关键链路图）

### 4.1 聊天完整链路（一张图看懂）

```
┌──────────────────────────────────────────────────────────────┐
│  ① 用户输入 "STM32 PA0 怎么配"                                │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  ② 前端 useChatStore.sendMessage()                            │
│     → apiSSE("/api/chat", {messages, model, kb_ids, ...})     │
│     → streamingSessionId 捕获发起会话（防切会话 race）         │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  ③ 后端 chat_sse() 接收                                       │
│     → _rewrite_query_for_rag() LLM 改写查询（24h LRU）        │
│       → SSE event: {type:"thinking", step:"rag", ...}         │
│     → _run_rag_retrieval() 搜手册                             │
│       → BM25（jieba+硬件术语字典）                             │
│       → 向量检索（ChromaDB cosine）                            │
│       → RRF 融合（constant_k=60）                              │
│       → BM25-only ×0.85 惩罚                                  │
│       → Reranker（bge-reranker-base cross-encoder）           │
│       → 返回 top_k=5 段相关手册                                │
│       → SSE event: {type:"source", docs:[...]}                │
│     → _build_system_prompt() 拼 [srcN] 标注                    │
│     → LLMClient.chat_stream() 调 AI                           │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  ④ AI 流式吐字                                                │
│     → SSE event: {type:"token", content:"P"}                  │
│     → SSE event: {type:"token", content:"A"}                  │
│     → SSE event: {type:"reasoning", content:"..."}  (推理模型) │
│     → SSE event: {type:"tool", name:"...", args:{...}}        │
│     → SSE event: {type:"done", usage:{prompt,completion,...}} │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  ⑤ 前端 onToken/onSource/onDone 回调                          │
│     → onToken: 实时更新 streamingContent（字一个个蹦出）       │
│     → onSource: 更新 streamingSources                          │
│     → onDone: 固化成 Message（含 activity）                    │
│       → 写 sessionMessages + localStorage（按 session 分片）   │
│       → persistLastTurn(sessionId) POST 后端                  │
│         → 用 persistedMsgIds Set 防重复 POST                  │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 知识库入库链路

```
用户选文件 → UploadChunkMethodDialog 选 chunk_method
  │
  ▼
useKnowledgeStore.uploadDoc()
  → apiPost("kbUpload", FormData)
  → kb_routes.py:kb_upload()
    ├─► magic bytes 校验
    ├─► UnifiedPdfParser / DocxParser / ... 解析
    ├─► HybridChunker / AgentChunker / MultimodalChunker 切分
    │     ├─► protect_structures() 保护表格/代码块/寄存器字段
    │     ├─► 递归字符分割
    │     └─► tiny chunk 合并（阈值 100 字符）
    ├─► HardwareVectorStore.add()
    │     ├─► embedding API（diskcache 缓存，sha256 key）
    │     ├─► ChromaDB 5000 一批
    │     └─► 维度严格校验
    ├─► BM25 索引即时重建
    └─► 写 KnowledgeDoc 记录

前端轮询索引状态（2s 间隔，120s 超时）
```

### 4.3 引脚审查链路

```
用户在 SafetyPane 贴代码
  │
  ▼
正则提取 #define / pinMode / digitalRead / digitalWrite
  → 构造 pin_assignments: {pin_name: {function, config}}
  → apiPost("auditPins", {chip, pin_assignments})
  → hardware_routes.py:audit_pins()
    → audit.audit_pins_core()
      ├─► gpio.resolve_gpio() 解析引脚名（GPIO5/纯数字/宏定义）
      ├─► 检查 GPIO 重复使用 → critical
      ├─► 检查 Strapping 引脚（esp32/esp32-s3）→ warning
      └─► 返回 {safe, conflicts, warnings, pin_map}
  → 前端显示引脚表 + 冲突详情
```

### 4.4 Agent 链路（LangGraph ReAct，v3）

```
POST /api/chat (chat_routes.py L202+)
  │
  ▼
_should_use_agent() 判断是否走 Agent
  │  是
  ▼
_run_agent_stream()
  → stream_agent_to_sse() (sse_adapter.py)
    → agent.astream(stream_mode=["messages","updates"])
    → chunk → SSE 事件:
        ├─ text            (LLM 文本增量)
        ├─ tool_call       (工具调用开始)
        ├─ tool_result     (工具返回)
        └─ tool_confirm_required (HITL 待确认)
  │
  ▼
流结束后:
  ├─ _check_loop_and_hint() (loop_detector.py) → soft hint SSE
  └─ handle_auto_resume() (hitl_handler.py) → HITL 中断处理

Agent 工厂:
  create_hardware_agent() (agent_factory.py)
    → create_agent(model, tools, system_prompt=, middleware=(TokenCounterMiddleware,),
        context_schema=ToolContext, checkpointer=SqliteSaver/InMemorySaver 单例,
        interrupt_before=["tools"] when HITL)
    [Stage 3 迁移: create_react_agent → create_agent, prompt= → system_prompt=]

9 个工具:
  v1: search_docs / audit_pins / wiring / web_search / generate_code
  v2: read_file / write_file / edit_file / run_command (本地工具)

权限门控 4 步 (permission_gate.py):
  path_guard.validate_path() → mode dispatch → risk_classifier.classify_risk() → 决策(allow/ask/deny)

上下文保护 (v3-T5, context_guard.py):
  ContextVar 累积 token 计数 + 墙钟超时
  → ContextLimitError / AgentTimeoutError → SSE error → 回退 LLM 流

死循环检测 (v3-T4, loop_detector.py):
  detect_repeat (同工具+同参数 5 窗口内 ≥2 次)
  + detect_no_progress (3 次相同输出 hash)
  → soft hint SSE
```

---

## 五、状态管理（前端 10 个 Store）

```
┌──────────────────────────────────────────────────────────────┐
│ useChatStore     ← 最重要！消息/流式/会话切换                  │
│   sendMessage / stopStreaming / retryMessage /                │
│   editAndResend / branchThread / setActiveSession /           │
│   fetchMessages / persistLastTurn / pushCodeToWorkbench       │
│   MAX_MESSAGES=200 滚动窗口 / persistedMsgIds 防重复 POST     │
│   needsApiKey: 401 时置 true，引导用户配置 API Key              │
├──────────────────────────────────────────────────────────────┤
│ useSessionStore  ← 会话列表 CRUD                              │
│   initSessions / newSession / deleteSession / pinSession /    │
│   renameSession / moveSessionToProject / createProject        │
│   MAX_PINNED=5 / 项目分组 / 按时间分组                        │
├──────────────────────────────────────────────────────────────┤
│ useSettingsStore ← 模型/温度/Key/Provider/RAG 默认             │
│   setActiveProvider / setProviderKey（加密存后端）/            │
│   setModel / setVisionModel / setBaseUrl /                    │
│   addMcpServer / toggleSkill / fetchMCPServers /              │
│   updateSetting / DEFAULT_BASE_URLS（5 个 provider）          │
├──────────────────────────────────────────────────────────────┤
│ useKnowledgeStore← 知识库管理                                 │
│   fetchCollections / createCollection / deleteCollection /    │
│   toggleCollection / renameCollection / updateKbConfig /      │
│   uploadDoc / deleteDoc / fetchDocChunks /                    │
│   exportCollection / importCollection / fetchEmbeddingModels  │
├──────────────────────────────────────────────────────────────┤
│ useAppStore      ← UI 状态（面板/导航/主题/字号/预览 tab/资源管理器）│
│   setThemeMode / setLang / setActiveNav /                     │
│   setLeftPanelWidth / setRightPanelWidth /                    │
│   setWbTab / setFlashCode / setQuotedMsg /                    │
│   addPreviewTab / removePreviewTab / setSearchOpen /          │
│   openFile / closeFile / pinFile / setExplorerRootPath /      │
│   explorerOpen / explorerWidth / recentFolders 持久化到 localStorage │
│   rightPanelOpen 持久化到 localStorage；无 API Key/空会话默认收起 │
├──────────────────────────────────────────────────────────────┤
│ useBookmarkStore ← 书签（CRUD + 文件夹分组）                   │
│   toggleBookmark / addBookmarkFolder /                        │
│   moveBookmarkToFolder / renameBookmarkFolder                 │
├──────────────────────────────────────────────────────────────┤
│ useSerialStore   ← 串口监视器                                 │
│   setConnected / setPort / setBaudRate / addLog（5000 行）/   │
│   toggleDtr / toggleRts / setFilter                           │
├──────────────────────────────────────────────────────────────┤
│ useLogStore      ← 日志（buffer 2000，5 级过滤）              │
│   log / clear / setFilter / getFiltered                       │
├──────────────────────────────────────────────────────────────┤
│ useWiringStore   ← 接线器件/连线可编辑状态                    │
│   addComponent / addWire / removeComponent / removeWire /     │
│   updateComponent / updateWire / clearAll                     │
├──────────────────────────────────────────────────────────────┤
│ useWorkbenchBridge ← SSE 工具事件路由到 Pane                  │
│   dispatchToolEvent / bindPane / unbindPane /                 │
│   handleToolCall / handleToolResult                           │
└──────────────────────────────────────────────────────────────┘
```

**API 客户端**（[client.ts](file:///e:/Desktop/agent/frontend/src/api/client.ts)）：
- `apiGet / apiPost / apiPut / apiDelete / apiPatch` — 普通 REST，8s 超时
- `apiSSE` — 流式（120s 连接超时 + 5 分钟空闲超时）
- `apiWS` — WebSocket（串口用，5173 端口时自动连 58080）
- `getAuthHeaders()` — 注入 X-API-Key / Authorization: Bearer / X-Model / X-Provider / X-Base-URL
- `unwrapResponse<T>()` — 解包 `{success,data}` 或抛 ApiError

---

## 六、数据库表清单（8 张表）

> 位置：[backend/app/db/models.py](file:///e:/Desktop/agent/backend/app/db/models.py)
> 初始化：[database.py](file:///e:/Desktop/agent/backend/app/db/database.py) `init_db()` 建表 + FTS5 虚拟表 + 3 个触发器同步

| 表名 | 字段 | 用途 |
|------|------|------|
| `sessions` | id / title / created_at / updated_at / branch_from_session_id / branch_from_message_id | 会话（含分支溯源） |
| `messages` | id / session_id / role / content / sources(JSON) / tool_calls(JSON) / activity(JSON) / created_at | 消息（activity 存思考卡/工具步骤） |
| `knowledge_bases` | id / name / enabled / is_builtin / embedding_model / embedding_base_url / embedding_api_key(加密) / agent_chunker_config(JSON) / chunk_method / small_chunk_size / context_window | 知识库配置 |
| `knowledge_docs` | id / kb_id / doc_id / title / status(indexing/indexed/error) / coverage_json / chunk_count | 文档入库记录 |
| `bookmark_folders` + `bookmarks` | folder(id/name) / bookmark(id/folder_id/session_id/message_id/title/content) | 书签（folder 删除时 bookmark 移到 default） |
| `settings` | key(白名单) / value | KV 配置存储 |
| `feedback` | id / session_id / message_id / rating(1=👍/-1=👎) | 消息反馈 |
| `token_usage` | id / session_id / model / provider / prompt_tokens / completion_tokens / total_tokens / created_at | Token 用量（按天聚合） |
| `tool_audit` | id / timestamp / session_id / tool_name / args_summary / decision / decision_source / risk_level / exit_code / duration_ms / error | Agent 工具调用审计（独立 SQLite，由 [audit_logger.py](file:///e:/Desktop/agent/backend/src/agent/audit_logger.py) 管理） |

**FTS5 全文搜索**：`messages_fts` 虚拟表 + 3 个触发器（INSERT/DELETE/UPDATE 同步）

**设置白名单**：activeProvider / model / visionModel / imageModel / temperature / topK / maxTokens / systemPrompt / longTermMemory / chatFontSize / themeMode / lang / permissionMode

**tool_audit 索引**：idx_tool_audit_ts(timestamp) / idx_tool_audit_session(session_id) / idx_tool_audit_tool(tool_name)；保留 30 天，启动时 `cleanup_old_logs()` 自动清理

---

## 七、API 路由总表（11 个模块 60+ 端点）

### 1. 聊天（[chat_routes.py](file:///e:/Desktop/agent/backend/app/api/chat_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/chat` | SSE 流式聊天（含 RAG/附件/多模态） |
| POST | `/api/models` | 拉取模型列表 |
| GET | `/api/token-usage/stats` | Token 用量统计 |

### 2. 知识库（[kb_routes.py](file:///e:/Desktop/agent/backend/app/api/kb_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/kb/upload` | 上传文档（异步索引） |
| GET | `/api/kb/list` | 文档列表 |
| POST | `/api/kb/delete` | 删除文档 |
| GET | `/api/kb/collections` | KB 列表 |
| POST | `/api/kb/collections` | 创建 KB |
| GET | `/api/kb/collections/{kb_id}` | KB 详情 |
| DELETE | `/api/kb/collections/{kb_id}` | 删除 KB |
| PATCH | `/api/kb/collections/{kb_id}/toggle` | 开关 KB |
| PATCH | `/api/kb/collections/{kb_id}/rename` | 重命名 |
| PATCH | `/api/kb/collections/{kb_id}/config` | 更新配置 |
| GET | `/api/kb/documents/{doc_id}/chunks` | 文档切片列表 |
| GET | `/api/kb/chunks/{small_chunk_id}` | 单个 chunk |
| POST | `/api/kb/embedding-models` | 代理 embedding 模型列表 |
| POST | `/api/kb/{kb_id}/export` | 导出 KB |
| POST | `/api/kb/{kb_id}/import` | 导入 KB |

### 3. 硬件（[hardware_routes.py](file:///e:/Desktop/agent/backend/app/api/hardware_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/devices` | 扫描串口 |
| POST | `/api/diagnose` | 代码诊断（5 类） |
| POST | `/api/wiring` | 生成接线 SVG + BOM |
| POST | `/api/audit_pins` | 引脚冲突审计 |

### 4. 编译烧录（[build_routes.py](file:///e:/Desktop/agent/backend/app/api/build_routes.py) + [pio_runner.py](file:///e:/Desktop/agent/backend/src/hardware/pio_runner.py)）— PlatformIO 真实
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/build` | SSE 真实编译（PlatformIO `pio run`） |
| POST | `/api/upload` | SSE 真实烧录（`pio run --target upload`） |

### 5. 工具+串口（[tool_routes.py](file:///e:/Desktop/agent/backend/app/api/tool_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/tool` | dispatch 工具 |
| GET | `/api/tools` | 工具列表 |
| WS | `/api/monitor/{port}` | 串口 WebSocket 桥接 |

### 6. 认证（[auth.py](file:///e:/Desktop/agent/backend/app/api/auth.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/auth/store-key` | 存 Key 返回 session_token（30 天） |
| GET | `/api/auth/keys` | 列 provider（不含明文） |
| DELETE | `/api/auth/keys/{provider}` | 删除 |

### 7. CRUD（[crud.py](file:///e:/Desktop/agent/backend/app/api/crud.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| GET/POST | `/api/sessions` | 列/建会话 |
| GET/PUT/DELETE | `/api/sessions/{id}` | 详情/改/删 |
| GET/POST/DELETE | `/api/sessions/{id}/messages` | 消息列表/添加/截断 |
| GET/PUT | `/api/settings` | 设置读写 |

### 8. MCP（[mcp_routes.py](file:///e:/Desktop/agent/backend/app/api/mcp_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST/GET | `/api/mcp/servers` | 添加/列出 |
| POST | `/api/mcp/servers/{id}/start` | 启动 |
| POST | `/api/mcp/servers/{id}/stop` | 停止 |
| GET | `/api/mcp/servers/{id}/tools` | 列工具 |
| DELETE | `/api/mcp/servers/{id}` | 删除 |

### 9. 文件浏览器（[explorer_routes.py](file:///e:/Desktop/agent/backend/app/api/explorer_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/explorer/browse` | 浏览目录（文件夹选择器，仅列目录名） |
| POST | `/api/explorer/open` | 授权根目录并返回目录树（asyncio.to_thread 包装） |
| GET | `/api/explorer/read` | 读文件（文本内容或二进制元信息） |
| GET | `/api/explorer/diff` | 返回 Git HEAD 基线 + 当前内容（asyncio.to_thread 包装） |
| POST | `/api/explorer/write` | 写文本文件 |
| POST | `/api/explorer/create` | 新建文件/目录（已存在 409） |
| POST | `/api/explorer/rename` | 重命名（目标存在 409） |
| POST | `/api/explorer/delete` | 删除文件/目录 |
| POST | `/api/explorer/move` | 移动文件/目录（目标存在 409） |
| POST | `/api/explorer/copy` | 复制文件/目录（目标存在 409） |
| POST | `/api/explorer/search` | 内容搜索（大小写不敏感，30s 超时返回 504） |
| GET | `/api/explorer/watch` | SSE 文件系统事件订阅 |

### 10. 反馈（[feedback_routes.py](file:///e:/Desktop/agent/backend/app/api/feedback_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/feedback` | 写反馈（1=👍/-1=👎） |
| GET | `/api/feedback/{session_id}` | 按会话查 |

### 11. 搜索（[search_routes.py](file:///e:/Desktop/agent/backend/app/api/search_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/search` | FTS5 全文搜索（降级 LIKE） |

### 11. Agent 沙箱（[agent_sandbox_routes.py](file:///e:/Desktop/agent/backend/app/api/agent_sandbox_routes.py)）
| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/agent-sandbox/policy` | 查询权限模式 |
| POST | `/api/agent-sandbox/policy` | 切换权限模式 |
| GET | `/api/agent-sandbox/audit` | 查询审计日志 |
| POST | `/api/agent-sandbox/resume` | HITL 用户决策恢复（SSE） |

---

## 八、前端组件清单（40 个组件）

### layout/（5 个）
- [AppRoot.tsx](file:///e:/Desktop/agent/frontend/src/components/layout/AppRoot.tsx) — 总布局，组合 IconNav + LeftPanel + MainArea + RightPanel + ExplorerPanel + 浮层
- [IconNav.tsx](file:///e:/Desktop/agent/frontend/src/components/layout/IconNav.tsx) — 左侧图标栏
- [LeftPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/layout/LeftPanel.tsx) — 左面板容器
- [MainArea.tsx](file:///e:/Desktop/agent/frontend/src/components/layout/MainArea.tsx) — 主区域模板
- [RightPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/layout/RightPanel.tsx) — 右面板（workbench/content 两模式）

### explorer/（1 个）
- [ExplorerPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/explorer/ExplorerPanel.tsx) — 第四栏资源管理器容器（文件树/编辑器占位）

### topbar/（1 个）
- [TopBar.tsx](file:///e:/Desktop/agent/frontend/src/components/topbar/TopBar.tsx) — 顶栏（标题+来源数+快照+来源面板+汉堡）

### chat/（13 个）
- [ChatArea.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ChatArea.tsx) — 聊天主区（拆分后 426 行，原 892 行；拆出 7 个子组件）
- [BranchTree.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/BranchTree.tsx) — 分支对话树
- [ErrorBlock.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ErrorBlock.tsx) — 错误卡片（7 种错误码）
- [ShortcutHelp.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ShortcutHelp.tsx) — 快捷键帮助
- [ConfirmDialog.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ConfirmDialog.tsx) — HITL 工具确认弹窗（4 按钮：允许本次/永久允许/拒绝/拒绝并停止）
- [PolicyBar.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/PolicyBar.tsx) — 权限策略切换（3 按钮：bypassPermissions=default/acceptEdits）
- [ImageLightbox.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/ImageLightbox.tsx) — 图片灯箱（ChatArea 拆分，42 行）
- [UserMessageContent.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/UserMessageContent.tsx) — 用户消息内容渲染（ChatArea 拆分，33 行）
- [AssistantMessageContent.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/AssistantMessageContent.tsx) — 助手消息内容渲染（ChatArea 拆分，73 行）
- [UserMessageRow.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/UserMessageRow.tsx) — 用户消息行（ChatArea 拆分，59 行）
- [AssistantMessageRow.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/AssistantMessageRow.tsx) — 助手消息行（ChatArea 拆分，215 行）
- [LoadingState.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/LoadingState.tsx) — 加载态（ChatArea 拆分，15 行）
- [EmptyState.tsx](file:///e:/Desktop/agent/frontend/src/components/chat/EmptyState.tsx) — 空态（ChatArea 拆分，42 行）

### input/（1 个）
- [InputBar.tsx](file:///e:/Desktop/agent/frontend/src/components/input/InputBar.tsx) — 输入栏（472 行，附件/模型选择/模板/引用/Markdown 预览）

### knowledge/（3 个）
- [KnowledgePanel.tsx](file:///e:/Desktop/agent/frontend/src/components/knowledge/KnowledgePanel.tsx) — 知识库主面板
- [KbCollectionManager.tsx](file:///e:/Desktop/agent/frontend/src/components/knowledge/KbCollectionManager.tsx) — KB 管理模态框（1064 行，最大组件）
- [UploadChunkMethodDialog.tsx](file:///e:/Desktop/agent/frontend/src/components/knowledge/UploadChunkMethodDialog.tsx) — 上传选 chunk method

### session/（1 个）
- [SessionPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/session/SessionPanel.tsx) — 会话列表（项目 chips + 时间分组 + 右键菜单）

### settings/（4 个）
- [SettingsPage.tsx](file:///e:/Desktop/agent/frontend/src/components/settings/SettingsPage.tsx) — 设置页（9 个 tab）
- [RagSettingsPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/settings/RagSettingsPanel.tsx) — RAG 全局默认配置
- [TokenUsagePanel.tsx](file:///e:/Desktop/agent/frontend/src/components/settings/TokenUsagePanel.tsx) — Token 用量图表
- [AuditLogPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/settings/AuditLogPanel.tsx) — 审计日志面板（按 session_id/tool_name/decision/risk_level 过滤）

### shared/（9 个）
- [MarkdownRenderer.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/MarkdownRenderer.tsx) — Markdown 渲染（[srcN] 转链接 + 代码块高亮）
- [ContextMenu.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/ContextMenu.tsx) — 右键菜单（多级子菜单）
- [ErrorBoundary.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/ErrorBoundary.tsx) — 错误边界
- [SearchModal.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/SearchModal.tsx) — 搜索（local/global 两 tab）
- [HamburgerMenu.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/HamburgerMenu.tsx) — 汉堡菜单
- [Modal.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/Modal.tsx) — 通用模态框
- [StatsPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/StatsPanel.tsx) — 聊天统计浮层
- [SnapshotPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/SnapshotPanel.tsx) — 对话快照（保存/恢复/对比）
- [TemplatePanel.tsx](file:///e:/Desktop/agent/frontend/src/components/shared/TemplatePanel.tsx) — 模板面板

### workbench/（1 个）
- [WorkbenchPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/workbench/WorkbenchPanel.tsx) — 工作台（1153 行，5 个 Pane：Serial/Flash/Preview/Wiring/Safety）

### bookmarks/（1 个）
- [BookmarkPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/bookmarks/BookmarkPanel.tsx) — 收藏夹面板

### Hooks（6 个）
- [useChat](file:///e:/Desktop/agent/frontend/src/hooks/useChat.ts) — useChatStore 包装
- [useKeyboard](file:///e:/Desktop/agent/frontend/src/hooks/useKeyboard.ts) — 全局快捷键（8 个）
- [usePanelResize](file:///e:/Desktop/agent/frontend/src/hooks/usePanelResize.ts) — 面板拖拽（px/pct）
- [useSSE](file:///e:/Desktop/agent/frontend/src/hooks/useSSE.ts) — SSE 封装
- [useTheme](file:///e:/Desktop/agent/frontend/src/hooks/useTheme.ts) — 主题应用
- [useWebSocket](file:///e:/Desktop/agent/frontend/src/hooks/useWebSocket.ts) — WS 封装

---

## 九、关键参数与限制汇总

### RAG 检索
| 参数 | 默认 | 位置 |
|------|------|------|
| `top_k` 单 KB | 5 | [kb_manager.py:704](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py#L704) |
| `top_k` 跨 KB | 3 | [kb_manager.py:790](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py#L790) |
| `score_threshold` | 0.0（UI 可调） | [kb_manager.py:704](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py#L704) |
| HNSW `ef_search` | 200 | [settings.py:82](file:///e:/Desktop/agent/backend/src/config/settings.py#L82) |
| BM25 `k1`/`b` | 1.5/0.75 | [settings.py:78-79](file:///e:/Desktop/agent/backend/src/config/settings.py#L78) |
| RRF `constant_k` | 60 | [kb_manager.py:190](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py#L190) |
| BM25-only 惩罚 | ×0.85 | [kb_manager.py:282](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py#L282) |
| ChromaDB 批次 | 5000 | [vector_store.py:405](file:///e:/Desktop/agent/backend/src/rag/vector_store.py#L405) |

### Chunking
| 参数 | 默认 | 位置 |
|------|------|------|
| HybridChunker chunk_size/overlap/small | 1000/200/800 | [hybrid_chunker.py:56-66](file:///e:/Desktop/agent/backend/src/rag/chunking/hybrid_chunker.py#L56) |
| AgentChunker num_rounds | 3（大文档降 1） | [agent_chunker.py:206](file:///e:/Desktop/agent/backend/src/rag/chunking/agent_chunker.py#L206) |
| AgentChunker max_batch_chars | 80000 | [agent_chunker.py:207](file:///e:/Desktop/agent/backend/src/rag/chunking/agent_chunker.py#L207) |
| AgentChunker max_chunks | 500 | [agent_chunker.py:211](file:///e:/Desktop/agent/backend/src/rag/chunking/agent_chunker.py#L211) |
| MultimodalChunker batch_size | 5 页 | [multimodal_chunker.py:273](file:///e:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L273) |
| MultimodalChunker dpi | 200（高清）/100（低清） | [multimodal_chunker.py:274](file:///e:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L274) |
| MultimodalChunker vision_concurrency | 4 | [multimodal_chunker.py:280](file:///e:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L280) |

### LLM
| 参数 | 默认 | 位置 |
|------|------|------|
| chat timeout | 60s | [client.py:256](file:///e:/Desktop/agent/backend/src/llm/client.py#L256) |
| chat_stream timeout | 300s | [client.py:324](file:///e:/Desktop/agent/backend/src/llm/client.py#L324) |
| max_retries | 3（指数退避 min(2^n, 8)s） | [client.py:67](file:///e:/Desktop/agent/backend/src/llm/client.py#L67) |
| 查询改写超时 | 8s（降级原始 query） | [chat_helpers.py:150-268](file:///e:/Desktop/agent/backend/app/api/chat_helpers.py#L150) |
| 查询改写缓存 | 24h LRU | 同上 |

### Agent（LangGraph ReAct v3）
| 参数 | 默认 | 位置 |
|------|------|------|
| `MAX_RECURSION` Agent 轮次上限 | 41 | [prompts.py](file:///e:/Desktop/agent/backend/src/agent/prompts.py) |
| `MAX_TOKEN_RATIO` 累积 token 占 context_window 比例上限 | 0.8 | [prompts.py](file:///e:/Desktop/agent/backend/src/agent/prompts.py) |
| `SINGLE_REQ_TIMEOUT_S` 单次 Agent 请求墙钟超时 | 120 | [prompts.py](file:///e:/Desktop/agent/backend/src/agent/prompts.py) |
| `RESULT_HISTORY_LIMIT` no_progress 检测窗口 | 5 | [loop_detector.py](file:///e:/Desktop/agent/backend/src/agent/loop_detector.py) |
| `SOFT_CALL_HINT_THRESHOLD` 软提示阈值 | 10 | [loop_detector.py](file:///e:/Desktop/agent/backend/src/agent/loop_detector.py) |
| `DEFAULT_TIMEOUT_MS` run_command 默认超时 | 30000 | [tools/run_command.py](file:///e:/Desktop/agent/backend/src/agent/tools/run_command.py) |
| `RETENTION_DAYS` 审计日志保留天数 | 30 | [audit_logger.py](file:///e:/Desktop/agent/backend/src/agent/audit_logger.py) |

### 前端
| 参数 | 值 |
|------|------|
| MAX_MESSAGES 滚动窗口 | 200 |
| 串口日志保留 | 5000 行 |
| 日志 buffer | 2000 条 |
| MAX_PINNED 置顶 | 5 |
| 附件限制 | 3 个，10MB |
| apiSSE 连接超时 | 120s |
| apiSSE 空闲超时 | 5 分钟 |
| fetchWithTimeout | 8s |
| 全局搜索 debounce | 300ms |

---

## 十、文件目录速查

```
e:\Desktop\agent\
├── backend/
│   ├── app/
│   │   ├── api/              11 个路由模块
│   │   │   ├── chat_routes.py        SSE 流式聊天
│   │   │   ├── chat_helpers.py       聊天辅助（改写/RAG/system prompt）
│   │   │   ├── kb_routes.py          知识库管理（1300+ 行）
│   │   │   ├── hardware_routes.py    硬件工具
│   │   │   ├── build_routes.py       编译烧录（PlatformIO 真实，调 pio_runner）
│   │   │   ├── tool_routes.py        工具调度 + 串口 WS
│   │   │   ├── mcp_routes.py         MCP Server 管理
│   │   │   ├── auth.py               认证（Fernet 加密）
│   │   │   ├── crud.py               会话/消息/设置 CRUD
│   │   │   ├── feedback_routes.py    消息反馈
│   │   │   ├── search_routes.py      FTS5 全文搜索
│   │   │   ├── agent_sandbox_routes.py Agent 沙箱权限/审计/HITL resume
│   │   │   ├── attachments.py        附件解析
│   │   │   ├── common.py             共享工具（DB/VectorStore/LLMClient 工厂）
│   │   │   ├── dependencies.py       鉴权依赖
│   │   │   ├── errors.py             错误脱敏
│   │   │   ├── locks.py              串口锁 + 接线图锁
│   │   │   └── sse.py                SSE 事件构造
│   │   ├── db/               数据库
│   │   │   ├── models.py             7 张表
│   │   │   └── database.py           init_db + FTS5 + 触发器
│   │   ├── hardware/
│   │   │   ├── audit.py              引脚冲突审计
│   │   │   └── gpio.py               Strapping 表 + GPIO 解析
│   │   ├── main.py           FastAPI 应用工厂 create_app()
│   │   └── config.py         Day 1 学习残留（不用）
│   ├── src/
│   │   ├── rag/             RAG 子系统
│   │   │   ├── kb_manager.py         BM25 + RRF + Reranker + 多 KB 管理
│   │   │   ├── vector_store.py       ChromaDB 封装 + embedding 缓存
│   │   │   ├── search.py             统一搜索入口
│   │   │   ├── reranker.py           bge-reranker-base
│   │   │   ├── document_loader.py    PDF 下载器（10 篇初始 datasheet）
│   │   │   ├── document_processor.py UnifiedPdfParser + TranslationPipeline
│   │   │   ├── file_parsers.py       6 种解析器（XLSX/DOCX/CSV/JSON/HTML/OCR）
│   │   │   └── chunking/             分块器
│   │   │       ├── base.py           共享基础设施（page marker/结构保护/JSON fallback）
│   │   │       ├── hybrid_chunker.py 规则分块
│   │   │       ├── agent_chunker.py  LLM 分块
│   │   │       ├── multimodal_chunker.py 视觉 LLM 分块
│   │   │       └── factory.py        工厂
│   │   ├── llm/
│   │   │   ├── client.py             LLMClient（chat/chat_stream/list_models）
│   │   │   ├── model_registry.py     12 个模型注册表
│   │   │   └── multimodal.py         多模态工具
│   │   ├── hardware/
│   │   │   └── svg_generator.py      接线 SVG 生成
│   │   ├── mcp/
│   │   │   ├── client.py             JSON-RPC stdio 客户端
│   │   │   └── manager.py            多 Server 管理
│   │   ├── agent/            LangGraph ReAct Agent（v3）
│   │   │   ├── agent_factory.py      create_hardware_agent + MemorySaver 单例
│   │   │   ├── sse_adapter.py        stream_agent_to_sse（SSE 事件转换）
│   │   │   ├── context_guard.py      v3-T5 累积 token + 超时保护（ContextVar）
│   │   │   ├── loop_detector.py      v3-T4 防死循环（repeat/no_progress）
│   │   │   ├── hitl_handler.py       HITL 中断处理 + resume
│   │   │   ├── permission_gate.py    4 步权限门控
│   │   │   ├── path_guard.py         路径白名单/黑名单
│   │   │   ├── risk_classifier.py    HIGH/MEDIUM/LOW 关键字分类
│   │   │   ├── audit_logger.py       SQLite 审计日志
│   │   │   ├── exceptions.py         ToolContext + Permission 权限 + Context/Timeout 异常
│   │   │   ├── prompts.py            SYSTEM_PROMPT + MAX_RECURSION=41 + MAX_TOKEN_RATIO=0.8
│   │   │   ├── tools/                9 个 BaseTool
│   │   │   │   ├── wrappers.py       search_docs/audit_pins/wiring 封装
│   │   │   │   ├── web_search.py     web_search
│   │   │   │   ├── generate_code.py  generate_code
│   │   │   │   ├── file_ops.py       read_file/write_file/edit_file
│   │   │   │   └── run_command.py    run_command
│   │   │   └── tool_router.py        工具注册 + 调度（旧 v1/v2 入口）
│   │   ├── config/
│   │   │   └── settings.py           18 项配置 + 热重载
│   │   └── compat/
│   │       └── torchcodec_shim.py    torchcodec 兼容
│   ├── alembic/             数据库迁移
│   ├── tests/               测试
│   │   ├── rag_eval/                Golden Eval 双轨评测
│   │   ├── conftest.py              autouse fixture 绕过鉴权
│   │   └── test_*.py                16 个测试文件
│   ├── main.py              CLI/Web 双模式入口
│   ├── pytest.ini
│   └── requirements.txt     47 个依赖
├── frontend/
│   ├── src/
│   │   ├── api/             API 客户端
│   │   │   ├── client.ts            apiGet/Post/Put/Delete/Patch/SSE/WS
│   │   │   ├── endpoints.ts         路由常量
│   │   │   └── mock.ts              Mock 数据
│   │   ├── components/      29 个组件（9 个子目录）
│   │   ├── stores/          10 个 Zustand store
│   │   ├── hooks/           6 个 hooks
│   │   ├── utils/           6 个工具文件
│   │   ├── types/           6 个类型文件
│   │   ├── i18n/            zh/en 两种语言
│   │   ├── config/          providers.ts
│   │   ├── constants/       chunkMethodInfo.ts
│   │   ├── styles/          globals.css
│   │   ├── App.tsx          useTheme + useKeyboard
│   │   └── main.tsx         ErrorBoundary + QueryClientProvider 包裹
│   ├── vite.config.ts       代理 /api → 127.0.0.1:58080 + SSE 禁缓冲
│   └── package.json         React 19 + Vite 6 + Vitest 2 + ESLint 9
├── scripts/                 开发辅助脚本
│   ├── dev.ps1              一键启动（⚠️ 端口坑）
│   ├── build_builtin_kb.py  构建内置 KB
│   ├── list_models.py       列模型
│   └── audit/               CH340G 审计工作目录
├── data/
│   ├── test_docs/           RAG 评测专用文档（7 个 MD）
│   └── pdfs/                PDF 资料
├── docs/                    文档（见 AGENTS.md Docs Map）
├── .env.example             环境变量模板
├── .gitignore               173 行，白名单保留 builtin_kb
├── AGENTS.md                项目规则
├── langgraph.json          LangGraph 配置（graphs.hardware_agent → create_hardware_agent，env: backend/.env）
└── fix-git-path.cmd/ps1     Git PATH 修复
```

---

## 十一、已修复的 bug 在哪条链上

| 问题 | 断点位置 | 修复日期 |
|------|---------|---------|
| 看不到历史对话 | ⑤ 的 onDone 没调后端存消息 + ③ 没调后端读消息 | 2026-06-29 |
| retry 后重复 | useChatStore.retryMessage 只截前端不截后端 | 2026-06-29 |
| 思考卡片消失 | ⑤ 持久化时漏存 activity 字段 | 2026-06-29 |
| 本地消息不迁移 | fetchMessages 没有懒迁移逻辑 | 2026-06-29 |
| RAG 相关性全 100% | BM25 分数归一化 + BM25-only ×0.85 惩罚 + RRF 显示分数取平均 | 2026-06-29 |
| MultimodalChunker 页码全为 1 | RecursiveCharacterTextSplitter 切断 `<!-- PAGE:N -->` 标记 | 2026-06-29 |
| HybridChunker 页码回退为 (1,1) | `_get_section_pages` 无 marker 时默认 (1,1)，`_split_plain_text/markdown` 未继承最近页码 | 2026-06-29 |
| 引脚冲突漏检 INPUT/OUTPUT | audit.py 只查 GPIO 重复，没查 INPUT/OUTPUT 冲突 | 2026-06-29 |
| generate_wiring_svg 缺 title 参数 | WiringRequest model 没对齐 | 2026-06-29 |
| WiringComponent 字段不匹配 | model 与 svg_generator/test 输入不对齐 | 2026-06-29 |
| Strapping 检测回归 | 修复引脚冲突时引入的回归 | 2026-06-29 |
| Agent 多轮 search_docs source 编号冲突 | 每次 search_docs 独立编号，ToolContext 未维护全局计数器 | 2026-07-02 |
| Agent 寒暄文本污染 thinking 卡片 | sse_adapter 把工具调用前后引导文本 flush 为 thinking | 2026-07-02 |
| Agent 流式输出不实时 | 文本全量缓冲后一次性下发 | 2026-07-02 |
| 循环恢复"换方法"无效 | _restart_after_loop 用 SystemMessage，Agent 忽略 | 2026-07-02 |
| search_docs 复杂查询被 60s 截断 | 默认超时过短，未匹配 CPU reranker 实际耗时 | 2026-07-02 |
| ToolAudit Index 列名错误 | idx_tool_audit 索引引用 created_at，实际列为 timestamp | 2026-07-01 |
| main.py logger 变量名错误 | main.py 用了未定义的 logger，应为 _LOGGER | 2026-07-01 |
| InMemorySaver 导入路径迁移 | langgraph 1.2.4 top-level import 抛 ImportError，改用 langgraph.checkpoint.memory 子模块路径 | 2026-07-06 |
| HITL 路线 B 保留 | interrupt_before + Command(resume=...) 路线保留，interrupt() 双重中断点冲突不引入 | 2026-07-06 |
| ReasoningChatOpenAI override 验证 | 1.x 下 reasoning_effort override 行为验证 + 测试覆盖 | 2026-07-06 |
| MAX_RECURSION 注释数值不一致 | 原 200 注释 "2*50+1" 实际 4*50+1，统一改为 101 | 2026-07-06 |
| Chroma 1.x _collection 私有属性 | vector_store 三处 _collection 直接访问改 getattr 容错 + hasattr 守卫 | 2026-07-06 |
| sqlalchemy __import__ 反模式 | kb_manager 用 __import__("sqlalchemy").sum(...) 改为 from sqlalchemy import func + func.sum(...) | 2026-07-06 |
| sse_adapter 漏发 risk_level | sse_adapter 补发 risk_level 字段到 tool_confirm_required 事件 | 2026-07-06 |
| 前端 HeartbeatSSEEvent 新增 | useChatStore.ts 新增 case "heartbeat" 分支，更新 _lastHeartbeatAt 不触发 UI 抖动 | 2026-07-06 |
| 前端 SSE 事件 timestamp 字段补齐 | api.ts SSE 事件类型补齐 timestamp 字段 | 2026-07-06 |
| 前端 case "tool" 死代码清理 | useChatStore.ts 删除 case "tool" 分支 + ToolSSEEvent interface 死代码 | 2026-07-06 |
| generate_code 死 import 清理 | tools/groups/code/__init__.py 清理未使用 import | 2026-07-06 |

---

## 十二、给小白的"看代码从哪开始"

1. **想理解聊天怎么工作** → [useChatStore.ts:sendMessage()](file:///e:/Desktop/agent/frontend/src/stores/useChatStore.ts) → [chat_routes.py:chat_sse()](file:///e:/Desktop/agent/backend/app/api/chat_routes.py)
2. **想理解 RAG 怎么搜** → [search_docs.py:SearchDocsTool](file:///e:/Desktop/agent/backend/src/agent/tools/groups/retrieval/search_docs.py) → [kb_manager.py:KnowledgeBaseManager.search_all_enabled()](file:///e:/Desktop/agent/backend/src/rag/kb_manager.py)
3. **想理解知识库怎么建** → [kb_routes.py:kb_upload()](file:///e:/Desktop/agent/backend/app/api/kb_routes.py) → [chunking/factory.py](file:///e:/Desktop/agent/backend/src/rag/chunking/factory.py)
4. **想理解会话怎么存** → [useSessionStore.ts:initSessions()](file:///e:/Desktop/agent/frontend/src/stores/useSessionStore.ts) → [crud.py](file:///e:/Desktop/agent/backend/app/api/crud.py)
5. **想理解界面怎么布局** → [AppRoot.tsx](file:///e:/Desktop/agent/frontend/src/components/layout/AppRoot.tsx)
6. **想理解硬件工作台** → [WorkbenchPanel.tsx](file:///e:/Desktop/agent/frontend/src/components/workbench/WorkbenchPanel.tsx)
7. **想理解 MCP** → [mcp_routes.py](file:///e:/Desktop/agent/backend/app/api/mcp_routes.py) → [manager.py](file:///e:/Desktop/agent/backend/src/mcp/manager.py) → [client.py](file:///e:/Desktop/agent/backend/src/mcp/client.py)
8. **想理解工具调度** → [tool_router.py:dispatch()](file:///e:/Desktop/agent/backend/src/agent/tool_router.py)
9. **想理解配置** → [settings.py](file:///e:/Desktop/agent/backend/src/config/settings.py)
10. **想理解启动流程** → [main.py](file:///e:/Desktop/agent/backend/main.py) → [app/main.py:create_app()](file:///e:/Desktop/agent/backend/app/main.py)
11. **想跑 RAG 评测** → [tests/rag_eval/GOLDEN_EVAL_README.md](file:///e:/Desktop/agent/backend/tests/rag_eval/GOLDEN_EVAL_README.md)

---

## 十三、长期迭代维护规则

本文件是"活文档"，随项目演进持续更新。维护规则见项目根 [AGENTS.md](file:///e:/Desktop/agent/AGENTS.md) 的「项目全景图维护」章节。

### 维护触发条件
- 新增/删除一个 API 路由 → 同步更新「API 路由总表」
- 新增/删除一个 store action → 同步更新「状态管理」
- 新增/删除一个核心组件 → 同步更新「前端组件清单」
- 新增/删除一个数据库表 → 同步更新「数据库表清单」和「整体架构」
- 新增/删除一个核心函数 → 同步更新对应链路
- 修复重要 bug → 追加到「已修复的 bug」表格
- 新增用户能力 → 同步更新「用户能力地图」
- 新增工程能力 → 同步更新「工程能力地图」
- 关键参数变更 → 同步更新「关键参数与限制汇总」

### 文件位置
- 桌面副本：`C:\Users\奶茶丸\Desktop\agent-architecture-map.md`（用户查阅版）
- 项目内副本：`docs/architecture-map.md`（开发迭代版）
- 两份内容必须保持一致，每次更新项目内副本后同步刷新桌面副本

### 同步命令
```powershell
Copy-Item "e:\Desktop\agent\docs\architecture-map.md" "C:\Users\奶茶丸\Desktop\agent-architecture-map.md" -Force
```

### 维护负责人
- Trae / Codex 任意线程修改代码后，若触发上述条件，必须同步更新本文件
- 00-control 在 PR 审查时核验本文件是否同步
