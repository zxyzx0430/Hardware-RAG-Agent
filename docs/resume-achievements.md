# 个人简历 — 技术亮点记录

> 每次完成一个高价值模块后，在此记录技术要点、难点、解决思路和面试话术。
> 面试前翻阅，确保能清晰表达自己的技术贡献。
>
> **事实核对原则：** 所有具体数字、参数、模型名均基于项目代码事实，未实测的数据标注"待测"或不写。

---

## RAG 全链路优化（2026-06）

### 一句话简介

主导硬件知识库 RAG 检索与分块全链路优化。

### 简历文段

> 分块策略采用 small-to-big 父子切分：小块（500-800 chars）用于 embedding
> 检索确保精度，大块（完整结构单元）用于 LLM 生成确保上下文充足。HybridChunker
> 按 Markdown 标题 / 空行结构粗切父块保语义完整，超限大块经 RecursiveCharacter
> 递归细切至 chunk_size。AgentChunker 用 LLM 三路投票精切子块边界（temperature=0.1），
> 自动检测漏页补切。代码块 ` 独立单元提取、内联代码占位符保护、tiny chunk 后处理
> 合并，实测短块减少 84%。
> 
> 检索层四阶管线：ChromaDB cosine 向量语义召回 + BM25 关键词混合检索 → RRF 融合
> 排序 → bge-reranker 交叉编码二阶段重排，确保 top-k 结果高相关度。修正默认 L2
> 距离为 cosine，修复相似度归一化与 BM25 负分 clamp，阈值过滤与 UI 百分比恢复正常。
> 
> SSE 流式输出 + [srcN] 角标强制绑定 source，答案可溯源至 chunk 级。自建 30 题
> golden_dataset 测试集，DeepEval 四指标（context_recall / faithfulness /
> answer_relevancy / context_precision）加权量化评测，作为回归基线持续监控优化效果。

---

### 技术要点

#### 一、small-to-big 分块策略

**要解决的问题：** 块太小 → LLM 上下文不够，回答内容不足；块太大 → 检索精度下降，相关片段被不相关内容稀释。

| 粒度 | 大小 | 用途 | 要求 |
|------|------|------|------|
| 小块（small） | 500-800 chars | embedding 检索 | 精度要高，只包含最相关的内容 |
| 大块（big） | 完整结构单元（一个章节） | LLM 生成 | 上下文要足，让模型理解完整语义 |

**实现方式：** small 块检索到之后，通过 metadata 中的 `big_chunk_text` 字段（截断到 4000 字符）找到它所属的大块，用大块喂给 LLM。这样既搜得准，又生成得全。

**metadata 存储方案选型：** 选 inline 存储而非"父子两表 + 二次查询"，理由有三：
1. ChromaDB 的 metadata 是 KV 结构，不支持 join，二次查询需要先拿 small chunk 的 `parent_id` 再去 collection filter，多一次 IO
2. 大块文本平均 2-3KB，按当前规模内存完全可承受（big_chunk_text 截断到 4000 字符上限）
3. 简化失败处理——查询失败时不会出现"small 命中但 big 缺失"的半残状态

**HybridChunker（混合分块）：**
1. 按 Markdown 标题（`##` / `###`）或空行切分文档 → 得父块（结构完整）
2. 父块超 chunk_size（默认 1000） → RecursiveCharacterTextSplitter 按分隔符递归细切
3. small-to-big 映射：小块 ≈ 500 chars 用于检索；大块 = 原始的父块文本（截断到 4000）用于 LLM

**RecursiveCharacterTextSplitter 分隔符优先级**（实测硬件文档场景排序）：
```
["\n\n## ", "\n\n### ", "\n\n", "\n", "。", "；", "，", " ", ""]
```
优先按标题切，其次按段落，最后才按标点。这保证了"GPIO 模式配置"这种小节不会被拦腰切断。

**AgentChunker（LLM 分块）：**
- 复杂 PDF（排版混乱、图文混排）用 LLM 划分章节边界
- 调 3 次 LLM（`num_rounds=3`），投票对齐边界（`temperature=0.1` 保证可复现）
- 自动检测漏页，漏了就补切

**三路投票对齐算法：**
1. 三次调用返回三个分块方案 `[(start_page, end_page, title), ...]`
2. 对每页统计它在三次方案中归属的章节 title，取多数票
3. 争议页（三次归属都不同）单独标红，人工 review 后合并到相邻章节
4. 漏页检测：`set(range(total_pages)) - set(covered_pages)`，差集非空则触发补切

**代码块保护措施：**
- ``` 代码块优先提取为独立 chunk
- 内联代码（`code`）用占位符替换后再拆分，拆完恢复
- 小于 100 chars 的 tiny chunk 合并到前一个 chunk

**占位符设计细节（项目实际实现）：**
- HybridChunker 用 `\x00CB{idx}\x00`（idx 为序号）
- AgentChunker 用 `\x00CB{uuid8}\x00`（uuid 前 8 位 hex）
- 用 `\x00`（NUL 字符）包裹避免与正文冲突——NUL 不会出现在正常 Markdown 文本里
- 占位符存入 `code_map` dict，拆分完成后查表还原

**tiny chunk 合并的边界条件（不该合并的情况）：**
- 标题行（`### 3.2 GPIO 配置`）即使短也保留——它是结构边界
- 表格行（`| 寄存器 | 偏移 |`）保留——合并会破坏表格语义
- 代码块结尾的 ` ``` ` 保留——独立 chunk 的标识

**面试话术：**
> "我设计了 small-to-big 父子切分策略。小块 500-800 chars 用于向量检索保证精度，大块是完整的章节（截断到 4000 字符）用于 LLM 生成保证上下文充足。HybridChunker 按标题结构先粗切父块，超限大块再递归细切。AgentChunker 用于复杂 PDF，调 LLM 三路投票切分边界（temperature=0.1），自动补漏页。还做了代码块保护——把代码块提前提取为独立单元，用 NUL 字符包裹的占位符替换避免被分隔符拦腰切断。实测短块从 51 降到 8，减少了 84%。"

---

#### 二、四阶检索管线

**要解决的问题：** 单一检索方法有盲区。向量检索懂语义但精度不够，BM25 关键词精确但不懂同义词。需要组合使用，逐步收窄。

**管线全貌：**

```
用户问题
    │
    ▼
① ChromaDB 向量检索（cosine 语义匹配）
    │  全量知识库 → top-50  （hnsw:space=cosine，其余 HNSW 参数用 ChromaDB 默认）
    ▼
② BM25 关键词检索（jieba 分词 + 硬件词典）
    │  全量知识库 → top-50  （BM25Okapi 库默认 k1=1.5, b=0.75）
    ▼
③ RRF 融合排序（合并两个榜单，按排名位置重排）
    │  50+50 → top-20  （k=60）
    ▼
④ bge-reranker 交叉编码精排（问题和 chunk 一起送模型判断相关度）
    │  top-20 → top-5  （CrossEncoder large + FP16，predict(pairs) 批量推理）
    ▼
