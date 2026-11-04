"""Review chunks for 01-stm32-gpio.md in builtin-001."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

API_BASE = "http://127.0.0.1:58080"


def get_kb_docs(kb_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/list?kb_id={kb_id}", timeout=60)
    r.raise_for_status()
    return r.json().get("data", {}).get("documents", [])


def get_doc_chunks(doc_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    r.raise_for_status()
    return r.json().get("data", {}).get("chunks", [])


def review_md_chunks(doc_id: str, title: str) -> dict:
    chunks = get_doc_chunks(doc_id)
    if not chunks:
        return {"doc_id": doc_id, "title": title, "chunks": 0, "error": "no chunks"}

    total_len = 0
    short_chunks = 0
    empty_chunks = 0
    section_counts: dict[str, int] = {}
    boundary_issues = []

    for i, c in enumerate(chunks):
        text = c.get("content", "")
        total_len += len(text)
        if len(text) < 50:
            short_chunks += 1
        if not text.strip():
            empty_chunks += 1
        sec = c.get("section_title", "")
        section_counts[sec] = section_counts.get(sec, 0) + 1

        # Check for bad boundaries: chunk ending mid-sentence or mid-list
        stripped = text.strip()
        if stripped and not stripped[-1] in {'.', '。', '!', '！', '?', '？', '：', ':', ']', '）', ')'}:
            # Heuristic: if next chunk starts with lowercase/continuation, it may be OK
            if i + 1 < len(chunks):
                next_text = chunks[i + 1].get("content", "").lstrip()
                if next_text and next_text[0].isupper():
                    boundary_issues.append({
                        "idx": i,
                        "reason": "chunk ends without terminal punctuation and next chunk starts new sentence",
                        "tail": stripped[-60:],
                        "next_head": next_text[:60],
                    })

    return {
        "doc_id": doc_id,
        "title": title,
        "chunks": len(chunks),
        "total_chars": total_len,
        "avg_chunk_len": round(total_len / len(chunks), 1) if chunks else 0,
        "short_chunks": short_chunks,
        "empty_chunks": empty_chunks,
        "section_count": len(section_counts),
        "sections": list(section_counts.keys())[:20],
        "boundary_issues": boundary_issues[:10],
        "chunk_preview": [
            {
                "idx": i,
                "len": len(c.get("content", "")),
                "section": c.get("section_title", "")[:80],
                "head": c.get("content", "")[:200].replace("\n", " "),
            }
            for i, c in enumerate(chunks[:10])
        ],
    }


def main():
    docs = get_kb_docs("builtin-001")
    target = [d for d in docs if d.get("title") == "01-stm32-gpio.md"]
    if not target:
        print("01-stm32-gpio.md not found in builtin-001")
        return
    doc = target[0]
    result = review_md_chunks(doc["doc_id"], doc["title"])
    out = Path("_chunk_review_gpio.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
