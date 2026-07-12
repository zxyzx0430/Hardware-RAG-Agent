"""Debug: list all ChromaDB collections and chunk counts."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import chromadb

CHROMA_DIR = BACKEND / "data" / "chroma_db"
client = chromadb.PersistentClient(path=str(CHROMA_DIR))

print(f"ChromaDB path: {CHROMA_DIR}")
collections = client.list_collections()
print(f"\nTotal collections: {len(collections)}")

for col_meta in collections:
    name = col_meta.name if hasattr(col_meta, "name") else col_meta.get("name", "?")
    try:
        col = client.get_collection(name)
        count = col.count()
        # Sample metadata to see doc_ids
        sample = col.get(include=["metadatas"], limit=50)
        doc_ids = set()
        kbs = set()
        for m in sample.get("metadatas", []):
            if m:
                if "doc_id" in m:
                    doc_ids.add(m["doc_id"])
                if "kb_id" in m:
                    kbs.add(m["kb_id"])
        print(f"\n  Collection: {name}")
        print(f"    count: {count}")
        print(f"    sample doc_ids: {list(doc_ids)[:5]}")
        print(f"    sample kb_ids: {list(kbs)[:5]}")
    except Exception as e:
        print(f"  Collection: {name} - ERROR: {e}")
