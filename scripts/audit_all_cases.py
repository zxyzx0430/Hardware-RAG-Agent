"""Audit all 3 cases after reindex.

Cases:
- CASE 1: ch340g (multimodal) doc_id=6e92c858-453f-4a9b-8bf7-1f46e9349641, kb=kb-96eca485
- CASE 2: STM32 GPIO (hybrid) doc_id=dc513ec9-f1d1-4a34-b33c-be7f8de9f612, kb=builtin-001
- CASE 3: 06-chaotic-embedded-notes (agent) doc_id=cadacc84-59b7-468f-a033-a2b6798a7533, kb=kb-567e2118
"""
import json
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

CASES = [
    {
        "name": "CASE 1: ch340g (multimodal)",
        "doc_id": "6e92c858-453f-4a9b-8bf7-1f46e9349641",
        "kb_id": "kb-96eca485",
        "collection_name": "kb_kb_96eca485",
        "total_pages": 14,
    },
    {
        "name": "CASE 2: STM32 GPIO (hybrid)",
        "doc_id": "dc513ec9-f1d1-4a34-b33c-be7f8de9f612",
        "kb_id": "builtin-001",
        "collection_name": "hardware-docs-test",
        "total_pages": 1,
    },
    {
        "name": "CASE 3: 06-chaotic-embedded-notes (agent)",
        "doc_id": "cadacc84-59b7-468f-a033-a2b6798a7533",
        "kb_id": "kb-567e2118",
        "collection_name": "kb_kb_567e2118",
        "total_pages": 1,
    },
]

PAGE_MARKER_RE = re.compile(r"<!-- PAGE:(\d+) -->")


def load_chunks_from_chroma(doc_id: str, collection_name: str) -> list[dict]:
    """Load chunks from ChromaDB."""
    import chromadb
    CHROMA_DIR = BACKEND / "data" / "chroma_db"
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        col = client.get_collection(collection_name)
    except Exception:
        return []
    data = col.get(include=["documents", "metadatas"], limit=10000)
    chunks = []
    ids = data.get("ids", []) or []
    docs = data.get("documents", []) or []
    metas = data.get("metadatas", []) or []
    for i, _id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        if meta.get("doc_id") == doc_id:
            chunks.append({
                "id": _id,
                "document": docs[i] if i < len(docs) else "",
                "metadata": meta,
            })
    return chunks


