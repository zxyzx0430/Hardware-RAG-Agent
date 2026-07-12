"""
Chunk-baseline-v2 Phase 1 cleanup script.

Idempotent cleanup of:
1. ChromaDB collection `hardware-docs-test` (all chunks).
2. SQLite `knowledge_docs` rows with `kb_id='builtin-001'`.
3. Update `knowledge_bases` row for `builtin-001`:
   - chunk_method = 'multimodal'
   - embedding_model = settings.embedding_model
   - embedding_base_url = settings.embedding_base_url
   - agent_chunker_model = 'oc/mimo-v2.5'
   - agent_chunker_base_url = settings.llm_base_url
4. Delete stale BM25 index `data/bm25/hardware-docs-test.pkl`.

Run with --dry-run to preview without deleting.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

# Import settings for authoritative paths.
sys.path.insert(0, str(BACKEND_DIR))
from src.config.settings import settings  # noqa: E402

CHROMA_DIR = Path(settings.chroma_persist_dir)
SQLITE_PATH = Path(settings.sqlite_db_path)
BM25_PATH = PROJECT_ROOT / "data" / "bm25" / "hardware-docs-test.pkl"

COLLECTION_NAME = "hardware-docs-test"
TARGET_KB_ID = "builtin-001"
TARGET_CHUNKER_MODEL = "oc/mimo-v2.5"


def get_chroma_collection_count() -> int:
    """Return chunk count for target collection, or -1 if collection missing."""
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError("chromadb not installed") from exc

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        collection = client.get_collection(COLLECTION_NAME)
        data = collection.get()
        return len(data.get("ids", []))
    except Exception as exc:
        logger.info(f"Collection '{COLLECTION_NAME}' not accessible: {exc}")
        return -1


def clear_chroma_collection(dry_run: bool) -> int:
    """Delete all chunks in target collection. Returns count before deletion."""
    count_before = get_chroma_collection_count()
    if count_before < 0:
        logger.info("[ChromaDB] Collection does not exist; nothing to clear.")
        return 0
    if count_before == 0:
        logger.info("[ChromaDB] Collection already empty; nothing to delete.")
        return 0

    logger.info(f"[ChromaDB] Collection '{COLLECTION_NAME}' has {count_before} chunks.")
    if dry_run:
        logger.info("[ChromaDB] (dry-run) would delete all chunks.")
        return count_before

    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError("chromadb not installed") from exc

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection(COLLECTION_NAME)
    try:
        collection.delete(where={})
    except Exception as exc:
        logger.warning(f"[ChromaDB] delete(where={{}}) failed ({exc}); falling back to id-based delete.")
        data = collection.get()
        ids = data.get("ids", [])
        if ids:
            batch_size = 500
            for i in range(0, len(ids), batch_size):
                collection.delete(ids=ids[i : i + batch_size])

    count_after = get_chroma_collection_count()
    logger.info(f"[ChromaDB] Cleared. Count after: {count_after}")
    return count_before


def get_sqlite_doc_count() -> int:
    """Return number of knowledge_docs rows for target kb_id."""
    if not SQLITE_PATH.exists():
        return -1

    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_docs'"
        )
        if not cur.fetchone():
            return -1
        cur.execute(
            "SELECT COUNT(*) FROM knowledge_docs WHERE kb_id = ?",
            (TARGET_KB_ID,),
        )
        count = cur.fetchone()[0]
        return count
    finally:
        conn.close()


def clear_sqlite_docs(dry_run: bool) -> int:
    """Delete knowledge_docs rows for target kb_id. Returns count before deletion."""
    count_before = get_sqlite_doc_count()
    if count_before < 0:
        logger.info("[SQLite] DB or table does not exist; nothing to clear.")
        return 0
    if count_before == 0:
        logger.info("[SQLite] knowledge_docs kb_id already empty; nothing to delete.")
        return 0

    logger.info(f"[SQLite] knowledge_docs kb_id='{TARGET_KB_ID}' has {count_before} rows.")
    if dry_run:
        logger.info("[SQLite] (dry-run) would delete matching rows.")
        return count_before

    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM knowledge_docs WHERE kb_id = ?",
            (TARGET_KB_ID,),
        )
        deleted = cur.rowcount
        conn.commit()
        logger.info(f"[SQLite] Deleted {deleted} rows.")
    finally:
        conn.close()

    count_after = get_sqlite_doc_count()
    logger.info(f"[SQLite] Count after: {count_after}")
    return count_before


def update_kb_config(dry_run: bool) -> bool:
    """Update knowledge_bases row for builtin-001 to multimodal config."""
    if not SQLITE_PATH.exists():
        logger.info("[SQLite] DB does not exist; skipping KB config update.")
        return False

    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_bases'"
        )
        if not cur.fetchone():
            logger.info("[SQLite] knowledge_bases table does not exist; skipping KB config update.")
            return False

        cur.execute("SELECT id FROM knowledge_bases WHERE id = ?", (TARGET_KB_ID,))
        if not cur.fetchone():
            logger.warning(f"[SQLite] KB '{TARGET_KB_ID}' not found in knowledge_bases.")
            return False

        logger.info(
            f"[KB config] Will set chunk_method=multimodal, "
            f"embedding_model={settings.embedding_model}, "
            f"embedding_base_url={settings.embedding_base_url}, "
            f"agent_chunker_model={TARGET_CHUNKER_MODEL}, "
            f"agent_chunker_base_url={settings.llm_base_url}, "
            f"builtin_path={settings.chroma_persist_dir}"
        )
        if dry_run:
            logger.info("[KB config] (dry-run) would update row.")
            return True

        cur.execute(
            """
            UPDATE knowledge_bases
            SET chunk_method = ?,
                embedding_model = ?,
                embedding_base_url = ?,
                agent_chunker_model = ?,
                agent_chunker_base_url = ?,
                builtin_path = ?
            WHERE id = ?
            """,
            (
                "multimodal",
                settings.embedding_model,
                settings.embedding_base_url,
                TARGET_CHUNKER_MODEL,
                settings.llm_base_url,
                str(CHROMA_DIR),
                TARGET_KB_ID,
            ),
        )
        conn.commit()
        logger.info("[KB config] Updated builtin-001 row.")
        return True
    finally:
        conn.close()


def delete_bm25_index(dry_run: bool) -> bool:
    """Delete stale BM25 index file for the target collection."""
    if not BM25_PATH.exists():
        logger.info("[BM25] Index file does not exist; nothing to delete.")
        return False

    logger.info(f"[BM25] Found stale index: {BM25_PATH}")
    if dry_run:
        logger.info("[BM25] (dry-run) would delete file.")
        return True

    try:
        BM25_PATH.unlink()
        logger.info("[BM25] Deleted stale index.")
        return True
    except Exception as exc:
        logger.warning(f"[BM25] Failed to delete index: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk-baseline-v2 Phase 1 cleanup")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without making changes.",
    )
    args = parser.parse_args()

    logger.info("=== Cleanup baseline KB (Phase 1 v2) ===")
    logger.info(f"dry_run={args.dry_run}")
    logger.info(f"chroma_dir={CHROMA_DIR}")
    logger.info(f"sqlite_path={SQLITE_PATH}")
    logger.info(f"bm25_path={BM25_PATH}")

    kb_updated = update_kb_config(args.dry_run)
    chroma_before = clear_chroma_collection(args.dry_run)
    sqlite_before = clear_sqlite_docs(args.dry_run)
    bm25_deleted = delete_bm25_index(args.dry_run)

    logger.info("=== Summary ===")
    logger.info(f"KB config updated: {kb_updated}")
    logger.info(f"ChromaDB '{COLLECTION_NAME}' chunks before cleanup: {chroma_before}")
    logger.info(f"SQLite kb_id='{TARGET_KB_ID}' rows before cleanup: {sqlite_before}")
    logger.info(f"BM25 index deleted: {bm25_deleted}")

    if not args.dry_run:
        chroma_after = get_chroma_collection_count()
        sqlite_after = get_sqlite_doc_count()
        logger.info(f"ChromaDB count after: {chroma_after}")
        logger.info(f"SQLite count after: {sqlite_after}")
        if chroma_after > 0:
            raise SystemExit(f"Verification failed: ChromaDB count is {chroma_after}, expected 0")
        if sqlite_after != 0:
            raise SystemExit(f"Verification failed: SQLite count is {sqlite_after}, expected 0")
        logger.info("Verification passed: ChromaDB count == 0 and SQLite kb_id rows == 0.")


if __name__ == "__main__":
    main()
