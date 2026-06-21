"""
Hardware RAG Agent — SQLAlchemy ORM 模型。

覆盖：
- Session（会话）
- Message（消息）
- KnowledgeDoc（知识库文档记录）
- Bookmark / BookmarkFolder（书签）
- Settings（全局设置）
"""

import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, JSON, Enum, Index
from sqlalchemy.orm import relationship
from app.db.database import Base


class Session(Base):
    """对话会话。"""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("idx_sessions_created", "created_at"),
    )

    id = Column(String, primary_key=True)
    title = Column(String, default="新对话")
    model = Column(String, default="")
    project = Column(String, default="", nullable=True)
    pinned = Column(Boolean, default=False)
    msg_count = Column(Integer, default=0)
    branch_from_session_id = Column(Text, nullable=True)
    branch_from_message_id = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    messages = relationship("Message", back_populates="session", order_by="Message.created_at", cascade="all, delete-orphan")


class Message(Base):
    """会话中的消息。"""

    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role = Column(Enum("user", "assistant", "tool", "system", name="message_role"), nullable=False)
    content = Column(Text, nullable=False)
    sources = Column(JSON, nullable=True)  # 来源引用列表
    tool_calls = Column(JSON, nullable=True)  # 工具调用记录
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    session = relationship("Session", back_populates="messages")


class KnowledgeDoc(Base):
    """知识库文档入库记录。"""

    __tablename__ = "knowledge_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String, unique=True, index=True)  # 文档唯一标识（文件名）
    title = Column(String, default="")
    category = Column(String, default="user_upload")
    file_type = Column(String, default="")  # pdf / md / txt
    file_size = Column(Integer, default=0)  # bytes
    chunk_count = Column(Integer, default=0)
    status = Column(String, default="indexed")  # indexing / indexed / error
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class BookmarkFolder(Base):
    """书签文件夹。"""

    __tablename__ = "bookmark_folders"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    icon = Column(String, default="📁")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    bookmarks = relationship("Bookmark", back_populates="folder", cascade="all, delete-orphan")


class Bookmark(Base):
    """书签（收藏的回答或代码片段）。"""

    __tablename__ = "bookmarks"

    id = Column(String, primary_key=True)
    folder_id = Column(String, ForeignKey("bookmark_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String, default="")
    content = Column(Text, nullable=False)
    content_type = Column(String, default="text")  # text / code / snippet
    source_message_id = Column(String, nullable=True)
    source_session_id = Column(String, nullable=True)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    folder = relationship("BookmarkFolder", back_populates="bookmarks")


class Settings(Base):
    """全局设置键值存储。"""

    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class Feedback(Base):
    """消息反馈（👍/👎）。"""

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1=👍, -1=👎
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
