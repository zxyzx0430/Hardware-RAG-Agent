import sys
sys.path.insert(0, "backend")

from src.rag.kb_manager import get_kb_manager
from app.db.models import KnowledgeBase
from app.api.common import get_db_ctx

with get_db_ctx() as db:
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == "builtin-001").first()
    print("DB kb:", kb.id, kb.collection_name, kb.is_builtin, kb.builtin_path)

kbm = get_kb_manager()
kb = kbm.get_kb("builtin-001")
print("Manager kb:", kb.id, kb.collection_name, kb.is_builtin, kb.builtin_path)
store = kbm._get_store(kb)
print("store persist_dir", store.persist_dir if hasattr(store, "persist_dir") else "no attr")
print("store collection", store.collection_name if hasattr(store, "collection_name") else "no attr")
