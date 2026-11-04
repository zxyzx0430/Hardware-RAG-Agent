"""Inspect JSON result structure to plan rescore script."""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else \
    "E:/Desktop/agent/data/test_results/rag_eval_20260627_034523.json"

d = json.load(open(path, "r", encoding="utf-8"))
print("Top-level keys:", list(d.keys()))
print()
for s in d.get("strategies", []):
    name = s.get("name") or s.get("strategy") or "?"
    print(f"Strategy: {name}")
    print(f"  keys: {list(s.keys())}")
    qs = s.get("question_results") or s.get("results") or s.get("questions") or []
    if qs:
        q = qs[0]
        print(f"  question keys: {list(q.keys())}")
        print(f"  has sources? {'sources' in q}")
        if "sources" in q and q["sources"]:
            print(f"  source[0] keys: {list(q['sources'][0].keys())}")
            print(f"  source[0] excerpt (first 100): {(q['sources'][0].get('excerpt') or '')[:100]!r}")
        print(f"  has chunks_by_doc? {'chunks_by_doc' in q}")
        if "chunks_by_doc" in q:
            print(f"  chunks_by_doc keys: {list(q['chunks_by_doc'].keys())[:3]}")
        print(f"  scores keys: {list(q.get('scores', {}).keys())}")
    print()
    break  # only first strategy
