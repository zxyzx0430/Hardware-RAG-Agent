# 任务单：RAG 全量化实现

> 读之前：先读 `docs/completed.md` 了解已有功能，再读 `docs/pitfalls.md` 看踩坑。
> 开工前：`plur inject "RAG 全量化实现" --fast --json`
> TODO：每完成一项在 `docs/todos/03-knowledge.md` 打勾

## 一、任务概述

把现有的「上传 → LangChain 基础分块 → 单 ChromaDB → 单 embedding」升级为完整 RAG 体系。

### 改动范围（~2200 行，涉及 15+ 文件）

后端新增 5 文件 | 后端改 4 文件 | 前端改/新增 4 文件 | 文档更新
- `chunking/` 模块 | `vector_store.py` 重构 | KB 管理页 | `api-contract.md`
- `kb_manager.py` | `models.py` 加 KnowledgeBase | 上传组件改 | `.gitignore`
- | `kb_routes.py` 扩展 | RAG 设置面板 | `requirements.txt`
- | `__init__.py` | `types/kb.ts`、`endpoints.ts` | `data/builtin_kb/` 路径

### 构建顺序（依赖驱动）

```
Step 1: chunking/ 模块（无外部依赖）
Step 2: KnowledgeBase DB 模型 + KnowledgeBaseManager
Step 3: vector_store.py 重构（per-KB embedding + BM25 + RRF）
Step 4: kb_routes.py 扩展（新路由 + upload 改造）
Step 5: api-contract.md 同步更新
Step 6: 前端 KB 管理页 + 上传组件 + RAG 设置面板
Step 7: 内置 KB 构建脚本
Step 8: 端到端验证 + pytest
```

---

## 二、chunking/ 模块

路径：`backend/src/rag/chunking/`
`__init__.py` 导出所有公开类。

### 2.1 base.py — 数据类 + 指纹校验

```python
@dataclass
class ChunkResult:
    text: str
    metadata: dict
    page_range: tuple[int, int]       # (start_page, end_page)
    fingerprint: str                  # SHA256(text)
    chunk_method: str                 # "agent" | "hybrid"
    section_title: str = ""           # 所属章节标题

def compute_fingerprint(text: str) -> str:
    """SHA256(content) 用于校验 chunk 覆盖完整性。"""

def verify_page_coverage(chunks: list[ChunkResult], total_pages: int) -> dict:
    """
    返回覆盖报告:
    {
        "covered_pages": set,
        "missing_pages": [...],      # 漏页 → 报错（agent）/补切（hybrid）
        "duplicate_pages": [...]      # 重叠 → 合并
    }
    """
```

### 2.2 hybrid_chunker.py — 混合分块

策略：结构粗切 → 递归细切 → 小-大映射

```
输入：纯文本（PDF/MD/TXT 解析后）
Step 1: 按文档天然结构初步划分
  Markdown → 按 ## / ### / --- 切
  TXT → 按连续空行 + 章节标题切
  PDF 解析文本 → 按段落边界 + 空行切
Step 2: 超 chunk_size（默认 1000）的大块 → 递归字符切分
  separators: ["\n## ", "\n### ", "\n\n", "\n", "。", ".", " ", ""]
  chunk_overlap: 200
Step 3: 小-大映射（Small-to-Big）
  小 chunk: Step 2 结果（~500 chars），用于 embedding 检索
  大 chunk: 小 chunk 所在的 Step 1 完整结构单元，用于 LLM 生成
  映射: metadata {small_chunk_id, big_chunk_text}

注意：Step 3 的小-大映射是 metadata 级别的，不实际写入 ChromaDB。
```

### 2.3 agent_chunker.py — Agent 分块

**适用场景：** 用户在上传时主动选择「Agent 分块」。
**用途：** 需要 LLM 理解文档语义才能确定分块边界的情况（表格混排、图文混排、复杂格式）。

#### 分块流程

```
Step 1: 提取书签/目录
  PyMuPDF doc.get_toc()
  有目录 → 按章节划分批次
  无目录 → 按字体大小检测标题 → 生成伪目录

Step 2: 按批次送 LLM
  context_window = 256000（配置项，RAG 设置可改）
  单批 ≈ 一个完整章节
  单批超过 context_window × 0.8 → 递归子标题或均分
  批次间 1 页 overlap
  每批 prompt 要求输出 JSON 数组:
  [
    {
      "start_page": 1,
      "end_page": 3,
      "title": "GPIO 配置",
      "summary": "GPIO 的推挽/开漏模式配置方法"
    }
  ]
  LLM 参数: temperature=0, response_format={"type": "json_object"}

Step 3: 完整文档做 3 次独立分块 → 多数投票对齐边界
  不一致的边界 → 标记争议页（可配置重试次数，默认 3）
  3 次后仍不一致 → 保留投票多数结果，标记 "边界有争议"

Step 4: 漏页检测
  verify_page_coverage(chunks, total_pages)
  全量覆盖 ✅ → 通过
  有漏页 → 报错 AGENT_CHUNK_INCOMPLETE，不清除已有数据，用户可以重试
```

