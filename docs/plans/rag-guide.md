# RAG 入门与实践指南

> 最后更新：2026-06-23
> 目标：理解 RAG 全链路，掌握可调参数
> 适用：Hardware RAG Agent 项目

---

## 目录

- [RAG 是什么](#rag-是什么)
- [全链路概览](#全链路概览)
- [分块策略：你的最终方案](#分块策略你的最终方案)
- [每个环节详解](#每个环节详解)
- [深入理解 BM25 + RRF 融合](#深入理解-bm25--rrf-融合)
- [深入理解 ChromaDB](#深入理解-chromadb)
- [深入理解向量化](#深入理解向量化)
- [调参速查](#调参速查)
- [RAG 的局限](#rag-的局限)
- [项目代码映射](#项目代码映射)

---

## RAG 是什么

RAG = Retrieval-Augmented Generation（检索增强生成）。

本质：**给 LLM 开卷考试**。不让它凭记忆瞎编，而是给它资料让它照着回答。

| 没 RAG | 有 RAG |
|--------|--------|
| "我记得好像是 20 mA..."（幻觉） | "根据 ESP32 数据手册，GPIO 最大输出电流是 40 mA" |

---

## 全链路概览

**离线阶段（入库）：**
```
原始 PDF → 解析 → 分块 → 向量化 → 存 ChromaDB
同步：BM25 关键词索引（可选）
```

**在线阶段（搜索）：**
```
用户问题 → 向量化 → ChromaDB 相似度搜索
→ (可选 BM25 + RRF 融合) → 拼 prompt → LLM 回答
```

你的项目里这条链是通的。缺 BM25。

---

## 分块策略：你的最终方案

> 以下来自 chunking-analysis.md 的最终决策。

### 上传时用户可选

| 文件类型 | 分块方式 | 说明 |
|---------|---------|------|
| **PDF** | Agent 分块 | 多模态 LLM 直接看图、表格、文字，一次性完成 OCR + 理解结构 + 分块 |
| **其他（HTML/TXT/MD/代码）** | 混合分块 | 第一层结构分块（按标题粗切），第二层递归字符分块（大块细切），小-大映射（小块搜，大块返回） |

### 为什么这样分

PDF 有图片、表格、扫描件 → Agent 一刀解决所有问题。
其他格式没有图片问题 → 结构分块已经够好。

### 上传时用户看到的界面

```
分块方式：
  ○ Agent 分块（适合 PDF，保留图片/表格/结构）
  ● 混合分块（适合 HTML/TXT/MD，按标题切分）

默认根据后缀建议：
  .pdf → Agent
  .html / .htm / .md / .txt / .csv → 混合
```

### 五种分块策略对比

| 策略 | 适用 | 优点 | 缺点 |
|------|------|------|------|
| 递归字符分块 | 通用文本 | 实现简单 | 不管语义，可能从表格/代码中间断开 |
| 语义分块 | 自然语言文档 | 语义完整 | 依赖 embedding，慢，边界不精确 |
| Agent 分块 | PDF/复杂文档 | 最强，理解结构 | 花钱（LLM 调用），慢 |
| 小-大分块 | 需要精确检索时 | 小块搜+大块返回，兼顾精度和上下文 | 实现复杂，需要映射逻辑 |
| 结构分块 | 有章节结构的文档 | 自然边界，不会跨章节 | 不同文档结构不同，需要适配器 |

### 与路线图的关系

| 波次 | 做什么 |
|------|--------|
| 第一波（单库搜索打稳） | 混合分块 + 小-大映射 + BM25 |
| 第二波（Agent 分块） | agent_chunker + 前端选择 + 对比测试 |
| 第三波（多库） | 每个知识库独立选分块方式 |

### 涉及的文件变更

| 文件 | 改什么 |
|------|--------|
| vector_store.py | 加小-大映射逻辑 |
| chunking/agent_chunker.py | 新增：Agent 分块 |
| chunking/hybrid_chunker.py | 新增：混合分块 |
| kb_routes.py | upload 加 chunk_method 参数 |
| 前端上传组件 | 加分块方式选择 |

---

## 每个环节详解

### 分块（你最该调的）

**当前值：** chunk_size=1000, chunk_overlap=200

| 参数 | 当前值 | 调大 | 调小 |
|------|--------|------|------|
| chunk_size | 1000 | 每块更完整，精度降 | 精度高，信息可能不全 |
| chunk_overlap | 200 | 上下文连接更好 | 省存储，可能断 |

建议从 1500/500 开始试。

**separator 顺序：** 当前空格在句号前，可能导致 "3.3 V" 被误伤 → 调一下顺序。

### 向量化

**当前：** OpenAIEmbeddings(model="text-embedding-3-small")

用户配 API Key → 调用户的 API。你不需要管内部。

### 检索（第二该调的）

**当前：** k=5, score_threshold=0.0

先设 k=10 多取一些，让 LLM 自己过滤。

### 生成

当前把检索结果拼进 system prompt。可调：prompt 模板的文字。差的和好的差别很大。

---

## 深入理解 BM25 + RRF 融合

### BM25 是什么

- **向量搜**语义相似（"上拉电阻" ≈ "pull-up resistor"）
- **BM25 搜**关键词相同（"GPIO" → 含 "GPIO" 的文档排名高）
- 两者互补，一起用效果最好

### RRF 融合公式

```
每条结果的 score = 1/(60 + rank_vector) + 1/(60 + rank_bm25)
```

**示例：**
- chunk_A：向量排第 2，BM25 排第 5 → score = 1/62 + 1/65 = 0.031
- chunk_B：向量排第 1，BM25 没出现（排名 = ∞）→ score = 1/61 + 0 = 0.016

结果：chunk_A 排前面（两个方法都找到它），chunk_B 排后面（只有向量找到它）。

**常数 60 越大** → 排名差异的影响越小（更公平）
**常数 60 越小** → 排名靠前的优势更大（更偏向第一名）
业界默认 60，一般不动。

### 何时加 BM25

BM25 在以下场景最有价值：
- 查询包含精确术语（寄存器名、引脚号、器件型号）
- 文档包含大量技术术语（硬件文档天然适合）
- 向量模型对罕见词/缩写匹配不好

**计划：** 第一波切片优化稳定后加 BM25。

### 何时需要 Rerank

| 文档数量 | 是否需要 Rerank |
|---------|----------------|
| < 500 | 不需要，BM25 + RRF 足够 |
| 500-5000 | 可选，加一个 lightweight reranker |
| > 5000 | 推荐加，明显提升 |

---

## 深入理解 ChromaDB

**存在你的 data/chroma_db/ 目录里。**

collection = 一张表，存了：
- **向量**（embedding 模型算出来的）
- **文本**（原始 chunk 内容）
- **metadata**（来源、章节、doc_id）

**搜索时：** 用户问题向量化 → 余弦相似度 → 返回距离最近的 N 条

**你能查的：**
- get_collection_stats() → 有多少 chunk、按 category 分布
- delete_document() → 删除指定文档的所有 chunk
- peek() → 看 collection 里有什么

---

## 深入理解向量化

文本 → 数字的过程叫 embedding。

```
"GPIO 上拉电阻" → [0.12, -0.45, 0.78, 0.01, ...]（1536 个数字）
"pull-up resistor"  → [0.11, -0.44, 0.79, 0.02, ...]（语义相似 → 距离近）
"今天天气很好"    → [0.89, 0.23, -0.56, ...]（语义不同 → 距离远）
```

**关键：** 向量是语义映射，不是翻译。相似的文本在向量空间里距离近。

你的项目用的是用户的 API，模型默认 text-embedding-3-small。
不同的 embedding 模型产出不同长度的向量，互相不兼容。

---

## 调参速查

| 现象 | 调什么 |
|------|--------|
| 搜不到但知识库有 | k 调大（5→10）或 score_threshold 调低 |
| 搜到不相关的 | chunk_size 调大（1000→1500） |
| 明显相关但搜不到 | 加 BM25 |
| 慢 | 按 category 过滤 |
| 表格中间断开 | 调 separator 顺序，空格放句号前 |

### 你当前可以动手改的地方

**vector_store.py：**
- chunk_size（1000 → 1500）
- chunk_overlap（200 → 500）
- k（5 → 10）
- separators 顺序（空格放句号前面）

**chat_routes.py：**
- prompt 模板文字（怎么要求 LLM 引用来源）
- 实际传给 LLM 的 top_k（从搜索结果里取前几条）

---

## RAG 的局限

RAG 不能解决的：

1. **知识库里没有相关内容** → 需要补文档，不是调参能解决的
2. **文档质量差**（过时、错误）→ RAG 会放大错误
3. **LLM 不听话** → 给它资料它不引用 → 换 prompt 模板
4. **问题不是事实性的**（"帮我写代码"）→ 不需要搜 → Agent 判断
5. **换 embedding 模型后旧向量全废** → 需要重新入库所有文档
6. **多知识库不同 embedding 模型不兼容** → 每个 KB 独立 ChromaDB collection

---

## 项目代码映射

### 上传 PDF 完整链路

```
kb_routes.py kb_upload
  → _validate_file_magic（文件校验）
  → _parse_file（解析）
  → _index_document（后台入库）
    → Docling / file_parsers.py
    → vector_store.ingest
      → MarkdownHeaderTextSplitter（按标题切）
      → RecursiveCharacterTextSplitter（细切）
      → OpenAIEmbeddings（向量化）
      → ChromaDB.add_documents
```

### 用户提问完整链路

```
chat_routes.py chat
  → user message
  → vector_store.search（搜索，k=5）
  → 拼 prompt（system_prompt + 参考文档）
  → LLM chat_stream
```
