from fastapi import APIRouter
from pydantic import BaseModel

from src.agent.session_search import search_messages

router = APIRouter(prefix="/api/search", tags=["search"])

CONTENT_PREVIEW_CHARS = 200


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


@router.post("")
def search(req: SearchRequest):
    results = [
        {
            "message_id": hit["message_id"],
            "session_id": hit["session_id"],
            "role": hit["role"],
            "content": hit["content"][:CONTENT_PREVIEW_CHARS],
            "created_at": hit["created_at"],
            "seq": hit["seq"],
            "session_title": hit["session_title"],
        }
        for hit in search_messages(req.query, req.limit)
    ]
    return {"success": True, "data": {"results": results, "total": len(results)}}
