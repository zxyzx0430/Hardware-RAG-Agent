"""List all knowledge_docs records and identify the 3 audit cases."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "hardware_rag.db"

def main():
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT doc_id, kb_id, title, chunk_count, chunk_method_used, status
        FROM knowledge_docs
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    conn.close()

    print(f"Total docs: {len(rows)}\n")
    print(f"{'doc_id':<40} {'kb_id':<15} {'chunks':>6} {'method':<12} {'status':<10} title")
    print("-" * 120)
    for r in rows:
        doc_id, kb_id, title, chunks, method, status = r
        # Mark 3 audit cases
        marker = ""
        if doc_id.startswith("32abb79e") or "ch340g" in (title or "").lower():
            marker = " ← CASE 1 (ch340g, multimodal)"
        elif doc_id.startswith("cecbacc8"):
            marker = " ← CASE 2 (STM32 GPIO, hybrid)"
        elif "06-chaotic" in (title or "").lower() or kb_id == "kb-567e2118":
            marker = " ← CASE 3 (06-chaotic-embedded-notes, agent)"
        print(f"{doc_id[:38]:<40} {kb_id:<15} {str(chunks or 0):>6} {method or '':<12} {status or '':<10} {(title or '')[:40]}{marker}")

if __name__ == "__main__":
    main()
