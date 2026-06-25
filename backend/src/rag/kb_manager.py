"""
KnowledgeBaseManager — multi-KB management with BM25 + Vector + RRF fusion.

Manages multiple knowledge bases, each with its own:
- ChromaDB collection
- Embedding model
- BM25 index
- Chunk method (hybrid / agent)
"""

import os
import uuid
import pickle
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from sqlalchemy.orm import Session as DBSession

from app.db.models import KnowledgeBase, KnowledgeDoc
from app.db.database import SessionLocal
from app.api.auth import encrypt_key, decrypt_key

from src.rag.vector_store import HardwareVectorStore, SearchResult
from src.rag.document_loader import CHROMA_DIR

logger = logging.getLogger(__name__)

# ─── Paths ───
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _BACKEND_DIR / "data"
BUILTIN_KB_DIR = _DATA_DIR / "builtin_kb"
USER_CHROMA_DIR = _DATA_DIR / "chroma_db"
BM25_DIR = _DATA_DIR / "bm25"

BUILTIN_KB_ID = "builtin-001"
BUILTIN_KB_NAME = "硬件手册库"


@dataclass
class FusedResult:
    """A single search result after RRF fusion."""

    content: str
    metadata: dict
    score: float
    doc_id: str
    kb_id: str
    kb_name: str


class BM25Index:
    """BM25 index using jieba tokenization (lazy-loaded)."""

    def __init__(self, corpus: list[str], metadatas: list[dict] | None = None):
        self.corpus = corpus
        self.metadatas = metadatas or [{} for _ in corpus]
        self._bm25 = None
        self._tokenized = None

    def _ensure_index(self):
        if self._bm25 is not None:
            return
        import jieba  # Lazy load (~2s first time)
        from rank_bm25 import BM25Okapi

        self._tokenized = [jieba.lcut(doc) for doc in self.corpus]
        self._bm25 = BM25Okapi(self._tokenized)

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Return top-k (doc_index, score)."""
        self._ensure_index()
        import jieba

        tokenized_query = jieba.lcut(query)
        scores = self._bm25.get_scores(tokenized_query)
        # Sort by score descending
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return ranked[:k]

    def save(self, path: Path):
        """Save BM25 index to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_index()
        with open(path, "wb") as f:
            pickle.dump({"corpus": self.corpus, "tokenized": self._tokenized, "metadatas": self.metadatas}, f)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        """Load BM25 index from disk."""
        with open(path, "rb") as f:
            data = pickle.load(f)
        idx = cls(data["corpus"], data.get("metadatas"))
        idx._tokenized = data["tokenized"]
        # Rebuild BM25 from tokenized corpus
        from rank_bm25 import BM25Okapi
        idx._bm25 = BM25Okapi(data["tokenized"])
        return idx


def rrf_fusion(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    constant_k: int = 60,
) -> list[SearchResult]:
    """
    Reciprocal Rank Fusion: score = sum(1/(k + rank_v)) + sum(1/(k + rank_b))

    Args:
        vector_results: Results from vector search, already sorted by score.
        bm25_results: Results from BM25 search, already sorted by score.
        constant_k: RRF constant (default 60).

    Returns:
        Fused results sorted by RRF score descending.
    """
    # Build rank maps by doc_id + chunk_index
    v_rank: dict[str, int] = {}
    for i, r in enumerate(vector_results):
        key = f"{r.doc_id}#{r.metadata.get('chunk_index', i)}"
        v_rank[key] = i

    b_rank: dict[str, int] = {}
    for i, r in enumerate(bm25_results):
        key = f"{r.doc_id}#{r.metadata.get('chunk_index', i)}"
        b_rank[key] = i

    # Collect all unique results (use same key logic as rank maps)
    all_results: dict[str, SearchResult] = {}
    for i, r in enumerate(vector_results):
        key = f"{r.doc_id}#{r.metadata.get('chunk_index', i)}"
        all_results[key] = r
    for i, r in enumerate(bm25_results):
        key = f"{r.doc_id}#{r.metadata.get('chunk_index', i)}"
        if key not in all_results:
            all_results[key] = r

    # Compute RRF scores
    scored: list[tuple[float, SearchResult]] = []
    for key, result in all_results.items():
        score = 0.0
        if key in v_rank:
            score += 1.0 / (constant_k + v_rank[key] + 1)
        if key in b_rank:
            score += 1.0 / (constant_k + b_rank[key] + 1)
        scored.append((score, result))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


