import sys
sys.path.insert(0, "backend")

from src.rag.kb_manager import get_kb_manager

kbm = get_kb_manager()
kb = kbm.get_kb("builtin-001")
print("kb", kb)
store = kbm._get_store(kb)
print("store", store, "collection", store.collection_name if hasattr(store, "collection_name") else None)

# Try to get any chunk
result = store.db.get(limit=3, include=["documents", "metadatas"])
print("sample ids", result.get("ids")[:3] if result.get("ids") else [])
for i, meta in enumerate(result.get("metadatas", [])):
    print(f"meta {i} keys={list(meta.keys())[:20]}")
    print(f"  doc_id={meta.get('doc_id')} title={meta.get('title')} page_start={meta.get('page_start')}")

# Try get by doc_id
for doc_id in ["baseline-esp32-datasheet-v2", "baseline-stm32f4-gpio-exti-v2"]:
    r = store.db.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
    print(f"\n{doc_id} count={len(r.get('ids', []))}")
