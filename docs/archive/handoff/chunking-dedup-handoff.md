# Chunking 模块去重交接说明

> 给：负责优化 multimodal_chunker.py 的 agent
> 来自：Trae 代码审查
> 日期：2026-06-29

## 背景

代码审查发现 `hybrid_chunker.py`、`agent_chunker.py`、`multimodal_chunker.py` 三个文件存在重复代码。我已修复 hybrid + agent 的部分，**multimodal 留给你同步**。

## 已完成的改动（hybrid + agent）

我已在 `backend/src/rag/chunking/base.py` 抽取了共享代码：

1. **`INLINE_CODE_RE`** 常量 — 三处重复的正则 `r"```[\s\S]*?```"`
2. **`SYMBOL_PATTERN`** 常量 — 纯符号 chunk 过滤正则（基础版，不含 `"`）
3. **`stash_inline_code(text)` + `restore_inline_code(text, code_map)`** 函数 — 代码块占位保护/恢复（用 UUID 版本）
4. **`MergeConfig` dataclass + `merge_tiny_chunks(chunks, threshold, config)`** 函数 — 小 chunk 合并，用 config 控制差异

hybrid 和 agent 已改为调用 base.py 的共享函数。

## 你需要做的（multimodal 同步）

### 1. 删除重复定义，改 import

```python
# multimodal_chunker.py 顶部
from src.rag.chunking.base import (
    INLINE_CODE_RE,  # 替代 _INLINE_CODE_RE
    SYMBOL_PATTERN,  # 替代 _SYMBOL_PATTERN（见下方注意）
    stash_inline_code, restore_inline_code,  # 替代 _stash_code 闭包
    merge_tiny_chunks, MergeConfig,  # 替代 _merge_tiny_chunks 方法
)
```

### 2. `_SYMBOL_PATTERN` 的差异

你的 `multimodal_chunker.py:1103` 用的是超集版本（多了 `"` 字符）：
```python
# 你的版本
_SYMBOL_PATTERN = re.compile(r'^[\s\-_=*#|+.:`"\s]+$')  ← 多了 "

# base.py 的基础版
SYMBOL_PATTERN = re.compile(r'^[\s\-_=*#|+.:`\s]+$')
```

**决定**：要么用基础版（可能漏过只含 `"` 的 chunk），要么在 base.py 升级为超集版。建议升级 base.py 为超集版（`"` 是无害的加入），然后三处统一。

### 3. `_stash_code` 的差异

你的 `multimodal_chunker.py:1032` 用的是索引版：
```python
key = f"\x00CB{len(code_map)}\x00"
```

base.py 用的是 UUID 版本（agent 的改进版，P2-4 修复碰撞）：
```python
key = f"\x00CB{uuid.uuid4().hex[:8]}\x00"
```

**直接改用 base.py 的版本**，更安全，无行为差异。

### 4. `_merge_tiny_chunks` 的差异

你的 `multimodal_chunker.py:1105` 与 hybrid 的版本基本一致，有 cross-section 支持。改用 base.py 时传这个 config：

```python
config = MergeConfig(
    cross_section_support=True,  # 你当前有这个
    log_prefix="MultimodalChunker::Merge",
    enable_logging=True,
)
merged = merge_tiny_chunks(chunks, threshold=100, config=config)
```

### 5. agent Pass 2 cross-section 问题（需你判断）

**发现**：`agent_chunker.py` 的 `_merge_tiny_chunks` Pass 2 **缺少 cross-section 支持**。注释说 "kept in sync with HybridChunker" 但实际没同步——hybrid 和 multimodal 都有 cross-section，唯独 agent 没有。

**可能原因**：
- **特性**：agent 经过 LLM 投票，section 划分更准确，不需要跨 section 合并
- **bug**：遗漏了同步

**建议**：检查 agent 的 LLM 投票输出，看 section 是否真的比 hybrid 更准确。如果是，保持 `cross_section_support=False`；如果不是，改为 `True` 统一行为。

## 联系

如有疑问，查看 `docs/pitfalls.md` 和 `docs/completed.md` 的 chunking 相关记录。
