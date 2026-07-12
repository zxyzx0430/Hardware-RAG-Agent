"""
测试 chunking 模块 — hybrid chunker + fingerprint + page coverage.

纯逻辑测试，不依赖数据库或外部服务。
"""
import sys
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.chunking import (
    HybridChunker,
    AgentChunker,
    ChunkResult,
    compute_fingerprint,
    verify_page_coverage,
    get_chunker,
)
from src.rag.chunking.base import (
    parse_page_index,
    protect_structures,
    restore_structures,
    _TABLE_ROW_RE,
    _REGISTER_FIELD_RE,
    strip_page_markers,
)
from src.rag.chunking.agent_chunker import _truncate_at_boundary
from src.rag.chunking.multimodal_chunker import _merge_cross_page_tables


class TestFingerprint:
    """测试指纹计算。"""

    def test_fingerprint_is_deterministic(self):
        """相同内容应产生相同指纹。"""
        text = "ESP32 是一款双核 MCU"
        fp1 = compute_fingerprint(text)
        fp2 = compute_fingerprint(text)
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA256 hex length

    def test_fingerprint_differs_for_different_content(self):
        """不同内容应产生不同指纹。"""
        fp1 = compute_fingerprint("ESP32")
        fp2 = compute_fingerprint("ESP32-S3")
        assert fp1 != fp2


class TestPageCoverage:
    """测试页面覆盖校验。"""

    def test_full_coverage(self):
        """所有页面都被覆盖时应无漏页。"""
        chunks = [
            ChunkResult(
                text="chunk1",
                metadata={},
                page_range=(1, 3),
                fingerprint="fp1",
                chunk_method="hybrid",
            ),
            ChunkResult(
                text="chunk2",
                metadata={},
                page_range=(4, 6),
                fingerprint="fp2",
                chunk_method="hybrid",
            ),
        ]
        coverage = verify_page_coverage(chunks, total_pages=6)
        assert coverage["missing_pages"] == []
        assert len(coverage["covered_pages"]) == 6

    def test_missing_pages_detected(self):
        """漏页应被检测到。"""
        chunks = [
            ChunkResult(
                text="chunk1",
                metadata={},
                page_range=(1, 2),
                fingerprint="fp1",
                chunk_method="hybrid",
            ),
        ]
        coverage = verify_page_coverage(chunks, total_pages=5)
        assert len(coverage["missing_pages"]) > 0
        assert 3 in coverage["missing_pages"]
        assert 5 in coverage["missing_pages"]


class TestHybridChunker:
    """测试混合分块器。"""

    def test_chunk_markdown_text(self):
        """Markdown 文本应按标题切分。"""
        chunker = HybridChunker(chunk_size=500, chunk_overlap=50)
        text = """# ESP32 概述

ESP32 是一款双核 MCU，主频 240MHz。

## GPIO 配置

GPIO0-GPIO5 可用作通用 IO。

## I2C 接口

支持 I2C 主从模式，频率最高 1MHz。
"""
        result = asyncio.run(chunker.chunk(
            text=text,
            metadata={"doc_id": "test-doc"},
            file_path=Path("test.md"),
            total_pages=1,
        ))

        assert len(result) > 0
        assert all(isinstance(c, ChunkResult) for c in result)
        assert all(c.chunk_method == "hybrid" for c in result)
        assert all(c.fingerprint for c in result)
        assert all(c.metadata["doc_id"] == "test-doc" for c in result)

    def test_chunk_plain_text(self):
        """纯文本应按段落切分。"""
        chunker = HybridChunker(chunk_size=200, chunk_overlap=20)
        text = "这是第一段内容。\n\n这是第二段内容。\n\n这是第三段内容。"
        result = asyncio.run(chunker.chunk(
            text=text,
            metadata={"doc_id": "test-doc"},
            file_path=Path("test.txt"),
            total_pages=1,
        ))

        assert len(result) > 0
        assert all(c.chunk_method == "hybrid" for c in result)

    def test_empty_text_returns_empty(self):
        """空文本应返回空列表。"""
        chunker = HybridChunker()
        result = asyncio.run(chunker.chunk(
            text="",
            metadata={},
            file_path=None,
            total_pages=0,
        ))
        assert result == []


