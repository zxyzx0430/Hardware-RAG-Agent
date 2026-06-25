"""
ChromaDB 向量存储模块。

功能：
  - 将翻译后的中文 Markdown 文档向量化并入库
  - 提供检索接口（相似度搜索 + 来源标注）
  - 支持按 category 过滤（Week 8 多知识库前置）
  - 持久化到本地磁盘
"""

import re
import uuid
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from src.config.settings import settings
from src.rag.document_loader import CHROMA_DIR
from src.rag.document_processor import ProcessedDocument

logger = logging.getLogger(__name__)


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
    ):
        self.collection_name = collection_name
        self.persist_dir = persist_dir or CHROMA_DIR
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self._embedding_api_key = embedding_api_key
        self._embedding_base_url = embedding_base_url

        # 初始化 Embeddings
        api_key = embedding_api_key or settings.embedding_api_key
        base_url = embedding_base_url or settings.embedding_base_url
        if api_key:
            self.embeddings = OpenAIEmbeddings(
                model=self.embedding_model,
                openai_api_key=api_key,
                openai_api_base=base_url,
                # Disable tiktoken tokenization — send raw text strings instead of token IDs.
                # Non-OpenAI providers (e.g. Alibaba Cloud Bailian/DashScope) reject token ID lists.
                tiktoken_enabled=False,
                check_embedding_ctx_length=False,
                # Limit batch size: Alibaba Cloud Bailian text-embedding-v4 allows max 10 rows per request.
                chunk_size=10,
            )
        else:
            self.embeddings = None

        # 文本分割器（针对中文硬件文档优化）
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n## ", "\n### ", "\n\n", "\n", "。", ".", " ", ""],
            length_function=len,
        )

        # 初始化 Chroma
        self._db: Optional[Chroma] = None

    @property
    def db(self) -> Optional[Chroma]:
        if self._db is None:
            if self.embeddings is None:
                return None
            self._db = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=str(self.persist_dir),
            )
        return self._db

    def _extract_sections(self, markdown: str) -> list[str]:
        """提取 Markdown 中的主要章节（按一级标题 ## 切分）。"""
        sections = re.split(r"\n(?=## )", markdown)
        return [s.strip() for s in sections if s.strip()]

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

    def ingest(self, doc: ProcessedDocument) -> int:
        """
        将一篇处理好的文档入库 ChromaDB。

        流程：
          1. Markdown 文件使用 MarkdownHeaderTextSplitter 先按标题切分
          2. RecursiveCharacterTextSplitter 进一步分块
          3. 每块包含 metadata（来源 URL、文档 ID、章节标题等）
          4. 写入 ChromaDB 持久化

        返回入库的 chunk 数量。
        """
        if self.embeddings is None:
            logger.warning("未配置 embedding API，跳过入库")
            return 0
        md = doc.translated_markdown

        # 判断是否为 Markdown 文件，使用智能分块
        is_markdown = (
            hasattr(doc, 'source') and
            hasattr(doc.source, 'tags') and
            'md' in doc.source.tags
        ) or (
            hasattr(doc, 'pdf_path') and
            doc.pdf_path and
            str(doc.pdf_path).endswith('.md')
        )

        lc_docs: list[LCDocument] = []
        chunk_index = 0

        if is_markdown:
            # Markdown 智能分块：先按标题切分，再二次分块
            headers_to_split_on = [
                ("#", "h1"),
                ("##", "h2"),
                ("###", "h3"),
                ("####", "h4"),
            ]
            md_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split_on,
                strip_headers=False,
            )
            try:
                md_splits = md_splitter.split_text(md)
            except Exception:
                # MarkdownHeaderTextSplitter 解析失败时回退到普通分块
                md_splits = [LCDocument(page_content=md, metadata={})]

            for md_split in md_splits:
                # 提取章节标题
                section_title = ""
                for h_key in ("h1", "h2", "h3", "h4"):
                    if h_key in md_split.metadata:
                        section_title = md_split.metadata[h_key]
                        break

                # 二次分块
                sub_chunks = self.text_splitter.split_text(md_split.page_content)
                for chunk in sub_chunks:
                    if not chunk.strip():
                        continue
                    metadata = self._build_chunk_metadata(doc, chunk_index, section_title)
                    lc_docs.append(LCDocument(page_content=chunk, metadata=metadata))
                    chunk_index += 1
        else:
            # 非 Markdown 文件：使用原有分块逻辑
            sections = self._extract_sections(md)
            for section in sections:
                title_match = re.match(r"##?\s+(.+)", section)
                section_title = title_match.group(1).strip() if title_match else ""
                chunks = self.text_splitter.split_text(section)
                for chunk in chunks:
                    if not chunk.strip():
                        continue
                    metadata = self._build_chunk_metadata(doc, chunk_index, section_title)
                    lc_docs.append(LCDocument(page_content=chunk, metadata=metadata))
                    chunk_index += 1

        # 入库 ChromaDB
        if lc_docs:
            self.db.add_documents(lc_docs)
            logger.info(f"入库完成: {doc.doc_id} → {len(lc_docs)} chunks")
        else:
            logger.warning(f"空文档: {doc.doc_id}")

        return len(lc_docs)

    def ingest_batch(self, docs: list[ProcessedDocument]) -> int:
        """批量入库。"""
        total = 0
        for doc in docs:
            total += self.ingest(doc)
        logger.info(f"总计入库: {total} chunks")
        return total

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
            results = self.db.similarity_search_with_relevance_scores(
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
        """删除指定 doc_id 对应的所有向量，返回删除的 chunk 数量。"""
        try:
            collection = self.db.get(where={"doc_id": doc_id})
            ids_to_delete = collection.get("ids", [])
            if ids_to_delete:
                self.db.delete(ids=ids_to_delete)
            return len(ids_to_delete)
        except Exception as e:
            logger.exception(f"删除文档向量失败")
            return 0

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
        """
        Ingest pre-chunked data (ChunkResult list) into ChromaDB.

        Args:
            chunks: List of ChunkResult objects from chunking module.
            doc_id: Document ID for metadata.

        Returns:
            Number of chunks ingested.
        """
        if self.embeddings is None:
            logger.warning("未配置 embedding API，跳过入库")
            return 0

        from langchain_core.documents import Document as LCDocument

        lc_docs: list[LCDocument] = []
        for chunk in chunks:
            if not chunk.text.strip():
                continue
            metadata = {
                **chunk.metadata,
                "doc_id": doc_id,
                "chunk_method": chunk.chunk_method,
                "fingerprint": chunk.fingerprint,
                "section_title": chunk.section_title,
                "page_start": chunk.page_range[0],
                "page_end": chunk.page_range[1],
            }
            lc_docs.append(LCDocument(page_content=chunk.text, metadata=metadata))

        if lc_docs:
            self.db.add_documents(lc_docs)
            logger.info(f"入库完成: {doc_id} → {len(lc_docs)} chunks")

        return len(lc_docs)

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
            collection = self.db._collection
            result = collection.get(include=["documents", "embeddings", "metadatas"])
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
        """
        try:
            collection = self.db._collection
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
            valid_embeddings = [embeddings[i] for i in valid_indices] if embeddings and len(embeddings) == len(documents) else None
            valid_metadatas = [metadatas[i] if i < len(metadatas) else {} for i in valid_indices]

            collection.add(
                ids=valid_ids,
                documents=valid_docs,
                embeddings=valid_embeddings,
                metadatas=valid_metadatas,
            )
            logger.info(f"导入完成: {len(valid_docs)} chunks")
            return len(valid_docs)
        except Exception:
            logger.exception("导入 ChromaDB 数据失败")
            return 0
