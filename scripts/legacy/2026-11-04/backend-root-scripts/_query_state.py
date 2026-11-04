"""Query current KB and document state for evaluation."""
import sqlite3
from pathlib import Path

db_path = Path(r"E:\Desktop\agent\backend\data\hardware_rag.db")
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

print("=== Knowledge Bases ===")
cur.execute("SELECT id, name, chunk_method, embedding_model, embedding_base_url FROM knowledge_bases")
for r in cur.fetchall():
    print(r)

print("\n=== Docs matching gpio or stm32 ===")
cur.execute(
    "SELECT kb_id, doc_id, title, status, chunk_count, chunk_method_used "
    "FROM knowledge_docs WHERE title LIKE '%gpio%' OR title LIKE '%stm32%'"
)
rows = cur.fetchall()
for r in rows:
    print(r)
if not rows:
    print("(none)")

print("\n=== All recent docs (last 20) ===")
cur.execute(
    "SELECT kb_id, doc_id, title, status, chunk_count, chunk_method_used "
    "FROM knowledge_docs ORDER BY rowid DESC LIMIT 20"
)
for r in cur.fetchall():
    print(r)

conn.close()
