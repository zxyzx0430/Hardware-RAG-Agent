"""Poll indexing status of the re-uploaded GPIO doc."""
import sqlite3
import time
from pathlib import Path

db_path = Path(r"E:\Desktop\agent\backend\data\hardware_rag.db")
doc_id = "cecbacc8-8d3f-4ba6-a5af-61e56d3270c4"

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
for i in range(60):
    cur.execute(
        "SELECT doc_id, title, status, chunk_count FROM knowledge_docs WHERE doc_id=?",
        (doc_id,),
    )
    row = cur.fetchone()
    print(f"{i*2}s: {row}")
    if row and row[2] == "indexed":
        print("Indexing complete.")
        break
    time.sleep(2)
else:
    print("Timeout waiting for indexing.")
conn.close()
