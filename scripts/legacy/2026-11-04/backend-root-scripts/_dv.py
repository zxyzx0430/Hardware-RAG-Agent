import fitz, requests
PDF=r"E:\Desktop\agent\data\pdfs\interface\ch340g_datasheet.pdf"
KB="http://127.0.0.1:58080/api"
r=requests.get(KB+"/kb/collections/kb-96eca485",timeout=30)
for d in r.json()["data"]["documents"]:
    if "ch340" in d["title"].lower(): DID=d["doc_id"];break
r=requests.get(KB+"/kb/documents/"+DID+"/chunks",timeout=60)
chunks=r.json()["data"]["chunks"]
doc=fitz.open(PDF)
pdf={}
for i in range(doc.page_count):
    p=doc[i];pdf[i+1]={"txt":p.get_text("text"),"imgs":p.get_images()}
doc.close()
print(f"Total chunks: {len(chunks)}")
cov={}
for c in chunks:
    for p in range(c["page_start"],c.get("page_end",c["page_start"])+1):
        if p not in cov:cov[p]={"t":[],"i":[]}
        if "图片描述" in c["content"][:50] or "Image description" in c["content"][:50]:
            cov[p]["i"].append(c)
        else:
            cov[p]["t"].append(c)
print("=== PAGE COVERAGE ===")
for p in range(1,15):
    info=pdf[p];c=cov.get(p,{"t":[],"i":[]})
    nt=len(c["t"]);ni=len(c["i"]);ni_pdf=len(info["imgs"])
    txt_f="TXT" if nt>0 else "MISS"
    img_f="IMG" if ni>0 or ni_pdf==0 else f"IMG_MISS({ni_pdf}img)"
    print(f"p{p:2d}: txt={nt:2d} {txt_f:4s} img={ni:2d} {img_f:12s}")
miss=set(range(1,15))-set(cov.keys())
print(f"\nZero-coverage pages: {sorted(miss) if miss else 'NONE ✅'}")
from collections import defaultdict
by_p=defaultdict(list)
for c in chunks:
    if "图片描述" not in c["content"][:50] and "Image description" not in c["content"][:50]:
        by_p[c["page_start"]].append(c["content"])
print("\n=== TEXT ACCURACY ===")
for p in sorted(by_p.keys()):
    pt=pdf[p]["txt"];ct="\n".join(by_p[p])
    ol=sum(1 for l in pt.split("\n") if l.strip() and l.strip() in ct)
    tl=sum(1 for l in pt.split("\n") if l.strip())
    pct=ol/tl*100 if tl>0 else 0
    flag="OK" if pct>70 else "LOW" if pct>40 else "BAD"
    print(f"p{p:2d}: {ol:3d}/{tl:3d} lines ({pct:5.1f}%) {flag}")
