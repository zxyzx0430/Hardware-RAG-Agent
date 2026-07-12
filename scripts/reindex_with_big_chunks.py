"""Reindex existing KBs to populate big_chunks table + big_chunk_id metadata.

For each KB (or a single --kb-id), re-reads the original uploaded file,
re-chunks with the KB's configured chunk_method, and re-ingests so that:
  - big_chunks table is populated (section-level parent text)
  - small chunks in ChromaDB carry big_chunk_id metadata

Usage:
    python scripts/reindex_with_big_chunks.py               # all KBs
    python scripts/reindex_with_big_chunks.py --kb-id kb-xxxx
    python scripts/reindex_with_big_chunks.py --dry-run
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Optional

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

UPLOAD_DIR = BACKEND.parent / "data" / "uploads"

_STATUS_OK = "ok"
_STATUS_SKIP = "skipped"
_STATUS_FAIL = "failed"
_STATUS_DRY = "dry_run"
_FALLBACK_METHOD = "hybrid"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Reindex KBs to populate big_chunks + big_chunk_id metadata",
    )
    parser.add_argument("--kb-id", default=None, help="Specific KB ID to reindex")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    return parser.parse_args()


def list_target_kbs(db, kb_id: Optional[str]) -> list:
    """List KBs to process (all or single)."""
    from app.db.models import KnowledgeBase
    q = db.query(KnowledgeBase)
    if kb_id:
        q = q.filter(KnowledgeBase.id == kb_id)
    return q.all()


def list_kb_docs(db, kb_id: str) -> list:
    """List KnowledgeDoc records for a KB."""
    from app.db.models import KnowledgeDoc
    return db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).all()


def find_doc_file(doc_id: str, file_type: str) -> Optional[Path]:
    """Find original uploaded file for a doc."""
    if not file_type:
        return None
    path = UPLOAD_DIR / f"{doc_id}.{file_type}"
    return path if path.exists() else None


def parse_file_content(file_path: Path, file_type: str) -> tuple:
    """Parse file -> (text, total_pages)."""
    from app.api.kb_routes import _parse_file
    ext = f".{file_type}"
    content_bytes = file_path.read_bytes()
    return _parse_file(ext, content_bytes, file_path)


def build_chunker_for_kb(kb):
    """Build a chunker configured for the KB's chunk_method."""
    from app.api.kb_routes import _get_kb_chunker
    return _get_kb_chunker(kb)


def build_chunk_metadata(doc, file_path: Path) -> dict:
    """Build base metadata for chunking."""
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "file_type": doc.file_type or "",
        "category": doc.category or "user_upload",
    }


def delete_old_vectors(km, kb, doc_id: str) -> int:
    """Delete old small chunks from ChromaDB for a doc."""
    store = km._get_store(kb)
    if not store:
        return 0
    return store.delete_document(doc_id)


def delete_old_big_chunks(db, doc_id: str) -> int:
    """Delete old big_chunks rows for a doc (clean slate)."""
    from app.db.models import BigChunk
    deleted = db.query(BigChunk).filter(BigChunk.doc_id == doc_id).delete(
        synchronize_session=False
    )
    db.commit()
    return deleted


def update_doc_record(db, doc, chunk_count: int) -> None:
    """Update KnowledgeDoc record after reindex."""
    doc.chunk_count = chunk_count
    doc.status = "indexed"
    doc.error_message = None
    db.commit()


async def chunk_with_fallback(kb, chunker, text, doc, file_path, total_pages) -> list:
    """Chunk text, fallback to hybrid on failure."""
    metadata = build_chunk_metadata(doc, file_path)
    try:
        return await chunker.chunk(
            text=text, metadata=metadata, file_path=file_path, total_pages=total_pages
        )
    except Exception as e:
        print(f"    Chunking failed ({e}), falling back to {_FALLBACK_METHOD}")
        from app.api.kb_routes import _get_kb_chunker
        fallback = _get_kb_chunker(kb, chunk_method_override=_FALLBACK_METHOD)
        return await fallback.chunk(
            text=text, metadata=metadata, file_path=file_path, total_pages=total_pages
        )


def _dry_run_info(doc, file_path: Path) -> dict:
    """Build dry-run info dict."""
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "status": _STATUS_DRY,
        "file": str(file_path),
    }


