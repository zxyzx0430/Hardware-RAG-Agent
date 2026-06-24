# Agent 工程实现指南

> 最后更新：2026-06-22
> 协议基线：OpenAI Function Calling（全线统一）

---

## 一、RAG → Agent 衔接

### 现状（Phase 2）

```
用户输入 → 自动 search_kb → 拼进 system prompt → LLM 回答
```

### 目标（Phase 3）

```
用户输入 → Agent 收到问题 → 判断：
  需要查手册 → 调 search_kb 工具 → 看结果 → 可能再调其他工具 → 回答
  不需要     → 直接回答
```

### 迁移原则

RAG 知识库层**完全不动**：

```
vector_store.py → search() / hybrid_search()    ✅ 不动
kb_routes.py → upload / list / delete / search   ✅ 不动
```

新增一层包装：

```
search_kb 工具 → 调用 vector_store.search()，包装成 OpenAI tool 格式
Agent loop → 替换 chat_routes.py 里的自动 RAG 注入
```

---

## 二、Agent Loop（ReAct 循环）

### 2.1 标准流程

```
LLM 返回 tool_calls
  → 逐个执行工具
  → tool_result 放回 messages（tool role）
  → 重新调 LLM
  → LLM 要么再调工具，要么输出最终回答
  → 循环直到 max_iterations 或 LLM 不调工具了
```

### 2.2 OpenAI 消息流转

```
第一轮：
  messages = [system, user_message]
  response = llm.chat(messages, tools=[...], tool_choice="auto")
  → response.tool_calls

第二轮：
  messages += assistant_message（含 tool_calls）
  for tool_call in response.tool_calls:
      result = execute_tool(tool_call)
      messages += {role: "tool", tool_call_id: id, content: result}
  response = llm.chat(messages, tools=[...])
  → 还有 tool_calls → 继续
  → response.content 有文本 → 最终回答
```

### 2.3 循环 vs LangGraph 的路线选择

做个明确的决定：

```
Phase 3 的顺序：
  第一步：手写一个最简单的 AgentLoop 类
          → 目的是：理解循环逻辑、排查问题方便
          → 验证通过后，保留作为 fallback/调试模式
          
  第二步：替换成 LangGraph
          → 用 StateGraph + streaming 做生产版本
          → 手写版退役，或做成"简易模式"开关
```

两个不共存。手写版是学习工具，LangGraph 版是最终方案。

---

## 三、死循环防护

### 3.1 防护层

| 防护 | 默认值 | 说明 |
|------|--------|------|
| 最大迭代次数 | 10 | tool_call 轮数上限 |
| 单工具超时 | 30s | 每个工具单独算 |
| 总执行时间 | 120s | 整个 Agent 调用 |
| 重复调用检测 | 3 次同工具+同参数 | 避免 LLM 反复调同一个搜索 |
| 循环模式检测 | 3 轮 | 检测 A→B→A→B 模式 |

### 3.2 循环模式检测

只查上一次不够，要查最近的 N 次：

```python
class AgentLoop:
    def __init__(self):
        self.tool_history = []  # [("search_kb", '{"query":"ESP32"}'), ("execute_code", ...)]
        self.iteration = 0
        self.max_iterations = 10

    def _detect_loop_pattern(self, name, args_str) -> bool:
        """检测循环模式：A→B→A→B 或 A→A→A→A"""
        self.tool_history.append((name, args_str))
        recent = self.tool_history[-4:]  # 看最近 4 次
        if len(recent) < 4:
            return False
        # 模式 1：连续 3 次同工具同参数
        if len({(n, a) for n, a in recent[-3:]}) == 1:
            return True
        # 模式 2：A→B→A→B 交替
        if recent[0][0] == recent[2][0] and recent[1][0] == recent[3][0]:
            return True
        # 模式 3：连续 4 次不同工具但没有出最终回答
        return False
```

### 3.3 降级输出

```
死循环打断 → "我尝试了多次搜索仍无法定位到准确信息。
               已搜索的关键词：xxx。建议换个说法重试。"

工具执行失败 → "搜索知识库时遇到错误，以下是已有信息：xxx"
```

---

## 四、工具调用管线

### 4.1 执行管线

```
tool_call 到达
  → Schema 校验（参数格式对不对）
  → 安全检查（是否是 destructive 操作）
  → try/except 执行工具
  → 失败 → 返回错误消息给 LLM（不是抛异常）
  → 成功 → 格式化输出，截断长度
```

