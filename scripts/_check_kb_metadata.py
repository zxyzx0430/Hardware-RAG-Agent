import chromadb
client = chromadb.PersistentClient(path="data/chroma")
coll = client.get_collection("hardware-docs-test")
res = coll.get(include=["metadatas"], limit=5)
for m in res["metadatas"]:
    print(m)
