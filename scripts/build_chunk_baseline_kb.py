"""
为 chunk-baseline-v1 Phase 4 构建测试 KB。
由于当前环境没有配置 embedding API key，使用本地 sentence-transformers 模型
(all-MiniLM-L6-v2) 生成 384 维向量，并创建 hardware-docs-test collection。
"""
import asyncio
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy.orm import Session
from sentence_transformers import SentenceTransformer
import chromadb

from app.db.database import init_db, SessionLocal
from app.db.models import KnowledgeBase, KnowledgeDoc
from src.config.settings import settings
from src.rag.document_processor import UnifiedPdfParser
from src.rag.chunking import get_chunker
from src.rag.chunking.base import ChunkResult


CHROMA_PERSIST_DIR = Path("data/chroma")
KB_ID = "builtin-001"
COLLECTION_NAME = "hardware-docs-test"

PDFS = [
    {
        "path": Path("data/pdfs/interface/ch340g_datasheet.pdf"),
        "doc_id": "12f13dda-4d71-4a4a-9870-fe7e9d7e5062",
        "title": "ch340g_datasheet.pdf",
        "chunk_method": "hybrid",
    },
    {
        "path": Path("data/pdfs/mcu/stm32f4_gpio_exti_extract.pdf"),
        "doc_id": "baseline-stm32f4-gpio-exti",
        "title": "stm32f4_gpio_exti_extract.pdf",
        "chunk_method": "hybrid",
    },
    {
        "path": Path("data/pdfs/mcu/esp32_datasheet.pdf"),
        "doc_id": "a01cb55a-3218-4c85-8115-f153a5916881",
        "title": "esp32_datasheet.pdf",
        "chunk_method": "hybrid",
    },
]


def setup_db():
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(KnowledgeBase).filter(KnowledgeBase.id == KB_ID).first()
        if not existing:
            kb = KnowledgeBase(
                id=KB_ID,
                name="硬件手册库",
                description="chunk-baseline-v1 测试知识库",
                collection_name=COLLECTION_NAME,
                chunk_method="hybrid",
                embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                embedding_base_url="local",
                embedding_api_key_encrypted=None,
                enabled=True,
                is_builtin=True,
            )
            db.add(kb)
            db.commit()
            print(f"Created KB {KB_ID} -> {COLLECTION_NAME}")
        else:
            print(f"KB {KB_ID} already exists")
    finally:
        db.close()


async def chunk_pdf(info: dict, chunker) -> list[ChunkResult]:
    parser = UnifiedPdfParser()
    text, total_pages = parser.parse(info["path"])
    metadata = {
        "doc_id": info["doc_id"],
        "title": info["title"],
        "source": str(info["path"]),
        "total_pages": total_pages,
    }
    chunks = await chunker.chunk(text, metadata, file_path=info["path"], total_pages=total_pages)
    print(f"{info['title']}: {len(chunks)} chunks, {total_pages} pages")
    return chunks, total_pages


def ingest(chunks: list[ChunkResult], doc_id: str):
    client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"kb_id": KB_ID},
    )

    model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = [c.text for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)

    ids = [f"{c.metadata.get('doc_id','doc')}_{i}" for i, c in enumerate(chunks)]
    metadatas = []
    for i, c in enumerate(chunks):
        meta = dict(c.metadata)
        meta["chunk_index"] = i
        meta["start_page"] = c.page_range[0]
        meta["end_page"] = c.page_range[1]
        meta["page_range"] = f"{c.page_range[0]}-{c.page_range[1]}"
        metadatas.append(meta)

    batch = 100
    for i in range(0, len(texts), batch):
        collection.add(
            ids=ids[i:i+batch],
            documents=texts[i:i+batch],
            embeddings=embeddings[i:i+batch].tolist(),
            metadatas=metadatas[i:i+batch],
        )
    print(f"Ingested {len(texts)} chunks into {COLLECTION_NAME}")
    return collection


def save_doc_records(total_pages_map: dict):
    db = SessionLocal()
    try:
        for info in PDFS:
            doc_id = info["doc_id"]
            existing = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
            if not existing:
                rec = KnowledgeDoc(
                    doc_id=doc_id,
                    title=info["title"],
                    kb_id=KB_ID,
                    file_path=str(info["path"]),
                    total_pages=total_pages_map[doc_id],
                    chunk_method_used=info["chunk_method"],
                    status="indexed",
                )
                db.add(rec)
        db.commit()
        print("Saved document records")
    finally:
        db.close()


async def main():
    setup_db()

    # 删除旧 collection 重新建
    client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted old collection {COLLECTION_NAME}")
    except Exception:
        pass

    chunker = get_chunker("hybrid", chunk_size=1000, chunk_overlap=200, small_chunk_size=800)

    all_chunks: list[ChunkResult] = []
    total_pages_map = {}
    for info in PDFS:
        chunks, total_pages = await chunk_pdf(info, chunker)
        total_pages_map[info["doc_id"]] = total_pages
        all_chunks.extend(chunks)

    ingest(all_chunks, "baseline")
    save_doc_records(total_pages_map)

    # 导出 chunks 供人工审核
    output_dir = Path("data/benchmark")
    output_dir.mkdir(parents=True, exist_ok=True)
    import json
    serializable = []
    for i, c in enumerate(all_chunks):
        serializable.append({
            "chunk_index": i,
            "doc_id": c.metadata.get("doc_id"),
            "title": c.metadata.get("title"),
            "page_range": c.page_range,
            "text": c.text,
            "metadata": {k: v for k, v in c.metadata.items() if k not in ("doc_id", "title")},
        })
    with open(output_dir / "chunk_baseline_chunks.json", "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(serializable)} chunks to {output_dir / 'chunk_baseline_chunks.json'}")


if __name__ == "__main__":
    asyncio.run(main())
