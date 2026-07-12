"""Investigate current chunk status of knowledge_docs + ChromaDB.

Read-only: does NOT modify databases. Uses only Python stdlib sqlite3.
Exports per-case chunks to scripts/audit/case{N}_<doc_id_prefix8>.jsonl.

Usage:
    python scripts/audit/_investigate_chunks.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "backend" / "data" / "hardware_rag.db"
CHROMA_SQLITE = ROOT / "backend" / "data" / "chroma_db" / "chroma.sqlite3"
AUDIT_DIR = ROOT / "scripts" / "audit"

CASE_DOC_ID_PREFIXES = ("32abb79e", "cecbacc8")  # ch340g, STM32 GPIO
CASE_TITLE_KEYWORD = "00"  # 00号文件 by title prefix


def list_knowledge_docs(cur: sqlite3.Cursor) -> list[dict]:
    rows = cur.execute(
        "SELECT doc_id, kb_id, title, chunk_count, chunk_method_used, status "
        "FROM knowledge_docs ORDER BY kb_id, title"
    ).fetchall()
    return [
        {
            "doc_id": r[0],
            "doc_id_prefix": r[0][:8] if r[0] else "",
            "kb_id": r[1],
            "title": r[2] or "",
            "chunk_count": r[3],
            "chunk_method_used": r[4],
            "status": r[5],
        }
        for r in rows
    ]


def list_collections(cur: sqlite3.Cursor) -> list[dict]:
    rows = cur.execute(
        "SELECT id, name, dimension FROM collections ORDER BY name"
    ).fetchall()
    return [{"id": r[0], "name": r[1], "dimension": r[2]} for r in rows]


def list_metadata_segments(cur: sqlite3.Cursor) -> dict[str, str]:
    """Return {collection_id: metadata_segment_id}."""
    rows = cur.execute(
        "SELECT collection, id FROM segments WHERE scope='METADATA'"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def load_chunks_for_segment(cur: sqlite3.Cursor, segment_id: str) -> list[dict]:
    """Load all chunks (embedding_id + metadata) for one metadata segment."""
    emb_rows = cur.execute(
        "SELECT id, embedding_id FROM embeddings WHERE segment_id=?",
        (segment_id,),
    ).fetchall()
    chunks: list[dict] = []
    for emb_pk, embedding_id in emb_rows:
        meta_rows = cur.execute(
            "SELECT key, string_value, int_value, float_value, bool_value "
            "FROM embedding_metadata WHERE id=?",
            (emb_pk,),
        ).fetchall()
        meta: dict = {}
        document = ""
        for key, sv, iv, fv, bv in meta_rows:
            val = sv if sv is not None else (
                iv if iv is not None else (fv if fv is not None else bool(bv))
            )
            if key == "chroma:document":
                document = sv or ""
            else:
                meta[key] = val
        chunks.append(
            {
                "id": embedding_id,
                "segment_id": segment_id,
                "document": document,
                "metadata": meta,
            }
        )
    return chunks


def scan_all_chunks(cur: sqlite3.Cursor) -> dict[str, list[dict]]:
    """Return {collection_name: [chunks]} for all collections."""
    seg_map = list_metadata_segments(cur)
    colls = list_collections(cur)
    result: dict[str, list[dict]] = {}
    for coll in colls:
        seg_id = seg_map.get(coll["id"])
        if not seg_id:
            result[coll["name"]] = []
            continue
        chunks = load_chunks_for_segment(cur, seg_id)
        for ch in chunks:
            ch["collection"] = coll["name"]
        result[coll["name"]] = chunks
    return result


def content_type_distribution(chunks: list[dict]) -> Counter:
    c = Counter()
    for ch in chunks:
        meta = ch.get("metadata") or {}
        ct = meta.get("content_type") or meta.get("type") or "text"
        c[ct] += 1
    return c


def group_chunks_by_doc(chunks_by_coll: dict[str, list[dict]]) -> dict[str, list[dict]]:
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for coll_name, chunks in chunks_by_coll.items():
        for ch in chunks:
            doc_id = (ch.get("metadata") or {}).get("doc_id", "")
            by_doc[doc_id].append(ch)
    return by_doc


def find_cases(docs: list[dict]) -> list[dict]:
    cases = []
    for d in docs:
        prefix = d["doc_id_prefix"]
        title = d["title"] or ""
        is_case1 = prefix in CASE_DOC_ID_PREFIXES
        is_case2 = title.startswith(CASE_TITLE_KEYWORD) and "00" in title[:3]
        if is_case1 or is_case2:
            cases.append({"case_no": len(cases) + 1, **d})
    return cases


def export_case(case: dict, chunks: list[dict]) -> Path:
    fname = f"case{case['case_no']}_{case['doc_id_prefix']}.jsonl"
    out = AUDIT_DIR / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")
    return out


def print_section(title: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: hardware_rag.db not found at {DB_PATH}")
        return 1
    if not CHROMA_SQLITE.exists():
        print(f"ERROR: chroma.sqlite3 not found at {CHROMA_SQLITE}")
        return 1

    rag_conn = sqlite3.connect(str(DB_PATH))
    chroma_conn = sqlite3.connect(str(CHROMA_SQLITE))
    rag_cur = rag_conn.cursor()
    chroma_cur = chroma_conn.cursor()

    print_section("Step 1: All knowledge_docs records")
    docs = list_knowledge_docs(rag_cur)
    print(f"Total docs: {len(docs)}\n")
    print(f"{'doc_id':38} {'kb_id':16} {'chk':>5} {'method':12} {'status':10} title")
    for d in docs:
        print(
            f"{d['doc_id'][:36]:38} {d['kb_id']:16} {d['chunk_count']:>5} "
            f"{d['chunk_method_used']:12} {d['status']:10} {d['title']}"
        )

    print_section("Step 2: ChromaDB collections + chunks per doc")
    chunks_by_coll = scan_all_chunks(chroma_cur)
    print(f"\nCollections: {len(chunks_by_coll)}")
    for coll_name, chunks in chunks_by_coll.items():
        print(f"  {coll_name:40} chunks={len(chunks):>5}")

    by_doc = group_chunks_by_doc(chunks_by_coll)
    print(f"\nUnique doc_ids in ChromaDB: {len(by_doc)}")
    doc_lookup = {d["doc_id"]: d for d in docs}
    print(f"\n{'doc_id':38} {'chk':>5} {'content_type':28} title")
    for doc_id, chunks in sorted(by_doc.items(), key=lambda kv: -len(kv[1])):
        ct = content_type_distribution(chunks)
        title = doc_lookup.get(doc_id, {}).get("title", "?")
        print(f"{doc_id[:36]:38} {len(chunks):>5} {str(dict(ct)):28} {title}")

    print_section("Step 3: Locate 3 case studies")
    cases = find_cases(docs)
    print(f"\nFound {len(cases)} cases:")
    for c in cases:
        print(
            f"  case#{c['case_no']} doc_id={c['doc_id']} kb_id={c['kb_id']} "
            f"title={c['title']} method={c['chunk_method_used']} "
            f"db_chunks={c['chunk_count']} status={c['status']}"
        )

    print_section("Step 4: Export each case to JSONL")
    for c in cases:
        chunks = by_doc.get(c["doc_id"], [])
        ct = content_type_distribution(chunks)
        out = export_case(c, chunks)
        print(f"\n  case#{c['case_no']} {c['title']}  (doc_id={c['doc_id']})")
        print(f"    DB chunk_count:    {c['chunk_count']}")
        print(f"    Chroma chunks:     {len(chunks)}")
        print(f"    content_type dist: {dict(ct)}")
        print(f"    Exported to:       {out.relative_to(ROOT)}")

    rag_conn.close()
    chroma_conn.close()
    print("\nDone. Read-only investigation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
