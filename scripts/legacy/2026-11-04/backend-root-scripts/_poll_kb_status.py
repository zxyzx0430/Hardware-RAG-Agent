"""Poll indexing status for test KB documents."""
import sqlite3
import time
from pathlib import Path

db_path = Path(r"E:\Desktop\agent\backend\data\hardware_rag.db")
kb_id = "kb-96eca485"

doc_ids = [
    "8f642c64-855f-4041-9af1-7b4818169b3f",
    "f0c9b03b-1703-4e01-9637-211a4cd39035",
    "9c86944b-c009-4cbd-914a-e8fc7bba703b",
    "4998bf62-e2a0-403c-add5-31ee3b7fedd6",
    "cb280325-2601-48a3-8387-e80d79e88d4f",
]

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

try:
    for i in range(180):  # 180 * 10s = 30 min max
        print(f"\n--- poll {i+1} ---")
        cur.execute(
            "SELECT doc_id, title, status, chunk_count, error_message FROM knowledge_docs WHERE kb_id=? AND doc_id IN ({})".format(
                ",".join("?" * len(doc_ids))
            ),
            (kb_id, *doc_ids),
        )
        rows = {r[0]: r for r in cur.fetchall()}
        for did in doc_ids:
            r = rows.get(did)
            if r:
                print(f"  {r[1][:35]:35} | {r[2]:10} | chunks={r[3]:4} | err={r[4]}")
            else:
                print(f"  {did[:8]}... MISSING")
        if all(r and r[2] == "indexed" for r in rows.values() if r):
            print("\nAll indexed!")
            break
        time.sleep(10)
    else:
        print("\nTimeout.")
finally:
    conn.close()
