"""Export chunks for a given document from ChromaDB to JSONL.

Run: python scripts/export_chroma_chunks.py <doc_id_or_filename_fragment>
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import chromadb

CHROMA_DIR = Path(__file__).resolve().parent.parent / "backend" / "data" / "chroma_db"
OUT_DIR = Path(__file__).resolve().parent / "audit"
OUT_DIR.mkdir(exist_ok=True)


def main() -> int:
    fragment = sys.argv[1] if len(sys.argv) > 1 else "ch340g"
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collections = client.list_collections()

    all_chunks = []
    for col in collections:
        try:
            data = col.get(include=["metadatas", "documents", "embeddings"], limit=10000)
        except Exception as e:
            print(f"  collection {col.name}: get() failed: {e}")
            continue
        metas = data.get("metadatas", []) or []
        docs = data.get("documents", []) or []
        ids = data.get("ids", []) or []
        for i, meta in enumerate(metas):
            if not meta:
                continue
            blob = json.dumps(meta, ensure_ascii=False).lower()
            if fragment.lower() not in blob:
                continue
            all_chunks.append({
                "id": ids[i] if i < len(ids) else None,
                "collection": col.name,
                "document": docs[i] if i < len(docs) else "",
                "metadata": meta,
            })

    if not all_chunks:
        print(f"No chunks found for fragment: {fragment}")
        return 1

    out_path = OUT_DIR / f"{fragment}_chunks_chroma.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"Exported {len(all_chunks)} chunks to {out_path}")

    # Summary by content_type and page
    from collections import Counter
    ct = Counter(c["metadata"].get("content_type", "text") for c in all_chunks)
    print(f"Content types: {dict(ct)}")
    pages = Counter()
    for c in all_chunks:
        meta = c["metadata"]
        ps = meta.get("page_start")
        pe = meta.get("page_end")
        if ps is not None and pe is not None:
            for p in range(int(ps), int(pe) + 1):
                pages[p] += 1
    print(f"Chunks per page: {dict(sorted(pages.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
