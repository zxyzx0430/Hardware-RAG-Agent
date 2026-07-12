"""
Test HybridChunker output quality on the 3 uploaded .md files.

Reports: chunk count, average/max/min size, exact duplicates (hash),
near duplicates (>80% Jaccard similarity on 5-gram shingles), and a
metadata spot-check (section_title path, page_range, small_chunk_id,
big_chunk_text length). Prints summary to terminal and writes a full
JSON report to docs/chunk-test-report.json.

Usage:
    python scripts/test_chunker.py
"""

import asyncio
import json
import sys
from itertools import combinations
from pathlib import Path

# ─── Bootstrap: add backend/ to sys.path ───
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import os
os.chdir(_BACKEND_DIR)

# ─── Stub out sentence_transformers to avoid OpenBLAS MemoryError on Py3.13 ───
# langchain_text_splitters/__init__.py imports sentence_transformers which
# triggers torch/OpenBLAS loading and crashes with MemoryError. We only need
# RecursiveCharacterTextSplitter, so stub the optional dependency.
import types as _types
if "sentence_transformers" not in sys.modules:
    _st_stub = _types.ModuleType("sentence_transformers")
    _st_stub.SentenceTransformer = type("SentenceTransformer", (), {})
    sys.modules["sentence_transformers"] = _st_stub

_UPLOADS_DIR = _BACKEND_DIR / "data" / "uploads"
_REPORT_PATH = _SCRIPT_DIR.parent / "docs" / "chunk-test-report.json"

# Old baseline (before refactor) for comparison
_OLD_BASELINE = {
    "1d617365-eea5-43e8-a2b6-340c248f1d5f.md": 60,
    "2d972478-cedc-446c-86eb-cbe295f574bd.md": 29,
    "40d6af22-8c42-44b2-847f-2d12ea19bbd2.md": 77,
}
_OLD_TOTAL = sum(_OLD_BASELINE.values())  # 166


