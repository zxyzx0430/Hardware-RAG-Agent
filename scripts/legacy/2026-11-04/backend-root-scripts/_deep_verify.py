import fitz, requests, json

PDF = r"E:\Desktop\agent\data\pdfs\interface\ch340g_datasheet.pdf"
KB = "http://127.0.0.1:58080/api"
DOC_ID = None

# Step 1: Get doc_id
r = requests.get(KB+"/kb/collections/kb-96eca485", timeout=30)
for d in r.json()["data"]["documents"]:
    if "ch340" in d["title"].lower():
        DOC_ID = d["doc_id"]
        break
print(f"Doc ID: {DOC_ID}")

# Step 2: Get chunks
r = requests.get(KB+"/kb/documents/"+DOC_ID+"/chunks", timeout=60)
chunks = r.json()["data"]["chunks"]
print(f"Total chunks: {len(chunks)}")

# Step 3: Read PDF directly with PyMuPDF
doc = fitz.open(PDF)
pdf_pages = {}
for i in range(doc.page_count):
    page = doc[i]
    pdf_pages[i+1] = {
        "text": page.get_text("text"),
        "text_len": len(page.get_text("text")),
        "images": page.get_images(),
    }
doc.close()

print("\n=== PAGE-BY-PAGE COVERAGE ===")
page_coverage = {}
for c in chunks:
    ps = c.get("page_start", 0)
    pe = c.get("page_end", ps)
    content = c.get("content", "")
    for p in range(ps, pe+1):
        if p not in page_coverage:
            page_coverage[p] = {"text_chunks": [], "img_chunks": []}
        if content.startswith("[图片描述") or content.startswith("[Image description"):
            page_coverage[p]["img_chunks"].append(c)
        else:
            page_coverage[p]["text_chunks"].append(c)

for p in range(1, 15):
    info = pdf_pages.get(p, {"text_len": 0, "images": []})
    cov = page_coverage.get(p, {"text_chunks": [], "img_chunks": []})
    n_text = len(cov["text_chunks"])
    n_img = len(cov["img_chunks"])
    n_img_pdf = len(info["images"])
    txt_len = info["text_len"]
    
    text_covered = "✅" if n_text > 0 else "❌ NO TEXT"
    img_covered = "✅" if n_img > 0 or n_img_pdf == 0 else f"❌ MISSING({n_img_pdf}img)"
    no_img = "(no img)" if n_img_pdf == 0 else f"({n_img_pdf}img)"
    print(f"  p{p:2d}: {txt_len:5d}chars {no_img:12s} text_chunks={n_text:2d} {text_covered:12s} img_chunks={n_img:2d} {img_covered}")

# Step 4: Check for pages with ZERO coverage
missing_pages = set(range(1,15)) - set(page_coverage.keys())
if missing_pages:
    print(f"\n⚠️ PAGES WITH ZERO COVERAGE: {sorted(missing_pages)}")
else:
    print(f"\n✅ ALL 14 PAGES have at least 1 chunk")

# Step 5: Check TEXT ACCURACY - does chunk text match PDF text?
print("\n=== TEXT ACCURACY CHECK ===")
# Group chunks by page_start
from collections import defaultdict
by_page = defaultdict(list)
for c in chunks:
    content = c.get("content", "")
    if not content.startswith("[图片描述") and not content.startswith("[Image description"):
        by_page[c["page_start"]].append(content)

for pnum in sorted(by_page.keys()):
    pdf_text = pdf_pages.get(pnum, {}).get("text", "")
    chunk_text = "\n".join(by_page[pnum])
    # Check how much of PDF text is in chunks
    overlap = sum(1 for line in pdf_text.split("\n") if line.strip() and line.strip() in chunk_text)
    pdf_lines = sum(1 for line in pdf_text.split("\n") if line.strip())
    if pdf_lines > 0:
        pct = overlap / pdf_lines * 100
        flag = "✅" if pct > 70 else "⚠️" if pct > 40 else "❌"
        print(f"  p{pnum:2d}: {overlap:3d}/{pdf_lines:3d} lines covered ({pct:5.1f}%) {flag}")

# Step 6: Check image description QUALITY
print("\n=== IMAGE DESCRIPTION QUALITY CHECK ===")
img_chunks = [c for c in chunks if c.get("content","").startswith("[图片描述") or c.get("content","").startswith("[Image description")]
for c in img_chunks:
    content = c.get("content","")
    ps = c["page_start"]
    pe = c.get("page_end", ps)
    # Check for key terms
    has_pin = "引脚" in content or "pin" in content.lower()
    has_table = "表" in content or "table" in content.lower()
    has_package = "封装" in content or "package" in content.lower()
    has_circuit = "电路" in content or "circuit" in content.lower()
    terms = []
    if has_pin: terms.append("pin")
    if has_table: terms.append("table")
    if has_package: terms.append("package")
    if has_circuit: terms.append("circuit")
    desc_len = len(content)
    first_line = content.split("\n")[0][:100]
    print(f"  p{ps}-{pe}: [{','.join(terms)}] ({desc_len}chars) {first_line}...")

# Summary
print("\n=== SUMMARY ===")
print(f"PDF pages: 14")
print(f"All pages covered: {'✅ Yes' if not set(range(1,15)) - set(page_coverage.keys()) else '❌ No'}")
print(f"Pages with images: {[p for p in range(1,15) if pdf_pages[p]['images']]}")
print(f"Images coverage in image desc: checked above")