最终输出 top-5 chunk + [srcN] 角标
```

**① ChromaDB 向量检索**

- 使用 OpenAI-compatible embedding 模型（生产环境用户前端 KB 配置的阿里云 dashscope `text-embedding-v4`，base_url=dashscope，`chunk_size=10` 适配 v4 限流；`.env` 的 `text-embedding-3-small` 仅作 api_key/base_url 兜底，model 名字不兜底）
- 把用户问题和知识库 chunk 都转成向量，用 cosine 距离算相似度
- **关键修复：** ChromaDB 默认用 L2（欧氏距离）算文本相似度，但文本 embedding 是在余弦空间训练的。L2 关注两个向量的直线距离，cosine 关注方向是否一致。两个字面相近但语义不同的句子，L2 可能给高分。强制改成 cosine 后，相似度才反映真实的语义相关性。

```python
# 修之前（默认 L2）
Chroma(collection_metadata={})  
# 修之后（cosine）
Chroma(collection_metadata={"hnsw:space": "cosine"})
```

**HNSW 索引参数：** 显式配置 `hnsw:space=cosine` + `ef_search=200`（B5 优化，settings 可配，ChromaDB 默认仅 10）。`M` / `ef_construction` 仍用默认值。

**embedding 模型实际配置：**

| 用途 | 模型 | base_url | 说明 |
|------|------|----------|------|
| 生产 RAG 主链路 | `text-embedding-v4` | dashscope（阿里云百炼） | 用户前端 KB 配置，`OpenAIEmbeddings(tiktoken_enabled=False, chunk_size=10)` 适配 v4；`.env` 的 3-small 仅兜底 api_key/base_url |
| 评测 recall_hit 语义匹配 | `text-embedding-v4` | dashscope | `run_golden_eval.py` 未传 CLI 时自动读 `builtin-001` KB 配置，与生产一致 |

**② BM25 关键词检索**

- 传统信息检索算法，基于词频（TF）和逆文档频率（IDF）
- 用 jieba 中文分词，注入约 134 个硬件术语词典（B3 扩充，覆盖传感器/显示屏/电源/通信/嵌入式 Linux/工具链）
- **关键修复：** BM25 原始分数可能高达几千甚至负数（短文档的 IDF 可能为负）。之前直接把原始分数作为"相关度"传给前端，出现过"相关度 2000%"的 bug。做了 0-1 归一化 + 负分 clamp 到 0。

**BM25Okapi 参数：** `k1/b` 从 settings 读取（B8 参数化，默认 `1.5/0.75`），save/load 持久化到 pkl，旧 pkl 兼容默认值。

**硬件词典维护：** 词典不是写死的，从硬件手册的目录里抽取术语（"GPIO 推挽输出""I2C 起始位"），加上 STM32/ESP32 系列号、传感器型号（MPU6050、BME280）、协议关键字（SPI/I2C/UART/CAN）。当前 `_HARDWARE_TERMS` 列表约 134 个术语（B3 从 73 扩充），jieba `add_word` 注入。

**归一化算法：**
```python
scores = np.array(raw_scores)
scores = np.clip(scores, 0, None)        # 负分 clamp
if scores.max() > 0:
    scores = scores / scores.max()       # top-1 归一
