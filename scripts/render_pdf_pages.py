"""将PDF页面渲染为图片，用于查看引脚图/封装图/原理图。"""
import fitz
from pathlib import Path

PDFS = {
    "ch340g": "data/pdfs/interface/ch340g_datasheet.pdf",
    "stm32f4": "data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf",
    "esp32": "data/pdfs/mcu/esp32_datasheet.pdf",
}

OUT_DIR = Path("data/benchmark/pdf_extracts/pages_png")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def render(pdf_path, name, pages, dpi=200):
    doc = fitz.open(pdf_path)
    for p in pages:
        if p > len(doc):
            continue
        page = doc[p - 1]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        out = OUT_DIR / f"{name}_p{p}_{dpi}dpi.png"
        pix.save(str(out))
        print(f"Saved {out}")


if __name__ == "__main__":
    render(PDFS["ch340g"], "ch340g", [4, 5, 6, 8, 13, 14], dpi=200)
    # stm32f4 and esp32: render first few pages and pages with tables/diagrams
    render(PDFS["stm32f4"], "stm32f4", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dpi=200)
    render(PDFS["esp32"], "esp32", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], dpi=200)
