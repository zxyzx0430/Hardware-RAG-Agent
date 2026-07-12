"""Dump chunks covering a question's source pages and compare with PDF text.

Usage:
    python scripts/analyze_question_chunks.py [sample_id ...]
    python scripts/analyze_question_chunks.py esp32-q004 esp32-q008 stm32f4-q008
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

import yaml
import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from src.rag.kb_manager import get_kb_manager  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import KnowledgeDoc  # noqa: E402

DATASET = ROOT / "data" / "benchmark" / "chunk-baseline-golden-v1.yaml"
PDF_ROOT = ROOT


def page_range(chunk: dict) -> tuple[int, int]:
    m = chunk.get("metadata", {})
    start = m.get("page_start", chunk.get("page_start", 1))
    end = m.get("page_end", chunk.get("page_end", start))
    return int(start or 1), int(end or start)


def overlaps(pages: set[int], start: int, end: int) -> bool:
    return bool(pages & set(range(start, end + 1)))


def pdf_page_text(pdf_path: Path, pages: set[int]) -> dict[int, str]:
    doc = fitz.open(pdf_path)
    out = {}
    for p in sorted(pages):
        if 1 <= p <= len(doc):
            out[p] = doc[p - 1].get_text()
    doc.close()
    return out


def main(ids: list[str]) -> None:
    with open(DATASET, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    samples = {s["id"]: s for s in data["samples"]}

    kbm = get_kb_manager()
    kb_id = data["metadata"].get("kb_id", "builtin-001")

    # doc_id -> title map from metadata
    doc_meta = {d["source_pdf"]: d["doc_id"] for d in data["metadata"]["pdfs"]}

    for sid in ids:
        s = samples.get(sid)
        if not s:
            print(f"[{sid}] not found in dataset")
            continue

        pdf_rel = s["source_pdf"]
        doc_id = doc_meta.get(pdf_rel, Path(pdf_rel).stem)
        # Resolve doc_id against DB; metadata may contain old IDs while reindex uses -v2 suffix.
        db = SessionLocal()
        try:
            docs = db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).all()
            known_ids = {d.doc_id for d in docs}
            if doc_id not in known_ids:
                for d in docs:
                    if d.title and (Path(d.title).name == Path(pdf_rel).name or d.doc_id.startswith(Path(pdf_rel).stem)):
                        doc_id = d.doc_id
                        break
        finally:
            db.close()

        pages = set(s["source_pages"])
        print("=" * 80)
        print(f"[{sid}] {s['query']}")
        print(f"  type={s['question_type']} pages={sorted(pages)}")
        print(f"  expected={s['expected_answer'][:160]}...")
        print(f"  pdf={pdf_rel} doc_id={doc_id}\n")

        chunks = kbm.get_doc_chunks(kb_id, doc_id)
        matched = []
        for c in chunks:
            start, end = page_range(c)
            if overlaps(pages, start, end):
                matched.append((start, end, c))

        print(f"  Matched chunks: {len(matched)} / {len(chunks)} total\n")
        for i, (start, end, c) in enumerate(matched, 1):
            text = c.get("content", "") or c.get("text", "")
            label = "image_desc" if "[图片描述" in text else "text"
            preview = text[:500].replace("\n", " ")
            print(f"  --- chunk {i} pages={start}-{end} type={label} ---")
            print(f"  {preview}{'...' if len(text) > 500 else ''}\n")

        # PDF native text for source pages
        pdf_texts = pdf_page_text(PDF_ROOT / pdf_rel, pages)
        print("  PDF native text (first 500 chars per page):")
        for p in sorted(pages):
            txt = pdf_texts.get(p, "")
            print(f"  [page {p}] {txt[:500].replace(chr(10), ' ')}{'...' if len(txt) > 500 else ''}\n")

        # Simple fact presence: count expected keywords found in matched chunks
        expected = s["expected_answer"]
        kws = set(re.findall(r"\b\d+[\s\u00A0]?(?:KB|kB|MB|µA|mA|V|MHz|GPIO)\b", expected, re.IGNORECASE))
        kws.update(re.findall(r"\b[A-Z]{2,}\d+[A-Z]?\b", expected))
        kws.update(re.findall(r"GPIO\d+|MTDI|MTDO|MTCK|MTMS|VDD[^\s]*", expected))
        all_chunk_text = "\n".join(c.get("content", "") or c.get("text", "") for _, _, c in matched)
        found = [k for k in kws if k.lower() in all_chunk_text.lower()]
        print(f"  Expected keyword presence in matched chunks: {len(found)}/{len(kws)}")
        print(f"  found={sorted(found)}")
        print(f"  missing={sorted(kws - set(found))}\n")


if __name__ == "__main__":
    ids = sys.argv[1:] or [
        "esp32-q004", "esp32-q007", "esp32-q008", "esp32-q009",
        "stm32f4-q008",
    ]
    main(ids)
