"""
ChromaDB 向量存储模块。

功能：
  - 将翻译后的中文 Markdown 文档向量化并入库
  - 提供检索接口（相似度搜索 + 来源标注）
  - 支持按 category 过滤（Week 8 多知识库前置）
  - 持久化到本地磁盘
"""

import uuid
import logging
import time
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import diskcache
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from src.config.settings import settings
from src.rag.document_loader import CHROMA_DIR
from src.rag.document_processor import ProcessedDocument
from app.db.database import SessionLocal
from app.db.models import BigChunk

logger = logging.getLogger(__name__)

# Embedding cache directory (text→vector cache, keyed by model+base_url+text)
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_EMBEDDING_CACHE_DIR = _BACKEND_DIR / "data" / "embedding_cache"

# Retry configuration for transient ChromaDB/embedding network errors (P1-E-1).
# A single transient failure on a 294-chunk ingest would otherwise abort the
# entire document and mark it as "error" with message "Connection error."
MAX_RETRIES: int = 3
INITIAL_BACKOFF_S: float = 0.5

# ChromaDB's Rust backend limits batch upsert to 5461 items. Large documents
# (AgentChunker on a 200KB doc can produce 8000+ chunks) hit this limit, so
# sub-batches are split below this safe margin.
CHROMA_BATCH_LIMIT: int = 5000

# Transient exceptions worth retrying (connection/timeout/network).
# Permanent errors (data format, dimension mismatch, auth) propagate immediately.
_TRANSIENT_EXC: tuple = (ConnectionError, TimeoutError, OSError)
try:  # httpx transport errors (OpenAIEmbeddings uses httpx under the hood)
    import httpx as _httpx
    _TRANSIENT_EXC += (
        _httpx.ConnectError,
        _httpx.ReadTimeout,
        _httpx.WriteTimeout,
        _httpx.PoolTimeout,
        _httpx.ConnectTimeout,
        _httpx.ReadError,
        _httpx.WriteError,
    )
except ImportError:
    pass
try:  # openai SDK network errors
    import openai as _openai
    _TRANSIENT_EXC += (_openai.APIConnectionError, _openai.APITimeoutError)
except (ImportError, AttributeError):
    pass


class _EmbeddingCache:
    """Wrapper around OpenAIEmbeddings with disk-based text→vector cache.

    Caches embeddings keyed by sha256(model|base_url|text) to avoid
    re-calling the embedding API for duplicate text (e.g. re-ingesting
    a deleted document). Same model+text always produces the same vector.
    """

    _cache: Optional["diskcache.Cache"] = None  # shared singleton

    @classmethod
    def _get_cache(cls) -> "diskcache.Cache":
        if cls._cache is None:
            _EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cls._cache = diskcache.Cache(str(_EMBEDDING_CACHE_DIR))
            logger.info(f"[EmbeddingCache] opened at {_EMBEDDING_CACHE_DIR}")
        return cls._cache

    def __init__(self, embeddings: OpenAIEmbeddings, model: str, base_url: str):
        self._embeddings = embeddings
        self._model = model
        self._base_url = base_url or ""

    def _key(self, text: str) -> str:
        return hashlib.sha256(
            f"{self._model}|{self._base_url}|{text}".encode("utf-8")
        ).hexdigest()

    def embed_query(self, text: str) -> list[float]:
        key = self._key(text)
        cache = self._get_cache()
        cached = cache.get(key)
        if cached is not None:
            return cached
        vec = self._embeddings.embed_query(text)
        cache[key] = vec
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        cache = self._get_cache()
        for i, text in enumerate(texts):
            key = self._key(text)
            cached = cache.get(key)
            if cached is not None:
                results[i] = cached
            else:
                miss_indices.append(i)
                miss_texts.append(text)
        if miss_texts:
            new_vecs = self._embeddings.embed_documents(miss_texts)
            for idx, vec in zip(miss_indices, new_vecs):
                results[idx] = vec
                cache[self._key(texts[idx])] = vec
        return results  # type: ignore[return-value]