### 4.2 关键：工具执行失败不能抛异常

LLM 等 tool_result 等不到就会卡住。所以：

```python
async def safe_dispatch(tool_call) -> str:
    try:
        result = await tool_router.dispatch(tool_call)
        return format_result(result)
    except TimeoutError:
        return "[工具执行超时：搜索知识库未在 30 秒内返回]"
    except Exception as e:
        return f"[工具执行失败：{sanitize_error(str(e))}]"
```

LLM 看到这条错误消息后，可以决定重试、换方式问、或者直接用自己的知识回答。**不会卡死。**

### 4.3 工具结果截断

每个工具结果太长会撑爆上下文。必须截断：

```python
MAX_TOOL_OUTPUT_LENGTH = 3000  # 字符

def truncate_output(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_LENGTH:
        return text
    return text[:MAX_TOOL_OUTPUT_LENGTH] + f"\n...（省略 {len(text) - MAX_TOOL_OUTPUT_LENGTH} 字）"
```

每一轮 messages 总长度也做预算：

```python
MAX_CONTEXT_TOKENS = 32000

def estimate_tokens(messages: list) -> int:
    return sum(len(str(m)) // 2 for m in messages)

def trim_messages(messages: list, max_tokens: int = MAX_CONTEXT_TOKENS):
    while estimate_tokens(messages) > max_tokens:
        # 从 early 对话开始删（不删 system）
        messages.pop(1)  # 删最早的非 system 消息
```

---

## 五、工具规范

### 5.1 每个工具必须有的字段（统一格式）

```python
class BaseTool:
    """所有工具的基础类"""

    name: str                              # 工具名，全小写+下划线
    description: str                       # 工具描述
    param_schema: dict                      # JSON Schema for 参数
    is_read_only: bool = True              # 是否只读
    is_destructive: bool = False           # 是否有破坏性
    timeout_ms: int = 30000                # 超时

    async def run(self, args: dict) -> dict:
        """返回 {"output": str, "sources": list, "duration_ms": int}"""
        raise NotImplementedError

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.param_schema,
            }
        }
```

### 5.2 description 写法

```
差：搜索知识库

好：搜索硬件知识库，返回芯片参数、引脚定义、接线方案、电气特性、
    驱动代码示例等文档片段。适合回答硬件参数对比、配置方式、
    兼容性判断等问题。不适用于通用编程问题、数学计算、翻译。
```

规则：
- 写清楚**什么时候调**
- 写清楚**什么时候不调**
- 包含具体的搜索词示例（LLM 学习你举的例子）

### 5.3 输出格式

```python
{
    "output": str,        # 给 LLM 读的执行结果
    "sources": list,      # 来源（给前端展示用，可选）
    "duration_ms": int,   # 耗时
}
```

---

## 六、SSE 流式 + LangGraph（最关键）

### 6.1 直接用 .invoke() 的问题

```python
result = app.invoke({...})    # 等全部跑完才返回值
```

Agent 调 3 轮工具 → 前端等 10 秒才看到第一个字 → 用户以为卡了。

### 6.2 正确的做法：astream_events

```python
from langgraph.graph import StateGraph, END

# 构建图（和标准写法一样）
graph = StateGraph(GraphState)
graph.add_node("agent", call_model)
graph.add_node("tools", call_tool)
graph.add_conditional_edges("agent", router)
graph.add_edge("tools", "agent")
graph.set_entry_point("agent")

app = graph.compile()

# 流式执行 + 推送到前端
async for event in app.astream_events(input_state, version="v2"):
    kind = event["event"]
    node = event.get("name", "")

    if kind == "on_chat_model_stream":
        chunk = event["data"]["chunk"]
        if chunk.content:
            yield sse_event("text", {"content": chunk.content})

    elif kind == "on_tool_start":
        yield sse_event("tool_start", {
            "name": event["name"],
            "args": event["data"].get("input"),
        })

    elif kind == "on_tool_end":
        yield sse_event("tool_result", {
            "name": event["name"],
            "output": event["data"].get("output", "")[:500],
        })
```

前端看到的顺序：

```
用户输入 "ESP32 最大 GPIO 电流"
  SSE: tool_start → "正在搜索知识库..."
  SSE: tool_start → "正在执行代码..."
  SSE: text → "ESP32 的 GPIO 最大输出电流是 40 mA..."
```

### 6.3 LangGraph stream 模式的三个事件

你实际只需要三个事件：

