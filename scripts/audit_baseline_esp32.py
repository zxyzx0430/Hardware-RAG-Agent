"""Audit ESP32 chunks ingested into builtin-001.

Run: python scripts/audit_baseline_esp32.py
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

PDF_PATH = BACKEND.parent / "data" / "pdfs" / "mcu" / "esp32_datasheet.pdf"
SNAPSHOT_PATH = BACKEND.parent / "data" / "benchmark" / "esp32_chunks.json"
REPORT_PATH = BACKEND.parent / "docs" / "reports" / "audit_baseline_esp32_20260630.md"
KB_ID = "builtin-001"


def _page_texts(pdf_path: Path) -> list[str]:
    doc = fitz.open(pdf_path)
    texts = []
    for page in doc:
        texts.append(page.get_text())
    doc.close()
    return texts


def _load_chunks():
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    doc_id = snapshot["doc_id"]
    chunks = snapshot["chunks"]
    return doc_id, chunks, snapshot


def _chunks_for_page(chunks: list[dict], page: int) -> list[dict]:
    matches = []
    for c in chunks:
        start, end = c["page_range"]
        if start <= page <= end:
            matches.append(c)
    return matches


KEY_CONTENT = {
    "pin_overview": {
        "name": "Pin Overview / Pin List",
        "pages": list(range(12, 22)),
        "signals": [
            "Pin Overview",
            "IO Pins",
            "Power Pins",
            "VDD3P3_RTC",
            "GPIO",
            "Pin No.",
        ],
    },
    "strapping_pins": {
        "name": "Strapping Pins / Boot Config",
        "pages": list(range(22, 26)),
        "signals": [
            "Strapping Pins",
            "Boot Configurations",
            "MTDI",
            "MTDO",
            "GPIO0",
            "GPIO2",
            "GPIO5",
        ],
    },
    "functional_block": {
        "name": "Functional Description / Block Diagram",
        "pages": list(range(26, 37)),
        "signals": [
            "Functional Description",
            "CPU and Memory",
            "External Flash",
            "System Clocks",
            "Radio",
        ],
    },
    "peripheral_interfaces": {
        "name": "Peripheral Pin Configurations (Table 4-6)",
        "pages": list(range(47, 52)),
        "signals": [
            "Peripheral Pin Configurations",
            "UART",
            "SPI",
            "I2C",
            "I2S",
            "SDIO",
        ],
        "table_signature": "|Interface|Signal|Pin|Function|",
    },
    "electrical_characteristics": {
        "name": "Electrical Characteristics",
        "pages": list(range(52, 60)),
        "signals": [
            "Electrical Characteristics",
            "DC Characteristics",
            "Current Consumption",
            "RF Power",
            "Transmitter Characteristics",
        ],
    },
    "appendix_pin_lists": {
        "name": "Appendix A Pin Lists (GPIO_Matrix / IO_MUX)",
        "pages": list(range(62, 71)),
        "signals": [
            "GPIO_Matrix",
            "IO_MUX",
            "Input Signals",
            "Output Signals",
            "Pin No.",
        ],
    },
}


def _count_signal_matches(text: str, signals: list[str]) -> int:
    text_lower = text.lower()
    return sum(1 for s in signals if s.lower() in text_lower)


def _has_table_signature(text: str, signature: str | None) -> bool:
    if not signature:
        return False
    return signature in text


def main():
    doc_id, chunks, snapshot = _load_chunks()
    total_pages = snapshot["total_pages"]
    page_texts = _page_texts(PDF_PATH)

    text_chunks = [c for c in chunks if c.get("content_type", "text") != "image_description"]
    img_chunks = [c for c in chunks if c.get("content_type", "text") == "image_description"]

    # Per-page audit
    page_report = []
    covered_pages = set()
    uncovered_pages = []
    for page in range(1, total_pages + 1):
        page_chunks = _chunks_for_page(chunks, page)
        if page_chunks:
            covered_pages.add(page)
        else:
            uncovered_pages.append(page)
        combined_text = "\n".join(c["text"] for c in page_chunks)
        page_report.append({
            "page": page,
            "chunk_count": len(page_chunks),
            "pdf_chars": len(page_texts[page - 1]),
            "chunk_chars": len(combined_text),
            "sections": sorted(set(c["section_title"] for c in page_chunks if c.get("section_title"))),
        })

    # Duplication rate: chunks with identical fingerprint
    fp_counts = Counter(c["fingerprint"] for c in chunks)
    duplicate_fingerprints = {fp: cnt for fp, cnt in fp_counts.items() if cnt > 1}
    duplicate_chunks = sum(cnt - 1 for cnt in duplicate_fingerprints.values())
    duplication_rate = duplicate_chunks / len(chunks) if chunks else 0.0

    # Page coverage
    page_coverage = len(covered_pages) / total_pages if total_pages else 0.0

    # Detect mis-attributed table chunks: markdown tables assigned to page 1 while
    # their real content belongs to later pages (e.g., Peripheral Pin Configurations).
    table_row_re = re.compile(r"^\|[^\n]+\|", re.MULTILINE)
    misattributed_tables = []
    for c in chunks:
        if c["page_range"][0] == 1 and table_row_re.findall(c["text"]):
            # Heuristic: real p1 is title page (417 chars); a p1 chunk containing
            # large markdown tables is almost certainly mis-attributed.
            if len(c["text"]) > 500:
                misattributed_tables.append({
                    "page_range": c["page_range"],
                    "section_title": c["section_title"],
                    "chunk_size": len(c["text"]),
                    "preview": c["text"][:120].replace("\n", " "),
                })

    # Key content coverage
    key_content_report = {}
    for key, cfg in KEY_CONTENT.items():
        key_pages = set(cfg["pages"])
        key_chunks = []
        for c in chunks:
            start, end = c["page_range"]
            if key_pages & set(range(start, end + 1)):
                key_chunks.append(c)
        combined = "\n".join(c["text"] for c in key_chunks)
        matched = _count_signal_matches(combined, cfg["signals"])
        total_signals = len(cfg["signals"])
        sig_ratio = matched / total_signals if total_signals else 0.0

        sig = cfg.get("table_signature")
        table_ok = _has_table_signature(combined, sig) if sig else None

        # Category passes if signal coverage >= 60% AND (no table signature required or table present)
        ok = sig_ratio >= 0.6
        if table_ok is not None:
            ok = ok and table_ok

        key_content_report[key] = {
            "name": cfg["name"],
            "pages": cfg["pages"],
            "chunk_count": len(key_chunks),
            "signal_coverage": f"{matched}/{total_signals}",
            "signal_ratio": sig_ratio,
            "table_signature_present": table_ok,
            "ok": ok,
        }

    overall_key_coverage = sum(1 for v in key_content_report.values() if v["ok"]) / len(key_content_report)

    # Build markdown report
    lines = []
    lines.append("# ESP32 Datasheet Chunk Audit Report")
    lines.append("")
    lines.append(f"- **Date**: 2026-06-30")
    lines.append(f"- **KB ID**: {KB_ID}")
    lines.append(f"- **Collection**: hardware-docs-test")
    lines.append(f"- **PDF**: `{PDF_PATH}`")
    lines.append(f"- **doc_id**: `{doc_id}`")
    lines.append("")

    lines.append("## Ingestion Summary")
    lines.append("")
    lines.append(f"- Total pages: {total_pages}")
    lines.append(f"- Total chunks: {len(chunks)}")
    lines.append(f"- Text chunks: {len(text_chunks)}")
    lines.append(f"- Image description chunks: {len(img_chunks)}")
    lines.append(f"- Small chunk size: {snapshot['small_chunk_size']}")
    lines.append(f"- Chunking time: {snapshot['chunking_time_seconds']}s")
    lines.append("")

    lines.append("## Overall Metrics")
    lines.append("")
    lines.append(f"- **Page coverage**: {len(covered_pages)}/{total_pages} ({page_coverage:.1%})")
    lines.append(f"- **Duplicate chunks**: {duplicate_chunks} ({duplication_rate:.1%})")
    lines.append(f"- **Key content coverage**: {overall_key_coverage:.1%} ({sum(1 for v in key_content_report.values() if v['ok'])}/{len(key_content_report)} categories OK)")
    if misattributed_tables:
        lines.append(f"- **Mis-attributed table chunks**: {len(misattributed_tables)} chunks (table content assigned to page 1 instead of source pages)")
    lines.append("")

    lines.append("## Key Content Coverage")
    lines.append("")
    lines.append("| Category | Pages | Chunks | Signal Coverage | Table Present | Status |")
    lines.append("|----------|-------|--------|-----------------|---------------|--------|")
    for key in [
        "pin_overview",
        "strapping_pins",
        "functional_block",
        "peripheral_interfaces",
        "electrical_characteristics",
        "appendix_pin_lists",
    ]:
        v = key_content_report[key]
        status = "OK" if v["ok"] else "WARN"
        table_present = "yes" if v["table_signature_present"] is True else ("n/a" if v["table_signature_present"] is None else "no")
        lines.append(
            f"| {v['name']} | {v['pages'][0]}-{v['pages'][-1]} | {v['chunk_count']} | {v['signal_coverage']} | {table_present} | {status} |"
        )
    lines.append("")

    if uncovered_pages:
        lines.append("## Uncovered Pages")
        lines.append("")
        lines.append(f"Pages with no chunk coverage: {uncovered_pages}")
        lines.append("")
    else:
        lines.append("## Page Coverage")
        lines.append("")
        lines.append("All 78 pages are covered by at least one chunk.")
        lines.append("")

    if misattributed_tables:
        lines.append("## Mis-attributed Table Content")
        lines.append("")
        lines.append("The following chunks contain markdown tables but are assigned to page 1, indicating page-marker loss during table placeholder protection/restoration.")
        lines.append("")
        lines.append("| page_range | chunk_size | section_title | preview |")
        lines.append("|------------|------------|---------------|---------|")
        for item in misattributed_tables[:20]:
            section = item["section_title"][:50]
            lines.append(
                f"| {item['page_range']} | {item['chunk_size']} | {section} | {item['preview']} |"
            )
        lines.append("")

    lines.append("## Per-Page Audit")
    lines.append("")
    lines.append("| Page | Chunks | PDF chars | Chunk chars | Sections |")
    lines.append("|------|--------|-----------|-------------|----------|")
    for pr in page_report:
        sections = ", ".join(pr["sections"][:3]) if pr["sections"] else "-"
        if len(sections) > 60:
            sections = sections[:57] + "..."
        lines.append(
            f"| {pr['page']} | {pr['chunk_count']} | {pr['pdf_chars']} | {pr['chunk_chars']} | {sections} |"
        )
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    issues = []
    if uncovered_pages:
        issues.append(f"Uncovered pages: {uncovered_pages}")
    if duplication_rate > 0.05:
        issues.append(f"Duplication rate {duplication_rate:.1%} exceeds 5% threshold")
    for key, v in key_content_report.items():
        if not v["ok"]:
            issues.append(f"Low coverage for {v['name']}: signals {v['signal_coverage']}, table present={v['table_signature_present']}")
    if misattributed_tables:
        issues.append(
            f"{len(misattributed_tables)} table chunks are mis-attributed to page 1 "
            "(likely due to page markers being stripped from protected table placeholders). "
            "Affected content includes Peripheral Pin Configurations (p47-51) and Appendix A pin lists (p62-70)."
        )

    if img_chunks:
        issues.append(f"Unexpected {len(img_chunks)} image_description chunks from HybridChunker")

    if issues:
        lines.append("### Issues")
        lines.append("")
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("")
    else:
        lines.append("No blocking issues found.")
        lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- HybridChunker does not generate image_description chunks; pinout/package diagrams are only covered if their caption/table text appears in text chunks.")
    lines.append("- This audit uses the chunk metadata snapshot at `data/benchmark/esp32_chunks.json`.")
    lines.append("- Signal coverage checks whether representative keywords for each key content category appear in chunks spanning the relevant page range.")
    lines.append("- Mis-attributed table chunks indicate that table content was preserved but assigned an incorrect page range, which harms page-filtered retrieval and citations.")
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit report saved: {REPORT_PATH}")
    print(f"Page coverage: {len(covered_pages)}/{total_pages} ({page_coverage:.1%})")
    print(f"Duplication rate: {duplication_rate:.1%}")
    print(f"Key content coverage: {overall_key_coverage:.1%}")
    print(f"Mis-attributed table chunks: {len(misattributed_tables)}")


if __name__ == "__main__":
    main()