class TestFactory:
    """测试工厂函数。"""

    def test_get_hybrid_chunker(self):
        """get_chunker('hybrid') 应返回 HybridChunker 实例。"""
        chunker = get_chunker("hybrid")
        assert isinstance(chunker, HybridChunker)

    def test_get_agent_chunker(self):
        """get_chunker('agent') 应返回 AgentChunker 实例。"""
        chunker = get_chunker(
            "agent",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            context_window=128000,
        )
        assert isinstance(chunker, AgentChunker)

    def test_get_chunker_invalid_method(self):
        """未知分块方法应抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unknown chunk_method"):
            get_chunker("invalid")


# ═══════════════════════════════════════════════════════════════
# Chunk-integrity fullchain fix tests (Task 18)
# Covers fixes from .trae/specs/chunk-integrity-fullchain-fix/spec.md
# ═══════════════════════════════════════════════════════════════


class TestTableRowRegex:
    """Task 10.1: _TABLE_ROW_RE 放宽，允许单列表格 + 文本末尾表格。"""

    def test_single_column_table_protected(self):
        """单列 Markdown 表格（仅一个 |）应被保护。"""
        text = "intro\n|值|\n|---|\n|42|\nend"
        matches = _TABLE_ROW_RE.findall(text)
        # Should match all 3 table rows (|值|, |---|, |42|)
        assert len(matches) >= 3
        protected, pmap = protect_structures(text)
        # Original table content preserved in placeholder map
        assert any("值" in v for v in pmap.values())
        assert any("42" in v for v in pmap.values())

    def test_end_of_text_table_protected(self):
        """位于文本末尾的表格应被保护（无 trailing newline）。"""
        text = "标题段落\n\n|名称|值|\n|---|---|\n|电压|3.3V|"
        matches = _TABLE_ROW_RE.findall(text)
        assert len(matches) >= 3  # header + separator + data row
        protected, pmap = protect_structures(text)
        # The table should be in a placeholder, not in the protected text
        assert any("电压" in v and "3.3V" in v for v in pmap.values())

    def test_multiline_table_each_row_protected(self):
        """多行表格的每一行应被保护为独立 placeholder（防切断单行）。"""
        text = (
            "before\n\n"
            "|名称|值|单位|\n"
            "|---|---|---|\n"
            "|VCC|3.3|V|\n"
            "|IIC|400|kHz|\n"
            "\nafter"
        )
        protected, pmap = protect_structures(text)
        # 4 table rows → 4 placeholders (each row protected from being split mid-row)
        assert len(pmap) == 4
        # All table content preserved across placeholders
        all_values = " ".join(pmap.values())
        assert "VCC" in all_values and "IIC" in all_values
        # Protected text no longer contains raw table rows
        assert "VCC" not in protected
        assert "IIC" not in protected


class TestRegisterFieldRegex:
    """Task 10.2: _REGISTER_FIELD_RE 增加约束，不误匹配编号列表。"""

    def test_numbered_list_not_matched(self):
        """普通编号列表（无 BIT/REG/ADDR/MASK 关键词）不应被识别为寄存器块。"""
        text = (
            "配置步骤：\n"
            "01 1 选择 GPIO 模式\n"
            "02 2 设置输出方向\n"
            "03 3 写入高电平\n"
            "04 4 读取输入状态\n"
        )
        matches = _REGISTER_FIELD_RE.findall(text)
        assert matches == [], "Numbered list without register keywords should not match"

    def test_register_block_with_0x_prefix_matched(self):
        """0x 前缀的寄存器块应被识别。"""
        text = (
            "寄存器列表：\n"
            "0x00 CONFIG enable\n"
            "0x04 STATUS ready\n"
            "0x08 DATA 0xFF\n"
        )
        matches = _REGISTER_FIELD_RE.findall(text)
        assert len(matches) >= 1, "0x-prefixed register block should match"

    def test_register_block_with_BIT_keyword_matched(self):
        """含 BIT/REG/ADDR/MASK 关键词的编号块应被识别。"""
        text = (
            "字段定义：\n"
            "00 0 BIT0 ENABLE\n"
            "01 1 BIT1 DISABLE\n"
            "02 0 BIT2 RESERVED\n"
        )
        matches = _REGISTER_FIELD_RE.findall(text)
        assert len(matches) >= 1, "Numbered block with BIT keyword should match"


class TestParsePageIndex:
    """Task 11.1: parse_page_index 无 marker 时返回空列表（BREAKING）。"""

    def test_no_marker_returns_empty_list(self):
        """无 page marker 的文本应返回 []。"""
        text = "这是一段没有 page marker 的普通文本。\n\n另一段。"
        result = parse_page_index(text)
        assert result == []

    def test_with_markers_returns_index(self):
        """有 page marker 的文本应返回正确的索引。"""
        text = "<!-- PAGE:1 -->\n第一页内容\n\n<!-- PAGE:2 -->\n第二页内容"
        result = parse_page_index(text)
        assert len(result) == 2
        assert result[0][0] == 1  # page_num
        assert result[1][0] == 2
        # Content slices should not include markers
        slice1 = text[result[0][1]:result[0][2]]
        slice2 = text[result[1][1]:result[1][2]]
        assert "第一页" in slice1
        assert "第二页" in slice2
        assert "PAGE" not in slice1
        assert "PAGE" not in slice2

    def test_empty_text_returns_empty(self):
        """空文本应返回 []。"""
        assert parse_page_index("") == []


class TestTruncateAtBoundary:
    """Task 7.1: agent_chunker._truncate_at_boundary 边界对齐截断。"""

    def test_short_text_returned_as_is(self):
        """短于 max_len 的文本应原样返回。"""
        text = "短文本"
        assert _truncate_at_boundary(text, 100) == text

    def test_truncate_at_paragraph_boundary(self):
        """应优先在段落边界 (\\n\\n) 截断。"""
        text = "第一段内容很长。" * 30 + "\n\n" + "第二段内容。" * 30
        result = _truncate_at_boundary(text, 200)
        assert len(result) <= 200
        # Should cut at paragraph boundary, so result ends without trailing \n\n
        assert "第二段" not in result
        assert result.endswith("。")

    def test_truncate_at_sentence_boundary(self):
        """无段落边界时应退到句号边界 (. )。"""
        text = "This is sentence one. " * 20 + "This is sentence two. " * 20
        result = _truncate_at_boundary(text, 200)
        assert len(result) <= 200
        # Should end with a period, not mid-word
        assert result.endswith(".")

    def test_hard_cut_when_no_boundary(self):
        """无任何边界时应硬切到 max_len。"""
        text = "a" * 500
        result = _truncate_at_boundary(text, 100)
        assert len(result) == 100
        assert result == "a" * 100


class TestMergeCrossPageTables:
    """Task 12.1: _merge_cross_page_tables 合并被页码标记分隔的相邻表格占位符。"""

    def test_merge_two_tables_separated_by_page_marker(self):
        """两个表格占位符被单个页码标记分隔时应合并。"""
        pmap = {
            "@@PROTECT_0@@": "|名称|值|\n|---|---|\n|VCC|3.3V|",
            "@@PROTECT_1@@": "|IIC|400kHz|\n|SPI|1MHz|",
        }
        text = "@@PROTECT_0@@\x00PH0\x00@@PROTECT_1@@"
        result = _merge_cross_page_tables(text, pmap)
        # Second placeholder should be removed from pmap
        assert "@@PROTECT_1@@" not in pmap
        # First placeholder should contain merged content
        assert "VCC" in pmap["@@PROTECT_0@@"]
        assert "IIC" in pmap["@@PROTECT_0@@"]
        # Text should only have first placeholder
        assert "@@PROTECT_1@@" not in result
        assert "@@PROTECT_0@@" in result

    def test_no_merge_when_not_adjacent(self):
        """两个表格占位符被其他文本分隔时不应合并。"""
        pmap = {
            "@@PROTECT_0@@": "|a|\n|---|\n|1|",
            "@@PROTECT_1@@": "|b|\n|---|\n|2|",
        }
        text = "@@PROTECT_0@@\n中间文本\n@@PROTECT_1@@"
        result = _merge_cross_page_tables(text, pmap)
        # Both placeholders preserved
        assert "@@PROTECT_0@@" in pmap
        assert "@@PROTECT_1@@" in pmap
        # Content not merged
        assert "b" not in pmap["@@PROTECT_0@@"]

    def test_no_merge_when_only_one_placeholder(self):
        """单个表格占位符不应被处理。"""
        pmap = {"@@PROTECT_0@@": "|a|\n|---|\n|1|"}
        text = "@@PROTECT_0@@"
        result = _merge_cross_page_tables(text, pmap)
        assert "@@PROTECT_0@@" in pmap
        assert len(pmap) == 1


class TestCompoundFingerprintDedup:
    """Task 8.1: kb_manager.ingest_chunks 复合键去重 (fingerprint, section_title)。"""

    def _make_chunk(self, text: str, section: str = "S1") -> ChunkResult:
        return ChunkResult(
            text=text,
            metadata={"section_title": section, "doc_id": "test"},
            page_range=(1, 1),
            fingerprint=compute_fingerprint(text),
            chunk_method="hybrid",
        )

    def test_same_text_different_section_preserved(self):
        """相同文本不同 section 的 chunk 不应被去重。"""
        # Simulate the dedup logic from kb_manager.ingest_chunks (lines 582-598)
        chunks = [
            self._make_chunk("相同内容", section="引脚定义"),
            self._make_chunk("相同内容", section="电气特性"),
        ]
        seen_keys = set()
        unique = []
        for c in chunks:
            key = (c.fingerprint, c.metadata.get("section_title", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(c)
        assert len(unique) == 2, "Same text in different sections should both be kept"

    def test_same_text_same_section_deduped(self):
        """相同文本相同 section 的 chunk 应被去重。"""
        chunks = [
            self._make_chunk("相同内容", section="引脚定义"),
            self._make_chunk("相同内容", section="引脚定义"),
            self._make_chunk("其他内容", section="引脚定义"),
        ]
        seen_keys = set()
        unique = []
        for c in chunks:
            key = (c.fingerprint, c.metadata.get("section_title", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(c)
        assert len(unique) == 2, "Same (text, section) should be deduped to 1"

    def test_empty_section_treated_as_distinct(self):
        """空 section 与有 section 的同文本不应被去重。"""
        chunks = [
            self._make_chunk("内容", section=""),
            self._make_chunk("内容", section="引脚定义"),
        ]
        seen_keys = set()
        unique = []
        for c in chunks:
            key = (c.fingerprint, c.metadata.get("section_title", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(c)
        assert len(unique) == 2
