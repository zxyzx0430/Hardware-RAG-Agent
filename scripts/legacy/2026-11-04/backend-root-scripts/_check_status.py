"""Check all PDFs status."""
import urllib.request
import json
import traceback

KB_ID = "kb-e75cab61"
BACKEND = "http://127.0.0.1:58080"

try:
    r = urllib.request.urlopen(f"{BACKEND}/api/kb/collections/{KB_ID}", timeout=30)
    d = json.loads(r.read())
    docs = d.get('data', {}).get('documents', [])
    for x in docs:
        title = x.get('title', '?')
        status = x.get('status', '?')
        chunks = x.get('chunk_count', 0)
        err = (x.get('error_message') or '')[:200]
        print(f"  {title}: status={status}, chunks={chunks}")
        if err:
            print(f"    err: {err}")
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