def shingle(text: str, k: int = 5) -> set[str]:
    """Character-level k-gram shingles for near-duplicate detection."""
    text = text.replace("\n", " ").strip()
    if len(text) < k:
        return {text}
    return {text[i : i + k] for i in range(len(text) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_exact_duplicates(chunks: list) -> list[tuple[int, int]]:
    """Find chunks with identical fingerprints."""
    seen: dict[str, int] = {}
    dups = []
    for i, c in enumerate(chunks):
        if c.fingerprint in seen:
            dups.append((seen[c.fingerprint], i))
        else:
            seen[c.fingerprint] = i
    return dups


def find_near_duplicates(chunks: list, threshold: float = 0.8) -> list[tuple[int, int, float]]:
    """Find chunks with >threshold Jaccard similarity on 5-gram shingles."""
    shingles = [shingle(c.text) for c in chunks]
    dups = []
    for i, j in combinations(range(len(chunks)), 2):
        sim = jaccard(shingles[i], shingles[j])
        if sim > threshold:
            dups.append((i, j, round(sim, 3)))
    return dups


def analyze_chunks(chunks: list, filename: str) -> dict:
    """Compute statistics for one file's chunks."""
    sizes = [c.metadata.get("chunk_size", len(c.text)) for c in chunks]
    exact_dups = find_exact_duplicates(chunks)
    near_dups = find_near_duplicates(chunks)

    report = {
        "filename": filename,
        "chunk_count": len(chunks),
        "old_chunk_count": _OLD_BASELINE.get(filename, "?"),
        "avg_size": round(sum(sizes) / len(sizes), 1) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
        "min_size": min(sizes) if sizes else 0,
        "exact_duplicates": len(exact_dups),
        "near_duplicates_gt_80pct": len(near_dups),
        "near_duplicate_pairs": near_dups[:10],  # cap for readability
    }

    # Spot-check first 5 chunks' metadata
    spot_check = []
    for c in chunks[:5]:
        spot_check.append({
            "chunk_index": c.metadata.get("chunk_index"),
            "section_title": c.metadata.get("section_title", ""),
            "page_range": list(c.page_range),
            "small_chunk_id": c.metadata.get("small_chunk_id", ""),
            "big_chunk_text_len": len(c.metadata.get("big_chunk_text", "")),
            "chunk_size": c.metadata.get("chunk_size"),
            "text_preview": c.text[:80].replace("\n", " ") + "...",
        })
    report["spot_check"] = spot_check

    return report


async def main():
    from src.rag.chunking.hybrid_chunker import HybridChunker

    md_files = sorted(_UPLOADS_DIR.glob("*.md"))
    if not md_files:
        print(f"[ERROR] No .md files in {_UPLOADS_DIR}")
        return

    # Filter to the 3 known test files if present; otherwise use all
    test_files = [f for f in md_files if f.name in _OLD_BASELINE]
    if not test_files:
        test_files = md_files

    print(f"{'='*70}")
    print(f"HybridChunker Test — {len(test_files)} files")
    print(f"{'='*70}")

    chunker = HybridChunker()
    all_reports = []

    for fpath in test_files:
        text = fpath.read_text(encoding="utf-8", errors="replace")
        metadata = {"doc_id": fpath.stem, "title": fpath.name, "file_type": "md"}
        chunks = await chunker.chunk(
            text=text,
            metadata=metadata,
            file_path=fpath,
            total_pages=0,
        )

        report = analyze_chunks(chunks, fpath.name)
        all_reports.append(report)

        print(f"\n📄 {fpath.name} ({len(text)} chars)")
        print(f"   chunks: {report['chunk_count']} (old: {report['old_chunk_count']})")
        print(f"   size: avg={report['avg_size']}B  min={report['min_size']}B  max={report['max_size']}B")
        print(f"   exact dups: {report['exact_duplicates']}")
        print(f"   near dups (>80%): {report['near_duplicates_gt_80pct']}")
        if report["near_duplicate_pairs"]:
            for i, j, sim in report["near_duplicate_pairs"][:3]:
                print(f"     [{i}]~[{j}] sim={sim}")

    # Summary
    total_new = sum(r["chunk_count"] for r in all_reports)
    total_exact = sum(r["exact_duplicates"] for r in all_reports)
    total_near = sum(r["near_duplicates_gt_80pct"] for r in all_reports)

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"Total chunks: {total_new} (old: {_OLD_TOTAL})")
    reduction = round((1 - total_new / _OLD_TOTAL) * 100, 1) if _OLD_TOTAL else 0
    print(f"Reduction:    {reduction}%")
    print(f"Exact dups:   {total_exact}")
    print(f"Near dups:    {total_near}")

    if total_exact == 0 and total_near == 0:
        print("\n✅ PASS — No duplicates detected")
    else:
        print("\n❌ FAIL — Duplicates detected, review needed")

    # Spot-check display
    print(f"\n{'='*70}")
    print("METADATA SPOT-CHECK (first chunk of each file)")
    print(f"{'='*70}")
    for report in all_reports:
        if report["spot_check"]:
            sc = report["spot_check"][0]
            print(f"\n📄 {report['filename']}")
            print(f"   section_title: {sc['section_title']}")
            print(f"   page_range:    {sc['page_range']}")
            print(f"   small_chunk_id: {sc['small_chunk_id']}")
            print(f"   big_chunk_text_len: {sc['big_chunk_text_len']}")
            print(f"   chunk_size:    {sc['chunk_size']}")
            print(f"   text_preview:  {sc['text_preview']}")

    # Write JSON report
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    full_report = {
        "summary": {
            "total_chunks_new": total_new,
            "total_chunks_old": _OLD_TOTAL,
            "reduction_pct": reduction,
            "exact_duplicates": total_exact,
            "near_duplicates": total_near,
            "status": "PASS" if total_exact == 0 and total_near == 0 else "FAIL",
        },
        "files": all_reports,
    }
    _REPORT_PATH.write_text(json.dumps(full_report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n📝 JSON report: {_REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
