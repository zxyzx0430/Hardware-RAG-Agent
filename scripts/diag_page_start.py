"""Diagnose why 80/114 text chunks have page_start=1.

Check: do chunks with page_start=1 actually contain content from page 1,
or are they misplaced content from other pages?
"""
import json
from pathlib import Path

CHUNKS = Path(__file__).resolve().parent / "audit" / "ch340g_chunks_chroma.jsonl"
NEW_DOC_PREFIX = "72c57c2d"

# Page content signatures (unique phrases from each page of CH340G datasheet)
PAGE_SIGNATURES = {
    1: ["CH340", "USB", "Datasheet", "DreamCity"],
    2: ["Features", "Operating Voltage", "USB 2.0"],
    3: ["Specifications", "Symbol", "Minimum", "Maximum"],
    4: ["Pinout", "Pin #", "Direction"],
    5: ["RS232", "adapter", "5.1"],
    6: ["Optically", "isolated", "UART"],
    7: ["USB to serial", "Version"],
    8: ["Package", "SOP", "DIP"],
    9: ["Function Description", "TNOW"],
    10: ["Data Transfer", "Communication"],
    11: ["Data Transfer", "Communication Pins"],
    12: ["Parameter", "Electrical"],
    13: ["Application", "RS232", "Converter"],
    14: ["RS232", "Converter", "Simplified"],
}


def guess_page(text: str) -> int:
    """Guess which page this text belongs to based on content signatures."""
    text_lower = text.lower()
    best_page = 1
    best_score = 0
    for page, keywords in PAGE_SIGNATURES.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > best_score:
            best_score = score
            best_page = page
    return best_page


def main():
    chunks = []
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                c = json.loads(line)
                if c["metadata"].get("doc_id", "").startswith(NEW_DOC_PREFIX):
                    if c["metadata"].get("content_type") != "image_description":
                        chunks.append(c)

    print(f"Text chunks: {len(chunks)}")

    # Check chunks with page_start=1
    page1_chunks = [c for c in chunks if c["metadata"].get("page_start") == 1]
    print(f"Chunks with page_start=1: {len(page1_chunks)}")

    mismatches = 0
    for c in page1_chunks[:30]:  # Check first 30
        doc = c["document"][:500]
        guessed = guess_page(doc)
        meta_ps = c["metadata"].get("page_start")
        meta_pe = c["metadata"].get("page_end")
        section = c["metadata"].get("section_title", "")[:50]
        if guessed != 1:
            mismatches += 1
            print(f"\n  ✗ MISMATCH: page_start={meta_ps} but content looks like page {guessed}")
            print(f"    section: {section}")
            print(f"    preview: {doc[:200].replace(chr(10), ' ')}")
        else:
            print(f"  ✓ page_start=1, content matches page 1 | {section}")

    print(f"\n--- {mismatches}/{min(30, len(page1_chunks))} chunks have wrong page_start ---")

    # Also check section_title distribution for page_start=1 chunks
    from collections import Counter
    sections = Counter(c["metadata"].get("section_title", "?")[:40] for c in page1_chunks)
    print(f"\nSection titles for page_start=1 chunks:")
    for s, cnt in sections.most_common():
        print(f"  ({cnt}x) {s}")


if __name__ == "__main__":
    main()
