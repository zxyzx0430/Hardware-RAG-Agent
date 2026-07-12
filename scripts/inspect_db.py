import sqlite3, os

db_path = 'data/app.db'
print('exists:', os.path.exists(db_path))
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print('tables:', [r[0] for r in c.fetchall()])
    try:
        c.execute('SELECT id, name, collection_name FROM knowledge_bases')
        print('kbs:', c.fetchall())
    except Exception as e:
        print('kb err:', e)
    try:
        c.execute('SELECT id, filename, kb_id, doc_id, chunk_method_used FROM documents LIMIT 10')
        print('docs:', c.fetchall())
    except Exception as e:
        print('doc err:', e)