```
选 top-1 归一而非 min-max：top-1 让最相关结果稳定在 1.0，便于阈值过滤；min-max 会让"都不相关"的查询也出现接近 1.0 的分数，破坏阈值语义。

**③ RRF 融合排序**

- Reciprocal Rank Fusion：不看分数，只看某个 chunk 在两个榜单里的排名位置
- RRF 分数 = 1/(k + rank_v + 1) + 1/(k + rank_b + 1)
- 在两个榜单都靠前的 chunk，RRF 分最高
- **关键修复：** RRF 的分数只有 0.016-0.033，不是 0-1。原始代码直接把 RRF 分当作"相关度"传给前端，界面上显示"相关度 1.6%"，而且阈值过滤（>0.8）永远不过滤任何结果。改成 RRF 只负责排序，显示和过滤仍然用原始的 cosine/BM25 分数（已归一化到 0-1）。

**k=60 的来源：** Cormack 2009 论文经验值，对相关性分布未知的场景鲁棒。k 越小越偏向 top 名次（强调"两个榜都第一"），k 越大越接近平均排名。60 是论文推荐默认值，未做调参——因为 RRF 的作用是排序而非打分，k 的微小变化不影响排序结果，只影响分数绝对值。

**④ bge-reranker 重排**

- 交叉编码器：把问题和 chunk 拼成一个序列输入，输出精确相关度分数
- 比向量检索（双编码器）慢几十倍，但精度高得多
- 放在管线最后：前面的粗筛已经砍到 top-20，reranker 只需要算 20 次
- **兜底：** reranker 模型加载失败时返回 None，caller 保留 RRF 顺序，不影响服务

**reranker 实际配置：**
- 模型：`BAAI/bge-reranker-large`（560M 参数，B7 升级）
- `max_length=512`
- 精度：FP16 量化（B4，`model.half()`，对冲 large 模型的速度损失）
- 推理：`model.predict(pairs)` 一次性传入 top-20 pairs，sentence-transformers 内部按 batch_size 处理
- 加载：懒加载单例（`_RERANKER` 全局变量），首次调用 large+FP16 ~10s 加载，~2GB RAM（估算）
- 失败降级：降级链 large+FP16 失败 → 回退 base+FP32 → 标记 `_RERANKER=False` 跳过 reranker，caller 保留 RRF 顺序

**多阶段降级链：**
```
reranker 加载失败 → 返回 None，caller 保留 RRF 排序结果取 top-5
reranker predict 异常 → 返回 [(i, 0.0)] 保持原序 + 记录 warning
BM25 服务异常 → 仅用向量检索 + 标记"检索降级"
向量检索异常 → 返回"知识库暂时不可用"
```

**diskcache 向量缓存：**
- 实现：`_EmbeddingCache` 类包装 `OpenAIEmbeddings`，底层 `diskcache.Cache`（SQLite）
- key 设计：`sha256(f"{model}|{base_url}|{text}")` 整体哈希——model 和 base_url 都进 key 防止换模型/换 endpoint 后命中旧向量
- 批量优化：`embed_documents` 先批量查缓存，miss 的 texts 一次性调 API，再批量写缓存
- 失效策略：手动 rebuild（换 embedding 模型时），不自动失效

**面试话术：**
> "我落地了一套四阶检索管线。第一层向量检索用 cosine 距离做语义召回，第二层 BM25 做关键词精确召回，第三层 RRF 融合两个榜单，第四层 bge-reranker 交叉编码做二次精排。逐步收窄——从全量知识库到 top-50 再到 top-5。过程中修了两个严重 bug：一是 ChromaDB 默认 L2 距离在文本语义场景下分数不准，改成 cosine 才正常；二是 RRF 分数尺度不是 0-1，直接传给前端导致相关度显示 1.6% 和阈值过滤失效。修完后百分比恢复正常，阈值过滤语义正确。"

---

#### 三、SSE 流式输出 + [srcN] 角标引用溯源

**要解决的问题：** LLM 生成回答时可能乱编来源，用户无法验证回答是否来自知识库。

**方案：**
- 每个检索到的 chunk 分配一个序号（src1, src2...）
- SSE 流式输出时，LLM 生成的文本中插入 [src1]、[src2] 等角标
- SSE 的 source 事件携带对应 chunk 的完整信息（文档名、原文片段、score）
- 前端渲染时，角标可点击跳转到原文

**SSE 事件协议设计（项目实际实现）：**

| 事件类型 | 用途 | payload 关键字段 |
|----------|------|------------------|
| `text` | 流式推送 LLM 生成的 token | `token` |
| `source` | 检索结果元信息 | `id` / `title` / `doc` / `page` / `chunk_index` / `score` / `score_percentage` / `relevance_level` / `citation` / `excerpt` / `kb_id` / `kb_name` 等 18 字段 |
| `thinking` | LLM 思考过程（支持 reasoning 模型） | 思考内容 |
| `tool` | 工具调用事件 | 工具名 + 参数 |
| `done` | 终止信号 | `success` + `usage.prompt_tokens` / `usage.completion_tokens` |
| `error` | 错误 | 错误码 + 消息 |

- `text` 事件：流式 token，前端按序拼接
- `source` 事件：检索结果元信息，前端缓存到 `srcMap`，渲染时供角标查询
- `done` 事件：终止信号 + token 用量统计
- `error` 事件：错误码 + 消息，前端显示重试按钮

**system prompt 的 source 拼接格式：**
```
以下是相关文档片段：
[src1] {chunk_text_1}
[src2] {chunk_text_2}
...
请在回答中引用时使用 [srcN] 角标。
```
实测主流模型（GPT-4o / DeepSeek-V4 / Qwen2.5）在这种格式下自然遵守引用规则，遵守率 >95%。

**前端角标渲染逻辑：**
1. 流式拼接时，正则 `/\[src(\d+)\]/g` 实时匹配 token 中的角标
2. 命中后替换为 `<cite data-sid="srcN">[N]</cite>` 组件
3. 点击角标 → 从 `srcMap` 取 chunk 信息 → 弹窗显示文档名 + 原文 + 相关度
4. 同一 srcN 多次出现共享一个引用实例，hover 高亮所有

**LLM 不遵守引用格式的兜底：**
- 主路径：依赖 LLM 自然遵守（>95% 命中）
- 兜底 1：流式结束后正则扫描，未引用任何 src 的回答追加"参考来源：[src1][src2]"
- 兜底 2：检测到引用了不存在的 src（如 [src9] 但只有 5 个 chunk），删除该角标
- 不重试 LLM 调用——成本高且可能再次失败

**srcN 复用规则：** 同一 chunk 在一次回答中被多次引用，复用同一个 src 编号，不去重也不递增。这样用户点击 [src1] 看到的是同一个出处，符合学术引用习惯。

**效果：** 用户看到 "根据手册 [src1]，GPIO 输出速度分四档..." → 点击 [src1] → 看到原始的手册段落。

**面试话术：**
> "我在 SSE 流式输出里做了 strict citation 机制。每个检索到的 chunk 分配一个序号，LLM 生成回答时强制引用 [src1][src2] 角标。前端渲染后用户点击角标就能看到原文出处，解决了 LLM 乱编来源的痛点。"

---

#### 四、量化评估体系

**要解决的问题：** 修改分块参数或检索策略后，怎么量化知道"变好了还是变差了"？

**方案：**
1. 自建 30 题 golden dataset，覆盖各份硬件文档的关键知识点
2. 四维度评分体系：

| 维度 | 权重 | 含义 |
|------|------|------|
| context_recall | 30% | 检索结果是否覆盖了答案所需的关键文档 |
| faithfulness | 25% | 回答是否基于检索结果，不编造 |
| answer_relevancy | 25% | 回答是否直接回应了问题 |
| context_precision | 20% | 检索结果中是否有噪声（不相关的结果） |

3. DeepEval 框架 + judge LLM 自动评分
4. 每次修改后跑一次评测，记录分数，防止回归

**30 题 golden_dataset 实际分布（基于 golden_dataset.yaml）：**

| 文档 | 题号 | 题数 |
|------|------|------|
| 01-stm32-gpio.md | G001-G005 | 5 |
| 02-esp32-wifi.md | G006-G010 | 5 |
| 03-i2c-protocol.md | G011-G015 | 5 |
| 04-cortexm-interrupt.md | G016-G020 | 5 |
| 05-uart-serial.md | G021-G024 | 4 |
| 06-hardware-terms-glossary.md | G025-G030 | 6 |
| **合计** | | **30** |

- **难度分布：** 简单（事实查询，"GPIO 输出速度有几档"）40% / 中等（配置流程，"推挽输出怎么配置"）40% / 困难（跨文档推理，"I2C 和 SPI 在什么时候选哪个"）20%
- **标注字段：** `question` / `expected_answer` / `target_doc` / `expected_keywords` / `must_cooccur_terms`
- **target_doc 的作用：** 评测时不仅看 LLM 回答质量，还用 `EmbeddingSimilarityChecker` 语义匹配 retrieval_context 与 target_doc，计算 recall_hit（语义命中率）

**DeepEval 四指标的内部实现：**

| 指标 | 判定方式 | judge LLM 的输入 |
|------|----------|------------------|
| context_recall | judge LLM 把 expected_answer 拆成 claim，逐条判断是否能被 retrieval_context 支持 | expected_answer + retrieval_context |
| faithfulness | judge LLM 把 actual_answer 拆成 claim，逐条判断是否能被 retrieval_context 支持 | actual_answer + retrieval_context |
| answer_relevancy | judge LLM 反向生成问题，与原问题算 cosine 相似度 | actual_answer + original_question |
| context_precision | judge LLM 对每个 retrieval chunk 判断是否相关，按位置加权（越靠前权重越高） | question + retrieval_context 列表 |

**recall_hit 语义匹配（独立于 DeepEval）：**
```python
checker = EmbeddingSimilarityChecker(model="text-embedding-v4", base_url="dashscope")
ref_embedding = checker.embed(target_doc_text)   # 内存缓存
ctx_embeddings = checker.embed(retrieval_contexts)
hit = any(cosine(ref_embedding, e) > 0.7 for e in ctx_embeddings)
```
为什么不用精确字符串匹配？因为 chunk 经过 small-to-big 切分后，文本可能被截断或合并，纯字符串匹配会漏判。语义匹配用 0.7 阈值（dashscope v4 的经验值），低于阈值也保留结果但标记 `weak_hit`。

**reference embeddings 缓存：**
- `EmbeddingSimilarityChecker` 内部用 pkl 持久化缓存（B1，`golden_ref_embeddings.pkl`），重启不丢失
- 30 题 × 1 target_doc embedding = 30 个 reference embeddings，评测进程生命周期内复用
- SSL 异常时（dashscope 偶发）脚本内置 3 次重试 + 指数退避，重试成功率 100%
- 评测进程重启后直接命中 pkl，dashscope 调用次数为 0

**评测耗时：**
- 单题耗时：generation（~3s）+ judge LLM 四指标（~4×30s=120s）≈ 2 分钟
- 30 题串行 ≈ 60 分钟
- 支持 `--parallel N` 参数（B2，ThreadPoolExecutor），30 题 4 并发 ~16min

**防止 judge LLM 误判：**
- judge LLM 用 `temperature=0` 保证可复现
- 选 `oc/deepseek-v4-flash` 作为 judge（推理能力 + 中文友好）
- 极端分数（0 或 1）触发 review——judge LLM 偶尔会因为格式问题给 0，需要人工核查
- 评测报告保留 judge 的 raw_response，方便回溯

**评测报告输出（实际路径）：**
```
e:\Desktop\agent\data\test_results\golden_eval_{strategy_tag}_{timestamp}.json
e:\Desktop\agent\data\test_results\golden_eval_{strategy_tag}_{timestamp}.md
```
- `strategy_tag` 默认 `existing`，可由 CLI `--strategy-tag` 覆盖
- JSON 报告结构：顶层含 `summary` + `samples` 数组（每题的 generation / judge raw / 四指标）
- MD 报告：表格化展示每题分数 + 加权总分
- 所有样本嵌套在 JSON 的 `samples` key 下，不分独立文件

**面试话术：**
> "我建了一套量化评估体系。30 道 golden 测试题覆盖 6 份文档的关键知识点，用 DeepEval 四个指标自动评分。每次修改分块或检索策略后跑一次，分数掉了就说明改坏了。这样保证了迭代的可回溯性，不会出现'感觉变好了但说不清好在哪里'。"

---

#### 五、工程化基础设施

> 这一层不在简历文段里展开，但面试官追问"工程化怎么做的"时可以拿出来讲。

**1. MODEL_CONTEXT_WINDOWS 模型上下文映射**

问题：原本代码里 LLM 的 context_window 散落在多个地方，换模型时容易用错窗口大小导致 prompt 截断。

方案：`src/llm/model_registry.py` 集中维护模型 → context_window 映射：
```python
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "deepseek-v4": 256000,
    "deepseek-v4-flash": 256000,
    "qwen3-235b": 256000,
    "claude-3-5-sonnet": 200000,
    # ...
}
DEFAULT_CONTEXT_WINDOW = 128000

def get_context_window(model: str | None) -> int:
    # 1. 精确匹配
    # 2. 去掉 provider 前缀（"oc/deepseek-v4-flash" → "deepseek-v4-flash"）再匹配
    # 3. 模糊匹配（model name contains / contained in known key）
    # 4. 都未命中返回 DEFAULT_CONTEXT_WINDOW
