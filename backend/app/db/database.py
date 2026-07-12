"""
数据库引擎与会话管理。

SQLAlchemy 2.0 async + SQLite（V1），后续可切换 PostgreSQL。
"""

import os
import logging
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

_LOGGER = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DB_DIR = _BACKEND_DIR / "data"
_DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("SQLITE_DB_PATH", str(_DB_DIR / "hardware_rag.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})

# Enable SQLite foreign key constraints on every new connection
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖注入：获取数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建表（首次启动时使用，后续通过 Alembic 迁移）。"""
    import app.db.models  # noqa: ensure models are loaded
    Base.metadata.create_all(bind=engine)
    # 为已有数据库添加分支字段（幂等，列已存在时忽略）
    with engine.connect() as conn:
        for col in ("branch_from_session_id", "branch_from_message_id"):
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE sessions ADD COLUMN {col} TEXT"
                    )
                )
            except Exception as e:
                _LOGGER.debug(f"DB migration skipped: {e}")

        # 为 knowledge_docs 添加 kb_id 和 chunk_method_used（幂等）
        for col_def in ("kb_id TEXT DEFAULT 'builtin-001'", "chunk_method_used TEXT DEFAULT 'hybrid'", "coverage_json TEXT"):
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE knowledge_docs ADD COLUMN {col_def}"
                    )
                )
            except Exception as e:
                _LOGGER.debug(f"DB migration skipped: {e}")

        # 为 messages 添加 activity 列（思考步骤/工具调用活动块，幂等）
        try:
            conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE messages ADD COLUMN activity JSON"
                )
            )
        except Exception as e:
            _LOGGER.debug(f"DB migration skipped: {e}")

        # 为 tool_audit 添加 call_id/success/error_type 列（审计日志增强，幂等）
        for col_def in (
            "call_id VARCHAR(64)",
            "success BOOLEAN DEFAULT 1",
            "error_type VARCHAR(64)",
        ):
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE tool_audit ADD COLUMN {col_def}"
                    )
                )
            except Exception as e:
                _LOGGER.debug(f"DB migration skipped: {e}")

        # 为 knowledge_bases 添加 big_chunk_max_chars 列（ParentDocument 检索模式，幂等）
        try:
            conn.execute(
                __import__("sqlalchemy").text(
                    "ALTER TABLE knowledge_bases ADD COLUMN big_chunk_max_chars INTEGER DEFAULT 4000"
                )
            )
        except Exception as e:
            _LOGGER.debug(f"DB migration skipped: {e}")

        # 为 sessions 添加 context_window 列（每会话上下文窗口配置，幂等）
        try:
            conn.execute(
                __import__("sqlalchemy").text(
                    f"ALTER TABLE sessions ADD COLUMN context_window INTEGER DEFAULT {app.db.models.CONTEXT_WINDOW_256K}"
                )
            )
        except Exception as e:
            _LOGGER.debug(f"DB migration skipped: {e}")

        # 创建 FTS5 虚拟表（用于消息全文搜索，幂等）
        try:
            conn.execute(
                __import__("sqlalchemy").text(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                    USING fts5(content, content='messages', content_rowid='rowid')
                    """
                )
            )
            # 创建触发器：消息插入/更新/删除时同步 FTS 索引
            conn.execute(
                __import__("sqlalchemy").text(
                    """
                    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
                    END
                    """
                )
            )
            conn.execute(
                __import__("sqlalchemy").text(
                    """
                    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
                    END
                    """
                )
            )
            conn.execute(
                __import__("sqlalchemy").text(
                    """
                    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
                        INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
                    END
                    """
                )
            )
        except Exception as e:
            # FTS5 不可用时静默降级（search_routes.py 有 LIKE fallback）
            __import__("logging").getLogger(__name__).warning(f"FTS5 初始化失败，将降级为 LIKE 查询: {e}")

        conn.commit()
