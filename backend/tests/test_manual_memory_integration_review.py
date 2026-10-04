"""Integration review against real, expiring SQLAlchemy settings sessions."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat_helpers, common
from app.api.chat_routes import ChatRequest
from app.db.database import Base
from app.db.database import get_db
from app.db.models import Settings


def test_saved_memory_is_materialized_before_session_closes(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    monkeypatch.setattr(common, "SessionLocal", factory)
    try:
        for value in ("Synthetic preference A.", "Synthetic preference B.", ""):
            with factory.begin() as db:
                row = db.query(Settings).filter(Settings.key == "longTermMemory").first()
                if row is None:
                    db.add(Settings(key="longTermMemory", value=value))
                else:
                    row.value = value
            request = ChatRequest(messages=[{"role": "user", "content": "Synthetic question"}])
            snapshot = chat_helpers.resolve_long_term_memory(request)
            assert snapshot == value
            prompt = chat_helpers._build_system_prompt(request, [], "", snapshot)
            assert (prompt.count(value) == 1) if value else "用户手动长期记忆" not in prompt
    finally:
        engine.dispose()


def test_explicit_empty_memory_bypasses_real_saved_preference(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(common, "SessionLocal", factory)
    try:
        with factory.begin() as db:
            db.add(Settings(key="longTermMemory", value="Synthetic stored preference"))
        request = ChatRequest(
            messages=[{"role": "user", "content": "Synthetic question"}],
            long_term_memory="",
        )
        assert chat_helpers.resolve_long_term_memory(request) == ""
    finally:
        engine.dispose()


def test_settings_to_fallback_chat_sse_uses_current_memory_once(monkeypatch):
    """Real settings DB and HTTP/SSE routes, with only the model replaced."""
    from app.api import chat_routes
    from app.api.crud import db_router
    from app.api.dependencies import current_user, current_user_optional
    from src.llm.client import StreamChunk

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    monkeypatch.setattr(common, "SessionLocal", factory)
    prompts = []

    class SyntheticModel:
        async def chat_stream(self, **kwargs):
            prompts.append(kwargs["system_prompt"])
            yield StreamChunk(type="text", content="Synthetic answer")

    monkeypatch.setattr(chat_routes, "make_client", lambda **kwargs: SyntheticModel())
    monkeypatch.setattr(chat_routes, "_AGENT_PATH_AVAILABLE", False)
    monkeypatch.setattr(chat_routes, "resolve_credentials", lambda *args: {
        "api_key": "synthetic-no-network", "base_url": "http://invalid.test",
        "model": "synthetic", "provider": "synthetic",
    })
    app = FastAPI()
    app.include_router(db_router)
    app.include_router(chat_routes.router)

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: {"anonymous": True}
    app.dependency_overrides[current_user_optional] = lambda: {"anonymous": True}
    try:
        with TestClient(app) as client:
            for memory in ("Synthetic preference A.", "Synthetic preference B.", ""):
                assert client.put("/api/settings", json={"longTermMemory": memory}).status_code == 200
                response = client.post("/api/chat", json={
                    "messages": [{"role": "user", "content": "Synthetic question"}],
                })
                assert response.status_code == 200
                assert '"type": "done"' in response.text
                assert '"success": true' in response.text
                prompt = prompts[-1]
                assert (prompt.count(memory) == 1) if memory else "用户手动长期记忆" not in prompt
                assert "Synthetic preference" not in response.text
            assert "Synthetic preference A." not in prompts[1]
    finally:
        engine.dispose()
