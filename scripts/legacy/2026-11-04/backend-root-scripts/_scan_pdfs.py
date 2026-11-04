"""Quick scan candidate PDFs: pages and image counts."""
from pathlib import Path
import fitz  # PyMuPDF

PDF_DIR = Path(r"E:\Desktop\agent\data\pdfs")

candidates = [
    "mcu/stm32f103c8_datasheet.pdf",
    "mcu/esp32-s3_datasheet.pdf",
    "mcu/stm32f407vg_datasheet.pdf",
    "sensors/mpu6050_datasheet.pdf",
    "sensors/bme280_datasheet.pdf",
    "interface/ch340g_datasheet.pdf",
]

for rel in candidates:
    p = PDF_DIR / rel
    if not p.exists():
        print(f"MISS {rel}")
        continue
    try:
        doc = fitz.open(str(p))
        page_count = len(doc)
        img_count = 0
        for page in doc:
            img_count += len(page.get_images(full=True))
        text_sample = doc[0].get_text()[:120].replace("\n", " ")
        doc.close()
        print(f"{rel}: {page_count} pages, {img_count} images, head={text_sample!r}")
    except Exception as e:
        print(f"{rel}: ERROR {e}")
