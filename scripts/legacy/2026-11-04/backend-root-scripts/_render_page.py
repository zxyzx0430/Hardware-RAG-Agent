from pathlib import Path
import fitz

pdf_dir = Path(r"E:\Desktop\agent\data\pdfs")

def render(pdf_name: str, page_num: int, out_name: str):
    matches = list(pdf_dir.rglob(pdf_name))
    if not matches:
        print(f"{pdf_name} not found")
        return
    doc = fitz.open(matches[0])
    page = doc[page_num - 1]  # 0-based
    pix = page.get_pixmap(dpi=150)
    out_path = Path(out_name)
    pix.save(out_path)
    print(f"Saved {out_path} ({pix.width}x{pix.height})")
    doc.close()

# Review gaps
render("bme280_datasheet.pdf", 31, "_bme280_page31.png")
for p in [1, 2, 3, 4]:
    render("mpu6050_datasheet.pdf", p, f"_mpu6050_page{p}.png")