def audit_case(case: dict) -> dict:
    """Audit one case, return metrics dict."""
    name = case["name"]
    doc_id = case["doc_id"]
    collection_name = case["collection_name"]
    total_pages = case["total_pages"]

    print(f"\n{'='*70}")
    print(f"Auditing {name}")
    print(f"  doc_id={doc_id[:8]}... collection={collection_name}")
    print(f"{'='*70}")

    chunks = load_chunks_from_chroma(doc_id, collection_name)
    print(f"  Total chunks: {len(chunks)}")

    if not chunks:
        return {"name": name, "error": "no chunks found"}

    text_chunks = [c for c in chunks if c["metadata"].get("content_type") != "image_description"]
    img_chunks = [c for c in chunks if c["metadata"].get("content_type") == "image_description"]
    print(f"  Text chunks: {len(text_chunks)}, Image description: {len(img_chunks)}")

    # 1. Duplication rate
    fp_section_pairs = []
    for c in text_chunks:
        fp = c["metadata"].get("fingerprint", "")
        section = c["metadata"].get("section_title", "") or ""
        fp_section_pairs.append((fp, section))
    fp_only = [p[0] for p in fp_section_pairs]
    unique_fp = set(fp_only)
    unique_pairs = set(fp_section_pairs)
    dup_rate_fp = 1 - len(unique_fp) / len(fp_only) if fp_only else 0
    dup_rate_pair = 1 - len(unique_pairs) / len(fp_section_pairs) if fp_section_pairs else 0
    print(f"\n  [Duplication]")
    print(f"    Unique fingerprints: {len(unique_fp)}/{len(fp_only)} (dup rate by fp: {dup_rate_fp*100:.1f}%)")
    print(f"    Unique (fp, section) pairs: {len(unique_pairs)}/{len(fp_section_pairs)} (dup rate by pair: {dup_rate_pair*100:.1f}%)")

    # 2. Page coverage
    page_starts = Counter()
    for c in text_chunks:
        ps = c["metadata"].get("page_start", 0)
        page_starts[ps] += 1
    print(f"\n  [Page coverage]")
    print(f"    page_start distribution: {dict(sorted(page_starts.items()))}")
    if total_pages > 1:
        covered = set(page_starts.keys())
        missing = [p for p in range(1, total_pages + 1) if p not in covered]
        print(f"    Missing pages: {missing if missing else 'none'}")

    # 3. Image description coverage (for multimodal)
    if img_chunks:
        img_pages = sorted(set(c["metadata"].get("source_page", 0) for c in img_chunks))
        print(f"\n  [Image description]")
        print(f"    source_pages: {img_pages}")
        p5 = [c for c in img_chunks if c["metadata"].get("source_page") == 5]
        p6 = [c for c in img_chunks if c["metadata"].get("source_page") == 6]
        print(f"    p5: {'✓' if p5 else '✗'} ({len(p5)} chunks)")
        print(f"    p6: {'✓' if p6 else '✗'} ({len(p6)} chunks)")

    # 4. Markdown tables
    table_chunks = []
    total_table_lines = 0
    for c in text_chunks:
        doc = c["document"]
        table_lines = [l for l in doc.split("\n") if l.strip().startswith("|")]
        if table_lines:
            table_chunks.append({
                "page_start": c["metadata"].get("page_start"),
                "lines": len(table_lines),
                "section": c["metadata"].get("section_title", "")[:40],
            })
            total_table_lines += len(table_lines)
    print(f"\n  [Markdown tables]")
    print(f"    Chunks with tables: {len(table_chunks)}, total table lines: {total_table_lines}")

    # 5. Mid-sentence cuts
    mid_start = 0
    mid_end = 0
    for c in text_chunks:
        doc = c["document"].strip()
        if not doc:
            continue
        first_word = doc.split()[0] if doc.split() else ""
        if first_word and first_word[0].islower() and first_word not in {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by"}:
            mid_start += 1
        last_char = doc[-1] if doc else ""
        if last_char not in ".。!！?？;；:：\n)）】」》\"'`|":
            mid_end += 1
    mid_start_rate = mid_start / len(text_chunks) if text_chunks else 0
    mid_end_rate = mid_end / len(text_chunks) if text_chunks else 0
    print(f"\n  [Boundary quality]")
    print(f"    Mid-sentence starts: {mid_start}/{len(text_chunks)} ({mid_start_rate*100:.1f}%)")
    print(f"    Non-terminal ends: {mid_end}/{len(text_chunks)} ({mid_end_rate*100:.1f}%)")

    # 6. Chunk size distribution
    sizes = [len(c["document"]) for c in text_chunks]
    if sizes:
        print(f"\n  [Chunk size]")
        print(f"    min={min(sizes)}, max={max(sizes)}, avg={sum(sizes)//len(sizes)}")
        tiny = sum(1 for s in sizes if s < 100)
        small = sum(1 for s in sizes if 100 <= s < 300)
        normal = sum(1 for s in sizes if s >= 300)
        print(f"    tiny(<100): {tiny}, small(100-300): {small}, normal(>=300): {normal}")

    # 7. Empty/short chunks
    empty = [c for c in text_chunks if len(c["document"].strip()) < 50]
    if empty:
        print(f"\n  [Empty/short chunks (<50 chars)]")
        for c in empty:
            print(f"    chunk_index={c['metadata'].get('chunk_index')}: '{c['document'][:80]}'")

    # Summary
    print(f"\n  [SUMMARY]")
    issues = []
    if dup_rate_fp > 0.05:
        issues.append(f"dup rate by fp {dup_rate_fp*100:.1f}% > 5%")
    if total_pages > 1:
        covered = set(page_starts.keys())
        missing = [p for p in range(1, total_pages + 1) if p not in covered]
        if missing:
            issues.append(f"missing pages: {missing}")
    if img_chunks and total_pages > 1:
        p5 = [c for c in img_chunks if c["metadata"].get("source_page") == 5]
        p6 = [c for c in img_chunks if c["metadata"].get("source_page") == 6]
        if not p5:
            issues.append("p5 missing image_description")
        if not p6:
            issues.append("p6 missing image_description")
    if mid_start_rate > 0.10:
        issues.append(f"mid-sentence start rate {mid_start_rate*100:.1f}% > 10%")
    if not table_chunks and total_pages > 1:
        issues.append("no markdown tables found")

    if issues:
        print(f"    ISSUES:")
        for i in issues:
            print(f"      ✗ {i}")
    else:
        print(f"    ✓ ALL CHECKS PASSED")

    return {
        "name": name,
        "total": len(chunks),
        "text": len(text_chunks),
        "img": len(img_chunks),
        "dup_rate_fp": dup_rate_fp,
        "dup_rate_pair": dup_rate_pair,
        "mid_start_rate": mid_start_rate,
        "mid_end_rate": mid_end_rate,
        "table_chunks": len(table_chunks),
        "issues": issues,
    }


def main():
    print("# Chunk Integrity Audit Report (Post-Fix)")
    print(f"# Generated: 2026-06-29\n")

    results = []
    for case in CASES:
        results.append(audit_case(case))

    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*70}")
    for r in results:
        if "error" in r:
            print(f"  {r['name']}: ERROR - {r['error']}")
            continue
        status = "✓ PASS" if not r["issues"] else f"✗ {len(r['issues'])} issues"
        print(f"  {r['name']}: {status}")
        print(f"    chunks={r['total']} (text={r['text']}, img={r['img']}), dup_fp={r['dup_rate_fp']*100:.1f}%, mid_start={r['mid_start_rate']*100:.1f}%, tables={r['table_chunks']}")


if __name__ == "__main__":
    main()
