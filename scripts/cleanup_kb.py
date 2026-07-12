"""
Interactive cleanup script for knowledge base documents.

Lists all documents in a KB, lets the user pick which ones to delete,
then removes: ChromaDB vectors → DB record → uploaded file.

Usage:
    python scripts/cleanup_kb.py [--kb-id builtin-001]
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

# ─── Stub sentence_transformers (same as test_chunker.py) ───
import types as _types
if "sentence_transformers" not in sys.modules:
    _st_stub = _types.ModuleType("sentence_transformers")
    _st_stub.SentenceTransformer = type("SentenceTransformer", (), {})
    sys.modules["sentence_transformers"] = _st_stub


def list_documents(kb_id: str) -> list[dict]:
    """List all documents in a KB. Returns list of {doc_id, title, chunk_count, status, file_type}."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeDoc

    docs = []
    with SessionLocal() as db:
        records = (
            db.query(KnowledgeDoc)
            .filter(KnowledgeDoc.kb_id == kb_id)
            .order_by(KnowledgeDoc.created_at.desc())
            .all()
        )
        for r in records:
            docs.append({
                "doc_id": r.doc_id,
                "title": r.title,
                "chunk_count": r.chunk_count,
                "status": r.status,
                "file_type": r.file_type,
                "file_size": r.file_size,
            })
    return docs


def delete_document(kb_id: str, doc_id: str) -> dict:
    """Delete a document: ChromaDB vectors → DB record → uploaded file."""
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeDoc
    from src.rag.kb_manager import get_kb_manager
    from app.api.kb_routes import UPLOAD_DIR, ALLOWED_EXTENSIONS

    result = {"doc_id": doc_id, "deleted_chunks": 0, "deleted_file": False, "deleted_record": False}

    with SessionLocal() as db:
        record = db.query(KnowledgeDoc).filter(KnowledgeDoc.doc_id == doc_id).first()
        if not record:
            result["error"] = "DB record not found"
            return result

        # Step 1: Delete vectors from ChromaDB
        kb_manager = get_kb_manager()
        kb = kb_manager.get_kb(kb_id) if kb_id else None
        if kb:
            store = kb_manager._get_store(kb)
            if store:
                try:
                    result["deleted_chunks"] = store.delete_document(doc_id)
                except Exception as e:
                    result["error"] = f"Vector deletion failed: {e}"
                    return result

        # Step 2: Delete uploaded file
        for ext in ALLOWED_EXTENSIONS:
            file_path = UPLOAD_DIR / f"{doc_id}{ext}"
            if file_path.exists():
                file_path.unlink()
                result["deleted_file"] = True
                break

        # Step 3: Delete DB record
        db.delete(record)
        db.commit()
        result["deleted_record"] = True

        # Mark BM25 stale
        if kb_id:
            kb_manager._bm25_stale.add(kb_id)

    return result


def main():
    parser = argparse.ArgumentParser(description="Clean up KB documents interactively")
    parser.add_argument("--kb-id", default="builtin-001", help="Knowledge base ID (default: builtin-001)")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"KB Cleanup — {args.kb_id}")
    print(f"{'='*60}\n")

    docs = list_documents(args.kb_id)
    if not docs:
        print("No documents found in this KB.")
        return

    # Display documents
    print(f"{'#':<4} {'Title':<40} {'Chunks':<8} {'Status':<10} {'Size'}")
    print(f"{'-'*4} {'-'*40} {'-'*8} {'-'*10} {'-'*8}")
    for i, d in enumerate(docs):
        title = d["title"][:38] + ".." if len(d["title"]) > 40 else d["title"]
        size_kb = f"{d['file_size'] // 1024}KB" if d.get("file_size") else "?"
        print(f"{i:<4} {title:<40} {d['chunk_count']:<8} {d['status']:<10} {size_kb}")

    # Interactive selection
    print(f"\nEnter the numbers of documents to delete (comma-separated, e.g. 0,1,2)")
    print(f"Type 'all' to delete everything, or press Enter to cancel:")
    selection = input("> ").strip()

    if not selection:
        print("Cancelled.")
        return

    if selection.lower() == "all":
        indices = list(range(len(docs)))
    else:
        try:
            indices = [int(x.strip()) for x in selection.split(",")]
        except ValueError:
            print("Invalid input. Please enter comma-separated numbers.")
            return

    to_delete = []
    for idx in indices:
        if 0 <= idx < len(docs):
            to_delete.append(docs[idx])
        else:
            print(f"  Skipping invalid index: {idx}")

    if not to_delete:
        print("Nothing to delete.")
        return

    # Confirm
    print(f"\n{'='*60}")
    print(f"Will delete {len(to_delete)} document(s):")
    for d in to_delete:
        print(f"  • {d['title']} ({d['chunk_count']} chunks, {d['doc_id']})")
    print(f"\nType 'yes' to confirm, anything else to cancel:")
    confirm = input("> ").strip().lower()

    if confirm != "yes":
        print("Cancelled.")
        return

    # Delete
    print(f"\n{'='*60}")
    success = 0
    failed = 0
    for d in to_delete:
        print(f"\nDeleting: {d['title']}")
        result = delete_document(args.kb_id, d["doc_id"])
        if result.get("deleted_record"):
            print(f"  ✅ Vectors: {result['deleted_chunks']} chunks deleted")
            print(f"  ✅ File: {'deleted' if result['deleted_file'] else 'not found'}")
            print(f"  ✅ DB record: deleted")
            success += 1
        else:
            print(f"  ❌ Failed: {result.get('error', 'unknown error')}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Done: {success} deleted, {failed} failed")
    print(f"\nNow re-upload the files to chunk them with the new HybridChunker.")


if __name__ == "__main__":
    main()
