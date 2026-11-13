"""RAG reranker control routes: load status and in-process hot reload.

Status reads never wait for the model loader. Reload starts a daemon thread
and returns immediately, so the UI can trigger it without restarting the
backend or blocking the request.
"""
from fastapi import APIRouter, Depends

from app.api.dependencies import current_user
from src.rag.reranker import get_reranker_status, reload_reranker

router = APIRouter(prefix="/api/rag", tags=["rag"])


@router.get("/reranker")
def reranker_status(user: dict = Depends(current_user)) -> dict:
    """Return the cross-encoder reranker load status."""
    return {"success": True, "data": get_reranker_status()}


@router.post("/reranker/reload")
def reranker_reload(user: dict = Depends(current_user)) -> dict:
    """Start a background reload; never waits for the cold model load."""
    return {"success": True, "data": reload_reranker()}
