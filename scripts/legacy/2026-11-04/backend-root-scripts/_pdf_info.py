from pathlib import Path
import fitz  # PyMuPDF

pdf_dir = Path(r"E:\Desktop\agent\data\pdfs")
names = ["bme280_datasheet.pdf", "mpu6050_datasheet.pdf", "nrf24l01_datasheet.pdf"]
for name in names:
    matches = list(pdf_dir.rglob(name))
    if not matches:
        print(f"{name}: NOT FOUND")
        continue
    p = matches[0]
    doc = fitz.open(p)
    print(f"{p.relative_to(pdf_dir.parent)}: {len(doc)} pages")
    doc.close()