@dataclass
class SearchResult:
    """检索结果。"""

    content: str
    metadata: dict
    score: float
    doc_id: str


class HardwareVectorStore:
    """
    ChromaDB 向量存储封装。

    使用 OpenAI-compatible embeddings（可由 settings 配置 base_url/api_key）。
    LangChain Chroma 包装器，支持持久化。
    """

    def __init__(
        self,
        collection_name: str = "hardware-docs",
        persist_dir: Optional[Path] = None,
        embedding_api_key: Optional[str] = None,
        embedding_base_url: Optional[str] = None,
        embedding_model: str = "text-embedding-3-small",
        ef_search: int = 200,
    ):
        self.collection_name = collection_name
        self.persist_dir = persist_dir or CHROMA_DIR
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self._embedding_api_key = embedding_api_key
        self._embedding_base_url = embedding_base_url
        # HNSW ef_search 调优（B5 优化）— 搜索时探索的邻居数，越大召回越高
        self._ef_search = ef_search

        # 初始化 Embeddings
        api_key = embedding_api_key or settings.embedding_api_key
        base_url = embedding_base_url or settings.embedding_base_url
        if api_key:
            raw_embeddings = OpenAIEmbeddings(
                model=self.embedding_model,
                openai_api_key=api_key,
                openai_api_base=base_url,
                # Disable tiktoken tokenization — send raw text strings instead of token IDs.
                # Non-OpenAI providers (e.g. Alibaba Cloud Bailian/DashScope) reject token ID lists.
                tiktoken_enabled=False,
                check_embedding_ctx_length=False,
                # Limit batch size: Alibaba Cloud Bailian text-embedding-v4 allows max 10 rows per request.
                chunk_size=10,
                # P0: Add retry on transient network errors (httpx.ConnectError,
                # ReadTimeout, etc). Without this, a single transient failure on
                # a 294-chunk document aborts the entire ingest and marks the
                # doc as "error" with message "Connection error."
                max_retries=6,
                request_timeout=60,
            )
            # Wrap with disk cache to avoid re-calling embedding API for duplicate text
            self.embeddings = _EmbeddingCache(raw_embeddings, self.embedding_model, base_url)
        else:
            self.embeddings = None

        # 初始化 Chroma
        self._db: Optional[Chroma] = None
        # P1: chromadb import 失败后标记不可用，避免每次 search 都重复尝试 import
        # （opentelemetry 版本冲突时 chromadb import 耗时数秒，多 KB 累积导致 100 秒+）
        self._db_unavailable: bool = False

        # P1: Cache embedding dimension probe result to avoid repeated API
        # calls. _dim_check_attempted tracks whether we've tried probing (so
        # a failed probe won't be retried on every import_data call).
        self._cached_embedding_dim: Optional[int] = None
        self._dim_check_attempted: bool = False

    @property
    def db(self) -> Optional[Chroma]:
        if self._db_unavailable:
            return None
        if self._db is None:
            if self.embeddings is None:
                return None
            try:
                if settings.chroma_mode == "http":
                    import chromadb
                    http_client = chromadb.HttpClient(
                        host=settings.chroma_host,
                        port=settings.chroma_port
                    )
                    self._db = Chroma(
                        collection_name=self.collection_name,
                        embedding_function=self.embeddings,
                        client=http_client,
                        collection_metadata={"hnsw:space": "cosine"},
                    )
                    logger.info(f"[VectorStore] Using HttpClient: {settings.chroma_host}:{settings.chroma_port}")
                else:
                    self._db = Chroma(
                        collection_name=self.collection_name,
                        embedding_function=self.embeddings,
                        persist_directory=str(self.persist_dir),
                        collection_metadata={"hnsw:space": "cosine"},
                    )
            except Exception as e:
                # chromadb import 失败（如 opentelemetry 版本冲突）— 标记不可用，
                # 避免后续每次 search 都重复尝试 import（耗时数秒，多 KB 累积 100 秒+）
                self._db_unavailable = True
                logger.error(
                    f"[VectorStore] ChromaDB 初始化失败，向量检索将不可用: {e}\n"
                    f"常见原因：opentelemetry 版本冲突 → 运行 "
                    f"`pip install --upgrade opentelemetry-exporter-otlp-proto-grpc`"
                )
                return None
            # HNSW ef_search 调优（B5 优化）— 提升召回率，默认 200。
            # chromadb 不同版本 API 不同，用 hasattr 守卫，失败时 warning 不阻断。
            try:
                # langchain_chroma 1.1.0: 用 getattr 容错 _collection 私有属性访问方式变更
                underlying_collection = getattr(self._db, "_collection", None)
                if underlying_collection is not None:
                    # chromadb 0.4+ 用 _collection.set_ef_search() 或 metadata
                    if hasattr(underlying_collection, "_collection"):
                        # LangChain 包装层 → chromadb Collection → hnswlib
                        inner = underlying_collection._collection
                        if hasattr(inner, "_metadata") and "hnsw:search_ef" not in (inner._metadata or {}):
                            # 通过 metadata 设置（部分版本支持）
                            pass
                    # 直接用 chromadb 的 set_ef_search API（如果可用）
                    if hasattr(underlying_collection, "set_ef_search"):
                        underlying_collection.set_ef_search(self._ef_search)
                logger.info(f"[VectorStore] ef_search set to {self._ef_search}")
            except Exception as e:
                logger.warning(f"[VectorStore] Failed to set ef_search: {e}")
        return self._db

    def _build_chunk_metadata(
        self, doc: ProcessedDocument, chunk_index: int, section_title: str
    ) -> dict:
        """构建每个 chunk 的元数据。"""
        # Sanitize source_url: strip local absolute paths to avoid leaking server dirs
        source_url = doc.source.url or ""
        if source_url and (source_url.startswith("/") or ":" in source_url[:3]):
            # Looks like a local path (e.g. /data/uploads/xxx or C:\...), use filename only
            source_url = Path(source_url).name if source_url else ""

        return {
            "doc_id": doc.doc_id,
            "title": doc.source.title,
            "category": doc.source.category,
            "source_url": source_url,
            "tags": ",".join(doc.source.tags),
            "last_updated": doc.source.last_updated,
            "chunk_index": chunk_index,
            "section_title": section_title,
            "chunk_id": f"{doc.doc_id}#chunk-{chunk_index}",
        }

    def _with_retry(self, func, *args, **kwargs):
        """Retry a ChromaDB/embedding operation with exponential backoff on transient errors.

        Transient errors (connection/timeout/network) are retried up to MAX_RETRIES
        times with exponential backoff. Permanent errors (data format, dimension
        mismatch, auth) propagate immediately so callers can handle them.
        """
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except _TRANSIENT_EXC as e:
                last_exc = e
                if attempt < MAX_RETRIES - 1:
                    wait_s = INITIAL_BACKOFF_S * (2 ** attempt)
                    logger.warning(
                        f"ChromaDB op failed (attempt {attempt+1}/{MAX_RETRIES}), "
                        f"retrying in {wait_s}s: {e}"
                    )
                    time.sleep(wait_s)
        logger.error(f"ChromaDB op failed after {MAX_RETRIES} attempts: {last_exc}")
        raise last_exc

    def search(
        self,
        query: str,
        k: int = 5,
        category: Optional[str] = None,
        score_threshold: float = 0.0,
    ) -> list[SearchResult]:
        """
        相似度搜索。如果未配置 embedding 则返回空列表。
        """
        if self.embeddings is None:
            return []
        filter_dict = None
        if category:
            filter_dict = {"category": category}
        try:
            results = self._with_retry(
                self.db.similarity_search_with_relevance_scores,
                query,
                k=k,
                filter=filter_dict,
                score_threshold=score_threshold,
            )
        except Exception:
            logger.exception("向量检索失败")
            return []
        search_results = []
        for lc_doc, score in results:
            search_results.append(
                SearchResult(
                    content=lc_doc.page_content,
                    metadata=lc_doc.metadata,
                    score=score,
                    doc_id=lc_doc.metadata.get("doc_id", ""),
                )
            )
        return search_results

    def get_collection_stats(self) -> dict:
        """获取知识库统计信息。"""
        collection = self.db.get()
        total_docs = len(collection["ids"]) if collection["ids"] else 0

        # 按 category 统计
        categories = {}
        if collection["metadatas"]:
            for meta in collection["metadatas"]:
                cat = meta.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1

        return {
            "collection": self.collection_name,
            "total_chunks": total_docs,
            "categories": categories,
        }

    def delete_document(self, doc_id: str) -> int:
        """删除指定 doc_id 对应的所有向量，返回删除的 chunk 数量。

        Returns 0 if no vectors exist (e.g. embedding not configured —
        document was chunked but never vectorized). Raises on actual
        deletion failures so callers can detect orphan vectors.
        """
        if self.db is None:
            # No ChromaDB instance (embedding not configured) — no vectors to delete.
            return 0
        collection = self.db.get(where={"doc_id": doc_id})
        ids_to_delete = collection.get("ids", [])
        if ids_to_delete:
            self.db.delete(ids=ids_to_delete)
        return len(ids_to_delete)

    def get_chunks_by_doc(self, doc_id: str) -> list[dict]:
        """Retrieve all chunks for a given doc_id. Returns list of dicts with id, content, metadata."""
        try:
            result = self.db.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
            ids = result.get("ids", [])
            documents = result.get("documents", [])
            metadatas = result.get("metadatas", [])
            chunks = []
            for i, cid in enumerate(ids):
                chunks.append({
                    "id": cid,
                    "content": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                })
            # Sort by chunk_index if available
            chunks.sort(key=lambda c: c["metadata"].get("chunk_index", 0))
            return chunks
        except Exception as e:
            logger.exception(f"获取文档 chunks 失败: {doc_id}")
            return []

    def get_chunk_by_small_id(self, small_chunk_id: str) -> Optional[dict]:
        """Retrieve a single chunk by its small_chunk_id metadata field."""
        try:
            result = self.db.get(
                where={"small_chunk_id": small_chunk_id},
                include=["documents", "metadatas"],
            )
            ids = result.get("ids", [])
            if not ids:
                return None
            documents = result.get("documents", [])
            metadatas = result.get("metadatas", [])
            return {
                "id": ids[0],
                "content": documents[0] if documents else "",
                "metadata": metadatas[0] if metadatas else {},
            }
        except Exception:
            logger.exception(f"按 small_chunk_id 查询失败: {small_chunk_id}")
            return None

    def delete_collection(self):
        """清空当前 collection。"""
        try:
            self.db.delete_collection()
            self._db = None
            logger.info(f"已清空 collection: {self.collection_name}")
        except Exception as e:
            logger.exception(f"清空 collection 失败")

    def ingest_chunks(self, chunks: list, doc_id: str) -> int:
        """Ingest pre-chunked data (ChunkResult list) into ChromaDB + big_chunks table.

        ParentDocument retrieval: small chunks go to ChromaDB (with a
        ``big_chunk_id`` pointer in metadata), full section text goes to the
        ``big_chunks`` SQL table. Big-chunk write failures are non-blocking.

        Args:
            chunks: List of ChunkResult objects from chunking module.
            doc_id: Document ID for metadata.

        Returns:
            Number of small chunks ingested into ChromaDB.
        """
        if self.embeddings is None:
            logger.warning("未配置 embedding API，跳过入库")
            return 0
        big_chunks = self._collect_big_chunks(chunks)
        self._write_big_chunks(big_chunks)
        lc_docs = self._build_small_chunk_docs(chunks, doc_id)
        if not lc_docs:
            return 0
        count = self._write_small_chunks(lc_docs, doc_id)
        logger.info(f"入库完成: {doc_id} → {count} chunks")
        return count

    def _collect_big_chunks(self, chunks: list) -> dict[str, dict]:
        """Collect and dedup big chunks from ChunkResult list by big_chunk_id.

        Later occurrences overwrite earlier ones (dict semantics). Only chunks
        with a non-empty big_chunk_id AND non-empty big_chunk_text are kept.
        Page range is aggregated across all small chunks sharing the same
        big_chunk_id (min start / max end) so cross-page sections record the
        full page span rather than just the last small chunk's range.
        """
        big_chunks: dict[str, dict] = {}
        for chunk in chunks:
            bc_id = getattr(chunk, "big_chunk_id", "") or ""
            bc_text = getattr(chunk, "big_chunk_text", "") or ""
            if not bc_id or not bc_text.strip():
                continue
            record = self._build_big_chunk_record(chunk, bc_id, bc_text)
            existing = big_chunks.get(bc_id)
            if existing is not None:
                record["page_start"] = min(existing["page_start"], record["page_start"])
                record["page_end"] = max(existing["page_end"], record["page_end"])
            big_chunks[bc_id] = record
        return big_chunks

    def _build_big_chunk_record(self, chunk, bc_id: str, bc_text: str) -> dict:
        """Build a single big-chunk record dict from a ChunkResult."""
        meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        return {
            "big_chunk_id": bc_id,
            "doc_id": meta.get("doc_id", ""),
            "kb_id": meta.get("kb_id", ""),
            "section_title": chunk.section_title or "",
            "text": bc_text,
            "page_start": chunk.page_range[0],
            "page_end": chunk.page_range[1],
        }

    def _write_big_chunks(self, big_chunks: dict[str, dict]) -> None:
        """Persist big chunks to the big_chunks table. Non-blocking on failure."""
        if not big_chunks:
            return
        db = SessionLocal()
        try:
            self._upsert_big_chunks(db, big_chunks)
            db.commit()
            logger.info(f"[BigChunk] wrote {len(big_chunks)} rows")
        except Exception:
            db.rollback()
            logger.warning(
                f"[BigChunk] write failed for {len(big_chunks)} rows (non-blocking)",
                exc_info=True,
            )
        finally:
            db.close()

    def _upsert_big_chunks(self, db, big_chunks: dict[str, dict]) -> None:
        """Delete existing rows for this batch then insert (idempotent re-ingest).

        big_chunk_id has a UNIQUE constraint, so re-ingesting the same doc
        would raise IntegrityError without the delete-first step.
        """
        ids = list(big_chunks.keys())
        db.query(BigChunk).filter(BigChunk.big_chunk_id.in_(ids)).delete(
            synchronize_session=False
        )
        rows = [BigChunk(**big_chunks[bid]) for bid in ids]
        db.bulk_save_objects(rows)

    def _build_small_chunk_docs(self, chunks: list, doc_id: str) -> list:
        """Build LangChain Documents for ChromaDB from small chunks."""
        from langchain_core.documents import Document as LCDocument
        lc_docs: list[LCDocument] = []
        for chunk in chunks:
            if not chunk.text.strip():
                continue
            metadata = self._build_small_chunk_metadata(chunk, doc_id)
            lc_docs.append(LCDocument(page_content=chunk.text, metadata=metadata))
        return lc_docs

    def _build_small_chunk_metadata(self, chunk, doc_id: str) -> dict:
        """Build small-chunk metadata. Keeps big_chunk_id, strips big_chunk_text."""
        metadata = {**chunk.metadata}
        metadata.pop("big_chunk_text", None)  # defensive: never store full text in ChromaDB
        metadata.update({
            "doc_id": doc_id,
            "chunk_method": chunk.chunk_method,
            "fingerprint": chunk.fingerprint,
            "section_title": chunk.section_title,
            "page_start": chunk.page_range[0],
            "page_end": chunk.page_range[1],
        })
        return metadata

    def _write_small_chunks(self, lc_docs: list, doc_id: str) -> int:
        """Write small chunks to ChromaDB, sub-batching past the Rust limit."""
        if len(lc_docs) <= CHROMA_BATCH_LIMIT:
            self._with_retry(self.db.add_documents, lc_docs)
            return len(lc_docs)
        return self._write_small_chunks_batched(lc_docs)

    def _write_small_chunks_batched(self, lc_docs: list) -> int:
        """Write large chunk lists in sub-batches below ChromaDB's 5461 limit."""
        total = 0
        for i in range(0, len(lc_docs), CHROMA_BATCH_LIMIT):
            batch = lc_docs[i:i + CHROMA_BATCH_LIMIT]
            self._with_retry(self.db.add_documents, batch)
            total += len(batch)
            logger.info(f"  ChromaDB batch {i // CHROMA_BATCH_LIMIT + 1}: "
                        f"added {len(batch)} chunks (total: {total}/{len(lc_docs)})")
        return total

    def get_big_chunks_by_ids(self, big_chunk_ids: list[str]) -> dict[str, dict]:
        """Batch query the big_chunks table by big_chunk_id list.

        Args:
            big_chunk_ids: List of big_chunk_id strings to look up.

        Returns:
            ``{big_chunk_id: {text, section_title, page_start, page_end}}``.
            Missing ids are absent from the result. Empty input returns ``{}``.
        """
        if not big_chunk_ids:
            return {}
        db = SessionLocal()
        try:
            rows = db.query(BigChunk).filter(
                BigChunk.big_chunk_id.in_(big_chunk_ids)
            ).all()
            return {r.big_chunk_id: self._big_chunk_row_to_dict(r) for r in rows}
        except Exception:
            logger.exception("[BigChunk] batch query failed")
            return {}
        finally:
            db.close()

    def _big_chunk_row_to_dict(self, row: BigChunk) -> dict:
        """Convert a BigChunk ORM row to a plain result dict."""
        return {
            "text": row.text,
            "section_title": row.section_title or "",
            "page_start": row.page_start,
            "page_end": row.page_end,
        }

    def get_all_texts(self) -> list[str]:
        """Get all document texts from ChromaDB (for BM25 index building)."""
        try:
            collection = self.db.get()
            return collection.get("documents", [])
        except Exception:
            logger.exception("获取 ChromaDB 文本失败")
            return []

    def export_data(self) -> dict:
        """Export all documents, embeddings, and metadatas from ChromaDB.

        Returns a dict suitable for JSON serialization.
        """
        try:
            # langchain_chroma 1.1.0: 用 Chroma 包装类的公开 get 方法替代 _collection 私有属性
            result = self.db.get(include=["documents", "embeddings", "metadatas"])
            return {
                "ids": result.get("ids", []),
                "documents": result.get("documents", []),
                "embeddings": result.get("embeddings", []),
                "metadatas": result.get("metadatas", []),
            }
        except Exception:
            logger.exception("导出 ChromaDB 数据失败")
            return {"ids": [], "documents": [], "embeddings": [], "metadatas": []}

    def import_data(self, data: dict) -> int:
        """Import documents, embeddings, and metadatas into ChromaDB.

        Args:
            data: Dict with keys 'ids', 'documents', 'embeddings', 'metadatas'.

        Returns:
            Number of documents imported.

        Raises:
            ValueError: If embedding dimensions don't match the current KB's
                       embedding model (P2-5: prevents silent corruption).
        """
        try:
            # langchain_chroma 1.1.0: 用 getattr 容错 _collection 私有属性访问方式变更。
            # import_data 必须用底层 collection.add 传入预计算的 embeddings，
            # Chroma 包装类的 add_texts 不支持 embeddings 参数。
            collection = getattr(self.db, "_collection", None)
            ids = data.get("ids", [])
            documents = data.get("documents", [])
            embeddings = data.get("embeddings", [])
            metadatas = data.get("metadatas", [])

            if not documents:
                return 0

            # Filter out empty documents
            valid_indices = [i for i, doc in enumerate(documents) if doc and doc.strip()]
            if not valid_indices:
                return 0

            valid_ids = [ids[i] if i < len(ids) else str(uuid.uuid4()) for i in valid_indices]
            valid_docs = [documents[i] for i in valid_indices]
            # P0: When embeddings exist but length doesn't match documents, raise
            # instead of silently dropping embeddings (which would cause ChromaDB
            # to re-embed or store vectorless docs — silent corruption).
            if embeddings and len(embeddings) > 0 and len(embeddings) != len(documents):
                raise ValueError(
                    f"Embeddings length mismatch: got {len(embeddings)} embeddings "
                    f"but {len(documents)} documents. Export data may be corrupted."
                )
            valid_embeddings = [embeddings[i] for i in valid_indices] if embeddings and len(embeddings) == len(documents) else None
            valid_metadatas = [metadatas[i] if i < len(metadatas) else {} for i in valid_indices]

            # P2-5: Validate embedding dimensions match the current KB's model.
            # If dimensions mismatch, ChromaDB would accept the data but searches
            # would fail silently (cosine similarity on mismatched vectors = garbage).
            if valid_embeddings and self.embeddings is not None:
                expected_dim = self._get_embedding_dimension()
                if expected_dim is not None:
                    # P1: Check ALL embeddings, not just the first one — a single
                    # None or wrong-dimension vector would corrupt the collection.
                    for idx, emb in enumerate(valid_embeddings):
                        actual_dim = len(emb) if emb is not None else 0
                        if actual_dim != expected_dim:
                            raise ValueError(
                                f"Embedding dimension mismatch at index {idx}: got {actual_dim}d "
                                f"but KB '{self.collection_name}' expects {expected_dim}d "
                                f"(model: {self.embedding_model}). Use the same embedding model "
                                f"or re-embed the documents."
                            )
                else:
                    # P1: API unavailable — warn but don't block import (fail-open)
                    logger.warning(
                        f"[IMPORT] Embedding dimension check skipped (API unavailable) "
                        f"for KB '{self.collection_name}'. Import may corrupt data if "
                        f"dimensions don't match model '{self.embedding_model}'."
                    )

            if collection is not None:
                self._with_retry(
                    collection.add,
                    ids=valid_ids,
                    documents=valid_docs,
                    embeddings=valid_embeddings,
                    metadatas=valid_metadatas,
                )
            else:
                # Fallback: langchain_chroma 1.1.0 不再暴露 _collection 时，
                # 用 Chroma 包装类的 add_texts（会重新 embed，丢失原 embeddings）。
                if valid_embeddings is not None:
                    logger.warning(
                        "[IMPORT] _collection unavailable, pre-computed embeddings "
                        "discarded; texts will be re-embedded by the embedding function."
                    )
                self._with_retry(
                    self.db.add_texts,
                    texts=valid_docs,
                    metadatas=valid_metadatas,
                    ids=valid_ids,
                )
            logger.info(f"导入完成: {len(valid_docs)} chunks")
            return len(valid_docs)
        except ValueError:
            raise  # Re-raise dimension mismatch errors for caller to handle
        except Exception:
            logger.exception("导入 ChromaDB 数据失败")
            return 0

    def _get_embedding_dimension(self) -> Optional[int]:
        """Get the embedding dimension for the current KB's embedding model.

        Caches the result (including failures) to avoid repeated API calls.
        Once a probe has been attempted, subsequent calls return the cached
        value without re-hitting the API — important when the embedding
        service is down, otherwise every import_data call would block on
        a failing probe.
        """
        if not self.embeddings:
            return None

        # P1: Return cached result if we've already probed (success or failure).
        # This avoids re-probing on every import_data call when the API is down.
        if self._dim_check_attempted:
            return self._cached_embedding_dim

        try:
            # Embed a short probe text to determine dimension
            probe = "dimension check"
            vec = self._with_retry(self.embeddings.embed_query, probe)
            dim = len(vec) if vec else None
            self._cached_embedding_dim = dim
            return dim
        except Exception:
            logger.warning(f"Failed to determine embedding dimension for model {self.embedding_model}")
            return None
        finally:
            # P1: Mark probe as attempted regardless of success/failure so we
            # don't retry on every call. Caller can reset by setting
            # _dim_check_attempted = False if they want to re-probe.
            self._dim_check_attempted = True
