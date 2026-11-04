"""Review page coverage for kb-44cf14d6 documents."""
from __future__ import annotations

import httpx

API_BASE = "http://127.0.0.1:58080"
KB_ID = "kb-44cf14d6"


def get_kb() -> dict:
    r = httpx.get(f"{API_BASE}/api/kb/collections/{KB_ID}", timeout=60)
    r.raise_for_status()
    return r.json()["data"]


def get_doc_chunks(doc_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    r.raise_for_status()
    return r.json().get("data", {}).get("chunks", [])


def main():
    kb = get_kb()
    print(f"KB: {KB_ID} ({len(kb.get('documents', []))} docs)\\n")
    for doc in kb["documents"]:
        doc_id = doc["doc_id"]
        title = doc["title"]
        chunks = get_doc_chunks(doc_id)
        covered: set[int] = set()
        for c in chunks:
            ps = c.get("page_start") or 0
            pe = c.get("page_end") or ps
            covered.update(range(ps, pe + 1))
        page_list = sorted(covered)
        if not page_list:
            print(f"{title}: no chunks")
            continue
        gaps = [p for p in range(min(page_list), max(page_list) + 1) if p not in covered]
        print(f"{title}: {len(chunks)} chunks, pages {min(page_list)}-{max(page_list)}, covered {len(covered)}, gaps={gaps[:10]}{'...' if len(gaps)>10 else ''}")


if __name__ == "__main__":
    main()
