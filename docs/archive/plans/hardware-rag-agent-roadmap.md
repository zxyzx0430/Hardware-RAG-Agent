# Hardware RAG Agent — 路线图

> 最后更新：2026-06-22
> 当前阶段：Phase 2 RAG 知识库

---

## Phase 1：基建（已完成）

| 模块 | 状态 |
|---|---|
| FastAPI 后端骨架 | 完成 |
| React 前端骨架 | 完成 |
| SSE 流式聊天 | 完成 |
| 模型列表 + 设置面板 | 完成 |
| 路由拆分 | 完成 |
| 工具注册表（5 个 stub）| 完成 |
| 文档框架（AGENTS.md + TODO 系统）| 完成 |

---

## Phase 2：RAG 知识库（当前）

### 第一波：单库搜索质量打稳

**目标**：在一个知识库里把搜索质量做到满意，再扩到多库。

#### 1.1 切片策略优化
- 改 vector_store.py ingest() 方法
- 按章节语义边界切，不按 1000 字硬切
- 表格保持完整，不从表格中间断开
- separator 顺序修：空格在句号前，避免 "3.3 V" 被误伤
- 依赖：无 | 估算：半天

#### 1.2 BM25 混合检索
- 安装 rank_bm25（零依赖）
- vector_store.py 新增 hybrid_search()
- 向量 top_k=10 + BM25 top_k=10 → RRF 融合
- 依赖：1.1（切片好 BM25 才有意义）| 估算：半天

#### 1.3 来源标注前端修复
- 检查 ChatArea.tsx 的 case "source" handler 是否还在
- 如果被之前删按钮误删了→复原
- 依赖：无 | 估算：2 小时

#### 1.4 单库质量验证
- 上传 3-5 篇硬件 PDF → 提 10 个问题 → 检查召回率
- 依赖：1.1-1.3 | 估算：半天

---

### 第二波：LangChain + 多库架构

**前置**：第一波做完，搜索质量已验证。

#### 2.1 手写 RAG → LangChain RetrievalQA Chain
- 学习过渡：行为不变，API 换成 LangChain
- 依赖：1.4 | 估算：半天

#### 2.2 多知识库后端
- 新增 KnowledgeBaseManager，管理多个 HardwareVectorStore
- 每个 KB 独立 ChromaDB collection + 独立 embedding 模型
- 新增 /api/kb/collections 增删查接口
- 依赖：2.1 | 估算：2 天

#### 2.3 前端知识库管理页面
- KnowledgePanel.tsx 大改：列表/新建/上传/删/搜
- 新建时选 embedding 模型
- 依赖：2.2 | 估算：2 天（可和后端并行）

---

### 第三波：出厂知识库 + 图片 OCR

#### 3.1 出厂知识库打包
- 选硬件文档（ST/ESP32/RPi），固定 embedding 模型算好
- 打包到 data/builtin_kb/，首次启动自动加载
- 依赖：无 | 估算：文档收集 + 1 天

#### 3.2 PDF 图片 OCR
- PyMuPDF 提取图片 + pytesseract OCR
- OCR 文字入库，保留原图路径 metadata，前端展示
- 依赖：无（建议放切片优化之后）| 估算：2 天

---

## Phase 3：Agent

### RAG 从强制注入 → Agent tool
- 删除 chat_routes.py 的自动检索逻辑
- tool_router.py 注册 search_hardware_kb 工具
- LLM 自主决定搜不搜
- SSE 发 tool_start / tool_result / tool_output
- 依赖：Phase 2 | 估算：2 天

### ReAct Agent + 多工具编排
- 工具集：search_kb / execute_code / diagnose / wiring
- LangChain create_react_agent
- 前端显示思考链
- 依赖：RAG tool | 估算：3 天

---

## Phase 4：收尾

| 任务 | 时机 |
|---|---|
| README.md | Phase 2 收尾时 |
| Docker Compose | 功能稳定后 |
| 测试补全 | 每阶段提交时 |
| pre-commit hook | 任何提交前 |

---

## 依赖关系表

| 波次 | 依赖 |
|---|---|
| 第一波 | 1.2 BM25 依赖 1.1 切片优化 |
| 第二波 | 全部依赖第一波完成 |
| 第三波 | 3.2 OCR 建议放切片优化之后 |
| Agent | 依赖 Phase 2 全部完成 |
