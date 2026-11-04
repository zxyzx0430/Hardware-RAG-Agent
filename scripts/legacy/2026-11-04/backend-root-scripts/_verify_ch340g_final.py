"""Verify ch340g image description chunks — correct field paths."""
import httpx

API = "http://127.0.0.1:58080/api"
DOC_ID = "de0661b5-3dc7-428e-b97e-fa2f36e0c025"

r = httpx.get(f"{API}/kb/documents/{DOC_ID}/chunks", timeout=60)
chunks = r.json()["data"]["chunks"]
print(f"Total chunks: {len(chunks)}")

img_chunks = [c for c in chunks if "[图片描述" in c.get("content", "")]
text_chunks = [c for c in chunks if "[图片描述" not in c.get("content", "")]

print(f"Text chunks: {len(text_chunks)}")
print(f"Image description chunks: {len(img_chunks)}")

print(f"\nSample chunk keys: {list(chunks[0].keys())}")

print("\n=== Image description chunks ===")
for c in img_chunks:
    ps = c.get("page_start")
    pe = c.get("page_end")
    st = c.get("section_title", "")
    ct = c.get("content_type", "")
    cm = c.get("chunk_method", "")
    l = c.get("content_length", len(c.get("content", "")))
    preview = c.get("content", "")[:100].replace("\n", " ")
    print(f"  pg={ps}-{pe} section=\"{st}\" content_type={ct} method={cm} chars={l}")
    print(f"    {preview}")
    print()

print("=== Text chunks (first 10) ===")
for c in text_chunks[:10]:
    ps = c.get("page_start")
    pe = c.get("page_end")
    st = c.get("section_title", "")[:45]
    l = c.get("content_length", len(c.get("content", "")))
    print(f"  pg={ps}-{pe} ch={l:5d} sec=\"{st}\"")
