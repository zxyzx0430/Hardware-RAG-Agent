"""Quick check KB document status — investigate empty error_message."""
import httpx
import json
import sys

API = "http://127.0.0.1:58080/api"

for kb_id in sys.argv[1:]:
    r = httpx.get(f"{API}/kb/collections/{kb_id}", timeout=30.0)
    d = r.json()
    if not d.get("success"):
        print(f"KB {kb_id}: query failed: {d}")
        continue
    data = d.get("data", {})
    print(f"\n=== KB {kb_id} ({data.get('name', '?')}) ===")
    print(f"  chunk_method: {data.get('chunk_method')}")
    print(f"  embedding_model: {data.get('embedding_model')}")
    print(f"  small_chunk_size: {data.get('small_chunk_size')}")
    docs = data.get("documents", [])
    print(f"  Documents: {len(docs)}")
    for x in docs:
        print(f"    {x.get('filename','?'):40} status={x.get('status','?'):10} "
              f"chunks={x.get('chunk_count',0):4} "
              f"err='{x.get('error_message') or ''}'")
        # Full doc dict keys for inspection
        if x.get("status") == "error":
            print(f"      ALL KEYS: {list(x.keys())}")
            print(f"      FULL DOC: {json.dumps(x, ensure_ascii=False)[:800]}")
