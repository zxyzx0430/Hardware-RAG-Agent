# Chunk 重复入库根因定位报告

> 生成时间：2026-06-29
> 案例对象：ch340g_datasheet.pdf (multimodal chunker, 90 chunks, 56% 重复率)

---

## 1. 现象

ch340g 重新索引后 90 chunks 中，80 个 text chunk 约 56% 是重复（fingerprint 相同）：

| fingerprint | chunk_index | 重复次数 | 涉及的 section_title |
|-------------|-------------|---------|---------------------|
| `9a6269c7...` | 52, 54, 56, 58, 60 | **5x** | "Parameter" / "Application" / "6.2. Electrical Parameter" / "6.3. Sequence Parameter" / "7.1.1 USB to RS232..." |
| `758c7bed...` | 0, 2, 4 | 3x | "WCH CH340G USB to UART Interface Datasheet" / "1. Overview" / "2. Features" |
| `fb41b078...` | 30, 32, 34 | 3x | "2 Features" / "2 Features" / "4. Pins" |
| `ec881844...` | 62, 66, 70 | 3x | "7.1.2..." / "7.1. USB to RS232 Converter Design Notes" / "7.2. USB to RS232..." |

## 2. 根因（已定位）

### 根因 A：section 边界 page_range 重叠

**位置**：`backend/src/rag/chunking/multimodal_chunker.py` L1166-1170

```python
page_texts = [
    page_text_map.get(p, "")
    for p in range(start_page, end_page + 1)   # ← 按 page_range 取页文本
]
section_text = "\n\n".join(t for t in page_texts if t)
```

LLM 返回的相邻 section 的 `page_range` 重叠（实测 ch340g 案例）：
- Section A "2. Features" start_page=1, end_page=3
- Section B "1. Overview" start_page=2, end_page=4
- Section C "WCH CH340G..." start_page=1, end_page=2

`range(1, 4)` 和 `range(2, 5)` 都包含 page 2-3，导致 page 2-3 的文本被 A、B 两个 section 各取一次，最终生成两个 fingerprint 相同的 chunk。

### 根因 B：无去重兜底

**位置 1**：`multimodal_chunker._build_chunks` 无 fingerprint 去重逻辑。
**位置 2**：`kb_manager.ingest_chunks` (L550-589) 无 fingerprint 去重逻辑，直接把所有 chunk 入库。
**位置 3**：`agent_chunker` 有 fingerprint 去重（L1100-1123），但去重键是单 `fingerprint`，会导致不同 section 的同文本 chunk 只保留一个（误删），而非解决根因。

## 3. 修复策略

### 修复 A（治本）：消除 section 边界重叠

让相邻 section 不共享同一页文本。两种方案：

**方案 A1（推荐）**：LLM prompt 强制 section 边界不重叠——在 `_MULTIMODAL_ANALYSIS_PROMPT` 中明确要求 "相邻 section 的 page_range 不重叠，前一 section 的 end_page + 1 = 后一 section 的 start_page"。

**方案 A2**：在 `_build_chunks` 中按 section 顺序维护 `last_end_page`，对每个 section 的 `start_page` 强制 `max(start_page, last_end_page + 1)`，end_page 不变。若 start > end 则跳过该 section。

**选择 A2**（代码层强制，不依赖 LLM 遵守 prompt）。

### 修复 B（兜底）：入库前 fingerprint 去重

在 `kb_manager.ingest_chunks` 入库前对所有 chunk 按 `(fingerprint, section_title)` 复合键去重，保留第一个实例。

### 修复 C（agent_chunker fingerprint 去重键修正）

`agent_chunker` 的 fingerprint 去重键从单 `fingerprint` 改为 `(fingerprint, section_title)` 复合键，避免误删不同 section 的 chunk。

## 4. 验证标准

- ch340g 重新索引后重复率 < 5%（修复前 56%）
- STM32 GPIO 重新索引后重复率 < 5%
- 06-chaotic-embedded-notes 重新索引后重复率 < 5%
- 三个案例的 chunk 内容不丢失（page_coverage 全覆盖）

## 5. 风险评估

- **方案 A2 风险**：若 LLM 返回的 section 顺序与阅读顺序不一致，强制 `max(start_page, last_end_page + 1)` 可能让某些 section 的 start > end 被跳过，导致内容丢失。
  - **缓解**：跳过前打 warning 日志，并在 `_build_chunks` 末尾用 `verify_page_coverage` 检查是否有页面未覆盖，若有则 fallback 到不去重原逻辑。
- **修复 B 风险**：复合键去重可能让原本应保留的 chunk 被误删（如不同 section 的引用同一段落）。
  - **缓解**：用 `(fingerprint, section_title)` 而非单 `fingerprint`，同一 section 内才去重，跨 section 保留。
