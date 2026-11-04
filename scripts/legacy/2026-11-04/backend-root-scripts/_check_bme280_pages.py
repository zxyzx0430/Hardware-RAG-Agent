import json
import httpx

API_BASE = "http://127.0.0.1:58080"
doc_id = "f19fffbb-c9da-4711-bc0d-9d7661408619"

r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
r.raise_for_status()
chunks = r.json()["data"]["chunks"]

# Find chunks around page 30-32
for c in chunks:
    ps = c.get("page_start") or 0
    pe = c.get("page_end") or ps
    if ps <= 32 and pe >= 30:
        print(f"idx={c['chunk_index']} pages={ps}-{pe} title={c.get('section_title','')[:60]}")
        print(f"  content_len={len(c.get('content',''))} summary={c.get('section_summary','')[:80]}")
        print()