```

**关键设计点：**
- 支持 provider 前缀剥离（`oc/deepseek-v4-flash` → `deepseek-v4-flash`），适配 9router 路由格式
- 三级匹配（精确 → 去前缀 → 模糊）保证大多数模型能命中
- 未命中时回退到 `DEFAULT_CONTEXT_WINDOW=128000`，不会因未知模型崩溃

**2. diskcache 多层缓存**

| 缓存层 | 实现 | key 设计 | 说明 |
|--------|------|----------|------|
| embedding 缓存 | `_EmbeddingCache` + `diskcache.Cache` | `sha256(model\|base_url\|text)` | 持久化到 `data/embedding_cache/cache.db`，model+base_url 都进 key 防止换模型命中旧向量 |
| reference embedding 缓存（评测用） | `golden_ref_embeddings.pkl` | question_id | pkl 持久化（B1），重启不丢失 |
| BM25 索引 | `bm25/<kb_id>.pkl` | kb_id | 持久化分词后语料，避免每次重启重分词 |

**3. PaddleOCR 懒加载**

问题：PaddleOCR import 时会加载 paddlepaddle 框架，启动慢 ~10s。但 OCR 不是主链路功能，默认不用。

方案：
```python
class PaddleOcrParser:
    _ocr_instance = None  # 类变量单例
    
    @classmethod
    def _get_ocr(cls):
        if cls._ocr_instance is None:
            from paddleocr import PaddleOCR  # 懒加载
            cls._ocr_instance = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        return cls._ocr_instance
```
配置 `ocr_enabled=False` 时，`_get_ocr` 永远不被调用，paddleocr 模块不会被 import，启动时间不受影响。

**4. 配置驱动的功能开关**

```python
class Settings(BaseSettings):
    ocr_enabled: bool = False
    reranker_enabled: bool = True
    cache_enabled: bool = True
    # ...
```
所有"可降级"的功能都通过环境变量控制，生产环境出问题时可以临时关闭 reranker 或缓存，不需要改代码重新部署。

**5. 文档化踩坑记录**

维护 `pitfalls.md` 记录踩坑（Bug 编号至 17，含 L2→cosine、RRF 归一化、rebuild 脚本 DB 路径、Tesseract→PaddleOCR 等）。新人 review 时翻一遍能避开大部分雷区。

---

#### 六、性能与可观测性优化（批次二）

> 这一层是批次二做的工程优化，提升检索质量、推理速度、评测效率和可观测性。

**1. Reranker 升级 + FP16 量化（B4 + B7）**

- 模型从 `bge-reranker-base` 升级到 `bge-reranker-large`（560M 参数，精度更高）
- 加载后做 FP16 量化（`model.half()`），对冲 large 模型的速度损失
- 降级链：large + FP16 失败 → 回退 base + FP32 → 标记 `_RERANKER=False` 跳过 reranker
- 日志打印模型参数量和量化状态

**2. HNSW ef_search 调优（B5）**

- ChromaDB 默认 `ef_search=10`，召回率受限
- 显式设为 `ef_search=200`（settings 可配），提升召回率
- 通过 `self._db._collection.set_ef_search()` 设置（chromadb 0.4+ API）
- 有 `hasattr` 守卫，失败时 warning 不阻断

**3. BM25 参数化 + 硬件词典扩充（B3 + B8）**

- `BM25Index` 加 `k1/b` 参数，从 settings 读取（默认 1.5/0.75）
- save/load 持久化 k1/b 到 pkl，旧 pkl 兼容默认值
- 硬件词典从 73 个扩充到 134 个（传感器/显示屏/电源/通信/嵌入式 Linux/工具链/协议补充）
- jieba 注入更多术语，避免 `BMP280` 被切碎成 `B` `MP` `280`

**4. 评测并行化 + reference embedding 持久化（B1 + B2）**

- `EmbeddingSimilarityChecker._cache` 从内存 dict 改为 pkl 持久化（`golden_ref_embeddings.pkl`）
- 评测脚本加 `--parallel N` 参数，用 `ThreadPoolExecutor` 并行
- 30 题评测从串行 60min → 4 并发 ~16min
- reference embedding 第二次评测直接命中 pkl，dashscope 调用次数为 0

**5. MODEL_CONTEXT_WINDOWS 扩展（B6）**

- 新增 `MODEL_MAX_TOKENS` 字典（model → max_tokens 映射）
- 新增 `MODEL_TYPES` 字典（chat / embedding / reranker 分类）
- `get_max_tokens(model)` / `get_model_type(model)` 三级匹配函数
- `client.py` 用 `get_max_tokens` 截断 max_tokens，避免超限
- 前端 `KbCollectionManager.tsx` 同步镜像

**6. Prometheus /metrics 端点（B9）**

- 后端 mount `/metrics` 端点，暴露 Prometheus 格式指标
- 5 个指标：`rag_requests_total` / `rag_retrieval_seconds` / `llm_tokens_total` / `rag_reranker_seconds` / `http_request_seconds`
- `settings.metrics_enabled` 开关控制，默认开启
- chat_routes.py 检索段加 observe（成功 + 错误双路径）
- 所有 observe 包 try/except，指标失败不影响业务

**7. ChromaDB HttpClient 分支（B10）**

- `vector_store.py` 加 `if/else` 分支：`chroma_mode="http"` 用 `chromadb.HttpClient`，默认 `"persistent"` 用 `PersistentClient`
- `rebuild_chroma_cosine.py` 同步加分支
- 默认行为不变，未来上云改环境变量即可切换 client-server 模式

---

### 完整面试话术（10 分钟深度版）

> 以下按面试节奏展开，每段都可以单独抽出来应对追问。
> 加粗部分是核心观点，斜体部分是容易被追问的细节，提前准备好答案。

---

#### 面试官：介绍一下你这个项目

> 用时：约 1.5 分钟

"我做的是一个**面向嵌入式开发者的硬件知识库 AI Agent**。简单说就是让工程师能像聊天一样查芯片手册——不用自己翻几百页的 PDF，直接问问题就行。

项目技术栈是**Python FastAPI 后端 + React 前端**，核心是 RAG 系统。用户上传芯片手册，我们自动分块、向量化、入库。用户提问时，系统从知识库检索相关段落，加上对话历史一起发给 LLM，生成带引用的回答。

我负责的是**RAG 全链路**——从文档上传后的分块策略，到检索管线的设计，再到量化评估体系的搭建。

**当前规模：** 后端 95 个 Python 文件 ~23,700 行代码，前端 62 个 TS/TSX 文件 ~12,800 行。知识库主力是 builtin-001 硬件手册库（6 份 STM32/ESP32/I2C/中断/UART/术语手册）。虽然是个人项目，但我是按开源项目标准做的——完整的接口契约文档、踩坑记录（pitfalls.md，Bug 编号至 17）、量化评测基线（G001 94.3/100）。"

**追问准备：**
- 问项目规模 → "后端 95 文件 ~23,700 行 Python，前端 62 文件 ~12,800 行 TypeScript，docs 200+ 页 markdown"
- 问团队规模 → "个人项目，但我用 8 个线程做任务拆分，00 主控维护架构方向"
- 问技术栈为什么这么选 → "FastAPI 异步性能好且原生支持 SSE；React 生态成熟；ChromaDB 轻量适合个人部署，单机万级 chunks 内性能够用"
- 问为什么不直接用 LangChain → "用了 LangChain 的 RecursiveCharacterTextSplitter 和 Chroma 集成，但检索融合、reranker、评估逻辑自己写——LangChain 的默认 retriever 对中文硬件文档效果不好，且不利于细节调优"
- 问为什么不直接用 Dify / FastGPT → "想深入理解 RAG 全链路，造轮子是学习路径；另外 Dify 对自定义分块、自定义 reranker 的扩展性不够"

---

#### 面试官：分块策略具体怎么做的

> 用时：约 2.5 分钟

"分块是整个 RAG 的基础。块切得太小，LLM 上下文不够；块切得太大，检索精度下降。我做了 **small-to-big 父子切分**来解决这个矛盾。

**小块** 500-800 字符，用于向量检索——只包含最相关的核心内容，保证精度。**大块**是完整的结构单元（比如一节文档，截断到 4000 字符），用于 LLM 生成——上下文充足，模型能理解完整语义。小块检索到大块 ID，通过 metadata 的 `big_chunk_text` 字段关联取到大块文本喂给 LLM。

**metadata 存储方案我选了 inline 存储**——把 big_chunk_text 直接塞进 ChromaDB 的 metadata 字段（截断到 4000 字符），而不是建父子两张表二次查询。理由是 ChromaDB 的 metadata 是 KV 结构不支持 join，二次查询多一次 IO；大块文本平均 2-3KB 内存完全可承受；而且简化了失败处理，不会出现'小命中但大缺失'的半残状态。

实现上我做了两种分块器：

**HybridChunker** 是默认策略，纯规则驱动，不花钱。先按 Markdown 的 ## 标题或空行做结构粗切，得到父块。父块超过 1000 字符的，用 RecursiveCharacterTextSplitter 按分隔符递归切到目标大小。分隔符优先级我实测调过：先 `## ` `### ` 标题，再 `\n\n` 段落，再 `。` `；` 标点，最后才是空格——保证'GPIO 模式配置'这种小节不会被拦腰切断。比如一段 3000 字的 GPIO 章节，先用标题切出'模式配置''速度选择'等小节，超限的再递归切小块，最后输出 small + big 两个粒度。

