"""Read a specific PDF page text."""
import sys
from pathlib import Path
import fitz

PDF_DIR = Path(r"E:\Desktop\agent\data\pdfs")

pdf_rel = sys.argv[1]
page_num = int(sys.argv[2]) - 1  # 0-based

p = PDF_DIR / pdf_rel
doc = fitz.open(str(p))
text = doc[page_num].get_text()
doc.close()
print(text)
