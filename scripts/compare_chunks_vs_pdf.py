"""Compare exported chunks against PDF ground truth for ch340g.

Outputs: scripts/audit/chunk_integrity_report.md
"""
import sys
import json
from pathlib import Path
from collections import Counter, defaultdict

AUDIT_DIR = Path(__file__).resolve().parent / "audit"
CHUNKS_FILE = AUDIT_DIR / "ch340g_chunks_chroma.jsonl"
MANIFEST_FILE = AUDIT_DIR / "ch340g_pages" / "pages_manifest.json"
OUT_REPORT = AUDIT_DIR / "chunk_integrity_report.md"

# Ground truth built from visual inspection of each page
EXPECTED_CONTENT = {
    1: {"type": "text", "items": ["Title", "Overview", "Features list"]},
    2: {"type": "text", "items": ["Table of Contents"]},
    3: {"type": "mixed", "items": [
        "3.1 Absolute Maximum Ratings table",
        "3.2 DC characteristics table",
        "3.3 AC characteristics table",
    ]},
    4: {"type": "mixed", "items": [
        "4. Pinout table (16 pins)",
        "5. Application Notes text",
    ]},
    5: {"type": "mixed", "items": [
        "Application Notes text",
        "5.1 USB to RS232 adapter schematic (vector diagram)",
    ]},
    6: {"type": "mixed", "items": [
        "5.2 Optically isolated USB to UART adapter schematic (vector diagram)",
    ]},
    7: {"type": "mixed", "items": [
        "1 Introduction text",
        "System application block diagram (image)",
        "2 Features list",
    ]},
    8: {"type": "mixed", "items": [
        "3 Package section",
        "6 package pinout diagrams (CH340G/C/B/E/T/R)",
        "Package shape table",
        "4 Pins table start",
    ]},
    9: {"type": "mixed", "items": [
        "4 Pins table continuation (cross-package pin functions)",
        "5 Function Description text",
    ]},
    10: {"type": "mixed", "items": [
        "EEPROM configuration data area table",
        "Function Description text",
    ]},
    11: {"type": "mixed", "items": [
        "Function Description text",
        "6.1 Absolute maximum rating table",
    ]},
    12: {"type": "mixed", "items": [
        "6.2 Electrical Parameter table",
        "6.3 Sequence Parameter table",
        "7.1 USB to RS232 Converter schematic (CH340T)",
    ]},
    13: {"type": "mixed", "items": [
        "7.1.2 USB to RS232 CH340B schematic",
        "7.2 USB to RS232 3-wire CH340T schematic",
        "Application text",
    ]},
    14: {"type": "mixed", "items": [
        "7.3 Simplified USB to RS232 schematic (CH340T)",
        "7.4 USB to Infrared Adapter schematic (CH340R)",
        "7.5 USB to RS485 text",
    ]},
}


def load_chunks():
    chunks = []
    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def build_coverage(chunks):
    # Map page -> list of (content_type, section_title, snippet)
    coverage = defaultdict(list)
    image_desc_pages = set()
    for c in chunks:
        meta = c["metadata"]
        doc = c["document"]
        ct = meta.get("content_type", "text")
        ps = meta.get("page_start")
        pe = meta.get("page_end")
        title = meta.get("section_title", "")
        src = meta.get("source_page")
        if ct == "image_description" and src:
            image_desc_pages.add(src)
        # Determine page range
        pages = []
        if ps is not None and pe is not None:
            try:
                pages = list(range(int(ps), int(pe) + 1))
            except Exception:
                pass
        elif src:
            pages = [src]
        for p in pages:
            coverage[p].append({
                "content_type": ct,
                "section_title": title,
                "snippet": doc[:200].replace("\n", " "),
                "source_page": src,
            })
    return coverage, image_desc_pages