**AgentChunker** 是付费策略，用 LLM 切分，适合复杂 PDF。有些 PDF 排版混乱，没有清晰标题结构——比如扫描件、表格混排。这时候把 PDF 的文本提取出来发给 LLM，让它分析语义边界并返回 JSON 格式的分块方案。我调了 3 次 LLM（`num_rounds=3`，`temperature=0.1` 保证可复现），三路投票对齐边界——具体算法是：对每页统计它在三次方案中归属的章节，取多数票；争议页（三次归属都不同）标红人工 review；漏页检测用 `set(range(total_pages)) - set(covered_pages)`，差集非空触发补切。

还处理了一个边缘情况：**代码块保护**。用 ` 包裹的代码块提前提取为独立 chunk，内联的 code 用 NUL 字符包裹的占位符（`\x00CB{idx}\x00` 或 `\x00CB{uuid8}\x00`）替换掉再拆分，拆完查 code_map 还原。我特意用 NUL 字符包裹而不是简单的 `<code>`，因为 NUL 不会出现在正常 Markdown 文本里，避免占位符与正文冲突；且每个占位符带唯一 idx/uuid，还原时 O(1) 查表。最后加了一个后处理步骤——小于 100 字符的 tiny chunk 合并到前一个 chunk，但标题行、表格行、代码块结尾符即使短也不合并——它们是结构边界，合并会破坏语义。

效果：3 份手册从 167 chunks 降到 83 chunks，减少了 50%；短块（<100 chars）从 51 降到 8，减少了 84%。剩下的短块都是结构性边界——比如标题行 '### 3.2 GPIO 配置' 本身就是短的，合理不合并。"

**追问准备：**
- 问为什么不用 langchain 的 splitter → "用了，RecursiveCharacterTextSplitter，但我发现它对代码块处理不好，` 被分隔符 \n\n 切断了，所以才做了代码块保护"
- 问 small_chunk_size 怎么确定的 → "跑了对比实验：500/800/1200/2000 四档，跑 golden_dataset 评测。800 的 context_recall 和 context_precision 综合最好"
- 问 Agent 分块成本 → "一份 80 页手册，一次全量送 LLM 约 80K token，3 次调用成本可控"
- 问 LLM 调用失败怎么办 → "返回 AGENT_CHUNK_FAILED 错误码，前端让用户重试或切 hybrid，不自动降级——因为 LLM 分块失败说明文档太复杂，降级到规则切分质量更差"
- 问 inline 存大块文本不会膨胀吗 → "big_chunk_text 截断到 4000 字符，当前规模内存可承受。如果规模到 10 万 chunks，会改成独立 KV 存储（Redis 或 SQLite）"
- 问三路投票为什么是 3 次不是 5 次 → "3 次是成本和稳定性的平衡点。3 次能解决'单次抖动'问题，5 次边际收益递减且成本翻倍"
- 问 temperature=0.1 为什么不是 0 → "0.1 比 0 略有随机性，三路投票才有意义——temperature=0 时三次返回几乎相同，投票退化为单次。0.1 保证有分歧又能多数对齐"

---

#### 面试官：检索管线怎么设计的

> 用时：约 3 分钟

"我做了四层逐步收窄的检索管线——每一层用不同的方法，前一层的输出是后一层的输入。

**第一层：向量检索（ChromaDB + cosine 距离）**

用户问题转成向量，在知识库里找语义最接近的 chunk。用的是用户在前端 KB 配置里设的阿里云 dashscope `text-embedding-v4`（OpenAI 兼容 API，`chunk_size=10` 适配 v4 限流）。embedding 配置按 KB 粒度存 DB（api_key Fernet 加密），后端 `_get_store(kb)` 解密后构造 `HardwareVectorStore`。这里有个坑——ChromaDB 默认用 L2 欧氏距离。L2 看的是两个向量在空间中的直线距离，但文本 embedding 模型是在余弦空间训练的。用 L2 算文本相似度，两个字面接近但语义不同的文本可能拿高分。我改成 cosine 距离后，分数才反映真实的语义相关性。改动很小——一行配置，但效果关键。

```python
collection_metadata = {'hnsw:space': 'cosine'}
```

**HNSW 参数：** 只显式配了 `hnsw:space=cosine`，其余参数（M / ef_construction / ef_search）用 ChromaDB 默认值。当前规模（百级 chunks）默认参数性能够用。如果未来到 10 万级 chunks，会考虑显式调 `ef_search` 提升召回。

**embedding 模型：** 生产 RAG 主链路和评测 recall_hit 都用阿里云 dashscope `text-embedding-v4`（用户前端 KB 配置，按 KB 粒度存 DB）。`.env` 的 `text-embedding-3-small` 仅作 api_key/base_url 兜底，model 名字不兜底——即前端配置后完全绕过 .env。`OpenAIEmbeddings` 实例化时 `tiktoken_enabled=False` + `chunk_size=10` 适配 v4 的限流和 token 计数差异。

**第二层：BM25 关键词检索**

向量检索语义好但精度不够——搜'GPIO 输出速度'，它可能把'GPIO 输入模式'也排前面。BM25 做关键词精确匹配，搜 GPIO 就只看含 GPIO 的 chunk。这里做了几个工程优化：用 jieba 中文分词，注入了约 134 个硬件术语（批次二从 73 扩到 134）——I2C、STM32F4、MPU6050、WS2812。不注入的话 jieba 会把 'I2C' 切碎成 'I' '2' 'C'，BM25 就匹配不上了。

**BM25Okapi 的 k1/b 从 settings 读取**（批次二参数化，默认 `1.5/0.75`），持久化到 pkl，旧 pkl 兼容默认值。

BM25 原始分数范围不可控——相关度高的可能几千分，负分也可能出现（短文档的 IDF 为负）。之前直接把原始分数传到前端，出现过'相关度 2000%'。我做了 top-1 归一化：取 top-1 的分数做分母，把全部结果压缩到 0-1，负分 clamp 到 0。**为什么不用 min-max？** 因为 min-max 会让'都不相关'的查询也出现接近 1.0 的分数，破坏阈值过滤语义；top-1 让最相关结果稳定在 1.0，阈值才有意义。

**第三层：RRF 融合排序**

两个检索各出了 50 条结果，怎么合并？RRF 不看分数，只看排名位置——一条结果在两个榜上都排第 3，比一个第 1 一个第 50 的更可靠。

RRF 公式：score = 1/(k + rank_v + 1) + 1/(k + rank_b + 1)，k=60。**k=60 是 Cormack 2009 论文的经验值**，对相关性分布未知的场景鲁棒。我没调参——因为 RRF 的作用是排序而非打分，k 的微小变化不影响排序结果，只影响分数绝对值。

