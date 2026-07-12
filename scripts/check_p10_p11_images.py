"""Check if p10/p11 of ch340g PDF contain embedded images."""
import fitz
from pathlib import Path

PDF_PATH = Path(__file__).resolve().parent.parent / "data" / "pdfs" / "interface" / "ch340g_datasheet.pdf"


def main():
    doc = fitz.open(str(PDF_PATH))
    for page_num in [10, 11]:
        page = doc[page_num - 1]
        images = page.get_images()
        text = page.get_text("text")[:200].replace("\n", " ")
        print(f"p{page_num}: images={len(images)}, text_preview={text}...")
    doc.close()


if __name__ == "__main__":
    main()