#### 异常处理
- LLM 调用失败（网络/认证/超时）→ `AGENT_CHUNK_FAILED`，不等候自动重试，返回给用户
- 用户重试清空当前批次的临时数据重新开始
- 不退 hybrid_chunker

#### 图片/OCR 预留
- 预留 `extract_images: bool` 字段
- 当前阶段先跳过图片区域，纯文本分块
- 未来可以解图片 → OCR → 混合分块

#### 独立模型配置
```python
# 独立于聊天模型的 Agent 分块配置
agent_chunker_model: str = "gpt-4o-mini"       # 默认值，用户可在 RAG 设置里改
agent_chunker_base_url: str = "https://api.openai.com/v1"  # 默认值
agent_chunker_api_key: str = ""                 # 用户填，前端注入后端
# 这三个字段在 KnowledgeBase 模型上（per-KB 级别）
# 默认值来自 RAG 设置面板（全局默认），创建 KB 时可选覆盖
```

**为什么三路投票选 `gpt-4o-mini` 作默认：** 便宜（$0.15/M input token），128K 上下文，绝大多数 PDF ≤ 200K token 可一次全量送。3 次投票 ≈ $0.04/文档。

### 2.4 factory.py — 工厂

```python
class BaseChunker(ABC):
    @abstractmethod
    async def chunk(
        self,
        text: str,
        metadata: dict,
        file_path: Optional[Path] = None,
        total_pages: int = 0,
    ) -> list[ChunkResult]:
        ...

def get_chunker(chunk_method: str, **kwargs) -> BaseChunker:
    if chunk_method == "agent":
        return AgentChunker(**kwargs)
    elif chunk_method == "hybrid":
        return HybridChunker(**kwargs)
    else:
        raise ValueError(f"Unknown chunk_method: {chunk_method}")
```

---

## 三、数据模型

### 3.1 KnowledgeBase（新建）

`backend/app/db/models.py` 新增：

```python
class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: str = Column(String, primary_key=True, default=uuid4_str)
    name: str = Column(String, nullable=False)
    description: str = Column(String, default="")
    collection_name: str = Column(String, unique=True, nullable=False)
    chunk_method: str = Column(String, default="hybrid")   # hybrid / agent
    embedding_model: str = Column(String, default="text-embedding-3-small")
    embedding_base_url: str = Column(String, nullable=True)
    embedding_api_key_encrypted: str = Column(String, nullable=True)
    agent_chunker_model: str = Column(String, default="gpt-4o-mini")
    agent_chunker_base_url: str = Column(String, default="https://api.openai.com/v1")
    agent_chunker_api_key_encrypted: str = Column(String, nullable=True)
    context_window: int = Column(Integer, default=256000)  # 分块用 256K
    enabled: bool = Column(Boolean, default=True)          # 是否参与搜索
    is_builtin: bool = Column(Boolean, default=False)
    builtin_path: str = Column(String, nullable=True)       # 内置 KB 的 Chroma 路径
    created_at: str = Column(String, default=lambda: datetime.datetime.utcnow().isoformat())
    updated_at: str = Column(String, default=lambda: datetime.datetime.utcnow().isoformat())
```

### 3.2 KnowledgeDoc 改造

现有表加字段：

```python
kb_id: str = Column(String, ForeignKey("knowledge_bases.id"), default=builtin_kb_id, index=True)
chunk_method_used: str = Column(String, default="hybrid")
```

### 3.3 Settings 新增键值

| key | value 示例 | 用途 |
|-----|-----------|------|
| `embedding_default_model` | `text-embedding-3-small` | KB embedding 全局默认 |
| `embedding_default_base_url` | `https://api.openai.com/v1` | |
| `embedding_default_api_key_encrypted` | Fernet 加密 | |
| `agent_chunker_default_model` | `gpt-4o-mini` | Agent 分块全局默认 |
| `agent_chunker_default_base_url` | `https://api.openai.com/v1` | |
| `agent_chunker_default_api_key_encrypted` | Fernet 加密 | |
| `default_context_window` | `256000` | 分块用的上下文窗口大小 |

---

## 四、KnowledgeBaseManager

