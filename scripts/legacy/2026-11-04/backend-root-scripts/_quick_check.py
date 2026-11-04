"""Quick check: list documents and errors in a KB."""
import sys, httpx
kb_id = sys.argv[1] if len(sys.argv) > 1 else "kb-9ea2e611"
r = httpx.get(f"http://127.0.0.1:58080/api/kb/collections/{kb_id}")
data = r.json().get("data", {})
docs = data.get("documents", [])
print(f"KB {kb_id}: {len(docs)} docs, method={data.get('chunk_method','?')}")
for d in docs:
    print(f"  {d.get('title','?')}: status={d.get('status','?')} chunks={d.get('chunk_count',0)} err={d.get('error_message','')[:200]}")
