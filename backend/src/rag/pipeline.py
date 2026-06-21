"""
知识库建设编排管线。

组合 DocumentLoader + DocumentProcessor + HardwareVectorStore
为 CLI 入口 `run_pipeline()` 提供一站式文档下载→解析→翻译→入库流程。
"""

import sys
import asyncio
import argparse
import logging
from pathlib import Path
from typing import Optional

from src.rag.document_loader import (
    DocumentLoader,
    DocumentSource,
    FIRST_BATCH,
    PDF_DIR,
    MARKDOWN_DIR,
)
from src.rag.document_processor import DocumentProcessor
from src.rag.vector_store import HardwareVectorStore


logger = logging.getLogger(__name__)


class KnowledgePipeline:
    """知识库建设全流程编排。"""

    def __init__(
        self,
        loader: Optional[DocumentLoader] = None,
        processor: Optional[DocumentProcessor] = None,
        vector_store: Optional[HardwareVectorStore] = None,
    ):
        self.loader = loader or DocumentLoader()
        self.processor = processor or DocumentProcessor()
        self.vector_store = vector_store or HardwareVectorStore()

    async def run(
        self,
        sources: list[DocumentSource],
        force_download: bool = False,
        force_process: bool = False,
        skip_download: bool = False,
        skip_translate: bool = False,
        skip_ingest: bool = False,
        max_concurrent: int = 3,
    ) -> dict:
        """
        执行完整管线。

        Args:
            sources: 文档源列表
            force_download: 强制重新下载
            force_process: 强制重新处理
            skip_download: 跳过下载（使用本地已有 PDF）
            skip_translate: 跳过翻译（使用本地已有 Markdown）
            skip_ingest: 跳过入库
        """
        report = {
            "total": len(sources),
            "downloaded": 0,
            "processed": 0,
            "ingested": 0,
            "errors": [],
        }

        # ════════════════════════
        # Step 1: 下载 PDF
        # ════════════════════════
        if skip_download:
            pdf_paths = {
                s.doc_id: PDF_DIR / f"{s.doc_id}.pdf"
                for s in sources
                if (PDF_DIR / f"{s.doc_id}.pdf").exists()
            }
            logger.info("跳过下载，使用本地 %d 篇 PDF", len(pdf_paths))
        else:
            # 过滤已下载
            to_download = sources
            if not force_download:
                to_download = [
                    s for s in sources if not self.loader.is_downloaded(s.doc_id)
                ]
                skipped = len(sources) - len(to_download)
                if skipped:
                    logger.info("跳过 %d 篇已下载文档", skipped)

            if to_download:
                print(f"\n{'='*60}")
                logger.info("Step 1: 下载 %d 篇 PDF", len(to_download))
                print(f"{'='*60}")
                try:
                    pdf_paths = await self.loader.download_batch(
                        to_download, max_concurrent=max_concurrent
                    )
                finally:
                    await self.loader.close()
                # 加上已下载的
                if not force_download:
                    for s in sources:
                        if s.doc_id not in pdf_paths:
                            p = PDF_DIR / f"{s.doc_id}.pdf"
                            if p.exists():
                                pdf_paths[s.doc_id] = p
            else:
                pdf_paths = {}
                for s in sources:
                    p = PDF_DIR / f"{s.doc_id}.pdf"
                    if p.exists():
                        pdf_paths[s.doc_id] = p

            report["downloaded"] = len(pdf_paths)

        # ════════════════════════
        # Step 2: 解析 + 翻译
        # ════════════════════════
        if skip_translate:
            processed_docs = []
            for s in sources:
                from src.rag.document_processor import ProcessedDocument
                md_path = MARKDOWN_DIR / f"{s.doc_id}.md"
                if md_path.exists():
                    processed_docs.append(
                        ProcessedDocument(
                            doc_id=s.doc_id,
                            source=s,
                            pdf_path=pdf_paths.get(s.doc_id, Path()),
                            raw_markdown="",
                            translated_markdown=md_path.read_text(encoding="utf-8"),
                            metadata={
                                "doc_id": s.doc_id,
                                "title": s.title,
                                "category": s.category,
                                "source_url": s.url,
                                "tags": s.tags,
                                "last_updated": s.last_updated,
                                "processed_at": "2026-06-15",
                            },
                        )
                    )
            logger.info("跳过翻译，使用本地 %d 篇 Markdown", len(processed_docs))
        else:
            print(f"\n{'='*60}")
            logger.info("Step 2: 解析 PDF + LLM 翻译")
            print(f"{'='*60}")
            processed_docs = await self.processor.process_batch(
                pdf_paths, sources, force=force_process
            )
            report["processed"] = len(processed_docs)

        # ════════════════════════
        # Step 3: 入库 ChromaDB
        # ════════════════════════
        if not skip_ingest and processed_docs:
            logger.info("Step 3: 入库 ChromaDB")
            total_chunks = self.vector_store.ingest_batch(processed_docs)
            report["ingested"] = total_chunks

            # 输出统计
            stats = self.vector_store.get_collection_stats()
            logger.info("知识库统计:")
            logger.info("  Collection: %s", stats["collection"])
            logger.info("  总 Chunks: %d", stats["total_chunks"])
            if stats["categories"]:
                logger.info("  分类分布:")
                for cat, count in sorted(stats["categories"].items()):
                    logger.info("    - %s: %d", cat, count)

        elif skip_ingest:
            logger.info("跳注入库")

        return report

    async def run_first_batch(self, **kwargs) -> dict:
        """运行首批 10 篇文档管线的快捷方法。"""
        return await self.run(FIRST_BATCH, **kwargs)


# ════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="硬件知识库建设管线")
    parser.add_argument("--force-download", action="store_true", help="强制重新下载")
    parser.add_argument("--force-process", action="store_true", help="强制重新处理")
    parser.add_argument("--skip-download", action="store_true", help="跳过下载")
    parser.add_argument("--skip-translate", action="store_true", help="跳过翻译")
    parser.add_argument("--skip-ingest", action="store_true", help="跳注入库")
    parser.add_argument("--concurrent", type=int, default=3, help="最大并发下载数")
    args = parser.parse_args()

    pipeline = KnowledgePipeline()
    report = await pipeline.run_first_batch(
        force_download=args.force_download,
        force_process=args.force_process,
        skip_download=args.skip_download,
        skip_translate=args.skip_translate,
        skip_ingest=args.skip_ingest,
        max_concurrent=args.concurrent,
    )

    print(f"\n{'='*60}")
    logger.info("管线执行完毕")
    logger.info("  总计: %d 篇", report["total"])
    logger.info("  下载: %d 篇", report["downloaded"])
    logger.info("  翻译: %d 篇", report["processed"])
    logger.info("  入库: %d chunks", report["ingested"])
    if report["errors"]:
        logger.info("  错误: %d", len(report["errors"]))
        for err in report["errors"]:
            logger.info("    - %s", err)
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
