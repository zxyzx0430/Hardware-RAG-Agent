"""Poll ch340g reindex status."""
import sqlite3
import time
from pathlib import Path

db_path = Path(r"E:\Desktop\agent\backend\data\hardware_rag.db")
doc_id = "02502d1d-f4f2-406c-926b-f2690e2a5dea"

db = sqlite3.connect(str(db_path))
cur = db.cursor()
for i in range(120):
    cur.execute("SELECT doc_id, title, status, chunk_count, error_message FROM knowledge_docs WHERE doc_id=?", (doc_id,))
    row = cur.fetchone()
    print(f"{i*2}s: {row}")
    if row and row[2] == "indexed":
        print("Indexing complete.")
        break
    time.sleep(2)
else:
    print("Timeout.")
db.close()