| LangGraph 事件 | 你对应的 SSE 事件 | 前端展示 |
|---------------|------------------|---------|
| on_chat_model_stream | text | 流式打字 |
| on_tool_start | tool_start | 显示工具调用 |
| on_tool_end | tool_result | 显示工具结果摘要 |

---

## 七、手写版 vs LangGraph 版的清晰边界

```
手写 AgentLoop（调试用，验证通过后退役）：
  └─ 文件：backend/src/agent/agent_loop_debug.py
  └─ 启用：环境变量 DEBUG_AGENT=true
  └─ 目的：出问题时可以直接断点调试，替换 LangGraph

LangGraph 版（生产用）：
  └─ 文件：backend/src/agent/agent_graph.py
  └─ 启用：默认
  └─ 运行时：astream_events 流式推送
```

两者调用同一个工具注册表，不重复。

---

## 八、文件清单

### 修改的文件

| 文件 | 改什么 |
|------|--------|
| chat_routes.py | 删除自动 RAG 注入，改成 Agent 调用 |
| tool_router.py | 工具继承 BaseTool，加 to_openai_tools() |
| client.py | chat_stream() 加 tools/tool_choice 参数 |

### 新增的文件

| 文件 | 内容 |
|------|------|
| src/agent/base_tool.py | BaseTool 基类 + to_openai_tool() |
| src/agent/agent_graph.py | LangGraph StateGraph + stream |
| src/agent/agent_loop_debug.py | 手写循环（调试用） |
| src/agent/tools/search_kb.py | search_hardware_kb 工具 |
| src/agent/tools/execute_code.py | code_executor 工具 |

### 不动的文件

| 文件 | 原因 |
|------|------|
| src/rag/vector_store.py | RAG 搜索逻辑，只被工具包装 |
| app/api/kb_routes.py | 知识库管理接口 |
| 前端 SSE handler | 只加 tool_start/tool_result 事件类型 |

---

## 九、实现顺序

```
准备工作（Phase 2 收尾时做好，不耽误 RAG）：
  └─ tool_router.py：工具继承 BaseTool
  └─ to_openai_tools() 方法
  └─ client.py：chat_stream() 加 tools 参数

第一步：手写 AgentLoop 验证循环逻辑（半天）
  └─ 不接 LangGraph
  └─ 验证：LLM → tool_call → 执行 → 回流 → 回答

第二步：手写版跑通 SSE 流式（半天）
  └─ tool_start / tool_result / text 事件全部正确

第三步：换成 LangGraph + astream_events（1 天）
  └─ StateGraph + 流式推送
  └─ 死循环防护

第四步：手写版退役，LangGraph 上线（半天）
  └─ 手写版保留一个 DEBUG_AGENT 开关
  └─ 默认为 LangGraph

---

## 十、Agent 常见问题

### 10.1 LLM 就是不调工具

| 现象 | 原因 | 修法 |
|------|------|------|
| 该搜索的时候不搜索，自己瞎编 | description 写得太模糊 | description 里加具体搜索词示例 |
| LLM 用工具名来造句而不是调用 | tool_choice="auto" 太松 | 改成 tool_choice="any" 或 tool_choice={"type":"function","function":{"name":"search_kb"}} |
| 换了模型就不调了 | 小模型 function calling 能力弱 | 同一个 prompt 在不同模型上表现不一样，qwen-72b 可能听话但 deepseek-v3 不听话。降级方案：加一层规则——如果 LLM 输出不含 tool_call 且问题明显需要搜索，由后端强制补一次搜索 |

### 10.2 LLM 死活用同一个参数调工具

常见场景：搜了一次找不到 → 换了个说法再搜 → 又找不到 → 再换说法 → 循环。

```
LLM: search_kb("GPIO")
结果：没找到
LLM: search_kb("GPIO 引脚")
结果：没找到
LLM: search_kb("GPIO 引脚定义")
结果：没找到
LLM: search_kb("什么是 GPIO")
```

这不算"同一个工具同参数"（query 每次不同），所以重复检测抓不到。

**修法**：结果相似度检测。如果连续 3 次搜索结果几乎一样（都是空），打断：

```python
def _detect_stagnation(self, last_results: list) -> bool:
    """连续多次搜索结果没变化 → 停滞"""
    if len(self.result_history) < 3:
        self.result_history.append(last_results)
        return False
    # 比较最后三次结果是否几乎一样
    recent = self.result_history[-3:]
    if all(r == recent[0] for r in recent):
        return True
    return False
