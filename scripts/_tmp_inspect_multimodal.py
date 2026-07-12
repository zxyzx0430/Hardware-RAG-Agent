"""Inspect multimodal chunks for specific pages and render PDF pages for visual comparison."""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from src.rag.kb_manager import get_kb_manager  # noqa: E402


def dump_page_chunks(kb_id: str, doc_id: str, page: int, out_path: Path) -> None:
    kbm = get_kb_manager()
    chunks = kbm.get_doc_chunks(kb_id, doc_id)
    matched = [
        c for c in chunks
        if c.get("metadata", {}).get("page_start") == page or c.get("metadata", {}).get("page_end") == page
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {doc_id} page {page} chunks ({len(matched)} matched)\n\n")
        for i, c in enumerate(matched, 1):
            meta = c.get("metadata", {})
            ctype = meta.get("content_type", "text")
            f.write(f"## chunk {i} page={meta.get('page_start')}-{meta.get('page_end')} type={ctype}\n\n")
            f.write(c.get("content", ""))
            f.write("\n\n---\n\n")
    print(f"Dumped {len(matched)} chunks to {out_path}")


def render_pdf_page(pdf_path: Path, page: int, out_path: Path, zoom: float = 2.0) -> None:
    doc = fitz.open(pdf_path)
    if page < 1 or page > len(doc):
        raise ValueError(f"Page {page} out of range (1-{len(doc)})")
    p = doc[page - 1]
    mat = fitz.Matrix(zoom, zoom)
    pix = p.get_pixmap(matrix=mat)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out_path)
    doc.close()
    print(f"Rendered page {page} to {out_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--out-dir", default="data/benchmark/inspect")
    args = parser.parse_args()

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    base = Path(args.pdf).stem
    dump_path = out_dir / f"{base}_p{args.page}_chunks.md"
    img_path = out_dir / f"{base}_p{args.page}.png"

    dump_page_chunks("builtin-001", args.doc, args.page, dump_path)
    render_pdf_page(ROOT / args.pdf, args.page, img_path)
