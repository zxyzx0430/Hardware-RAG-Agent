import sqlite3
from pathlib import Path

db = sqlite3.connect(r"E:\Desktop\agent\backend\data\hardware_rag.db")
cur = db.cursor()
cur.execute("SELECT doc_id, title, status, chunk_count, error_message, created_at FROM knowledge_docs WHERE kb_id='kb-96eca485'")
for r in cur.fetchall():
    print(r)
db.close()
