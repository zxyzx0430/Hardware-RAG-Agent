"""Inspect source excerpt lengths and content to debug chunk_boundary scoring."""
import json
import sys

path = "E:/Desktop/agent/data/test_results/rag_eval_20260627_034523_rescored.json"
d = json.load(open(path, "r", encoding="utf-8"))

s = d["strategies"][0]  # hybrid-500
print(f"Strategy: {s['name']}")
print()

for qr in s["question_results"][:3]:
    qid = qr["question_id"]
    print(f"=== Q{qid} ===")
    sources = qr.get("sources_full", [])
    print(f"  Sources count: {len(sources)}")
    for i, src in enumerate(sources):
        excerpt = src.get("excerpt", "")
        print(f"  src[{i}]: title={src.get('title','')[:30]} len={len(excerpt)}")
        # Show first 150 and last 150 chars
        if excerpt:
            print(f"    HEAD: {excerpt[:150]!r}")
            print(f"    TAIL: {excerpt[-150:]!r}")
    print()
