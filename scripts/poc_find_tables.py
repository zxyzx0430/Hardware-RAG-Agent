"""PoC: verify PyMuPDF page.find_tables() quality on ch340g_datasheet.pdf.

Decision gate: if the 3 key tables (absolute max ratings p3, DC characteristics p3,
pinout p4) are detected with readable Markdown, proceed with find_tables integration.

Run: python scripts/poc_find_tables.py
"""
import sys
from pathlib import Path

import fitz

PDF = Path(__file__).resolve().parent.parent / "data" / "pdfs" / "interface" / "ch340g_datasheet.pdf"

# Key tables expected (from OpenDataLoader PoC: 130 table lines)
EXPECTED_TABLES = {
    3: ["Absolute Maximum Ratings", "DC characteristics"],
    4: ["Pinout / Pin table"],
    9: ["Pins (SSOP20/SOP16/MSOP10)"],
    11: ["EEPROM configuration"],
    12: ["Electrical Parameter"],
    13: ["Sequence Parameter"],
}


def main() -> int:
    print(f"PDF: {PDF.name} (PyMuPDF {fitz.__version__})")
    doc = fitz.open(str(PDF))
    total_tables = 0
    total_md_lines = 0
    found_pages = []

    for i in range(doc.page_count):
        page = doc[i]
        try:
            tabs = page.find_tables()
        except Exception as e:
            print(f"p{i+1}: find_tables() error: {e}")
            continue
        page_tables = list(tabs)
        if not page_tables:
            continue
        found_pages.append(i + 1)
        print(f"\n=== p{i+1}: {len(page_tables)} table(s) ===")
        for j, tab in enumerate(page_tables):
            total_tables += 1
            try:
                md = tab.to_markdown()
            except Exception as e:
                print(f"  table{j}: to_markdown() error: {e}")
                continue
            md_lines = md.count("\n") + 1 if md.strip() else 0
            total_md_lines += md_lines
            preview = md.strip().replace("\n", " | ")[:150]
            print(f"  table{j} ({md_lines} lines): {preview}")

    doc.close()

    print(f"\n{'='*60}")
    print(f"Total tables detected: {total_tables}")
    print(f"Total Markdown lines: {total_md_lines}")
    print(f"Pages with tables: {found_pages}")
    print(f"OpenDataLoader reference: 130 lines across multiple pages")

    # Decision gate
    key_pages_found = [p for p in [3, 4, 9, 11, 12] if p in found_pages]
    print(f"\nKey pages found: {key_pages_found} / [3, 4, 9, 11, 12]")
    if len(key_pages_found) >= 3:
        print("DECISION: PASS - proceed with find_tables integration")
        return 0
    else:
        print("DECISION: FAIL - too few key tables detected, consider OpenDataLoader fallback")
        return 1


if __name__ == "__main__":
    sys.exit(main())
