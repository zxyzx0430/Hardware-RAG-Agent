"""Verify that multimodal chunker covers pages/images/tables behind hard questions.

Usage:
    python scripts/verify_hard_question_coverage.py
"""
from __future__ import annotations

import sys
import json
import re
from pathlib import Path

import yaml
import fitz  # PyMuPDF
import httpx

ROOT = Path(__file__).resolve().parent.parent

DATASET = ROOT / "data" / "benchmark" / "chunk-baseline-golden-v1.yaml"
PDF_ROOT = ROOT
API_BASE = "http://127.0.0.1:58080/api"

HARD_IDS = [
    # stm32f4 table/image questions
    "stm32f4-q005",
    "stm32f4-q006",
    "stm32f4-q009",
    # esp32 table/image questions
    "esp32-q005",
    "esp32-q006",
    "esp32-q007",
    "esp32-q009",
]


def pdf_page_stats(pdf_path: Path) -> dict:
    """Return text char count and embedded image count for each page."""
    doc = fitz.open(pdf_path)
    stats = {}
    for page in doc:
        text = page.get_text()
        img_count = len(page.get_images(full=True))
        # Also try find_tables for table detection
        try:
            tables = page.find_tables()
            table_count = len(tables.tables)
        except Exception:
            table_count = 0
        stats[page.number + 1] = {
            "chars": len(text),
            "images": img_count,
            "tables": table_count,
            "first_100": text[:100].replace("\n", " "),
        }
    doc.close()
    return stats


def get_kb_docs(kb_id: str = "builtin-001") -> list[dict]:
    r = httpx.get(f"{API_BASE}/kb/list", params={"kb_id": kb_id}, timeout=30.0)
    r.raise_for_status()
    payload = r.json()
    return payload.get("data", {}).get("documents", [])


def _get_doc_chunks_direct(doc_id: str, kb_id: str = "builtin-001") -> list[dict]:
    """Fallback: query ChromaDB directly via kb_manager (avoids API cache/state bugs)."""
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from src.rag.kb_manager import get_kb_manager
        kbm = get_kb_manager()
        chunks = kbm.get_doc_chunks(kb_id, doc_id)
        # Normalize to same shape as API response
        return [
            {
                "id": c.get("id", ""),
                "content": c.get("content", ""),
                "text": c.get("content", ""),
                "page_start": c.get("metadata", {}).get("page_start"),
                "page_end": c.get("metadata", {}).get("page_end"),
                "metadata": c.get("metadata", {}),
            }
            for c in chunks
        ]
    finally:
        sys.path.remove(str(ROOT / "backend"))


def get_doc_chunks(doc_id: str, kb_id: str = "builtin-001") -> list[dict]:
    r = httpx.get(f"{API_BASE}/kb/documents/{doc_id}/chunks", params={"kb_id": kb_id}, timeout=30.0)
    r.raise_for_status()
    payload = r.json()
    chunks: list[dict] = []
    if isinstance(payload, dict):
        chunks = payload.get("data", {}).get("chunks", payload.get("chunks", []))
    else:
        chunks = payload
    # API may return empty due to stale Chroma client; fall back to direct DB access.
    if not chunks:
        chunks = _get_doc_chunks_direct(doc_id, kb_id)
    return chunks


def parse_page_range(chunk: dict) -> set[int]:
    pages = set()
    # Check both top-level and metadata keys
    sources = [chunk, chunk.get("metadata", {})]
    for src in sources:
        for key in ("page_range", "page_start", "page_end", "pages"):
            v = src.get(key)
            if not v and v != 0:
                continue
            if isinstance(v, list):
                for p in v:
                    try:
                        pages.add(int(p))
                    except Exception:
                        pass
            elif isinstance(v, int):
                pages.add(v)
            elif isinstance(v, str):
                for part in re.split(r"[-,\s]+", v):
                    try:
                        pages.add(int(part))
                    except Exception:
                        pass
    return pages


