"""Memory persistence models.

The tables are registered on ``app.db.database.Base`` at import time. Startup
wiring must import this module before ``init_db`` so ``create_all`` sees them;
tests that build their own ``Base.metadata`` need the same import.

Memory v2 keeps the legacy ``scope``/``scope_key`` columns for compatibility
and adds ``tier``/``status``/``project_id``/``principal_id``/``evidence_json``.
Existing databases get those columns through
``app.db.memory_v2_schema.apply_memory_v2_schema``; ``create_all`` alone never
alters an existing table.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, desc

from app.db.database import Base

MEMORY_SCOPES = ("global", "project", "session")
MEMORY_KINDS = ("preference", "fact", "constraint", "decision", "procedure")
MEMORY_SOURCES = ("user", "agent", "skill", "import")

MEMORY_TIERS = ("working", "project", "user", "episodic")
MEMORY_STATUSES = ("proposed", "active", "rejected", "superseded")
DEFAULT_MEMORY_TIER = "project"
DEFAULT_MEMORY_STATUS = "active"
DEFAULT_PRINCIPAL_ID = "local"
MEMORY_SCHEMA_VERSION = 2

# Legacy scope remains the compatibility surface; tier is authoritative for v2
# readers. Episodic rows keep a project scope for attribution but are excluded
# from legacy context injection by the tier filter.
TIER_TO_SCOPE = {
    "working": "session",
    "project": "project",
    "user": "global",
    "episodic": "project",
}
SCOPE_TO_TIER = {
    "global": "user",
    "project": "project",
    "session": "working",
}

# Hardware profiles have their own store and must never be injected through
# generic memory context. Kept here so store and retrieval share one constant.
HARDWARE_PROFILE_KIND = "hardware_profile"


class MemoryEntry(Base):
    """One explicit memory entry (no per-turn extraction in v1)."""

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("idx_memory_entries_scope", "scope", "scope_key", "kind"),
        Index("idx_memory_entries_deleted_at", "deleted_at"),
        Index("idx_memory_entries_updated_at", "updated_at"),
        Index("idx_memory_entries_tier_status", "tier", "status"),
        Index("idx_memory_entries_project_status", "project_id", "status"),
    )

    id = Column(String, primary_key=True)
    scope = Column(String, nullable=False)
    scope_key = Column(String, nullable=False, default="", server_default="")
    kind = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    structured_json = Column(Text, nullable=False, default="{}", server_default="{}")
    source = Column(String, nullable=False, default="agent", server_default="agent")
    confidence = Column(Float, nullable=False, default=0.7, server_default="0.7")
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    supersedes_id = Column(String, nullable=True)
    tier = Column(
        String, nullable=False, default=DEFAULT_MEMORY_TIER, server_default=DEFAULT_MEMORY_TIER
    )
    status = Column(
        String, nullable=False, default=DEFAULT_MEMORY_STATUS, server_default=DEFAULT_MEMORY_STATUS
    )
    project_id = Column(String, nullable=False, default="", server_default="")
    principal_id = Column(
        String, nullable=False, default=DEFAULT_PRINCIPAL_ID, server_default=DEFAULT_PRINCIPAL_ID
    )
    evidence_json = Column(Text, nullable=False, default="{}", server_default="{}")
    schema_version = Column(
        Integer, nullable=False, default=MEMORY_SCHEMA_VERSION, server_default=str(MEMORY_SCHEMA_VERSION)
    )


class MemorySnapshot(Base):
    """One sealed structured checkpoint projected into working memory.

    ``body_json`` is the canonical JSON body of the checkpoint. Duplicate
    ``(session_id, seal_id)`` writes are ignored; the first sealed generation
    wins. ``memory_epoch``/``delete_epoch`` let readers reject snapshots that
    predate a memory clear or a session deletion.
    """

    __tablename__ = "memory_snapshots"
    __table_args__ = (
        UniqueConstraint("session_id", "seal_id", name="uq_memory_snapshots_session_seal"),
        Index("idx_memory_snapshots_session_created", "session_id", desc("created_at")),
    )

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False)
    principal_hash = Column(String, nullable=False, default="", server_default="")
    memory_epoch = Column(String, nullable=False, default="", server_default="")
    delete_epoch = Column(String, nullable=False, default="", server_default="")
    seal_id = Column(String, nullable=False)
    generation = Column(Integer, nullable=False, default=0, server_default="0")
    body_json = Column(Text, nullable=False, default="{}", server_default="{}")
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
