"""Reindex case 2 (STM32 GPIO, hybrid) with current code.

Run: python scripts/reindex_case2_stm32_hybrid.py
"""
import asyncio
import sys
import uuid
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

DOC_PATH = BACKEND.parent / "data" / "test_docs" / "01-stm32-gpio.md"
KB_ID = "builtin-001"
OLD_DOC_ID = "cecbacc8-8d3f-4ba6-a5af-61e56d3270c4"


async def reindex():
    from app.db.database import init_db, SessionLocal
    from app.db.models import KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from src.rag.chunking import get_chunker

    init_db()
    km = get_kb_manager()
    kb = km.get_kb(KB_ID)

    # 1. Delete old doc
    print(f"[1/4] Deleting old chunks for doc_id={OLD_DOC_ID}")
    store = km._get_store(kb)
    deleted = store.delete_document(OLD_DOC_ID)
    print(f"  Deleted {deleted} vectors from ChromaDB")
    with SessionLocal() as db:
        old = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == OLD_DOC_ID).first()
        if old:
            db.delete(old)
            db.commit()
            print(f"  Deleted DB record: {old.title}")
    km._rebuild_bm25(KB_ID)

    # 2. Read markdown
    text_content = DOC_PATH.read_text(encoding="utf-8")
    total_pages = 1  # markdown is single-page
    print(f"[2/4] Read {DOC_PATH.name}: {len(text_content)} chars")

    # 3. Chunk with hybrid
    chunker = get_chunker("hybrid", small_chunk_size=800)
    new_doc_id = str(uuid.uuid4())
    print(f"[3/4] Chunking with hybrid (doc_id={new_doc_id[:8]}...)")
    t0 = time.time()
    chunks = await chunker.chunk(
        text=text_content,
        metadata={
            "doc_id": new_doc_id,
            "title": DOC_PATH.name,
            "file_type": "md",
            "category": "user_upload",
        },
        file_path=DOC_PATH,
        total_pages=total_pages,
    )
    elapsed = time.time() - t0
    print(f"  Generated {len(chunks)} chunks in {elapsed:.1f}s")

    # 4. Ingest
    print(f"[4/4] Ingesting {len(chunks)} chunks")
    ingested = km.ingest_chunks(KB_ID, chunks, new_doc_id)
    print(f"  Ingested {ingested} chunks")

    with SessionLocal() as db:
        db.add(KnowledgeDoc(
            doc_id=new_doc_id,
            kb_id=KB_ID,
            title=DOC_PATH.name,
            category="user_upload",
            file_type="md",
            file_size=DOC_PATH.stat().st_size,
            chunk_count=len(chunks),
            chunk_method_used="hybrid",
            status="indexed",
        ))
        db.commit()
    print(f"\nDone: {len(chunks)} chunks ingested as {new_doc_id}")

    from collections import Counter
    ps_dist = Counter(c.page_range[0] for c in chunks)
    print(f"page_start distribution: {dict(sorted(ps_dist.items()))}")


if __name__ == "__main__":
    asyncio.run(reindex())
