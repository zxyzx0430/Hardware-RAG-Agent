"""Check chunks covering ESP32 q004 source pages."""
import sys
sys.path.insert(0, "backend")
from src.rag.kb_manager import get_kb_manager
import re

kbm = get_kb_manager()
chunks = kbm.get_doc_chunks("builtin-001", "baseline-esp32-datasheet-v2")
for c in chunks:
    meta = c.get("metadata", {})
    ps = meta.get("page_start")
    pe = meta.get("page_end") or ps
    if ps is None:
        continue
    if not (ps <= 4 <= pe or ps <= 37 <= pe):
        continue
    text = c.get("content", "")
    print(f"--- chunk {meta.get('chunk_index')} pages={ps}-{pe} title={meta.get('section_title','')[:60]} ---")
    print(text[:1800])
    print()
