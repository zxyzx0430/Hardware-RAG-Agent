"""Review PDF chunk coverage for a specific doc.

Compares PyMuPDF-extracted text and images against indexed chunks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz  # PyMuPDF
import httpx

API_BASE = "http://127.0.0.1:58080"
PDF_DIR = Path(r"E:\Desktop\agent\data\pdfs")


def get_doc_chunks(doc_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    r.raise_for_status()
    return r.json().get("data", {}).get("chunks", [])


def clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def extract_pdf_data(pdf_path: Path) -> dict:
    doc = fitz.open(str(pdf_path))
    pages = []
    total_images = 0
    for i, page in enumerate(doc):
        text = page.get_text()
        images = page.get_images(full=True)
        total_images += len(images)
        pages.append({
            "page": i + 1,
            "chars": len(text),
            "text": text,
            "image_count": len(images),
        })
    doc.close()
    return {"page_count": len(pages), "total_images": total_images, "pages": pages}


def check_text_coverage(pages: list[dict], chunks: list[dict]) -> dict:
    all_chunk_text = clean_text("\n".join(c.get("content", "") for c in chunks))
    page_results = []
    fully_covered = 0
    partially_covered = 0
    uncovered = 0
    for p in pages:
        clean_page = clean_text(p["text"])
        if not clean_page:
            continue
        # Sample multiple snippets from the page
        snippets = [clean_page[i:i + 80] for i in range(0, len(clean_page), 200) if len(clean_page[i:i + 80]) >= 30]
        matched = sum(1 for s in snippets if s in all_chunk_text)
        ratio = matched / len(snippets) if snippets else 1.0
        if ratio >= 0.8:
            fully_covered += 1
        elif ratio >= 0.3:
            partially_covered += 1
        else:
            uncovered += 1
        page_results.append({
            "page": p["page"],
            "chars": p["chars"],
            "images": p["image_count"],
            "snippet_count": len(snippets),
            "matched_snippets": matched,
            "coverage_ratio": round(ratio, 2),
        })
    return {
        "page_results": page_results,
        "fully_covered": fully_covered,
        "partially_covered": partially_covered,
        "uncovered": uncovered,
    }


def check_key_strings(pages: list[dict], chunks: list[dict], key_strings: list[str]) -> dict:
    all_chunk_text = clean_text("\n".join(c.get("content", "") for c in chunks))
    missing = []
    for s in key_strings:
        if clean_text(s) not in all_chunk_text:
            missing.append(s)
    return {"checked": len(key_strings), "missing": missing, "missing_count": len(missing)}


def main():
    import sys
    doc_id = sys.argv[1] if len(sys.argv) > 1 else None
    pdf_rel = sys.argv[2] if len(sys.argv) > 2 else None
    if not doc_id or not pdf_rel:
        print("Usage: python _review_pdf_chunks.py <doc_id> <pdf_rel_path>")
        print("Example: python _review_pdf_chunks.py cb280325-... interface/ch340g_datasheet.pdf")
        return

    pdf_path = PDF_DIR / pdf_rel
    print(f"Reviewing {pdf_path.name} (doc_id={doc_id})")

    print("Fetching chunks...")
    chunks = get_doc_chunks(doc_id)
    print(f"  {len(chunks)} chunks, {sum(len(c.get('content','')) for c in chunks)} chars")

    print("Extracting PDF text/images...")
    pdf_data = extract_pdf_data(pdf_path)
    print(f"  {pdf_data['page_count']} pages, {pdf_data['total_images']} images")

    print("Checking text coverage...")
    coverage = check_text_coverage(pdf_data["pages"], chunks)
    print(f"  fully_covered={coverage['fully_covered']}, partial={coverage['partially_covered']}, uncovered={coverage['uncovered']}")

    # Show low coverage pages
    for pr in coverage["page_results"]:
        if pr["coverage_ratio"] < 0.5 and pr["chars"] > 100:
            print(f"  LOW page {pr['page']}: ratio={pr['coverage_ratio']}, chars={pr['chars']}, images={pr['images']}")

    print("Checking key strings...")
    # Generic key strings; can be overridden per PDF
    key_strings = [
        "CH340G",
        "USB to UART",
        "TXD",
        "RXD",
        "DTR",
        "RTS",
    ]
    key_result = check_key_strings(pdf_data["pages"], chunks, key_strings)
    print(f"  checked={key_result['checked']}, missing={key_result['missing_count']}")
    for s in key_result["missing"]:
        print(f"    MISSING: {s}")

    out = Path(f"_chunk_review_{Path(pdf_rel).stem}.json")
    report = {
        "doc_id": doc_id,
        "pdf": str(pdf_rel),
        "page_count": pdf_data["page_count"],
        "total_images": pdf_data["total_images"],
        "chunk_count": len(chunks),
        "chunk_chars": sum(len(c.get("content", "")) for c in chunks),
        "coverage": coverage,
        "key_strings": key_result,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved to {out}")


if __name__ == "__main__":
    main()
