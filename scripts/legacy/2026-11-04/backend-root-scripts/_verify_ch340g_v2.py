"""Verify ch340g chunks with correct field names."""
import httpx

API = "http://127.0.0.1:58080/api"
DOC_ID = "de0661b5-3dc7-428e-b97e-fa2f36e0c025"

r = httpx.get(f"{API}/kb/documents/{DOC_ID}/chunks", timeout=60)
chunks = r.json()["data"]["chunks"]
print(f"Total chunks: {len(chunks)}")

img_chunks = [c for c in chunks if "[图片描述" in c.get("content", "")]
print(f"Image description chunks: {len(img_chunks)}")

types = {}
for c in chunks:
    content = c.get("content", "")
    if "[图片描述" in content:
        ct = "image_description"
    else:
        ct = "text"
    types[ct] = types.get(ct, 0) + 1
print(f"Chunk types: {types}")

print("\n=== Image description chunks detail ===")
for c in img_chunks:
    content = c.get("content", "")
    section = c.get("section_title", c.get("metadata", {}).get("section_title", "?"))
    ps = c.get("page_start", c.get("metadata", {}).get("page_start", "?"))
    pe = c.get("page_end", c.get("metadata", {}).get("page_end", "?"))
    ct = c.get("content_type", c.get("metadata", {}).get("content_type", ""))
    print(f"  pg={ps}-{pe} section=\"{section}\" content_type={ct} chars={len(content)}")
    print(f"    preview: {content[:120].replace(chr(10), ' ')}")
    print()

print("=== Text chunks with page info (first 10) ===")
text_chunks = [c for c in chunks if "[图片描述" not in c.get("content", "")]
for c in text_chunks[:10]:
    ps = c.get("page_start", "?")
    pe = c.get("page_end", "?")
    section = c.get("section_title", "")[:40]
    l = len(c.get("content", ""))
    print(f"  pg={ps}-{pe} ch={l:5d} sec=\"{section}\"")

print("\n=== Sample chunk raw keys ===")
if chunks:
    print("Keys:", list(chunks[0].keys()))
    print("Sample:", {k: str(v)[:50] for k, v in chunks[0].items()})