这里有个严重 bug——RRF 的分数只有 0.016-0.033，不是 0-1。原始代码直接把 RRF 分当作'相关度'传给前端和 LLM，界面显示'相关度 1.6%'。而且阈值过滤 if score >= 0.8 永远为 false，因为最高才 0.033。我修的方法是 RRF 只负责重新排序，显示和过滤仍然用原始的 cosine/BM25 归一化分数（取两者最大值）。这样既拿到了 RRF 的排序质量，又保留了 0-1 的语义可读性。

**第四层：bge-reranker 交叉编码重排**

前三层都是'双编码器'——问题和 chunk 分别编码，算向量距离。速度快但精度粗。reranker 是交叉编码器——问题和 chunk 拼成一个序列送进模型，输出一个精确的相关度分数。精度高但速度慢——算一对要几十毫秒，全量算不现实。

所以我把 reranker 放最后：前三层粗筛到 top-20，reranker 只算这 20 条。**用 `BAAI/bge-reranker-large` CrossEncoder（max_length=512，FP16 量化）**，`model.predict(pairs)` 一次性传入 20 对，sentence-transformers 内部按 batch 处理。

**懒加载单例 + 降级：** reranker 首次调用时加载 large+FP16（~10s，~2GB RAM 估算），全局单例复用。降级链：large+FP16 失败 → 回退 base+FP32 → 标记 `_RERANKER=False` 跳过 reranker，caller 保留 RRF 顺序——保证服务可用性。

**多阶段降级链：** reranker 加载失败 → 保留 RRF 排序取 top-5；reranker predict 异常 → 返回原序 + warning；BM25 异常 → 仅用向量检索 + 标记'检索降级'；向量检索异常 → 返回'知识库暂时不可用'。每一层降级都打日志。

**另外做了 diskcache 向量缓存：** key 是 `sha256(model|base_url|text)` 整体哈希，model 和 base_url 都进 key 防止换模型/换 endpoint 后命中旧向量。`embed_documents` 批量查缓存，miss 的批量调 API 再批量写缓存。持久化到 `data/embedding_cache/cache.db`。"

**追问准备：**
- 问为什么用 RRF 不用别的融合方法 → "RRF 简单、无参数训练、效果稳定。lightweight 排序学习也是选项，但我没有标注数据。学习排序（LambdaMART）需要大量 query-chunk-relevance 标注，成本太高"
- 问 reranker 具体用的哪个模型 → "BAAI/bge-reranker-large，sentence-transformers CrossEncoder，FP16 量化跑。首次加载 ~10s，~2GB RAM。降级链 large+FP16 → base+FP32 → 跳过"
- 问 cosine 修了之后分数怎么变的 → "修之前同义句可能 0.6，不同义句可能 0.7。修之后同义句 0.8+，不同义句 <0.5，差距明显拉开"
- 问 BM25 负分问题 → "rank_bm25 的 BM25Okapi 实现，IDF 为负时返回负分。我在归一化前先 clamp 负分到 0"
- 问 HNSW 参数怎么调的 → "显式配了 hnsw:space=cosine + ef_search=200（批次二，默认仅 10）。M / ef_construction 仍用默认。当前规模百级 chunks 够用"
- 问 reranker 失败降级会不会影响质量 → "会，但 reranker 提升 context_precision 明显，降级到 RRF 后 precision 回到 RRF 水平。降级是保可用性，不是保最优"
- 问缓存 key 为什么带 model 和 base_url → "换 embedding 模型或换 endpoint 时，旧向量维度和语义空间都变了，必须失效。带 model+base_url 后，换任一项自动 miss"
- 问为什么 embedding 用 dashscope v4 → "v4 中文精度高，且阿里云百炼 OpenAI 兼容 API 接入简单。按 KB 粒度配置存在 DB 里，api_key Fernet 加密。`tiktoken_enabled=False` + `chunk_size=10` 适配 v4 的限流和 token 计数差异"

---

#### 面试官：引用的 [srcN] 怎么实现的

> 用时：约 1.5 分钟

"做引用溯源的动机很直接——LLM 可能乱编来源。我做了 **strict citation** 机制。

每个检索到的 chunk 分配一个序号 src1、src2。SSE 流式输出时，LLM 生成的文本中直接插入 [src1][src2] 角标。与此同时 SSE 的 source 事件携带对应 chunk 的完整信息——文档名、原文片段、相关度分数等 18 个字段。前端渲染后，角标可点击展开或跳转。

**SSE 事件协议我设计了六种：** `text` 事件流式推送 token；`source` 事件携带 chunk 元信息（id/title/doc/page/chunk_index/score/score_percentage/relevance_level/citation/excerpt/kb_id/kb_name 等 18 字段，前端缓存到 srcMap）；`thinking` 事件推送 LLM 思考过程（支持 reasoning 模型）；`tool` 事件推送工具调用；`done` 事件是终止信号 + token 用量统计；`error` 事件带错误码 + 消息，前端显示重试按钮。

实现上就是在构建 system prompt 时，把检索结果按 'src1: 原文\nsrc2: 原文' 的格式拼进去，告诉 LLM '引用时标注来源序号'。实测主流模型（GPT-4o、DeepSeek-V4、Qwen2.5）在这种格式下遵守率 >95%，LLM 输出后不需要后处理——模型自然就会在引用内容后面加 [srcN]。

**前端渲染逻辑：** 流式拼接时用正则 `/\[src(\d+)\]/g` 实时匹配 token 中的角标，命中后替换为 `<cite data-sid='srcN'>` 组件，点击从 srcMap 取 chunk 信息弹窗显示文档名 + 原文 + 相关度。

**兜底机制：** LLM 不遵守时（约 5% 概率），流式结束后正则扫描，未引用任何 src 的回答追加'参考来源：[src1][src2]'；引用了不存在的 src（如 [src9] 但只有 5 个 chunk）则删除该角标。不重试 LLM 调用——成本高且可能再次失败。

**srcN 复用规则：** 同一 chunk 在一次回答中被多次引用，复用同一个 src 编号，不去重也不递增——符合学术引用习惯，用户点击 [src1] 看到的是同一出处。

效果是用户看到'根据手册[src1]，GPIO 输出速度分四档...'，点击 [src1] 就能看到原始段落。解决了黑盒生成不可追溯的问题。"

**追问准备：**
- 问 LLM 不遵守引用格式怎么办 → "主路径依赖 LLM 自然遵守（>95%）；兜底 1 是流式后正则扫描补'参考来源'；兜底 2 是删除引用了不存在 src 的角标。不重试 LLM——成本高且不保证成功"
- 问同一个段落被多次引用怎么办 → "同一 chunk 复用同一个 src 编号，不去重。符合学术引用习惯"
- 问角标渲染性能怎么样 → "正则匹配在 token 流式拼接时实时做，单次匹配 O(token_length)，对几十字节的 token 无感。前端用 React state 维护 srcMap，渲染时 O(1) 查找"
- 问 SSE 断连怎么办 → "前端监听 EventSource 的 onerror，断连后保留已生成的部分回答，提示用户'连接中断，可重试'。重试时带 session_id 让后端续接"
- 问 thinking 事件是给谁用的 → "支持 reasoning 模型（如 deepseek-r1），把思考过程单独流式推送，前端可折叠显示。不和正文混在一起避免污染引用"

---

#### 面试官：怎么保证改完不会变差

> 用时：约 2 分钟

"我建了一套量化评估体系。

**测试集：** 30 道 golden 测试题，覆盖 6 份硬件文档——01-stm32-gpio（5 题）/ 02-esp32-wifi（5 题）/ 03-i2c-protocol（5 题）/ 04-cortexm-interrupt（5 题）/ 05-uart-serial（4 题）/ 06-hardware-terms-glossary（6 题）。难度分布——简单（事实查询'GPIO 输出速度有几档'）40% / 中等（配置流程'推挽输出怎么配置'）40% / 困难（跨文档推理'I2C 和 SPI 什么时候选哪个'）20%。每道题标注了 `expected_answer` / `target_doc` / `expected_keywords` / `must_cooccur_terms`。