```

### 10.3 工具返回了内容但 LLM 不引用

```
search_kb 返回了 5 段关于 GPIO 电流的文档
LLM 回答："我不确定 ESP32 的 GPIO 电流是多少"
```

**原因**：tool_result 格式不对。如果返回的是 JSON 块或原始文档片段，LLM 可能不认为那是"答案"。

**修法**：tool 的 output 用**陈述句**写，不是抛原始数据：

```
差：
{"chunks": [{"content": "...", "score": 0.85}]}

好：
根据 ESP32 技术参考手册.pdf 第 28 章：
GPIO 最大输出电流为每个引脚 40 mA。
所有引脚总电流不超过 200 mA。
```

### 10.4 Agent 调了工具但结果没回流

```
chat_routes.py 流式输出时，tool 返回结果没有加回 messages
→ LLM 不知道工具执行过了
→ 下一轮 LLM 又问同一个问题
→ 死循环
```

**修法**：每轮 messages 要连接好：

```python
# 正确的 messages 流转
messages = [system, user_message]
# 第一轮
response = llm.chat(messages, tools)
# messages += assistant 消息（含 tool_calls）
messages.append(response)
for tc in tool_calls:
    result = execute(tc)
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "content": result,
    })
# 第二轮：messages 里现在有 system + user + assistant(with tool_calls) + tool
response = llm.chat(messages, tools)  # 这次 LLM 能看到工具结果
```

### 10.5 多 tool_call 并发冲突

OpenAI 一次可能返回多个 tool_call：

```json
"tool_calls": [
  {"id": "call_1", "function": {"name": "search_kb", "arguments": "{\"query\":\"GPIO\"}"}},
  {"id": "call_2", "function": {"name": "search_kb", "arguments": "{\"query\":\"电流\"}"}}
]
```

两个工具可以**并行执行**，但你必须等**全部完成**才能调下一轮 API。如果串行执行，总耗时会翻倍。

```python
async def execute_all(tool_calls: list) -> list:
    """并行执行所有 tool_calls"""
    tasks = []
    for tc in tool_calls:
        tasks.append(safe_dispatch(tc))
    results = await asyncio.gather(*tasks)
    # 把结果按 tool_call 顺序加回 messages
    for tc, result in zip(tool_calls, results):
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })
    return messages
```

### 10.6 System prompt 里不要写工具描述

新人常见错误：

```

system = """
你是硬件助手。你有以下工具可用：search_kb、execute_code。
search_kb 用来搜索知识库。
execute_code 用来执行 Python 代码。
...
"""

messages = [{"role": "system", "content": system}, {"role": "user", "content": "..."}]
```

**不要写。** 工具的 name 和 description 通过 `tools` 参数传给 API，LLM 已经知道了。写在 system prompt 里是浪费 token，而且会和 tools 参数冲突。

system prompt 只需要写 LLM 的角色和行为准则，不写工具有哪些。

### 10.7 前端 SSE 事件和 LLM stream 交错

Agent 循环里，LLM 的流式输出和工具调用的 SSE 事件是**交错**的。前端不能假设"先收到全部 text 再收到 tool_start"。

```
实际顺序：
  text: "我需要查一下手册..."
  tool_start: search_kb
  tool_result: 找到 3 篇文档
  text: "根据资料..."
```

前端需要用 `event.type` 来区分，不能用顺序推断。

### 10.8 多轮对话里 tools 传不传

用户和 Agent 对话超过一轮之后，第二轮还要不要传 tools？

```
用户：ESP32 GPIO 电流多少？
Agent：调 search_kb → 40 mA
用户：那 STM32 呢？
```

**要传。** 如果不传 tools，LLM 不知道它还能调工具，第二问就会直接凭记忆回答（可能乱编）。

```python
# 每轮对话都把 tools 参数传进去
response = await llm.chat(messages, tools=ALL_TOOLS)
```

但可以不传整个数组，第一次传给完整的，后续传引用即可（API 会缓存）。

### 10.9 tool_choice 的三个档位

| tool_choice | 效果 | 什么时候用 |
|------------|------|-----------|
| "none" | 不调工具 | 用户闲聊时 |
| "auto" | LLM 自己决定 | 默认 |
| "any" 或 `{"type":"function","function":{"name":"xxx"}}` | 强制调指定工具 | 明确场景（如"搜一下 ESP32"） |

如果你的 Agent 在"不需要搜也搜了"和"该搜不搜"之间反复横跳，先用 `"auto"` 调参，不行再上规则兜底。

### 10.10 测试 Agent 的正确方式

不要用手点。写测试：

```python
# 测试 1：明确需要搜索的问题 → 必须调 search_kb
result = await agent.run("ESP32 的 GPIO 最大电流是多少")
assert "search_kb" in result.tool_calls_used

