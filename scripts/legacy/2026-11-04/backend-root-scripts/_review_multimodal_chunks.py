"""Review multimodal chunker output for stm32f4_gpio_exti_extract.pdf."""
import httpx
import json
import re
from pathlib import Path

API = 'http://127.0.0.1:58080/api'
DOC_ID = '987bedcb-8d17-4cb1-9848-8b95a14167ea'
OUTPUT = Path(r'E:/Desktop/agent/data/test_results')

r = httpx.get(f'{API}/kb/documents/{DOC_ID}/chunks', timeout=60)
data = r.json().get('data', {})
chunks = data if isinstance(data, list) else data.get('chunks', [])
print(f'Total chunks: {len(chunks)}')

results = []
all_pages_seen = set()
types = {}

for i, c in enumerate(chunks):
    meta = c.get('metadata', {})
    text = c.get('content', c.get('text', ''))
    ct = meta.get('content_type', meta.get('chunk_method', 'text'))
    pr = meta.get('page_range', '')
    src_page = meta.get('source_page', '')
    chunk_idx = meta.get('chunk_index', '')
    section = meta.get('section_title', '')

    pages_in_text = [int(m.group(1)) for m in re.finditer(r'<!-- PAGE:(\d+)', text)]
    
    effective_pages = set()
    if isinstance(pr, (list, tuple)) and len(pr) == 2:
        for p in range(int(pr[0]), int(pr[1]) + 1):
            effective_pages.add(p)
    elif isinstance(pr, (list, tuple)) and len(pr) == 1:
        effective_pages.add(int(pr[0]))
    if src_page:
        effective_pages.add(int(src_page))
    for p in pages_in_text:
        effective_pages.add(p)
    
    all_pages_seen.update(effective_pages)
    types[ct] = types.get(ct, 0) + 1
    
    preview = text[:150].replace('\n', ' ')
    entry = {
        'index': i,
        'chunk_index': chunk_idx,
        'type': ct,
        'pages': sorted(effective_pages),
        'section': section,
        'chars': len(text),
        'preview': preview,
    }
    results.append(entry)
    print(f'[{i:3d}] ci={str(chunk_idx):5s} type={ct:25s} pages={sorted(effective_pages)} '
          f'section={section[:40]} chars={len(text):5d} preview={preview[:60]!r}')

print(f'\n=== Summary ===')
print(f'Total chunks: {len(chunks)}')
print(f'Chunk types: {types}')
print(f'Pages seen: {sorted(all_pages_seen)}')
missing = [p for p in range(1, 45) if p not in all_pages_seen]
print(f'Missing pages (of 44): {missing}')

with open(OUTPUT / 'multimodal_chunks_review.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f'\nSaved to {OUTPUT / "multimodal_chunks_review.json"}')
