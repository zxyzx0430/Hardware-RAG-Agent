# P0 聊天体验优化设计

> 聚焦两个核心痛点：RAG 等待无感知、错误信息不友好。
> 完整体验问题清单见 brainstorming 阶段探索结果，P1/P2 留后续任务。

## 1. 背景

用户发送消息后，RAG 检索期间（query rewrite 0-8s + 向量搜索 + BM25 + 融合 + 可选 reranker）只看到一个空 spinner，不知道系统在干什么。错误发生时，原始英文技术报错直接拼入消息内容，普通用户看不懂。

## 2. 设计目标

- **RAG 进度**：用户发送消息后 1 秒内看到至少一条进度提示，每个阶段切换时更新文案
- **错误友好**：错误以独立卡片展示，包含错误类别图标、友好中文文案、可操作建议
- **可扩展**：进度事件机制适配未来 Agent 架构（每个工具执行都能发进度）
- **最小侵入**：不改后端架构，不改 SSE 协议，在现有 thinking 事件上扩展

---

## 3. Part 1：RAG 细粒度进度事件

### 3.1 现状

[chat_helpers.py L293](file:///E:/Desktop/agent/backend/app/api/chat_helpers.py#L293) 只发一条：
```python
events.append(sse_event("thinking", {"content": "正在检索知识库...", "source": "rag"}))
```

RAG 管线实际有 4 个阶段（query rewrite → 搜索 → 融合 → 结果处理），用户全部感知为"正在检索知识库..."。

### 3.2 改动

在 [chat_helpers.py](file:///E:/Desktop/agent/backend/app/api/chat_helpers.py) `_run_rag_retrieval()` 中，将单一 thinking 事件拆分为阶段事件：

```python
# 当前（L293）：
events.append(sse_event("thinking", {"content": "正在检索知识库...", "source": "rag"}))

# 改为：
events.append(sse_event("thinking", {"content": "正在改写查询...", "source": "rag"}))
# ... query rewrite 完成后 ...
events.append(sse_event("thinking", {"content": "正在检索文档...", "source": "rag"}))
# ... search_all_enabled 完成后（含向量搜索+BM25+RRF融合+reranker），后续 source/tool 事件正常 yield ...
```

具体插入位置：

| 阶段 | 文案 | 插入位置 | 说明 |
|------|------|----------|------|
| 1. Query Rewrite | "正在改写查询..." | L293 替换原事件 | 0-8s，LLM 改写查询 |
| 2. 文档搜索 | "正在检索文档..." | L304 之后（rewrite 返回后） | ChromaDB + BM25 + RRF 融合 + 可选 reranker，是一个阻塞调用 |

`search_all_enabled()` 内部完成向量搜索、BM25、RRF 融合、reranker，从外部看是一个调用，拆为"调用前"和"调用后"两个事件。加上后端 L108 已有的 `thinking("正在生成回答...", source="llm")`，用户能感知到完整的 3 个阶段：改写查询 → 检索文档 → 生成回答。这已经足够让用户感知进度。

### 3.3 前端适配

前端 [ActivityBlock](file:///E:/Desktop/agent/frontend/src/components/chat/ChatArea.tsx#L416-L472) 已有完整的 thinking 渲染逻辑：
- `ThinkingStep` 组件根据 `source` 显示不同图标（RAG=灯泡，reasoning=眼睛）
- 折叠状态下显示前 40 字符预览

**唯一需要改的**：ActivityBlock header 在 `stepCount === 0` 时显示 "工具链: 0 个工具"，收到首个 thinking 后才更新。改为：

```tsx
// ChatArea.tsx L439
// 当前：
{t('activityLabel')}: {stepCount} {t('tools')}
// 改为：
{stepCount === 0 && isRunning ? '...' : `${t('activityLabel')}: ${stepCount} ${t('tools')}`}
```

这样在收到首个 thinking 事件前显示 "..."，收到后正常显示步骤数。

### 3.4 SSE 事件序列对比

**改前**：
```
thinking("正在检索知识库...")     ← 3-8 秒内只有这一条
source(...)
tool(search_docs, ...)
thinking("正在生成回答...")
text("token1" → "token2" → ...)
done
```

**改后**：
```
thinking("正在改写查询...")       ← 立即出现（0-8s）
thinking("正在检索文档...")       ← rewrite 完成后（~1-2s）
source(...)
tool(search_docs, ...)
thinking("正在生成回答...")
text("token1" → "token2" → ...)
done
```

### 3.5 扩展性

后期 Agent 架构中，每个工具执行前可以 yield：
```python
yield sse_event("thinking", {"content": "正在扫描串口设备...", "source": "tool"})
# ... tool 执行 ...
yield sse_event("tool", {"name": "scan_devices", "result": "..."})
```

进度事件的 `content` 字段是自由文本，不绑定 RAG，天然适配任意工具。

---

## 4. Part 2：错误友好卡片

### 4.1 现状

后端 error 事件格式（[chat_routes.py L143](file:///E:/Desktop/agent/backend/app/api/chat_routes.py#L143)、L211）：
```python
yield sse_event("error", {"message": "LLM 响应超时，请重试"})
yield sse_event("error", {"message": sanitize_error(f"LLM 调用失败: {str(e)}")})
```

前端 [useChatStore.ts L577-578](file:///E:/Desktop/agent/frontend/src/stores/useChatStore.ts#L577-L578)：
```typescript
const finalContent = errorMessage
  ? (existingContent ? `${existingContent}\n\n❌ ${errorMessage}` : `❌ ${errorMessage}`)
  : (streamingContent || existingContent);
```

错误消息直接拼入 assistant 消息内容，用户看到的是 `❌ LLMError: API Key 无效：Incorrect API key provided: sk-xxxx...`

### 4.2 后端改动

#### 4.2.1 错误码枚举

在 [chat_routes.py](file:///E:/Desktop/agent/backend/app/api/chat_routes.py) 中定义错误码：

```python
class ChatErrorCode(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    RAG_FAILED = "RAG_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
```

#### 4.2.2 错误事件结构化

error 事件新增 `code` 字段：
```python
sse_event("error", {
    "code": "AUTH_FAILED",
    "message": "API Key 无效",
    "detail": "请检查设置中的 API Key 是否正确，或重新生成一个"
})
```

#### 4.2.3 异常转错误码

在 [chat_routes.py](file:///E:/Desktop/agent/backend/app/api/chat_routes.py) `event_generator()` 的 try/except 中，按异常类型映射错误码：

```python
except LLMError as e:
    msg = str(e)
    if "API Key" in msg or "AuthenticationError" in msg:
        code = ChatErrorCode.AUTH_FAILED
        message = "API Key 无效"
        detail = "请检查设置中的 API Key 是否正确"
    elif "频率" in msg or "rate" in msg.lower():
        code = ChatErrorCode.RATE_LIMITED
        message = "请求频率超限"
        detail = "请稍后再试，或降低并发请求"
    elif "超时" in msg or "timeout" in msg.lower():
        code = ChatErrorCode.TIMEOUT
        message = "LLM 响应超时"
        detail = "请检查网络连接后重试"
    else:
        code = ChatErrorCode.INTERNAL_ERROR
        message = "LLM 调用失败"
        detail = msg  # 保留原始错误供开发者查看
```

同时更新 L143 的 idle timeout 错误：
```python
# 当前：
yield sse_event("error", {"message": "LLM 响应超时，请重试"})
# 改为：
yield sse_event("error", {"code": "TIMEOUT", "message": "LLM 响应超时", "detail": "5 分钟内无新数据，连接已断开"})
```

### 4.3 前端改动

#### 4.3.1 Store 新增 streamingError

[useChatStore.ts](file:///E:/Desktop/agent/frontend/src/stores/useChatStore.ts) 类型声明新增：

```typescript
streamingError: { code: string; message: string; detail: string } | null;
```

初始值设为 `null`。`sendMessage` 中 set 时也加 `streamingError: null`。

#### 4.3.2 onEvent 回调改动

[useChatStore.ts L276-280](file:///E:/Desktop/agent/frontend/src/stores/useChatStore.ts#L276-L280)：

```typescript
// 当前：
if (event.type === "error") {
  const errMsg = event.message ?? "未知错误";
  get().stopStreaming(errMsg);
  return;
}

// 改为：
if (event.type === "error") {
  const errPayload = event as { type: "error"; code?: string; message: string; detail?: string };
  set({
    streamingError: {
      code: errPayload.code ?? "UNKNOWN",
      message: errPayload.message ?? "未知错误",
      detail: errPayload.detail ?? "",
    },
  });
  get().stopStreaming(errPayload.message);
  return;
}
```

#### 4.3.3 stopStreaming 改动

[useChatStore.ts L577-578](file:///E:/Desktop/agent/frontend/src/stores/useChatStore.ts#L577-L578)：

```typescript
// 当前：错误拼入消息内容
const finalContent = errorMessage
  ? (existingContent ? `${existingContent}\n\n❌ ${errorMessage}` : `❌ ${errorMessage}`)
  : (streamingContent || existingContent);

// 改为：有 streamingError 时不拼入消息内容
const { streamingError } = get();
const finalContent = errorMessage && !streamingError
  ? (existingContent ? `${existingContent}\n\n❌ ${errorMessage}` : `❌ ${errorMessage}`)
  : (streamingContent || existingContent);
```

#### 4.3.4 ErrorBlock 组件

新建 [ErrorBlock.tsx](file:///E:/Desktop/agent/frontend/src/components/chat/ErrorBlock.tsx)：

```tsx
// 展示结构：
// [图标] 错误标题
//         错误详情（灰色小字）
//         [重试按钮]

// 错误码 → 颜色 + 图标映射：
// AUTH_FAILED     → 橙色 + 警告三角
// TIMEOUT         → 黄色 + 时钟
// RATE_LIMITED    → 黄色 + 时钟
// NETWORK_ERROR   → 红色 + 断开链接
// RAG_FAILED      → 蓝色 + 信息圆圈
// INTERNAL_ERROR  → 红色 + 警告三角
// UNKNOWN         → 灰色 + 问号
```

重试按钮：点击后调用 `sendMessage(lastUserMessage)`，传入最后一条用户消息重新发送。

#### 4.3.5 ChatArea.tsx 渲染 ErrorBlock

在 assistant 消息的渲染逻辑中，当 `streamingError` 存在时，在消息内容下方插入 ErrorBlock：

```tsx
{isCurrentlyStreaming && streamingError && (
  <ErrorBlock
    code={streamingError.code}
    message={streamingError.message}
    detail={streamingError.detail}
    onRetry={() => {
      set({ streamingError: null });
      const lastUserMsg = messages.findLast(m => m.role === 'user');
      if (lastUserMsg) sendMessage(typeof lastUserMsg.content === 'string' ? lastUserMsg.content : '');
    }}
  />
)}
```

### 4.4 SSE 事件格式对比

**改前**：
```json
{ "type": "error", "message": "LLMError: API Key 无效：Incorrect API key provided: sk-xxxx..." }
```

**改后**：
```json
{ "type": "error", "code": "AUTH_FAILED", "message": "API Key 无效", "detail": "请检查设置中的 API Key 是否正确" }
```

---

## 5. 涉及文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `backend/app/api/chat_helpers.py` | 修改 | `_run_rag_retrieval()` 拆分 thinking 事件为 2 个阶段 |
| `backend/app/api/chat_routes.py` | 修改 | 新增 ChatErrorCode 枚举；error 事件结构化；异常转错误码 |
| `frontend/src/stores/useChatStore.ts` | 修改 | 新增 streamingError 字段；onEvent 错误处理；stopStreaming 不拼入错误 |
| `frontend/src/components/chat/ChatArea.tsx` | 修改 | ActivityBlock 过渡文案；ErrorBlock 渲染 |
| `frontend/src/components/chat/ErrorBlock.tsx` | 新建 | 错误卡片组件 |

## 6. 不涉及

- Agent ReAct 循环（P1）
- 流式输出自动滚动（P1）
- SSE 重连机制（P1）
- Markdown 数学公式（P2）
- 工具调用接入（P1）
- 后端 SSE 协议大改

## 7. 验证方式

1. 启动前后端，发送一条消息，确认：
   - ActivityBlock 在 1 秒内出现 "正在改写查询..."
   - 检索完成后切换为 "正在检索文档..." → "正在生成回答..."
   - 每个阶段 ThinkingStep 正确显示
2. 设置无效 API Key，发送消息，确认：
   - 出现橙色错误卡片，显示 "API Key 无效"
   - 点击重试按钮后重新发送
3. 断开网络后发送消息，确认：
   - 出现红色错误卡片，显示 "网络连接失败"
4. 正常对话不受影响，消息内容和来源引用正常展示
