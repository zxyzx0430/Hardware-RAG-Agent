"""Find KB chunks by doc_id and page number."""
import sys
import chromadb

CHROMA_PERSIST_DIR = "data/chroma"
COLLECTION_NAME = "hardware-docs-test"

client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
coll = client.get_collection(COLLECTION_NAME)

def find(doc_id: str, page: int):
    res = coll.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
    matches = []
    for doc, meta in zip(res["documents"], res["metadatas"]):
        start = meta.get("start_page", 0)
        end = meta.get("end_page", 0)
        if start <= page <= end:
            matches.append({
                "chunk_id": meta.get("small_chunk_id") or meta.get("id"),
                "chunk_index": meta.get("chunk_index"),
                "page_range": meta.get("page_range"),
                "section_title": meta.get("section_title", ""),
                "text": doc,
            })
    return matches

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python _find_kb_chunks_by_page.py <doc_id> <page>")
        sys.exit(1)
    doc_id = sys.argv[1]
    page = int(sys.argv[2])
    matches = find(doc_id, page)
    print(f"Found {len(matches)} chunks for {doc_id} page {page}")
    for m in matches:
        print(f"\n[{m['chunk_index']}] {m['chunk_id']} | {m['page_range']} | {m['section_title']}")
        print(m['text'][:1000].replace("\n", " "))
        print("-" * 80)
