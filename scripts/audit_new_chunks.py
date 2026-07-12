"""Audit reindexed ch340g chunks: image_description, tables, page markers."""
import json
from pathlib import Path
from collections import Counter, defaultdict

CHUNKS = Path(__file__).resolve().parent / "audit" / "ch340g_chunks_chroma.jsonl"


def main():
    chunks = []
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    print(f"Total chunks: {len(chunks)}")

    # 1. image_description audit
    print("\n=== image_description chunks ===")
    img_chunks = [c for c in chunks if c["metadata"].get("content_type") == "image_description"]
    for c in img_chunks:
        meta = c["metadata"]
        src = meta.get("source_page", "?")
        ps = meta.get("page_start", "?")
        pe = meta.get("page_end", "?")
        title = meta.get("section_title", "")[:60]
        doc = c["document"][:150].replace("\n", " ")
        print(f"  src_p{src} pages={ps}-{pe} | {title} | {doc}")

    # 2. Page marker audit
    print("\n=== Page marker audit ===")
    page_starts = Counter()
    marker_re = __import__("re").compile(r"<!-- PAGE:(\d+) -->")
    no_marker = 0
    for c in chunks:
        meta = c["metadata"]
        if meta.get("content_type") == "image_description":
            continue
        ps = meta.get("page_start", 0)
        page_starts[ps] += 1
        doc = c["document"]
        markers = marker_re.findall(doc)
        if not markers:
            no_marker += 1
    print(f"  page_start distribution: {dict(sorted(page_starts.items()))}")
    print(f"  text chunks without <!-- PAGE:N --> marker: {no_marker}")

    # 3. Table audit (Markdown tables)
    print("\n=== Table audit ===")
    table_chunks = []
    for c in chunks:
        if c["metadata"].get("content_type") == "image_description":
            continue
        doc = c["document"]
        table_lines = [l for l in doc.split("\n") if l.strip().startswith("|")]
        if table_lines:
            table_chunks.append({
                "page_start": c["metadata"].get("page_start"),
                "page_end": c["metadata"].get("page_end"),
                "lines": len(table_lines),
                "preview": table_lines[0][:100] if table_lines else "",
            })
    print(f"  Chunks containing Markdown tables: {len(table_chunks)}")
    for tc in table_chunks[:15]:
        print(f"    p{tc['page_start']}-{tc['page_end']} ({tc['lines']} lines): {tc['preview']}")

    # 4. Page coverage
    print("\n=== Page coverage (text chunks) ===")
    page_coverage = defaultdict(int)
    for c in chunks:
        if c["metadata"].get("content_type") == "image_description":
            continue
        ps = c["metadata"].get("page_start")
        pe = c["metadata"].get("page_end")
        if ps and pe:
            try:
                for p in range(int(ps), int(pe) + 1):
                    page_coverage[p] += 1
            except (ValueError, TypeError):
                pass
    print(f"  Pages covered: {dict(sorted(page_coverage.items()))}")
    uncovered = [p for p in range(1, 15) if p not in page_coverage]
    if uncovered:
        print(f"  UNCOVERED pages: {uncovered}")
    else:
        print(f"  All 14 pages covered")

    # 5. Check p5/p6 specifically
    print("\n=== p5/p6 image_description check ===")
    p5_img = [c for c in img_chunks if c["metadata"].get("source_page") == 5]
    p6_img = [c for c in img_chunks if c["metadata"].get("source_page") == 6]
    print(f"  p5 image_description: {len(p5_img)} chunk(s)")
    if p5_img:
        print(f"    content: {p5_img[0]['document'][:300]}")
    print(f"  p6 image_description: {len(p6_img)} chunk(s)")
    if p6_img:
        print(f"    content: {p6_img[0]['document'][:300]}")


if __name__ == "__main__":
    main()