路径：`backend/src/rag/kb_manager.py`

```python
class KnowledgeBaseManager:
    """管理多个知识库的创建、删除、检索路由。"""

    def __init__(self, db_session_factory, builtin_kb_path: Path = BUILTIN_KB_DIR):
        self._stores: dict[str, HardwareVectorStore] = {}  # kb_id → store
        self._bm25_indices: dict[str, BM25Index] = {}      # kb_id → BM25
        ...

    # === Collection 管理 ===
    def create_kb(self, name, chunk_method, embedding_config) -> KnowledgeBase: ...
    def list_kbs(self) -> list[KnowledgeBase]: ...
    def get_kb(self, kb_id) -> KnowledgeBase: ...
    def delete_kb(self, kb_id): ...
        """删除: DB 记录 + ChromaDB collection + BM25 索引文件"""

    # === 入库 ===
    def ingest_chunks(self, kb_id: str, chunks: list[ChunkResult], doc_id: str) -> int:
        """分块入库 → 重建 BM25 索引"""

    # === 检索 ===
    def search(self, kb_id: str, query: str, k: int = 5) -> list[SearchResult]:
        """单 KB 检索: BM25 + Vector → RRF 融合"""

    def search_all_enabled(self, query: str, k: int = 3) -> dict[str, list[SearchResult]]:
        """在所有 enabled=True 的 KB 中检索 → 按 KB 分组返回"""

    # === 内置 KB ===
    def ensure_builtin_kb(self):
        """启动时检测，builtin_kb 路径存在但 DB 无记录则自动创建"""
```

### 4.1 BM25 + RRF

```python
class BM25Index:
    def __init__(self, corpus: list[str]):
        import jieba  # 懒加载（首次 ~2s）
        self.bm25 = BM25Okapi([jieba.lcut(doc) for doc in corpus])

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        tokenized_query = jieba.lcut(query)  # 懒加载
        scores = self.bm25.get_scores(tokenized_query)
        # 返回 top-k (doc_index, score)

    def save(self, path: Path): ...
    @classmethod
    def load(cls, path: Path, corpus: list[str]) -> "BM25Index": ...
```

**存储路径：** `data/bm25/{collection_name}.pkl`
**内置 KB 的 BM25 索引**也放这里，提交 git。
**重建时机：** KB 增删文档后 → 标记 stale → 下次 search 前重建。

```python
def rrf_fusion(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    constant_k: int = 60
) -> list[SearchResult]:
    """
    RRF: score = Σ 1/(k + rank_v(d)) + Σ 1/(k + rank_b(d))
    默认 k=60，按融合分降序排列
    """
```

---

## 五、API 路由

### 5.1 新增路由（写入 api-contract.md 后再写代码）

| 方法 | 路径 | 用途 | 状态 |
|------|------|------|------|
| `GET` | `/api/kb/collections` | 列出所有 KB | agreed |
| `POST` | `/api/kb/collections` | 创建 KB | agreed |
| `GET` | `/api/kb/collections/{kb_id}` | 单个 KB 详情 | agreed |
| `DELETE` | `/api/kb/collections/{kb_id}` | 删除 KB | agreed |
| `PATCH` | `/api/kb/collections/{kb_id}/toggle` | 切换 KB 搜索开关 | agreed |
| `GET` | `/api/kb/embedding-models` | 代理上游 embedding 模型列表 | agreed |

### 5.2 POST /api/kb/collections 请求体

```json
{
  "name": "ESP32 笔记库",
  "description": "我整理的 ESP32 踩坑记录",
  "chunk_method": "hybrid",
  "embedding_model": "text-embedding-3-small",
  "embedding_base_url": "https://api.openai.com/v1",
  "embedding_api_key": "sk-...",
  "agent_chunker_model": "gpt-4o-mini",
  "agent_chunker_base_url": "https://api.openai.com/v1",
  "agent_chunker_api_key": "sk-..."
}
```

### 5.3 POST /api/kb/upload 改造

请求体加字段：

```json
{
  "file": "...",
  "kb_id": "默认上传到当前选中的 KB",
  "chunk_method": "可选，覆盖 KB 默认值",
  "chunk_size": 1000
}
```

响应加字段：

```json
{
  "success": true,
  "data": {
    "doc_id": "...",
    "kb_id": "...",
    "chunk_method_used": "hybrid",
    "chunks_created": 42,
    "page_coverage": {
      "total_pages": 10,
      "covered_pages": 10,
      "missing_pages": [],
      "duplicate_pages": []
    }
  }
}
```

### 5.4 搜索 API 改动

