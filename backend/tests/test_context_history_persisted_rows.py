"""Regression: persisted history rows must be materialized before commit expires them."""

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import context_history
from app.db.database import Base
from app.db.models import Message as MessageModel
from app.db.models import Session as SessionModel


def test_persisted_rows_survives_commit_expiry(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with factory() as db:
        db.add(SessionModel(id="s1", title="A", model="test"))
        db.add(MessageModel(id="m1", session_id="s1", role="user", content="hi", seq=1))
        db.add(MessageModel(id="m2", session_id="s1", role="assistant", content="yo", seq=2))
        db.commit()

    @contextmanager
    def _ctx():
        db = factory()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    monkeypatch.setattr(context_history, "get_db_ctx", _ctx)
    monkeypatch.setattr(context_history, "require_session_owner", lambda *args, **kwargs: None)

    rows = context_history._persisted_rows("s1", {"anonymous": True})

    assert rows == [("user", "hi", "m1"), ("assistant", "yo", "m2")]
    engine.dispose()
