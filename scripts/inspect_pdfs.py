import fitz, os, json

pdf_paths = [
    'data/pdfs/interface/ch340g_datasheet.pdf',
    'data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf',
    'data/pdfs/mcu/esp32_datasheet.pdf'
]

for p in pdf_paths:
    doc = fitz.open(p)
    print(f'=== {p} ===')
    print(f'pages: {len(doc)}')
    for i in range(min(5, len(doc))):
        text = doc[i].get_text()
        preview = text[:200].replace('\n', ' ')
        print(f'Page {i+1}: {preview}')
    print()
