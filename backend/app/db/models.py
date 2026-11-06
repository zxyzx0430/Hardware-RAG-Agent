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
import uuid
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, Float, DateTime, ForeignKey, JSON, Enum, Index, CheckConstraint, UniqueConstraint, LargeBinary
from sqlalchemy.orm import relationship
from app.db.database import Base

# Context window presets (tokens) — per-session context budget
CONTEXT_WINDOW_256K = 262144
CONTEXT_WINDOW_1M = 1048576


class Session(Base):
    """对话会话。"""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("idx_sessions_created", "created_at"),
        CheckConstraint("message_revision >= 0", name="ck_sessions_message_revision_non_negative"),
    )

    id = Column(String, primary_key=True)
    title = Column(String, default="新对话")
    model = Column(String, default="")
    project = Column(String, default="", nullable=True)
    pinned = Column(Boolean, default=False)
    msg_count = Column(Integer, default=0)
    message_revision = Column(Integer, nullable=False, default=0, server_default="0")
    branch_from_session_id = Column(Text, nullable=True)
    branch_from_message_id = Column(Text, nullable=True)
    context_window = Column(Integer, default=CONTEXT_WINDOW_256K)  # per-session context budget (tokens)
    version = Column(Integer, default=0, nullable=False)  # bumped on every message/session mutation
    messages_imported = Column(Integer, default=0, nullable=False)  # one-shot legacy import guard
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), onupdate=datetime.datetime.now(datetime.timezone.utc))

    messages = relationship("Message", back_populates="session", order_by="(Message.created_at, Message.seq)", cascade="all, delete-orphan")


class Message(Base):
    """会话中的消息。"""

    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_messages_session_order", "session_id", "created_at", "seq"),
        Index("uq_messages_session_seq", "session_id", "seq", unique=True),
        Index("uq_messages_session_client_key", "session_id", "client_key", unique=True),
    )

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    role = Column(Enum("user", "assistant", "tool", "system", name="message_role"), nullable=False)
    content = Column(Text, nullable=False)
    sources = Column(JSON, nullable=True)  # 来源引用列表
    tool_calls = Column(JSON, nullable=True)  # 工具调用记录
    activity = Column(JSON, nullable=True)  # 思考步骤/工具调用活动块 (durationMs, steps, status)
    seq = Column(Integer, nullable=False, default=0)  # per-session total-order tiebreaker
    status = Column(String, nullable=False, default="complete")  # complete / partial / error
    client_key = Column(String, nullable=True)  # server turn id or client message id
    tool_call_id = Column(String, nullable=True)
    tool_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), onupdate=datetime.datetime.now(datetime.timezone.utc))

    session = relationship("Session", back_populates="messages")


class KnowledgeBase(Base):
    """Knowledge base collection — supports multiple KBs with different embeddings."""

    __tablename__ = "knowledge_bases"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    collection_name = Column(String, unique=True, nullable=False)
    chunk_method = Column(String, default="hybrid")  # hybrid / agent
    small_chunk_size = Column(Integer, default=800)  # hybrid chunker target size (500/800/1200/2000)
    big_chunk_max_chars = Column(Integer, default=4000)  # ParentDocument big chunk max chars
    embedding_model = Column(String, default="text-embedding-3-small")
    embedding_base_url = Column(String, nullable=True)
    embedding_api_key_encrypted = Column(String, nullable=True)
    agent_chunker_model = Column(String, default="gpt-4o-mini")
    agent_chunker_base_url = Column(String, default="https://api.openai.com/v1")
    agent_chunker_api_key_encrypted = Column(String, nullable=True)
    context_window = Column(Integer, default=256000)
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    builtin_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), onupdate=datetime.datetime.now(datetime.timezone.utc))

    docs = relationship("KnowledgeDoc", back_populates="kb")


class KnowledgeDoc(Base):
    """知识库文档入库记录。"""

    __tablename__ = "knowledge_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String, unique=True, index=True, nullable=False)
    kb_id = Column(String, ForeignKey("knowledge_bases.id"), default="builtin-001", nullable=False, index=True)
    title = Column(String, default="")
    category = Column(String, default="user_upload")
    file_type = Column(String, default="")  # pdf / md / txt
    file_size = Column(Integer, default=0)  # bytes
    chunk_count = Column(Integer, default=0)
    chunk_method_used = Column(String, default="hybrid")
    status = Column(String, default="indexed")  # indexing / indexed / error
    error_message = Column(String, nullable=True)
    coverage_json = Column(Text, nullable=True)  # JSON string of page coverage stats
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    kb = relationship("KnowledgeBase", back_populates="docs")


