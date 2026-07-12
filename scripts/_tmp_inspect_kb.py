import sqlite3, os, sys
sys.path.insert(0, 'backend')
from src.config.settings import settings
print('sqlite path', settings.sqlite_db_path)
db = settings.sqlite_db_path
print('exists', os.path.exists(db))
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('tables', [r[0] for r in c.fetchall()])
c.execute('SELECT id,name,collection_name,chunk_method,is_builtin FROM knowledge_bases')
print('kbs')
for r in c.fetchall():
    print(' ', r)
c.execute('SELECT doc_id,kb_id,title,chunk_count,chunk_method_used FROM knowledge_docs')
print('docs')
for r in c.fetchall():
    print(' ', r)
conn.close()
