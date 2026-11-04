"""Verify that indexed chunks actually cover the source markdown content."""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

API_BASE = "http://127.0.0.1:58080"
SOURCE_PATH = Path(r"E:\Desktop\agent\data\test_docs\01-stm32-gpio.md")
DOC_ID = "cecbacc8-8d3f-4ba6-a5af-61e56d3270c4"


def get_doc_chunks(doc_id: str) -> list[dict]:
    r = httpx.get(f"{API_BASE}/api/kb/documents/{doc_id}/chunks", timeout=60)
    r.raise_for_status()
    return r.json().get("data", {}).get("chunks", [])


def clean_text(text: str) -> str:
    """Normalize whitespace for comparison."""
    return re.sub(r"\s+", "", text.strip())


def split_by_headers(text: str) -> list[tuple[str, str]]:
    """Split markdown by top-level (##) and second-level (###) headers."""
    pattern = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        header = m.group(2).strip()
        body = text[start:end].strip()
        sections.append((header, body))
    return sections


def check_key_strings(chunks: list[dict], key_strings: list[str], label: str) -> dict:
    """Check if specific key strings from source appear in any chunk."""
    all_chunk_text = "\n".join(c.get("content", "") for c in chunks)
    missing = []
    for s in key_strings:
        if clean_text(s) not in clean_text(all_chunk_text):
            missing.append(s)
    return {
        "label": label,
        "checked": len(key_strings),
        "missing": missing,
        "missing_count": len(missing),
    }


def check_section_coverage(sections: list[tuple[str, str]], chunks: list[dict]) -> list[dict]:
    """For each source section, check whether a meaningful excerpt is present in chunks."""
    all_chunk_text = "\n".join(c.get("content", "") for c in chunks)
    clean_chunks = clean_text(all_chunk_text)
    results = []
    for header, body in sections:
        # Skip table of contents and very short sections
        if header == "目录" or len(body) < 50:
            continue
        # Extract first substantial sentence (>=20 chars after cleaning)
        sentences = re.split(r"[。\.\n]", body)
        found = False
        sample = ""
        for sent in sentences:
            clean_sent = clean_text(sent)
            if len(clean_sent) >= 20:
                sample = clean_sent[:80]
                if clean_sent in clean_chunks:
                    found = True
                    break
        if not sample:
            sample = clean_text(body)[:80]
            found = sample in clean_chunks
        results.append({
            "header": header,
            "found": found,
            "sample": sample,
            "body_len": len(body),
        })
    return results


def main():
    print("Loading source markdown...")
    source = SOURCE_PATH.read_text(encoding="utf-8")
    sections = split_by_headers(source)
    print(f"Source: {len(source)} chars, {len(sections)} sections")

    print("\nFetching indexed chunks...")
    chunks = get_doc_chunks(DOC_ID)
    chunk_total = sum(len(c.get("content", "")) for c in chunks)
    print(f"Indexed: {len(chunks)} chunks, {chunk_total} chars")

    print("\n=== Section coverage check ===")
    coverage = check_section_coverage(sections, chunks)
    missing_sections = [c for c in coverage if not c["found"]]
    print(f"Checked {len(coverage)} sections, missing: {len(missing_sections)}")
    for c in missing_sections[:10]:
        print(f"  MISSING: {c['header']} | sample={c['sample'][:60]}")

    print("\n=== Key strings check (EXTI / LCKR) ===")
    exti_lckr_strings = [
        "EXTI0~EXTI4 有独立的中断向量，EXTI5~EXTI9 共享一个向量",
        "同一个 EXTI 线上的多个引脚不能同时使用中断",
        "NVIC_SetPriority(EXTI0_IRQn, 5)",
        "EXTI9_5_IRQHandler",
        "LCKR 寄存器用于锁定 GPIO 引脚的配置寄存器",
        "锁定后，直到下一次 MCU 复位才能解锁",
        "写 LCKR[16] = 1 + LCKR[15:0]",
        "读 LCKR[16]：如果为 1，锁定成功",
        "HAL_GPIO_LockPin",
    ]
    key_result = check_key_strings(chunks, exti_lckr_strings, "EXTI/LCKR")
    print(f"Checked {key_result['checked']} strings, missing: {key_result['missing_count']}")
    for s in key_result["missing"]:
        print(f"  MISSING: {s[:80]}")

    print("\n=== Global coverage ratio estimate ===")
    clean_source = clean_text(source)
    clean_chunks = clean_text("\n".join(c.get("content", "") for c in chunks))
    # Estimate coverage by sampling non-trivial lines from source
    source_lines = [clean_text(line) for line in source.splitlines() if len(clean_text(line)) >= 30]
    matched = sum(1 for line in source_lines if line in clean_chunks)
    print(f"Sampled {len(source_lines)} non-trivial lines, matched {matched} ({matched/len(source_lines):.1%})")

    out = Path("_chunk_integrity_report.json")
    report = {
        "doc_id": DOC_ID,
        "source_chars": len(source),
        "source_sections": len(sections),
        "chunk_count": len(chunks),
        "chunk_chars": chunk_total,
        "missing_sections": missing_sections,
        "key_strings": key_result,
        "line_coverage": round(matched / len(source_lines), 4) if source_lines else 0,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved to {out}")


if __name__ == "__main__":
    main()