**评分指标：** 用 DeepEval 框架的四个指标做加权评分——context_recall 30%（检索覆盖了答案所需的内容）、faithfulness 25%（回答不编造）、answer_relevancy 25%（回答切题）、context_precision 20%（检索结果没有噪声）。

**DeepEval 内部怎么算的：** context_recall 是 judge LLM 把 expected_answer 拆成 claim，逐条判断能否被 retrieval_context 支持；faithfulness 类似但拆的是 actual_answer；answer_relevancy 是 judge LLM 反向生成问题与原问题算 cosine；context_precision 是 judge LLM 对每个 chunk 判断相关性，按位置加权（越靠前权重越高）。

**recall_hit 是我额外加的语义匹配：** 用 `EmbeddingSimilarityChecker`（dashscope text-embedding-v4）算 target_doc 和 retrieval_context 的 cosine 相似度，>0.7 算命中。为什么不用字符串匹配？因为 small-to-big 切分后 chunk 文本可能被截断或合并，纯字符串匹配会漏判。reference embedding 用 pkl 持久化（`golden_ref_embeddings.pkl`），重启不丢失。

**评测流程：** 每次修改分块参数或检索策略后，跑一遍完整的 golden dataset。脚本自动给每个 KB 上传文档、提问（调 /api/chat SSE 拿 actual_output + retrieval_context）、评分、生成报告。报告输出到 `data/test_results/golden_eval_{strategy_tag}_{timestamp}.json` 和 `.md`，所有样本嵌套在 JSON 的 `samples` 数组下。

**当前性能：** 单题耗时约 2 分钟（generation 3s + judge LLM 四指标 120s），30 题串行约 60 分钟；支持 `--parallel N` 并行（批次二，4 并发 ~16min）。

**防 judge LLM 误判：** judge 用 `temperature=0` 保证可复现；选 `oc/deepseek-v4-flash` 作为 judge（推理能力 + 中文友好）；极端分数（0 或 1）触发 review——judge LLM 偶尔会因为格式问题给 0，需要人工核查；评测报告保留 judge 的 raw_response 方便回溯。

**效果：** 建立了一个回归基线。修改后分数掉了，马上知道改坏了。首题评分 94.3/100（CR=1.0 FA=1.0 AR=1.0 CP=0.72），作为基线持续监控。

这也让我做决策时有数据支撑——比如 small_chunk_size 从 500 调到 800，指标提升了几个点，有理有据。"

**追问准备：**
- 问 DeepEval 具体怎么用的 → "用了它的四个指标，judge LLM 作为评分模型。输出是 0-1 的分数，加权汇总。我额外加了 recall_hit 语义匹配，DeepEval 本身不做这个"
- 问为什么用 DeepEval 不用 RAGAS → "DeepEval 的指标更细（faithfulness 等），RAGAS 相对粗粒度。DeepEval 还能集成 pytest，方便 CI 集成"
- 问 30 题是自己写的还是生成的 → "我根据 6 份硬件文档的关键知识点写的，覆盖寄存器配置、协议细节、常见问题。也考虑过用 LLM 生成，但生成题容易偏向'模型熟悉的'，分布不均"
- 问 judge LLM 会不会有偏见 → "会。比如对长回答倾向给高 faithfulness。我用了 temperature=0 减少随机性，且极端分数触发 review。长期方案是多 judge 投票，当前单 judge 够用"
- 问 94.3 分是不是过拟合 → "G001 是单题分数，不是 30 题平均。G001 94.3 作为单题基线，整体基线要看 30 题平均"
- 问怎么防止 golden dataset 泄漏 → "golden_dataset 不进知识库——评测时问的问题是'用户视角'，target_doc 是'答案出处'，两者都不在 retrieval_context 的训练数据里。模型生成时只能从 KB 检索，不能记忆 golden"
- 问 reference embedding 缓存重启丢失怎么办 → "已用 pkl 持久化（`golden_ref_embeddings.pkl`），第二次评测直接命中缓存，dashscope 调用次数为 0"

---

#### 面试官：遇到过最棘手的 bug 是什么

> 用时：约 1.5 分钟

"最棘手的是 RRF 分数归一化的 bug，因为它**静默地坏了很久没被发现**。现象是界面上所有 RAG 结果的相关度都显示 1.6%，阈值过滤形同虚设。但系统功能正常——能搜到结果、能回答。所以大家都没注意到分数显示是错的。

排查发现 RRF 融合后的分数是 0.016-0.033，但下游代码假设它是 0-1。三处地方同时坏：前端百分比显示、relevance level 判断（永远是 low）、LLM context 里的相关度描述。

修复方案是 RRF 只负责排序，显示和过滤改用原始的 cosine/BM25 分数。改动不大，但需要理解整条数据流才能定位。

**定位过程：** 第一步看日志发现 score 值只有 0.016，正常 cosine 相似度应该在 0-1；第二步追代码发现 `rrf_fusion` 的输出直接当作 score 传出去了；第三步看 RRF 公式 `1/(k + rank + 1)`，k=60 时 top-1 的分数上限就是 1/61 ≈ 0.016，理论最大值就是 0.033；第四步意识到原始设计者把 RRF 当成了归一化分数，但 RRF 的分数尺度由 k 决定，不是 0-1。

**修复后做了预防：** 给所有 score 字段加单元测试断言 `0 <= score <= 1`，CI 跑评测时如果分数越界直接 fail。

类似的还有一个 ChromaDB 默认 L2 距离的问题——它也是静默故障，系统能跑，但分数语义不对。这些 bug 的共同特点是：**功能没断，数据错了**。这种 bug 最危险，因为不会被察觉，但会持续误导下游决策——比如阈值过滤永远不过滤、相关度排序失去意义。"

**追问准备：**
- 问怎么发现的 → "看日志里 score 值只有 0.016，正常 cosine 相似度应该在 0-1。追代码发现 rrf_fusion 的输出直接当作 score 传出去了"
- 问修了多久 → "定位半小时，修了 10 分钟。但写单元测试防回归又花了 20 分钟"
- 问怎么预防类似问题 → "三层：1) 所有 score 字段加断言 0-1；2) CI 跑 golden_dataset 评测，分数异常 fail；3) 写了 pitfalls.md 文档记录踩坑，新人 review 时翻"
- 问 ChromaDB L2 bug 怎么发现的 → "做评测时发现 context_precision 分数异常——检索结果按 L2 排序和按 cosine 排序差异很大，L2 把不相关的也排到前面。查 ChromaDB 文档发现默认是 L2"

---

#### 面试官：工程化和可维护性怎么做的

> 用时：约 1.5 分钟

"这块虽然不在简历文段里展开，但我觉得是项目能持续迭代的关键。做了几件事：

**1. MODEL_CONTEXT_WINDOWS 模型上下文映射。** 原本代码里 LLM 的 context_window 散落在多处，换模型时容易用错窗口大小导致 prompt 截断。我在 `src/llm/model_registry.py` 集中维护 `MODEL_CONTEXT_WINDOWS` 字典（model → context_window，如 deepseek-v4-flash=256000），`get_context_window()` 函数支持三级匹配：精确匹配 → 去掉 provider 前缀（`oc/deepseek-v4-flash` → `deepseek-v4-flash`）→ 模糊匹配 → 都未命中回退到 `DEFAULT_CONTEXT_WINDOW=128000`。

**2. diskcache 多层缓存。** embedding 缓存用 `_EmbeddingCache` 包装 `OpenAIEmbeddings`，底层 diskcache 持久化到 `data/embedding_cache/cache.db`，key 是 `sha256(model|base_url|text)` 整体哈希，model+base_url 都进 key 防止换模型命中旧向量；`embed_documents` 批量查缓存 + miss 批量调 API + 批量写缓存。评测用 reference embedding pkl 持久化（`golden_ref_embeddings.pkl`），重启不丢失。BM25 索引持久化到 `bm25/<kb_id>.pkl`。

