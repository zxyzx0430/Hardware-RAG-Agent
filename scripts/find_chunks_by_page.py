"""按页码查找chunks。"""
import json

chunks = json.load(open("data/benchmark/chunk_baseline_chunks.json", encoding="utf-8"))

for c in chunks:
    if c["doc_id"] == "baseline-stm32f4-gpio-exti" and 52 <= c["chunk_index"] <= 58:
        print(f"[{c['chunk_index']}] p{c['page_range']}")
        print(c["text"][:800].replace('\n', ' '))
        print("-"*80)
