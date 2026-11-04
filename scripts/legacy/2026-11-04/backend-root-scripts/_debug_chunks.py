"""Debug script: dump chunks of frontend-prompt.md to inspect chunking issues."""
import sys
import os
import types as _t
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if "sentence_transformers" not in sys.modules:
    s = _t.ModuleType("sentence_transformers")
    s.SentenceTransformer = type("ST", (), {})
    sys.modules["sentence_transformers"] = s

warnings.filterwarnings("ignore")

from src.rag.kb_manager import get_kb_manager

DOC_ID = "30b53484-f851-4b0f-82ed-836e93544d52"  # frontend-prompt.md

km = get_kb_manager()
kb = km.get_kb("builtin-001")
store = km._get_store(kb)
chunks = store.get_chunks_by_doc(DOC_ID)

print(f"Total chunks: {len(chunks)}")
print()

# Stats
sizes = [c.get("metadata", {}).get("chunk_size", len(c.get("content", ""))) for c in chunks]
print(f"Size stats: min={min(sizes)} max={max(sizes)} avg={sum(sizes)//len(sizes)}")
print()

# Show chunks 20..60 first 200 chars only (to spot other issues)
for i in range(20, len(chunks)):
    c = chunks[i]
    meta = c.get("metadata", {})
    idx = meta.get("chunk_index", "?")
    size = meta.get("chunk_size", len(c.get("content", "")))
    section = meta.get("section_title", "")
    content_preview = c.get("content", "")[:200].replace("\n", " / ")
    print(f"[{i}] idx={idx} size={size} section={section!r}")
    print(f"    {content_preview}")
    print()

# Detect duplicates (exact content match)
print("=" * 60)
print("Duplicate detection (exact content):")
seen = {}
for i, c in enumerate(chunks):
    content = c.get("content", "")
    if content in seen:
        print(f"  chunk[{i}] EXACT DUP of chunk[{seen[content]}] (size={len(content)})")
    else:
        seen[content] = i
print(f"Unique chunks: {len(seen)} / {len(chunks)}")