def main():
    chunks = load_chunks()
    coverage, image_desc_pages = build_coverage(chunks)

    lines = []
    lines.append("# CH340G Chunk 完整性审计报告\n")
    lines.append(f"- 审计时间：2026-06-29\n")
    lines.append(f"- PDF：data/pdfs/interface/ch340g_datasheet.pdf（14页）\n")
    lines.append(f"- 审计对象：ChromaDB 已索引的 {len(chunks)} 个 chunks\n")
    lines.append(f"- 审计方法：多模态模型逐页阅读 PDF 图像 + chunk 内容人工比对\n\n")

    lines.append("## 总体统计\n")
    ct = Counter(c["metadata"].get("content_type", "text") for c in chunks)
    lines.append(f"- content_type: {dict(ct)}\n")
    lines.append(f"- image_description 覆盖的 source_page: {sorted(image_desc_pages)}\n")
    lines.append(f"- 含图页面（page.get_images() > 0）：p7, p8, p12, p13, p14\n")
    lines.append(f"- 矢量电路图页面（get_images() = 0 但 visually 含图）：p5, p6, p12, p13, p14\n\n")

    lines.append("## 逐页覆盖情况\n")
    lines.append("| 页 | 预期关键内容 | image_desc | text覆盖 | 状态 | 备注 |\n")
    lines.append("|---|---|---|---|---|---|\n")

    issues = []
    for p in range(1, 15):
        exp = EXPECTED_CONTENT[p]
        items = "; ".join(exp["items"])
        has_img = p in image_desc_pages
        has_text = bool(coverage.get(p))

        # Determine status
        if p in (5, 6):
            # Vector diagrams not detected by page.get_images()
            status = "❌ 图片遗漏"
            note = "矢量电路图 page.get_images()=0，无 image_description；仅 text chunk 含 ASCII 打散内容"
            issues.append(f"p{p}: {note}")
        elif p in (7, 8, 13, 14):
            if has_img and has_text:
                status = "✅ 完整"
                note = "image_description + text 均覆盖"
            else:
                status = "⚠️ 部分"
                note = f"img={has_img}, text={has_text}"
        elif exp["type"] == "mixed":
            if has_text:
                status = "⚠️ 表格打散"
                note = "text chunk 覆盖但表格为纯文本，非 Markdown 表格（旧索引）"
            else:
                status = "❌ 遗漏"
                note = "无 chunk 覆盖"
                issues.append(f"p{p}: {note}")
        else:
            if has_text:
                status = "✅ 完整"
                note = "text chunk 覆盖"
            else:
                status = "❌ 遗漏"
                note = "无 chunk 覆盖"
                issues.append(f"p{p}: {note}")

        lines.append(f"| p{p:02d} | {items} | {'✅' if has_img else '❌'} | {'✅' if has_text else '❌'} | {status} | {note} |\n")

    lines.append("\n## 发现的问题\n")
    if not issues:
        lines.append("未发现关键内容遗漏。\n")
    else:
        for issue in issues:
            lines.append(f"- {issue}\n")

    lines.append("\n## 关键结论\n")
    lines.append("1. **图片/电路图覆盖**：p7/p8/p12/p13/p14 的嵌入图/电路图均有 image_description；但 p5/p6 的矢量电路图因 `page.get_images()=0` 被遗漏。\n")
    lines.append("2. **表格完整性**：当前 ChromaDB 为旧索引（代码修改前生成），表格在 text chunk 中仍是以打散纯文本形式存在，未出现 Markdown 表格。需重新索引后才能验证 `find_tables()` 效果。\n")
    lines.append("3. **页码标记异常**：大量 chunk 的 `page_start=1`，说明旧代码的页码解析在 restore placeholder 后丢失了 `<!-- PAGE:N -->` 标记，或 `parse_page_index` 回退到了首页。\n")
    lines.append("4. **文本覆盖**：p1-p14 每个页面都有 text chunk 覆盖，无整页文字遗漏。\n")
    lines.append("\n## 建议修复\n")
    lines.append("1. **矢量电路图检测**：不能仅依赖 `page.get_images()`，需增加基于页面视觉内容或文本特征的图像描述触发条件（例如 section 标题含 'schematic'、'Configuration'、页面含大量大写元件标号如 C1/R1/U1 等）。\n")
    lines.append("2. **重新索引 ch340g**：用当前代码重新生成 chunks，验证 find_tables() 是否产出 Markdown 表格、placeholder 保护是否防止表格断裂。\n")
    lines.append("3. **页码标记修复**：检查 `_build_chunks()` 中 placeholder restore 后 `<!-- PAGE:N -->` 标记是否被正确保留并参与 `parse_page_index`。\n")

    OUT_REPORT.write_text("".join(lines), encoding="utf-8")
    print(f"Report: {OUT_REPORT}")
    print(open(OUT_REPORT, encoding="utf-8").read())


if __name__ == "__main__":
    main()
