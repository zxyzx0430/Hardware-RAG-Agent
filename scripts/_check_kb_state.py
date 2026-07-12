import chromadb

client = chromadb.PersistentClient(path="data/chroma")
try:
    coll = client.get_collection("hardware-docs-test")
    count = coll.count()
    print(f"Collection count: {count}")
    res = coll.get(include=["metadatas"])
    doc_ids = {}
    chunk_methods = {}
    for meta in res["metadatas"]:
        did = meta.get("doc_id") or meta.get("document_id") or "unknown"
        doc_ids[did] = doc_ids.get(did, 0) + 1
        cm = meta.get("chunk_method", "unknown")
        chunk_methods[(did, cm)] = chunk_methods.get((did, cm), 0) + 1
    print("Doc counts:")
    for did, c in sorted(doc_ids.items()):
        print(f"  {did}: {c}")
    print("Chunk methods:")
    for (did, cm), c in sorted(chunk_methods.items()):
        print(f"  {did}/{cm}: {c}")
except Exception as e:
    print(f"Error: {e}")
