import sqlite3
from pathlib import Path

db = Path(r"e:\Desktop\agent\backend\data\hardware_rag.db")
print(f"DB exists: {db.exists()}, size: {db.stat().st_size} bytes")
c = sqlite3.connect(db)
cur = c.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print("\nTABLES:")
for r in cur.fetchall():
    print(f"  {r[0]}")

for tbl in ("knowledge_bases", "knowledge_docs"):
    try:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"\n{tbl} rows: {cur.fetchone()[0]}")
    except Exception as e:
        print(f"\n{tbl} error: {e}")

print("\nKB contents:")
try:
    cur.execute("SELECT id,name,enabled,is_builtin,doc_count,chunk_count,created_at FROM knowledge_bases")
    cols = [d[0] for d in cur.description]
    print("  cols:", cols)
    for r in cur.fetchall():
        print("  ", r)
except Exception as e:
    print("  error:", e)

print("\nDocs per KB:")
try:
    cur.execute("SELECT kb_id, COUNT(*), SUM(chunk_count) FROM knowledge_docs GROUP BY kb_id")
    for r in cur.fetchall():
        print("  ", r)
except Exception as e:
    print("  error:", e)
c.close()
