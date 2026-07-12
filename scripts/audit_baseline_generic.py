"""Generic baseline chunk audit for any PDF/doc_id pair.

Run:
    python scripts/audit_baseline_generic.py --pdf data/pdfs/mcu/esp32_datasheet.pdf --doc-id baseline-esp32-datasheet-v2 --out-prefix esp32
    python scripts/audit_baseline_generic.py --pdf data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf --title-contains stm32f4 --out-prefix stm32f4
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz

BACKEND = Path(__file__).resolve().parent.parent / "backend"
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

KB_ID = "builtin-001"
PAGE_MARKER_RE = re.compile(r"<!-- PAGE:(\d+) -->")


def get_doc_chunks(km, doc_id: str):
    """Fetch all chunks for a doc from ChromaDB via kb_manager."""
    return km.get_doc_chunks(KB_ID, doc_id)


def render_pages(pdf_path: Path, dpi: int = 180):
    """Render PDF pages metadata (text/tables/images) without saving PNGs."""
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pages = []
    for i in range(doc.page_count):
        page = doc[i]
        page_num = i + 1

        text = page.get_text("text") or ""
        tables_md = []
        try:
            tabs = page.find_tables()
            for tab in tabs:
                try:
                    md = tab.to_markdown()
                except Exception:
                    continue
                if md and md.strip():
                    tables_md.append(md.strip())
        except Exception:
            pass

        try:
            img_count = len(page.get_images())
        except Exception:
            img_count = 0

        pages.append({
            "page": page_num,
            "text": text,
            "tables": tables_md,
            "table_count": len(tables_md),
            "embedded_images": img_count,
            "chars": len(text),
        })
    doc.close()
    return pages


def covers_page(meta: dict, page: int) -> bool:
    """Return True if chunk metadata covers the given page."""
    ps = meta.get("page_start")
    pe = meta.get("page_end")
    if ps is not None and pe is not None:
        try:
            if int(ps) <= page <= int(pe):
                return True
        except (ValueError, TypeError):
            pass
    sp = meta.get("source_page")
    if sp is not None:
        try:
            if int(sp) == page:
                return True
        except (ValueError, TypeError):
            pass
    return False


def audit_page(page: dict, chunks: list[dict]) -> dict:
    """Audit coverage for a single PDF page."""
    page_num = page["page"]
    text_chunks = [c for c in chunks if c["metadata"].get("content_type") != "image_description" and covers_page(c["metadata"], page_num)]
    img_chunks = [c for c in chunks if c["metadata"].get("content_type") == "image_description" and covers_page(c["metadata"], page_num)]

    issues = []
    if not text_chunks and not img_chunks:
        issues.append("no chunk covers this page")

    table_covered = False
    if page["tables"]:
        table_rows = set()
        for t in page["tables"]:
            for line in t.splitlines():
                line = line.strip()
                if line.startswith("|"):
                    table_rows.add(re.sub(r"\s+", " ", line))
        covered_rows = 0
        for c in text_chunks:
            doc = c.get("document") or c.get("content", "")
            for row in table_rows:
                if row in doc or row.replace(" ", "") in doc.replace(" ", ""):
                    covered_rows += 1
        table_covered = covered_rows >= max(1, len(table_rows) * 0.5)
        if not table_covered:
            issues.append(f"tables not fully covered ({covered_rows}/{len(table_rows)} rows found in chunks)")

    image_covered = True
    if page["embedded_images"] > 0:
        image_covered = len(img_chunks) > 0
        if not image_covered:
            issues.append("page contains embedded images but no image_description chunk")

    return {
        "page": page_num,
        "text_chunks": len(text_chunks),
        "image_description_chunks": len(img_chunks),
        "tables": page["table_count"],
        "embedded_images": page["embedded_images"],
        "chars": page["chars"],
        "table_covered": table_covered,
        "image_covered": image_covered,
        "issues": issues,
    }


def compute_duplication_rate(chunks: list[dict]) -> float:
    """Compute duplication rate based on fingerprint metadata."""
    fps = [c["metadata"].get("fingerprint", "") for c in chunks]
    total = len(fps)
    if total <= 1:
        return 0.0
    unique = len(set(fps))
    return (total - unique) / total * 100.0


def generate_markdown_report(summary: dict, pages: list[dict], chunks: list[dict], out_path: Path, title: str):
    """Write per-page audit markdown report."""
    lines = []
    lines.append(f"# {title} Baseline Chunk Audit Report")
    lines.append("")
    lines.append(f"> 生成时间：2026-06-30")
    lines.append(f"> 文档：doc_id=`{summary['doc_id']}`，KB=`{summary['kb_id']}`")
    lines.append("")
    lines.append("## 1. 执行摘要")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 总 chunks | {summary['total_chunks']} |")
    lines.append(f"| text chunks | {summary['text_chunks']} |")
    lines.append(f"| image_description chunks | {summary['image_description_chunks']} |")
    lines.append(f"| 页面覆盖率 | {summary['page_coverage_rate']:.1f}% |")
    lines.append(f"| 表格覆盖率 | {summary['table_coverage_rate']:.1f}% |")
    lines.append(f"| 图片覆盖率 | {summary['image_coverage_rate']:.1f}% |")
    lines.append(f"| 重复率（fingerprint） | {summary['duplication_rate']:.2f}% |")
    lines.append("")

    all_issues = [(r["page"], r["issues"]) for r in summary["pages"] if r["issues"]]
    if all_issues:
        lines.append("### 发现问题")
        lines.append("")
        for p, issues in all_issues:
            lines.append(f"- **p{p}**: {', '.join(issues)}")
        lines.append("")
    else:
        lines.append("### 发现问题")
        lines.append("")
        lines.append("无自动检测问题。")
        lines.append("")

    lines.append("## 2. 逐页 Audit")
    lines.append("")
    lines.append("| 页码 | text chunks | image_description | 表格数 | 嵌入式图 | 表格覆盖 | 图片覆盖 | 问题 |")
    lines.append("|------|-------------|-------------------|--------|----------|----------|----------|------|")
    for r in summary["pages"]:
        issues = ", ".join(r["issues"]) if r["issues"] else "—"
        lines.append(
            f"| p{r['page']} | {r['text_chunks']} | {r['image_description_chunks']} | "
            f"{r['tables']} | {r['embedded_images']} | "
            f"{'✓' if r['table_covered'] else '✗'} | "
            f"{'✓' if r['image_covered'] else '✗'} | {issues} |"
        )
    lines.append("")

    lines.append("## 3. Chunk 类型分布")
    lines.append("")
    ct = Counter(c["metadata"].get("content_type", "text") for c in chunks)
    for ctype, count in sorted(ct.items()):
        lines.append(f"- {ctype}: {count}")
    lines.append("")

    lines.append("## 4. 结论")
    lines.append("")
    pass_audit = summary["page_coverage_rate"] >= 99.0 and summary["duplication_rate"] < 5.0
    lines.append(f"- **整体结论**: {'通过' if pass_audit else '未通过'}")
    lines.append(f"- **页面覆盖**: {summary['page_coverage_rate']:.1f}%")
    lines.append(f"- **关键表格覆盖**: {summary['table_coverage_rate']:.1f}%")
    lines.append(f"- **关键图片覆盖**: {summary['image_coverage_rate']:.1f}%")
    lines.append(f"- **重复率**: {summary['duplication_rate']:.2f}%")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def resolve_doc(db, args) -> tuple[str, str]:
    """Resolve doc_id and title from CLI args or DB query."""
    from app.db.models import KnowledgeDoc

    if args.doc_id:
        doc = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == args.doc_id).first()
        if not doc:
            raise ValueError(f"doc_id {args.doc_id} not found")
        return doc.doc_id, doc.title

    if args.title_contains:
        doc = db.query(KnowledgeDoc).filter(
            KnowledgeDoc.kb_id == KB_ID,
            KnowledgeDoc.title.ilike(f"%{args.title_contains}%")
        ).order_by(KnowledgeDoc.created_at.desc()).first()
        if not doc:
            raise ValueError(f"No doc matching title '{args.title_contains}' found")
        return doc.doc_id, doc.title

    raise ValueError("Provide --doc-id or --title-contains")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--doc-id", default="", help="Exact doc_id to audit")
    parser.add_argument("--title-contains", default="", help="Match latest doc by title substring")
    parser.add_argument("--out-prefix", required=True, help="Output file prefix (e.g. esp32)")
    parser.add_argument("--kb-id", default=KB_ID, help="Knowledge base ID")
    args = parser.parse_args()

    from app.db.database import init_db, SessionLocal
    from src.rag.kb_manager import get_kb_manager

    init_db()
    km = get_kb_manager()

    with SessionLocal() as db:
        doc_id, title = resolve_doc(db, args)

    print(f"Auditing doc_id={doc_id}, title={title}")
    chunks = get_doc_chunks(km, doc_id)
    print(f"Loaded {len(chunks)} chunks from ChromaDB")

    pdf_path = ROOT / args.pdf
    pages = render_pages(pdf_path, dpi=180)
    print(f"Rendered {len(pages)} pages")

    page_results = [audit_page(p, chunks) for p in pages]

    covered_pages = [r["page"] for r in page_results if r["text_chunks"] > 0 or r["image_description_chunks"] > 0]
    pages_with_tables = [r["page"] for r in page_results if r["tables"] > 0]
    table_covered_pages = [r["page"] for r in page_results if r["table_covered"]]
    image_relevant_pages = [r["page"] for r in page_results if r["embedded_images"] > 0]
    image_covered_pages = [r["page"] for r in page_results if r["embedded_images"] > 0 and r["image_covered"]]

    page_coverage_rate = len(covered_pages) / len(pages) * 100.0 if pages else 0.0
    table_coverage_rate = len(table_covered_pages) / len(pages_with_tables) * 100.0 if pages_with_tables else 100.0
    image_coverage_rate = len(image_covered_pages) / len(image_relevant_pages) * 100.0 if image_relevant_pages else 100.0
    dup_rate = compute_duplication_rate(chunks)

    summary = {
        "doc_id": doc_id,
        "kb_id": args.kb_id,
        "total_chunks": len(chunks),
        "text_chunks": len([c for c in chunks if c["metadata"].get("content_type") != "image_description"]),
        "image_description_chunks": len([c for c in chunks if c["metadata"].get("content_type") == "image_description"]),
        "page_coverage_rate": page_coverage_rate,
        "table_coverage_rate": table_coverage_rate,
        "image_coverage_rate": image_coverage_rate,
        "duplication_rate": dup_rate,
        "pages": page_results,
    }

    out_dir = ROOT / "data" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.out_prefix}_audit_summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Summary saved: {out_path}")

    report_path = ROOT / "docs" / "reports" / f"audit_baseline_{args.out_prefix}_20260630.md"
    generate_markdown_report(summary, pages, chunks, report_path, args.out_prefix.upper())
    print(f"Report saved: {report_path}")

    print("\n=== Audit Summary ===")
    print(f"doc_id: {doc_id}")
    print(f"total_chunks: {summary['total_chunks']}")
    print(f"text_chunks: {summary['text_chunks']}")
    print(f"image_description_chunks: {summary['image_description_chunks']}")
    print(f"page_coverage_rate: {page_coverage_rate:.1f}%")
    print(f"table_coverage_rate: {table_coverage_rate:.1f}%")
    print(f"image_coverage_rate: {image_coverage_rate:.1f}%")
    print(f"duplication_rate: {dup_rate:.2f}%")
    all_issues = [(r["page"], r["issues"]) for r in page_results if r["issues"]]
    if all_issues:
        print("\nIssues:")
        for p, issues in all_issues:
            print(f"  p{p}: {', '.join(issues)}")
    else:
        print("\nNo issues found.")
    print("====================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
