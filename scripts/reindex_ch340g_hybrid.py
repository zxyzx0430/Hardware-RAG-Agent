"""Reindex ch340g with hybrid chunker (no vision, fast) to verify page markers."""
import asyncio
import sys
import uuid
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

PDF_PATH = BACKEND.parent / "data" / "pdfs" / "interface" / "ch340g_datasheet.pdf"
KB_ID = "kb-96eca485"


async def reindex():
    from app.db.database import init_db, SessionLocal
    from app.db.models import KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from src.rag.chunking import get_chunker
    from src.rag.document_processor import UnifiedPdfParser

    init_db()
    km = get_kb_manager()

    # Delete ALL ch340g docs first
    store = km._get_store(km.get_kb(KB_ID))
    with SessionLocal() as db:
        docs = db.query(KnowledgeDoc).filter(
            KnowledgeDoc.kb_id == KB_ID,
            KnowledgeDoc.title.like("%ch340g%"),
        ).all()
        for d in docs:
            deleted = store.delete_document(d.doc_id)
            print(f"Deleted {deleted} vectors for doc {d.doc_id[:8]}...")
            db.delete(d)
        db.commit()
    km._rebuild_bm25(KB_ID)

    chunker = get_chunker("hybrid", small_chunk_size=800)
    text_content, total_pages = UnifiedPdfParser().parse(PDF_PATH)
    print(f"Parsed: {len(text_content)} chars, {total_pages} pages")

    new_doc_id = str(uuid.uuid4())
    t0 = time.time()
    chunks = await chunker.chunk(
        text=text_content,
        metadata={
            "doc_id": new_doc_id,
            "title": PDF_PATH.name,
            "file_type": "pdf",
            "category": "user_upload",
        },
        file_path=PDF_PATH,
        total_pages=total_pages,
    )
    elapsed = time.time() - t0
    print(f"Generated {len(chunks)} chunks in {elapsed:.1f}s")

    ingested = km.ingest_chunks(KB_ID, chunks, new_doc_id)
    print(f"Ingested {ingested} chunks")

    with SessionLocal() as db:
        db.add(KnowledgeDoc(
            doc_id=new_doc_id,
            kb_id=KB_ID,
            title=PDF_PATH.name,
            category="user_upload",
            file_type="pdf",
            file_size=PDF_PATH.stat().st_size,
            chunk_count=len(chunks),
            chunk_method_used="hybrid",
            status="indexed",
        ))
        db.commit()

    from collections import Counter
    ps_dist = Counter(c.page_range[0] for c in chunks)
    print(f"page_start distribution: {dict(sorted(ps_dist.items()))}")


if __name__ == "__main__":
    asyncio.run(reindex())
