"""Dump chunks covering hard question source pages for manual verification."""
import sys
sys.path.insert(0, "backend")

import yaml
from pathlib import Path
from src.rag.kb_manager import get_kb_manager

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "benchmark" / "chunk-baseline-golden-v1.yaml"

HARD_IDS = {
    "stm32f4-q005": {"doc_id": "baseline-stm32f4-gpio-exti-v2", "pages": [15]},
    "stm32f4-q006": {"doc_id": "baseline-stm32f4-gpio-exti-v2", "pages": [5]},
    "stm32f4-q009": {"doc_id": "baseline-stm32f4-gpio-exti-v2", "pages": [6, 7, 15, 16, 17, 18]},
    "esp32-q005": {"doc_id": "baseline-esp32-datasheet-v2", "pages": [22]},
    "esp32-q006": {"doc_id": "baseline-esp32-datasheet-v2", "pages": [23]},
    "esp32-q007": {"doc_id": "baseline-esp32-datasheet-v2", "pages": [18, 22, 24]},
    "esp32-q009": {"doc_id": "baseline-esp32-datasheet-v2", "pages": [2]},
}

OUTPUT = ROOT / "data" / "benchmark" / "hard_question_chunks_dump.txt"

with open(DATASET, encoding="utf-8") as f:
    data = yaml.safe_load(f)
samples = {s["id"]: s for s in data["samples"]}

kbm = get_kb_manager()

with open(OUTPUT, "w", encoding="utf-8") as out:
    for sid, info in HARD_IDS.items():
        doc_id = info["doc_id"]
        chunks = kbm.get_doc_chunks("builtin-001", doc_id)
        pages = set(info["pages"])
        out.write(f"\n{'='*70}\n[{sid}] {samples[sid]['query']}\n  doc_id={doc_id} pages={sorted(pages)} total_chunks={len(chunks)}\n\n")
        for c in chunks:
            meta = c.get("metadata", {})
            ps = meta.get("page_start")
            pe = meta.get("page_end")
            if ps is None:
                continue
            page_set = set(range(ps, (pe or ps) + 1))
            if page_set & pages:
                out.write(f"--- chunk {meta.get('chunk_index')} pages={ps}-{pe} title={meta.get('section_title','')[:60]} ---\n")
                content = c.get("content", "")
                out.write(content[:2000])
                out.write("\n\n")
print(f"Wrote {OUTPUT}")
