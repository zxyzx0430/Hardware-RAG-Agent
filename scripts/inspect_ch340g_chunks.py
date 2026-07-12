"""Inspect ch340g chunks in ChromaDB via PersistentClient.

Run: python scripts/inspect_ch340g_chunks.py
"""
import sys
from pathlib import Path
from collections import Counter

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

CHROMA_DIR = BACKEND / "data" / "chroma_db"


def main() -> None:
    try:
        import chromadb
    except Exception as e:
        print(f"chromadb import failed: {e}")
        return

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collections = client.list_collections()
    print(f"Collections: {len(collections)}")

    total_img_desc = 0
    for col in collections:
        name = col.name
        count = col.count()
        if count == 0:
            continue
        # Get a sample to find ch340g docs
        sample = col.get(limit=2000, include=["metadatas", "documents"])
        ch340g_indices = []
        for i, meta in enumerate(sample.get("metadatas", [])):
            if not meta:
                continue
            # Check various fields for ch340g
            blob = json.dumps(meta, ensure_ascii=False).lower()
            if "ch340g" in blob:
                ch340g_indices.append(i)
        if not ch340g_indices:
            continue
        print(f"\n=== Collection: {name} ({count} total, {len(ch340g_indices)} ch340g) ===")
        content_types = Counter()
        source_pages = []
        for i in ch340g_indices:
            meta = sample["metadatas"][i]
            ct = meta.get("content_type", "text")
            content_types[ct] += 1
            sp = meta.get("source_page")
            if sp:
                source_pages.append(sp)
            doc = sample["documents"][i] if sample.get("documents") else ""
            title = meta.get("section_title", "")
            print(f"  [{ct:20s}] p{meta.get('page_start','?')}-{meta.get('page_end','?')} src={sp} | {title[:40]} | {doc[:60]}")
        print(f"  Content types: {dict(content_types)}")
        if source_pages:
            print(f"  Image desc source pages: {sorted(set(source_pages))}")
        total_img_desc += content_types.get("image_description", 0)

    print(f"\nTotal image_description chunks across all collections: {total_img_desc}")
    EXPECTED = {7, 8, 12, 13, 14}
    print(f"Expected image pages for ch340g: {sorted(EXPECTED)}")


import json
if __name__ == "__main__":
    main()
