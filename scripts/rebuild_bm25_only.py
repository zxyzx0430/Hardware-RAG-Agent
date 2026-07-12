"""Rebuild BM25 index only (no re-chunking) for builtin-001.

Used after BM25 tokenizer logic changed — old pkl has stale tokenized corpus.

Run:
    python scripts/rebuild_bm25_only.py
"""
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

KB_ID = "builtin-001"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    import os
    from src.config.settings import settings
    os.environ.setdefault("SQLITE_DB_PATH", settings.sqlite_db_path)
    from app.db.database import init_db
    from src.rag.kb_manager import get_kb_manager, BM25_DIR

    init_db()
    km = get_kb_manager()
    kb = km.get_kb(KB_ID)
    if not kb:
        raise RuntimeError(f"KB {KB_ID} not found")

    # Delete stale pkl before rebuild
    pkl_path = BM25_DIR / f"{kb.collection_name}.pkl"
    if pkl_path.exists():
        pkl_path.unlink()
        logger.info(f"Deleted stale BM25 pkl: {pkl_path}")

    # Rebuild uses new _tokenize_for_bm25 logic
    km._rebuild_bm25(KB_ID)
    logger.info(f"BM25 index rebuilt for KB {KB_ID}")

    # Verify
    bm25 = km._bm25_indices.get(KB_ID)
    if bm25:
        logger.info(f"Verification: {len(bm25.corpus)} docs in index")
        # Test tokenization on a known tricky term
        test_query = "GPIOx_MODER MODERy[1:0] SWJ-DP Deep-sleep"
        tokens = bm25._tokenize_for_bm25(test_query)
        logger.info(f"Test query tokenization: {test_query}")
        logger.info(f"Tokens: {tokens}")


if __name__ == "__main__":
    main()
