"""Inspect mid-sentence-start chunks in CASE 1 (ch340g)."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import chromadb

CHROMA_DIR = BACKEND / "data" / "chroma_db"
client = chromadb.PersistentClient(path=str(CHROMA_DIR))
col = client.get_collection("kb_kb_96eca485")
data = col.get(include=["documents", "metadatas"], limit=10000)

target_doc = "6e92c858-453f-4a9b-8bf7-1f46e9349641"
ENGLISH_CONNECTORS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by"}

mid_start_chunks = []
all_text_chunks = []
for i, _id in enumerate(data.get("ids", [])):
    meta = data["metadatas"][i]
    if meta.get("doc_id") != target_doc:
        continue
    if meta.get("content_type") == "image_description":
        continue
    doc = (data["documents"][i] or "").strip()
    all_text_chunks.append((meta, doc))
    if not doc:
        continue
    first_word = doc.split()[0] if doc.split() else ""
    if first_word and first_word[0].islower() and first_word.lower() not in ENGLISH_CONNECTORS:
        mid_start_chunks.append((meta, doc))

print(f"Total text chunks: {len(all_text_chunks)}")
print(f"Mid-sentence start chunks: {len(mid_start_chunks)}\n")

print("="*80)
for i, (meta, doc) in enumerate(mid_start_chunks):
    print(f"\n--- chunk {i+1} ---")
    print(f"  chunk_index={meta.get('chunk_index')}, section={meta.get('section_title','')[:60]}, pages={meta.get('page_start')}-{meta.get('page_end')}")
    print(f"  First 200 chars:\n{doc[:200]}")
    print(f"  ...")
    print(f"  Last 100 chars:\n{doc[-100:]}")
