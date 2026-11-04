import sqlite3
conn = sqlite3.connect(r"E:\Desktop\agent\backend\data\hardware_rag.db")
cur = conn.cursor()
cur.execute("SELECT doc_id, kb_id, title, status, error_message, created_at FROM knowledge_docs WHERE kb_id IN ('kb-69c3cb6d','kb-965a0ba3')")
for r in cur.fetchall():
    print(r)
conn.close()
