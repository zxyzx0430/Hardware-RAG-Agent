"""Memory v1 persistence model: explicit generic memory entries.

The table is registered on ``app.db.database.Base`` at import time. Startup
wiring must import this module before ``init_db`` so ``create_all`` sees it;
tests that build their own ``Base.metadata`` need the same import.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Index, String, Text

from app.db.database import Base

MEMORY_SCOPES = ("global", "project", "session")
MEMORY_KINDS = ("preference", "fact", "constraint", "decision", "procedure")
MEMORY_SOURCES = ("user", "agent", "skill", "import")

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
