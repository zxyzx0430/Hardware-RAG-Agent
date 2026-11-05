"""SQLite storage for hardware profiles.

The store is intentionally small and explicit: there is no per-turn LLM
extraction. Profiles are written only by ``HardwareProfileTool.upsert`` (or a
future API route) and read on demand by the ``hardware-profile`` Skill.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import threading
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import database
from app.db.hardware_profile_models import DEFAULT_PROFILE_CONFIDENCE, HardwareProfile

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 200
MAX_SEARCH_QUERY_CHARS = 200
MAX_PROFILE_ID_CHARS = 128
MAX_NAME_CHARS = 256
MAX_ALIAS_CHARS = 64
MAX_ALIASES = 20
MAX_SCALAR_CHARS = 200
MAX_PINS = 100
MAX_PIN_NAME_CHARS = 64
MAX_PIN_VALUE_CHARS = 200
MAX_LIST_ITEMS = 50
MAX_LIST_ITEM_CHARS = 500
MAX_PROFILE_JSON_BYTES = 64 * 1024

_SCALAR_FIELDS = ("mcu", "package", "voltage", "clock", "toolchain")


class HardwareProfileValidationError(ValueError):
    """Raised when a profile payload is outside the accepted bounds."""


class HardwareProfileSecretError(HardwareProfileValidationError):
    """Raised when a profile payload looks like it contains credentials."""


@dataclass(frozen=True, slots=True)
class HardwareProfileRecord:
    id: str
    name: str
    aliases: tuple[str, ...]
    mcu: str
    package: str
    voltage: str
    clock: str
    pins: dict[str, str]
    peripherals: tuple[str, ...]
    toolchain: str
    sources: tuple[str, ...]
    rejected: tuple[str, ...]
    confidence: float
    use_count: int
    last_used_at: _dt.datetime | None
    created_at: _dt.datetime | None
    updated_at: _dt.datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "mcu": self.mcu,
            "package": self.package,
            "voltage": self.voltage,
            "clock": self.clock,
            "pins": dict(self.pins),
            "peripherals": list(self.peripherals),
            "toolchain": self.toolchain,
            "sources": list(self.sources),
            "rejected": list(self.rejected),
            "confidence": self.confidence,
            "use_count": self.use_count,
            "last_used_at": _isoformat(self.last_used_at),
            "created_at": _isoformat(self.created_at),
            "updated_at": _isoformat(self.updated_at),
        }


def _isoformat(value: _dt.datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=_dt.timezone.utc)
    return aware.astimezone(_dt.timezone.utc).isoformat()


def _utc_now_naive() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def _to_utc_aware(value: _dt.datetime | None) -> _dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


# Credential-shaped strings are rejected before they can reach the database.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"client[_-]?secret|password|passwd|private[_-]?key)\b"
        r"\s*[:=]\s*\S{6,}"
    ),
)

_SCHEMA_READY: "weakref.WeakSet[Any]" = weakref.WeakSet()
_SCHEMA_LOCK = threading.Lock()


def ensure_schema(bind: Any) -> None:
    """Create the hardware_profiles table on ``bind`` once (idempotent)."""
    with _SCHEMA_LOCK:
        try:
            if bind in _SCHEMA_READY:
                return
        except TypeError:
            pass
        HardwareProfile.__table__.create(bind=bind, checkfirst=True)
        try:
            _SCHEMA_READY.add(bind)
        except TypeError:
            pass


def _contains_secret(value: Any, depth: int = 0) -> bool:
    if depth > 8:
        return True
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    if isinstance(value, Mapping):
        return any(
            _contains_secret(str(key), depth + 1) or _contains_secret(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_secret(item, depth + 1) for item in value)
    return False


def _clean_text(value: Any, field_name: str, max_chars: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise HardwareProfileValidationError(f"{field_name} must be a string")
    text = value.strip()
    if required and not text:
        raise HardwareProfileValidationError(f"{field_name} is required")
    if len(text) > max_chars:
        raise HardwareProfileValidationError(
            f"{field_name} exceeds {max_chars} characters"
        )
    if any(ord(char) < 32 for char in text):
        raise HardwareProfileValidationError(f"{field_name} contains control characters")
    return text


def _clean_list(
    value: Any,
    field_name: str,
    *,
    max_items: int = MAX_LIST_ITEMS,
    max_item_chars: int = MAX_LIST_ITEM_CHARS,
) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise HardwareProfileValidationError(f"{field_name} must be a list of strings")
    if len(value) > max_items:
        raise HardwareProfileValidationError(
            f"{field_name} exceeds {max_items} entries"
        )
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item, field_name, max_item_chars)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _clean_pins(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise HardwareProfileValidationError("pins must be an object of pin name to value")
    if len(value) > MAX_PINS:
        raise HardwareProfileValidationError(f"pins exceeds {MAX_PINS} entries")
    pins: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _clean_text(raw_key, "pin name", MAX_PIN_NAME_CHARS)
        if not key:
            raise HardwareProfileValidationError("pin name must not be empty")
        if isinstance(raw_value, (Mapping, list, tuple, set)):
            raise HardwareProfileValidationError("pin values must be scalar strings")
        text = _clean_text(
            "" if raw_value is None else str(raw_value), "pin value", MAX_PIN_VALUE_CHARS
        )
        pins[key] = text
    return pins


def _clean_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HardwareProfileValidationError("confidence must be a number")
    number = float(value)
    if number != number or not 0.0 <= number <= 1.0:
        raise HardwareProfileValidationError("confidence must be between 0 and 1")
    return number


def _normalize_incoming(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise HardwareProfileValidationError("profile must be an object")
    if _contains_secret(profile):
        raise HardwareProfileSecretError(
            "profile contains credential-like data; remove secrets before saving"
        )
    incoming = {
        "id": _clean_text(profile.get("id"), "id", MAX_PROFILE_ID_CHARS),
        "name": _clean_text(profile.get("name"), "name", MAX_NAME_CHARS, required=True),
        "aliases": _clean_list(
            profile.get("aliases"), "aliases",
            max_items=MAX_ALIASES, max_item_chars=MAX_ALIAS_CHARS,
        ),
        "pins": _clean_pins(profile.get("pins")),
        "peripherals": _clean_list(profile.get("peripherals"), "peripherals"),
        "sources": _clean_list(profile.get("sources"), "sources"),
        "rejected": _clean_list(profile.get("rejected"), "rejected"),
        "confidence": _clean_confidence(profile.get("confidence")),
    }
    for field_name in _SCALAR_FIELDS:
        incoming[field_name] = _clean_text(
            profile.get(field_name), field_name, MAX_SCALAR_CHARS
        )
    try:
        encoded = json.dumps(incoming, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise HardwareProfileValidationError("profile is not JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_PROFILE_JSON_BYTES:
        raise HardwareProfileValidationError("profile exceeds the stored size limit")
    return incoming


def _load_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring corrupt hardware profile JSON list")
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _load_pins(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring corrupt hardware profile JSON pins")
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _union(existing: list[str], incoming: list[str], *, casefold: bool) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        text = item.strip()
        if not text:
            continue
        key = text.casefold() if casefold else text
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged


def _record(row: HardwareProfile) -> HardwareProfileRecord:
    return HardwareProfileRecord(
        id=row.id,
        name=row.name or "",
        aliases=tuple(_load_list(row.aliases_json)),
        mcu=row.mcu or "",
        package=row.package or "",
        voltage=row.voltage or "",
        clock=row.clock or "",
        pins=_load_pins(row.pins_json),
        peripherals=tuple(_load_list(row.peripherals_json)),
        toolchain=row.toolchain or "",
        sources=tuple(_load_list(row.sources_json)),
        rejected=tuple(_load_list(row.rejected_json)),
        confidence=float(
            DEFAULT_PROFILE_CONFIDENCE if row.confidence is None else row.confidence
        ),
        use_count=int(row.use_count or 0),
        last_used_at=_to_utc_aware(row.last_used_at),
        created_at=_to_utc_aware(row.created_at),
        updated_at=_to_utc_aware(row.updated_at),
    )


def _match_score(row: HardwareProfile, incoming: Mapping[str, Any]) -> int:
    name_key = str(incoming["name"]).casefold()
    incoming_aliases = {alias.casefold() for alias in incoming["aliases"]}
    row_name = (row.name or "").strip().casefold()
    row_aliases = {alias.casefold() for alias in _load_list(row.aliases_json)}
    if row_name and row_name == name_key:
        return 5
    if row_name and row_name in incoming_aliases:
        return 4
    if name_key in row_aliases:
        return 4
    if incoming_aliases & row_aliases:
        return 3
    return 0


def _merge_into(row: HardwareProfile, incoming: Mapping[str, Any], now: _dt.datetime) -> None:
    aliases = _union(_load_list(row.aliases_json), incoming["aliases"], casefold=True)
    peripherals = _union(
        _load_list(row.peripherals_json), incoming["peripherals"], casefold=True
    )
    sources = _union(_load_list(row.sources_json), incoming["sources"], casefold=False)
    rejected = _union(_load_list(row.rejected_json), incoming["rejected"], casefold=False)
    pins = _load_pins(row.pins_json)
    for key, value in incoming["pins"].items():
        if key not in pins or value:
            pins[key] = value

    for field_name, values, limit in (
        ("aliases", aliases, MAX_ALIASES),
        ("peripherals", peripherals, MAX_LIST_ITEMS),
        ("sources", sources, MAX_LIST_ITEMS),
        ("rejected", rejected, MAX_LIST_ITEMS),
    ):
        if len(values) > limit:
            raise HardwareProfileValidationError(
                f"merged {field_name} exceeds {limit} entries; remove stale entries first"
            )
    if len(pins) > MAX_PINS:
        raise HardwareProfileValidationError(
            f"merged pins exceeds {MAX_PINS} entries; remove stale entries first"
        )

    name = incoming["name"]
    merged_payload = {
        "name": name,
        "aliases": aliases,
        "pins": pins,
        "peripherals": peripherals,
        "sources": sources,
        "rejected": rejected,
        **{field_name: incoming[field_name] for field_name in _SCALAR_FIELDS},
    }
    encoded = json.dumps(merged_payload, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_PROFILE_JSON_BYTES:
        raise HardwareProfileValidationError("merged profile exceeds the stored size limit")

    row.name = name
    for field_name in _SCALAR_FIELDS:
        if incoming[field_name]:
            setattr(row, field_name, incoming[field_name])
    row.aliases_json = _dump_json(aliases)
    row.pins_json = _dump_json(pins)
    row.peripherals_json = _dump_json(peripherals)
    row.sources_json = _dump_json(sources)
    row.rejected_json = _dump_json(rejected)
    incoming_confidence = incoming["confidence"]
    current_confidence = float(
        DEFAULT_PROFILE_CONFIDENCE if row.confidence is None else row.confidence
    )
    row.confidence = max(current_confidence, incoming_confidence or 0.0)
    row.use_count = int(row.use_count or 0) + 1
    row.last_used_at = now
    row.updated_at = now


class HardwareProfileStore:
    """Persistence facade for ``hardware_profiles`` rows."""

    def __init__(self, session_factory=None):
        # Resolve SessionLocal lazily so tests can patch database.SessionLocal.
        self._session_factory = session_factory

    @property
    def session_factory(self):
        return self._session_factory or database.SessionLocal

    def _session(self):
        session = self.session_factory()
        ensure_schema(session.get_bind())
        return session

    def upsert(self, profile: Mapping[str, Any]) -> HardwareProfileRecord:
        """Create or merge one profile; only non-empty fields are overwritten."""
        incoming = _normalize_incoming(profile)
        now = _utc_now_naive()
        with self._session() as session:
            row = self._find_merge_target(session, incoming)
            if row is None:
                row = HardwareProfile(
                    id=incoming["id"] or f"hp_{uuid.uuid4().hex}",
                    name=incoming["name"],
                    aliases_json=_dump_json(incoming["aliases"]),
                    mcu=incoming["mcu"],
                    package=incoming["package"],
                    voltage=incoming["voltage"],
                    clock=incoming["clock"],
                    pins_json=_dump_json(incoming["pins"]),
                    peripherals_json=_dump_json(incoming["peripherals"]),
                    toolchain=incoming["toolchain"],
                    sources_json=_dump_json(incoming["sources"]),
                    rejected_json=_dump_json(incoming["rejected"]),
                    confidence=(
                        incoming["confidence"]
                        if incoming["confidence"] is not None
                        else DEFAULT_PROFILE_CONFIDENCE
                    ),
                    use_count=1,
                    last_used_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                _merge_into(row, incoming, now)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HardwareProfileValidationError(
                    "profile id already exists; retry without an explicit id"
                ) from exc
            session.refresh(row)
            return _record(row)

    def _find_merge_target(
        self, session, incoming: Mapping[str, Any]
    ) -> HardwareProfile | None:
        if incoming["id"]:
            return session.get(HardwareProfile, incoming["id"])
        best: HardwareProfile | None = None
        best_score = 0
        for row in session.execute(select(HardwareProfile)).scalars().all():
            score = _match_score(row, incoming)
            if score <= 0:
                continue
            if (
                best is None
                or score > best_score
                or (
                    score == best_score
                    and (row.updated_at or _dt.datetime.min)
                    > (best.updated_at or _dt.datetime.min)
                )
            ):
                best = row
                best_score = score
        return best

    def get(self, profile_id: str) -> HardwareProfileRecord | None:
        profile_id = _clean_text(profile_id, "profile id", MAX_PROFILE_ID_CHARS, required=True)
        with self._session() as session:
            row = session.get(HardwareProfile, profile_id)
            return _record(row) if row is not None else None

    def record_use(self, profile_id: str) -> HardwareProfileRecord | None:
        """Count one real use (profile loaded by the agent)."""
        profile_id = _clean_text(profile_id, "profile id", MAX_PROFILE_ID_CHARS, required=True)
        now = _utc_now_naive()
        with self._session() as session:
            result = session.execute(
                update(HardwareProfile)
                .where(HardwareProfile.id == profile_id)
                .values(
                    use_count=HardwareProfile.use_count + 1,
                    last_used_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            changed = result.rowcount
            session.commit()
        if changed != 1:
            return None
        return self.get(profile_id)

    def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[HardwareProfileRecord]:
        """Match name, aliases and MCU; exact matches rank first."""
        if not isinstance(query, str):
            raise HardwareProfileValidationError("query must be a string")
        query = query.strip()
        if not query:
            return []
        if len(query) > MAX_SEARCH_QUERY_CHARS:
            raise HardwareProfileValidationError(
                f"query exceeds {MAX_SEARCH_QUERY_CHARS} characters"
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise HardwareProfileValidationError(
                f"limit must be an integer between 1 and {MAX_SEARCH_LIMIT}"
            )
        with self._session() as session:
            rows = session.execute(select(HardwareProfile)).scalars().all()
            scored: list[tuple[int, HardwareProfile]] = []
            for row in rows:
                score = _search_score(row, query)
                if score > 0:
                    scored.append((score, row))
            # Stable multi-pass sort: score desc, use_count desc, updated desc, name asc.
            scored.sort(key=lambda item: (item[1].name or "").casefold())
            scored.sort(
                key=lambda item: item[1].updated_at or _dt.datetime.min,
                reverse=True,
            )
            scored.sort(key=lambda item: int(item[1].use_count or 0), reverse=True)
            scored.sort(key=lambda item: item[0], reverse=True)
            return [_record(row) for _, row in scored[:limit]]

    def list_aliases(self) -> list[tuple[str, str]]:
        """Return ``(profile_id, alias)`` pairs for mention detection."""
        with self._session() as session:
            rows = session.execute(
                select(HardwareProfile.id, HardwareProfile.aliases_json)
            ).all()
            pairs: list[tuple[str, str]] = []
            for profile_id, aliases_json in rows:
                for alias in _load_list(aliases_json):
                    if alias.strip():
                        pairs.append((profile_id, alias.strip()))
            return pairs

    def list_all(self, limit: int = DEFAULT_SEARCH_LIMIT) -> list[HardwareProfileRecord]:
        """Return profiles newest-first for the management panel."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise HardwareProfileValidationError(
                f"limit must be an integer between 1 and {MAX_SEARCH_LIMIT}"
            )
        with self._session() as session:
            rows = session.execute(
                select(HardwareProfile)
                .order_by(HardwareProfile.updated_at.desc(), HardwareProfile.name.asc())
                .limit(limit)
            ).scalars().all()
            return [_record(row) for row in rows]


def _search_score(row: HardwareProfile, query: str) -> int:
    needle = query.casefold()
    name = (row.name or "").strip().casefold()
    aliases = [alias.casefold() for alias in _load_list(row.aliases_json)]
    mcu = (row.mcu or "").strip().casefold()
    if name and name == needle:
        return 100
    if any(alias == needle for alias in aliases):
        return 90
    if mcu and mcu == needle:
        return 80
    if name.startswith(needle):
        return 70
    if any(alias.startswith(needle) for alias in aliases):
        return 60
    if needle in name:
        return 50
    if any(needle in alias for alias in aliases):
        return 40
    if mcu and needle in mcu:
        return 30
    return 0
