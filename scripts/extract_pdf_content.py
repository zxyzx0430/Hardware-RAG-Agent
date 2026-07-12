"""深度提取3个PDF的文本、表格、图像，供设计golden问题。"""
import fitz
import json
from pathlib import Path

PDFS = {
    "ch340g": "data/pdfs/interface/ch340g_datasheet.pdf",
    "stm32f4": "data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf",
    "esp32": "data/pdfs/mcu/esp32_datasheet.pdf",
}

OUT_DIR = Path("data/benchmark/pdf_extracts")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_tables(page):
    tables = []
    try:
        tabs = page.find_tables()
        for t in tabs.tables:
            tables.append({
                "bbox": t.bbox,
                "rows": t.extract(),
            })
    except Exception as e:
        tables.append({"error": str(e)})
    return tables


def extract_images(page, doc_name, page_num):
    images = []
    for img_idx, img in enumerate(page.get_images(full=True)):
        xref = img[0]
        try:
            pix = fitz.Pixmap(page.parent, xref)
            if pix.n > 4:  # CMYK: convert to RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            out_path = OUT_DIR / f"{doc_name}_p{page_num+1}_img{img_idx}.png"
            pix.save(str(out_path))
            images.append(str(out_path))
            pix = None
        except Exception as e:
            images.append({"error": str(e), "xref": xref})
    return images


def main():
    for name, path in PDFS.items():
        doc = fitz.open(path)
        result = {
            "path": path,
            "pages": len(doc),
            "pages_data": [],
        }
        for i in range(len(doc)):
            page = doc[i]
            text = page.get_text()
            tables = extract_tables(page)
            images = extract_images(page, name, i)
            result["pages_data"].append({
                "page": i + 1,
                "text": text,
                "tables": tables,
                "images": images,
            })
        out_file = OUT_DIR / f"{name}_extract.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Extracted {name}: {len(doc)} pages -> {out_file}")


if __name__ == "__main__":
    main()
