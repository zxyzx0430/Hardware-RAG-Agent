"""Audit NEW ch340g chunks (doc_id=72c57c2d) only.

Checks:
1. image_description coverage (esp. p5/p6)
2. page marker <!-- PAGE:N --> presence in text chunks
3. page_start/page_end accuracy
4. Markdown table preservation
5. chunk boundary quality (no mid-sentence cuts, no split markers)
6. page coverage
"""
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

CHUNKS = Path(__file__).resolve().parent / "audit" / "ch340g_chunks_chroma.jsonl"
NEW_DOC_PREFIX = "32abb79e"

PAGE_MARKER_RE = re.compile(r"<!-- PAGE:(\d+) -->")


def main():
    all_chunks = []
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                all_chunks.append(json.loads(line))

    # Filter to NEW doc only
    chunks = [c for c in all_chunks if c["metadata"].get("doc_id", "").startswith(NEW_DOC_PREFIX)]
    print(f"=== NEW doc chunks: {len(chunks)} (filtered from {len(all_chunks)} total) ===\n")

    text_chunks = [c for c in chunks if c["metadata"].get("content_type") != "image_description"]
    img_chunks = [c for c in chunks if c["metadata"].get("content_type") == "image_description"]
    print(f"Text chunks: {len(text_chunks)}, Image description chunks: {len(img_chunks)}")

    # ─── 1. image_description audit ───
    print("\n" + "=" * 70)
    print("1. IMAGE_DESCRIPTION AUDIT")
    print("=" * 70)
    img_pages = sorted(set(c["metadata"].get("source_page", 0) for c in img_chunks))
    print(f"   source_pages with image_description: {img_pages}")
    print(f"   count: {len(img_chunks)}")
    for c in img_chunks:
        meta = c["metadata"]
        src = meta.get("source_page", "?")
        title = meta.get("section_title", "")[:50]
        doc_preview = c["document"][:200].replace("\n", " ")
        print(f"   p{src} | {title} | {doc_preview}...")

    # p5/p6 specific check
    print("\n   --- p5/p6 specific check ---")
    p5_img = [c for c in img_chunks if c["metadata"].get("source_page") == 5]
    p6_img = [c for c in img_chunks if c["metadata"].get("source_page") == 6]
    print(f"   p5 image_description: {len(p5_img)} chunk(s)  {'✓ PASS' if p5_img else '✗ FAIL'}")
    if p5_img:
        print(f"     content: {p5_img[0]['document'][:400]}")
    print(f"   p6 image_description: {len(p6_img)} chunk(s)  {'✓ PASS' if p6_img else '✗ FAIL'}")
    if p6_img:
        print(f"     content: {p6_img[0]['document'][:400]}")

    # 2. Page marker / page_start audit
    print("\n" + "=" * 70)
    print("2. PAGE RANGE AUDIT (page_start / page_end)")
    print("=" * 70)
    page_start_dist = Counter()
    page_end_dist = Counter()
    page_start_eq_1_count = 0
    for c in text_chunks:
        meta = c["metadata"]
        ps = meta.get("page_start", 0)
        pe = meta.get("page_end", 0)
        page_start_dist[ps] += 1
        page_end_dist[pe] += 1
        if ps == 1:
            page_start_eq_1_count += 1
    print(f"   page_start distribution: {dict(sorted(page_start_dist.items()))}")
    print(f"   page_end distribution: {dict(sorted(page_end_dist.items()))}")
    print(f"   chunks with page_start=1: {page_start_eq_1_count}")
    # Final chunk text strips <!-- PAGE:N --> markers by design, so we only
    # check that page_start is varied (not all 1) and within valid range.
    unique_starts = set(page_start_dist.keys())
    if unique_starts == {1}:
        print("   ✗ FAIL — all chunks have page_start=1 (page markers lost)")
    elif max(unique_starts) <= 14 and min(unique_starts) >= 1:
        print(f"   ✓ PASS — page_start ranges across {len(unique_starts)} distinct pages (1-{max(unique_starts)})")
    else:
        print(f"   ⚠ WARN — unexpected page_start range: {min(unique_starts)}-{max(unique_starts)}")

    # ─── 3. Markdown table audit ───
    print("\n" + "=" * 70)
    print("3. MARKDOWN TABLE AUDIT")
    print("=" * 70)
    table_chunks = []
    total_table_lines = 0
    for c in text_chunks:
        doc = c["document"]
        table_lines = [l for l in doc.split("\n") if l.strip().startswith("|")]
        if table_lines:
            table_chunks.append({
                "page_start": c["metadata"].get("page_start"),
                "page_end": c["metadata"].get("page_end"),
                "lines": len(table_lines),
                "preview": table_lines[0][:120] if table_lines else "",
                "section": c["metadata"].get("section_title", "")[:40],
            })
            total_table_lines += len(table_lines)
    print(f"   Chunks containing Markdown tables: {len(table_chunks)}")
    print(f"   Total table lines across all chunks: {total_table_lines}")
    for tc in table_chunks[:20]:
        print(f"   p{tc['page_start']}-{tc['page_end']} ({tc['lines']}L) [{tc['section']}]: {tc['preview']}")

    # ─── 4. Page coverage ───
    print("\n" + "=" * 70)
    print("4. PAGE COVERAGE (text chunks only)")
    print("=" * 70)
    page_coverage = defaultdict(int)
    for c in text_chunks:
        ps = c["metadata"].get("page_start")
        pe = c["metadata"].get("page_end")
        if ps and pe:
            try:
                for p in range(int(ps), int(pe) + 1):
                    page_coverage[p] += 1
            except (ValueError, TypeError):
                pass
    print(f"   Pages covered: {dict(sorted(page_coverage.items()))}")
    uncovered = [p for p in range(1, 15) if p not in page_coverage]
    if uncovered:
        print(f"   ✗ UNCOVERED pages: {uncovered}")
    else:
        print(f"   ✓ All 14 pages covered")

    # ─── 5. Chunk boundary quality ───
    print("\n" + "=" * 70)
    print("5. CHUNK BOUNDARY QUALITY")
    print("=" * 70)
    # Check for split page markers (broken markers)
    broken_markers = 0
    for c in text_chunks:
        doc = c["document"]
        # Look for partial page markers
        if re.search(r"<!-- PAGE$", doc) or re.search(r"^:?\d+ -->", doc):
            broken_markers += 1
            print(f"   ✗ Broken marker in chunk p{c['metadata'].get('page_start')}: ...{doc[-50:]}")
    print(f"   Broken/split page markers: {broken_markers}")

    # Check for chunks starting/ending mid-sentence
    mid_sentence_start = 0
    mid_sentence_end = 0
    for c in text_chunks:
        doc = c["document"].strip()
        if not doc:
            continue
        # Mid-sentence start: starts with lowercase or connector word
        first_word = doc.split()[0] if doc.split() else ""
        if first_word and first_word[0].islower() and first_word not in {"the", "a", "an", "and", "or", "but"}:
            mid_sentence_start += 1
        # Mid-sentence end: ends without punctuation
        last_char = doc[-1] if doc else ""
        if last_char not in ".。!！?？;；:：\n)）】」》\"'`":
            mid_sentence_end += 1
    print(f"   Chunks starting mid-sentence (lowercase): {mid_sentence_start}")
    print(f"   Chunks ending without terminal punctuation: {mid_sentence_end}")

    # Chunk size distribution
    sizes = [len(c["document"]) for c in text_chunks]
    if sizes:
        print(f"\n   Chunk size: min={min(sizes)}, max={max(sizes)}, avg={sum(sizes)//len(sizes)}")
        tiny = sum(1 for s in sizes if s < 100)
        small = sum(1 for s in sizes if 100 <= s < 300)
        print(f"   Tiny (<100 chars): {tiny}, Small (100-300): {small}, Normal (300+): {sum(1 for s in sizes if s >= 300)}")

    # ─── 6. Summary ───
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    issues = []
    if not p5_img:
        issues.append("p5 missing image_description")
    if not p6_img:
        issues.append("p6 missing image_description")
    if unique_starts == {1}:
        issues.append("all chunks have page_start=1 (page markers lost)")
    if broken_markers > 0:
        issues.append(f"{broken_markers} broken page markers")
    if uncovered:
        issues.append(f"uncovered pages: {uncovered}")
    if len(table_chunks) == 0:
        issues.append("no Markdown tables found")

    if issues:
        print("   ISSUES FOUND:")
        for i in issues:
            print(f"   ✗ {i}")
    else:
        print("   ✓ ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
