"""Reindex case 3 (06-chaotic-embedded-notes, agent) with current code.

Run: python scripts/reindex_case3_chaos_agent.py
"""
import asyncio
import sys
import uuid
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

DOC_PATH = BACKEND.parent / "data" / "test_docs" / "06-chaotic-embedded-notes.md"
KB_ID = "kb-567e2118"
OLD_DOC_ID = "e8c1d726-c4ec-4979-b8b7-e91e5bc48c89"


async def reindex():
    from app.db.database import init_db, SessionLocal
    from app.db.models import KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from src.rag.chunking import get_chunker
    from app.api.auth import decrypt_key

    init_db()
    km = get_kb_manager()
    kb = km.get_kb(KB_ID)

    # 1. Delete old doc
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

    # 2. Decrypt API key for agent chunker
    agent_key = ""
    if kb.agent_chunker_api_key_encrypted:
        try:
            agent_key = decrypt_key(kb.agent_chunker_api_key_encrypted)
        except Exception as e:
            print(f"  Failed to decrypt agent key: {e}")
    print(f"[2/5] API key: {'ready' if agent_key else 'EMPTY'}, model={kb.agent_chunker_model}")

    # 3. Read markdown
    text_content = DOC_PATH.read_text(encoding="utf-8")
    total_pages = 1
    print(f"[3/5] Read {DOC_PATH.name}: {len(text_content)} chars")

    # 4. Chunk with agent
    chunker = get_chunker(
        "agent",
        model=kb.agent_chunker_model or "gpt-4o",
        base_url=kb.agent_chunker_base_url or "https://api.openai.com/v1",
        api_key=agent_key,
        small_chunk_size=kb.small_chunk_size or 800,
    )
    new_doc_id = str(uuid.uuid4())
    print(f"[4/5] Chunking with agent (doc_id={new_doc_id[:8]}...)")
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

    # 5. Ingest
    print(f"[5/5] Ingesting {len(chunks)} chunks")
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
            chunk_method_used="agent",
            status="indexed",
        ))
        db.commit()
    print(f"\nDone: {len(chunks)} chunks ingested as {new_doc_id}")

    from collections import Counter
    ps_dist = Counter(c.page_range[0] for c in chunks)
    print(f"page_start distribution: {dict(sorted(ps_dist.items()))}")


if __name__ == "__main__":
    asyncio.run(reindex())