class KnowledgeBaseManager:
    """Manages multiple knowledge bases: creation, deletion, search routing."""

    def __init__(self, db_session_factory=SessionLocal):
        self._db_factory = db_session_factory
        self._stores: dict[str, HardwareVectorStore] = {}  # kb_id → store
        self._bm25_indices: dict[str, BM25Index] = {}  # kb_id → BM25
        self._bm25_stale: set[str] = set()  # kb_ids needing BM25 rebuild

    # ═══════════════════════════════════════
    # KB CRUD
    # ═══════════════════════════════════════

    def create_kb(
        self,
        name: str,
        chunk_method: str = "hybrid",
        embedding_model: str = "text-embedding-3-small",
        embedding_base_url: str = "",
        embedding_api_key: str = "",
        agent_chunker_model: str = "gpt-4o-mini",
        agent_chunker_base_url: str = "https://api.openai.com/v1",
        agent_chunker_api_key: str = "",
        context_window: int = 256000,
        description: str = "",
    ) -> KnowledgeBase:
        """Create a new knowledge base."""
        kb_id = f"kb-{uuid.uuid4().hex[:8]}"
        collection_name = f"kb_{kb_id.replace('-', '_')}"

        # Encrypt API keys
        emb_key_enc = encrypt_key(embedding_api_key) if embedding_api_key else None
        agent_key_enc = encrypt_key(agent_chunker_api_key) if agent_chunker_api_key else None

        db = self._db_factory()
        try:
            kb = KnowledgeBase(
                id=kb_id,
                name=name,
                description=description,
                collection_name=collection_name,
                chunk_method=chunk_method,
                embedding_model=embedding_model,
                embedding_base_url=embedding_base_url or None,
                embedding_api_key_encrypted=emb_key_enc,
                agent_chunker_model=agent_chunker_model,
                agent_chunker_base_url=agent_chunker_base_url,
                agent_chunker_api_key_encrypted=agent_key_enc,
                context_window=context_window,
                enabled=True,
                is_builtin=False,
            )
            db.add(kb)
            db.commit()
            db.refresh(kb)
            logger.info(f"Created KB: {kb_id} ({name})")
            return kb
        finally:
            db.close()

    def update_kb_config(
        self,
        kb_id: str,
        embedding_model: Optional[str] = None,
        embedding_base_url: Optional[str] = None,
        embedding_api_key: Optional[str] = None,
        agent_chunker_model: Optional[str] = None,
        agent_chunker_base_url: Optional[str] = None,
        agent_chunker_api_key: Optional[str] = None,
        chunk_method: Optional[str] = None,
        context_window: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Optional[KnowledgeBase]:
        """Update KB configuration. Pass None to skip a field.
        For api_key fields, pass empty string to CLEAR, None to KEEP existing.
        Invalidates store cache so next access creates a fresh store.
        """
        db = self._db_factory()
        try:
            kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
            if not kb:
                return None

            if embedding_model is not None:
                kb.embedding_model = embedding_model
            if embedding_base_url is not None:
                kb.embedding_base_url = embedding_base_url or None
            if embedding_api_key is not None:
                kb.embedding_api_key_encrypted = encrypt_key(embedding_api_key) if embedding_api_key else None
            if agent_chunker_model is not None:
                kb.agent_chunker_model = agent_chunker_model
            if agent_chunker_base_url is not None:
                kb.agent_chunker_base_url = agent_chunker_base_url
            if agent_chunker_api_key is not None:
                kb.agent_chunker_api_key_encrypted = encrypt_key(agent_chunker_api_key) if agent_chunker_api_key else None
            if chunk_method is not None:
                kb.chunk_method = chunk_method
            if context_window is not None:
                kb.context_window = context_window
            if description is not None:
                kb.description = description

            db.commit()
            db.refresh(kb)

            # Invalidate store cache — next _get_store will create fresh store with new config
            self._stores.pop(kb_id, None)
            # Also mark BM25 stale since embedding change might affect search
            self._bm25_stale.add(kb_id)

            logger.info(f"Updated KB config: {kb_id} (cache invalidated)")
            return kb
        finally:
            db.close()

    def list_kbs(self) -> list[dict]:
        """List all knowledge bases with stats."""
        db = self._db_factory()
        try:
            kbs = db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).all()
            result = []
            for kb in kbs:
                doc_count = db.query(KnowledgeDoc).filter(
                    KnowledgeDoc.kb_id == kb.id
                ).count()
                chunk_sum = db.query(KnowledgeDoc).filter(
                    KnowledgeDoc.kb_id == kb.id
                ).with_entities(
                    __import__("sqlalchemy").func.sum(KnowledgeDoc.chunk_count)
                ).scalar() or 0

                result.append({
                    "id": kb.id,
                    "name": kb.name,
                    "description": kb.description or "",
                    "collection_name": kb.collection_name,
                    "chunk_method": kb.chunk_method,
                    "embedding_model": kb.embedding_model,
                    "embedding_base_url": kb.embedding_base_url or "",
                    "agent_chunker_model": kb.agent_chunker_model,
                    "agent_chunker_base_url": kb.agent_chunker_base_url or "",
                    "context_window": kb.context_window or 0,
                    "enabled": kb.enabled,
                    "is_builtin": kb.is_builtin,
                    "doc_count": doc_count,
                    "chunk_count": chunk_sum,
                    "created_at": kb.created_at.isoformat() if kb.created_at else "",
                })
            return result
        finally:
            db.close()

    def get_kb(self, kb_id: str) -> Optional[KnowledgeBase]:
        """Get a single KB by ID."""
        db = self._db_factory()
        try:
            return db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        finally:
            db.close()

    def delete_kb(self, kb_id: str) -> bool:
        """Delete KB: DB record + ChromaDB collection + BM25 index."""
        db = self._db_factory()
        try:
            kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
            if not kb:
                return False
            if kb.is_builtin:
                raise ValueError("Cannot delete builtin knowledge base")

            # Delete ChromaDB collection
            store = self._get_store(kb)
            if store:
                try:
                    store.delete_collection()
                except Exception:
                    logger.warning(f"Failed to delete ChromaDB collection for {kb_id}")

            # Delete BM25 index file
            bm25_path = BM25_DIR / f"{kb.collection_name}.pkl"
            if bm25_path.exists():
                bm25_path.unlink()

            # Delete DB records
            db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).delete()
            db.delete(kb)
            db.commit()

            # Clear caches
            self._stores.pop(kb_id, None)
            self._bm25_indices.pop(kb_id, None)
            self._bm25_stale.discard(kb_id)

            logger.info(f"Deleted KB: {kb_id}")
            return True
        finally:
            db.close()

    def toggle_kb(self, kb_id: str, enabled: bool) -> bool:
        """Toggle KB search on/off."""
        db = self._db_factory()
        try:
            kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
            if not kb:
                return False
            kb.enabled = enabled
            db.commit()
            logger.info(f"KB {kb_id} enabled={enabled}")
            return True
        finally:
            db.close()

    # ═══════════════════════════════════════
    # Ingest
    # ═══════════════════════════════════════

    def ingest_chunks(self, kb_id: str, chunks: list, doc_id: str) -> int:
        """Ingest pre-chunked data into KB's ChromaDB + mark BM25 stale.

        Delegates to HardwareVectorStore.ingest_chunks() which handles
        embedding checks and metadata enrichment.
        """
        kb = self.get_kb(kb_id)
        if not kb:
            raise ValueError(f"KB not found: {kb_id}")

        store = self._get_store(kb)
        if not store:
            logger.warning(f"No store for KB {kb_id}, skipping ingest")
            return 0

        # Inject kb_id into each chunk's metadata so it's persisted in ChromaDB
        for chunk in chunks:
            if hasattr(chunk, "metadata") and isinstance(chunk.metadata, dict):
                chunk.metadata["kb_id"] = kb_id

        # Delegate to store's ingest_chunks (handles embeddings check + metadata)
        ingested = store.ingest_chunks(chunks, doc_id)

        # Mark BM25 as stale
        self._bm25_stale.add(kb_id)

        logger.info(f"Ingested {ingested} chunks into KB {kb_id}")
        return ingested

    def get_doc_chunks(self, kb_id: str, doc_id: str) -> list[dict]:
        """Get all chunks for a specific document in a KB."""
        kb = self.get_kb(kb_id)
        if not kb:
            return []
        store = self._get_store(kb)
        if not store:
            return []
        return store.get_chunks_by_doc(doc_id)

    def get_chunk_by_small_id(self, kb_id: str, small_chunk_id: str) -> Optional[dict]:
        """Get a single chunk by its small_chunk_id metadata field."""
        kb = self.get_kb(kb_id)
        if not kb:
            return None
        store = self._get_store(kb)
        if not store:
            return None
        return store.get_chunk_by_small_id(small_chunk_id)

    def export_kb(self, kb_id: str) -> dict:
        """Export KB data (documents + embeddings + metadatas) for migration.

        Returns a dict with KB metadata and ChromaDB data, suitable for JSON serialization.
        """
        kb = self.get_kb(kb_id)
        if not kb:
            raise ValueError(f"KB not found: {kb_id}")

        store = self._get_store(kb)
        if not store:
            return {"kb_id": kb_id, "name": kb.name, "data": {"ids": [], "documents": [], "embeddings": [], "metadatas": []}}

        data = store.export_data()
        return {
            "kb_id": kb_id,
            "name": kb.name,
            "embedding_model": kb.embedding_model,
            "embedding_base_url": kb.embedding_base_url,
            "chunk_method": kb.chunk_method,
            "exported_at": __import__("datetime").datetime.now().isoformat(),
            "chunk_count": len(data.get("documents", [])),
            "data": data,
        }

    def import_kb(self, kb_id: str, export_data: dict) -> int:
        """Import previously exported KB data into an existing KB.

        This directly writes embeddings into ChromaDB without re-embedding.
        The target KB should use the same embedding model as the source.

        Args:
            kb_id: Target KB ID (must already exist).
            export_data: Dict from export_kb().

        Returns:
            Number of chunks imported.
        """
        kb = self.get_kb(kb_id)
        if not kb:
            raise ValueError(f"KB not found: {kb_id}")

        store = self._get_store(kb)
        if not store:
            raise ValueError(f"Store not available for KB {kb_id}")

        data = export_data.get("data", {})
        imported = store.import_data(data)

        # Mark BM25 as stale so it gets rebuilt on next search
        if imported > 0:
            self._bm25_stale.add(kb_id)
            # Invalidate store cache to force reload
            self._stores.pop(kb_id, None)

        logger.info(f"Imported {imported} chunks into KB {kb_id}")
        return imported

    # ═══════════════════════════════════════
    # Search
    # ═══════════════════════════════════════

    def search(self, kb_id: str, query: str, k: int = 5) -> list[FusedResult]:
        """Search a single KB: BM25 + Vector → RRF fusion."""
        kb = self.get_kb(kb_id)
        if not kb or not kb.enabled:
            return []

        store = self._get_store(kb)
        if not store:
            return []

        # Vector search
        vector_results = store.search(query, k=k)

        # BM25 search
        bm25_results = self._bm25_search(kb_id, query, k)

        # RRF fusion
        fused = rrf_fusion(vector_results, bm25_results)

        return [
            FusedResult(
                content=r.content,
                metadata=r.metadata,
                score=r.score,
                doc_id=r.doc_id,
                kb_id=kb_id,
                kb_name=kb.name,
            )
            for r in fused[:k]
        ]

    def search_all_enabled(self, query: str, k: int = 3, kb_ids: Optional[list[str]] = None) -> list[FusedResult]:
        """Search all enabled KBs (or selected KBs if kb_ids provided), merge results.

        Args:
            query: Search query text
            k: Top-k results to return
            kb_ids: If provided, only search these KBs; if None/empty, search all enabled KBs
        """
        db = self._db_factory()
        try:
            query_q = db.query(KnowledgeBase).filter(KnowledgeBase.enabled == True)
            if kb_ids:
                query_q = query_q.filter(KnowledgeBase.id.in_(kb_ids))
            kbs = query_q.all()
        finally:
            db.close()

        all_results: list[FusedResult] = []
        for kb in kbs:
            try:
                results = self.search(kb.id, query, k=k)
                all_results.extend(results)
            except Exception:
                logger.exception(f"Search failed for KB {kb.id}")

        # Sort by score descending
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:k]  # Return top-k fused results across all KBs

    # ═══════════════════════════════════════
    # Builtin KB
    # ═══════════════════════════════════════

    def ensure_builtin_kb(self):
        """Ensure builtin KB exists if path is present."""
        db = self._db_factory()
        try:
            existing = db.query(KnowledgeBase).filter(
                KnowledgeBase.is_builtin == True
            ).first()

            if existing:
                return existing

            # Check if builtin path exists
            if not BUILTIN_KB_DIR.exists():
                logger.info("Builtin KB path not found, skipping creation")
                return None

            # Create builtin KB record
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
            logger.info(f"Created builtin KB: {BUILTIN_KB_ID}")
            return kb
        finally:
            db.close()

    # ═══════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════

    def _get_store(self, kb: KnowledgeBase) -> Optional[HardwareVectorStore]:
        """Get or create HardwareVectorStore for a KB."""
        if kb.id in self._stores:
            return self._stores[kb.id]

        # Determine persist directory
        if kb.is_builtin and kb.builtin_path:
            persist_dir = Path(kb.builtin_path)
        else:
            persist_dir = USER_CHROMA_DIR

        # Decrypt embedding API key
        emb_key = None
        if kb.embedding_api_key_encrypted:
            try:
                emb_key = decrypt_key(kb.embedding_api_key_encrypted)
            except Exception:
                logger.warning(f"Failed to decrypt embedding key for KB {kb.id}")

        store = HardwareVectorStore(
            collection_name=kb.collection_name,
            persist_dir=persist_dir,
            embedding_api_key=emb_key,
            embedding_base_url=kb.embedding_base_url,
            embedding_model=kb.embedding_model,
        )
        self._stores[kb.id] = store
        return store

    def _bm25_search(self, kb_id: str, query: str, k: int) -> list[SearchResult]:
        """BM25 search for a KB."""
        # Rebuild if stale
        if kb_id in self._bm25_stale:
            self._rebuild_bm25(kb_id)
            self._bm25_stale.discard(kb_id)

        # Load index
        if kb_id not in self._bm25_indices:
            kb = self.get_kb(kb_id)
            if not kb:
                return []
            bm25_path = BM25_DIR / f"{kb.collection_name}.pkl"
            if not bm25_path.exists():
                return []
            try:
                self._bm25_indices[kb_id] = BM25Index.load(bm25_path)
            except Exception:
                logger.warning(f"Failed to load BM25 index for KB {kb_id}")
                return []

        bm25 = self._bm25_indices[kb_id]
        results = bm25.search(query, k)

        # Convert to SearchResult with metadata from BM25 index
        search_results = []
        for doc_idx, score in results:
            if doc_idx < len(bm25.corpus):
                meta = bm25.metadatas[doc_idx] if doc_idx < len(bm25.metadatas) else {}
                search_results.append(SearchResult(
                    content=bm25.corpus[doc_idx],
                    metadata={**meta, "bm25_score": score, "doc_idx": doc_idx},
                    score=score,
                    doc_id=meta.get("doc_id", ""),
                ))

        return search_results

    def _rebuild_bm25(self, kb_id: str):
        """Rebuild BM25 index for a KB from ChromaDB."""
        kb = self.get_kb(kb_id)
        if not kb:
            return

        store = self._get_store(kb)
        if not store:
            return

        # Get all texts + metadatas from ChromaDB
        try:
            collection = store.db.get()
            texts = collection.get("documents", [])
            metadatas = collection.get("metadatas", [])
            if not texts:
                return

            if not metadatas:
                metadatas = [{} for _ in texts]

            bm25 = BM25Index(texts, metadatas)
            bm25_path = BM25_DIR / f"{kb.collection_name}.pkl"
            bm25.save(bm25_path)
            self._bm25_indices[kb_id] = bm25
            logger.info(f"Rebuilt BM25 index for KB {kb_id}: {len(texts)} docs")
        except Exception:
            logger.exception(f"Failed to rebuild BM25 for KB {kb_id}")


# ─── Singleton ───
_kb_manager: Optional[KnowledgeBaseManager] = None


def get_kb_manager() -> KnowledgeBaseManager:
    """Get the global KnowledgeBaseManager singleton."""
    global _kb_manager
    if _kb_manager is None:
        _kb_manager = KnowledgeBaseManager()
    return _kb_manager