# 测试 2：通用问题 → 不调工具
result = await agent.run("你好")
assert len(result.tool_calls_used) == 0

# 测试 3：死循环打断
result = await agent.run("搜一下", max_iterations=20)
assert result.iteration <= 10  # 防护层生效

# 测试 4：工具失败降级
result = await agent.run("搜 xxx", with_mock={"search_kb": TimeoutError})
assert "fallback" in result.path  # 降级策略触发
```

---

## 十一、和 Claude Code / Codex 的差异对照

| 场景 | Claude Code | Codex | 你的项目 |
|------|------------|-------|---------|
| Tool 格式 | Anthropic Tool Use | OpenAI Function Calling | OpenAI Function Calling |
| 工具定义 | Tool 接口含 20+ 方法 | 标准 JSON Schema | BaseTool 基类 + JSON Schema |
| 执行管线 | 校验→权限→hook→执行 | 校验→执行 | 校验→安全→执行→截断 |
| 循环控制 | QueryEngine 内建 | Agent 循环内建 | LangGraph StateGraph |
| 流式 | 标准 SSE | 标准 SSE | astream_events |
| 死循环防护 | 有 | 有 | 5 层防护 |
| 多工具并发 | 按 concurrency_safe 分批 | 内置调度 | asyncio.gather |
| 权限控制 | checkPermissions | tool 层级 | is_read_only / is_destructive |

---

## 附录：RAG 工程常见问题

### A.1 多路召回

| 问题 | 说明 |
|------|------|
| **纯向量检索找不到关键词** | 用户搜"上拉电阻"，文档里写的是"pull-up resistor"，向量能匹配到，但中文精确匹配不如 BM25 |
| **纯 BM25 找不到同义词** | 用户搜"MCU"，文档里写的是"微控制器"，BM25 匹配不到，向量能 |
| **单路召回上限低** | 一个方法 top_k=5 就是 5 条，但可能正确结果在两个方法里各排第 6 |

**解法：多路召回 + RRF 融合**

```
向量检索 top_k=10
BM25 检索 top_k=10
  → 合并去重
  → RRF 排序：score = 1/(60 + rank_vector) + 1/(60 + rank_bm25)
  → 取前 k 条
```

RRF 的好处是不需要调权重，向量和 BM25 天然平等。

**什么时候不加 BM25：**
- 用户的 API 不支持 embedding（纯文本模型）→ 没必要加
- 文档全部是英文且用词规范 → 向量够了，BM25 收益小
- 每次检索都在 100ms 以内 → 加 BM25 多一次检索，耗时翻倍

**加 BM25 的正确做法：**

```python
from rank_bm25 import BM25Okapi

class HardwareVectorStore:
    def __init__(self):
        self.bm25_index = None
        self.bm25_docs = []       # 原始文本，按 chunk 存
        self.bm25_metadata = []   # 每个 chunk 的 metadata

    def rebuild_bm25_index(self):
        """入库后重建 BM25 索引"""
        all_chunks = self.db.get()
        self.bm25_docs = [doc.page_content for doc in all_chunks]
        tokenized = [self._tokenize(doc) for doc in self.bm25_docs]
        self.bm25_index = BM25Okapi(tokenized)

    def _tokenize(self, text: str) -> list[str]:
        """中文分词 + 英文小写切分"""
        import re
        # 简单分词：中文按字拆 + 英文按空格
        tokens = re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]+', text.lower())
        return tokens

    def hybrid_search(self, query, k=5, vector_weight=0.5, bm25_weight=0.5):
        """混合检索"""
        # 向量检索
        vector_results = self.search(query, k=k*2)

        # BM25 检索
        tokenized_query = self._tokenize(query)
        bm25_scores = self.bm25_index.get_scores(tokenized_query)
        bm25_top = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True
        )[:k*2]

        # RRF 融合
        combined = {}
        for rank, r in enumerate(vector_results):
            combined[r.doc_id] = combined.get(r.doc_id, 0) + 1 / (60 + rank)
        for rank, idx in enumerate(bm25_top):
            doc_id = self.bm25_metadata[idx]["doc_id"]
            combined[doc_id] = combined.get(doc_id, 0) + 1 / (60 + rank)

        # 按 RRF 分排序取 top_k
        sorted_docs = sorted(combined.items(), key=lambda x: -x[1])[:k]
        return [self._get_doc_by_id(doc_id) for doc_id, _ in sorted_docs]
