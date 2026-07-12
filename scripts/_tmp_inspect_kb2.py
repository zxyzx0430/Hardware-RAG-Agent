import sqlite3, os, sys
sys.path.insert(0, 'backend')
from src.config.settings import settings
conn = sqlite3.connect(settings.sqlite_db_path)
c = conn.cursor()
c.execute('SELECT id,name,collection_name,chunk_method,small_chunk_size,embedding_model,agent_chunker_model,agent_chunker_base_url,is_builtin FROM knowledge_bases')
for r in c.fetchall():
    print(r)
c.execute('SELECT doc_id,kb_id,title,chunk_count,chunk_method_used FROM knowledge_docs')
for r in c.fetchall():
    print(r)
conn.close()
