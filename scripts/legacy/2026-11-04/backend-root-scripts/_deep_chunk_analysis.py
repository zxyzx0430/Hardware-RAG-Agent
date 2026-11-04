"""Deep analysis: identify page imprecision + table fragmentation."""
import httpx
import re
import fitz
from pathlib import Path

API = 'http://127.0.0.1:58080/api'
DOC_ID = '987bedcb-8d17-4cb1-9848-8b95a14167ea'
PDF = Path(r"E:/Desktop/agent/data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf")

r = httpx.get(f'{API}/kb/documents/{DOC_ID}/chunks', timeout=60)
chunks = r.json()['data']['chunks']

doc = fitz.open(PDF)
page_texts = {}
for i, page in enumerate(doc):
    page_texts[i + 1] = page.get_text("text")
doc.close()

print("=" * 70)
print("ISSUE 1: Page imprecision — chunks where page_start != actual page")
print("=" * 70)
wrong_page = 0
for i, c in enumerate(chunks):
    meta = c.get('metadata', {})
    page_start = meta.get('page_start', 0)
    content = c.get('content', '')
    first_50 = content[:80].replace('\n', ' ').strip()
    
    actual_pages = []
    for pnum in range(1, 45):
        snippet = page_texts[pnum][:60].replace('\n', ' ')
        if snippet and snippet[:30] in first_50[:50]:
            actual_pages.append(pnum)
    
    if actual_pages and page_start not in actual_pages:
        wrong_page += 1
        if wrong_page <= 10:
            print(f'  [{i}] page_start={page_start} but content seems from page {actual_pages}: "{first_50[:60]}"')

print(f'\n  Total with wrong page_start: {wrong_page}/{len(chunks)}')

print("\n" + "=" * 70)
print("ISSUE 2: Table/structure fragmentation — very small chunks near tables")
print("=" * 70)
small_chunks = [(i, c) for i, c in enumerate(chunks) if len(c.get('content', '')) < 400]
print(f'  Chunks < 400 chars: {len(small_chunks)}')
for i, c in small_chunks[:8]:
    meta = c.get('metadata', {})
    print(f'  [{i}] page={meta.get("page_start")} section="{meta.get("section_title","")[:40]}" chars={len(c["content"])} preview={c["content"][:60]!r}')

print("\n" + "=" * 70)
print("ISSUE 3: Sub-split table breakage — register tables split mid-row")
print("=" * 70)
table_breaks = 0
for i in range(len(chunks) - 1):
    c1 = chunks[i].get('content', '')
    c2 = chunks[i + 1].get('content', '')
    if (c1.rstrip().endswith(('0', '1')) and
        c2.lstrip()[:10].lstrip().startswith(('0', '1', '|', ' ')) and
        chunks[i].get('metadata', {}).get('section_title') == chunks[i+1].get('metadata', {}).get('section_title')):
        table_breaks += 1
        if table_breaks <= 5:
            print(f'  [{i}]->[{i+1}] possible table split:')
            print(f'    end: "...{c1[-40:].replace(chr(10), " ")}"')
            print(f'    next: "{c2[:40].replace(chr(10), " ")}..."')

print(f'\n  Possible table splits: {table_breaks}')

print("\n" + "=" * 70)
print("ISSUE 4: Image description coverage")
print("=" * 70)
image_chunks = [c for c in chunks if c.get('metadata', {}).get('content_type') == 'image_description']
print(f'  Image description chunks: {len(image_chunks)}')
fig_pages = [p for p in range(1, 45) if 'Figure' in page_texts.get(p, '')]
print(f'  Pages mentioning "Figure": {fig_pages}')
low_text_pages = [p for p in range(1, 45) if len(page_texts.get(p, '')) < 500]
print(f'  Low-text pages (<500 chars): {low_text_pages}')
for p in low_text_pages:
    print(f'    Page {p}: {len(page_texts[p])} chars, preview: {page_texts[p][:100].replace(chr(10), " ")!r}')
