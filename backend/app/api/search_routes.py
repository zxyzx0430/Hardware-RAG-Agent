from fastapi import APIRouter
from pydantic import BaseModel

from app.db.database import SessionLocal

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


@router.post("")
async def search(req: SearchRequest):
    with SessionLocal() as db:
        # 尝试使用 SQLite FTS5 全文搜索
        try:
            rows = db.execute(
                """
                SELECT m.id, m.session_id, m.role, m.content, s.title
                FROM messages_fts f
                JOIN messages m ON m.id = f.rowid
                JOIN sessions s ON m.session_id = s.id
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (req.query, req.limit),
            ).fetchall()
        except Exception:
            # FTS5 不可用时 fallback 到 LIKE
            rows = db.execute(
                """
                SELECT m.id, m.session_id, m.role, m.content, s.title
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE m.content LIKE ?
                LIMIT ?
                """,
                (f"%{req.query}%", req.limit),
            ).fetchall()

    results = []
    for r in rows:
        content = r[3] or ""
        results.append({
            "message_id": r[0],
            "session_id": r[1],
            "role": r[2],
            "content": content[:200],
            "session_title": r[4],
        })
    return {"success": True, "data": {"results": results, "total": len(results)}}
