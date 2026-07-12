"""Diagnose context_recall=0 by comparing retrieval_context vs expected_answer keywords.

Usage:
    python scripts/diag_context_recall.py
"""
from __future__ import annotations

import os
import sys
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from tests.rag_eval.run_golden_eval import RAGChatClient, RAG_OPTIMIZED_SYSTEM_PROMPT

DATASET = ROOT / "data" / "benchmark" / "chunk-baseline-golden-v1.yaml"
API_BASE = os.getenv("API_BASE", "http://127.0.0.1:58080/api")
API_KEY = os.getenv("LLM_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")
BASE_URL = os.getenv("LLM_BASE_URL", "")
KB_ID = "builtin-001"
TARGET_IDS = ["ch340g-q007", "stm32f4-q001", "stm32f4-q002"]


def extract_keywords(text: str) -> set[str]:
    """Extract key terms (numbers, model names, register values) from text."""
    kws = set()
    # Model names: MAX213, MAX232, CH340G, etc.
    for m in re.finditer(r"\b[A-Z]{2,}\d+[A-Z]?\b", text):
        kws.add(m.group())
    # Frequencies: 12MHz
    for m in re.finditer(r"\b\d+\s*MHz\b", text, re.IGNORECASE):
        kws.add(m.group().lower())
    # Register values: MODER=00, 0x00
    for m in re.finditer(r"\b(?:MODER|OSPEEDR|OTYPER|PUPDR)[^a-zA-Z]*\d+\b", text):
        kws.add(m.group())
    # Binary: 00, 01, 10, 11 (in context of modes)
    for m in re.finditer(r"\b(?:输入模式|输出模式|复用功能|模拟模式|Input mode)\b", text):
        kws.add(m.group())
    return kws


def main() -> None:
    with open(DATASET, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    samples = {s["id"]: s for s in data["samples"]}

    client = RAGChatClient(
        api_base_url=API_BASE, api_key=API_KEY, model=MODEL, base_url=BASE_URL,
        kb_ids=[KB_ID], top_k=8, system_prompt=RAG_OPTIMIZED_SYSTEM_PROMPT,
    )

    for sid in TARGET_IDS:
        s = samples[sid]
        query = s["query"]
        expected = s["expected_answer"]
        print(f"\n{'='*70}")
        print(f"[{sid}] {query}")
        print(f"  source: {s['source_pdf']} p{s['source_pages']}")
        print(f"  expected_answer: {expected}")
        print(f"{'='*70}")

        answer, ctx, _ = client.chat(query)
        print(f"\n  actual_output ({len(answer)} chars): {answer[:300]}...")
        print(f"\n  retrieval_context: {len(ctx)} chunks")
        for i, c in enumerate(ctx):
            print(f"\n  --- chunk {i+1} ({len(c)} chars) ---")
            print(f"  {c[:400]}{'...' if len(c)>400 else ''}")

        # Keyword coverage
        exp_kws = extract_keywords(expected)
        if exp_kws:
            all_ctx = " ".join(ctx).lower()
            covered = {k for k in exp_kws if k.lower() in all_ctx}
            missing = exp_kws - covered
            print(f"\n  === KEYWORD COVERAGE ===")
            print(f"  expected keywords: {sorted(exp_kws)}")
            print(f"  covered ({len(covered)}/{len(exp_kws)}): {sorted(covered)}")
            print(f"  MISSING ({len(missing)}): {sorted(missing)}")
        print()


if __name__ == "__main__":
    main()
