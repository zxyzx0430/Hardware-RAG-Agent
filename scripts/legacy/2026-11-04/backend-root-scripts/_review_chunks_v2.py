"""Re-review multimodal chunks with correct field names."""
import httpx
import re
import json
from pathlib import Path

API = 'http://127.0.0.1:58080/api'
DOC_ID = '987bedcb-8d17-4cb1-9848-8b95a14167ea'
OUT = Path(r'E:/Desktop/agent/data/test_results')

r = httpx.get(f'{API}/kb/documents/{DOC_ID}/chunks', timeout=60)
data = r.json().get('data', {})
chunks = data.get('chunks', []) if isinstance(data, dict) else data
print(f'Total chunks: {len(chunks)}')

missing_page = 0
missing_section = 0
has_page = 0
has_section = 0
types = {}
min_chars = 99999
max_chars = 0

for i, c in enumerate(chunks):
    meta = c.get('metadata', c)  
    page_start = meta.get('page_start')
    page_end = meta.get('page_end')
    section_title = meta.get('section_title', '')
    chunk_method = meta.get('chunk_method', '')
    content_type = meta.get('content_type', '')
    content = c.get('content', c.get('text', ''))
    chars = len(content)

    types[content_type or chunk_method] = types.get(content_type or chunk_method, 0) + 1

    if page_start is not None and page_start != '':
        has_page += 1
    else:
        missing_page += 1

    if section_title:
        has_section += 1
    else:
        missing_section += 1

    if chars < min_chars:
        min_chars = chars
    if chars > max_chars:
        max_chars = chars

    if i < 15 or content_type == 'image_description':
        preview = content[:100].replace('\n', ' ')
        print(f'[{i:3d}] page_start={page_start} page_end={page_end} section="{section_title[:40]}" '
              f'type={content_type or chunk_method} chars={chars} preview={preview!r}')

print(f'\n=== Summary ===')
print(f'Chunks with page_start: {has_page}/{len(chunks)}')
print(f'Chunks missing page_start: {missing_page}/{len(chunks)}')
print(f'Chunks with section_title: {has_section}/{len(chunks)}')
print(f'Chunks missing section_title: {missing_section}/{len(chunks)}')
print(f'Chunk types: {types}')
print(f'Char range: {min_chars} - {max_chars}')
print(f'Image description chunks: {types.get("image_description", 0)}')
