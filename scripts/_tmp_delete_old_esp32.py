import chromadb, sys
sys.path.insert(0, 'backend')
from src.config.settings import settings
client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
coll = client.get_collection('hardware-docs-test')
old_id = 'a01cb55a-3218-4c85-8115-f153a5916881'
res = coll.get(where={"doc_id": old_id}, include=[])
print('old esp32 chunks found', len(res['ids']))
if res['ids']:
    coll.delete(ids=res['ids'])
    print('deleted')
print('total count after', coll.count())