现有 `/api/chat` 的 RAG 搜索 → 调用 `search_all_enabled()`，每条 source 加 `kb_id` + `kb_name`。

---

## 六、前端改动

### 6.1 知识库管理页面

**入口：** 现有的 Knowledge 面板右上角加「管理知识库」按钮 → 打开 KB 管理页（新组件或弹窗）。

**功能：**
- 列表：名称 / 分块方式 / Embedding 模型 / 文档数 / Chunk 数 / 开关
- 开关 = 是否参与搜索（`PATCH /api/kb/collections/{id}/toggle`）
- 新建：弹窗表单（名称 + 描述 + 分块方式 + Embedding 配置 + Agent 分块配置）
- 删除：确认弹窗「删除不可恢复，关联文档全部删除」
- 点击 KB → 展开该 KB 的文档列表

### 6.2 上传组件改造

- 文件选择后→ 下拉选目标 KB（默认给你最常用的那个）
- 分块方式下拉：Agent / Hybrid
  - 默认 = KB 设定的方式
  - 可手动覆盖
  - 选 Agent 时显示提示「AI 分块耗时较长，可能需要几轮 LLM 调用」
- 进度条：分块阶段 → embedding 阶段 → 完成
- Agent 分块时显示当前状态「LLM 正在分析文档结构（第 2/3 轮）...」
- 分块失败时显示错误 + 重试按钮

### 6.3 RAG 设置面板

新建 `frontend/src/components/settings/RagSettingsPanel.tsx`，放在设置页。

**字段：**

| 字段 | 类型 | 默认 |
|------|------|------|
| Embedding 模型 | text | `text-embedding-3-small` |
| Embedding Base URL | text | `https://api.openai.com/v1` |
| Embedding API Key | password | (空) |
| Agent 分块模型 | text | `gpt-4o-mini` |
| Agent 分块 Base URL | text | `https://api.openai.com/v1` |
| Agent 分块 API Key | password | (空) |
| 分块上下文窗口 | number | 256000 |

> 这些值作为全局默认。用户创建 KB 时可覆盖。

### 6.4 TypeScript 类型更新

`frontend/src/types/kb.ts` 新增：

```typescript
interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  collection_name: string;
  chunk_method: 'hybrid' | 'agent';
  embedding_model: string;
  agent_chunker_model: string;
  enabled: boolean;
  is_builtin: boolean;
  doc_count: number;
  chunk_count: number;
  created_at: string;
}

interface CreateKBRequest {
  name: string;
  description?: string;
  chunk_method: 'hybrid' | 'agent';
  embedding_model: string;
  embedding_base_url?: string;
  embedding_api_key?: string;
  agent_chunker_model?: string;
  agent_chunker_base_url?: string;
  agent_chunker_api_key?: string;
  context_window?: number;
}
```

### 6.5 来源展示

现有 RAG source 展示增加 KB 标签。`source` 事件加字段：

```json
{
  "kb_id": "builtin-001",
  "kb_name": "硬件手册库",
  "title": "STM32F4 参考手册",
  "content": "..."
}
```

前端展示：`[硬件手册库] STM32F4 参考手册 — 第 3 章 GPIO 配置`

---

## 七、内置 KB 构建

### 7.1 构建脚本

路径：`scripts/build_builtin_kb.py`

```python
"""
从 data/pdfs/ 读取全部 PDF → hybrid 分块 → text-embedding-3-small 向量化
→ 写入 backend/data/builtin_kb/（ChromaDB collection + BM25 索引）

用法: python scripts/build_builtin_kb.py [--force]
--force: 清空重建
"""
```

### 7.2 存储路径

```
backend/data/builtin_kb/          ← ChromaDB persist_dir（走 git）
backend/data/builtin_kb/bm25.pkl  ← BM25 索引（走 git）

backend/data/chroma_db/           ← 用户 KB 的 ChromaDB 数据（gitignored）
backend/data/bm25/                ← 用户 KB 的 BM25 索引（gitignored）
```

### 7.3 启动时检测

`KnowledgeBaseManager.ensure_builtin_kb()`:
- 检测 `backend/data/builtin_kb/` 存在
- 检测 DB 中有无 `is_builtin=True` 的 KB
- 有一个缺失就自动创建
- 嵌入式 KB 的名字固定为「硬件手册库」

### 7.4 `.gitignore` 改动

```
# 加一行保留内置 KB
!backend/data/builtin_kb/
```

---

## 八、边界情况

