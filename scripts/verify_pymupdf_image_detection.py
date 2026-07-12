"""Verify PyMuPDF image detection fix on ch340g_datasheet.pdf.

Checks:
1. _render_pages() correctly detects has_embedded_images for p7/p8/p12/p13/p14
2. _needs_image_description() returns True for sections covering those pages
3. _pick_description_page() picks a page with image (not a text-only page)

Run: python scripts/verify_pymupdf_image_detection.py
"""
import sys
from pathlib import Path

# Ensure backend is importable
BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from src.rag.chunking.multimodal_chunker import MultimodalChunker

PDF_PATH = BACKEND.parent / "data" / "pdfs" / "interface" / "ch340g_datasheet.pdf"

# Pages known to contain embedded images (from PyMuPDF get_images scan)
EXPECTED_IMAGE_PAGES = {7, 8, 12, 13, 14}


def main() -> None:
    chunker = MultimodalChunker(api_key="dummy", dpi=200)
    print(f"PDF: {PDF_PATH.name}")

    # Step 1: render pages (no LLM needed)
    page_data = chunker._render_pages(PDF_PATH)
    print(f"\n[Step 1] _render_pages: {len(page_data)} pages")
    print(f"{'page':>4} {'chars':>6} {'images':>6} {'has_img':>8}")
    detected_image_pages = set()
    for p in page_data:
        has_img = p.get("has_embedded_images", False)
        cnt = p.get("embedded_image_count", 0)
        if has_img:
            detected_image_pages.add(p["page_num"])
        print(f"{p['page_num']:>4} {len(p['text']):>6} {cnt:>6} {str(has_img):>8}")

    missing = EXPECTED_IMAGE_PAGES - detected_image_pages
    extra = detected_image_pages - EXPECTED_IMAGE_PAGES
    print(f"\nExpected image pages: {sorted(EXPECTED_IMAGE_PAGES)}")
    print(f"Detected image pages: {sorted(detected_image_pages)}")
    if missing:
        print(f"FAIL: missing {sorted(missing)}")
    if extra:
        print(f"WARN: extra {sorted(extra)}")
    if not missing:
        print("PASS: all expected image pages detected")

    # Step 2: _needs_image_description for sections covering each image page
    page_text_map = {p["page_num"]: p["text"] for p in page_data}
    print(f"\n[Step 2] _needs_image_description per page")
    print(f"{'page':>4} {'needs_desc':>10}")
    all_pass = True
    for pg in sorted(EXPECTED_IMAGE_PAGES):
        # Simulate a single-page section
        section = {"start_page": pg, "end_page": pg, "has_table": False}
        needs = chunker._needs_image_description(section, page_text_map, page_data)
        mark = "PASS" if needs else "FAIL"
        if not needs:
            all_pass = False
        print(f"{pg:>4} {str(needs):>10} {mark}")

    # Step 3: _pick_description_page picks a page WITH image
    print(f"\n[Step 3] _pick_description_page for multi-page sections")
    test_sections = [
        {"start_page": 6, "end_page": 8, "title": "p6-8 (cover p7 pinout)"},
        {"start_page": 11, "end_page": 14, "title": "p11-14 (cover p12-14 circuits)"},
    ]
    for s in test_sections:
        picked = chunker._pick_description_page(s, page_data)
        if picked is None:
            print(f"  {s['title']}: None (FAIL)")
            all_pass = False
            continue
        pg = picked["page_num"]
        has_img = picked.get("has_embedded_images", False)
        mark = "PASS" if has_img else "WARN (picked text-only page)"
        print(f"  {s['title']}: picked p{pg} (has_img={has_img}) {mark}")

    print(f"\n{'='*50}")
    print("ALL PASS" if all_pass and not missing else "SOME CHECKS FAILED")
    return 0 if all_pass and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
