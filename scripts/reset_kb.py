"""
One-shot KB reset script.

Wipes ALL user-uploaded documents across ALL knowledge bases:
  1. ChromaDB vectors (per KB collection)
  2. KnowledgeDoc DB records (preserves builtin KB row itself)
  3. Uploaded files in data/uploads/

Use this when the DB has accumulated orphan records / duplicate chunks /
stuck indexing state and you want a clean slate to re-upload fresh files
with the new chunker.

Usage (interactive — will prompt for confirmation):
    python scripts/reset_kb.py
    python scripts/reset_kb.py --yes          # skip confirmation prompt
    python scripts/reset_kb.py --kb-id builtin-001   # only reset one KB
"""

import argparse
import os
import sys
from pathlib import Path

# ─── Bootstrap: add backend/ to sys.path ───
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

os.chdir(_BACKEND_DIR)

# ─── Stub sentence_transformers (Python 3.13 OpenBLAS workaround) ───
import types as _types
if "sentence_transformers" not in sys.modules:
    _st_stub = _types.ModuleType("sentence_transformers")
    _st_stub.SentenceTransformer = type("SentenceTransformer", (), {})
    sys.modules["sentence_transformers"] = _st_stub


def list_all_kbs() -> list[dict]:
    """List all knowledge bases."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeBase
    out = []
    with SessionLocal() as db:
        for kb in db.query(KnowledgeBase).order_by(KnowledgeBase.id).all():
            out.append({
                "id": kb.id,
                "name": kb.name,
                "is_builtin": kb.is_builtin,
                "chunk_method": kb.chunk_method,
            })
    return out


def list_docs_in_kb(kb_id: str) -> list[dict]:
    """List all KnowledgeDoc records in a KB."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeDoc
    out = []
    with SessionLocal() as db:
        for r in db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).all():
            out.append({
                "doc_id": r.doc_id,
                "title": r.title,
                "status": r.status,
                "chunk_count": r.chunk_count,
                "file_type": r.file_type,
            })
    return out


def reset_kb(kb_id: str) -> dict:
    """Wipe all documents from a single KB. Returns counts."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from app.api.kb_routes import UPLOAD_DIR, ALLOWED_EXTENSIONS

    result = {"kb_id": kb_id, "docs": 0, "vectors": 0, "files": 0, "errors": []}

    # Collect doc_ids first (need them for vector + file cleanup)
    with SessionLocal() as db:
        records = db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).all()
        doc_ids = [(r.doc_id, r.title) for r in records]

    if not doc_ids:
        return result

    result["docs"] = len(doc_ids)
    print(f"  [{kb_id}] {len(doc_ids)} documents to delete")

    # Step 1: Delete vectors from ChromaDB
    kb_manager = get_kb_manager()
    kb = kb_manager.get_kb(kb_id)
    if kb:
        store = kb_manager._get_store(kb)
        if store:
            for doc_id, _title in doc_ids:
                try:
                    deleted = store.delete_document(doc_id)
                    result["vectors"] += deleted
                except Exception as e:
                    result["errors"].append(f"vector {doc_id}: {e}")
        # Mark BM25 stale
        kb_manager._bm25_stale.add(kb_id)

    # Step 2: Delete uploaded files
    for doc_id, _title in doc_ids:
        for ext in ALLOWED_EXTENSIONS:
            file_path = UPLOAD_DIR / f"{doc_id}{ext}"
            if file_path.exists():
                try:
                    file_path.unlink()
                    result["files"] += 1
                except Exception as e:
                    result["errors"].append(f"file {file_path.name}: {e}")
                break

    # Step 3: Delete DB records
    with SessionLocal() as db:
        db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).delete()
        db.commit()

    return result


def main():
    parser = argparse.ArgumentParser(description="Reset KBs — wipe all uploaded docs")
    parser.add_argument("--kb-id", default=None, help="Only reset this KB (default: all KBs)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("KB Reset — wipe all uploaded documents")
    print(f"{'='*60}\n")

    kbs = list_all_kbs()
    if not kbs:
        print("No knowledge bases found. Nothing to reset.")
        return

    target_kbs = [kb for kb in kbs if not args.kb_id or kb["id"] == args.kb_id]
    if not target_kbs:
        print(f"No KB matched --kb-id={args.kb_id}")
        return

    # Preview what will be deleted
    total_docs = 0
    for kb in target_kbs:
        docs = list_docs_in_kb(kb["id"])
        builtin_tag = " (builtin)" if kb["is_builtin"] else ""
        print(f"  KB {kb['id']}{builtin_tag} — {kb['name']}")
        print(f"    chunk_method: {kb['chunk_method']}, docs: {len(docs)}")
        for d in docs:
            print(f"      - [{d['status']:<8}] {d['title']:<40} ({d['chunk_count']} chunks)")
        total_docs += len(docs)
    print(f"\nTotal documents to delete: {total_docs}")
    print("This will: delete DB records + ChromaDB vectors + uploaded files.")
    print("KB definitions themselves are PRESERVED.\n")

    if not args.yes:
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return

    print()
    total = {"docs": 0, "vectors": 0, "files": 0, "errors": []}
    for kb in target_kbs:
        result = reset_kb(kb["id"])
        for k in ("docs", "vectors", "files"):
            total[k] += result[k]
        total["errors"].extend(result["errors"])
        print(f"  [{kb['id']}] deleted {result['docs']} docs, "
              f"{result['vectors']} vectors, {result['files']} files")

    print(f"\n{'='*60}")
    print(f"Reset complete.")
    print(f"  Documents: {total['docs']}")
    print(f"  Vectors:   {total['vectors']}")
    print(f"  Files:     {total['files']}")
    if total["errors"]:
        print(f"  Errors:    {len(total['errors'])}")
        for e in total["errors"][:10]:
            print(f"    - {e}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
