"""PoC: compare PyMuPDF vs opendataloader-pdf on ch340g_datasheet.pdf.

Compares:
1. Speed (parsing time)
2. Table quality (electrical params on p12, register tables)
3. Image detection (p7/p8/p12/p13/p14)
4. Text completeness (total chars, section coverage)

Run: python scripts/opendataloader_poc.py
"""
import sys
import time
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
ROOT = BACKEND.parent
PDF = ROOT / "data" / "pdfs" / "interface" / "ch340g_datasheet.pdf"
OUT_DIR = ROOT / "scripts" / "output_opendataloader"
OUT_DIR.mkdir(exist_ok=True)


def run_pymupdf() -> dict:
    """Parse with PyMuPDF (current approach)."""
    import fitz
    t0 = time.time()
    doc = fitz.open(str(PDF))
    pages = []
    for i in range(doc.page_count):
        page = doc[i]
        text = page.get_text("text") or ""
        images = page.get_images()
        pages.append({
            "page": i + 1,
            "chars": len(text),
            "images": len(images),
            "text": text,
        })
    doc.close()
    elapsed = time.time() - t0
    total_chars = sum(p["chars"] for p in pages)
    return {
        "engine": "PyMuPDF",
        "elapsed_s": round(elapsed, 2),
        "total_chars": total_chars,
        "pages": pages,
    }


def run_opendataloader() -> dict:
    """Parse with opendataloader-pdf (local mode, no hybrid server)."""
    import opendataloader_pdf
    t0 = time.time()
    opendataloader_pdf.convert(
        input_path=[str(PDF)],
        output_dir=str(OUT_DIR),
        format="markdown,json",
    )
    elapsed = time.time() - t0
    # Find output files
    md_files = list(OUT_DIR.glob("*.md"))
    json_files = list(OUT_DIR.glob("*.json"))
    total_chars = 0
    md_content = ""
    if md_files:
        md_content = md_files[0].read_text(encoding="utf-8")
        total_chars = len(md_content)
    # Parse JSON for element stats
    elements = []
    if json_files:
        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            elements = data if isinstance(data, list) else data.get("elements", [])
        except Exception:
            pass
    return {
        "engine": "OpenDataLoader",
        "elapsed_s": round(elapsed, 2),
        "total_chars": total_chars,
        "md_file": str(md_files[0]) if md_files else "",
        "json_file": str(json_files[0]) if json_files else "",
        "elements": len(elements),
        "md_content": md_content,
    }


def main() -> None:
    print(f"PDF: {PDF.name} ({PDF.stat().st_size / 1024:.0f} KB)")

    # PyMuPDF
    print("\n--- PyMuPDF ---")
    pm = run_pymupdf()
    print(f"Time: {pm['elapsed_s']}s, Chars: {pm['total_chars']}")
    img_pages = [p["page"] for p in pm["pages"] if p["images"] > 0]
    print(f"Image pages: {img_pages}")
    # Show p12 table area
    p12 = next(p for p in pm["pages"] if p["page"] == 12)
    print(f"p12 preview (first 300 chars):\n{p12['text'][:300]}")

    # OpenDataLoader
    print("\n--- OpenDataLoader ---")
    try:
        od = run_opendataloader()
        print(f"Time: {od['elapsed_s']}s, Chars: {od['total_chars']}, Elements: {od['elements']}")
        print(f"MD: {od['md_file']}")
        print(f"JSON: {od['json_file']}")
        # Show table areas in markdown
        md = od["md_content"]
        if "|" in md:
            # Find table lines
            table_lines = [l for l in md.split("\n") if l.strip().startswith("|")]
            print(f"Table lines in MD: {len(table_lines)}")
            if table_lines:
                print("First table preview:")
                for l in table_lines[:10]:
                    print(f"  {l}")
    except Exception as e:
        print(f"OpenDataLoader failed: {e}")
        import traceback
        traceback.print_exc()
        return

    # Comparison
    print("\n=== Comparison ===")
    print(f"{'Metric':<25} {'PyMuPDF':>15} {'OpenDataLoader':>15}")
    print(f"{'Time (s)':<25} {pm['elapsed_s']:>15} {od['elapsed_s']:>15}")
    print(f"{'Total chars':<25} {pm['total_chars']:>15} {od['total_chars']:>15}")
    speed_ratio = pm["elapsed_s"] / od["elapsed_s"] if od["elapsed_s"] > 0 else 0
    print(f"Speed ratio (PM/OD): {speed_ratio:.2f}x")


if __name__ == "__main__":
    main()
