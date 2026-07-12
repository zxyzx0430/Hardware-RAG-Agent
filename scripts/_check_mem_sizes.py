"""Check if ESP32 memory sizes appear in chunks."""
import sys
sys.path.insert(0, "backend")
from src.rag.kb_manager import get_kb_manager
import re

kbm = get_kb_manager()
chunks = kbm.get_doc_chunks("builtin-001", "baseline-esp32-datasheet-v2")
patterns = [r"448\s*KB", r"520\s*KB", r"16\s*KB", r"448", r"520", r"16\s*KB.*RTC"]
for c in chunks:
    text = c.get("content", "")
    meta = c.get("metadata", {})
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            print(f"chunk {meta.get('chunk_index')} pages={meta.get('page_start')}-{meta.get('page_end')}: match {pat}")
            print(text[:300].replace("\n", " "))
            print()
            break