class BigChunk(Base):
    """ParentDocument 检索模式的大块文本存储。"""

    __tablename__ = "big_chunks"
    __table_args__ = (
        Index("idx_big_chunks_kb", "kb_id"),
        Index("idx_big_chunks_doc", "doc_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    big_chunk_id = Column(String, unique=True, index=True, nullable=False)  # {doc_id}#b{section_idx}
    doc_id = Column(String, nullable=False)
    kb_id = Column(String, nullable=False)
    section_title = Column(Text, default="")
    text = Column(Text, nullable=False)  # 边界截断后的 section 全文
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))


class BookmarkFolder(Base):
    """书签文件夹。"""

    __tablename__ = "bookmark_folders"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    icon = Column(String, default="📁")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    # Use save-update, merge (not delete-orphan) to align with FK ondelete="SET NULL"
    # When a folder is deleted, bookmarks are preserved with folder_id=NULL
    bookmarks = relationship("Bookmark", back_populates="folder", cascade="save-update, merge")


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
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

    folder = relationship("BookmarkFolder", back_populates="bookmarks")


class Settings(Base):
    """全局设置键值存储。"""

    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), onupdate=datetime.datetime.now(datetime.timezone.utc))


class Feedback(Base):
    """消息反馈（👍/👎）。"""

    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1=👍, -1=👎
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))


class TokenUsage(Base):
    """Token 用量记录 — 每次 LLM 调用记录一条。"""

    __tablename__ = "token_usage"
    __table_args__ = (
        Index("idx_token_usage_created", "created_at"),
        Index("idx_token_usage_model", "model"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String, nullable=False, index=True)
    provider = Column(String, default="", nullable=True)
    session_id = Column(String, nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    run_id = Column(String(64), nullable=True, index=True)
    call_id = Column(String(64), nullable=True)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    cached_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Integer, nullable=True)
    status = Column(String(32), default="ok")
    source = Column(String(32), default="")
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))


class ToolAudit(Base):
    """Agent 工具调用审计日志 — 每次工具调用决策记录一条（v3-T1）。"""

    __tablename__ = "tool_audit"
    __table_args__ = (
        Index("idx_tool_audit_ts", "timestamp"),
        Index("idx_tool_audit_session", "session_id"),
        Index("idx_tool_audit_tool", "tool_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    session_id = Column(String, nullable=True, index=True)
    tool_name = Column(String, nullable=False)
    args_summary = Column(Text, default="")
    decision = Column(String, default="")  # allow / ask / deny
    decision_source = Column(String, default="")  # bypass / risk_level / user / deny_pattern
    risk_level = Column(String, default="")  # low / medium / high / n/a
    exit_code = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    # industrial-tool-runtime Task 6: extended audit fields
    call_id = Column(String(64), nullable=True)
    run_id = Column(String(64), nullable=True, index=True)
    success = Column(Boolean, default=True)
    error_type = Column(String(64), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    result_hash = Column(String(64), nullable=True)


class SessionTombstone(Base):
    """Deleted session marker; blocks stale clients from recreating the id."""

    __tablename__ = "session_tombstones"

    session_id = Column(String, primary_key=True)
    deleted_at = Column(DateTime, nullable=False, default=datetime.datetime.now(datetime.timezone.utc))
    epoch = Column(String, nullable=False, default="")


class Todo(Base):
    """Persisted session-scoped TODO list (replaces the in-memory dict)."""

    __tablename__ = "todos"
    __table_args__ = (
        Index("idx_todos_session", "session_id"),
    )

    session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")
    priority = Column(String, nullable=False, default="medium")
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), onupdate=datetime.datetime.now(datetime.timezone.utc))


class Plan(Base):
    """Versioned, session-scoped plan submitted in read-only plan mode."""

    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_plans_session_version"),
        Index("idx_plans_session_status", "session_id", "status"),
        Index("idx_plans_session_created", "session_id", "created_at"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')",
            name="ck_plans_status",
        ),
    )

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    steps_json = Column(Text, nullable=False)
    risks_json = Column(Text, nullable=False, default="[]")
    open_questions_json = Column(Text, nullable=False, default="[]")
    run_id = Column(String(128), nullable=True)
    content_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.now(datetime.timezone.utc))
    decided_at = Column(DateTime, nullable=True)
    executed_run_id = Column(String(128), nullable=True)


class MCPServerConfig(Base):
    """Persisted MCP stdio server configuration (no secrets handling here)."""

    __tablename__ = "mcp_servers"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, default="")
    command = Column(Text, nullable=False)
    args = Column(JSON, default=list)
    env = Column(JSON, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), onupdate=datetime.datetime.now(datetime.timezone.utc))