| 场景 | 处理方式 |
|------|---------|
| Agent 分块 LLM 失败 | 捕获异常 → 返回 `AGENT_CHUNK_FAILED`，数据清空，用户重试 |
| 漏页检测不通过 | 报 `AGENT_CHUNK_INCOMPLETE`，保留已有数据，用户可选重试或切 hybrid |
| 超大文件超 token | 按 TOC 章节分批 → 单章超限递归子标题 → 仍超则均分 |
| 3 次投票不一致 | 多数投票 + 标记「边界有争议」，争议页在 metadata 加 `boundary_disputed: true` |
| embedding 模型换了 | 旧向量不动，用户需手动删除 KB 重建 |
| BM25 索引过期 | 增删文档后标记 stale → 下次 search 前自动重建 |
| 上传非 PDF/MD/TXT | 走现有 file_parsers.py → hybrid_chunker |
| 上传含图片的 PDF | 当前阶段跳过图片区域，只分文本。预留 `extract_images` 字段 |
| 内置 KB 路径不存在 | 不报错，`is_builtin=True` 的 KB 不创建，search_all_enabled 跳过它 |

---

## 九、验证清单

- [ ] pytest: `chunking/` 模块（纯逻辑，好测）
- [ ] pytest: `kb_manager.py`（mock ChromaDB）
- [ ] pytest: `kb_routes.py`（mock DB session）
- [ ] 手动验证：内置 KB 首次启动自动创建
- [ ] 手动验证：创建 hybrid KB → 上传 PDF → 入库 → 搜索
- [ ] 手动验证：创建 agent KB → 上传 PDF → Agent 分块完成 → 入库 → 搜索
- [ ] 手动验证：KB 开关 → 关掉后搜索不返回该 KB 结果
- [ ] 手动验证：Agent 分块失败 → 前端报错 + 重试
- [ ] 手动验证：2 个不同 embedding 模型的 KB 同时搜索
- [ ] 前端 TS `--noEmit` 通过
- [ ] `api-contract.md` 已同步更新
- [ ] `docs/pitfalls.md` 已记录踩坑
- [ ] `docs/todos/03-knowledge.md` 已打勾

---

## 十、约束

### 接口契约铁律
- 任何新接口先写 `docs/api-contract.md` 再写代码
- 修改已有接口路径/字段/状态码 → 先改文档

### 踩坑规则
- 修 bug 后必须更新 `docs/pitfalls.md`
- 格式：日期 / 现象 / 原因 / 修法 / 下次注意

### TODO 清单
- 每完成一项，在 `docs/todos/03-knowledge.md` 的 `[ ]` 改为 `[x]` + 完成说明
- 新任务加到文件最前面（倒序）

### 不做范围
- 不改 `docs/thread-map.md`
- 不改现有 `pipeline.py` CLI 入口（后续适配）
- 不改 `main.py` 路由注册（kb_routes 已注册）
- 不改 Fernet 加密机制，复用现有 auth.py

---

## 十一、涉及文件完整清单

### 后端新增（5 文件）
- `backend/src/rag/chunking/__init__.py`
- `backend/src/rag/chunking/base.py`
- `backend/src/rag/chunking/hybrid_chunker.py`
- `backend/src/rag/chunking/agent_chunker.py`
- `backend/src/rag/chunking/factory.py`
- `backend/src/rag/kb_manager.py`

### 后端修改（4 文件）
- `backend/src/rag/vector_store.py` — 加 ingest_chunks(), get_all_texts(), 支持 per-KB embedding
- `backend/app/db/models.py` — 加 KnowledgeBase, KnowledgeDoc.kb_id, KnowledgeDoc.chunk_method_used
- `backend/app/api/kb_routes.py` — 加 collection/embedding-models 路由, upload 加 chunk_method
- `backend/src/rag/__init__.py` — 导出新模块

### 前端新增/修改（4 文件）
- `frontend/src/types/kb.ts` — 加 KnowledgeBase / CreateKBRequest / EmbeddingModel 类型
- `frontend/src/api/endpoints.ts` — 加新端点
- `frontend/src/stores/useKnowledgeStore.ts` — 扩展
- `frontend/src/components/knowledge/KnowledgePanel.tsx` — 加 KB 管理入口
- 新建 KB 管理页组件
- 新建 `frontend/src/components/settings/RagSettingsPanel.tsx`

### 脚本
- `scripts/build_builtin_kb.py` — 内置 KB 构建

### 文档 & 配置
- `docs/api-contract.md` — 新增接口写进去再写代码
- `.gitignore` — 加 `!backend/data/builtin_kb/`
- `requirements.txt` — 加 `rank_bm25`, `jieba`
- `docs/todos/03-knowledge.md` — 打勾
- `docs/pitfalls.md` — 记录踩坑
