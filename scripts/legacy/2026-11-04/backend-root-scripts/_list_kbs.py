"""List all KBs with doc counts and chunk counts."""
import httpx
c = httpx.Client(timeout=30.0)
r = c.get("http://127.0.0.1:58080/api/kb/collections")
d = r.json()
cols = d.get("data", {}).get("collections", [])
print(f"Total KBs: {len(cols)}")
for x in cols:
    kb_id = x["id"]
    # Get detail to count docs/chunks
    r2 = c.get(f"http://127.0.0.1:58080/api/kb/collections/{kb_id}")
    d2 = r2.json().get("data", {})
    docs = d2.get("documents", [])
    total_chunks = sum(doc.get("chunk_count", 0) for doc in docs)
    statuses = [doc.get("status", "?") for doc in docs]
    print(f"  {kb_id}  {x.get('name','?'):45}  method={x.get('chunk_method','?'):10}  "
          f"docs={len(docs)}  chunks={total_chunks}  statuses={statuses}")