class SessionIncarnation(Base):
    """Retained lifecycle identity for a session incarnation."""

    __tablename__ = "session_incarnations"

    id = Column(String(32), primary_key=True, nullable=False, default=lambda: uuid.uuid4().hex)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    retired_at = Column(DateTime, nullable=True)


class SessionOwnerBinding(Base):
    """Active ownership mapping for a session; stores only a principal digest."""

    __tablename__ = "session_owner_bindings"

    session_id = Column(String, ForeignKey("sessions.id", ondelete="RESTRICT"), primary_key=True, nullable=False)
    owner_principal_hash = Column(String(64), nullable=False)
    incarnation_id = Column(String(32), ForeignKey("session_incarnations.id", ondelete="RESTRICT"), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))


class LegacyMessageSnapshotGeneration(Base):
    """Manifest for one incarnation-bound capture of legacy Message rows.

    The ORM records a proposed immutable snapshot shape. It does not itself
    make rows immutable or prove that a capture is complete or authorized.
    """

    __tablename__ = "legacy_message_snapshot_generations"
    __table_args__ = (
        Index(
            "idx_legacy_message_snapshot_owner",
            "session_id",
            "incarnation_id",
            "created_at",
        ),
        CheckConstraint(
            "source_message_revision >= 0",
            name="ck_legacy_message_snapshot_revision_non_negative",
        ),
        CheckConstraint(
            "capture_cursor >= 0 AND capture_cursor <= total_count",
            name="ck_legacy_message_snapshot_cursor_bounds",
        ),
        CheckConstraint(
            "total_count >= 0",
            name="ck_legacy_message_snapshot_total_non_negative",
        ),
        CheckConstraint(
            "ordering_version IN ('created_at_nulls_first_id_asc_v1')",
            name="ck_legacy_message_snapshot_ordering_version",
        ),
        CheckConstraint(
            "coverage_status IN ('pending', 'complete', 'incomplete')",
            name="ck_legacy_message_snapshot_coverage_status",
        ),
        CheckConstraint(
            "publication_status IN ('building', 'complete', 'incomplete')",
            name="ck_legacy_message_snapshot_publication_status",
        ),
        CheckConstraint(
            "coverage_status != 'complete' OR capture_cursor = total_count",
            name="ck_legacy_message_snapshot_complete_coverage_cursor",
        ),
        CheckConstraint(
            "publication_status != 'complete' OR "
            "(capture_cursor = total_count AND coverage_status IN ('complete', 'incomplete'))",
            name="ck_legacy_message_snapshot_complete_publication_cursor",
        ),
        CheckConstraint(
            "length(capture_chain_hash) = 64",
            name="ck_legacy_message_snapshot_chain_hash_length",
        ),
    )

    migration_id = Column(
        String(32),
        primary_key=True,
        nullable=False,
        default=lambda: uuid.uuid4().hex,
    )
    # Historical identity only: sessions are intentionally deletable, and
    # their Message rows are deleted by the existing session cascade.
    session_id = Column(
        String,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SessionOwnerBinding is removed when an owner retires/deletes a session;
    # retain its verified identity values and point at the retained incarnation.
    incarnation_id = Column(
        String(32),
        ForeignKey("session_incarnations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_principal_hash = Column(String(64), nullable=False)
    source_message_revision = Column(Integer, nullable=False)
    ordering_version = Column(
        String(64),
        nullable=False,
        default="created_at_nulls_first_id_asc_v1",
        server_default="created_at_nulls_first_id_asc_v1",
    )
    capture_cursor = Column(BigInteger, nullable=False, default=0, server_default="0")
    total_count = Column(BigInteger, nullable=False)
    coverage_status = Column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    publication_status = Column(
        String(16), nullable=False, default="building", server_default="building"
    )
    # Lowercase SHA-256 hex for the last committed row, or 64 zeroes at cursor 0.
    capture_chain_hash = Column(
        String(64),
        nullable=False,
        default="0" * 64,
        server_default="0" * 64,
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )


class LegacyMessageSnapshotRow(Base):
    """One ordered, hash-addressed source slot in a legacy snapshot generation."""

    __tablename__ = "legacy_message_snapshot_rows"
    __table_args__ = (
        UniqueConstraint(
            "migration_id",
            "legacy_message_id",
            name="uq_legacy_message_snapshot_message",
        ),
        CheckConstraint(
            "sequence >= 0",
            name="ck_legacy_message_snapshot_sequence_non_negative",
        ),
        CheckConstraint(
            "source_message_revision >= 0",
            name="ck_legacy_message_snapshot_row_revision_non_negative",
        ),
        CheckConstraint(
            "capture_status IN ('captured', 'missing', 'unavailable')",
            name="ck_legacy_message_snapshot_row_status",
        ),
        CheckConstraint(
            "((capture_status = 'captured' AND canonical_envelope IS NOT NULL "
            "AND capture_reason IS NULL) OR "
            "(capture_status IN ('missing', 'unavailable') "
            "AND canonical_envelope IS NULL AND capture_reason IS NOT NULL "
            "AND length(trim(capture_reason)) > 0))",
            name="ck_legacy_message_snapshot_row_payload_status",
        ),
        CheckConstraint(
            "length(row_hash) = 64",
            name="ck_legacy_message_snapshot_row_hash_length",
        ),
        CheckConstraint(
            "length(chain_hash) = 64",
            name="ck_legacy_message_snapshot_row_chain_hash_length",
        ),
    )

    migration_id = Column(
        String(32),
        ForeignKey("legacy_message_snapshot_generations.migration_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    # Sequence is zero-based and unique within a generation; it is not a
    # cumulative limit or a fixed-size page allocation.
    sequence = Column(BigInteger, primary_key=True, nullable=False)
    legacy_message_id = Column(String, nullable=False)
    source_message_revision = Column(Integer, nullable=False)
    canonical_envelope = Column(JSON(none_as_null=True), nullable=True)
    row_hash = Column(String(64), nullable=False)
    chain_hash = Column(String(64), nullable=False)
    capture_status = Column(String(16), nullable=False)
    capture_reason = Column(Text, nullable=True)


class LegacyBodyMigration(Base):
    """Append-only progress and verified manifest for one owner-scoped body copy.

    A complete row certifies the copied chunks against the declared byte length
    and SHA-256. It does not certify that an inline legacy Settings value was
    read in a physically bounded way or authorize removing that source row.
    """

    __tablename__ = "legacy_body_migrations"
    __table_args__ = (
        Index("idx_legacy_body_migrations_owner", "session_id", "incarnation_id"),
        CheckConstraint(
            "source_byte_length >= 0",
            name="ck_legacy_body_migration_source_length_non_negative",
        ),
        CheckConstraint(
            "chunk_size = 8192",
            name="ck_legacy_body_migration_fixed_chunk_size",
        ),
        CheckConstraint(
            "next_chunk_index >= 0",
            name="ck_legacy_body_migration_next_chunk_non_negative",
        ),
        CheckConstraint(
            "bytes_appended >= 0 AND bytes_appended <= source_byte_length",
            name="ck_legacy_body_migration_bytes_bounds",
        ),
        CheckConstraint(
            "status IN ('building', 'complete')",
            name="ck_legacy_body_migration_status",
        ),
        CheckConstraint(
            "length(source_key_digest) = 64",
            name="ck_legacy_body_migration_source_key_digest_length",
        ),
        CheckConstraint(
            "length(source_sha256) = 64",
            name="ck_legacy_body_migration_source_sha256_length",
        ),
        CheckConstraint(
            "length(chain_sha256) = 64",
            name="ck_legacy_body_migration_chain_sha256_length",
        ),
        CheckConstraint(
            "source_verification_status = 'source_unverified'",
            name="ck_legacy_body_migration_source_verification_status",
        ),
        CheckConstraint(
            "(status = 'building' AND verified_byte_length IS NULL "
            "AND verified_sha256 IS NULL) OR "
            "(status = 'complete' AND verified_byte_length = source_byte_length "
            "AND verified_sha256 = source_sha256)",
            name="ck_legacy_body_migration_verified_manifest",
        ),
    )

    # The migration identity is also the immutable manifest identity.
    migration_id = Column(String(32), primary_key=True, nullable=False)
    session_id = Column(
        String,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    incarnation_id = Column(
        String(32),
        ForeignKey("session_incarnations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_principal_hash = Column(String(64), nullable=False)
    source_key_digest = Column(String(64), nullable=False)
    source_byte_length = Column(BigInteger, nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    chunk_size = Column(Integer, nullable=False, default=8192, server_default="8192")
    next_chunk_index = Column(BigInteger, nullable=False, default=0, server_default="0")
    bytes_appended = Column(BigInteger, nullable=False, default=0, server_default="0")
    chain_sha256 = Column(
        String(64),
        nullable=False,
        default="0" * 64,
        server_default="0" * 64,
    )
    status = Column(
        String(16),
        nullable=False,
        default="building",
        server_default="building",
    )
    verified_byte_length = Column(BigInteger, nullable=True)
    verified_sha256 = Column(String(64), nullable=True)
    source_verification_status = Column(
        String(32),
        nullable=False,
        default="source_unverified",
        server_default="source_unverified",
    )


class LegacyBodyChunk(Base):
    """One fixed-size BLOB chunk in an owner-scoped legacy body copy."""

    __tablename__ = "legacy_body_chunks"
    __table_args__ = (
        CheckConstraint(
            "chunk_index >= 0",
            name="ck_legacy_body_chunk_index_non_negative",
        ),
        CheckConstraint(
            "byte_offset >= 0",
            name="ck_legacy_body_chunk_offset_non_negative",
        ),
        CheckConstraint(
            "chunk_length > 0 AND chunk_length <= 8192",
            name="ck_legacy_body_chunk_fixed_size_bound",
        ),
        CheckConstraint(
            "typeof(body) = 'blob' AND length(body) = chunk_length",
            name="ck_legacy_body_chunk_blob_length",
        ),
        CheckConstraint(
            "length(previous_chain_sha256) = 64",
            name="ck_legacy_body_chunk_previous_chain_hash_length",
        ),
        CheckConstraint(
            "length(chunk_sha256) = 64",
            name="ck_legacy_body_chunk_sha256_length",
        ),
        CheckConstraint(
            "length(chain_sha256) = 64",
            name="ck_legacy_body_chunk_chain_sha256_length",
        ),
    )

    migration_id = Column(
        String(32),
        ForeignKey("legacy_body_migrations.migration_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    chunk_index = Column(BigInteger, primary_key=True, nullable=False)
    byte_offset = Column(BigInteger, nullable=False)
    chunk_length = Column(Integer, nullable=False)
    previous_chain_sha256 = Column(String(64), nullable=False)
    chunk_sha256 = Column(String(64), nullable=False)
    chain_sha256 = Column(String(64), nullable=False)
    body = Column(LargeBinary, nullable=False)


class MessageBodyManifest(Base):
    """Append-only write state and writer-verified manifest for UTF-8 message bodies."""

    __tablename__ = "message_body_manifests"
    __table_args__ = (
        Index("idx_message_body_manifests_owner", "session_id", "incarnation_id"),
        CheckConstraint(
            "status IN ('building', 'writer_verified')",
            name="ck_message_body_manifest_status",
        ),
        CheckConstraint(
            "next_chunk_offset >= 0 AND next_chunk_offset % 8192 = 0",
            name="ck_message_body_manifest_next_offset",
        ),
        CheckConstraint(
            "bytes_appended >= 0 AND chunk_count >= 0",
            name="ck_message_body_manifest_progress_non_negative",
        ),
        CheckConstraint(
            "next_chunk_offset = chunk_count * 8192",
            name="ck_message_body_manifest_chunk_cursor",
        ),
        CheckConstraint(
            "terminal_chunk_offset IS NULL OR "
            "(terminal_chunk_offset >= 0 AND terminal_chunk_offset % 8192 = 0 "
            "AND terminal_chunk_offset < next_chunk_offset)",
            name="ck_message_body_manifest_terminal_chunk",
        ),
        CheckConstraint(
            "length(chain_sha256) = 64",
            name="ck_message_body_manifest_chain_hash_length",
        ),
        CheckConstraint(
            "(status = 'building' AND byte_length IS NULL AND sha256 IS NULL "
            "AND final_chain_sha256 IS NULL) OR "
            "(status = 'writer_verified' AND byte_length = bytes_appended "
            "AND length(sha256) = 64 AND final_chain_sha256 = chain_sha256)",
            name="ck_message_body_manifest_writer_verification",
        ),
    )

    body_id = Column(String(32), primary_key=True, nullable=False)
    session_id = Column(
        String,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    incarnation_id = Column(
        String(32),
        ForeignKey("session_incarnations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_principal_hash = Column(String(64), nullable=False)
    status = Column(
        String(24),
        nullable=False,
        default="building",
        server_default="building",
    )
    next_chunk_offset = Column(BigInteger, nullable=False, default=0, server_default="0")
    bytes_appended = Column(BigInteger, nullable=False, default=0, server_default="0")
    chunk_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    terminal_chunk_offset = Column(BigInteger, nullable=True)
    chain_sha256 = Column(
        String(64),
        nullable=False,
        default="0" * 64,
        server_default="0" * 64,
    )
    byte_length = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    final_chain_sha256 = Column(String(64), nullable=True)


class MessageBodyChunk(Base):
    """One contiguous BLOB chunk belonging to a message-body manifest."""

    __tablename__ = "message_body_chunks"
    __table_args__ = (
        CheckConstraint(
            "byte_offset >= 0 AND byte_offset % 8192 = 0",
            name="ck_message_body_chunk_offset_alignment",
        ),
        CheckConstraint(
            "chunk_length > 0 AND chunk_length <= 8192",
            name="ck_message_body_chunk_size_bound",
        ),
        CheckConstraint(
            "typeof(body) = 'blob' AND length(body) = chunk_length",
            name="ck_message_body_chunk_blob_length",
        ),
        CheckConstraint(
            "length(previous_chain_sha256) = 64",
            name="ck_message_body_chunk_previous_chain_hash_length",
        ),
        CheckConstraint(
            "length(chunk_sha256) = 64",
            name="ck_message_body_chunk_sha256_length",
        ),
        CheckConstraint(
            "length(chain_sha256) = 64",
            name="ck_message_body_chunk_chain_sha256_length",
        ),
    )

    body_id = Column(
        String(32),
        ForeignKey("message_body_manifests.body_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    byte_offset = Column(BigInteger, primary_key=True, nullable=False)
    chunk_length = Column(Integer, nullable=False)
    previous_chain_sha256 = Column(String(64), nullable=False)
    chunk_sha256 = Column(String(64), nullable=False)
    chain_sha256 = Column(String(64), nullable=False)
    body = Column(LargeBinary, nullable=False)


class LegacyMessageBlobSnapshot(Base):
    """Owner-bound checkpoint for a bounded snapshot of legacy Message rows."""

    __tablename__ = "legacy_message_blob_snapshots"
    __table_args__ = (
        Index("idx_legacy_message_blob_snapshots_owner", "session_id", "incarnation_id"),
        CheckConstraint(
            "source_generation >= 0 AND source_message_count >= 0 "
            "AND source_non_append_generation >= 0",
            name="ck_legacy_message_blob_snapshot_fence_non_negative",
        ),
        CheckConstraint(
            "ordering_version = 'created_at_nulls_first_id_asc_v1'",
            name="ck_legacy_message_blob_snapshot_ordering_version",
        ),
        CheckConstraint(
            "((source_message_count = 0 AND high_water_message_id IS NULL "
            "AND high_water_created_at IS NULL) OR "
            "(source_message_count > 0 AND high_water_message_id IS NOT NULL))",
            name="ck_legacy_message_blob_snapshot_high_water",
        ),
        CheckConstraint(
            "typeof(session_id) = 'text' AND length(session_id) BETWEEN 1 AND 128 "
            "AND instr(session_id, char(0)) = 0",
            name="ck_legacy_message_blob_snapshot_session_id_bound",
        ),
        CheckConstraint(
            "high_water_created_at IS NULL OR (typeof(high_water_created_at) = 'text' "
            "AND length(high_water_created_at) <= 8192 "
            "AND instr(high_water_created_at, char(0)) = 0)",
            name="ck_legacy_message_blob_snapshot_high_water_created_at_bound",
        ),
        CheckConstraint(
            "high_water_message_id IS NULL OR (typeof(high_water_message_id) = 'text' "
            "AND length(high_water_message_id) BETWEEN 1 AND 512 "
            "AND instr(high_water_message_id, char(0)) = 0)",
            name="ck_legacy_message_blob_snapshot_high_water_message_id_bound",
        ),
        CheckConstraint(
            "capture_message_id IS NULL OR (typeof(capture_message_id) = 'text' "
            "AND length(capture_message_id) BETWEEN 1 AND 512 "
            "AND instr(capture_message_id, char(0)) = 0)",
            name="ck_legacy_message_blob_snapshot_capture_message_id_bound",
        ),
        CheckConstraint(
            "capture_cursor >= 0 AND capture_cursor <= source_message_count",
            name="ck_legacy_message_blob_snapshot_cursor_bounds",
        ),
        CheckConstraint(
            "capture_body_offset >= 0",
            name="ck_legacy_message_blob_snapshot_body_offset_non_negative",
        ),
        CheckConstraint(
            "length(capture_chain_sha256) = 64",
            name="ck_legacy_message_blob_snapshot_chain_hash_length",
        ),
        CheckConstraint(
            "length(capture_body_chain_sha256) = 64",
            name="ck_legacy_message_blob_snapshot_body_chain_hash_length",
        ),
        CheckConstraint(
            "status IN ('building', 'sealed')",
            name="ck_legacy_message_blob_snapshot_status",
        ),
        CheckConstraint(
            "status != 'sealed' OR (capture_cursor = source_message_count "
            "AND capture_message_id IS NULL AND capture_body_id IS NULL "
            "AND capture_body_offset = 0 "
            "AND capture_body_chain_sha256 = '0000000000000000000000000000000000000000000000000000000000000000')",
            name="ck_legacy_message_blob_snapshot_sealed_cursor",
        ),
        CheckConstraint(
            "capture_message_id IS NOT NULL OR "
            "(capture_body_id IS NULL AND capture_body_offset = 0 "
            "AND capture_body_chain_sha256 = '0000000000000000000000000000000000000000000000000000000000000000')",
            name="ck_legacy_message_blob_snapshot_idle_body_cursor",
        ),
        CheckConstraint(
            "delete_epoch = '' OR length(delete_epoch) = 32",
            name="ck_legacy_message_blob_snapshot_delete_epoch",
        ),
        CheckConstraint(
            "length(owner_principal_hash) = 64",
            name="ck_legacy_message_blob_snapshot_owner_hash_length",
        ),
    )

    migration_id = Column(String(32), primary_key=True, nullable=False)
    snapshot_id = Column(String(32), nullable=False, unique=True)
    session_id = Column(
        String,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_principal_hash = Column(String(64), nullable=False)
    incarnation_id = Column(
        String(32),
        ForeignKey("session_incarnations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    delete_epoch = Column(String(32), nullable=False)
    source_generation = Column(BigInteger, nullable=False)
    source_message_count = Column(BigInteger, nullable=False)
    source_non_append_generation = Column(BigInteger, nullable=False)
    ordering_version = Column(
        String(64),
        nullable=False,
        default="created_at_nulls_first_id_asc_v1",
        server_default="created_at_nulls_first_id_asc_v1",
    )
    high_water_created_at = Column(Text, nullable=True)
    high_water_message_id = Column(String, nullable=True)
    capture_cursor = Column(BigInteger, nullable=False, default=0, server_default="0")
    capture_message_id = Column(String, nullable=True)
    capture_body_id = Column(String(32), nullable=True)
    capture_body_offset = Column(BigInteger, nullable=False, default=0, server_default="0")
    capture_body_chain_sha256 = Column(
        String(64),
        nullable=False,
        default="0" * 64,
        server_default="0" * 64,
    )
    capture_chain_sha256 = Column(
        String(64),
        nullable=False,
        default="0" * 64,
        server_default="0" * 64,
    )
    status = Column(String(16), nullable=False, default="building", server_default="building")


class LegacyMessageBlobSnapshotRow(Base):
    """Metadata and a verified body-manifest reference for one legacy Message."""

    __tablename__ = "legacy_message_blob_snapshot_rows"
    __table_args__ = (
        UniqueConstraint(
            "migration_id",
            "legacy_message_id",
            name="uq_legacy_message_blob_snapshot_message",
        ),
        UniqueConstraint(
            "body_id",
            name="uq_legacy_message_blob_snapshot_body",
        ),
        CheckConstraint("sequence >= 0", name="ck_legacy_message_blob_snapshot_row_sequence"),
        CheckConstraint("body_byte_length >= 0", name="ck_legacy_message_blob_snapshot_row_body_length"),
        CheckConstraint(
            "status IN ('capturing', 'sealed')",
            name="ck_legacy_message_blob_snapshot_row_status",
        ),
        CheckConstraint(
            "(status = 'capturing' AND body_sha256 IS NULL AND row_hash IS NULL) OR "
            "(status = 'sealed' AND length(body_sha256) = 64 AND length(row_hash) = 64)",
            name="ck_legacy_message_blob_snapshot_row_sealed_manifest",
        ),
        CheckConstraint(
            "typeof(legacy_message_id) = 'text' "
            "AND length(legacy_message_id) BETWEEN 1 AND 512 "
            "AND instr(legacy_message_id, char(0)) = 0",
            name="ck_legacy_message_blob_snapshot_row_message_id_bound",
        ),
        CheckConstraint(
            "typeof(role) = 'text' AND length(role) <= 8192 "
            "AND instr(role, char(0)) = 0",
            name="ck_legacy_message_blob_snapshot_row_role_bound",
        ),
        CheckConstraint(
            "created_at IS NULL OR (typeof(created_at) = 'text' "
            "AND length(created_at) <= 8192 AND instr(created_at, char(0)) = 0)",
            name="ck_legacy_message_blob_snapshot_row_created_at_bound",
        ),
        CheckConstraint(
            "metadata_json IS NOT NULL AND length(metadata_json) > 0",
            name="ck_legacy_message_blob_snapshot_row_metadata",
        ),
        CheckConstraint(
            "typeof(metadata_json) = 'text' AND length(metadata_json) <= 65536 "
            "AND instr(metadata_json, char(0)) = 0",
            name="ck_legacy_message_blob_snapshot_row_metadata_bound",
        ),
        CheckConstraint(
            "4 * (length(legacy_message_id) + length(role) "
            "+ COALESCE(length(created_at), 0) + length(metadata_json)) + 256 <= 262144",
            name="ck_legacy_message_blob_snapshot_row_payload_bound",
        ),
    )

    migration_id = Column(
        String(32),
        ForeignKey("legacy_message_blob_snapshots.migration_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    sequence = Column(BigInteger, primary_key=True, nullable=False)
    legacy_message_id = Column(String, nullable=False)
    source_rowid = Column(BigInteger, nullable=False)
    body_id = Column(
        String(32),
        ForeignKey("message_body_manifests.body_id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String, nullable=False)
    created_at = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=False)
    body_byte_length = Column(BigInteger, nullable=False)
    body_sha256 = Column(String(64), nullable=True)
    row_hash = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="capturing", server_default="capturing")


class LegacyMessageBlobSnapshotPage(Base):
    """Idempotency receipt for one compare-and-swap snapshot capture page."""

    __tablename__ = "legacy_message_blob_snapshot_pages"
    __table_args__ = (
        CheckConstraint("request_cursor >= 0", name="ck_legacy_message_blob_snapshot_page_request_cursor"),
        CheckConstraint("request_body_offset >= 0", name="ck_legacy_message_blob_snapshot_page_request_offset"),
        CheckConstraint("max_rows >= 1 AND max_rows <= 32", name="ck_legacy_message_blob_snapshot_page_row_bound"),
        CheckConstraint("max_body_bytes >= 8192 AND max_body_bytes <= 1048576", name="ck_legacy_message_blob_snapshot_page_body_bound"),
        CheckConstraint(
            "typeof(request_message_id) = 'text' AND length(request_message_id) <= 512 "
            "AND instr(request_message_id, char(0)) = 0",
            name="ck_legacy_message_blob_snapshot_page_request_message_id_bound",
        ),
        CheckConstraint(
            "typeof(response_message_id) = 'text' AND length(response_message_id) <= 512 "
            "AND instr(response_message_id, char(0)) = 0",
            name="ck_legacy_message_blob_snapshot_page_response_message_id_bound",
        ),
        CheckConstraint("body_bytes_captured >= 0 AND body_bytes_captured <= max_body_bytes", name="ck_legacy_message_blob_snapshot_page_body_bytes"),
        CheckConstraint("row_count >= 0 AND row_count <= max_rows", name="ck_legacy_message_blob_snapshot_page_row_count"),
        CheckConstraint("length(request_chain_sha256) = 64", name="ck_legacy_message_blob_snapshot_page_request_chain"),
        CheckConstraint("length(request_body_chain_sha256) = 64", name="ck_legacy_message_blob_snapshot_page_request_body_chain"),
        CheckConstraint("length(response_chain_sha256) = 64", name="ck_legacy_message_blob_snapshot_page_response_chain"),
        CheckConstraint("length(response_capture_chain_sha256) = 64", name="ck_legacy_message_blob_snapshot_page_response_capture_chain"),
        CheckConstraint("status IN ('building', 'sealed')", name="ck_legacy_message_blob_snapshot_page_status"),
    )

    migration_id = Column(
        String(32),
        ForeignKey("legacy_message_blob_snapshots.migration_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    request_cursor = Column(BigInteger, primary_key=True, nullable=False)
    request_message_id = Column(String, primary_key=True, nullable=False, default="", server_default="")
    request_body_offset = Column(BigInteger, primary_key=True, nullable=False)
    request_chain_sha256 = Column(String(64), primary_key=True, nullable=False)
    request_body_chain_sha256 = Column(String(64), primary_key=True, nullable=False)
    max_rows = Column(Integer, nullable=False)
    max_body_bytes = Column(Integer, nullable=False)
    response_cursor = Column(BigInteger, nullable=False)
    response_message_id = Column(String, nullable=False, default="", server_default="")
    response_body_offset = Column(BigInteger, nullable=False)
    response_chain_sha256 = Column(String(64), nullable=False)
    response_capture_chain_sha256 = Column(String(64), nullable=False)
    row_count = Column(Integer, nullable=False)
    body_bytes_captured = Column(BigInteger, nullable=False)
    status = Column(String(16), nullable=False)
