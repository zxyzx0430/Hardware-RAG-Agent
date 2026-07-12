"""Render hard-question PDF pages to PNG for visual verification."""
import fitz
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "benchmark" / "hard_pages_render"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf", [5, 6, 7, 15, 16, 17, 18]),
    ("data/pdfs/mcu/esp32_datasheet.pdf", [2, 18, 22, 23, 24]),
]

for pdf_rel, pages in PAGES:
    pdf_path = ROOT / pdf_rel
    doc = fitz.open(pdf_path)
    name = pdf_path.stem
    for p in pages:
        page = doc[p - 1]
        pix = page.get_pixmap(dpi=200)
        out_path = OUT_DIR / f"{name}_p{p:03d}.png"
        pix.save(out_path)
        print(f"Saved {out_path}")
    doc.close()
