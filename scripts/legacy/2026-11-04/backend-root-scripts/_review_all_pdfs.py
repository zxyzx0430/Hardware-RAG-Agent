"""Batch review PDF chunk coverage for the test KB."""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz
import httpx

API_BASE = "http://127.0.0.1:58080"
PDF_DIR = Path(r"E:\Desktop\agent\data\pdfs")

DOCS = [
    {
        "doc_id": "8f642c64-855f-4041-9af1-7b4818169b3f",
        "pdf": "mcu/stm32f103c8_datasheet.pdf",
        "key_strings": [
            "STM32F103C8",
            "GPIO",
            "Pinout",
            "LQFP48",
            "ARM Cortex-M3",
            "72 MHz",
            "Flash memory",
        ],
    },
    {
        "doc_id": "f0c9b03b-1703-4e01-9637-211a4cd39035",
        "pdf": "mcu/esp32-s3_datasheet.pdf",
        "key_strings": [
            "ESP32-S3",
            "Xtensa LX7",
            "Wi-Fi",
            "Bluetooth 5",
            "GPIO",
            "QFN56",
        ],
    },
    {
        "doc_id": "9c86944b-c009-4cbd-914a-e8fc7bba703b",
        "pdf": "sensors/mpu6050_datasheet.pdf",
        "key_strings": [
            "MPU-6050",
            "gyroscope",
            "accelerometer",
            "I2C",
            "Pinout",
            "FIFO",
        ],
    },
    {
        "doc_id": "4998bf62-e2a0-403c-add5-31ee3b7fedd6",
        "pdf": "sensors/bme280_datasheet.pdf",
        "key_strings": [
            "BME280",
            "humidity",
            "pressure",
            "temperature",
            "I2C",
            "SPI",
            "Pinout",
        ],
    },
    {
        "doc_id": "cb280325-2601-48a3-8387-e80d79e88d4f",
        "pdf": "interface/ch340g_datasheet.pdf",
        "key_strings": [
            "CH340G",
            "USB to UART",
            "TXD",
            "RXD",
            "DTR",
            "RTS",
        ],
    },
]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def get_chunks(doc_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    r.raise_for_status()
    return r.json().get("data", {}).get("chunks", [])


def extract_pdf(pdf_path: Path) -> dict:
    doc = fitz.open(str(pdf_path))
    pages = []
    total_images = 0
    total_chars = 0
    for i, page in enumerate(doc):
        text = page.get_text()
        images = page.get_images(full=True)
        total_images += len(images)
        total_chars += len(text)
        pages.append({"page": i + 1, "chars": len(text), "text": text, "images": len(images)})
    doc.close()
    return {"page_count": len(pages), "total_images": total_images, "total_chars": total_chars, "pages": pages}


def check_coverage(pages: list[dict], chunks: list[dict]) -> dict:
    all_chunk_text = clean_text("\n".join(c.get("content", "") for c in chunks))
    results = []
    fully = partial = uncovered = 0
    for p in pages:
        clean_page = clean_text(p["text"])
        if not clean_page:
            continue
        snippets = [clean_page[i:i + 80] for i in range(0, len(clean_page), 200) if len(clean_page[i:i + 80]) >= 30]
        matched = sum(1 for s in snippets if s in all_chunk_text)
        ratio = matched / len(snippets) if snippets else 1.0
        if ratio >= 0.8:
            fully += 1
        elif ratio >= 0.3:
            partial += 1
        else:
            uncovered += 1
        results.append({
            "page": p["page"],
            "chars": p["chars"],
            "images": p["images"],
            "snippet_count": len(snippets),
            "matched": matched,
            "ratio": round(ratio, 2),
        })
    return {"page_results": results, "fully": fully, "partial": partial, "uncovered": uncovered}


def check_key_strings(chunks: list[dict], key_strings: list[str]) -> dict:
    all_chunk_text = clean_text("\n".join(c.get("content", "") for c in chunks))
    missing = [s for s in key_strings if clean_text(s) not in all_chunk_text]
    return {"checked": len(key_strings), "missing": missing, "missing_count": len(missing)}


def main():
    reports = []
    for item in DOCS:
        pdf_path = PDF_DIR / item["pdf"]
        print(f"\n=== {pdf_path.name} ===")
        chunks = get_chunks(item["doc_id"])
        pdf_data = extract_pdf(pdf_path)
        coverage = check_coverage(pdf_data["pages"], chunks)
        keys = check_key_strings(chunks, item["key_strings"])
        low_pages = [r for r in coverage["page_results"] if r["ratio"] < 0.5 and r["chars"] > 100]
        print(f"  pages={pdf_data['page_count']} images={pdf_data['total_images']} chars={pdf_data['total_chars']}")
        print(f"  chunks={len(chunks)} chunk_chars={sum(len(c.get('content','')) for c in chunks)}")
        print(f"  coverage: fully={coverage['fully']} partial={coverage['partial']} uncovered={coverage['uncovered']}")
        print(f"  key_strings: checked={keys['checked']} missing={keys['missing_count']}")
        for lp in low_pages[:5]:
            print(f"  LOW page {lp['page']}: ratio={lp['ratio']} chars={lp['chars']} images={lp['images']}")
        reports.append({
            "doc_id": item["doc_id"],
            "pdf": item["pdf"],
            "page_count": pdf_data["page_count"],
            "total_images": pdf_data["total_images"],
            "total_chars": pdf_data["total_chars"],
            "chunk_count": len(chunks),
            "chunk_chars": sum(len(c.get("content", "")) for c in chunks),
            "coverage": coverage,
            "key_strings": keys,
            "low_pages": low_pages,
        })

    out = Path("_chunk_review_all_pdfs.json")
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCombined report saved to {out}")


if __name__ == "__main__":
    main()
