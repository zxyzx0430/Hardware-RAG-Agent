import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))
from src.rag.kb_manager import get_kb_manager
kbm = get_kb_manager()
chunks = kbm.get_doc_chunks(sys.argv[1], sys.argv[2])
page = int(sys.argv[3])
matched = [c for c in chunks if c.get('metadata',{}).get('page_start') == page or c.get('metadata',{}).get('page_end') == page]
print(f'matched {len(matched)} chunks for page {page}')
for i,c in enumerate(matched,1):
    m = c.get('metadata',{})
    print(f'--- chunk {i} p{m.get(\"page_start\")}-{m.get(\"page_end\")} {m.get(\"content_type\",\"text\")} ---')
    print(c.get('content',''))
    print()