def main() -> None:
    with open(DATASET, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    samples = {s["id"]: s for s in data["samples"]}

    docs = get_kb_docs()
    # Map by title basename -> doc_id
    doc_map = {Path(d.get("title", "")).name: d["doc_id"] for d in docs}
    print(f"Found {len(docs)} KB documents")

    for sid in HARD_IDS:
        s = samples[sid]
        pdf_rel = s["source_pdf"]
        pdf_name = Path(pdf_rel).name
        doc_id = doc_map.get(pdf_name)
        # Also try matching by dataset's declared doc_id (some docs use -v2 suffix)
        if not doc_id:
            for d in docs:
                if d.get("doc_id", "").startswith(Path(pdf_rel).stem) or pdf_name in d.get("title", ""):
                    doc_id = d["doc_id"]
                    break
        if not doc_id:
            print(f"\n[{sid}] DOC NOT FOUND IN KB: {pdf_rel} (tried {pdf_name})")
            print(f"  available titles={list(doc_map.keys())}")
            continue

        pdf_path = PDF_ROOT / pdf_rel
        pages = set(s["source_pages"])
        print(f"\n{'='*70}")
        print(f"[{sid}] {s['query']}")
        print(f"  type={s['question_type']} | pages={sorted(pages)}")
        print(f"  pdf={pdf_rel} | doc_id={doc_id}")

        # PDF native stats for source pages
        stats = pdf_page_stats(pdf_path)
        print(f"\n  PDF native analysis:")
        for p in sorted(pages):
            st = stats.get(p, {})
            print(f"    p{p}: chars={st.get('chars', 0):>5} images={st.get('images', 0)} tables={st.get('tables', 0)}")

        # Chunks coverage
        chunks = get_doc_chunks(doc_id)
        covered_pages = set()
        image_desc_pages = set()
        for c in chunks:
            c_pages = parse_page_range(c)
            covered_pages.update(c_pages)
            text = c.get("content", "") or c.get("text", "")
            if "[图片描述" in text or "image description" in text.lower():
                image_desc_pages.update(c_pages)

        # Debug: show page range distribution
        all_page_starts = [c.get("page_start") for c in chunks if c.get("page_start") is not None]
        all_page_ends = [c.get("page_end") for c in chunks if c.get("page_end") is not None]
        print(f"\n  Debug page range: starts={min(all_page_starts) if all_page_starts else None}-{max(all_page_starts) if all_page_starts else None}, "
              f"ends={min(all_page_ends) if all_page_ends else None}-{max(all_page_ends) if all_page_ends else None}")
        print(f"  Sample chunks pages: {[(c.get('page_start'), c.get('page_end')) for c in chunks[:5]]}")

        print(f"\n  Chunk coverage:")
        print(f"    total chunks={len(chunks)}")
        for p in sorted(pages):
            covered = "✅" if p in covered_pages else "❌"
            img = " (image_desc)" if p in image_desc_pages else ""
            print(f"    p{p}: {covered}{img}")

        # Check if any source page missing
        missing = pages - covered_pages
        if missing:
            print(f"\n  ⚠️ MISSING PAGES: {sorted(missing)}")
        else:
            print(f"\n  ✅ All source pages covered by chunks")

        # Keyword presence in chunks
        expected = s["expected_answer"]
        all_text = "\n".join(c.get("text", "") for c in chunks).lower()
        # Extract key named entities / numbers
        keywords = set()
        for m in re.finditer(r"\b[A-Z]{2,}\d+[A-Z]?\b", expected):
            keywords.add(m.group())
        for m in re.finditer(r"\b\d+\s*(?:µA|mA|V|MHz|KB|kB|MB)\b", expected, re.IGNORECASE):
            raw = m.group().lower()
            keywords.add(raw.replace(" ", ""))  # e.g. "448kb"
            keywords.add(raw)  # e.g. "448 kb"
        if not keywords:
            # fallback: split by punctuation, drop parenthetical explanations
            keywords = set()
            for w in re.split(r"[，。、；：\s]", expected):
                w = re.sub(r"[（(].*?[）)]", "", w).strip()
                if len(w) > 3 and len(w) < 20:
                    keywords.add(w)

        # Normalize spaces for unit-bearing keywords so "448kb" matches "448 kb" in text.
        norm_text = all_text.replace(" ", "")

        def _is_covered(k: str) -> bool:
            if k.lower() in all_text:
                return True
            if k.replace(" ", "").lower() in norm_text:
                return True
            return False

        covered_kws = {k for k in keywords if _is_covered(k)}
        missing_kws = keywords - covered_kws
        print(f"\n  Keyword coverage across whole KB document:")
        print(f"    checked={sorted(keywords)}")
        print(f"    covered={len(covered_kws)}/{len(keywords)}: {sorted(covered_kws)}")
        if missing_kws:
            print(f"    ❌ missing={sorted(missing_kws)}")


if __name__ == "__main__":
    main()
