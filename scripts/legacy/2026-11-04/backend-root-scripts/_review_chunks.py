"""Review chunk boundaries and page coverage for a KB document."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

API_BASE = "http://127.0.0.1:58080"


def get_doc_chunks(doc_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("chunks", [])


def review_document(doc_id: str, title: str, expected_pages: int | None = None) -> dict:
    chunks = get_doc_chunks(doc_id)
    if not chunks:
        return {"doc_id": doc_id, "title": title, "chunks": 0, "error": "no chunks"}

    covered_pages: set[int] = set()
    short_chunks = 0
    section_counts: dict[str, int] = {}
    for c in chunks:
        ps = c.get("page_start") or 0
        pe = c.get("page_end") or ps
        for p in range(ps, pe + 1):
            covered_pages.add(p)
        text = c.get("content", "")
        if len(text) < 100:
            short_chunks += 1
        sec = c.get("section_title", "")
        section_counts[sec] = section_counts.get(sec, 0) + 1

    page_list = sorted(covered_pages)
    gaps: list[int] = []
    if expected_pages and page_list:
        for p in range(1, expected_pages + 1):
            if p not in covered_pages:
                gaps.append(p)

    # detect over-split sections
    over_split = {s: n for s, n in section_counts.items() if n > 5}

    return {
        "doc_id": doc_id,
        "title": title,
        "chunks": len(chunks),
        "covered_pages": len(covered_pages),
        "page_range": f"{min(page_list)}-{max(page_list)}" if page_list else "",
        "expected_pages": expected_pages,
        "gaps": gaps,
        "short_chunks": short_chunks,
        "over_split_sections": over_split,
    }


def main():
    # Review kb-965a0ba3 multimodal PDF documents (after page marker fix)
    docs = [
        ("b9677b73-83c0-4ec3-ac94-ef7d1e426be9", "bme280_datasheet.pdf", 60),
    ]

    results = []
    for doc_id, title, pages in docs:
        print(f"Reviewing {title} ...")
        results.append(review_document(doc_id, title, pages))

    out = Path("_chunk_review.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved to {out}")
    for r in results:
        print(f"\n{r['title']}: {r['chunks']} chunks, {r['covered_pages']} pages covered")
        if r.get("gaps"):
            print(f"  gaps: {r['gaps']}")
        if r.get("short_chunks"):
            print(f"  short chunks: {r['short_chunks']}")
        if r.get("over_split_sections"):
            print(f"  over-split sections: {list(r['over_split_sections'].keys())[:5]}")


if __name__ == "__main__":
    main()