```

### A.2 重排序（Rerank）

多路召回是"多找一些候选"，重排序是"从候选中挑最好的"。

**什么时候需要 Rerank：**

```
多路召回返回 20 条候选 → 里面混了很多不相关的
→ 用一个轻量模型（cross-encoder）逐个打分
→ 重新排序 → 取前 5 条
```

**你现在需要吗？**

```
知识库文档 < 100 篇   → 不需要，向量 + BM25 够了
知识库文档 > 500 篇   → 可以考虑
单次检索返回结果里 > 50% 不相关 → 需要
```

**如果你要加 Rerank：**

| 方案 | 依赖 | 说明 |
|------|------|------|
| Cohere Rerank API | cohere 包 + API Key | 效果最好，但多一个 API 依赖 |
| BGE Reranker | sentence-transformers | 本地跑，不需要额外 API |
| LLM 自己 rerank | 无依赖 | 调 LLM："从以下段落中选出最相关的 3 条" |

**不建议新人一上来就加 Rerank。** 先在切片和 BM25 上调，调不动了再加。

### A.3 切片粒度不对

```
chunk 太短（< 200 字） → 信息不完整，LLM 看不懂在说什么
chunk 太长（> 2000 字）→ 一个 chunk 包含多个主题，搜索精度差
表格被切断 → LLM 收到半张表 → 乱编数据
```

**硬件文档的特殊性：**
- 表格多（引脚定义表、寄存器表）→ 表格必须完整
- 术语密集（"GPIO 上拉电阻 10kΩ"）→ 小块也够
- 章节边界清晰（概述 / 引脚 / 电气特性 / 通信接口）→ 按章节切天然合理

**建议的切片策略：**

```python
# 第一优先级：按 ## / ### 标题切
# 第二优先级：表格保持完整（检测 --- 边界）
# 第三优先级：RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=500)
```

### A.4 Embedding 模型选不对

| 模型 | 适用场景 | 说明 |
|------|---------|------|
| text-embedding-3-small | 通用，英文强 | 你的默认，用户大部分 API 支持 |
| text-embedding-ada-002 | 旧版，不推荐 | OpenAI 已建议迁移 |
| bge-large-zh | 中文强 | 如果你的硬件文档全是中文手册 |
| 用户自选 | 灵活 | 每个知识库独立记录，互不干扰 |

**坑：** 用户选了一个模型 → 上传了一批文档 → 换模型后新文档正常入库，旧文档还能搜（因为已有向量不变）。但**用户以为换了模型所有数据都升级了**——这是体验问题，不是技术问题。前端要提示清楚。

### A.5 检索结果太多/太少

```
top_k=5 但命中的只有 2 条 → 返回 2 条没问题
top_k=5 但命中的 5 条都不相关 → 降 score_threshold
top_k=5 但相关结果排在第 6 → 调成 top_k=10，用 Rerank 或 LLM 自己挑
```

**不调阈值的做法：**

```python
def search(self, query, k=5, min_score=0.0):
    results = self.db.similarity_search_with_relevance_scores(query, k=k*2)
    # 过滤低分
    filtered = [(doc, score) for doc, score in results if score >= min_score]
    # 即使低于阈值也保留一条最相关的，避免空结果
    if not filtered and results:
        filtered = [results[0]]
    return filtered[:k]
```

### A.6 知识库更新后检索结果没变

```
上传了新文档 → 入库成功 → 但搜索还是只返回旧文档
```

**原因：** ChromaDB 的 collection 是增量的，但搜索时可能用了缓存的 embedding 函数或旧的 collection 引用。

```python
# 解决方法：入库后调用一次重新加载
def refresh_index(self):
    """重新加载向量库（不重建，只是刷新引用）"""
    self._db = None  # 强制下次访问时重建 Chroma 对象
```

### A.7 来源标注的坑

```
前端展示了来源 → 但来源是错的最常见的三种情况：

1. chunk 的 metadata 里的 title 是文件名，不是文档标题
   修法：入库时从 YAML frontmatter 提取 title

2. 来源 URL 是本地上传路径
   修法：入库时检测到本地路径 → 替换成文件名

