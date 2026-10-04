r"""
RAG Evaluation Runner — 量化测试 chunk 策略的优化程度

用法:
    cd E:\Desktop\agent\backend
    python -m tests.rag_eval.run_eval --api-key YOUR_KEY --model gpt-4o-mini

    # 只测试不删 KB（方便排查）
    python -m tests.rag_eval.run_eval --api-key YOUR_KEY --model gpt-4o-mini --keep-kb

    # 指定 embedding key（如果和 LLM key 不同）
    python -m tests.rag_eval.run_eval --api-key LLM_KEY --embedding-key EMB_KEY --model gpt-4o-mini

产出:
    1. JSON 详细结果: data/test_results/rag_eval_<timestamp>.json
    2. Markdown 报告: data/test_results/rag_eval_<timestamp>.md
    3. 控制台实时进度 + 最终评分摘要

评分体系 (100分制):
    - 召回命中率 (30分): 引用来源是否命中目标文档
    - 回答关键词覆盖 (25分): 回答中包含期望知识点的比例
    - chunk 语义完整性 (25分): 期望关键词组是否在同一 chunk 共现
    - chunk 边界质量 (10分): 代码块/表格/列表是否完整
    - 跨章节关联 (10分): 关联内容是否在相邻 chunk
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import re
import sys
import httpx
from pathlib import Path
from datetime import datetime

# P0: Force UTF-8 on stdout/stderr — Windows GBK console can't encode ✓/✗/△
# (UnicodeEncodeError: 'gbk' codec can't encode character '\u2713').
# This must happen before any print() call.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        pass
from typing import Optional

# Add backend to path for config import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tests.rag_eval.config import (
    TEST_DOCS_DIR, OUTPUT_DIR, TEST_DOC_FILES,
    API_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_BASE_URL,
    DEFAULT_EMBEDDING_MODEL, DEFAULT_EMBEDDING_BASE_URL,
    TOP_K, RELEVANCE_THRESHOLD, SCORE_WEIGHTS,
    QUESTIONS, STRATEGIES, StrategyVariant, TestQuestion,
)
from tests.rag_eval.chat_evidence import (
    count_evidence_statuses,
    extract_agent_context,
    summarize_chat_evidence,
)

CHAT_DEADLINE_SECONDS = 300.0
CHAT_READ_TIMEOUT_SECONDS = 45.0
_HTTPX_SYNC_CLIENT_CLASS = httpx.Client


def _chat_exception_code(exc: Exception) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "answer_deadline"
    if isinstance(exc, httpx.ConnectTimeout):
        return "answer_connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "answer_read_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "answer_transport_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"answer_http_error_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return "answer_transport_error"
    return "answer_client_error"


def _safe_strategy_error(exc: Exception) -> str:
    message = str(exc).strip()
    sensitive_markers = ("api_key", "authorization", "bearer ", "secret", "password", "token")
    if (
        not message
        or len(message) > 200
        or any(marker in message.lower() for marker in sensitive_markers)
        or "{" in message
        or "}" in message
        or "http://" in message.lower()
        or "https://" in message.lower()
    ):
        return f"strategy_failure_{type(exc).__name__}"
    return message


def _fetch_builtin_embedding_config() -> dict:
    """Read embedding config (model, base_url, decrypted api_key) from builtin-001 KB.

    The proxy 9router doesn't support embeddings, but builtin-001 uses Aliyun
    DashScope (text-embedding-v4) which works. We reuse that config for all
    test KBs so indexing succeeds without requiring a separate embedding key.
    """
    try:
        from app.api.auth import decrypt_key
        from app.db.database import SessionLocal
        from app.db.models import KnowledgeBase
        db = SessionLocal()
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == "builtin-001").first()
        if not kb:
            print("  ⚠ builtin-001 not found, falling back to config defaults")
            db.close()
            return {}
        config = {
            "embedding_model": kb.embedding_model or DEFAULT_EMBEDDING_MODEL,
            "embedding_base_url": kb.embedding_base_url or DEFAULT_EMBEDDING_BASE_URL,
            "embedding_api_key": "",
        }
        if kb.embedding_api_key_encrypted:
            try:
                config["embedding_api_key"] = decrypt_key(kb.embedding_api_key_encrypted)
            except Exception:
                pass
        db.close()
        print(f"  Reusing builtin-001 embedding: {config['embedding_model']} @ {config['embedding_base_url']}")
        return config
    except Exception as e:
        print(f"  ⚠ Failed to read builtin embedding config: {type(e).__name__}")
        return {}


# ═══════════════════════════════════════════════════════════════════
# API Client
# ═══════════════════════════════════════════════════════════════════
class RAGTestClient:
    """HTTP client for RAG API calls."""

    def __init__(self, api_key: str, model: str, base_url: str,
                 embedding_key: str, embedding_model: str, embedding_base_url: str,
                 builtin_embedding: Optional[dict] = None, use_agent: bool = True,
                 chat_deadline_seconds: float = CHAT_DEADLINE_SECONDS,
                 chat_read_timeout_seconds: float = CHAT_READ_TIMEOUT_SECONDS):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.embedding_key = embedding_key
        self.embedding_model = embedding_model
        self.embedding_base_url = embedding_base_url
        # If builtin_embedding is provided, it overrides the CLI/embedding args
        # (used when the LLM proxy doesn't support embeddings — we reuse the
        # builtin-001 KB's DashScope config instead).
        self.builtin_embedding = builtin_embedding or {}
        self.use_agent = use_agent
        self.client = httpx.Client(timeout=120.0)
        self.chat_deadline_seconds = chat_deadline_seconds
        self.chat_read_timeout_seconds = chat_read_timeout_seconds
        self._sync_test_transport_injected = httpx.Client is not _HTTPX_SYNC_CLIENT_CLASS

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "x-model": self.model,
            "x-base-url": self.base_url,
            "x-provider": "openai",
        }

    def create_kb(self, name: str, strategy: StrategyVariant) -> dict:
        """Create a knowledge base with specific chunk strategy."""
        # Use builtin embedding config if available (proxy doesn't support embeddings)
        emb = self.builtin_embedding or {}
        payload = {
            "name": name,
            "chunk_method": strategy.chunk_method,
            "embedding_model": strategy.embedding_model or emb.get("embedding_model") or self.embedding_model,
            "embedding_base_url": emb.get("embedding_base_url") or self.embedding_base_url,
            "embedding_api_key": emb.get("embedding_api_key") or self.embedding_key,
            "description": strategy.description,
        }
        if strategy.agent_chunker_model:
            payload["agent_chunker_model"] = strategy.agent_chunker_model
            payload["agent_chunker_base_url"] = self.base_url
            payload["agent_chunker_api_key"] = self.api_key

        resp = self.client.post(f"{API_BASE_URL}/kb/collections", json=payload)
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Create KB failed: {data}")
        return data["data"]

    def delete_kb(self, kb_id: str) -> bool:
        """Delete a knowledge base."""
        try:
            resp = self.client.delete(f"{API_BASE_URL}/kb/collections/{kb_id}")
            return resp.status_code == 200
        except Exception:
            return False

    def upload_doc(self, kb_id: str, file_path: Path, chunk_method: str = "",
                   small_chunk_size: Optional[int] = None) -> dict:
        """Upload a document to a knowledge base."""
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            data = {"kb_id": kb_id}
            if chunk_method:
                data["chunk_method"] = chunk_method
            if small_chunk_size:
                data["small_chunk_size"] = str(small_chunk_size)
            resp = self.client.post(
                f"{API_BASE_URL}/kb/upload",
                files=files,
                data=data,
                timeout=300.0,  # 5 min for large files / indexing
            )
        result = resp.json()
        if not result.get("success"):
            raise RuntimeError(f"Upload failed for {file_path.name}: {result}")
        return result["data"]

    def list_docs(self, kb_id: str) -> list[dict]:
        """List documents in a knowledge base (uses /kb/collections/{kb_id})."""
        resp = self.client.get(f"{API_BASE_URL}/kb/collections/{kb_id}")
        data = resp.json()
        if data.get("success"):
            return data.get("data", {}).get("documents", [])
        return []

    def get_doc_chunks(self, doc_id: str) -> list[dict]:
        """Get all chunks of a document."""
        resp = self.client.get(f"{API_BASE_URL}/kb/documents/{doc_id}/chunks")
        data = resp.json()
        if data.get("success"):
            return data.get("data", {}).get("chunks", [])
        return []

    def chat(self, question: str, kb_ids: list[str]) -> dict:
        """Send a chat question and collect SSE events.

        Preserve partial evidence if the total request deadline expires.
        """
        payload = {
            "messages": [{"role": "user", "content": question}],
            "model": self.model,
            "top_k": TOP_K,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "kb_ids": kb_ids,
            "use_agent": self.use_agent,
        }
        state = {
            "answer_parts": [], "sources": [], "thinking_parts": [], "tools": [],
            "evidence_events": [], "error": "",
        }
        if self._sync_test_transport_injected:
            self._receive_chat_sync_test_transport(payload, state)
        else:
            try:
                asyncio.run(self._receive_chat_async(payload, state))
            except Exception as exc:
                state["error"] = state["error"] or _chat_exception_code(exc)

        answer = "".join(state["answer_parts"])
        contexts, quality_input_status, parent_metadata = extract_agent_context(
            state["evidence_events"], state["sources"],
        )
        evidence = summarize_chat_evidence(state["evidence_events"], answer, self.use_agent)
        if evidence["status"] == "awaiting_confirmation":
            state["error"] = state["error"] or "awaiting_confirmation"
        elif not evidence["done_succeeded"]:
            state["error"] = state["error"] or "answer_incomplete"
        safe_source_fields = (
            "id", "title", "doc", "page", "pages", "chunk_index", "page_start",
            "page_end", "section_title", "score", "kb_id", "kb_name",
            "small_chunk_id", "big_chunk_id", "excerpt",
        )
        source_evidence = [
            *[
                {key: event[key] for key in safe_source_fields if key in event}
                for event in state["sources"]
            ],
            *[{"origin": "search_docs_tool_result", **row} for row in parent_metadata],
        ]
        evidence.update({
            "answer_error": state["error"],
            "quality_input_status": quality_input_status,
            "quality_input_count": len(contexts) if quality_input_status == "complete" else 0,
            "source_evidence": source_evidence,
        })
        return {
            "answer": answer,
            "sources": state["sources"],
            "agent_context": contexts,
            "quality_input_status": quality_input_status,
            "source_evidence": source_evidence,
            "thinking": "".join(state["thinking_parts"]),
            "tools": state["tools"],
            "error": state["error"],
            "path_evidence": evidence,
        }

    @staticmethod
    def _consume_chat_line(line: str, state: dict) -> None:
        if not line or not line.startswith("data:"):
            return
        raw = line[5:].strip()
        if not raw:
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            state["error"] = state["error"] or "answer_stream_format"
            return
        if not isinstance(event, dict):
            state["error"] = state["error"] or "answer_stream_format"
            return
        event_type = event.get("type", "")
        if event_type in (
            "source", "tool_call", "tool_result", "tool", "tool_confirm",
            "tool_confirm_required", "awaiting_confirmation", "error", "done",
        ):
            state["evidence_events"].append(event)
        if event_type == "text":
            content = event.get("content")
            if isinstance(content, str):
                state["answer_parts"].append(content)
        elif event_type == "source":
            state["sources"].append(event)
        elif event_type == "thinking" and event.get("source") != "reasoning":
            content = event.get("content")
            if isinstance(content, str):
                state["thinking_parts"].append(content)
        elif event_type == "tool":
            state["tools"].append(event)
        elif event_type == "error":
            state["error"] = state["error"] or "answer_stream_error"

    def _receive_chat_sync_test_transport(self, payload: dict, state: dict) -> None:
        timeout = httpx.Timeout(
            connect=15.0, read=self.chat_read_timeout_seconds,
            write=15.0, pool=15.0,
        )
        deadline = time.monotonic() + self.chat_deadline_seconds
        try:
            with self.client.stream(
                "POST", f"{API_BASE_URL}/chat", json=payload,
                headers=self._headers(), timeout=timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("chat deadline")
                    self._consume_chat_line(line, state)
        except Exception as exc:
            state["error"] = state["error"] or _chat_exception_code(exc)

    async def _receive_chat_async(self, payload: dict, state: dict) -> None:
        timeout = httpx.Timeout(
            connect=15.0, read=self.chat_read_timeout_seconds,
            write=15.0, pool=15.0,
        )
        deadline = time.monotonic() + self.chat_deadline_seconds

        async def receive() -> None:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", f"{API_BASE_URL}/chat", json=payload,
                    headers=self._headers(),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if time.monotonic() >= deadline:
                            raise TimeoutError("chat deadline")
                        self._consume_chat_line(line, state)

        try:
            await asyncio.wait_for(receive(), timeout=self.chat_deadline_seconds)
        except (asyncio.TimeoutError, TimeoutError):
            state["error"] = state["error"] or "answer_deadline"
        except Exception as exc:
            state["error"] = state["error"] or _chat_exception_code(exc)


# ═══════════════════════════════════════════════════════════════════
# Evaluator
# ═══════════════════════════════════════════════════════════════════
class Evaluator:
    """Score RAG results across 5 dimensions."""

    def __init__(self, weights: dict = SCORE_WEIGHTS):
        self.weights = weights

    def evaluate_question(
        self,
        q: TestQuestion,
        answer: str,
        sources: list[dict],
        chunks_by_doc: dict[str, list[dict]],
    ) -> dict:
        """Evaluate a single question. Returns detailed scores."""
        # 1. Recall: did sources hit the target document?
        recall_score, recall_detail = self._score_recall(q, sources)

        # 2. Answer keyword coverage
        answer_score, answer_detail = self._score_answer_coverage(q, answer)

        # 3. Chunk semantic completeness
        chunk_score, chunk_detail = self._score_chunk_completeness(q, sources, chunks_by_doc)

        # 4. Chunk boundary quality
        boundary_score, boundary_detail = self._score_chunk_boundary(q, sources, chunks_by_doc)

        # 5. Cross-section association
        cross_score, cross_detail = self._score_cross_section(q, sources, chunks_by_doc)

        total = recall_score + answer_score + chunk_score + boundary_score + cross_score

        return {
            "question_id": q.id,
            "question": q.question,
            "target_doc": q.target_doc,
            "difficulty": q.difficulty,
            "scores": {
                "recall": {"score": recall_score, "max": self.weights["recall"], "detail": recall_detail},
                "answer_coverage": {"score": answer_score, "max": self.weights["answer_coverage"], "detail": answer_detail},
                "chunk_completeness": {"score": chunk_score, "max": self.weights["chunk_completeness"], "detail": chunk_detail},
                "chunk_boundary": {"score": boundary_score, "max": self.weights["chunk_boundary"], "detail": boundary_detail},
                "cross_section": {"score": cross_score, "max": self.weights["cross_section"], "detail": cross_detail},
            },
            "total_score": total,
            "max_score": sum(self.weights.values()),
            "answer_preview": answer[:500] if answer else "(empty)",
            "sources_count": len(sources),
            "source_titles": [s.get("title", "") for s in sources[:5]],
            # Preserve the entire captured excerpt; hidden clipping changes the
            # evidence used by the diagnostic and prevents faithful rescoring.
            "sources_full": [
                {
                    key: s[key]
                    for key in (
                        "id", "title", "doc", "page", "pages", "page_start", "page_end",
                        "section_title", "chunk_index", "score", "kb_id", "kb_name",
                        "big_chunk_id", "small_chunk_id", "doc_id", "chunk_id",
                    )
                    if key in s
                } | {
                    "excerpt": s.get("excerpt", "") or s.get("content", ""),
                }
                for s in sources
            ],
        }

    def _score_recall(self, q: TestQuestion, sources: list[dict]) -> tuple[float, str]:
        """Check if sources hit the target document."""
        if not sources:
            return 0.0, "No sources returned"
        target = q.target_doc
        for s in sources:
            title = s.get("title", "") or s.get("doc", "")
            if target in title or title in target:
                return float(self.weights["recall"]), f"Hit target doc: {title}"
        # Partial credit: any source returned
        return self.weights["recall"] * 0.3, f"Sources returned but target doc '{target}' not found. Got: {[s.get('title','') for s in sources[:3]]}"

    def _score_answer_coverage(self, q: TestQuestion, answer: str) -> tuple[float, str]:
        """Check if answer covers expected keywords."""
        if not q.expected_keywords:
            return float(self.weights["answer_coverage"]), "No keywords defined"
        if not answer:
            return 0.0, "Empty answer"

        answer_lower = answer.lower()
        hit_groups = 0
        missed = []
        for group in q.expected_keywords:
            # Check if any synonym in the group is present
            found = any(kw.lower() in answer_lower for kw in group)
            if found:
                hit_groups += 1
            else:
                missed.append(group[0])  # Report first synonym

        coverage = hit_groups / len(q.expected_keywords)
        score = coverage * self.weights["answer_coverage"]
        detail = f"Covered {hit_groups}/{len(q.expected_keywords)} keyword groups"
        if missed:
            detail += f". Missed: {', '.join(missed[:5])}"
        return score, detail

    def _score_chunk_completeness(self, q: TestQuestion, sources: list[dict],
                                   chunks_by_doc: dict[str, list[dict]]) -> tuple[float, str]:
        """Check if expected keyword groups co-occur in the same chunk."""
        if not q.chunk_cooccur_groups:
            return float(self.weights["chunk_completeness"]), "No co-occurrence groups defined"

        # Collect all chunk texts from sources
        chunk_texts = []
        for s in sources:
            excerpt = s.get("excerpt", "")
            if excerpt:
                chunk_texts.append(excerpt)
        # Also check chunks_by_doc
        for doc_chunks in chunks_by_doc.values():
            for c in doc_chunks:
                chunk_texts.append(c.get("content", ""))

        if not chunk_texts:
            return 0.0, "No chunk text available"

        passed_groups = 0
        details = []
        for group in q.chunk_cooccur_groups:
            # Check if all keywords in this group appear in the SAME chunk
            found_in_same = False
            for text in chunk_texts:
                text_lower = text.lower()
                if all(kw.lower() in text_lower for kw in group):
                    found_in_same = True
                    break
            if found_in_same:
                passed_groups += 1
                details.append(f"✓ Group {group} found in same chunk")
            else:
                # Check if keywords exist but scattered across chunks
                all_texts = " ".join(chunk_texts).lower()
                scattered = all(kw.lower() in all_texts for kw in group)
                if scattered:
                    details.append(f"△ Group {group} keywords exist but scattered across chunks")
                else:
                    details.append(f"✗ Group {group} not fully found in any chunk")

        score = (passed_groups / len(q.chunk_cooccur_groups)) * self.weights["chunk_completeness"]
        return score, "; ".join(details)

    def _score_chunk_boundary(self, q: TestQuestion, sources: list[dict],
                               chunks_by_doc: dict[str, list[dict]]) -> tuple[float, str]:
        """Check chunk boundary quality: code blocks, tables, lists, sentences intact.

        Stricter than before — checks 6 boundary quality signals:
        1. Unclosed code blocks (odd ``` count)
        2. Code block at chunk start without context (starts with ```)
        3. Sentence truncation (ends mid-sentence, not at natural boundary)
        4. Broken table (starts with | but no header separator)
        5. Bare heading (line is only # markers, no title text)
        6. Bare list marker at end (ends with - or * alone)
        """
        # P0 fix: Only check chunks returned as RAG sources (top-k=5), not all
        # 200-300 chunks in the doc. Checking all chunks guaranteed 0/10 due to
        # false positives accumulating. Source chunks are what users actually see.
        chunk_texts = []
        for s in sources:
            excerpt = s.get("excerpt", "") or s.get("content", "")
            if excerpt:
                chunk_texts.append(excerpt)

        if not chunk_texts:
            return 0.0, "No source chunks returned"

        issues = []
        # Natural-ending characters: a well-formed chunk should end at one of these
        # (sentence punctuation, code fence close, table row, list item, heading, or blank line)
        _NATURAL_END_CHARS = set("。！？.!?：:；;）)】》\"'\n```|-*#> \t")

        for i, text in enumerate(chunk_texts):
            stripped = text.strip()
            if not stripped:
                continue

            # 1. Unclosed code blocks (genuine boundary break)
            code_fence_count = text.count("```")
            if code_fence_count % 2 != 0:
                issues.append(f"src#{i}: unclosed code block (```count={code_fence_count})")

            # 2. Code block at chunk start without context — only flag if also unclosed
            if stripped.startswith("```") and code_fence_count % 2 != 0:
                issues.append(f"src#{i}: starts with code fence, no matching close")

            # 3. Sentence truncation — P0 fix: don't flag CJK chars at end
            # (too many false positives; CJK sentences often omit final punctuation
            # in chunked text). Only flag clear ASCII mid-word truncation.
            last_char = stripped[-1]
            if last_char not in _NATURAL_END_CHARS:
                if last_char.isascii() and last_char.isalpha():
                    # Check last 5 chars: if all ASCII alpha, likely mid-word
                    tail = stripped[-5:]
                    ascii_alpha_tail = [c for c in tail if c.isascii() and c.isalpha()]
                    if len(ascii_alpha_tail) >= 4:
                        issues.append(f"src#{i}: ends mid-word (tail='{tail}')")
                # CJK chars at end: don't flag

            # 4. Broken table — P0 fix: only flag if chunk is a tiny fragment (<3 lines)
            # A multi-line chunk starting with | is likely a mid-table slice (normal).
            lines = text.split("\n")
            if (lines and lines[0].strip().startswith("|")
                    and len(lines) < 3):
                issues.append(f"src#{i}: table fragment (only {len(lines)} lines)")

            # 5. Bare heading (only # marks, no title text) — genuine issue
            for j, line in enumerate(lines):
                line_s = line.strip()
                if line_s and len(line_s) <= 6 and all(c == "#" for c in line_s):
                    issues.append(f"src#{i}: bare heading at line {j}")
                    break

            # 6. Bare list marker at end (chunk ends with just "-" or "*")
            if (stripped.endswith("-") or stripped.endswith("*")) and len(stripped) < 5:
                issues.append(f"src#{i}: ends with bare list marker")

        if not issues:
            return float(self.weights["chunk_boundary"]), f"All {len(chunk_texts)} src chunks clean"

        # P0: Proportional scoring (was: 2 pts/issue, capping at 0 after 5 issues).
        # New tiered deduction preserves discrimination across strategies.
        n_issues = len(issues)
        if n_issues == 1:
            deduction = 2
        elif n_issues <= 3:
            deduction = 5
        else:
            deduction = 8
        score = max(0.0, self.weights["chunk_boundary"] - deduction)
        return score, f"{n_issues} issues in {len(chunk_texts)} srcs: {'; '.join(issues[:3])}"

    def _score_cross_section(self, q: TestQuestion, sources: list[dict],
                              chunks_by_doc: dict[str, list[dict]]) -> tuple[float, str]:
        """Check if cross-section keywords appear in adjacent or same chunks.

        Three-tier scoring for better discrimination:
        - Same chunk (distance 0): full score — chunk is semantically complete
        - Adjacent chunks (distance 1): 80% — good cross-section association
        - Same doc but far (distance > 1): 30% — keywords exist but weakly associated
        - Not found: 0
        """
        if not q.cross_section_keywords:
            return float(self.weights["cross_section"]), "No cross-section keywords defined"

        # Get ordered chunks from target doc
        target_chunks = []
        for doc_name, chunks in chunks_by_doc.items():
            if q.target_doc in doc_name:
                target_chunks = sorted(chunks, key=lambda c: c.get("chunk_index", 0))
                break

        if len(target_chunks) < 2:
            # Fallback: check if all keywords exist in any source
            all_text = " ".join(s.get("excerpt", "") for s in sources).lower()
            if all(kw.lower() in all_text for kw in q.cross_section_keywords):
                return self.weights["cross_section"] * 0.5, "Keywords found but can't verify adjacency"
            return 0.0, "Not enough chunks to check adjacency"

        # Find all chunk positions where each keyword appears
        kw_positions = {kw: [] for kw in q.cross_section_keywords}
        for i, chunk in enumerate(target_chunks):
            text = chunk.get("content", "").lower()
            for kw in q.cross_section_keywords:
                if kw.lower() in text:
                    kw_positions[kw].append(i)

        # Check all keyword pairs and find the minimum distance
        keywords = list(q.cross_section_keywords)
        best_distance = float('inf')
        best_pair = None

        for i in range(len(keywords)):
            for j in range(i + 1, len(keywords)):
                for pos_i in kw_positions[keywords[i]]:
                    for pos_j in kw_positions[keywords[j]]:
                        dist = abs(pos_i - pos_j)
                        if dist < best_distance:
                            best_distance = dist
                            best_pair = (keywords[i], keywords[j], pos_i, pos_j)

        if best_distance == float('inf'):
            # No pair found — check if at least some keywords exist
            found_count = sum(1 for positions in kw_positions.values() if positions)
            if found_count > 0:
                partial = (found_count / len(keywords)) * 0.2
                return self.weights["cross_section"] * partial, f"Only {found_count}/{len(keywords)} keywords found: {kw_positions}"
            return 0.0, f"No cross-section keywords found: {kw_positions}"

        # Score based on best distance
        if best_distance == 0:
            # Same chunk — best possible
            score = float(self.weights["cross_section"])
            detail = f"✓ {best_pair[0]}+{best_pair[1]} in same chunk#{best_pair[2]}"
        elif best_distance == 1:
            # Adjacent chunks — good
            score = self.weights["cross_section"] * 0.8
            detail = f"△ {best_pair[0]}(chunk#{best_pair[2]}) + {best_pair[1]}(chunk#{best_pair[3]}) adjacent"
        else:
            # Far apart — weak association
            score = self.weights["cross_section"] * 0.3
            detail = f"✗ {best_pair[0]}(chunk#{best_pair[2]}) + {best_pair[1]}(chunk#{best_pair[3]}) distance={best_distance}"

        return score, detail


# ═══════════════════════════════════════════════════════════════════
# Reporter
# ═══════════════════════════════════════════════════════════════════
class Reporter:
    """Generate JSON + Markdown reports."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_json(self, results: dict, timestamp: str) -> Path:
        path = self.output_dir / f"rag_eval_{timestamp}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        return path

    def save_markdown(self, results: dict, timestamp: str) -> Path:
        path = self.output_dir / f"rag_eval_{timestamp}.md"
        lines = []

        # Header
        lines.append(f"# RAG 评估报告")
        lines.append(f"\n> 生成时间: {results['timestamp']}")
        lines.append(f"> 测试问题: {results['total_questions']} 个")
        lines.append(f"> 聊天模式: {results['chat_mode']}（仅 agent 模式的有效样本可代表 Agent RAG）")
        lines.append(f"> 评测协议: {results.get('evaluation_kind', 'rule_based_diagnostic')}（非 DeepEval 标准质量评分）")
        lines.append(f"> 评分权重: 召回({results['weights']['recall']}) + 回答覆盖({results['weights']['answer_coverage']}) + chunk完整性({results['weights']['chunk_completeness']}) + 边界({results['weights']['chunk_boundary']}) + 跨章节({results['weights']['cross_section']})\n")

        # Strategy comparison table
        if len(results.get("strategies", [])) > 1:
            lines.append("## 策略对比\n")
            lines.append("| 策略 | 总分 | 召回 | 回答覆盖 | chunk完整性 | 边界 | 跨章节 |")
            lines.append("|------|------|------|----------|------------|------|--------|")
            for strat in results["strategies"]:
                if strat.get("scored_count", 0) == 0:
                    lines.append(f"| {strat['name']} | 未计分 | - | - | - | - | - |")
                else:
                    lines.append(f"| {strat['name']} | {strat['total_score']:.1f}/{strat['max_score']} | {strat['dimension_scores']['recall']:.1f} | {strat['dimension_scores']['answer_coverage']:.1f} | {strat['dimension_scores']['chunk_completeness']:.1f} | {strat['dimension_scores']['chunk_boundary']:.1f} | {strat['dimension_scores']['cross_section']:.1f} |")
            lines.append("")

        # Detailed results per strategy
        for strat in results.get("strategies", []):
            lines.append(f"\n## 策略: {strat['name']}")
            lines.append(f"\n**描述**: {strat['description']}")
            planned_count = strat.get("planned_count", len(strat.get("question_results", [])))
            attempted_count = strat.get("attempted_count", len(strat.get("question_results", [])))
            lines.append(f"**可计分题数**: {strat.get('scored_count', 0)}/{planned_count}")
            lines.append(f"**已尝试题数**: {attempted_count}/{planned_count}")
            lines.append(f"**路径状态**: {strat.get('path_status_counts', {})}")
            if strat.get("error"):
                lines.append(f"**失败原因**: {strat['error']}")
            if strat.get("scored_count", 0):
                lines.append(f"**总分**: {strat['total_score']:.1f} / {strat['max_score']}")
                lines.append(f"**平均分**: {strat['total_score'] / strat['max_score'] * 100:.1f}%\n")
            else:
                lines.append("**总分**: 未计分\n")

            # Per-question breakdown
            lines.append("### 逐题得分\n")
            lines.append("| 题目 | 难度 | 召回 | 回答覆盖 | chunk完整性 | 边界 | 跨章节 | 总分 |")
            lines.append("|------|------|------|----------|------------|------|--------|------|")
            for q_result in strat["question_results"]:
                if q_result.get("not_scored"):
                    lines.append(f"| {q_result['question_id']} | {q_result['difficulty']} | 未计分：{q_result['path_evidence']['status']} | - | - | - | - | - |")
                    continue
                s = q_result["scores"]
                lines.append(
                    f"| {q_result['question_id']} | {q_result['difficulty']} | "
                    f"{s['recall']['score']:.1f}/{s['recall']['max']} | "
                    f"{s['answer_coverage']['score']:.1f}/{s['answer_coverage']['max']} | "
                    f"{s['chunk_completeness']['score']:.1f}/{s['chunk_completeness']['max']} | "
                    f"{s['chunk_boundary']['score']:.1f}/{s['chunk_boundary']['max']} | "
                    f"{s['cross_section']['score']:.1f}/{s['cross_section']['max']} | "
                    f"**{q_result['total_score']:.1f}** |"
                )
            lines.append("")

            # Detailed issues
            lines.append("### 问题详情\n")
            for q_result in strat["question_results"]:
                lines.append(f"\n#### {q_result['question_id']} ({q_result['difficulty']})")
                lines.append(f"**问题**: {q_result['question']}")
                lines.append(f"**目标文档**: `{q_result['target_doc']}`")
                if q_result.get("not_scored"):
                    lines.append(f"**未计分**: {q_result['path_evidence']['status']}；{q_result.get('error', '')}")
                    continue
                lines.append(f"**得分**: {q_result['total_score']:.1f} / {q_result['max_score']}")
                lines.append(f"**引用来源数**: {q_result['sources_count']}")
                lines.append(f"**来源**: {', '.join(q_result['source_titles'][:3])}")
                lines.append(f"**回答预览**: {q_result['answer_preview'][:200]}...")
                lines.append("")
                for dim_name, dim_data in q_result["scores"].items():
                    lines.append(f"- **{dim_name}**: {dim_data['score']:.1f}/{dim_data['max']} — {dim_data['detail']}")
                lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path


# ═══════════════════════════════════════════════════════════════════
# Main Runner
# ═══════════════════════════════════════════════════════════════════
def run_evaluation(
    api_key: str,
    model: str,
    base_url: str,
    embedding_key: str,
    embedding_model: str,
    embedding_base_url: str,
    keep_kb: bool = False,
    strategies: list = None,
    use_agent: bool = True,
    questions: list[TestQuestion] | None = None,
    doc_files: list[str] | None = None,
    chat_deadline_seconds: float = CHAT_DEADLINE_SECONDS,
    chat_read_timeout_seconds: float = CHAT_READ_TIMEOUT_SECONDS,
) -> dict:
    """Run full RAG evaluation across all strategies."""
    strategies = strategies or STRATEGIES
    questions = QUESTIONS if questions is None else questions
    doc_files = TEST_DOC_FILES if doc_files is None else doc_files
    if (
        not math.isfinite(chat_deadline_seconds)
        or not math.isfinite(chat_read_timeout_seconds)
        or chat_deadline_seconds <= 0
        or chat_read_timeout_seconds <= 0
    ):
        raise ValueError("chat deadline and read timeout must be finite positive numbers")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*60}")
    print(f"RAG Evaluation Started — {timestamp}")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Strategies: {len(strategies)}")
    print(f"Questions: {len(questions)}")
    print(f"Documents: {len(doc_files)}")

    # Fetch builtin-001 embedding config (proxy doesn't support embeddings,
    # so we reuse the working DashScope config from builtin-001).
    builtin_emb = _fetch_builtin_embedding_config()

    client = RAGTestClient(
        api_key=api_key, model=model, base_url=base_url,
        embedding_key=embedding_key, embedding_model=embedding_model,
        embedding_base_url=embedding_base_url,
        builtin_embedding=builtin_emb,
        use_agent=use_agent,
        chat_deadline_seconds=chat_deadline_seconds,
        chat_read_timeout_seconds=chat_read_timeout_seconds,
    )
    evaluator = Evaluator(SCORE_WEIGHTS)
    reporter = Reporter(OUTPUT_DIR)

    all_strategy_results = []

    for strat_idx, strategy in enumerate(strategies):
        print(f"\n{'─'*60}")
        print(f"Strategy {strat_idx + 1}/{len(strategies)}: {strategy.name}")
        print(f"  Method: {strategy.chunk_method}")
        print(f"  Desc: {strategy.description}")
        print(f"{'─'*60}")

        kb_id = None
        question_results = []
        attempted_count = 0
        request_in_progress = False
        try:
            # Step 1: Create KB
            print("  [1/4] Creating knowledge base...")
            kb_info = client.create_kb(f"eval_{strategy.name}_{timestamp}", strategy)
            kb_id = kb_info["id"]
            print(f"        KB created: {kb_id}")

            # Step 2: Upload documents
            print("  [2/4] Uploading test documents...")
            doc_id_map = {}  # filename -> doc_id
            for doc_file in doc_files:
                doc_path = TEST_DOCS_DIR / doc_file
                if not doc_path.exists():
                    print(f"        ⚠ Skip (not found): {doc_file}")
                    continue
                print(f"        Uploading {doc_file}...", end=" ", flush=True)
                doc_info = client.upload_doc(kb_id, doc_path, strategy.chunk_method,
                                             small_chunk_size=strategy.small_chunk_size)
                doc_id = doc_info.get("doc_id", "")
                doc_id_map[doc_file] = doc_id
                status = doc_info.get("status", "unknown")
                print(f"done (status={status})")

                # Wait for indexing if needed (max 1800s, poll every 3s)
                # Agent chunking calls LLM per round per batch — with flaky
                # proxy APIs and max_retries=5, a single doc can take 10+ min.
                if status == "indexing":
                    print(f"        Waiting for indexing...", end=" ", flush=True)
                    waited = 0
                    done = False
                    for _ in range(600):  # 600 * 3s = 1800s max (30 min)
                        time.sleep(3)
                        waited += 3
                        docs = client.list_docs(kb_id)
                        for d in docs:
                            if d.get("doc_id") == doc_id:
                                st = d.get("status")
                                if st == "indexing":
                                    print(".", end="", flush=True)
                                else:
                                    print(f"done ({st}, {waited}s)", flush=True)
                                    done = True
                                    break
                        if done:
                            break
                    if not done:
                        print(f"TIMEOUT after {waited}s", flush=True)
                        raise RuntimeError(f"Indexing timeout for {doc_file}")
                docs = client.list_docs(kb_id)
                current = next((d for d in docs if d.get("doc_id") == doc_id), {})
                if current.get("status") != "indexed":
                    raise RuntimeError(
                        f"Indexing failed for {doc_file}: {current.get('status', 'missing')} "
                        f"{current.get('error_message', '')}"
                    )

            # Step 3: Fetch all chunks for each document (for chunk quality evaluation)
            print("  [3/4] Fetching chunks for evaluation...")
            chunks_by_doc = {}  # filename -> list of chunk dicts
            for doc_file, doc_id in doc_id_map.items():
                chunks = client.get_doc_chunks(doc_id)
                if not chunks:
                    raise RuntimeError(f"Indexed document has no retrievable chunks: {doc_file}")
                chunks_by_doc[doc_file] = chunks
                print(f"        {doc_file}: {len(chunks)} chunks")

            # Step 4: Run questions
            print("  [4/4] Running test questions...")
            for q_idx, q in enumerate(questions):
                print(f"\n    {q.id} ({q.difficulty}): {q.question[:60]}...")
                print(f"       Asking LLM...", end=" ", flush=True)
                attempted_count += 1
                request_in_progress = True
                chat_result = client.chat(q.question, [kb_id])
                request_in_progress = False
                evidence = chat_result["path_evidence"]
                if chat_result.get("error"):
                    print(f"ERROR: {chat_result['error']}")
                else:
                    print(f"done ({len(chat_result['answer'])} chars, {len(chat_result['sources'])} sources)")

                # Evaluate
                if chat_result.get("error") or (
                    use_agent and evidence["status"] != "verified_source_path"
                ):
                    q_result = {
                        "question_id": q.id, "question": q.question,
                        "difficulty": q.difficulty, "target_doc": q.target_doc,
                        "not_scored": True, "path_evidence": evidence,
                        "error": chat_result.get("error") or evidence["status"],
                        "actual_output": chat_result.get("answer", ""),
                        "answer_preview": chat_result.get("answer", "")[:500],
                        "sources_count": len(chat_result.get("sources", [])),
                        "sources_full": chat_result.get("sources", []),
                        "agent_context": chat_result.get("agent_context", []),
                        "quality_input_status": chat_result.get("quality_input_status", "unavailable"),
                        "source_evidence": chat_result.get("source_evidence", []),
                    }
                else:
                    q_result = evaluator.evaluate_question(
                        q, chat_result["answer"], chat_result["sources"], chunks_by_doc
                    )
                    q_result["path_evidence"] = evidence
                    q_result["actual_output"] = chat_result.get("answer", "")
                    q_result["agent_context"] = chat_result.get("agent_context", [])
                    q_result["quality_input_status"] = chat_result.get("quality_input_status", "unavailable")
                    q_result["source_evidence"] = chat_result.get("source_evidence", [])
                question_results.append(q_result)

                # Print score
                if q_result.get("not_scored"):
                    print(f"       Not scored: {evidence['status']}")
                else:
                    print(f"       Score: {q_result['total_score']:.1f}/{q_result['max_score']}")
                    for dim_name, dim_data in q_result["scores"].items():
                        print(f"         {dim_name}: {dim_data['score']:.1f}/{dim_data['max']} — {dim_data['detail'][:80]}")

            # Aggregate scores
            scored_results = [qr for qr in question_results if not qr.get("not_scored")]
            total_score = sum(qr["total_score"] for qr in scored_results) if scored_results else None
            max_score = sum(qr["max_score"] for qr in scored_results) if scored_results else None
            dim_scores = {}
            for dim in SCORE_WEIGHTS:
                dim_scores[dim] = (
                    sum(qr["scores"][dim]["score"] for qr in scored_results)
                    if scored_results else None
                )

            strategy_result = {
                "evaluation_kind": "rule_based_diagnostic",
                "name": strategy.name,
                "description": strategy.description,
                "chunk_method": strategy.chunk_method,
                "kb_id": kb_id,
                "total_score": total_score,
                "max_score": max_score,
                "dimension_scores": dim_scores,
                "question_results": question_results,
                "scored_count": len(scored_results),
                "eligible_count": len(scored_results),
                "planned_count": len(questions),
                "attempted_count": attempted_count,
                "path_status_counts": count_evidence_statuses([
                    qr["path_evidence"] for qr in question_results
                ]),
            }
            all_strategy_results.append(strategy_result)

            if scored_results:
                print(f"\n  Strategy total: {total_score:.1f}/{max_score} ({total_score/max_score*100:.1f}%)")
            else:
                print("\n  Strategy total: not scored (no verified Agent RAG path)")

        except Exception as e:
            error_code = _safe_strategy_error(e)
            print(f"\n  ✗ Strategy failed: {error_code}")
            import traceback
            traceback.print_exc()
            path_status_counts = count_evidence_statuses([
                qr["path_evidence"] for qr in question_results
            ])
            unfinished_attempts = attempted_count - len(question_results)
            if unfinished_attempts:
                status = "request_failed" if request_in_progress else "strategy_failed"
                path_status_counts[status] = path_status_counts.get(status, 0) + unfinished_attempts
            not_attempted = len(questions) - attempted_count
            if not_attempted:
                path_status_counts["not_attempted"] = not_attempted
            all_strategy_results.append({
                "evaluation_kind": "rule_based_diagnostic",
                "name": strategy.name,
                "description": strategy.description,
                "error": error_code,
                "total_score": None,
                "max_score": None,
                "scored_count": 0,
                "eligible_count": 0,
                "planned_count": len(questions),
                "attempted_count": attempted_count,
                "path_status_counts": path_status_counts,
                "question_results": question_results,
                "dimension_scores": {dim: None for dim in SCORE_WEIGHTS},
            })
        finally:
            if kb_id and not keep_kb:
                print(f"\n  Cleaning up KB {kb_id}...")
                client.delete_kb(kb_id)

    # Build final report
    final_results = {
        "timestamp": timestamp,
        "model": model,
        "total_questions": len(questions),
        "chat_mode": "agent" if use_agent else "legacy",
        "evaluation_kind": "rule_based_diagnostic",
        "weights": SCORE_WEIGHTS,
        "strategies": all_strategy_results,
    }

    # Save reports
    json_path = reporter.save_json(final_results, timestamp)
    md_path = reporter.save_markdown(final_results, timestamp)

    print(f"\n{'='*60}")
    print(f"Evaluation Complete!")
    print(f"{'='*60}")
    print(f"\nReports saved:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    print(f"\nSummary:")
    for strat in all_strategy_results:
        if "error" in strat:
            print(f"  {strat['name']}: FAILED ({strat['error']})")
        else:
            if strat.get("scored_count", 0):
                print(f"  {strat['name']}: {strat['total_score']:.1f}/{strat['max_score']} ({strat['total_score']/strat['max_score']*100:.1f}%)")
            else:
                print(f"  {strat['name']}: not scored")

    return final_results


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="RAG Evaluation — test chunk strategy quality")
    parser.add_argument("--api-key", required=True, help="LLM API Key")
    parser.add_argument("--model", default=DEFAULT_LLM_MODEL, help=f"LLM model name (default: {DEFAULT_LLM_MODEL})")
    parser.add_argument("--base-url", default=DEFAULT_LLM_BASE_URL, help="LLM base URL")
    parser.add_argument("--embedding-key", default="", help="Embedding API Key (default: same as --api-key)")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help=f"Embedding model (default: {DEFAULT_EMBEDDING_MODEL})")
    parser.add_argument("--embedding-base-url", default=DEFAULT_EMBEDDING_BASE_URL, help="Embedding base URL")
    parser.add_argument("--keep-kb", action="store_true", help="Keep KB after test (for debugging)")
    parser.add_argument("--strategies", default="", help="Comma-separated strategy names to run (default: all). e.g. 'agent-deepseek,hybrid-800'")
    parser.add_argument("--legacy-chat", action="store_true", help="Use non-Agent chat; scores are not Agent RAG quality")
    parser.add_argument("--smoke", action="store_true", help="One fixed Markdown document and its first two questions")
    parser.add_argument("--chat-deadline", type=float, default=CHAT_DEADLINE_SECONDS,
                        help="Overall SSE chat request deadline in seconds")
    parser.add_argument("--chat-read-timeout", type=float, default=CHAT_READ_TIMEOUT_SECONDS,
                        help="Timeout between SSE reads in seconds")
    args = parser.parse_args()

    embedding_key = args.embedding_key or args.api_key

    # Filter strategies if --strategies is specified
    strategies = STRATEGIES
    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",") if s.strip()]
        strategies = [s for s in STRATEGIES if s.name in names]
        if not strategies:
            print(f"No matching strategies found. Available: {[s.name for s in STRATEGIES]}")
            return

    run_evaluation(
        api_key=args.api_key,
        model=args.model,
        base_url=args.base_url,
        embedding_key=embedding_key,
        embedding_model=args.embedding_model,
        embedding_base_url=args.embedding_base_url,
        keep_kb=args.keep_kb,
        strategies=strategies,
        use_agent=not args.legacy_chat,
        questions=QUESTIONS[:2] if args.smoke else None,
        doc_files=["01-stm32-gpio.md"] if args.smoke else None,
        chat_deadline_seconds=args.chat_deadline,
        chat_read_timeout_seconds=args.chat_read_timeout,
    )


if __name__ == "__main__":
    main()
