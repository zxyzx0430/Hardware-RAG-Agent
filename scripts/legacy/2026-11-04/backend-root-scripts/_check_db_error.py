"""Query DB directly for full error_message of failed docs."""
import sys
sys.path.insert(0, ".")
from app.db.database import SessionLocal
from app.db.models import KnowledgeDoc

db = SessionLocal()
try:
    for kb_id in sys.argv[1:]:
        docs = db.query(KnowledgeDoc).filter(KnowledgeDoc.kb_id == kb_id).all()
        print(f"\n=== KB {kb_id} ({len(docs)} docs) ===")
        for d in docs:
            print(f"  [{d.status:9}] {d.title:35} chunks={d.chunk_count:4} "
                  f"size={d.file_size} method={d.chunk_method_used}")
            if d.status == "error" or d.error_message:
                print(f"      error_message = {d.error_message!r}")
finally:
    db.close()
