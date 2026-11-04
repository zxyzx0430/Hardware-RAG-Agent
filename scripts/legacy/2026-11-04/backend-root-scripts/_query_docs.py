import sqlite3
conn = sqlite3.connect(r"E:\Desktop\agent\backend\data\hardware_rag.db")
cur = conn.cursor()
for kb_id in ['kb-69c3cb6d', 'kb-44cf14d6']:
    print(f"\n=== {kb_id} ===")
    cur.execute(
        "SELECT doc_id, title, status, chunk_count FROM knowledge_docs WHERE kb_id=? ORDER BY title",
        (kb_id,),
    )
    for r in cur.fetchall():
        print(r)
conn.close()
