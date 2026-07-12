"""
Build builtin knowledge base — reads PDFs from data/pdfs/, hybrid chunks them,
vectorizes with text-embedding-3-small, writes to backend/data/builtin_kb/
(ChromaDB collection + BM25 index).

Usage:
    python scripts/build_builtin_kb.py [--force]

--force: Clear existing builtin KB (ChromaDB collection + BM25 index + DB doc
        records) and rebuild from scratch.

Requirements:
    - PDFs placed in backend/data/pdfs/ (or data/pdfs/ at project root)
    - EMBEDDING_API_KEY env var set (or .env in backend/)
    - Dependencies installed: see backend/requirements.txt

This script is idempotent: running it multiple times without --force will
skip PDFs that already have a KnowledgeDoc record with status="indexed".
"""

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

# ─── Bootstrap: add backend/ to sys.path so we can import app.* and src.* ───
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Change cwd to backend/ so relative paths in config resolve correctly
os.chdir(_BACKEND_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build_builtin_kb")


# ─── Locate PDF source directory ───
def find_pdf_dir() -> Path:
    """Find PDF directory: prefer backend/data/pdfs/, fall back to data/pdfs/."""
    candidates = [
        _BACKEND_DIR / "data" / "pdfs",
        _SCRIPT_DIR.parent / "data" / "pdfs",
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    # Default to backend/data/pdfs/ (will be created if needed)
    return candidates[0]


# ─── Core build logic ───
async def build_builtin_kb(force: bool = False):
    """Build the builtin knowledge base from PDFs."""
    from app.db.database import init_db, SessionLocal
    from app.db.models import KnowledgeBase, KnowledgeDoc
    from src.rag.kb_manager import (
        get_kb_manager,
        BUILTIN_KB_DIR,
        BUILTIN_KB_ID,
        BUILTIN_KB_NAME,
        BM25_DIR,
        BM25Index,
    )
    from src.rag.chunking import get_chunker
    from src.rag.document_processor import DoclingParser

    # Ensure DB tables exist
    init_db()
    logger.info("Database initialized")

    pdf_dir = find_pdf_dir()
    if not pdf_dir.exists():
        logger.error(f"PDF directory not found: {pdf_dir}")
        logger.info("Please place PDF files in backend/data/pdfs/ before running this script.")
        sys.exit(1)

    pdf_files = sorted(list(pdf_dir.glob("*.pdf")))
    if not pdf_files:
        logger.error(f"No PDF files found in {pdf_dir}")
        sys.exit(1)

    logger.info(f"Found {len(pdf_files)} PDF files in {pdf_dir}")

    # Ensure builtin KB directory exists
    BUILTIN_KB_DIR.mkdir(parents=True, exist_ok=True)
    BM25_DIR.mkdir(parents=True, exist_ok=True)

    kb_manager = get_kb_manager()

    # ─── Handle --force: clear existing data ───
    if force:
        logger.info("--force specified, clearing existing builtin KB data...")
        _clear_builtin_kb(kb_manager)
        # Re-init DB (creates fresh tables if needed)
        init_db()

    # ─── Ensure builtin KB record exists in DB ───
    db = SessionLocal()
    try:
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.is_builtin == True).first()
        if not kb:
            kb = KnowledgeBase(
                id=BUILTIN_KB_ID,
                name=BUILTIN_KB_NAME,
                description="系统内置硬件手册知识库",
                collection_name="hardware-docs",
                chunk_method="hybrid",
                embedding_model="text-embedding-3-small",
                enabled=True,
                is_builtin=True,
                builtin_path=str(BUILTIN_KB_DIR),
            )
            db.add(kb)
            db.commit()
            db.refresh(kb)
            logger.info(f"Created builtin KB record: {BUILTIN_KB_ID}")
        else:
            logger.info(f"Builtin KB already exists: {kb.id} ({kb.name})")
    finally:
        db.close()

    # ─── Get hybrid chunker ───
    chunker = get_chunker("hybrid", chunk_size=1000, chunk_overlap=200)
    parser = DoclingParser()

    # ─── Process each PDF ───
    total_ingested = 0
    skipped = 0
    failed = 0

    for pdf_path in pdf_files:
        logger.info(f"Processing: {pdf_path.name}")

        # Check if already indexed (skip unless --force already cleared)
        doc_id = pdf_path.stem
        db = SessionLocal()
        try:
            existing = db.query(KnowledgeDoc).filter(
                KnowledgeDoc.doc_id == doc_id,
                KnowledgeDoc.kb_id == BUILTIN_KB_ID,
            ).first()
            if existing and existing.status == "indexed":
                logger.info(f"  Skipping (already indexed): {pdf_path.name}")
                skipped += 1
                continue

            # Create/update KnowledgeDoc record
            if existing:
                existing.status = "indexing"
                existing.error_message = None
            else:
                record = KnowledgeDoc(
                    doc_id=doc_id,
                    kb_id=BUILTIN_KB_ID,
                    title=pdf_path.name,
                    category="builtin",
                    file_type="pdf",
                    file_size=pdf_path.stat().st_size,
                    chunk_count=0,
                    chunk_method_used="hybrid",
                    status="indexing",
                )
                db.add(record)
            db.commit()
        finally:
            db.close()

        # Parse PDF
        try:
            text_content = parser.parse(pdf_path)
        except Exception as e:
            logger.error(f"  PDF parse failed: {pdf_path.name}: {e}")
            _update_doc_status(doc_id, "error", error_message=f"PDF parse failed: {e}")
            failed += 1
            continue

        if not text_content.strip():
            logger.warning(f"  Empty content: {pdf_path.name}")
            _update_doc_status(doc_id, "error", error_message="Parsed content is empty")
            failed += 1
            continue

        # Get page count via PyMuPDF
        total_pages = 0
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            total_pages = doc.page_count
            doc.close()
        except Exception:
            pass

        # Chunk
        try:
            metadata = {
                "doc_id": doc_id,
                "title": pdf_path.name,
                "file_type": "pdf",
                "category": "builtin",
            }
            chunks = await chunker.chunk(
                text=text_content,
                metadata=metadata,
                file_path=pdf_path,
                total_pages=total_pages,
            )
        except Exception as e:
            logger.error(f"  Chunking failed: {pdf_path.name}: {e}")
            _update_doc_status(doc_id, "error", error_message=f"Chunking failed: {e}")
            failed += 1
            continue

        if not chunks:
            logger.warning(f"  No chunks produced: {pdf_path.name}")
            _update_doc_status(doc_id, "error", error_message="No chunks produced")
            failed += 1
            continue

        logger.info(f"  Chunked: {len(chunks)} chunks ({total_pages} pages)")

        # Ingest into ChromaDB
        try:
            ingested = kb_manager.ingest_chunks(BUILTIN_KB_ID, chunks, doc_id)
        except Exception as e:
            logger.error(f"  Ingest failed: {pdf_path.name}: {e}")
            _update_doc_status(doc_id, "error", error_message=f"Ingest failed: {e}")
            failed += 1
            continue

        _update_doc_status(doc_id, "indexed", chunk_count=ingested)
        logger.info(f"  Ingested: {ingested} chunks")
        total_ingested += ingested

    # ─── Build & save BM25 index ───
    if total_ingested > 0:
        logger.info("Building BM25 index...")
        try:
            kb = kb_manager.get_kb(BUILTIN_KB_ID)
            if kb:
                store = kb_manager._get_store(kb)
                if store:
                    texts = store.get_all_texts()
                    if texts:
                        bm25 = BM25Index(texts)
                        bm25_path = BM25_DIR / f"{kb.collection_name}.pkl"
                        bm25.save(bm25_path)
                        logger.info(f"BM25 index saved: {bm25_path} ({len(texts)} docs)")
                    else:
                        logger.warning("No texts in ChromaDB, skipping BM25 index")
                else:
                    logger.warning("No vector store available, skipping BM25 index")
        except Exception as e:
            logger.error(f"BM25 index build failed: {e}")

    # ─── Summary ───
    logger.info("=" * 60)
    logger.info(f"Build complete: {total_ingested} chunks ingested, {skipped} skipped, {failed} failed")
    logger.info(f"Builtin KB path: {BUILTIN_KB_DIR}")
    logger.info(f"BM25 index path: {BM25_DIR}/hardware-docs.pkl")
    logger.info("=" * 60)


def _clear_builtin_kb(kb_manager):
    """Clear existing builtin KB data (ChromaDB + BM25 + DB records)."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeBase, KnowledgeDoc
    from src.rag.kb_manager import BUILTIN_KB_DIR, BM25_DIR

    # Delete ChromaDB collection
    db = SessionLocal()
    try:
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.is_builtin == True).first()
        if kb:
            try:
                store = kb_manager._get_store(kb)
                if store:
                    store.delete_collection()
                    logger.info("Deleted ChromaDB collection")
            except Exception as e:
                logger.warning(f"Failed to delete ChromaDB collection: {e}")

            # Delete BM25 index file
            bm25_path = BM25_DIR / f"{kb.collection_name}.pkl"
            if bm25_path.exists():
                bm25_path.unlink()
                logger.info(f"Deleted BM25 index: {bm25_path}")

            # Delete KnowledgeDoc records
            deleted_docs = db.query(KnowledgeDoc).filter(
                KnowledgeDoc.kb_id == kb.id
            ).delete()
            logger.info(f"Deleted {deleted_docs} KnowledgeDoc records")

            # Delete KB record itself (will be recreated)
            db.delete(kb)
            db.commit()
            logger.info("Deleted builtin KB record")
    finally:
        db.close()

    # Clear cache
    kb_manager._stores.pop(BUILTIN_KB_ID, None)
    kb_manager._bm25_indices.pop(BUILTIN_KB_ID, None)
    kb_manager._bm25_stale.discard(BUILTIN_KB_ID)


def _update_doc_status(doc_id: str, status: str, chunk_count: int = None, error_message: str = None):
    """Update KnowledgeDoc status in DB."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeDoc

    try:
        db = SessionLocal()
        try:
            record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
            if not record:
                return
            record.status = status
            if chunk_count is not None:
                record.chunk_count = chunk_count
            if error_message is not None:
                record.error_message = error_message
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception(f"Failed to update doc status: {doc_id}")


def main():
    parser = argparse.ArgumentParser(
        description="Build builtin knowledge base from PDFs in data/pdfs/"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear existing builtin KB and rebuild from scratch",
    )
    args = parser.parse_args()

    asyncio.run(build_builtin_kb(force=args.force))


if __name__ == "__main__":
    main()
