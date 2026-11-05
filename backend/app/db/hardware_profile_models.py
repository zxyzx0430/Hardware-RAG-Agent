"""SQLAlchemy model for hardware profiles (Memory v1 / hardware-profile Skill).

One row per board or MCU the user has worked with. Profiles are only written
through explicit tool calls and are never injected into the prompt; the
``hardware_mention`` hint may name an alias but never returns profile fields.
"""

import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.db.database import Base

DEFAULT_PROFILE_CONFIDENCE = 0.7


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class HardwareProfile(Base):
    """A verified hardware profile keyed by a stable id."""

    __tablename__ = "hardware_profiles"
    __table_args__ = (
        Index("idx_hardware_profiles_name", "name"),
        Index("idx_hardware_profiles_updated", "updated_at"),
    )

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    aliases_json = Column(Text, nullable=False, default="[]", server_default="[]")
    mcu = Column(Text, nullable=False, default="", server_default="")
    package = Column(Text, nullable=False, default="", server_default="")
    voltage = Column(Text, nullable=False, default="", server_default="")
    clock = Column(Text, nullable=False, default="", server_default="")
    pins_json = Column(Text, nullable=False, default="{}", server_default="{}")
    peripherals_json = Column(Text, nullable=False, default="[]", server_default="[]")
    toolchain = Column(Text, nullable=False, default="", server_default="")
    sources_json = Column(Text, nullable=False, default="[]", server_default="[]")
    rejected_json = Column(Text, nullable=False, default="[]", server_default="[]")
    confidence = Column(
        Float, nullable=False, default=DEFAULT_PROFILE_CONFIDENCE,
        server_default=str(DEFAULT_PROFILE_CONFIDENCE),
    )
    use_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utc_now)
    updated_at = Column(DateTime, nullable=False, default=_utc_now)