async def _do_reindex(km, kb, doc, file_path: Path) -> dict:
    """Execute reindex for a single doc."""
    from app.db.database import SessionLocal
    t0 = time.time()
    deleted = delete_old_vectors(km, kb, doc.doc_id)
    with SessionLocal() as db:
        delete_old_big_chunks(db, doc.doc_id)
    text_content, total_pages = parse_file_content(file_path, doc.file_type)
    if not text_content.strip():
        return {"doc_id": doc.doc_id, "title": doc.title, "status": _STATUS_FAIL, "reason": "empty content"}
    chunker = build_chunker_for_kb(kb)
    chunks = await chunk_with_fallback(kb, chunker, text_content, doc, file_path, total_pages)
    ingested = km.ingest_chunks(kb.id, chunks, doc.doc_id)
    with SessionLocal() as db:
        from app.db.models import KnowledgeDoc
        fresh = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc.doc_id).first()
        if fresh:
            update_doc_record(db, fresh, len(chunks))
    elapsed = time.time() - t0
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "status": _STATUS_OK,
        "deleted_old_vectors": deleted,
        "chunks": len(chunks),
        "ingested": ingested,
        "elapsed_s": round(elapsed, 1),
    }


async def reindex_one_doc(km, kb, doc, dry_run: bool) -> dict:
    """Reindex a single document. Returns stats dict."""
    file_path = find_doc_file(doc.doc_id, doc.file_type)
    if not file_path:
        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "status": _STATUS_SKIP,
            "reason": "file not found",
        }
    if dry_run:
        return _dry_run_info(doc, file_path)
    try:
        return await _do_reindex(km, kb, doc, file_path)
    except Exception as e:
        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "status": _STATUS_FAIL,
            "reason": str(e),
        }


async def reindex_one_kb(km, kb, dry_run: bool) -> list:
    """Reindex all docs in a KB. Returns list of doc stats."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeDoc
    print(f"\n{'='*60}")
    print(f"KB: {kb.id} ({kb.name}) chunk_method={kb.chunk_method}")
    print(f"{'='*60}")
    with SessionLocal() as db:
        docs = list_kb_docs(db, kb.id)
        doc_snapshots = [(d.doc_id, d.title, d.file_type, d.category) for d in docs]
    if not docs:
        print("  No documents found, skipping.")
        return []
    print(f"  Found {len(docs)} document(s)")
    results = []
    for snap in doc_snapshots:
        with SessionLocal() as db:
            doc = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == snap[0]).first()
            if not doc:
                continue
            print(f"\n  -> Doc: {doc.title} (doc_id={doc.doc_id[:8]}...)")
            result = await reindex_one_doc(km, kb, doc, dry_run)
            _print_doc_result(result)
            results.append(result)
    return results


def _print_doc_result(result: dict) -> None:
    """Print result for a single doc."""
    status = result["status"]
    if status == _STATUS_OK:
        print(f"     OK: {result['chunks']} chunks, {result['ingested']} ingested "
              f"(deleted {result['deleted_old_vectors']} old) in {result['elapsed_s']}s")
    elif status == _STATUS_DRY:
        print(f"     DRY-RUN: would reindex {result['file']}")
    elif status == _STATUS_SKIP:
        print(f"     SKIP: {result['reason']}")
    else:
        print(f"     FAIL: {result.get('reason', 'unknown')}")


def _print_summary(all_results: list) -> None:
    """Print final summary."""
    ok = sum(1 for r in all_results if r["status"] == _STATUS_OK)
    skip = sum(1 for r in all_results if r["status"] == _STATUS_SKIP)
    fail = sum(1 for r in all_results if r["status"] == _STATUS_FAIL)
    dry = sum(1 for r in all_results if r["status"] == _STATUS_DRY)
    total_chunks = sum(r.get("ingested", 0) for r in all_results)
    print(f"\n{'='*60}")
    print(f"SUMMARY: {ok} ok / {skip} skipped / {fail} failed / {dry} dry-run")
    print(f"  Total chunks ingested: {total_chunks}")
    print(f"{'='*60}")


async def main() -> None:
    """Entry point: parse args, init DB, reindex KBs."""
    args = parse_args()
    from app.db.database import init_db, SessionLocal
    from src.rag.kb_manager import get_kb_manager

    init_db()
    km = get_kb_manager()
    with SessionLocal() as db:
        kbs = list_target_kbs(db, args.kb_id)
        kb_ids = [(kb.id, kb.name) for kb in kbs]
    if not kb_ids:
        print("No KBs found.")
        return
    print(f"Reindexing {len(kb_ids)} KB(s)" + (" (dry-run)" if args.dry_run else ""))
    all_results = []
    for kb_id, _ in kb_ids:
        kb = km.get_kb(kb_id)
        if not kb:
            print(f"KB {kb_id} not found, skipping.")
            continue
        results = await reindex_one_kb(km, kb, args.dry_run)
        all_results.extend(results)
    _print_summary(all_results)


if __name__ == "__main__":
    asyncio.run(main())
