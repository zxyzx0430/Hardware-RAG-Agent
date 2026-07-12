"""Reindex ch340g with multimodal chunker (delete old + re-chunk + ingest).

Run: python scripts/reindex_ch340g.py
"""
import asyncio
import sys
import uuid
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

PDF_PATH = BACKEND.parent / "data" / "pdfs" / "interface" / "ch340g_datasheet.pdf"
KB_ID = "kb-96eca485"
OLD_DOC_ID = "048f704c-3964-409d-9afe-38707c057cd1"


async def reindex():
    from app.db.database import init_db, SessionLocal
    from app.db.models import KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from src.rag.chunking import get_chunker
    from app.api.auth import decrypt_key
    from src.rag.document_processor import UnifiedPdfParser

    init_db()
    km = get_kb_manager()
    kb = km.get_kb(KB_ID)

    # 1. Delete old doc chunks
    print(f"[1/5] Deleting old chunks for doc_id={OLD_DOC_ID}")
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
    print("  BM25 rebuilt")

    # 2. Decrypt API key
    agent_key = ""
    if kb.agent_chunker_api_key_encrypted:
        try:
            agent_key = decrypt_key(kb.agent_chunker_api_key_encrypted)
        except Exception as e:
            print(f"  Failed to decrypt agent key: {e}")
    if not agent_key:
        print("ERROR: No agent_chunker_api_key configured for KB")
        return
    print(f"[2/5] API key decrypted, model={kb.agent_chunker_model}")

    # 3. Build chunker
    chunker = get_chunker(
        "multimodal",
        model=kb.agent_chunker_model or "gpt-4o",
        base_url=kb.agent_chunker_base_url or "https://api.openai.com/v1",
        api_key=agent_key,
        small_chunk_size=kb.small_chunk_size or 800,
    )

    # 4. Parse PDF + chunk
    print(f"[3/5] Parsing PDF: {PDF_PATH.name}")
    text_content, total_pages = UnifiedPdfParser().parse(PDF_PATH)
    print(f"  Parsed: {len(text_content)} chars, {total_pages} pages")

    new_doc_id = str(uuid.uuid4())
    print(f"[4/5] Chunking with multimodal (doc_id={new_doc_id[:8]}...)")
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
    print(f"  Generated {len(chunks)} chunks in {elapsed:.1f}s")

    # 5. Ingest
    print(f"[5/5] Ingesting {len(chunks)} chunks")
    ingested = km.ingest_chunks(KB_ID, chunks, new_doc_id)
    print(f"  Ingested {ingested} chunks")

    # 6. Write DB record
    with SessionLocal() as db:
        db.add(KnowledgeDoc(
            doc_id=new_doc_id,
            kb_id=KB_ID,
            title=PDF_PATH.name,
            category="user_upload",
            file_type="pdf",
            file_size=PDF_PATH.stat().st_size,
            chunk_count=len(chunks),
            chunk_method_used="multimodal",
            status="indexed",
        ))
        db.commit()
    print(f"\nDone: {len(chunks)} chunks ingested as {new_doc_id}")

    # Summary
    from collections import Counter
    ct = Counter(c.metadata.get("content_type", "text") for c in chunks)
    print(f"Content types: {dict(ct)}")
    img_pages = sorted(set(
        c.metadata.get("source_page") for c in chunks
        if c.metadata.get("content_type") == "image_description"
    ))
    print(f"Image description source_pages: {img_pages}")


if __name__ == "__main__":
    asyncio.run(reindex())
