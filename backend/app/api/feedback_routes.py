from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime

from app.db.database import SessionLocal

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    message_id: str
    session_id: str
    rating: int  # 1=👍, -1=👎


@router.post("")
async def create_feedback(req: FeedbackRequest):
    with SessionLocal() as db:
        db.execute(
            "INSERT INTO feedback (message_id, session_id, rating, created_at) VALUES (?, ?, ?, ?)",
            (req.message_id, req.session_id, req.rating, datetime.now().isoformat()),
        )
        db.commit()
    return {"success": True}


@router.get("/{session_id}")
async def get_feedback(session_id: str):
    with SessionLocal() as db:
        rows = db.execute(
            "SELECT message_id, rating, created_at FROM feedback WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    return {"success": True, "data": [{"message_id": r[0], "rating": r[1], "created_at": r[2]} for r in rows]}