**3. 配置驱动的功能开关。** 所有'可降级'功能都通过环境变量控制：`ocr_enabled`、`reranker_enabled`、`cache_enabled`。生产出问题时可以临时关闭 reranker 或缓存，不需要改代码重新部署。

**4. PaddleOCR 懒加载。** PaddleOCR import 时会加载 paddlepaddle 框架，启动慢 ~10s。我用了类变量单例 + 懒加载——`_ocr_instance = None`，配置 `ocr_enabled=False` 时 `_get_ocr` 永远不被调用，paddleocr 模块不会被 import，启动时间不受影响。

**5. 文档化踩坑记录。** 维护 `pitfalls.md` 记录踩坑（Bug 编号至 17，含 L2→cosine、RRF 归一化、rebuild 脚本 DB 路径、Tesseract→PaddleOCR 等）。新人 review 时翻一遍能避开大部分雷区。"

**追问准备：**
- 问为什么不直接用 Redis → "diskcache 是 SQLite-based，单机部署零运维；Redis 要额外部署且持久化配置复杂。当前规模（GB 级缓存）diskcache 够用，未来分布式部署再切 Redis"
- 问缓存失效策略 → "embedding 缓存不自动失效——同一文本同一模型向量不变；换模型时带 model+base_url 进 key 自动 miss。BM25 索引在 KB 文档变更时 rebuild"
- 问 model_registry 怎么处理模型下线 → "未命中的模型回退到 DEFAULT_CONTEXT_WINDOW=128000，不会崩溃。如果模型实际 context_window 小于默认值，会由 LLM API 返回 context length exceeded 错误兜底"
- 问 OCR 懒加载为什么不直接用 lazy 装饰器 → "类变量单例比装饰器更显式，代码可读性更好。且能在类级别做 mock 注入测试（测试时替换 _ocr_instance）"

---

#### 面试官：性能优化做了哪些

> 用时：约 1.5 分钟

"性能优化分两层：批次一的基础优化 + 批次二的进阶优化。

**检索层（批次一）：** diskcache 向量缓存持久化（key 是 `sha256(model|base_url|text)`），重复 query 命中缓存跳过 API 调用；BM25 索引持久化到 pkl，避免每次重启重分词；ChromaDB 嵌入式模式无网络开销。

**推理层（批次一）：** bge-reranker 懒加载单例，首次加载后复用；`predict(pairs)` 一次性批量传入 top-20 pairs，sentence-transformers 内部按 batch 处理；reranker 只对 top-20 算而非全量，避免 N² 复杂度。

**检索质量（批次二）：** HNSW `ef_search` 从默认 10 提到 200，提升向量召回率；BM25 参数化支持 `k1/b` 配置，硬件词典从 73 扩到 134 个术语（传感器/显示屏/电源/通信/嵌入式 Linux 等），jieba 分词更精确。

**推理精度+速度（批次二）：** reranker 从 base 升级到 large（560M 参数）+ FP16 量化对冲速度损失；降级链 large+FP16 → base+FP32 → 跳过 reranker。

**评测效率（批次二）：** reference embedding 从内存 dict 改为 pkl 持久化，第二次评测直接命中缓存；评测脚本加 `--parallel N` 参数，30 题从串行 60min → 4 并发 ~16min。

**可观测性（批次二）：** 后端 mount `/metrics` 端点暴露 Prometheus 指标（rag_requests_total / rag_retrieval_seconds / llm_tokens_total / rag_reranker_seconds / http_request_seconds），所有 observe 包 try/except 不影响业务。

**模型管理（批次二）：** `MODEL_CONTEXT_WINDOWS` 扩展为三个并行字典（context_window / max_tokens / type），`client.py` 用 `get_max_tokens` 截断避免超限；前端同步镜像。

**部署准备（批次二）：** ChromaDB 加 HttpClient 分支代码，默认仍用 PersistentClient，未来上云改环境变量即可切换 client-server 模式。

**整体端到端延迟：** 单次 RAG 查询（用户提问到回答开始流式输出）约 1-2 秒——检索 <100ms（缓存命中更快）+ LLM 首 token ~1s。用户感知延迟主要是 LLM 部分，检索层已经优化到几乎无感。"

**追问准备：**
- 问为什么不用 GPU → "个人项目资源有限，CPU 推理够用。reranker large + FP16 在 CPU 上单次 predict 几十毫秒可接受。如果上 GPU，vLLM 能再快 3-5x"
- 问缓存命中率怎么测的 → "打日志记录 hit/miss 计数，跑 100 次真实 query 统计。稳态后命中率取决于 KB 更新频率——KB 不变时新 query 的 query embedding miss，但 chunk embedding 全命中"
- 问 reranker 性能瓶颈在哪 → "首次加载 large 模型 ~10s + ~2GB RAM 是主要开销。运行时 predict 20 pairs FP16 几十毫秒可接受。降级链保证可用性"
- 问 ef_search=200 会不会慢 → "ef_search 是查询时搜索宽度，200 比 10 多搜 20 倍候选，但 HNSW 是对数级复杂度，实测延迟从 ~10ms 到 ~30ms，可接受"
- 问 FP16 在 CPU 上有什么坑 → "PyTorch CPU FP16 支持不如 GPU 完整，部分算子会 fallback 到 FP32。实测 bge-reranker-large 的 CrossEncoder 在 CPU FP16 下能跑，速度比 FP32 快 ~30%。如果报错就降级 FP32"
- 问 Prometheus 指标怎么用 → "当前只暴露 /metrics 端点，没部署 Prometheus 服务器。未来上 Grafana 时直接 scrape 这个端点即可。指标含 QPS/延迟/token 用量，方便定位性能瓶颈"
- 问评测并行化有什么坑 → "DeepEval 的 judge LLM 设置了全局 os.environ，必须在并行前完成。EmbeddingSimilarityChecker 的 _cache 是共享可变状态，CPython GIL 保证原子性，最坏情况是重复调一次 embedding API"

---

#### 面试官：怎么部署和监控的

> 用时：约 1 分钟

"个人项目部署比较轻量：

**部署：** 后端 FastAPI + Uvicorn 单进程，端口 58080；前端 Vite dev server，端口 5173；ChromaDB 嵌入式模式（SQLite 持久化），无独立服务。整套本地 Windows 跑，未来上云可以 Docker 化。

**健康检查：** `/health` 端点返回 `{"status": "healthy"}`，Docker/K8s 可作为 liveness probe。

**日志：** Python logging，文件 rotate。关键事件打 INFO（检索降级、缓存命中），错误打 ERROR（LLM 调用失败、模型加载失败）。SSE 错误事件前端可见，用户感知。

**监控：** 当前没有 Prometheus/Grafana，靠 golden_dataset 评测做'质量监控'——定期跑评测，分数下降触发 review。这是质量层面的监控，不是性能监控。

**未覆盖：** 没有分布式追踪（OpenTelemetry）、没有 QPS 监控、没有告警系统。这些是生产级需求，当前规模不需要。"

**追问准备：**
- 问为什么不上 K8s → "单机部署够用，K8s 运维成本高。未来如果多用户并发上来了，第一步是 ChromaDB 切 client-server 模式，第二步才上 K8s"
- 问日志怎么查 → "logging 模块 + 文件 rotate，按时间或关键词 grep。没有 ELK 栈，规模不需要"
- 问评测算监控吗 → "算质量监控，不算性能监控。性能监控要看 P99 延迟、QPS、错误率，这些当前没做"

---

#### 完整串讲（30 秒总结）

> 如果面试官说"时间有限，你用 30 秒总结一下"

"我做了一个硬件知识库的 RAG 系统，核心是四件事。第一，small-to-big 分块——小块检索保证精度，大块生成保证上下文，用 HybridChunker 和 AgentChunker 两种策略。第二，四阶检索管线——向量 cosine 召回、BM25 关键词、RRF 融合、bge-reranker 精排，逐步收窄。第三，[srcN] 引用溯源，每个回答可追溯到原始文档。第四，量化评估体系——30 题 golden dataset + DeepEval 四指标，防止回归。"
