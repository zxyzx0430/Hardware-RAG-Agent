import requests,r
KB="http://127.0.0.1:58080/api"
r=requests.get(f"{KB}/kb/collections/kb-96eca485",timeout=30)
docs=r.json()["data"]["documents"]
for d in docs:
    if "ch340" in d.get("title","").lower():
        did=d["doc_id"]
        print(f"Doc: {d['title']} ({did})")
r2=requests.get(f"{KB}/kb/documents/{did}/chunks",timeout=60)
chunks=r2.json()["data"]["chunks"]
img=[c for c in chunks if c.get("content","").startswith("[图片描述") or c.get("content","").startswith("[Image description")]
img_pages=set()
for c in img:
    for p in range(c.get("page_start",0),c.get("page_end",c.get("page_start",0))+1):
        img_pages.add(p)
print(f"Total: {len(chunks)}, Image desc: {len(img)}")
print(f"Image covers pages: {sorted(img_pages)}")
expected={7,8,12,13,14}
missed=expected-img_pages
if missed: print(f"MISSING: {sorted(missed)}")
else: print("ALL expected pages covered!")
for c in img:
    l=c.get("content","").split("\n")[0][:120]
    print(f"  p{c['page_start']}-{c['page_end']}: {l}")
