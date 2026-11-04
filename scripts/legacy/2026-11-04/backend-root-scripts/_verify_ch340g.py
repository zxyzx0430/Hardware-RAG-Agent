"""Verify ch340g image description chunks after fix."""
import httpx

API = "http://127.0.0.1:58080/api"
DOC_ID = "de0661b5-3dc7-428e-b97e-fa2f36e0c025"

r = httpx.get(f"{API}/kb/documents/{DOC_ID}/chunks", timeout=60)
chunks = r.json()["data"]["chunks"]
print(f"Total chunks: {len(chunks)}")

types = {}
for c in chunks:
    m = c.get("metadata", {})
    ct = m.get("content_type", m.get("chunk_method", ""))
    types[ct] = types.get(ct, 0) + 1

print(f"Chunk types: {types}")
print()

for i, c in enumerate(chunks):
    m = c.get("metadata", {})
    ct = m.get("content_type", m.get("chunk_method", ""))
    ps = m.get("page_start", "?")
    pe = m.get("page_end", "?")
    st = m.get("section_title", "")[:35]
    l = len(c.get("content", ""))
    preview = c.get("content", "")[:80].replace("\n", " ")
    marker = " *** IMG ***" if ct == "image_description" else ""
    print(f"[{i:3d}] pg={ps}-{pe} type={ct:20s} ch={l:5d} sec=\"{st}\"{marker} pre={preview!r}")
