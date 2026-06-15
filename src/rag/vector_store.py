"""
ChromaDB 向量存储模块。

功能：
  - 将翻译后的中文 Markdown 文档向量化并入库
  - 提供检索接口（相似度搜索 + 来源标注）
  - 支持按 category 过滤（Week 8 多知识库前置）
  - 持久化到本地磁盘
"""

import re
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document as LCDocument
from langchain.text_splitter import RecursiveCharacterTextSplitter

from src.config.settings import settings
from src.rag.document_loader import CHROMA_DIR
from src.rag.document_processor import ProcessedDocument


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
        embedding_model: str = "text-embedding-3-small",
    ):
        self.collection_name = collection_name
        self.persist_dir = persist_dir or CHROMA_DIR
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model

        # 初始化 Embeddings
        self.embeddings = OpenAIEmbeddings(
            model=self.embedding_model,
            openai_api_key=settings.llm_api_key,
            openai_api_base=settings.llm_base_url,
        )

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
    def db(self) -> Chroma:
        if self._db is None:
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
        return {
            "doc_id": doc.doc_id,
            "title": doc.source.title,
            "category": doc.source.category,
            "source_url": doc.source.url,
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
          1. 按标题切分文档为 sections
          2. RecursiveCharacterTextSplitter 进一步分块
          3. 每块包含 metadata（来源 URL、文档 ID、章节标题等）
          4. 写入 ChromaDB 持久化

        返回入库的 chunk 数量。
        """
        md = doc.translated_markdown
        sections = self._extract_sections(md)

        lc_docs: list[LCDocument] = []
        chunk_index = 0

        for section in sections:
            # 提取章节标题
            title_match = re.match(r"##?\s+(.+)", section)
            section_title = title_match.group(1).strip() if title_match else ""

            # 分块
            chunks = self.text_splitter.split_text(section)
            for chunk in chunks:
                if not chunk.strip():
                    continue
                metadata = self._build_chunk_metadata(
                    doc, chunk_index, section_title
                )
                lc_docs.append(LCDocument(page_content=chunk, metadata=metadata))
                chunk_index += 1

        # 入库 ChromaDB
        if lc_docs:
            self.db.add_documents(lc_docs)
            print(
                f"  📥 入库完成: {doc.doc_id} → {len(lc_docs)} chunks"
            )
        else:
            print(f"  ⚠️  空文档: {doc.doc_id}")

        return len(lc_docs)

    def ingest_batch(self, docs: list[ProcessedDocument]) -> int:
        """批量入库。"""
        total = 0
        for doc in docs:
            total += self.ingest(doc)
        print(f"\n  📊 总计入库: {total} chunks")
        return total

    def search(
        self,
        query: str,
        k: int = 5,
        category: Optional[str] = None,
        score_threshold: float = 0.0,
    ) -> list[SearchResult]:
        """
        相似度搜索。

        Args:
            query: 查询文本
            k: 返回结果数量
            category: 按分类过滤（如 "sensors"）
            score_threshold: 相似度阈值
        """
        filter_dict = None
        if category:
            filter_dict = {"category": category}

        results = self.db.similarity_search_with_relevance_scores(
            query,
            k=k,
            filter=filter_dict,
            score_threshold=score_threshold,
        )

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

    def delete_collection(self):
        """清空当前 collection。"""
        try:
            self.db.delete_collection()
            self._db = None
            print(f"  🗑️  已清空 collection: {self.collection_name}")
        except Exception as e:
            print(f"  ⚠️  清空失败: {e}")
