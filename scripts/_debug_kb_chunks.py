import httpx
import json

base = "http://127.0.0.1:58080/api"
r = httpx.get(f"{base}/kb/list", params={"kb_id": "builtin-001"}, timeout=30)
print("list status", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:3000])
print("---")
for doc_id in ["baseline-stm32f4-gpio-exti-v2", "baseline-esp32-datasheet-v2", "12f13dda-4d71-4a4a-9870-fe7e9d7e5062", "d4d191db-0765-4d12-805e-d0176cc7908b"]:
    rc = httpx.get(f"{base}/kb/documents/{doc_id}/chunks", timeout=30)
    print(f"{doc_id} status {rc.status_code}")
    d = rc.json()
    print("success", d.get("success"), "total", d.get("data", {}).get("total_chunks"), "kb_id", d.get("data", {}).get("kb_id"))
    print("first chunk keys", list((d.get("data", {}).get("chunks") or [{}])[0].keys())[:20])
