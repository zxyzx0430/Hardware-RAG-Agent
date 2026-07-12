"""Query ch340g doc_id/kb_id and KB API key status."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "data" / "hardware_rag.db"


def main():
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    print("=== ch340g docs ===")
    rows = cur.execute(
        "SELECT doc_id, kb_id, title, status, chunk_count, chunk_method_used "
        "FROM knowledge_docs WHERE LOWER(title) LIKE '%ch340g%'"
    ).fetchall()
    for r in rows:
        print(f"  doc_id={r[0]} kb_id={r[1]} title={r[2]} status={r[3]} chunks={r[4]} method={r[5]}")

    if not rows:
        print("  (none found)")
        conn.close()
        return

    print("\n=== All KBs ===")
    kbs = cur.execute(
        "SELECT id, name, chunk_method, agent_chunker_model, agent_chunker_base_url, "
        "CASE WHEN agent_chunker_api_key_encrypted IS NOT NULL THEN 'YES' ELSE 'NO' END "
        "FROM knowledge_bases"
    ).fetchall()
    for kb in kbs:
        print(f"  id={kb[0]} name={kb[1]} method={kb[2]} model={kb[3]} base_url={kb[4]} has_agent_key={kb[5]}")

    conn.close()


if __name__ == "__main__":
    main()
