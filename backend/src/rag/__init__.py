"""
RAG 知识库模块。

管线：document_loader → document_processor → vector_store → pipeline
"""

# 延迟导入，避免模块加载链阻塞
# 各子模块应直接 import 使用

__all__ = [
    "DocumentLoader",
    "DocumentSource",
    "FIRST_BATCH",
    "DocumentProcessor",
    "DoclingParser",
    "TranslationPipeline",
    "ProcessedDocument",
    "HardwareVectorStore",
    "SearchResult",
    "KnowledgePipeline",
    "KnowledgeBaseManager",
    "get_kb_manager",
    "BM25Index",
    "rrf_fusion",
    "get_chunker",
    "ChunkResult",
    "BaseChunker",
    "HybridChunker",
    "AgentChunker",
]


def __getattr__(name):
    """惰性导入，仅在访问时加载。"""
    import importlib

    _module_map = {
        "DocumentLoader": "src.rag.document_loader",
        "DocumentSource": "src.rag.document_loader",
        "FIRST_BATCH": "src.rag.document_loader",
        "DocumentProcessor": "src.rag.document_processor",
        "DoclingParser": "src.rag.document_processor",
        "TranslationPipeline": "src.rag.document_processor",
        "ProcessedDocument": "src.rag.document_processor",
        "HardwareVectorStore": "src.rag.vector_store",
        "SearchResult": "src.rag.vector_store",
        "KnowledgePipeline": "src.rag.pipeline",
        "KnowledgeBaseManager": "src.rag.kb_manager",
        "get_kb_manager": "src.rag.kb_manager",
        "BM25Index": "src.rag.kb_manager",
        "rrf_fusion": "src.rag.kb_manager",
        "get_chunker": "src.rag.chunking",
        "ChunkResult": "src.rag.chunking",
        "BaseChunker": "src.rag.chunking",
        "HybridChunker": "src.rag.chunking",
        "AgentChunker": "src.rag.chunking",
    }

    if name in _module_map:
        module = importlib.import_module(_module_map[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
