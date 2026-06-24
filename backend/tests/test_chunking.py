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
