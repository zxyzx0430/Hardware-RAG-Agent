import sys
sys.path.insert(0, 'backend')
from src.config.settings import settings
import sqlite3
conn = sqlite3.connect(settings.sqlite_db_path)
c = conn.cursor()
c.execute('SELECT doc_id,kb_id,title,chunk_count,chunk_method_used FROM knowledge_docs')
for r in c.fetchall():
    print(r)
conn.close()
