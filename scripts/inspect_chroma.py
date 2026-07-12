import os, sys
sys.path.insert(0, 'backend')
import chromadb
from src.config.settings import settings

client = chromadb.PersistentClient(path=str(settings.chroma_persist_dir))
print('Collections:', client.list_collections())

try:
    coll = client.get_collection('hardware-docs-test')
    print('Collection count:', coll.count())
    # peek a few
    peek = coll.peek(limit=3)
    for i, doc in enumerate(peek['documents']):
        meta = peek['metadatas'][i]
        print(f'--- doc {i} ---')
        print('meta:', meta)
        print('doc preview:', doc[:300].replace('\n', ' '))
except Exception as e:
    print('Error:', e)