3. 同一个事实出现在多个 chunk 里 → 来源列表重复
   修法：前端用 doc_id 去重
```

### A.8 多知识库搜索的边界

```
用户出厂知识库和用户自己的知识库都搜到了 → 合并展示
但：
  用户删除了自己上传的一篇文档 → 出厂知识库不受影响
  用户切换 embedding 模型 → 出厂知识库仍然是旧模型 → 两个库共存
  出厂知识库更新 → 用户需要手动触发出厂库重建，不影响用户自己的库
```

### A.9 RAG 结果注入到 LLM 的格式

```
差的做法：
  把 5 段原始 chunk 用 \n 拼在一起丢进 system prompt

好的做法：
  [来源：ESP32 数据手册.pdf 第 28 章]
  GPIO 最大输出电流：40 mA（每个引脚）

  [来源：ESP32 技术参考手册.pdf 第 5.2 节]
  所有 GPIO 引脚总输出电流不超过 200 mA

LLM 看到来源清晰的文本，更容易正确引用
```

### A.10 没有 RAG 时的幻觉控制

```
知识库里没有相关内容 → 搜到 0 条 → LLM 自己编

应该：
  搜到 0 条 → 在 system prompt 里明确写：
  "如果参考文档中没有相关信息，请直接告诉用户
  知识库里没有找到相关内容，不要自己编造参数。"
```

### A.11 embedding 和 chat 共用 API Key 的风险

```
用户的 API Key 同时被 embedding 和 chat 使用 → 
embedding 调用算 token 消耗 →
用户发现用量比预想的高 →
投诉

修法：
  设置页分开配置：
    Chat API Key（必填）
    Embedding API Key（可选，不填则共用 Chat 的）
  
  或者在前端提示："知识库文档入库会产生 embedding token 消耗"
```

### A.12 文档解析失败时不要静默

```
上传 PDF → 解析失败 → 返回 200 OK → 但知识库里没有新内容
用户以为上传成功了，实际上没有

修法：
  /api/kb/upload 返回显式的入库结果：
  {
    "status": "completed" | "failed" | "partial",
    "chunks_count": 42,
    "errors": ["第 3 页表格解析失败", "图片 OCR 跳过"]
  }
  前端根据 status 显示上传结果
```

---

## 附录 B：Agent 对话管理

### B.1 多轮对话中的状态

单轮 Agent 流程是完整的，但多轮对话之间有状态继承问题：

```
第一轮：用户"ESP32 GPIO 电流"
  → Agent 调 search_kb → 回答 40 mA
  → messages 里多了 tool_call 和 tool_result

第二轮：用户"那 STM32 呢？"
  → "那" 指的是"电流"
  → 但 messages 里上一轮的 tool_call / tool_result 可能干扰 LLM
```

**解法：** 每轮对话开始时清理上一轮的 tool 中间产物，只保留最终回答。

```python
def clean_messages_for_new_turn(messages: list) -> list:
    """新轮对话前，清理上一轮的 tool_call 和 tool_result"""
    cleaned = []
    for msg in messages:
        # 保留 system
        if msg["role"] == "system":
            cleaned.append(msg)
        # 保留 user
        elif msg["role"] == "user":
            cleaned.append(msg)
        # 保留最终的 assistant 回答（不含 tool_calls 的）
        elif msg["role"] == "assistant" and not msg.get("tool_calls"):
            cleaned.append(msg)
        # tool_call 和 tool_result 和包含 tool_calls 的 assistant 都跳过
    return cleaned
```

### B.2 Agent 流式输出和前端 message 结构

前端 message 结构要能表达"这轮对话里调用了工具"：

```typescript
interface Message {
  role: "user" | "assistant" | "system";
  content: string;                    // 文本内容
  tool_calls?: ToolCall[];            // assistant 发起的工具调用
  tool_results?: ToolResultDisplay[]; // 工具执行结果展示
}

interface ToolCall {
  name: string;
  args: Record<string, any>;
  status: "running" | "completed" | "failed";
  output?: string;
}
```

用户看到的消息气泡：

```
[用户] ESP32 GPIO 电流多少？
[助手] 🔍 搜索知识库... [展开]
       根据资料，ESP32 GPIO 最大输出电流 40 mA
       📎 来源：ESP32 数据手册.pdf
```

### B.3 流式中断恢复

```
用户正在看 Agent 流式输出 → 刷新了页面 → 所有状态丢失

