"""Clean up ALL stale ch340g docs from ChromaDB + SQLite, keeping only the latest."""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import chromadb
import sqlite3

CHROMA_DIR = BACKEND / "data" / "chroma_db"
DB_PATH = BACKEND / "data" / "hardware_rag.db"
KB_ID = "kb-96eca485"


def main():
    # 1. Find all ch340g docs in SQLite
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT doc_id, title, chunk_count, chunk_method_used, status FROM knowledge_docs WHERE kb_id=? AND title LIKE '%ch340g%'",
        (KB_ID,),
    )
    rows = cur.fetchall()
    print(f"Found {len(rows)} ch340g docs in SQLite:")
    for r in rows:
        print(f"  doc_id={r[0][:8]}... title={r[1]} chunks={r[2]} method={r[3]} status={r[4]}")
    conn.close()

    if not rows:
        print("No ch340g docs found.")
        return

    # 2. Delete all from ChromaDB
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    for col in client.list_collections():
        try:
            data = col.get(include=["metadatas"], limit=10000)
        except Exception as e:
            print(f"  collection {col.name}: get() failed: {e}")
            continue
        metas = data.get("metadatas", []) or []
        ids = data.get("ids", []) or []
        to_delete = []
        for i, meta in enumerate(metas):
            if not meta:
                continue
            blob = str(meta).lower()
            if "ch340g" in blob:
                to_delete.append(ids[i])
        if to_delete:
            col.delete(ids=to_delete)
            print(f"  Deleted {len(to_delete)} chunks from collection {col.name}")

    # 3. Delete all from SQLite
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM knowledge_docs WHERE kb_id=? AND title LIKE '%ch340g%'",
        (KB_ID,),
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {deleted} ch340g doc records from SQLite")
    print("Cleanup complete. Ready for fresh reindex.")


if __name__ == "__main__":
    main()
