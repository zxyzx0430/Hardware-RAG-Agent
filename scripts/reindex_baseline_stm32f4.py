"""Reindex STM32F4 GPIO/EXTI extract into builtin-001 using MultimodalChunker.

Run:
    python scripts/reindex_baseline_stm32f4.py
    python scripts/reindex_baseline_stm32f4.py --model oc/mimo-v2.5
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

PDF_PATH = ROOT / "data" / "pdfs" / "mcu" / "stm32f4_gpio_exti_extract.pdf"
KB_ID = "builtin-001"
BENCHMARK_DIR = ROOT / "data" / "benchmark"
DOC_ID = "baseline-stm32f4-gpio-exti-v2"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

VISION_PATTERNS = (
    "gpt-4o", "gpt-4-turbo", "claude-3", "gemini", "qwen-vl", "llava",
    "mimo", "minimax", "kimi-k2", "glm-4v", "oc/mimo", "oc/minimax",
)


def supports_vision(model: str) -> bool:
    """Return True if model name looks vision-capable."""
    m = model.lower()
    return any(p in m for p in VISION_PATTERNS)


def resolve_config(kb, args):
    """Resolve model/base_url/api_key from KB config, .env, or CLI."""
    from src.config.settings import settings

    has_kb_key = bool(kb.agent_chunker_api_key_encrypted)

    if args.model:
        model = args.model
    elif has_kb_key and kb.agent_chunker_model:
        model = kb.agent_chunker_model
    else:
        model = settings.llm_model or kb.agent_chunker_model or "gpt-4o"

    if args.base_url:
        base_url = args.base_url
    elif has_kb_key and kb.agent_chunker_base_url:
        base_url = kb.agent_chunker_base_url
    else:
        base_url = settings.llm_base_url or kb.agent_chunker_base_url or "https://api.openai.com/v1"

    api_key = args.api_key or ""
    if not api_key and has_kb_key:
        from app.api.auth import decrypt_key
        try:
            api_key = decrypt_key(kb.agent_chunker_api_key_encrypted)
        except Exception as e:
            logger.warning(f"Failed to decrypt KB agent_chunker_api_key_encrypted: {e}")
    if not api_key:
        api_key = settings.llm_api_key or ""

    return model, base_url, api_key


def clean_existing_doc(km, db):
    """Delete existing STM32F4 records from builtin-001 by doc_id or title."""
    from app.db.models import KnowledgeDoc

    old_docs = db.query(KnowledgeDoc).filter(
        KnowledgeDoc.kb_id == KB_ID,
        (KnowledgeDoc.doc_id == DOC_ID) | (KnowledgeDoc.title.ilike("%stm32f4%"))
    ).all()

    deleted_vectors = 0
    for doc in old_docs:
        try:
            store = km._get_store(km.get_kb(KB_ID))
            deleted_vectors += store.delete_document(doc.doc_id) or 0
        except Exception as e:
            logger.warning(f"Failed to delete vectors for {doc.doc_id}: {e}")
        db.delete(doc)
    db.commit()

    if old_docs:
        logger.info(f"Deleted {len(old_docs)} old stm32f4 docs and {deleted_vectors} vectors")
        km._rebuild_bm25(KB_ID)
        logger.info("BM25 rebuilt")
    return len(old_docs), deleted_vectors


async def run_reindex(args):
    import os

    from src.config.settings import settings

    os.environ.setdefault("SQLITE_DB_PATH", settings.sqlite_db_path)

    from app.db.database import init_db, SessionLocal
    from app.db.models import KnowledgeBase, KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from src.rag.chunking import get_chunker
    from src.rag.document_processor import UnifiedPdfParser

    init_db()
    km = get_kb_manager()
    kb = km.get_kb(KB_ID)
    if not kb:
        raise RuntimeError(f"KB {KB_ID} not found")

    model, base_url, api_key = resolve_config(kb, args)

    if not api_key:
        logger.error("No API key available. Configure KB.agent_chunker_api_key_encrypted or LLM_API_KEY in .env")
        return 1
    if not supports_vision(model):
        logger.error(f"Model '{model}' does not appear to support vision. Use --model to specify a vision model.")
        return 1

    logger.info(f"Using model={model}, base_url={base_url}")

    with SessionLocal() as db:
        clean_existing_doc(km, db)

    chunker = get_chunker(
        "multimodal",
        model=model,
        base_url=base_url,
        api_key=api_key,
        small_chunk_size=kb.small_chunk_size or 800,
        timeout=300.0,
    )

    logger.info(f"Parsing PDF: {PDF_PATH}")
    text_content, total_pages = UnifiedPdfParser().parse(PDF_PATH)
    logger.info(f"Parsed {len(text_content)} chars, {total_pages} pages")

    logger.info(f"Chunking doc_id={DOC_ID}")
    t0 = time.time()

    try:
        chunks = await asyncio.wait_for(
            chunker.chunk(
                text=text_content,
                metadata={
                    "doc_id": DOC_ID,
                    "title": PDF_PATH.name,
                    "file_type": "pdf",
                    "category": "baseline",
                },
                file_path=PDF_PATH,
                total_pages=total_pages,
            ),
            timeout=1800.0,
        )
    except asyncio.TimeoutError:
        logger.error("Chunking exceeded 30-minute total timeout")
        return 1

    elapsed = time.time() - t0
    logger.info(f"Generated {len(chunks)} chunks in {elapsed:.1f}s")

    ingested = km.ingest_chunks(KB_ID, chunks, DOC_ID)
    logger.info(f"Ingested {ingested} chunks")

    with SessionLocal() as db:
        existing = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == DOC_ID).first()
        if existing:
            db.delete(existing)
            db.commit()
        db.add(KnowledgeDoc(
            doc_id=DOC_ID,
            kb_id=KB_ID,
            title=PDF_PATH.name,
            category="baseline",
            file_type="pdf",
            file_size=PDF_PATH.stat().st_size,
            chunk_count=len(chunks),
            chunk_method_used="multimodal",
            status="indexed",
        ))
        db.commit()

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = [
        {
            "id": getattr(c, "id", None),
            "document": getattr(c, "text", "") or getattr(c, "document", ""),
            "metadata": dict(getattr(c, "metadata", {})),
        }
        for c in chunks
    ]
    snapshot_path = BENCHMARK_DIR / "stm32f4_chunks_v2.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Snapshot saved: {snapshot_path}")

    ct = Counter(c.metadata.get("content_type", "text") for c in chunks)
    img_count = ct.get("image_description", 0)
    text_count = len(chunks) - img_count

    print("\n=== Reindex Summary ===")
    print(f"doc_id: {DOC_ID}")
    print(f"total_chunks: {len(chunks)}")
    print(f"text_chunks: {text_count}")
    print(f"image_description_chunks: {img_count}")
    print(f"elapsed_seconds: {elapsed:.1f}")
    print(f"chunk_method: multimodal")
    print(f"model: {model}")
    print(f"content_types: {dict(ct)}")
    print("=======================\n")

    return 0, DOC_ID, len(chunks), text_count, img_count, elapsed, model


def main():
    parser = argparse.ArgumentParser(description="Reindex STM32F4 GPIO/EXTI into builtin-001")
    parser.add_argument("--model", default="", help="Override vision model")
    parser.add_argument("--base-url", default="", help="Override base URL")
    parser.add_argument("--api-key", default="", help="Override API key")
    args = parser.parse_args()

    result = asyncio.run(run_reindex(args))
    if isinstance(result, int):
        return result
    return 0


if __name__ == "__main__":
    sys.exit(main())