解法：
  1. SSE 断开时前端记录已收到的文本
  2. 后端标记这个 SSE 连接已关闭
  3. 如果 Agent 循环还在跑 → 终止（清理资源）
  4. 下次用户发新消息 → 从上一轮最终状态继续
```

### B.4 Agent 日志

每条 Agent 执行记录写结构化日志：

```python
log_entry = {
    "event": "agent_run",
    "session_id": "...",
    "user_message": "ESP32 GPIO 电流",
    "iterations": [
        {"step": 1, "tool": "search_kb", "args": {...}, "result_summary": "找到 3 篇", "duration_ms": 1200},
        {"step": 2, "tool": None, "response_preview": "根据资料...40 mA", "duration_ms": 3400},
    ],
    "total_duration_ms": 4600,
    "total_tokens": 1250,
    "status": "completed" | "max_iterations" | "timeout" | "error",
}
```

对调试 Agent 行为至关重要——出问题时不用猜，看日志就知道 LLM 调了什么工具、结果是什么、在哪一步卡住了。

---

## 附录 C：SSE 工程注意事项

### C.1 SSE 连接生命周期

```
前端连接 /api/chat/sse
  → 后端开始 Agent 循环
  → 循环可能调多轮工具（几秒到几十秒）
  → 这个过程中 SSE 连接必须保持

问题：
  浏览器 HTTP 连接有超时（通常 2-5 分钟）
  代理/Nginx 也有超时
  用户切 Tab 后浏览器可能断开 SSE

解法：
  1. 后端每 15 秒发一个 keepalive 事件（type: "ping"）
  2. 前端检测到断开后自动重连，发最后一条消息 ID
  3. 后端支持 from_message_id 续接
```

### C.2 keepalive 实现

```python
async def event_generator():
    try:
        while True:
            # 正常发送数据
            for chunk in agent_stream:
                yield sse_event(chunk.type, chunk.data)
            break
    except asyncio.CancelledError:
        # 客户端断开
        logger.info("SSE 客户端断开，清理资源")
        await agent.cleanup()
    finally:
        # 确保资源释放
        await agent.cleanup()
```

### C.3 前端重连

```typescript
function useSSE(url: string) {
  const reconnect = useRef(true);

  const connect = () => {
    const source = new EventSource(url);
    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleEvent(data);
    };
    source.onerror = () => {
      if (reconnect.current) {
        setTimeout(connect, 3000); // 3 秒后重连
      }
    };
  };
}
```

### C.4 浏览器 EventSource 的限制

EventSource 只支持 GET，不支持自定义 Header。

你目前的前端 `/api/chat` 用 EventSource，但 Agent 循环需要传 `X-API-Key` 和 `X-Model`。如果你用 EventSource，**传不了自定义 Header**。

**两种解法：**

```
解法 A：API Key 和 Model 放在 URL query 参数
  new EventSource("/api/chat?s=xxx&model=gpt-4o")
  后端从 query 参数取

解法 B：改用 fetch + ReadableStream
  可以传 Header，但比 EventSource 多写一些代码

解法 C：统一走 fetch + POST stream
  你的 chat 已经是 POST，如果改成 Agent 后还是 POST，
  用 Response.stream 返回 SSE 格式
```

建议你直接在 url query 里传，改动最小。

---

## 附录 D：Performance 注意事项

### D.1 Agent 启动慢

```
第一轮：加载 ChromaDB → 初始化 LLM 客户端 → 首次调 API（冷启动）
→ 用户可能等 3-5 秒才能看到第一个字

解法：
  后端启动时预加载 LLM 客户端和 ChromaDB（app.on_event("startup")）
  Agent 第一轮不调工具时直接返回，省去搜知识库的时间
```

### D.2 Embedding 调用慢

```
用户上传文档 → 向量化入库
如果文档多（50 页 PDF），embedding 调用可能花几十秒

解法：
  上传接口立即返回 202 Accepted
  后台异步入库
  前端轮询入库状态
```

### D.3 ChromaDB 查询慢

```
文档多了（> 10000 个 chunk）→ 搜索变慢

解法：
  确认 ChromaDB 持久化路径在 SSD 上
  按 category 过滤缩小搜索范围
  考虑给 ChromaDB 加索引（默认就有，但确认没有关闭）
```

### D.4 多用户共用

```
你的项目是 self-hosted，默认单用户。
但如果部署到团队使用：
  → 每个用户的知识库隔离
  → ChromaDB 支持多 collection，每个用户一个
