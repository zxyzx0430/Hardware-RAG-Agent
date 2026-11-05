"""
数据库引擎与会话管理。

SQLAlchemy 2.0 async + SQLite（V1），后续可切换 PostgreSQL。
"""

import os
import logging
import re
import hashlib
import struct
import codecs
import json
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, event, inspect
from sqlalchemy.orm import sessionmaker, DeclarativeBase

_LOGGER = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DB_DIR = _BACKEND_DIR / "data"

# The lightweight config loader must run before the environment is read here:
# pydantic Settings reads .env, but this module only uses os.getenv for the
# default engine. Without this, .env's SQLITE_DB_PATH is silently ignored.
from app.config import load_environment  # noqa: E402

load_environment()

# Only the default location needs the directory created here. A caller that
# points SQLITE_DB_PATH at a temp directory owns that directory's lifecycle, so
# tests never write into the user's backend/data.
_DB_PATH_ENV = os.getenv("SQLITE_DB_PATH")
if _DB_PATH_ENV:
    _db_path = Path(_DB_PATH_ENV)
    if not _db_path.is_absolute():
        _db_path = _BACKEND_DIR / _db_path
    DB_PATH = str(_db_path)
else:
    DB_PATH = str(_DB_DIR / "hardware_rag.db")
if not _DB_PATH_ENV:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})

# Enable SQLite foreign key constraints on every new connection
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except Exception as exc:
        _LOGGER.debug("WAL mode unavailable: %s", exc)
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


LEGACY_BODY_CHUNK_SIZE = 8192
LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_ROWS = 32
LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_BODY_BYTES = 1024 * 1024
# JSON escaping expands any source UTF-8 byte by at most 6x; the fixed JSON keys
# and punctuation fit within 1 KiB, so 8 KiB raw metadata fits within 64 KiB.
LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES = 256 * 1024
LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_CHARS = 64 * 1024
LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES = 8192
LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_FIXED_BYTES = 256
LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_METADATA_BYTES = (
    LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_ROWS
    * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
)
LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_COMBINED_BYTES = (
    LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_BODY_BYTES
    + LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_METADATA_BYTES
)
LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_SOURCE_RAW_BYTES = (
    LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_ROWS
    * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
)
LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_CREATED_AT_MAX_CHARS = 8192
LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS = 512
LEGACY_MESSAGE_BLOB_SNAPSHOT_SOURCE_ROLE_MAX_CHARS = 8192
LEGACY_MESSAGE_BLOB_SNAPSHOT_ORDERING = "created_at_nulls_first_id_asc_v1"
_LEGACY_BODY_GENESIS_CHAIN = "0" * 64
_LEGACY_BODY_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_LEGACY_BODY_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_LEGACY_BODY_MAX_SQLITE_INTEGER = (1 << 63) - 1


@dataclass(frozen=True, slots=True)
class LegacyBodyMigrationState:
    migration_id: str
    session_id: str
    source_key_digest: str
    source_byte_length: int
    source_sha256: str
    chunk_size: int
    next_chunk_index: int
    bytes_appended: int
    chain_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class LegacyBodyChunkReceipt:
    migration_id: str
    chunk_index: int
    byte_offset: int
    byte_length: int
    chunk_sha256: str
    previous_chain_sha256: str
    chain_sha256: str


@dataclass(frozen=True, slots=True)
class LegacyBodyManifest:
    manifest_id: str
    session_id: str
    source_key_digest: str
    byte_length: int
    sha256: str
    chunk_size: int
    chunk_count: int
    chain_sha256: str
    copy_status: str
    source_verification_status: str


@dataclass(frozen=True, slots=True)
class LegacyBodyChunk:
    manifest_id: str
    session_id: str
    chunk_index: int
    byte_offset: int
    byte_length: int
    chunk_bytes: bytes
    chunk_sha256: str
    previous_chain_sha256: str
    chain_sha256: str
    manifest_byte_length: int
    manifest_sha256: str
    manifest_chunk_count: int
    manifest_chain_sha256: str


@dataclass(frozen=True, slots=True)
class MessageBodyWriteState:
    body_id: str
    session_id: str
    next_chunk_offset: int
    bytes_appended: int
    chunk_count: int
    terminal_chunk_offset: int | None
    chain_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class MessageBodyChunkReceipt:
    body_id: str
    byte_offset: int
    byte_length: int
    chunk_sha256: str
    previous_chain_sha256: str
    chain_sha256: str


@dataclass(frozen=True, slots=True)
class MessageBodyManifest:
    body_id: str
    session_id: str
    byte_length: int
    sha256: str
    chunk_size: int
    chunk_count: int
    final_chain_sha256: str
    status: str
    encoding: str


@dataclass(frozen=True, slots=True)
class MessageBodyChunk:
    body_id: str
    byte_offset: int
    next_offset: int
    chunk_length: int
    chunk_bytes: bytes
    chunk_sha256: str
    previous_chain_sha256: str
    chain_sha256: str
    manifest_byte_length: int
    manifest_sha256: str
    manifest_chunk_count: int
    manifest_chain_sha256: str


@dataclass(frozen=True, slots=True)
class LegacyMessageBlobSnapshotState:
    migration_id: str
    snapshot_id: str
    session_id: str
    source_generation: int
    source_message_count: int
    source_non_append_generation: int
    high_water_created_at: str | None
    high_water_message_id: str | None
    capture_cursor: int
    capture_message_id: str | None
    capture_body_offset: int
    capture_body_chain_sha256: str
    capture_chain_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class LegacyMessageBlobSnapshotRowMetadata:
    migration_id: str
    snapshot_id: str
    sequence: int
    message_id: str
    role: str
    created_at: str | None
    metadata_json: str
    body_id: str
    body_byte_length: int
    body_sha256: str | None
    row_hash: str | None
    status: str


@dataclass(frozen=True, slots=True)
class LegacyMessageBlobSnapshotPageReceipt:
    migration_id: str
    snapshot_id: str
    request_cursor: int
    request_message_id: str | None
    request_body_offset: int
    response_cursor: int
    response_message_id: str | None
    response_body_offset: int
    response_body_chain_sha256: str
    response_capture_chain_sha256: str
    row_count: int
    body_bytes_captured: int
    status: str
    rows: tuple[LegacyMessageBlobSnapshotRowMetadata, ...]
    # Exact UTF-8 payload counts; these bound returned metadata, not exact RSS.
    metadata_bytes_captured: int = 0
    combined_bytes_captured: int = 0
    combined_bytes_limit: int = LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_COMBINED_BYTES
    source_metadata_raw_bytes_captured: int = 0


@dataclass(frozen=True, slots=True)
class LegacyMessageBlobSnapshotBodyChunk:
    migration_id: str
    snapshot_id: str
    message_id: str
    row_hash: str
    byte_offset: int
    next_offset: int
    chunk_bytes: bytes
    chunk_sha256: str
    body_byte_length: int
    body_sha256: str


def _legacy_body_query_rows(handle, statement, parameters=()):
    execute = getattr(handle, "execute", None)
    if not callable(execute):
        raise TypeError("legacy body storage requires a restricted SQL handle")
    result = execute(statement, parameters)
    rows = getattr(result, "rows", None)
    if type(rows) is not tuple or any(type(row) is not tuple for row in rows):
        raise TypeError("legacy body SQL handle must return materialized rows")
    return rows


def _legacy_body_execute_write(handle, statement, parameters=()):
    execute = getattr(handle, "execute", None)
    if not callable(execute):
        raise TypeError("legacy body storage requires a restricted SQL handle")
    result = execute(statement, parameters)
    if type(result) is not int:
        raise TypeError("legacy body SQL handle must return an integer row count")
    return result


def _legacy_message_blob_validate_text_size_preflight(
    value_type, char_length, nul_position, *, field_name, max_chars, nullable=False, min_chars=0
):
    if value_type == "null" and nullable:
        if char_length is not None or nul_position is not None:
            raise ValueError(f"{field_name} NULL size preflight is invalid")
        return None
    if (
        value_type != "text"
        or type(char_length) is not int
        or not min_chars <= char_length <= max_chars
        or type(nul_position) is not int
        or nul_position != 0
    ):
        raise ValueError(f"{field_name} exceeds its stored text bound")
    return char_length


def _legacy_message_blob_source_text_size_preflight(
    handle, *, session_id, rowid, field_names
):
    """Check bounded scalar sizes before selecting legacy source TEXT values."""
    field_bounds = {
        "id": (LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS, False, 1),
        "role": (LEGACY_MESSAGE_BLOB_SNAPSHOT_SOURCE_ROLE_MAX_CHARS, False, 0),
        "created_at": (
            LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_CREATED_AT_MAX_CHARS,
            True,
            0,
        ),
    }
    _legacy_body_validate_session_id(session_id)
    if (
        type(rowid) is not int
        or not -(1 << 63) <= rowid <= _LEGACY_BODY_MAX_SQLITE_INTEGER
        or type(field_names) is not tuple
        or not field_names
        or any(type(name) is not str for name in field_names)
        or len(set(field_names)) != len(field_names)
        or any(name not in field_bounds for name in field_names)
    ):
        raise ValueError("legacy Message source text preflight request is invalid")
    columns = ", ".join(
        expression
        for field_name in field_names
        for expression in (
            f"typeof({field_name})",
            f"length({field_name})",
            f"instr({field_name}, char(0))",
        )
    )
    rows = _legacy_body_query_rows(
        handle,
        f"SELECT {columns} FROM messages WHERE session_id = ? AND rowid = ? LIMIT 2",
        (session_id, rowid),
    )
    if len(rows) != 1:
        raise LookupError("legacy Message source row is unavailable for text preflight")
    preflight = rows[0]
    field_char_lengths = {}
    for index, field_name in enumerate(field_names):
        max_chars, nullable, min_chars = field_bounds[field_name]
        field_char_lengths[field_name] = _legacy_message_blob_validate_text_size_preflight(
            preflight[index * 3],
            preflight[index * 3 + 1],
            preflight[index * 3 + 2],
            field_name=field_name,
            max_chars=max_chars,
            nullable=nullable,
            min_chars=min_chars,
        )
    return field_char_lengths


def _legacy_message_blob_verify_source_text_value(
    value, expected_char_length, *, field_name, nullable=False
):
    if expected_char_length is None:
        if nullable and value is None:
            return 0
        raise ValueError(f"legacy Message source {field_name} changed after preflight")
    if (
        type(value) is not str
        or len(value) != expected_char_length
        or "\x00" in value
    ):
        raise ValueError(f"legacy Message source {field_name} changed after preflight")
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise ValueError(f"legacy Message source {field_name} is not valid UTF-8") from None


def _legacy_body_validate_id(value, field_name):
    if type(value) is not str or _LEGACY_BODY_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 32 lowercase hexadecimal characters")


def _legacy_body_validate_hash(value, field_name):
    if type(value) is not str or _LEGACY_BODY_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _legacy_body_validate_session_id(session_id):
    if (
        type(session_id) is not str
        or not session_id
        or len(session_id) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
    ):
        raise ValueError("session_id is invalid")


def _legacy_body_owner_identity(handle, session_id):
    """Recheck the host-bound owner against the same caller transaction."""
    _legacy_body_validate_session_id(session_id)
    try:
        from app.api.rolling_history_owner_scope import get_rolling_history_owner_identity

        identity = get_rolling_history_owner_identity()
    except Exception:
        raise PermissionError("legacy body owner scope is unavailable") from None

    principal_hash = getattr(identity, "principal_hash", None)
    incarnation_id = getattr(identity, "incarnation_id", None)
    if (
        getattr(identity, "session_id", None) != session_id
        or type(principal_hash) is not str
        or _LEGACY_BODY_HASH_PATTERN.fullmatch(principal_hash) is None
        or type(incarnation_id) is not str
        or _LEGACY_BODY_ID_PATTERN.fullmatch(incarnation_id) is None
    ):
        raise PermissionError("legacy body owner scope does not match session_id")

    rows = _legacy_body_query_rows(
        handle,
        "SELECT s.id, b.owner_principal_hash, b.incarnation_id, i.retired_at "
        "FROM sessions AS s "
        "JOIN session_owner_bindings AS b ON b.session_id = s.id "
        "JOIN session_incarnations AS i ON i.id = b.incarnation_id "
        "WHERE s.id = ? LIMIT 2",
        (session_id,),
    )
    if (
        len(rows) != 1
        or rows[0][0] != session_id
        or rows[0][1] != principal_hash
        or rows[0][2] != incarnation_id
        or rows[0][3] is not None
    ):
        raise PermissionError("legacy body session ownership is stale or unavailable")
    return principal_hash, incarnation_id


def _legacy_body_load_migration(handle, session_id, migration_id):
    rows = _legacy_body_query_rows(
        handle,
        "SELECT migration_id, session_id, incarnation_id, owner_principal_hash, "
        "source_key_digest, source_byte_length, source_sha256, chunk_size, "
        "next_chunk_index, bytes_appended, chain_sha256, status, "
        "verified_byte_length, verified_sha256, source_verification_status "
        "FROM legacy_body_migrations WHERE migration_id = ? AND session_id = ? LIMIT 2",
        (migration_id, session_id),
    )
    if len(rows) != 1:
        raise LookupError("legacy body migration is unavailable")
    return rows[0]


def _legacy_body_check_row_owner(row, principal_hash, incarnation_id):
    if row[2] != incarnation_id or row[3] != principal_hash:
        raise PermissionError("legacy body migration owner binding has changed")


def _legacy_body_state_from_row(row):
    return LegacyBodyMigrationState(
        migration_id=row[0],
        session_id=row[1],
        source_key_digest=row[4],
        source_byte_length=row[5],
        source_sha256=row[6],
        chunk_size=row[7],
        next_chunk_index=row[8],
        bytes_appended=row[9],
        chain_sha256=row[10],
        status=row[11],
    )


def _legacy_body_expected_chunk_count(byte_length):
    return (byte_length + LEGACY_BODY_CHUNK_SIZE - 1) // LEGACY_BODY_CHUNK_SIZE


def _legacy_body_expected_chunk_length(byte_length, chunk_index):
    offset = chunk_index * LEGACY_BODY_CHUNK_SIZE
    if offset >= byte_length:
        raise ValueError("chunk_index is outside the declared body")
    return min(LEGACY_BODY_CHUNK_SIZE, byte_length - offset)


def _legacy_body_chain_hash(previous_chain_sha256, chunk_index, byte_offset, byte_length, chunk_sha256):
    payload = (
        b"legacy-body-chunk-chain-v1\x00"
        + bytes.fromhex(previous_chain_sha256)
        + struct.pack(">QQI", chunk_index, byte_offset, byte_length)
        + bytes.fromhex(chunk_sha256)
    )
    return hashlib.sha256(payload).hexdigest()


def _legacy_body_get_previous_chain(handle, migration_id, chunk_index):
    if chunk_index == 0:
        return _LEGACY_BODY_GENESIS_CHAIN
    rows = _legacy_body_query_rows(
        handle,
        "SELECT chain_sha256 FROM legacy_body_chunks "
        "WHERE migration_id = ? AND chunk_index = ? LIMIT 1",
        (migration_id, chunk_index - 1),
    )
    if len(rows) != 1 or type(rows[0][0]) is not str:
        raise ValueError("legacy body chunk chain is incomplete")
    _legacy_body_validate_hash(rows[0][0], "previous chain hash")
    return rows[0][0]


def _legacy_body_validate_chunk_record(
    row,
    *,
    migration_id,
    byte_length,
    chunk_index,
    previous_chain_sha256,
    expected_body=None,
):
    byte_offset = chunk_index * LEGACY_BODY_CHUNK_SIZE
    expected_length = _legacy_body_expected_chunk_length(byte_length, chunk_index)
    stored_offset, stored_length, stored_chunk_sha256, stored_previous, stored_chain = row[:5]
    body = row[5] if len(row) > 5 else expected_body
    if (
        stored_offset != byte_offset
        or stored_length != expected_length
        or stored_previous != previous_chain_sha256
        or type(body) is not bytes
        or len(body) != expected_length
    ):
        raise ValueError("legacy body chunk metadata is inconsistent")
    actual_chunk_sha256 = hashlib.sha256(body).hexdigest()
    if actual_chunk_sha256 != stored_chunk_sha256:
        raise ValueError("legacy body chunk SHA-256 verification failed")
    actual_chain_sha256 = _legacy_body_chain_hash(
        previous_chain_sha256,
        chunk_index,
        byte_offset,
        expected_length,
        actual_chunk_sha256,
    )
    if actual_chain_sha256 != stored_chain:
        raise ValueError("legacy body chunk chain verification failed")
    if expected_body is not None and body != expected_body:
        raise ValueError("legacy body chunk retry does not match the stored bytes")
    return LegacyBodyChunkReceipt(
        migration_id=migration_id,
        chunk_index=chunk_index,
        byte_offset=byte_offset,
        byte_length=expected_length,
        chunk_sha256=actual_chunk_sha256,
        previous_chain_sha256=previous_chain_sha256,
        chain_sha256=actual_chain_sha256,
    )


def begin_legacy_body_migration_in_transaction(
    handle,
    *,
    session_id,
    migration_id,
    source_key_digest,
    source_byte_length,
    source_sha256,
):
    """Start or resume a body copy through the caller's owner-bound transaction."""
    _legacy_body_validate_id(migration_id, "migration_id")
    _legacy_body_validate_hash(source_key_digest, "source_key_digest")
    _legacy_body_validate_hash(source_sha256, "source_sha256")
    if (
        type(source_byte_length) is not int
        or source_byte_length < 0
        or source_byte_length > _LEGACY_BODY_MAX_SQLITE_INTEGER
    ):
        raise ValueError("source_byte_length must be a non-negative SQLite integer")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    rows = _legacy_body_query_rows(
        handle,
        "SELECT migration_id, session_id, incarnation_id, owner_principal_hash, "
        "source_key_digest, source_byte_length, source_sha256 "
        "FROM legacy_body_migrations WHERE migration_id = ? LIMIT 2",
        (migration_id,),
    )
    if rows:
        existing = rows[0]
        if (
            len(rows) != 1
            or existing[1] != session_id
            or existing[2] != incarnation_id
            or existing[3] != principal_hash
            or existing[4] != source_key_digest
            or existing[5] != source_byte_length
            or existing[6] != source_sha256
        ):
            raise ValueError("migration_id was already bound to different source metadata")
        return _legacy_body_state_from_row(
            _legacy_body_load_migration(handle, session_id, migration_id)
        )

    _legacy_body_execute_write(
        handle,
        "INSERT INTO legacy_body_migrations "
        "(migration_id, session_id, incarnation_id, owner_principal_hash, "
        "source_key_digest, source_byte_length, source_sha256, chunk_size, "
        "next_chunk_index, bytes_appended, chain_sha256, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 'building')",
        (
            migration_id,
            session_id,
            incarnation_id,
            principal_hash,
            source_key_digest,
            source_byte_length,
            source_sha256,
            LEGACY_BODY_CHUNK_SIZE,
            _LEGACY_BODY_GENESIS_CHAIN,
        ),
    )
    return _legacy_body_state_from_row(
        _legacy_body_load_migration(handle, session_id, migration_id)
    )


def append_legacy_body_chunk_in_transaction(
    handle,
    *,
    session_id,
    migration_id,
    chunk_index,
    chunk_bytes,
):
    """Append one 8 KiB-bounded chunk, accepting exact retries only."""
    _legacy_body_validate_id(migration_id, "migration_id")
    if type(chunk_index) is not int or chunk_index < 0:
        raise ValueError("chunk_index must be a non-negative integer")
    if type(chunk_bytes) is not bytes or not 1 <= len(chunk_bytes) <= LEGACY_BODY_CHUNK_SIZE:
        raise ValueError("chunk_bytes must contain between 1 and 8192 bytes")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    migration = _legacy_body_load_migration(handle, session_id, migration_id)
    _legacy_body_check_row_owner(migration, principal_hash, incarnation_id)
    source_byte_length = migration[5]
    existing_rows = _legacy_body_query_rows(
        handle,
        "SELECT byte_offset, chunk_length, chunk_sha256, previous_chain_sha256, "
        "chain_sha256, body FROM legacy_body_chunks "
        "WHERE migration_id = ? AND chunk_index = ? LIMIT 2",
        (migration_id, chunk_index),
    )
    if existing_rows:
        if len(existing_rows) != 1:
            raise ValueError("legacy body chunk identity is ambiguous")
        previous_chain = _legacy_body_get_previous_chain(handle, migration_id, chunk_index)
        return _legacy_body_validate_chunk_record(
            existing_rows[0],
            migration_id=migration_id,
            byte_length=source_byte_length,
            chunk_index=chunk_index,
            previous_chain_sha256=previous_chain,
            expected_body=chunk_bytes,
        )

    if migration[11] != "building" or chunk_index != migration[8]:
        raise ValueError("chunk_index is not the next appendable migration cursor")
    expected_length = _legacy_body_expected_chunk_length(source_byte_length, chunk_index)
    byte_offset = migration[9]
    if byte_offset != chunk_index * LEGACY_BODY_CHUNK_SIZE or len(chunk_bytes) != expected_length:
        raise ValueError("chunk length or offset does not match the migration cursor")
    previous_chain = migration[10]
    if chunk_index == 0:
        if previous_chain != _LEGACY_BODY_GENESIS_CHAIN or byte_offset != 0:
            raise ValueError("legacy body migration genesis state is inconsistent")
    else:
        previous_chunk_rows = _legacy_body_query_rows(
            handle,
            "SELECT byte_offset, chunk_length, chain_sha256 "
            "FROM legacy_body_chunks WHERE migration_id = ? AND chunk_index = ? LIMIT 1",
            (migration_id, chunk_index - 1),
        )
        if (
            len(previous_chunk_rows) != 1
            or previous_chunk_rows[0][0] + previous_chunk_rows[0][1] != byte_offset
            or previous_chunk_rows[0][2] != previous_chain
        ):
            raise ValueError("legacy body migration chain cursor is inconsistent")

    chunk_sha256 = hashlib.sha256(chunk_bytes).hexdigest()
    chain_sha256 = _legacy_body_chain_hash(
        previous_chain,
        chunk_index,
        byte_offset,
        len(chunk_bytes),
        chunk_sha256,
    )
    _legacy_body_execute_write(
        handle,
        "INSERT INTO legacy_body_chunks "
        "(migration_id, chunk_index, byte_offset, chunk_length, "
        "previous_chain_sha256, chunk_sha256, chain_sha256, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            migration_id,
            chunk_index,
            byte_offset,
            len(chunk_bytes),
            previous_chain,
            chunk_sha256,
            chain_sha256,
            chunk_bytes,
        ),
    )
    changed = _legacy_body_execute_write(
        handle,
        "UPDATE legacy_body_migrations "
        "SET next_chunk_index = ?, bytes_appended = ?, chain_sha256 = ? "
        "WHERE migration_id = ? AND session_id = ? AND status = 'building' "
        "AND next_chunk_index = ? AND bytes_appended = ? AND chain_sha256 = ?",
        (
            chunk_index + 1,
            byte_offset + len(chunk_bytes),
            chain_sha256,
            migration_id,
            session_id,
            chunk_index,
            byte_offset,
            previous_chain,
        ),
    )
    if changed != 1:
        raise ValueError("legacy body migration cursor changed during append")
    return LegacyBodyChunkReceipt(
        migration_id=migration_id,
        chunk_index=chunk_index,
        byte_offset=byte_offset,
        byte_length=len(chunk_bytes),
        chunk_sha256=chunk_sha256,
        previous_chain_sha256=previous_chain,
        chain_sha256=chain_sha256,
    )


def _legacy_body_verify_all_chunks(handle, migration):
    migration_id = migration[0]
    source_byte_length = migration[5]
    expected_count = _legacy_body_expected_chunk_count(source_byte_length)
    if migration[8] != expected_count or migration[9] != source_byte_length:
        raise ValueError("legacy body migration is incomplete")
    count_rows = _legacy_body_query_rows(
        handle,
        "SELECT COUNT(*) FROM legacy_body_chunks WHERE migration_id = ?",
        (migration_id,),
    )
    if len(count_rows) != 1 or count_rows[0][0] != expected_count:
        raise ValueError("legacy body migration chunk count does not match its manifest")

    body_sha256 = hashlib.sha256()
    previous_chain = _LEGACY_BODY_GENESIS_CHAIN
    verified_byte_length = 0
    for chunk_index in range(expected_count):
        rows = _legacy_body_query_rows(
            handle,
            "SELECT byte_offset, chunk_length, chunk_sha256, previous_chain_sha256, "
            "chain_sha256, body FROM legacy_body_chunks "
            "WHERE migration_id = ? AND chunk_index = ? LIMIT 1",
            (migration_id, chunk_index),
        )
        if len(rows) != 1:
            raise ValueError("legacy body migration has a missing chunk")
        receipt = _legacy_body_validate_chunk_record(
            rows[0],
            migration_id=migration_id,
            byte_length=source_byte_length,
            chunk_index=chunk_index,
            previous_chain_sha256=previous_chain,
        )
        body_sha256.update(rows[0][5])
        verified_byte_length += receipt.byte_length
        previous_chain = receipt.chain_sha256

    if (
        verified_byte_length != source_byte_length
        or body_sha256.hexdigest() != migration[6]
        or previous_chain != migration[10]
    ):
        raise ValueError("legacy body full length, SHA-256, or chain verification failed")
    return expected_count, previous_chain


def finalize_legacy_body_migration_in_transaction(handle, *, session_id, migration_id):
    """Verify every stored chunk and freeze the copied-body manifest.

    This verifies the submitted bytes against the caller-declared digest. It
    deliberately does not read or delete the source Settings.value row because
    the current owner SQL handle cannot stream an inline SQLite TEXT value.
    """
    _legacy_body_validate_id(migration_id, "migration_id")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    migration = _legacy_body_load_migration(handle, session_id, migration_id)
    _legacy_body_check_row_owner(migration, principal_hash, incarnation_id)
    chunk_count, chain_sha256 = _legacy_body_verify_all_chunks(handle, migration)
    if migration[11] == "building":
        changed = _legacy_body_execute_write(
            handle,
            "UPDATE legacy_body_migrations "
            "SET status = 'complete', verified_byte_length = ?, verified_sha256 = ? "
            "WHERE migration_id = ? AND session_id = ? AND status = 'building' "
            "AND next_chunk_index = ? AND bytes_appended = ? AND chain_sha256 = ?",
            (
                migration[5],
                migration[6],
                migration_id,
                session_id,
                migration[8],
                migration[9],
                chain_sha256,
            ),
        )
        if changed != 1:
            raise ValueError("legacy body migration could not be finalized")
    elif migration[11] != "complete":
        raise ValueError("legacy body migration status is invalid")
    return LegacyBodyManifest(
        manifest_id=migration_id,
        session_id=session_id,
        source_key_digest=migration[4],
        byte_length=migration[5],
        sha256=migration[6],
        chunk_size=LEGACY_BODY_CHUNK_SIZE,
        chunk_count=chunk_count,
        chain_sha256=chain_sha256,
        copy_status="complete",
        source_verification_status=migration[14],
    )


def read_legacy_body_chunk_in_transaction(
    handle,
    *,
    session_id,
    manifest_id,
    chunk_index,
    max_rows,
    max_bytes,
):
    """Return one owner-checked BLOB chunk without leaving the bound transaction."""
    _legacy_body_validate_id(manifest_id, "manifest_id")
    if type(chunk_index) is not int or chunk_index < 0:
        raise ValueError("chunk_index must be a non-negative integer")
    if type(max_rows) is not int or max_rows != 3:
        raise ValueError(
            "max_rows must be exactly 3: one owner row, one joined metadata row, "
            "and one BLOB row"
        )
    if type(max_bytes) is not int or not 1 <= max_bytes <= LEGACY_BODY_CHUNK_SIZE:
        raise ValueError("max_bytes must be between 1 and 8192")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    rows = _legacy_body_query_rows(
        handle,
        "SELECT m.migration_id, m.session_id, m.incarnation_id, "
        "m.owner_principal_hash, m.source_key_digest, m.source_byte_length, "
        "m.source_sha256, m.chunk_size, m.next_chunk_index, m.bytes_appended, "
        "m.chain_sha256, m.status, m.verified_byte_length, m.verified_sha256, "
        "m.source_verification_status, c.byte_offset, c.chunk_length, "
        "c.chunk_sha256, c.previous_chain_sha256, c.chain_sha256, p.chain_sha256 "
        "FROM legacy_body_migrations AS m "
        "JOIN legacy_body_chunks AS c ON c.migration_id = m.migration_id "
        "LEFT JOIN legacy_body_chunks AS p "
        "ON p.migration_id = c.migration_id AND p.chunk_index = c.chunk_index - 1 "
        "WHERE m.migration_id = ? AND m.session_id = ? AND c.chunk_index = ? "
        "LIMIT 1",
        (manifest_id, session_id, chunk_index),
    )
    if len(rows) != 1:
        raise LookupError("legacy body chunk is unavailable")
    row = rows[0]
    if row[2] != incarnation_id or row[3] != principal_hash:
        raise PermissionError("legacy body manifest owner binding has changed")
    expected_count = _legacy_body_expected_chunk_count(row[5])
    if (
        row[11] != "complete"
        or row[12] != row[5]
        or row[13] != row[6]
        or row[7] != LEGACY_BODY_CHUNK_SIZE
        or row[8] != expected_count
        or row[9] != row[5]
        or (chunk_index == expected_count - 1 and row[10] != row[19])
    ):
        raise ValueError("legacy body manifest is not complete")
    if row[14] != "verified_bounded_blob":
        raise PermissionError(
            "legacy inline source is unverified; bounded chunk reads are disabled"
        )
    if chunk_index >= expected_count:
        raise LookupError("legacy body chunk is unavailable")
    expected_length = _legacy_body_expected_chunk_length(row[5], chunk_index)
    if expected_length > max_bytes:
        raise ValueError("legacy body chunk exceeds max_bytes")
    metadata = row[15:20]
    previous_chain = (
        _LEGACY_BODY_GENESIS_CHAIN if chunk_index == 0 else row[20]
    )
    if (
        (chunk_index == 0 and row[20] is not None)
        or (chunk_index > 0 and type(previous_chain) is not str)
        or metadata[0] != chunk_index * LEGACY_BODY_CHUNK_SIZE
        or metadata[1] != expected_length
        or metadata[3] != previous_chain
    ):
        raise ValueError("legacy body chunk offset or chain cursor is inconsistent")
    body_rows = _legacy_body_query_rows(
        handle,
        "SELECT body FROM legacy_body_chunks "
        "WHERE migration_id = ? AND chunk_index = ? LIMIT 1",
        (manifest_id, chunk_index),
    )
    if len(body_rows) != 1 or type(body_rows[0][0]) is not bytes:
        raise ValueError("legacy body chunk BLOB is unavailable")
    body = body_rows[0][0]
    verified_row = metadata + (body,)
    receipt = _legacy_body_validate_chunk_record(
        verified_row,
        migration_id=manifest_id,
        byte_length=row[5],
        chunk_index=chunk_index,
        previous_chain_sha256=previous_chain,
    )
    return LegacyBodyChunk(
        manifest_id=manifest_id,
        session_id=session_id,
        chunk_index=chunk_index,
        byte_offset=receipt.byte_offset,
        byte_length=receipt.byte_length,
        chunk_bytes=body,
        chunk_sha256=receipt.chunk_sha256,
        previous_chain_sha256=receipt.previous_chain_sha256,
        chain_sha256=receipt.chain_sha256,
        manifest_byte_length=row[5],
        manifest_sha256=row[6],
        manifest_chunk_count=expected_count,
        manifest_chain_sha256=row[10],
    )


def _message_body_chain_hash(previous_chain_sha256, byte_offset, byte_length, chunk_sha256):
    chunk_index = byte_offset // LEGACY_BODY_CHUNK_SIZE
    payload = (
        b"message-body-chunk-chain-v1\x00"
        + bytes.fromhex(previous_chain_sha256)
        + struct.pack(">QQI", chunk_index, byte_offset, byte_length)
        + bytes.fromhex(chunk_sha256)
    )
    return hashlib.sha256(payload).hexdigest()


def _message_body_load(handle, session_id, body_id):
    rows = _legacy_body_query_rows(
        handle,
        "SELECT body_id, session_id, incarnation_id, owner_principal_hash, status, "
        "next_chunk_offset, bytes_appended, chunk_count, terminal_chunk_offset, "
        "chain_sha256, byte_length, sha256, final_chain_sha256 "
        "FROM message_body_manifests WHERE body_id = ? AND session_id = ? LIMIT 2",
        (body_id, session_id),
    )
    if len(rows) != 1:
        raise LookupError("message body is unavailable")
    return rows[0]


def _message_body_check_owner(row, principal_hash, incarnation_id):
    if row[2] != incarnation_id or row[3] != principal_hash:
        raise PermissionError("message body owner binding has changed")


def _message_body_state(row):
    return MessageBodyWriteState(
        body_id=row[0],
        session_id=row[1],
        next_chunk_offset=row[5],
        bytes_appended=row[6],
        chunk_count=row[7],
        terminal_chunk_offset=row[8],
        chain_sha256=row[9],
        status=row[4],
    )


def _message_body_previous_chain(handle, body_id, byte_offset):
    if byte_offset == 0:
        return _LEGACY_BODY_GENESIS_CHAIN
    rows = _legacy_body_query_rows(
        handle,
        "SELECT byte_offset, chunk_length, chain_sha256 FROM message_body_chunks "
        "WHERE body_id = ? AND byte_offset = ? LIMIT 1",
        (body_id, byte_offset - LEGACY_BODY_CHUNK_SIZE),
    )
    if (
        len(rows) != 1
        or rows[0][0] != byte_offset - LEGACY_BODY_CHUNK_SIZE
        or rows[0][1] != LEGACY_BODY_CHUNK_SIZE
        or type(rows[0][2]) is not str
    ):
        raise ValueError("message body chunk chain is incomplete")
    _legacy_body_validate_hash(rows[0][2], "previous chain hash")
    return rows[0][2]


def _message_body_validate_chunk_record(
    row,
    *,
    body_id,
    byte_offset,
    previous_chain_sha256,
    expected_body=None,
):
    chunk_length, stored_sha256, stored_previous_chain, stored_chain, body = row
    if (
        byte_offset < 0
        or byte_offset % LEGACY_BODY_CHUNK_SIZE != 0
        or not 1 <= chunk_length <= LEGACY_BODY_CHUNK_SIZE
        or stored_previous_chain != previous_chain_sha256
        or type(body) is not bytes
        or len(body) != chunk_length
    ):
        raise ValueError("message body chunk metadata is inconsistent")
    actual_sha256 = hashlib.sha256(body).hexdigest()
    if actual_sha256 != stored_sha256:
        raise ValueError("message body chunk SHA-256 verification failed")
    actual_chain = _message_body_chain_hash(
        previous_chain_sha256,
        byte_offset,
        chunk_length,
        actual_sha256,
    )
    if actual_chain != stored_chain:
        raise ValueError("message body chunk chain verification failed")
    if expected_body is not None and expected_body != body:
        raise ValueError("message body chunk retry does not match the stored bytes")
    return MessageBodyChunkReceipt(
        body_id=body_id,
        byte_offset=byte_offset,
        byte_length=chunk_length,
        chunk_sha256=actual_sha256,
        previous_chain_sha256=previous_chain_sha256,
        chain_sha256=actual_chain,
    )


def begin_message_body_write_in_transaction(
    handle, *, session_id: str, body_id: str
) -> MessageBodyWriteState:
    """Create or resume an owner-bound message body write."""
    _legacy_body_validate_id(body_id, "body_id")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    existing_rows = _legacy_body_query_rows(
        handle,
        "SELECT body_id, session_id, incarnation_id, owner_principal_hash "
        "FROM message_body_manifests WHERE body_id = ? LIMIT 2",
        (body_id,),
    )
    if existing_rows:
        existing = existing_rows[0]
        if (
            len(existing_rows) != 1
            or existing[1] != session_id
            or existing[2] != incarnation_id
            or existing[3] != principal_hash
        ):
            raise ValueError("body_id is already bound to another owner or session")
        return _message_body_state(_message_body_load(handle, session_id, body_id))
    _legacy_body_execute_write(
        handle,
        "INSERT INTO message_body_manifests "
        "(body_id, session_id, incarnation_id, owner_principal_hash) "
        "VALUES (?, ?, ?, ?)",
        (body_id, session_id, incarnation_id, principal_hash),
    )
    return _message_body_state(_message_body_load(handle, session_id, body_id))


def append_message_body_chunk_in_transaction(
    handle,
    *,
    session_id: str,
    body_id: str,
    byte_offset: int,
    chunk_bytes: bytes,
) -> MessageBodyChunkReceipt:
    """Append a contiguous UTF-8 byte chunk; only byte-identical retries replay."""
    _legacy_body_validate_id(body_id, "body_id")
    if (
        type(byte_offset) is not int
        or byte_offset < 0
        or byte_offset > _LEGACY_BODY_MAX_SQLITE_INTEGER - LEGACY_BODY_CHUNK_SIZE
        or byte_offset % LEGACY_BODY_CHUNK_SIZE != 0
    ):
        raise ValueError("byte_offset must be a non-negative aligned SQLite integer")
    if type(chunk_bytes) is not bytes or not 1 <= len(chunk_bytes) <= LEGACY_BODY_CHUNK_SIZE:
        raise ValueError("chunk_bytes must contain between 1 and 8192 bytes")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    body = _message_body_load(handle, session_id, body_id)
    _message_body_check_owner(body, principal_hash, incarnation_id)
    existing_rows = _legacy_body_query_rows(
        handle,
        "SELECT chunk_length, chunk_sha256, previous_chain_sha256, chain_sha256, body "
        "FROM message_body_chunks WHERE body_id = ? AND byte_offset = ? LIMIT 2",
        (body_id, byte_offset),
    )
    if existing_rows:
        if len(existing_rows) != 1:
            raise ValueError("message body chunk identity is ambiguous")
        previous_chain = _message_body_previous_chain(handle, body_id, byte_offset)
        return _message_body_validate_chunk_record(
            existing_rows[0],
            body_id=body_id,
            byte_offset=byte_offset,
            previous_chain_sha256=previous_chain,
            expected_body=chunk_bytes,
        )
    if (
        body[4] != "building"
        or body[8] is not None
        or byte_offset != body[5]
        or body[6] != byte_offset
    ):
        raise ValueError("message body append is gapped, terminal, or already finalized")
    previous_chain = _message_body_previous_chain(handle, body_id, byte_offset)
    if body[9] != previous_chain:
        raise ValueError("message body migration chain cursor is inconsistent")
    chunk_sha256 = hashlib.sha256(chunk_bytes).hexdigest()
    chain_sha256 = _message_body_chain_hash(
        previous_chain,
        byte_offset,
        len(chunk_bytes),
        chunk_sha256,
    )
    _legacy_body_execute_write(
        handle,
        "INSERT INTO message_body_chunks "
        "(body_id, byte_offset, chunk_length, previous_chain_sha256, "
        "chunk_sha256, chain_sha256, body) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            body_id,
            byte_offset,
            len(chunk_bytes),
            previous_chain,
            chunk_sha256,
            chain_sha256,
            chunk_bytes,
        ),
    )
    terminal_offset = byte_offset if len(chunk_bytes) < LEGACY_BODY_CHUNK_SIZE else None
    changed = _legacy_body_execute_write(
        handle,
        "UPDATE message_body_manifests SET next_chunk_offset = ?, bytes_appended = ?, "
        "chunk_count = ?, terminal_chunk_offset = ?, chain_sha256 = ? "
        "WHERE body_id = ? AND session_id = ? AND status = 'building' "
        "AND next_chunk_offset = ? AND bytes_appended = ? AND chunk_count = ? "
        "AND chain_sha256 = ? AND terminal_chunk_offset IS NULL",
        (
            byte_offset + LEGACY_BODY_CHUNK_SIZE,
            body[6] + len(chunk_bytes),
            body[7] + 1,
            terminal_offset,
            chain_sha256,
            body_id,
            session_id,
            byte_offset,
            body[6],
            body[7],
            previous_chain,
        ),
    )
    if changed != 1:
        raise ValueError("message body append cursor changed")
    return MessageBodyChunkReceipt(
        body_id=body_id,
        byte_offset=byte_offset,
        byte_length=len(chunk_bytes),
        chunk_sha256=chunk_sha256,
        previous_chain_sha256=previous_chain,
        chain_sha256=chain_sha256,
    )


def _message_body_verify_chunks(handle, body):
    body_id = body[0]
    chunk_count = body[7]
    if (
        type(chunk_count) is not int
        or chunk_count < 0
        or body[5] != chunk_count * LEGACY_BODY_CHUNK_SIZE
        or body[8] is not None and body[8] != (chunk_count - 1) * LEGACY_BODY_CHUNK_SIZE
    ):
        raise ValueError("message body append state is inconsistent")
    count_rows = _legacy_body_query_rows(
        handle,
        "SELECT COUNT(*) FROM message_body_chunks WHERE body_id = ?",
        (body_id,),
    )
    if len(count_rows) != 1 or count_rows[0][0] != chunk_count:
        raise ValueError("message body chunk count does not match its cursor")

    body_sha256 = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    previous_chain = _LEGACY_BODY_GENESIS_CHAIN
    verified_length = 0
    for chunk_index in range(chunk_count):
        byte_offset = chunk_index * LEGACY_BODY_CHUNK_SIZE
        rows = _legacy_body_query_rows(
            handle,
            "SELECT chunk_length, chunk_sha256, previous_chain_sha256, "
            "chain_sha256, body FROM message_body_chunks "
            "WHERE body_id = ? AND byte_offset = ? LIMIT 1",
            (body_id, byte_offset),
        )
        if len(rows) != 1:
            raise ValueError("message body has a missing chunk")
        chunk_length = rows[0][0]
        if chunk_index < chunk_count - 1 and chunk_length != LEGACY_BODY_CHUNK_SIZE:
            raise ValueError("a non-final message body chunk is shorter than 8192 bytes")
        if chunk_index == chunk_count - 1:
            if not 1 <= chunk_length <= LEGACY_BODY_CHUNK_SIZE:
                raise ValueError("final message body chunk has an invalid length")
            expected_terminal = (
                byte_offset if chunk_length < LEGACY_BODY_CHUNK_SIZE else None
            )
            if body[8] != expected_terminal:
                raise ValueError("message body terminal chunk marker is inconsistent")
        receipt = _message_body_validate_chunk_record(
            rows[0],
            body_id=body_id,
            byte_offset=byte_offset,
            previous_chain_sha256=previous_chain,
        )
        try:
            decoder.decode(rows[0][4], final=False)
        except UnicodeDecodeError:
            raise ValueError("message body is not valid UTF-8") from None
        body_sha256.update(rows[0][4])
        verified_length += receipt.byte_length
        previous_chain = receipt.chain_sha256
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        raise ValueError("message body ends with incomplete UTF-8") from None

    if chunk_count == 0 and body[8] is not None:
        raise ValueError("empty message body has a terminal chunk marker")
    if (
        verified_length != body[6]
        or previous_chain != body[9]
        or (body[10] is not None and body[10] != verified_length)
        or (body[11] is not None and body[11] != body_sha256.hexdigest())
        or (body[12] is not None and body[12] != previous_chain)
    ):
        raise ValueError("message body length, SHA-256, or final chain verification failed")
    return verified_length, body_sha256.hexdigest(), chunk_count, previous_chain


def _message_body_manifest(body, byte_length, sha256, chunk_count, final_chain_sha256):
    return MessageBodyManifest(
        body_id=body[0],
        session_id=body[1],
        byte_length=byte_length,
        sha256=sha256,
        chunk_size=LEGACY_BODY_CHUNK_SIZE,
        chunk_count=chunk_count,
        final_chain_sha256=final_chain_sha256,
        status="writer_verified",
        encoding="utf-8",
    )


def finalize_message_body_write_in_transaction(
    handle, *, session_id: str, body_id: str
) -> MessageBodyManifest:
    """Verify all chunks and strict UTF-8, then freeze the writer manifest."""
    _legacy_body_validate_id(body_id, "body_id")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    body = _message_body_load(handle, session_id, body_id)
    _message_body_check_owner(body, principal_hash, incarnation_id)
    byte_length, sha256, chunk_count, final_chain = _message_body_verify_chunks(
        handle, body
    )
    if body[4] == "building":
        changed = _legacy_body_execute_write(
            handle,
            "UPDATE message_body_manifests SET status = 'writer_verified', "
            "byte_length = ?, sha256 = ?, final_chain_sha256 = ? "
            "WHERE body_id = ? AND session_id = ? AND status = 'building' "
            "AND next_chunk_offset = ? AND bytes_appended = ? AND chunk_count = ? "
            "AND chain_sha256 = ?",
            (
                byte_length,
                sha256,
                final_chain,
                body_id,
                session_id,
                body[5],
                body[6],
                chunk_count,
                final_chain,
            ),
        )
        if changed != 1:
            raise ValueError("message body manifest could not be finalized")
    elif body[4] != "writer_verified":
        raise ValueError("message body status is invalid")
    return _message_body_manifest(body, byte_length, sha256, chunk_count, final_chain)


def read_message_body_chunk_in_transaction(
    handle,
    *,
    session_id: str,
    body_id: str,
    byte_offset: int,
    max_rows: int,
    max_bytes: int,
) -> MessageBodyChunk | None:
    """Read one committed body chunk; the row budget includes owner and manifest queries."""
    _legacy_body_validate_id(body_id, "body_id")
    if (
        type(byte_offset) is not int
        or byte_offset < 0
        or byte_offset > _LEGACY_BODY_MAX_SQLITE_INTEGER
    ):
        raise ValueError("byte_offset must be a non-negative SQLite integer")
    if type(max_rows) is not int or max_rows != 3:
        raise ValueError("max_rows must be exactly 3 for owner, joined metadata, and BLOB rows")
    if type(max_bytes) is not int or not 1 <= max_bytes <= LEGACY_BODY_CHUNK_SIZE:
        raise ValueError("max_bytes must be between 1 and 8192")
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    rows = _legacy_body_query_rows(
        handle,
        "SELECT m.body_id, m.session_id, m.incarnation_id, m.owner_principal_hash, "
        "m.status, m.next_chunk_offset, m.bytes_appended, m.chunk_count, "
        "m.terminal_chunk_offset, m.chain_sha256, m.byte_length, m.sha256, "
        "m.final_chain_sha256, c.byte_offset, c.chunk_length, c.chunk_sha256, "
        "c.previous_chain_sha256, c.chain_sha256, p.chain_sha256, p.chunk_length "
        "FROM message_body_manifests AS m "
        "LEFT JOIN message_body_chunks AS c "
        "ON c.body_id = m.body_id AND c.byte_offset = ? "
        "LEFT JOIN message_body_chunks AS p "
        "ON p.body_id = c.body_id AND p.byte_offset = c.byte_offset - 8192 "
        "WHERE m.body_id = ? AND m.session_id = ? LIMIT 1",
        (byte_offset, body_id, session_id),
    )
    if len(rows) != 1:
        raise LookupError("message body is unavailable")
    row = rows[0]
    if row[2] != incarnation_id or row[3] != principal_hash:
        raise PermissionError("message body manifest owner binding has changed")
    if (
        row[4] != "writer_verified"
        or type(row[10]) is not int
        or type(row[11]) is not str
        or type(row[12]) is not str
        or row[6] != row[10]
        or row[5] != row[7] * LEGACY_BODY_CHUNK_SIZE
        or row[12] != row[9]
    ):
        raise ValueError("message body manifest is not writer-verified")
    if byte_offset == row[10]:
        return None
    if byte_offset > row[10]:
        raise ValueError("byte_offset is beyond the message body end")
    if byte_offset % LEGACY_BODY_CHUNK_SIZE != 0:
        raise ValueError("byte_offset must align to an 8192-byte chunk boundary")
    expected_length = min(LEGACY_BODY_CHUNK_SIZE, row[10] - byte_offset)
    if expected_length > max_bytes:
        raise ValueError("message body chunk exceeds max_bytes")
    previous_chain = (
        _LEGACY_BODY_GENESIS_CHAIN if byte_offset == 0 else row[18]
    )
    if (
        row[13] != byte_offset
        or row[14] != expected_length
        or row[16] != previous_chain
        or (byte_offset == 0 and row[18] is not None)
        or (byte_offset > 0 and row[19] != LEGACY_BODY_CHUNK_SIZE)
    ):
        raise ValueError("message body chunk offset or chain cursor is inconsistent")
    body_rows = _legacy_body_query_rows(
        handle,
        "SELECT body FROM message_body_chunks "
        "WHERE body_id = ? AND byte_offset = ? LIMIT 1",
        (body_id, byte_offset),
    )
    if len(body_rows) != 1 or type(body_rows[0][0]) is not bytes:
        raise ValueError("message body chunk BLOB is unavailable")
    receipt = _message_body_validate_chunk_record(
        (
            row[14],
            row[15],
            row[16],
            row[17],
            body_rows[0][0],
        ),
        body_id=body_id,
        byte_offset=byte_offset,
        previous_chain_sha256=previous_chain,
    )
    return MessageBodyChunk(
        body_id=body_id,
        byte_offset=byte_offset,
        next_offset=byte_offset + receipt.byte_length,
        chunk_length=receipt.byte_length,
        chunk_bytes=body_rows[0][0],
        chunk_sha256=receipt.chunk_sha256,
        previous_chain_sha256=receipt.previous_chain_sha256,
        chain_sha256=receipt.chain_sha256,
        manifest_byte_length=row[10],
        manifest_sha256=row[11],
        manifest_chunk_count=row[7],
        manifest_chain_sha256=row[12],
    )


def _legacy_message_blob_validate_id(value, field_name):
    _legacy_body_validate_id(value, field_name)


def _legacy_message_blob_owner_binding(handle, session_id):
    """Require current owner, incarnation, and delete epoch inside this handle."""
    principal_hash, incarnation_id = _legacy_body_owner_identity(handle, session_id)
    try:
        from app.api.rolling_history_owner_scope import get_rolling_history_owner_identity
        from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX

        identity = get_rolling_history_owner_identity()
    except Exception:
        raise PermissionError("legacy message snapshot owner scope is unavailable") from None
    delete_epoch = getattr(identity, "delete_epoch", None)
    if (
        getattr(identity, "session_id", None) != session_id
        or getattr(identity, "principal_hash", None) != principal_hash
        or getattr(identity, "incarnation_id", None) != incarnation_id
        or type(delete_epoch) is not str
        or (delete_epoch != "" and _LEGACY_BODY_ID_PATTERN.fullmatch(delete_epoch) is None)
    ):
        raise PermissionError("legacy message snapshot owner binding is stale")

    epoch_key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(
        session_id.encode("utf-8")
    ).hexdigest()
    epoch_rows = _legacy_body_query_rows(
        handle,
        "SELECT value FROM settings WHERE key = ? LIMIT 2",
        (epoch_key,),
    )
    database_epoch = "" if not epoch_rows else epoch_rows[0][0]
    if (
        len(epoch_rows) > 1
        or type(database_epoch) is not str
        or database_epoch != delete_epoch
    ):
        raise PermissionError("legacy message snapshot delete epoch has changed")
    return principal_hash, incarnation_id, delete_epoch


def _legacy_message_blob_source_fence(handle, session_id):
    _legacy_body_validate_session_id(session_id)
    rows = _legacy_body_query_rows(
        handle,
        "SELECT generation, message_count, non_append_generation, tail_order_dirty, "
        "tail_order_created_at IS (SELECT messages.created_at FROM messages "
        "WHERE messages.session_id = ? ORDER BY (messages.created_at IS NOT NULL) DESC, "
        "messages.created_at DESC, messages.id COLLATE BINARY DESC LIMIT 1), "
        "tail_order_message_id IS (SELECT messages.id FROM messages "
        "WHERE messages.session_id = ? ORDER BY (messages.created_at IS NOT NULL) DESC, "
        "messages.created_at DESC, messages.id COLLATE BINARY DESC LIMIT 1) "
        "FROM message_source_generations WHERE session_id = ? LIMIT 2",
        (session_id, session_id, session_id),
    )
    if len(rows) != 1:
        raise LookupError("legacy message source fence is unavailable")
    generation_row = rows[0]
    if (
        any(type(value) is not int or value < 0 for value in generation_row[:3])
        or type(generation_row[3]) is not int
        or generation_row[3] != 0
        or generation_row[4] != 1
        or generation_row[5] != 1
    ):
        raise ValueError("legacy message source fence is invalid or dirty")
    count_rows = _legacy_body_query_rows(
        handle,
        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
        (session_id,),
    )
    if (
        len(count_rows) != 1
        or type(count_rows[0][0]) is not int
        or count_rows[0][0] != generation_row[1]
    ):
        raise ValueError("legacy message source count does not match its fence")
    tail_rows = _legacy_body_query_rows(
        handle,
        "SELECT rowid FROM messages WHERE session_id = ? "
        "ORDER BY (created_at IS NOT NULL) DESC, created_at DESC, "
        "id COLLATE BINARY DESC LIMIT 1",
        (session_id,),
    )
    if len(tail_rows) > 1:
        raise ValueError("legacy message source high-water mark is ambiguous")
    if tail_rows:
        source_rowid = tail_rows[0][0]
        if (
            type(source_rowid) is not int
            or not -(1 << 63) <= source_rowid <= _LEGACY_BODY_MAX_SQLITE_INTEGER
        ):
            raise ValueError("legacy message high-water rowid is invalid")
        high_water_char_lengths = _legacy_message_blob_source_text_size_preflight(
            handle,
            session_id=session_id,
            rowid=source_rowid,
            field_names=("id", "created_at"),
        )
        high_water_rows = _legacy_body_query_rows(
            handle,
            "SELECT id, created_at FROM messages WHERE session_id = ? AND rowid = ? LIMIT 2",
            (session_id, source_rowid),
        )
        if len(high_water_rows) != 1:
            raise LookupError("legacy message high-water row is unavailable")
        high_water_message_id, high_water_created_at = high_water_rows[0]
        _legacy_message_blob_verify_source_text_value(
            high_water_message_id,
            high_water_char_lengths["id"],
            field_name="id",
        )
        if not high_water_message_id:
            raise ValueError("legacy message high-water ID is empty")
        _legacy_message_blob_verify_source_text_value(
            high_water_created_at,
            high_water_char_lengths["created_at"],
            field_name="created_at",
            nullable=True,
        )
    else:
        high_water_created_at = None
        high_water_message_id = None
        if generation_row[1] != 0:
            raise ValueError("empty legacy message source has a non-empty high-water mark")
    return (
        generation_row[0],
        generation_row[1],
        generation_row[2],
        high_water_created_at,
        high_water_message_id,
    )


def _legacy_message_blob_load_snapshot(handle, session_id, migration_id):
    preflight_rows = _legacy_body_query_rows(
        handle,
        "SELECT typeof(session_id), length(session_id), instr(session_id, char(0)), "
        "typeof(snapshot_id), length(snapshot_id), instr(snapshot_id, char(0)), "
        "typeof(owner_principal_hash), length(owner_principal_hash), instr(owner_principal_hash, char(0)), "
        "typeof(incarnation_id), length(incarnation_id), instr(incarnation_id, char(0)), "
        "typeof(delete_epoch), length(delete_epoch), instr(delete_epoch, char(0)), "
        "typeof(ordering_version), length(ordering_version), instr(ordering_version, char(0)), "
        "typeof(high_water_created_at), length(high_water_created_at), instr(high_water_created_at, char(0)), "
        "typeof(high_water_message_id), length(high_water_message_id), instr(high_water_message_id, char(0)), "
        "typeof(capture_message_id), length(capture_message_id), instr(capture_message_id, char(0)), "
        "typeof(capture_body_id), length(capture_body_id), instr(capture_body_id, char(0)), "
        "typeof(capture_body_chain_sha256), length(capture_body_chain_sha256), instr(capture_body_chain_sha256, char(0)), "
        "typeof(capture_chain_sha256), length(capture_chain_sha256), instr(capture_chain_sha256, char(0)), "
        "typeof(status), length(status), instr(status, char(0)) "
        "FROM legacy_message_blob_snapshots "
        "WHERE migration_id = ? AND session_id = ? LIMIT 2",
        (migration_id, session_id),
    )
    if len(preflight_rows) != 1:
        raise LookupError("legacy message blob snapshot is unavailable")
    preflight = preflight_rows[0]
    for index, (field_name, max_chars, nullable, min_chars) in enumerate(
        (
            ("session_id", 128, False, 1),
            ("snapshot_id", 32, False, 32),
            ("owner_principal_hash", 64, False, 64),
            ("incarnation_id", 32, False, 32),
            ("delete_epoch", 32, False, 0),
            ("ordering_version", 64, False, 1),
            ("high_water_created_at", 8192, True, 0),
            ("high_water_message_id", LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS, True, 1),
            ("capture_message_id", LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS, True, 1),
            ("capture_body_id", 32, True, 32),
            ("capture_body_chain_sha256", 64, False, 64),
            ("capture_chain_sha256", 64, False, 64),
            ("status", 16, False, 1),
        )
    ):
        _legacy_message_blob_validate_text_size_preflight(
            preflight[index * 3],
            preflight[index * 3 + 1],
            preflight[index * 3 + 2],
            field_name=field_name,
            max_chars=max_chars,
            nullable=nullable,
            min_chars=min_chars,
        )
    rows = _legacy_body_query_rows(
        handle,
        "SELECT migration_id, snapshot_id, session_id, owner_principal_hash, "
        "incarnation_id, delete_epoch, source_generation, source_message_count, "
        "source_non_append_generation, ordering_version, high_water_created_at, "
        "high_water_message_id, capture_cursor, capture_message_id, capture_body_id, "
        "capture_body_offset, capture_body_chain_sha256, capture_chain_sha256, status "
        "FROM legacy_message_blob_snapshots "
        "WHERE migration_id = ? AND session_id = ? LIMIT 2",
        (migration_id, session_id),
    )
    if len(rows) != 1:
        raise LookupError("legacy message blob snapshot is unavailable")
    row = rows[0]
    return row


def _legacy_message_blob_check_snapshot_owner(row, principal_hash, incarnation_id, delete_epoch):
    if row[3] != principal_hash or row[4] != incarnation_id or row[5] != delete_epoch:
        raise PermissionError("legacy message blob snapshot owner binding has changed")


def _legacy_message_blob_snapshot_state(row):
    return LegacyMessageBlobSnapshotState(
        migration_id=row[0],
        snapshot_id=row[1],
        session_id=row[2],
        source_generation=row[6],
        source_message_count=row[7],
        source_non_append_generation=row[8],
        high_water_created_at=row[10],
        high_water_message_id=row[11],
        capture_cursor=row[12],
        capture_message_id=row[13],
        capture_body_offset=row[15],
        capture_body_chain_sha256=row[16],
        capture_chain_sha256=row[17],
        status=row[18],
    )


def _legacy_message_blob_source_matches_snapshot(handle, snapshot):
    fence = _legacy_message_blob_source_fence(handle, snapshot[2])
    expected = (snapshot[6], snapshot[7], snapshot[8], snapshot[10], snapshot[11])
    if fence != expected:
        raise ValueError("legacy Message source fence changed during snapshot capture")


def _legacy_message_blob_body_id(migration_id, sequence):
    digest = hashlib.sha256(
        b"legacy-message-blob-snapshot-body-id-v1\x00"
        + bytes.fromhex(migration_id)
        + struct.pack(">Q", sequence)
    ).hexdigest()
    return digest[:32]


def _legacy_message_blob_metadata_utf8_size(metadata_json):
    if (
        type(metadata_json) is not str
        or len(metadata_json) > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_CHARS
    ):
        raise ValueError("legacy Message snapshot metadata exceeds its character bound")
    try:
        metadata_bytes = metadata_json.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("legacy Message snapshot metadata is not valid UTF-8 text") from None
    if len(metadata_bytes) > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES:
        raise ValueError("legacy Message snapshot metadata exceeds its UTF-8 byte bound")
    return len(metadata_bytes)


def _legacy_message_blob_metadata_source_raw_size(metadata_json):
    _legacy_message_blob_metadata_utf8_size(metadata_json)
    try:
        payload = json.loads(metadata_json)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("legacy Message snapshot metadata JSON is invalid") from None
    expected_keys = {
        "activity_json",
        "created_at",
        "legacy_message_id",
        "role",
        "session_id",
        "sources_json",
        "tool_calls_json",
    }
    if type(payload) is not dict or set(payload) != expected_keys:
        raise ValueError("legacy Message snapshot metadata JSON fields are invalid")
    required_keys = ("legacy_message_id", "role", "session_id")
    nullable_keys = ("activity_json", "created_at", "sources_json", "tool_calls_json")
    raw_size = 0
    for key in required_keys:
        value = payload[key]
        if type(value) is not str:
            raise ValueError("legacy Message snapshot metadata JSON has an invalid value")
        try:
            raw_size += len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("legacy Message snapshot metadata JSON is not valid UTF-8") from None
    for key in nullable_keys:
        value = payload[key]
        if value is None:
            continue
        if type(value) is not str:
            raise ValueError("legacy Message snapshot metadata JSON has an invalid value")
        try:
            raw_size += len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("legacy Message snapshot metadata JSON is not valid UTF-8") from None
    if raw_size > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES:
        raise ValueError("legacy Message snapshot metadata exceeds its raw-byte bound")
    return raw_size


def _legacy_message_blob_read_text_chunk(
    handle, *, session_id, message_id, rowid, column_name, byte_offset, max_bytes
):
    """Read bounded raw UTF-8 chunks from one of three JSON metadata columns.

    The required future handle method is
    ``read_message_text_chunk(session_id, message_id, rowid, column_name, byte_offset, max_bytes)``
    and returns ``tuple[int | None, bytes]`` as
    ``(total_utf8_bytes_or_none, chunk_bytes)``. ``session_id`` and the resolved
    non-empty ``message_id`` bind the exact ``rowid`` under the current owner
    transaction. A zero-byte request at offset zero returns only the size; a
    NULL column returns ``(None, b"")``. Non-zero reads use raw UTF-8 byte
    offsets and return at most the requested bytes. The column allowlist is exactly
    ``sources``, ``tool_calls``, and ``activity``; ``message_id`` is always the
    already-resolved ID. Each column and each chunk is at most 8192 raw bytes.

    The future owner scope must verify the row/session and column type using its
    current transaction connection and its read-only ``blobopen`` reader. The
    scope must reject any other column and any request exceeding these limits.
    """
    allowed_columns = {"sources", "tool_calls", "activity"}
    if (
        column_name not in allowed_columns
        or type(message_id) is not str
        or not message_id
        or len(message_id) > LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS
        or type(rowid) is not int
        or not -(1 << 63) <= rowid <= _LEGACY_BODY_MAX_SQLITE_INTEGER
        or type(byte_offset) is not int
        or byte_offset < 0
        or type(max_bytes) is not int
        or not 0 <= max_bytes <= LEGACY_BODY_CHUNK_SIZE
        or (max_bytes == 0 and byte_offset != 0)
    ):
        raise ValueError("legacy Message metadata chunk request is outside its bounds")
    _legacy_body_validate_session_id(session_id)
    reader = getattr(handle, "read_message_text_chunk", None)
    if not callable(reader):
        raise RuntimeError(
            "legacy Message capture requires the owner-scoped bounded TEXT reader"
        )
    result = reader(
        session_id,
        message_id,
        rowid,
        column_name,
        byte_offset,
        max_bytes,
    )
    if (
        type(result) is not tuple
        or len(result) != 2
        or (result[0] is not None and (type(result[0]) is not int or result[0] < 0))
        or type(result[1]) is not bytes
        or len(result[1]) > max_bytes
    ):
        raise ValueError("owner-scoped SQLite TEXT reader returned an invalid chunk")
    total_byte_length, chunk_bytes = result
    if total_byte_length is None:
        if byte_offset != 0 or max_bytes != 0 or chunk_bytes:
            raise ValueError("owner-scoped SQLite TEXT reader returned invalid NULL data")
    elif (
        total_byte_length > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
        or byte_offset > total_byte_length
        or byte_offset + len(chunk_bytes) > total_byte_length
        or (max_bytes == 0 and chunk_bytes)
    ):
        raise ValueError("owner-scoped SQLite TEXT reader returned an invalid byte range")
    return result


def _legacy_message_blob_read_text_value(
    handle, *, session_id, message_id, rowid, column_name, byte_length
):
    if byte_length is None:
        if column_name not in {"sources", "tool_calls", "activity"}:
            raise ValueError("legacy Message required TEXT field is NULL")
        return None
    if (
        type(byte_length) is not int
        or not 0 <= byte_length <= LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
    ):
        raise ValueError("legacy Message TEXT field length is invalid")
    raw_bytes = bytearray()
    byte_offset = 0
    while byte_offset < byte_length:
        chunk_length = min(LEGACY_BODY_CHUNK_SIZE, byte_length - byte_offset)
        total_byte_length, chunk_bytes = _legacy_message_blob_read_text_chunk(
            handle,
            session_id=session_id,
            message_id=message_id,
            rowid=rowid,
            column_name=column_name,
            byte_offset=byte_offset,
            max_bytes=chunk_length,
        )
        if (
            total_byte_length != byte_length
            or len(chunk_bytes) != chunk_length
            or not chunk_bytes
        ):
            raise ValueError("legacy Message TEXT field changed or returned a short chunk")
        raw_bytes.extend(chunk_bytes)
        byte_offset += len(chunk_bytes)
    if len(raw_bytes) != byte_length:
        raise ValueError("legacy Message TEXT field byte length changed while reading")
    try:
        return bytes(raw_bytes).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("legacy Message TEXT field is not valid UTF-8") from None


def _legacy_message_blob_encode_metadata(
    *, message_id, session_id, role, created_at, sources, tool_calls, activity
):
    if (
        type(message_id) is not str
        or not message_id
        or len(message_id) > LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS
        or type(session_id) is not str
        or len(session_id) > 128
        or type(role) is not str
        or len(role) > LEGACY_MESSAGE_BLOB_SNAPSHOT_SOURCE_ROLE_MAX_CHARS
        or (created_at is not None and type(created_at) is not str)
        or (created_at is not None and len(created_at) > LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_CREATED_AT_MAX_CHARS)
        or any(value is not None and type(value) is not str for value in (sources, tool_calls, activity))
    ):
        raise ValueError("legacy Message metadata has an unsupported SQLite value")
    payload = {
        "activity_json": activity,
        "created_at": created_at,
        "legacy_message_id": message_id,
        "role": role,
        "session_id": session_id,
        "sources_json": sources,
        "tool_calls_json": tool_calls,
    }
    metadata_json = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _legacy_message_blob_metadata_utf8_size(metadata_json)
    return metadata_json


def _legacy_message_blob_source_metadata_at_sequence(handle, session_id, sequence):
    rowid_rows = _legacy_body_query_rows(
        handle,
        "SELECT rowid FROM messages WHERE session_id = ? "
        "ORDER BY (created_at IS NOT NULL) ASC, created_at ASC, "
        "id COLLATE BINARY ASC LIMIT 1 OFFSET ?",
        (session_id, sequence),
    )
    if len(rowid_rows) != 1:
        raise LookupError("legacy Message source row is missing at the fenced cursor")
    source_rowid = rowid_rows[0][0]
    if (
        type(source_rowid) is not int
        or not -(1 << 63) <= source_rowid <= _LEGACY_BODY_MAX_SQLITE_INTEGER
    ):
        raise ValueError("legacy Message source rowid is invalid")
    _legacy_body_validate_session_id(session_id)
    source_char_lengths = _legacy_message_blob_source_text_size_preflight(
        handle,
        session_id=session_id,
        rowid=source_rowid,
        field_names=("id", "role", "created_at"),
    )
    source_rows = _legacy_body_query_rows(
        handle,
        "SELECT id, role, created_at FROM messages "
        "WHERE session_id = ? AND rowid = ? LIMIT 2",
        (session_id, source_rowid),
    )
    if len(source_rows) != 1:
        raise LookupError("legacy Message source row disappeared after text preflight")
    message_id, role, created_at = source_rows[0]
    message_id_raw_bytes = _legacy_message_blob_verify_source_text_value(
        message_id,
        source_char_lengths["id"],
        field_name="id",
    )
    if not message_id:
        raise ValueError("legacy Message ID is empty")
    role_raw_bytes = _legacy_message_blob_verify_source_text_value(
        role,
        source_char_lengths["role"],
        field_name="role",
    )
    created_at_raw_bytes = _legacy_message_blob_verify_source_text_value(
        created_at,
        source_char_lengths["created_at"],
        field_name="created_at",
        nullable=True,
    )
    try:
        session_raw_bytes = len(session_id.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        raise ValueError("legacy Message session ID is not valid UTF-8 text") from None

    json_columns = ("sources", "tool_calls", "activity")
    json_byte_lengths = {}
    for column_name in json_columns:
        byte_length, _ = _legacy_message_blob_read_text_chunk(
            handle,
            session_id=session_id,
            message_id=message_id,
            rowid=source_rowid,
            column_name=column_name,
            byte_offset=0,
            max_bytes=0,
        )
        if byte_length is not None and (
            type(byte_length) is not int
            or not 0 <= byte_length <= LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
        ):
            raise ValueError("legacy Message JSON metadata field exceeds its raw-byte bound")
        json_byte_lengths[column_name] = byte_length
    source_metadata_raw_bytes = (
        message_id_raw_bytes
        + role_raw_bytes
        + created_at_raw_bytes
        + session_raw_bytes
        + sum(byte_length or 0 for byte_length in json_byte_lengths.values())
    )
    if source_metadata_raw_bytes > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES:
        raise ValueError("legacy Message source metadata exceeds its raw-byte bound")

    json_values = {}
    for column_name in json_columns:
        value = _legacy_message_blob_read_text_value(
            handle,
            session_id=session_id,
            message_id=message_id,
            rowid=source_rowid,
            column_name=column_name,
            byte_length=json_byte_lengths[column_name],
        )
        if value is not None and len(value.encode("utf-8", errors="strict")) != json_byte_lengths[column_name]:
            raise ValueError("legacy Message JSON metadata changed after size preflight")
        json_values[column_name] = value
    metadata_json = _legacy_message_blob_encode_metadata(
        message_id=message_id,
        session_id=session_id,
        role=role,
        created_at=created_at,
        sources=json_values["sources"],
        tool_calls=json_values["tool_calls"],
        activity=json_values["activity"],
    )
    if _legacy_message_blob_metadata_source_raw_size(metadata_json) != source_metadata_raw_bytes:
        raise ValueError("legacy Message source metadata changed after bounded reads")
    return source_rowid, message_id, role, created_at, metadata_json


def _legacy_message_blob_read_source_chunk(
    handle, *, session_id, message_id, rowid, byte_offset, max_bytes
):
    """Use the scope's bounded reader: (UTF-8 byte length, raw UTF-8 bytes).

    The owner scope must check the exact row/session/type, use its current
    SQLite connection's read-only blobopen handle, and close that handle before
    returning. ``max_bytes=0`` is a length-only read. Every non-empty read is
    capped at 8192 bytes; the body writer validates UTF-8 across chunk edges.
    """
    if (
        type(rowid) is not int
        or type(byte_offset) is not int
        or byte_offset < 0
        or type(max_bytes) is not int
        or not 0 <= max_bytes <= LEGACY_BODY_CHUNK_SIZE
    ):
        raise ValueError("legacy Message source chunk request is outside its bounds")
    reader = getattr(handle, "read_message_content_chunk", None)
    if not callable(reader):
        raise RuntimeError(
            "legacy Message capture requires the owner-scoped SQLite blobopen reader"
        )
    result = reader(session_id, message_id, rowid, byte_offset, max_bytes)
    if (
        type(result) is not tuple
        or len(result) != 2
        or type(result[0]) is not int
        or result[0] < 0
        or type(result[1]) is not bytes
        or len(result[1]) > max_bytes
        or byte_offset < 0
        or byte_offset > result[0]
        or byte_offset + len(result[1]) > result[0]
    ):
        raise ValueError("owner-scoped SQLite TEXT reader returned an invalid bounded chunk")
    return result


def _legacy_message_blob_load_row(handle, migration_id, sequence):
    preflight_rows = _legacy_body_query_rows(
        handle,
        "SELECT typeof(legacy_message_id), length(legacy_message_id), instr(legacy_message_id, char(0)), "
        "typeof(role), length(role), instr(role, char(0)), "
        "typeof(created_at), length(created_at), instr(created_at, char(0)), "
        "typeof(metadata_json), length(metadata_json), instr(metadata_json, char(0)), "
        "typeof(body_id), length(body_id), instr(body_id, char(0)), "
        "typeof(body_sha256), length(body_sha256), instr(body_sha256, char(0)), "
        "typeof(row_hash), length(row_hash), instr(row_hash, char(0)), "
        "typeof(status), length(status), instr(status, char(0)) "
        "FROM legacy_message_blob_snapshot_rows "
        "WHERE migration_id = ? AND sequence = ? LIMIT 2",
        (migration_id, sequence),
    )
    if len(preflight_rows) != 1:
        raise LookupError("legacy Message snapshot row is unavailable")
    preflight = preflight_rows[0]
    field_limits = (
        (0, "legacy_message_id", 512, False, 1),
        (3, "role", LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES, False, 0),
        (6, "created_at", LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES, True, 0),
        (9, "metadata_json", LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_CHARS, False, 1),
        (12, "body_id", 32, False, 32),
        (15, "body_sha256", 64, True, 64),
        (18, "row_hash", 64, True, 64),
        (21, "status", 16, False, 1),
    )
    field_char_lengths = {}
    for index, field_name, max_chars, nullable, min_chars in field_limits:
        field_char_lengths[field_name] = _legacy_message_blob_validate_text_size_preflight(
            preflight[index],
            preflight[index + 1],
            preflight[index + 2],
            field_name=field_name,
            max_chars=max_chars,
            nullable=nullable,
            min_chars=min_chars,
        ) or 0
    metadata_upper_bound = (
        4
        * sum(
            field_char_lengths[name]
            for name in ("legacy_message_id", "role", "created_at", "metadata_json")
        )
        + LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_FIXED_BYTES
    )
    if metadata_upper_bound > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES:
        raise ValueError("legacy Message snapshot row exceeds its metadata preflight bound")
    rows = _legacy_body_query_rows(
        handle,
        "SELECT sequence, legacy_message_id, source_rowid, body_id, role, created_at, "
        "metadata_json, body_byte_length, body_sha256, row_hash, status "
        "FROM legacy_message_blob_snapshot_rows "
        "WHERE migration_id = ? AND sequence = ? LIMIT 2",
        (migration_id, sequence),
    )
    if len(rows) != 1:
        raise LookupError("legacy Message snapshot row is unavailable")
    row = rows[0]
    if (
        type(row[1]) is not str
        or len(row[1]) > 512
        or type(row[4]) is not str
        or (row[5] is not None and type(row[5]) is not str)
        or type(row[6]) is not str
    ):
        raise ValueError("legacy Message snapshot row text changed after preflight")
    _legacy_message_blob_metadata_utf8_size(row[6])
    return row


def _legacy_message_blob_row_hash(
    *, migration_id, snapshot_id, sequence, message_id, source_rowid,
    metadata_json, body_byte_length, body_sha256,
):
    message_bytes = message_id.encode("utf-8", errors="strict")
    metadata_bytes = metadata_json.encode("utf-8", errors="strict")
    payload = (
        b"legacy-message-blob-snapshot-row-v1\x00"
        + bytes.fromhex(migration_id)
        + bytes.fromhex(snapshot_id)
        + struct.pack(">QQQ", sequence, source_rowid, body_byte_length)
        + struct.pack(">I", len(message_bytes))
        + message_bytes
        + bytes.fromhex(body_sha256)
        + struct.pack(">Q", len(metadata_bytes))
        + metadata_bytes
    )
    return hashlib.sha256(payload).hexdigest()


def _legacy_message_blob_chain_hash(previous_chain_sha256, sequence, row_hash):
    return hashlib.sha256(
        b"legacy-message-blob-snapshot-chain-v1\x00"
        + bytes.fromhex(previous_chain_sha256)
        + struct.pack(">Q", sequence)
        + bytes.fromhex(row_hash)
    ).hexdigest()


def _legacy_message_blob_metadata_from_row(snapshot_id, migration_id, row):
    _legacy_message_blob_metadata_utf8_size(row[6])
    metadata = LegacyMessageBlobSnapshotRowMetadata(
        migration_id=migration_id,
        snapshot_id=snapshot_id,
        sequence=row[0],
        message_id=row[1],
        role=row[4],
        created_at=row[5],
        metadata_json=row[6],
        body_id=row[3],
        body_byte_length=row[7],
        body_sha256=row[8],
        row_hash=row[9],
        status=row[10],
    )
    if (
        _legacy_message_blob_snapshot_row_metadata_payload_size(metadata)
        > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
    ):
        raise ValueError("legacy Message snapshot row exceeds its returned metadata bound")
    return metadata


def _legacy_message_blob_snapshot_row_metadata_payload_size(metadata):
    total_bytes = 0
    for value in (
        metadata.migration_id,
        metadata.snapshot_id,
        metadata.message_id,
        metadata.role,
        metadata.created_at,
        metadata.metadata_json,
        metadata.body_id,
        metadata.body_sha256,
        metadata.row_hash,
        metadata.status,
    ):
        if value is None:
            continue
        if type(value) is not str:
            raise ValueError("legacy Message snapshot row contains invalid metadata text")
        try:
            total_bytes += len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("legacy Message snapshot row is not valid UTF-8") from None
    return total_bytes


def _legacy_message_blob_snapshot_row_metadata_upper_bound(
    migration_id, snapshot_id, row
):
    return (
        sum(
            len(value.encode("utf-8", errors="strict"))
            for value in (
                migration_id,
                snapshot_id,
                row[1],
                row[4],
                row[5],
                row[6],
                row[3],
                row[10],
            )
            if value is not None
        )
        + 2 * 64
    )


def _legacy_message_blob_page_receipt(handle, migration_id, snapshot_id, request, page_row):
    if (
        type(page_row[0]) is not int
        or not 1 <= page_row[0] <= LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_ROWS
        or type(page_row[1]) is not int
        or not 8192 <= page_row[1] <= LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_BODY_BYTES
        or type(page_row[2]) is not int
        or type(page_row[7]) is not int
        or type(page_row[8]) is not int
    ):
        raise ValueError("legacy Message snapshot page receipt limits are invalid")
    completed_row_count = page_row[2] - request[0]
    active_message_id = page_row[3]
    expected_row_count = completed_row_count + (1 if active_message_id else 0)
    combined_bytes_limit = (
        page_row[1]
        + page_row[0] * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
    )
    if (
        completed_row_count < 0
        or completed_row_count > page_row[0]
        or expected_row_count != page_row[7]
        or expected_row_count > page_row[0]
        or page_row[8] > page_row[1]
    ):
        raise ValueError("legacy Message snapshot page receipt progress is inconsistent")
    metadata_bytes_upper_bound = 0
    if expected_row_count > 0:
        preflight_rows = _legacy_body_query_rows(
            handle,
            "SELECT sequence, "
            "typeof(legacy_message_id), length(legacy_message_id), instr(legacy_message_id, char(0)), "
            "typeof(role), length(role), instr(role, char(0)), "
            "typeof(created_at), length(created_at), instr(created_at, char(0)), "
            "typeof(metadata_json), length(metadata_json), instr(metadata_json, char(0)), "
            "typeof(body_id), length(body_id), instr(body_id, char(0)), "
            "typeof(body_sha256), length(body_sha256), instr(body_sha256, char(0)), "
            "typeof(row_hash), length(row_hash), instr(row_hash, char(0)), "
            "typeof(status), length(status), instr(status, char(0)) "
            "FROM legacy_message_blob_snapshot_rows WHERE migration_id = ? "
            "AND sequence >= ? AND sequence < ? ORDER BY sequence ASC LIMIT ?",
            (
                migration_id,
                request[0],
                request[0] + expected_row_count,
                expected_row_count + 1,
            ),
        )
        if len(preflight_rows) != expected_row_count:
            raise ValueError("legacy Message snapshot page metadata rows are incomplete")
        for index, preflight in enumerate(preflight_rows):
            expected_sequence = request[0] + index
            expected_statuses = (
                ("sealed",)
                if index < completed_row_count
                else ("capturing", "sealed")
            )
            if preflight[0] != expected_sequence or preflight[22] not in expected_statuses:
                raise ValueError("legacy Message snapshot page metadata exceeds its preflight bound")
            row_char_lengths = {}
            for field_index, field_name, max_chars, nullable, min_chars in (
                (1, "legacy_message_id", 512, False, 1),
                (4, "role", LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES, False, 0),
                (7, "created_at", LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES, True, 0),
                (10, "metadata_json", LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_CHARS, False, 1),
                (13, "body_id", 32, False, 32),
                (16, "body_sha256", 64, True, 64),
                (19, "row_hash", 64, True, 64),
                (22, "status", 16, False, 1),
            ):
                row_char_lengths[field_name] = _legacy_message_blob_validate_text_size_preflight(
                    preflight[field_index],
                    preflight[field_index + 1],
                    preflight[field_index + 2],
                    field_name=field_name,
                    max_chars=max_chars,
                    nullable=nullable,
                    min_chars=min_chars,
                ) or 0
            row_upper_bound = (
                4
                * sum(
                    row_char_lengths[field_name]
                    for field_name in (
                        "legacy_message_id",
                        "role",
                        "created_at",
                        "metadata_json",
                    )
                )
                + LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_FIXED_BYTES
            )
            if row_upper_bound > LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES:
                raise ValueError("legacy Message snapshot row exceeds its metadata preflight bound")
            metadata_bytes_upper_bound += row_upper_bound
        if (
            metadata_bytes_upper_bound
            > page_row[0] * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
            or page_row[8] + metadata_bytes_upper_bound > combined_bytes_limit
        ):
            raise ValueError("legacy Message snapshot page exceeds its combined preflight bound")
    rows = []
    if completed_row_count > 0:
        source_rows = _legacy_body_query_rows(
            handle,
            "SELECT sequence, legacy_message_id, source_rowid, body_id, role, created_at, "
            "metadata_json, body_byte_length, body_sha256, row_hash, status "
            "FROM legacy_message_blob_snapshot_rows WHERE migration_id = ? "
            "AND sequence >= ? AND sequence < ? ORDER BY sequence ASC LIMIT ?",
            (migration_id, request[0], page_row[2], completed_row_count + 1),
        )
        if (
            len(source_rows) != completed_row_count
            or any(row[10] != "sealed" for row in source_rows)
        ):
            raise ValueError("legacy Message snapshot page receipt rows are incomplete")
        rows.extend(
            _legacy_message_blob_metadata_from_row(snapshot_id, migration_id, row)
            for row in source_rows
        )
    if active_message_id:
        active_rows = _legacy_body_query_rows(
            handle,
            "SELECT sequence, legacy_message_id, source_rowid, body_id, role, created_at, "
            "metadata_json, body_byte_length, body_sha256, row_hash, status "
            "FROM legacy_message_blob_snapshot_rows WHERE migration_id = ? "
            "AND sequence = ? LIMIT 2",
            (migration_id, page_row[2]),
        )
        if (
            len(active_rows) != 1
            or active_rows[0][1] != active_message_id
            or active_rows[0][10] not in ("capturing", "sealed")
        ):
            raise ValueError("legacy Message snapshot page active row is unavailable")
        active = _legacy_message_blob_metadata_from_row(
            snapshot_id, migration_id, active_rows[0]
        )
        rows.append(
            LegacyMessageBlobSnapshotRowMetadata(
                migration_id=active.migration_id,
                snapshot_id=active.snapshot_id,
                sequence=active.sequence,
                message_id=active.message_id,
                role=active.role,
                created_at=active.created_at,
                metadata_json=active.metadata_json,
                body_id=active.body_id,
                body_byte_length=active.body_byte_length,
                body_sha256=None,
                row_hash=None,
                status="capturing",
            )
        )
    metadata_bytes_captured = sum(
        _legacy_message_blob_snapshot_row_metadata_payload_size(row)
        for row in rows
    )
    source_metadata_raw_bytes_captured = sum(
        _legacy_message_blob_metadata_source_raw_size(row.metadata_json)
        for row in rows
    )
    combined_bytes_captured = page_row[8] + metadata_bytes_captured
    if (
        metadata_bytes_captured
        > page_row[0] * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
        or source_metadata_raw_bytes_captured
        > page_row[0] * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
        or combined_bytes_captured > combined_bytes_limit
    ):
        raise ValueError("legacy Message snapshot page exceeds its combined byte bound")
    return LegacyMessageBlobSnapshotPageReceipt(
        migration_id=migration_id,
        snapshot_id=snapshot_id,
        request_cursor=request[0],
        request_message_id=None if request[1] == "" else request[1],
        request_body_offset=request[2],
        response_cursor=page_row[2],
        response_message_id=None if page_row[3] == "" else page_row[3],
        response_body_offset=page_row[4],
        response_body_chain_sha256=page_row[5],
        response_capture_chain_sha256=page_row[6],
        row_count=page_row[7],
        body_bytes_captured=page_row[8],
        status=page_row[9],
        rows=tuple(rows),
        metadata_bytes_captured=metadata_bytes_captured,
        combined_bytes_captured=combined_bytes_captured,
        combined_bytes_limit=combined_bytes_limit,
        source_metadata_raw_bytes_captured=source_metadata_raw_bytes_captured,
    )


def _legacy_message_blob_lookup_page(handle, migration_id, request):
    preflight_rows = _legacy_body_query_rows(
        handle,
        "SELECT typeof(request_message_id), length(request_message_id), instr(request_message_id, char(0)), "
        "typeof(response_message_id), length(response_message_id), instr(response_message_id, char(0)), "
        "typeof(request_chain_sha256), length(request_chain_sha256), instr(request_chain_sha256, char(0)), "
        "typeof(request_body_chain_sha256), length(request_body_chain_sha256), instr(request_body_chain_sha256, char(0)), "
        "typeof(response_chain_sha256), length(response_chain_sha256), instr(response_chain_sha256, char(0)), "
        "typeof(response_capture_chain_sha256), length(response_capture_chain_sha256), instr(response_capture_chain_sha256, char(0)), "
        "typeof(status), length(status), instr(status, char(0)) "
        "FROM legacy_message_blob_snapshot_pages WHERE migration_id = ? "
        "AND request_cursor = ? AND request_message_id = ? AND request_body_offset = ? "
        "AND request_chain_sha256 = ? AND request_body_chain_sha256 = ? LIMIT 2",
        (migration_id, *request),
    )
    if not preflight_rows:
        return ()
    if len(preflight_rows) != 1:
        raise ValueError("legacy Message snapshot page receipt is ambiguous")
    preflight = preflight_rows[0]
    for index, field_name, max_chars, min_chars in (
        (0, "request_message_id", 512, 0),
        (3, "response_message_id", 512, 0),
        (6, "request_chain_sha256", 64, 64),
        (9, "request_body_chain_sha256", 64, 64),
        (12, "response_chain_sha256", 64, 64),
        (15, "response_capture_chain_sha256", 64, 64),
        (18, "status", 16, 1),
    ):
        _legacy_message_blob_validate_text_size_preflight(
            preflight[index],
            preflight[index + 1],
            preflight[index + 2],
            field_name=field_name,
            max_chars=max_chars,
            min_chars=min_chars,
        )
    return _legacy_body_query_rows(
        handle,
        "SELECT max_rows, max_body_bytes, response_cursor, response_message_id, "
        "response_body_offset, response_chain_sha256, response_capture_chain_sha256, "
        "row_count, body_bytes_captured, status "
        "FROM legacy_message_blob_snapshot_pages WHERE migration_id = ? "
        "AND request_cursor = ? AND request_message_id = ? AND request_body_offset = ? "
        "AND request_chain_sha256 = ? AND request_body_chain_sha256 = ? LIMIT 2",
        (migration_id, *request),
    )


def begin_legacy_message_blob_snapshot_in_transaction(
    handle, *, session_id, migration_id, snapshot_id
):
    """Create or idempotently read a source-fenced, owner-bound snapshot checkpoint."""
    _legacy_message_blob_validate_id(migration_id, "migration_id")
    _legacy_message_blob_validate_id(snapshot_id, "snapshot_id")
    principal_hash, incarnation_id, delete_epoch = _legacy_message_blob_owner_binding(
        handle, session_id
    )
    existing = _legacy_body_query_rows(
        handle,
        "SELECT migration_id, snapshot_id, session_id, owner_principal_hash, "
        "incarnation_id, delete_epoch FROM legacy_message_blob_snapshots "
        "WHERE migration_id = ? LIMIT 2",
        (migration_id,),
    )
    if existing:
        row = existing[0]
        if (
            len(existing) != 1
            or row[1] != snapshot_id
            or row[2] != session_id
            or row[3] != principal_hash
            or row[4] != incarnation_id
            or row[5] != delete_epoch
        ):
            raise ValueError("migration_id is already bound to another snapshot or owner")
        return _legacy_message_blob_snapshot_state(
            _legacy_message_blob_load_snapshot(handle, session_id, migration_id)
        )

    source_generation, message_count, non_append_generation, high_water_created_at, high_water_message_id = (
        _legacy_message_blob_source_fence(handle, session_id)
    )
    _legacy_body_execute_write(
        handle,
        "INSERT INTO legacy_message_blob_snapshots ("
        "migration_id, snapshot_id, session_id, owner_principal_hash, incarnation_id, "
        "delete_epoch, source_generation, source_message_count, source_non_append_generation, "
        "ordering_version, high_water_created_at, high_water_message_id, capture_cursor, "
        "capture_message_id, capture_body_id, capture_body_offset, capture_body_chain_sha256, "
        "capture_chain_sha256, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, 0, ?, ?, 'building')",
        (
            migration_id,
            snapshot_id,
            session_id,
            principal_hash,
            incarnation_id,
            delete_epoch,
            source_generation,
            message_count,
            non_append_generation,
            LEGACY_MESSAGE_BLOB_SNAPSHOT_ORDERING,
            high_water_created_at,
            high_water_message_id,
            _LEGACY_BODY_GENESIS_CHAIN,
            _LEGACY_BODY_GENESIS_CHAIN,
        ),
    )
    return _legacy_message_blob_snapshot_state(
        _legacy_message_blob_load_snapshot(handle, session_id, migration_id)
    )


def capture_legacy_message_blob_snapshot_page_in_transaction(
    handle,
    *,
    session_id,
    migration_id,
    snapshot_id,
    expected_cursor,
    expected_message_id,
    expected_body_offset,
    expected_body_chain_sha256,
    expected_capture_chain_sha256,
    max_rows=LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_ROWS,
    max_body_bytes=LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_BODY_BYTES,
):
    """Capture one CAS page through a write-locked migration owner handle.

    The handle must bind the current owner/session/incarnation/delete epoch to
    the same transaction and expose ``read_message_content_chunk`` and
    ``read_message_text_chunk``. The latter is limited to sources, tool_calls,
    and activity metadata. This function never opens a connection and never
    selects the content or those JSON metadata values. The combined UTF-8
    payload limit is ``max_body_bytes + max_rows * 256 KiB``
    (at most 9 MiB); it bounds payload bytes, not exact process RSS.
    """
    _legacy_message_blob_validate_id(migration_id, "migration_id")
    _legacy_message_blob_validate_id(snapshot_id, "snapshot_id")
    if (
        type(expected_cursor) is not int
        or expected_cursor < 0
        or (
            expected_message_id is not None
            and (
                type(expected_message_id) is not str
                or len(expected_message_id) > LEGACY_MESSAGE_BLOB_SNAPSHOT_FENCE_MESSAGE_ID_MAX_CHARS
                or "\x00" in expected_message_id
            )
        )
        or type(expected_body_offset) is not int
        or expected_body_offset < 0
        or type(max_rows) is not int
        or not 1 <= max_rows <= LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_ROWS
        or type(max_body_bytes) is not int
        or not 8192 <= max_body_bytes <= LEGACY_MESSAGE_BLOB_SNAPSHOT_PAGE_BODY_BYTES
    ):
        raise ValueError("legacy Message snapshot page limits or cursor are invalid")
    _legacy_body_validate_hash(expected_body_chain_sha256, "expected body chain")
    _legacy_body_validate_hash(expected_capture_chain_sha256, "expected capture chain")
    request_message_key = expected_message_id or ""
    request = (
        expected_cursor,
        request_message_key,
        expected_body_offset,
        expected_capture_chain_sha256,
        expected_body_chain_sha256,
    )
    principal_hash, incarnation_id, delete_epoch = _legacy_message_blob_owner_binding(
        handle, session_id
    )
    snapshot = _legacy_message_blob_load_snapshot(handle, session_id, migration_id)
    _legacy_message_blob_check_snapshot_owner(
        snapshot, principal_hash, incarnation_id, delete_epoch
    )
    if snapshot[1] != snapshot_id:
        raise ValueError("snapshot_id does not match migration_id")

    prior_page = _legacy_message_blob_lookup_page(handle, migration_id, request)
    if prior_page:
        if len(prior_page) != 1:
            raise ValueError("legacy Message snapshot page receipt is ambiguous")
        page_row = prior_page[0]
        if page_row[0] != max_rows or page_row[1] != max_body_bytes:
            raise ValueError("snapshot page retry changed its requested bounds")
        return _legacy_message_blob_page_receipt(
            handle, migration_id, snapshot[1], request, page_row
        )

    if snapshot[18] != "building":
        raise ValueError("legacy Message snapshot is already sealed")
    actual_message_key = snapshot[13] or ""
    if (
        snapshot[12] != expected_cursor
        or actual_message_key != request_message_key
        or snapshot[15] != expected_body_offset
        or snapshot[16] != expected_body_chain_sha256
        or snapshot[17] != expected_capture_chain_sha256
    ):
        raise ValueError("legacy Message snapshot checkpoint changed")
    _legacy_message_blob_source_matches_snapshot(handle, snapshot)

    cursor = snapshot[12]
    active_message_id = snapshot[13]
    active_body_id = snapshot[14]
    active_body_offset = snapshot[15]
    active_body_chain = snapshot[16]
    capture_chain = snapshot[17]
    page_body_bytes = 0
    page_metadata_bytes = 0
    page_source_metadata_raw_bytes = 0
    touched_sequences = set()
    combined_bytes_limit = (
        max_body_bytes
        + max_rows * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
    )

    while len(touched_sequences) < max_rows:
        if page_body_bytes >= max_body_bytes:
            break
        if active_message_id is None:
            if cursor >= snapshot[7]:
                break
            source_rowid, message_id, role, created_at, metadata_json = (
                _legacy_message_blob_source_metadata_at_sequence(
                    handle, session_id, cursor
                )
            )
            length, first_bytes = _legacy_message_blob_read_source_chunk(
                handle,
                session_id=session_id,
                message_id=message_id,
                rowid=source_rowid,
                byte_offset=0,
                max_bytes=0,
            )
            if first_bytes or length < 0 or length > _LEGACY_BODY_MAX_SQLITE_INTEGER:
                raise ValueError("legacy Message source body length is invalid")
            body_id = _legacy_message_blob_body_id(migration_id, cursor)
            occupied_body_ids = _legacy_body_query_rows(
                handle,
                "SELECT body_id FROM message_body_manifests WHERE body_id = ? LIMIT 2",
                (body_id,),
            )
            if occupied_body_ids:
                raise ValueError("deterministic legacy Message body ID is already occupied")
            body_state = begin_message_body_write_in_transaction(
                handle, session_id=session_id, body_id=body_id
            )
            if body_state.bytes_appended != 0 or body_state.status != "building":
                raise ValueError("new legacy Message body writer has unexpected progress")
            _legacy_body_execute_write(
                handle,
                "INSERT INTO legacy_message_blob_snapshot_rows ("
                "migration_id, sequence, legacy_message_id, source_rowid, body_id, role, "
                "created_at, metadata_json, body_byte_length, body_sha256, row_hash, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'capturing')",
                (
                    migration_id,
                    cursor,
                    message_id,
                    source_rowid,
                    body_id,
                    role,
                    created_at,
                    metadata_json,
                    length,
                ),
            )
            active_message_id = message_id
            active_body_id = body_id
            active_body_offset = 0
            active_body_chain = _LEGACY_BODY_GENESIS_CHAIN
            source_row = _legacy_message_blob_load_row(handle, migration_id, cursor)
        else:
            source_row = _legacy_message_blob_load_row(handle, migration_id, cursor)
            if (
                source_row[1] != active_message_id
                or source_row[3] != active_body_id
                or source_row[10] != "capturing"
            ):
                raise ValueError("legacy Message snapshot active row is inconsistent")
            body_state = _message_body_state(
                _message_body_load(handle, session_id, active_body_id)
            )
            if body_state.status != "building":
                raise ValueError("legacy Message snapshot body writer is not building")

        if cursor not in touched_sequences:
            row_metadata_upper_bound = _legacy_message_blob_snapshot_row_metadata_upper_bound(
                migration_id, snapshot[1], source_row
            )
            source_metadata_raw_bytes = _legacy_message_blob_metadata_source_raw_size(
                source_row[6]
            )
            page_metadata_bytes += row_metadata_upper_bound
            page_source_metadata_raw_bytes += source_metadata_raw_bytes
            touched_sequences.add(cursor)
            if (
                page_metadata_bytes
                > max_rows * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
                or page_source_metadata_raw_bytes
                > max_rows * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
                or page_body_bytes + page_metadata_bytes > combined_bytes_limit
            ):
                raise ValueError("legacy Message snapshot page exceeds its metadata or raw-byte bound")
        body_length = source_row[7]
        if (
            body_state.bytes_appended != active_body_offset
            or body_state.chain_sha256 != active_body_chain
            or body_state.bytes_appended > body_length
        ):
            raise ValueError("legacy Message snapshot body checkpoint is inconsistent")

        if body_length == body_state.bytes_appended:
            body_manifest = finalize_message_body_write_in_transaction(
                handle, session_id=session_id, body_id=active_body_id
            )
            if body_manifest.byte_length != body_length:
                raise ValueError("legacy Message body length differs from the source fence")
            row_hash = _legacy_message_blob_row_hash(
                migration_id=migration_id,
                snapshot_id=snapshot[1],
                sequence=cursor,
                message_id=source_row[1],
                source_rowid=source_row[2],
                metadata_json=source_row[6],
                body_byte_length=body_length,
                body_sha256=body_manifest.sha256,
            )
            changed = _legacy_body_execute_write(
                handle,
                "UPDATE legacy_message_blob_snapshot_rows SET body_sha256 = ?, row_hash = ?, "
                "status = 'sealed' WHERE migration_id = ? AND sequence = ? "
                "AND status = 'capturing' AND body_id = ? AND body_byte_length = ?",
                (body_manifest.sha256, row_hash, migration_id, cursor, active_body_id, body_length),
            )
            if changed != 1:
                raise ValueError("legacy Message snapshot row could not be sealed")
            capture_chain = _legacy_message_blob_chain_hash(capture_chain, cursor, row_hash)
            cursor += 1
            active_message_id = None
            active_body_id = None
            active_body_offset = 0
            active_body_chain = _LEGACY_BODY_GENESIS_CHAIN
            continue

        chunk_length = min(LEGACY_BODY_CHUNK_SIZE, body_length - body_state.bytes_appended)
        if chunk_length > max_body_bytes - page_body_bytes:
            break
        source_length, chunk_bytes = _legacy_message_blob_read_source_chunk(
            handle,
            session_id=session_id,
            message_id=active_message_id,
            rowid=source_row[2],
            byte_offset=body_state.bytes_appended,
            max_bytes=chunk_length,
        )
        if (
            source_length != body_length
            or len(chunk_bytes) != chunk_length
            or not chunk_bytes
        ):
            raise ValueError("legacy Message source changed or returned a short UTF-8 chunk")
        append_message_body_chunk_in_transaction(
            handle,
            session_id=session_id,
            body_id=active_body_id,
            byte_offset=body_state.bytes_appended,
            chunk_bytes=chunk_bytes,
        )
        page_body_bytes += len(chunk_bytes)
        if page_body_bytes + page_metadata_bytes > combined_bytes_limit:
            raise ValueError("legacy Message snapshot page exceeds its combined byte bound")
        body_state = _message_body_state(
            _message_body_load(handle, session_id, active_body_id)
        )
        active_body_offset = body_state.bytes_appended
        active_body_chain = body_state.chain_sha256

    new_status = "sealed" if cursor == snapshot[7] and active_message_id is None else "building"
    if (
        page_body_bytes > max_body_bytes
        or page_metadata_bytes
        > max_rows * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_MAX_BYTES
        or page_source_metadata_raw_bytes
        > max_rows * LEGACY_MESSAGE_BLOB_SNAPSHOT_ROW_METADATA_SOURCE_MAX_RAW_BYTES
        or page_body_bytes + page_metadata_bytes > combined_bytes_limit
    ):
        raise ValueError("legacy Message snapshot page exceeds its combined byte bound")
    _legacy_message_blob_source_matches_snapshot(handle, snapshot)
    changed = _legacy_body_execute_write(
        handle,
        "UPDATE legacy_message_blob_snapshots SET capture_cursor = ?, capture_message_id = ?, "
        "capture_body_id = ?, capture_body_offset = ?, capture_body_chain_sha256 = ?, "
        "capture_chain_sha256 = ?, status = ? WHERE migration_id = ? AND session_id = ? "
        "AND status = 'building' AND capture_cursor = ? AND capture_message_id IS ? "
        "AND capture_body_id IS ? AND capture_body_offset = ? "
        "AND capture_body_chain_sha256 = ? AND capture_chain_sha256 = ?",
        (
            cursor,
            active_message_id,
            active_body_id,
            active_body_offset,
            active_body_chain,
            capture_chain,
            new_status,
            migration_id,
            session_id,
            snapshot[12],
            snapshot[13],
            snapshot[14],
            snapshot[15],
            snapshot[16],
            snapshot[17],
        ),
    )
    if changed != 1:
        raise ValueError("legacy Message snapshot checkpoint changed during page capture")

    response_message_id = active_message_id or ""
    _legacy_body_execute_write(
        handle,
        "INSERT INTO legacy_message_blob_snapshot_pages ("
        "migration_id, request_cursor, request_message_id, request_body_offset, "
        "request_chain_sha256, request_body_chain_sha256, max_rows, max_body_bytes, "
        "response_cursor, response_message_id, response_body_offset, response_chain_sha256, "
        "response_capture_chain_sha256, row_count, body_bytes_captured, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            migration_id,
            request[0],
            request[1],
            request[2],
            request[3],
            request[4],
            max_rows,
            max_body_bytes,
            cursor,
            response_message_id,
            active_body_offset,
            active_body_chain,
            capture_chain,
            len(touched_sequences),
            page_body_bytes,
            new_status,
        ),
    )
    page_rows = _legacy_message_blob_lookup_page(handle, migration_id, request)
    if len(page_rows) != 1:
        raise ValueError("legacy Message snapshot page receipt was not recorded")
    return _legacy_message_blob_page_receipt(
        handle, migration_id, snapshot[1], request, page_rows[0]
    )


def read_legacy_message_blob_snapshot_body_chunk_in_transaction(
    handle,
    *,
    session_id,
    migration_id,
    snapshot_id,
    message_id,
    row_hash,
    byte_offset,
    max_bytes=LEGACY_BODY_CHUNK_SIZE,
):
    """Read a sealed body from the caller's current migration owner transaction."""
    for value, name in ((migration_id, "migration_id"), (snapshot_id, "snapshot_id")):
        _legacy_message_blob_validate_id(value, name)
    if (
        type(message_id) is not str
        or not message_id
        or len(message_id) > 512
        or type(byte_offset) is not int
        or byte_offset < 0
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= LEGACY_BODY_CHUNK_SIZE
    ):
        raise ValueError("legacy Message snapshot read cursor is invalid")
    _legacy_body_validate_hash(row_hash, "row_hash")
    principal_hash, incarnation_id, delete_epoch = _legacy_message_blob_owner_binding(
        handle, session_id
    )
    snapshot = _legacy_message_blob_load_snapshot(handle, session_id, migration_id)
    _legacy_message_blob_check_snapshot_owner(
        snapshot, principal_hash, incarnation_id, delete_epoch
    )
    if (
        snapshot[1] != snapshot_id
        or snapshot[18] != "sealed"
        or snapshot[12] != snapshot[7]
    ):
        raise ValueError("legacy Message snapshot is not the requested sealed generation")
    sequence_rows = _legacy_body_query_rows(
        handle,
        "SELECT sequence "
        "FROM legacy_message_blob_snapshot_rows WHERE migration_id = ? "
        "AND legacy_message_id = ? LIMIT 2",
        (migration_id, message_id),
    )
    if len(sequence_rows) != 1:
        raise LookupError("legacy Message snapshot row is unavailable")
    row = _legacy_message_blob_load_row(handle, migration_id, sequence_rows[0][0])
    if row[1] != message_id:
        raise ValueError("legacy Message snapshot row ID changed after preflight")
    if row[10] != "sealed" or row[9] != row_hash:
        raise ValueError("legacy Message snapshot row hash or seal state does not match")
    expected_row_hash = _legacy_message_blob_row_hash(
        migration_id=migration_id,
        snapshot_id=snapshot_id,
        sequence=row[0],
        message_id=row[1],
        source_rowid=row[2],
        metadata_json=row[6],
        body_byte_length=row[7],
        body_sha256=row[8],
    )
    if expected_row_hash != row_hash:
        raise ValueError("legacy Message snapshot row hash verification failed")
    body_manifest = _message_body_load(handle, session_id, row[3])
    _message_body_check_owner(body_manifest, principal_hash, incarnation_id)
    if (
        body_manifest[4] != "writer_verified"
        or body_manifest[10] != row[7]
        or body_manifest[11] != row[8]
    ):
        raise ValueError("legacy Message snapshot body manifest does not match its row")
    if byte_offset > row[7]:
        raise ValueError("legacy Message snapshot offset is beyond the body end")
    if byte_offset == row[7]:
        return LegacyMessageBlobSnapshotBodyChunk(
            migration_id=migration_id,
            snapshot_id=snapshot_id,
            message_id=message_id,
            row_hash=row_hash,
            byte_offset=byte_offset,
            next_offset=byte_offset,
            chunk_bytes=b"",
            chunk_sha256=hashlib.sha256(b"").hexdigest(),
            body_byte_length=row[7],
            body_sha256=row[8],
        )

    aligned_offset = (byte_offset // LEGACY_BODY_CHUNK_SIZE) * LEGACY_BODY_CHUNK_SIZE
    body_chunk = read_message_body_chunk_in_transaction(
        handle,
        session_id=session_id,
        body_id=row[3],
        byte_offset=aligned_offset,
        max_rows=3,
        max_bytes=LEGACY_BODY_CHUNK_SIZE,
    )
    if (
        body_chunk is None
        or body_chunk.manifest_byte_length != row[7]
        or body_chunk.manifest_sha256 != row[8]
        or body_chunk.byte_offset != aligned_offset
    ):
        raise ValueError("legacy Message snapshot BLOB chunk is unavailable or mismatched")
    inner_offset = byte_offset - aligned_offset
    chunk_bytes = body_chunk.chunk_bytes[inner_offset : inner_offset + max_bytes]
    if not chunk_bytes:
        raise ValueError("legacy Message snapshot BLOB chunk made no progress")
    return LegacyMessageBlobSnapshotBodyChunk(
        migration_id=migration_id,
        snapshot_id=snapshot_id,
        message_id=message_id,
        row_hash=row_hash,
        byte_offset=byte_offset,
        next_offset=byte_offset + len(chunk_bytes),
        chunk_bytes=chunk_bytes,
        chunk_sha256=hashlib.sha256(chunk_bytes).hexdigest(),
        body_byte_length=row[7],
        body_sha256=row[8],
    )


def _ensure_legacy_body_chunk_storage_schema():
    """Install SQLite guards that keep copied chunks append-only and finalized rows frozen."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "legacy body chunk storage requires SQLite; startup aborted"
        )

    trigger_sql = (
        "CREATE TRIGGER IF NOT EXISTS legacy_body_chunks_insert_guard "
        "BEFORE INSERT ON legacy_body_chunks BEGIN "
        "SELECT RAISE(ABORT, 'legacy body chunk violates the migration cursor') "
        "WHERE NOT EXISTS (SELECT 1 FROM legacy_body_migrations AS m "
        "WHERE m.migration_id = NEW.migration_id AND m.status = 'building' "
        "AND NEW.chunk_index = m.next_chunk_index "
        "AND NEW.byte_offset = m.bytes_appended "
        "AND NEW.previous_chain_sha256 = m.chain_sha256 "
        "AND typeof(NEW.body) = 'blob' "
        "AND NEW.chunk_length = length(NEW.body) "
        "AND NEW.chunk_length > 0 AND NEW.chunk_length <= m.chunk_size "
        "AND NEW.chunk_length = CASE "
        "WHEN m.source_byte_length - m.bytes_appended > m.chunk_size "
        "THEN m.chunk_size ELSE m.source_byte_length - m.bytes_appended END); END"
    )
    chunk_update_sql = (
        "CREATE TRIGGER IF NOT EXISTS legacy_body_chunks_no_update "
        "BEFORE UPDATE ON legacy_body_chunks BEGIN "
        "SELECT RAISE(ABORT, 'legacy body chunks are immutable'); END"
    )
    chunk_delete_sql = (
        "CREATE TRIGGER IF NOT EXISTS legacy_body_chunks_no_delete "
        "BEFORE DELETE ON legacy_body_chunks WHEN EXISTS ("
        "SELECT 1 FROM legacy_body_migrations AS m "
        "JOIN sessions AS s ON s.id = m.session_id "
        "WHERE m.migration_id = OLD.migration_id) BEGIN "
        "SELECT RAISE(ABORT, 'legacy body chunks are immutable'); END"
    )
    migration_delete_sql = (
        "CREATE TRIGGER IF NOT EXISTS legacy_body_migrations_no_delete "
        "BEFORE DELETE ON legacy_body_migrations WHEN EXISTS ("
        "SELECT 1 FROM sessions AS s WHERE s.id = OLD.session_id) BEGIN "
        "SELECT RAISE(ABORT, 'legacy body manifests are immutable'); END"
    )
    migration_update_sql = (
        "CREATE TRIGGER IF NOT EXISTS legacy_body_migrations_update_guard "
        "BEFORE UPDATE ON legacy_body_migrations WHEN "
        "OLD.status = 'complete' "
        "OR NEW.migration_id IS NOT OLD.migration_id "
        "OR NEW.session_id IS NOT OLD.session_id "
        "OR NEW.incarnation_id IS NOT OLD.incarnation_id "
        "OR NEW.owner_principal_hash IS NOT OLD.owner_principal_hash "
        "OR NEW.source_key_digest IS NOT OLD.source_key_digest "
        "OR NEW.source_byte_length IS NOT OLD.source_byte_length "
        "OR NEW.source_sha256 IS NOT OLD.source_sha256 "
        "OR NEW.chunk_size IS NOT OLD.chunk_size "
        "OR NEW.source_verification_status IS NOT OLD.source_verification_status "
        "OR NOT ("
        "(NEW.status = 'building' AND OLD.status = 'building' "
        "AND NEW.verified_byte_length IS NULL AND NEW.verified_sha256 IS NULL "
        "AND NEW.next_chunk_index = OLD.next_chunk_index + 1 "
        "AND NEW.bytes_appended = OLD.bytes_appended + COALESCE(("
        "SELECT chunk_length FROM legacy_body_chunks "
        "WHERE migration_id = OLD.migration_id "
        "AND chunk_index = OLD.next_chunk_index), -1) "
        "AND NEW.chain_sha256 = COALESCE((SELECT chain_sha256 "
        "FROM legacy_body_chunks WHERE migration_id = OLD.migration_id "
        "AND chunk_index = OLD.next_chunk_index), '') "
        "AND NEW.source_verification_status = 'source_unverified') "
        "OR (NEW.status = 'complete' AND OLD.status = 'building' "
        "AND NEW.next_chunk_index = OLD.next_chunk_index "
        "AND NEW.bytes_appended = OLD.bytes_appended "
        "AND NEW.chain_sha256 = OLD.chain_sha256 "
        "AND NEW.verified_byte_length = OLD.source_byte_length "
        "AND NEW.verified_sha256 = OLD.source_sha256 "
        "AND NEW.source_verification_status = 'source_unverified' "
        "AND NEW.next_chunk_index = "
        "((OLD.source_byte_length + OLD.chunk_size - 1) / OLD.chunk_size) "
        "AND NEW.bytes_appended = OLD.source_byte_length "
        "AND (SELECT COUNT(*) FROM legacy_body_chunks "
        "WHERE migration_id = OLD.migration_id) = NEW.next_chunk_index)) "
        "BEGIN SELECT RAISE(ABORT, 'legacy body manifest update is invalid'); END"
    )
    message_body_chunk_insert_sql = (
        "CREATE TRIGGER IF NOT EXISTS message_body_chunks_insert_guard "
        "BEFORE INSERT ON message_body_chunks BEGIN "
        "SELECT RAISE(ABORT, 'message body chunk violates the write cursor') "
        "WHERE NOT EXISTS (SELECT 1 FROM message_body_manifests AS m "
        "WHERE m.body_id = NEW.body_id AND m.status = 'building' "
        "AND m.terminal_chunk_offset IS NULL "
        "AND NEW.byte_offset = m.next_chunk_offset "
        "AND NEW.previous_chain_sha256 = m.chain_sha256 "
        "AND typeof(NEW.body) = 'blob' "
        "AND NEW.chunk_length = length(NEW.body) "
        "AND NEW.chunk_length > 0 AND NEW.chunk_length <= 8192); END"
    )
    message_body_chunk_update_sql = (
        "CREATE TRIGGER IF NOT EXISTS message_body_chunks_no_update "
        "BEFORE UPDATE ON message_body_chunks BEGIN "
        "SELECT RAISE(ABORT, 'message body chunks are immutable'); END"
    )
    message_body_chunk_delete_sql = (
        "CREATE TRIGGER IF NOT EXISTS message_body_chunks_no_delete "
        "BEFORE DELETE ON message_body_chunks WHEN EXISTS ("
        "SELECT 1 FROM message_body_manifests AS m "
        "JOIN sessions AS s ON s.id = m.session_id "
        "WHERE m.body_id = OLD.body_id) BEGIN "
        "SELECT RAISE(ABORT, 'message body chunks are immutable'); END"
    )
    message_body_manifest_update_sql = (
        "CREATE TRIGGER IF NOT EXISTS message_body_manifests_update_guard "
        "BEFORE UPDATE ON message_body_manifests WHEN "
        "OLD.status = 'writer_verified' "
        "OR NEW.body_id IS NOT OLD.body_id "
        "OR NEW.session_id IS NOT OLD.session_id "
        "OR NEW.incarnation_id IS NOT OLD.incarnation_id "
        "OR NEW.owner_principal_hash IS NOT OLD.owner_principal_hash "
        "OR NOT ("
        "(NEW.status = 'building' AND OLD.status = 'building' "
        "AND NEW.byte_length IS NULL AND NEW.sha256 IS NULL "
        "AND NEW.final_chain_sha256 IS NULL "
        "AND OLD.terminal_chunk_offset IS NULL "
        "AND NEW.next_chunk_offset = OLD.next_chunk_offset + 8192 "
        "AND NEW.bytes_appended = OLD.bytes_appended + COALESCE(("
        "SELECT chunk_length FROM message_body_chunks "
        "WHERE body_id = OLD.body_id AND byte_offset = OLD.next_chunk_offset), -1) "
        "AND NEW.chunk_count = OLD.chunk_count + 1 "
        "AND NEW.chain_sha256 = COALESCE((SELECT chain_sha256 "
        "FROM message_body_chunks WHERE body_id = OLD.body_id "
        "AND byte_offset = OLD.next_chunk_offset), '') "
        "AND NEW.terminal_chunk_offset IS CASE WHEN COALESCE(("
        "SELECT chunk_length FROM message_body_chunks "
        "WHERE body_id = OLD.body_id AND byte_offset = OLD.next_chunk_offset), 8192) < 8192 "
        "THEN OLD.next_chunk_offset ELSE NULL END) "
        "OR (NEW.status = 'writer_verified' AND OLD.status = 'building' "
        "AND NEW.next_chunk_offset = OLD.next_chunk_offset "
        "AND NEW.bytes_appended = OLD.bytes_appended "
        "AND NEW.chunk_count = OLD.chunk_count "
        "AND NEW.terminal_chunk_offset IS OLD.terminal_chunk_offset "
        "AND NEW.chain_sha256 = OLD.chain_sha256 "
        "AND NEW.byte_length = OLD.bytes_appended "
        "AND length(NEW.sha256) = 64 "
        "AND NEW.final_chain_sha256 = OLD.chain_sha256 "
        "AND (SELECT COUNT(*) FROM message_body_chunks "
        "WHERE body_id = OLD.body_id) = NEW.chunk_count)) "
        "BEGIN SELECT RAISE(ABORT, 'message body manifest update is invalid'); END"
    )
    message_body_manifest_delete_sql = (
        "CREATE TRIGGER IF NOT EXISTS message_body_manifests_no_delete "
        "BEFORE DELETE ON message_body_manifests WHEN EXISTS ("
        "SELECT 1 FROM sessions AS s WHERE s.id = OLD.session_id) BEGIN "
        "SELECT RAISE(ABORT, 'message body manifests are immutable'); END"
    )
    with engine.begin() as conn:
        for statement in (
            trigger_sql,
            chunk_update_sql,
            chunk_delete_sql,
            migration_update_sql,
            migration_delete_sql,
            message_body_chunk_insert_sql,
            message_body_chunk_update_sql,
            message_body_chunk_delete_sql,
            message_body_manifest_update_sql,
            message_body_manifest_delete_sql,
        ):
            conn.exec_driver_sql(statement)


def get_db():
    """FastAPI 依赖注入：获取数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_session_message_revision_schema():
    """Add and strictly validate the SQLite sessions.message_revision column."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "sessions.message_revision migration requires a SQLite database"
        )

    with engine.begin() as conn:
        columns = conn.exec_driver_sql("PRAGMA table_info(sessions)").all()
        revision_column = next(
            (column for column in columns if column[1] == "message_revision"),
            None,
        )

        if revision_column is None:
            conn.exec_driver_sql(
                "ALTER TABLE sessions ADD COLUMN message_revision INTEGER "
                "NOT NULL DEFAULT 0 CHECK(message_revision >= 0)"
            )
            columns = conn.exec_driver_sql("PRAGMA table_info(sessions)").all()
            revision_column = next(
                (column for column in columns if column[1] == "message_revision"),
                None,
            )

        if revision_column is None:
            raise RuntimeError(
                "sessions.message_revision migration did not create the column"
            )
        if str(revision_column[2]).strip().upper() != "INTEGER":
            raise RuntimeError(
                "sessions.message_revision must have SQLite type INTEGER"
            )
        if revision_column[3] != 1:
            raise RuntimeError(
                "sessions.message_revision must be declared NOT NULL"
            )
        default_value = revision_column[4]
        default_sql = "" if default_value is None else str(default_value).strip()
        while default_sql.startswith("(") and default_sql.endswith(")"):
            default_sql = default_sql[1:-1].strip()
        if default_sql not in {"0", "'0'"}:
            raise RuntimeError(
                "sessions.message_revision must have DEFAULT 0 or '0'"
            )

        schema_row = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'sessions'"
        ).first()
        if schema_row is None or not schema_row[0]:
            raise RuntimeError("could not inspect the SQLite sessions table schema")
        check_pattern = (
            r"\bCHECK\s*\(\s*"
            r"(?:message_revision|\"message_revision\"|`message_revision`|"
            r"\[message_revision\])\s*>=\s*0\s*\)"
        )
        if re.search(check_pattern, schema_row[0], flags=re.IGNORECASE) is None:
            raise RuntimeError(
                "sessions.message_revision must have CHECK(message_revision >= 0)"
            )

        invalid_rows = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM sessions "
            "WHERE typeof(message_revision) != 'integer' "
            "OR message_revision < 0"
        ).scalar_one()
        if invalid_rows:
            raise RuntimeError(
                "sessions.message_revision contains non-integer or negative data"
            )


def _ensure_message_source_generation_schema():
    """Install the SQLite-only, session-owned Message mutation fence."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "Message source generation requires SQLite; startup aborted"
        )

    table_name = "message_source_generations"
    table_sql = (
        "CREATE TABLE message_source_generations ("
        "session_id TEXT NOT NULL PRIMARY KEY "
        "REFERENCES sessions(id) ON DELETE CASCADE, "
        "generation INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(generation) = 'integer' AND generation >= 0), "
        "message_count INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(message_count) = 'integer' AND message_count >= 0), "
        "non_append_generation INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(non_append_generation) = 'integer' "
        "AND non_append_generation >= 0), "
        "tail_order_created_at, "
        "tail_order_message_id TEXT, "
        "tail_order_dirty INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(tail_order_dirty) = 'integer' "
        "AND tail_order_dirty IN (0, 1))"
        ")"
    )
    count_table_sql = (
        "CREATE TABLE message_source_generations ("
        "session_id TEXT NOT NULL PRIMARY KEY "
        "REFERENCES sessions(id) ON DELETE CASCADE, "
        "generation INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(generation) = 'integer' AND generation >= 0), "
        "message_count INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(message_count) = 'integer' AND message_count >= 0)"
        ")"
    )
    trigger_sql = {
        "message_source_generation_sessions_ai": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_sessions_ai "
            "AFTER INSERT ON sessions BEGIN "
            "INSERT INTO message_source_generations "
            "(session_id, generation, message_count, non_append_generation, "
            "tail_order_created_at, tail_order_message_id, tail_order_dirty) "
            "VALUES (NEW.id, 0, 0, 0, NULL, NULL, 0); END"
        ),
        "message_source_generation_messages_ai": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_ai "
            "AFTER INSERT ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE NEW.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = NEW.session_id); "
            "UPDATE message_source_generations "
            "SET generation = generation + 1, message_count = message_count + 1, "
            "non_append_generation = non_append_generation + CASE "
            "WHEN tail_order_dirty != 0 THEN 1 "
            "WHEN message_count = 0 THEN 0 "
            "WHEN NEW.created_at IS NOT NULL AND tail_order_created_at IS NULL THEN 0 "
            "WHEN NEW.created_at IS tail_order_created_at "
            "AND NEW.id COLLATE BINARY > tail_order_message_id COLLATE BINARY THEN 0 "
            "WHEN NEW.created_at IS NOT NULL AND tail_order_created_at IS NOT NULL "
            "AND NEW.created_at > tail_order_created_at THEN 0 ELSE 1 END, "
            "tail_order_created_at = CASE WHEN tail_order_dirty = 0 AND ("
            "message_count = 0 OR "
            "(NEW.created_at IS NOT NULL AND tail_order_created_at IS NULL) OR "
            "(NEW.created_at IS tail_order_created_at AND "
            "NEW.id COLLATE BINARY > tail_order_message_id COLLATE BINARY) OR "
            "(NEW.created_at IS NOT NULL AND tail_order_created_at IS NOT NULL "
            "AND NEW.created_at > tail_order_created_at)) "
            "THEN NEW.created_at ELSE tail_order_created_at END, "
            "tail_order_message_id = CASE WHEN tail_order_dirty = 0 AND ("
            "message_count = 0 OR "
            "(NEW.created_at IS NOT NULL AND tail_order_created_at IS NULL) OR "
            "(NEW.created_at IS tail_order_created_at AND "
            "NEW.id COLLATE BINARY > tail_order_message_id COLLATE BINARY) OR "
            "(NEW.created_at IS NOT NULL AND tail_order_created_at IS NOT NULL "
            "AND NEW.created_at > tail_order_created_at)) "
            "THEN NEW.id ELSE tail_order_message_id END "
            "WHERE session_id = NEW.session_id; END"
        ),
        "message_source_generation_messages_au": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_au "
            "AFTER UPDATE ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE (OLD.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = OLD.session_id)) OR "
            "(NEW.session_id IS NOT NULL "
            "AND NEW.session_id IS NOT OLD.session_id AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = NEW.session_id)); "
            "UPDATE message_source_generations SET generation = generation + 1, "
            "non_append_generation = non_append_generation + 1, "
            "tail_order_dirty = 1, "
            "message_count = message_count + CASE "
            "WHEN NEW.session_id IS OLD.session_id THEN 0 ELSE -1 END "
            "WHERE session_id = OLD.session_id; "
            "UPDATE message_source_generations SET generation = generation + 1, "
            "non_append_generation = non_append_generation + 1, "
            "tail_order_dirty = 1, "
            "message_count = message_count + 1 "
            "WHERE session_id = NEW.session_id "
            "AND NEW.session_id IS NOT OLD.session_id; END"
        ),
        "message_source_generation_messages_ad": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_ad "
            "AFTER DELETE ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE OLD.session_id IS NOT NULL AND EXISTS "
            "(SELECT 1 FROM sessions WHERE id = OLD.session_id) AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = OLD.session_id); "
            "UPDATE message_source_generations SET generation = generation + 1, "
            "non_append_generation = non_append_generation + 1, "
            "tail_order_dirty = 1, "
            "message_count = message_count - 1 "
            "WHERE session_id = OLD.session_id; END"
        ),
    }
    count_trigger_sql = {
        "message_source_generation_sessions_ai": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_sessions_ai "
            "AFTER INSERT ON sessions BEGIN "
            "INSERT INTO message_source_generations "
            "(session_id, generation, message_count) "
            "VALUES (NEW.id, 0, 0); END"
        ),
        "message_source_generation_messages_ai": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_ai "
            "AFTER INSERT ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE NEW.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = NEW.session_id); "
            "UPDATE message_source_generations "
            "SET generation = generation + 1, message_count = message_count + 1 "
            "WHERE session_id = NEW.session_id; END"
        ),
        "message_source_generation_messages_au": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_au "
            "AFTER UPDATE ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE (OLD.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = OLD.session_id)) OR "
            "(NEW.session_id IS NOT NULL "
            "AND NEW.session_id IS NOT OLD.session_id AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = NEW.session_id)); "
            "UPDATE message_source_generations SET generation = generation + 1, "
            "message_count = message_count + CASE "
            "WHEN NEW.session_id IS OLD.session_id THEN 0 ELSE -1 END "
            "WHERE session_id = OLD.session_id; "
            "UPDATE message_source_generations SET generation = generation + 1, "
            "message_count = message_count + 1 "
            "WHERE session_id = NEW.session_id "
            "AND NEW.session_id IS NOT OLD.session_id; END"
        ),
        "message_source_generation_messages_ad": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_ad "
            "AFTER DELETE ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE OLD.session_id IS NOT NULL AND EXISTS "
            "(SELECT 1 FROM sessions WHERE id = OLD.session_id) AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = OLD.session_id); "
            "UPDATE message_source_generations SET generation = generation + 1, "
            "message_count = message_count - 1 "
            "WHERE session_id = OLD.session_id; END"
        ),
    }
    legacy_table_sql = (
        "CREATE TABLE message_source_generations ("
        "session_id TEXT NOT NULL PRIMARY KEY "
        "REFERENCES sessions(id) ON DELETE CASCADE, "
        "generation INTEGER NOT NULL DEFAULT 0 "
        "CHECK(typeof(generation) = 'integer' AND generation >= 0)"
        ")"
    )
    legacy_trigger_sql = {
        "message_source_generation_sessions_ai": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_sessions_ai "
            "AFTER INSERT ON sessions BEGIN "
            "INSERT INTO message_source_generations (session_id, generation) "
            "VALUES (NEW.id, 0); END"
        ),
        "message_source_generation_messages_ai": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_ai "
            "AFTER INSERT ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE NEW.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = NEW.session_id); "
            "UPDATE message_source_generations SET generation = generation + 1 "
            "WHERE session_id = NEW.session_id; END"
        ),
        "message_source_generation_messages_au": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_au "
            "AFTER UPDATE ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE (OLD.session_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = OLD.session_id)) OR "
            "(NEW.session_id IS NOT NULL "
            "AND NEW.session_id IS NOT OLD.session_id AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = NEW.session_id)); "
            "UPDATE message_source_generations SET generation = generation + 1 "
            "WHERE session_id = OLD.session_id; "
            "UPDATE message_source_generations SET generation = generation + 1 "
            "WHERE session_id = NEW.session_id "
            "AND NEW.session_id IS NOT OLD.session_id; END"
        ),
        "message_source_generation_messages_ad": (
            "CREATE TRIGGER IF NOT EXISTS message_source_generation_messages_ad "
            "AFTER DELETE ON messages BEGIN "
            "SELECT RAISE(ABORT, 'message source generation is missing') "
            "WHERE OLD.session_id IS NOT NULL AND EXISTS "
            "(SELECT 1 FROM sessions WHERE id = OLD.session_id) AND NOT EXISTS "
            "(SELECT 1 FROM message_source_generations "
            "WHERE session_id = OLD.session_id); "
            "UPDATE message_source_generations SET generation = generation + 1 "
            "WHERE session_id = OLD.session_id; END"
        ),
    }
    trigger_tables = {
        "message_source_generation_sessions_ai": "sessions",
        "message_source_generation_messages_ai": "messages",
        "message_source_generation_messages_au": "messages",
        "message_source_generation_messages_ad": "messages",
    }

    def normalize_sql(value):
        return re.sub(r"\s+", "", str(value)).casefold().replace(";", "").replace(
            "ifnotexists", ""
        )

    def is_text_affinity(value):
        declared_type = str(value or "").strip().upper()
        return any(marker in declared_type for marker in ("CHAR", "CLOB", "TEXT"))

    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
                raise RuntimeError(
                    "SQLite foreign keys must be enabled for Message source generation"
                )

            session_columns = conn.exec_driver_sql(
                "PRAGMA table_info(sessions)"
            ).all()
            session_id = next(
                (column for column in session_columns if column[1] == "id"), None
            )
            if (
                session_id is None
                or not is_text_affinity(session_id[2])
                or session_id[3] != 1
                or session_id[5] != 1
            ):
                raise RuntimeError(
                    "sessions.id must be a textual primary key for Message source generation"
                )

            message_columns = conn.exec_driver_sql(
                "PRAGMA table_info(messages)"
            ).all()
            message_session_id = next(
                (column for column in message_columns if column[1] == "session_id"),
                None,
            )
            if message_session_id is None or not is_text_affinity(message_session_id[2]):
                raise RuntimeError(
                    "messages.session_id must be a textual column for Message source generation"
                )
            message_id = next(
                (column for column in message_columns if column[1] == "id"), None,
            )
            message_created_at = next(
                (column for column in message_columns if column[1] == "created_at"),
                None,
            )
            if (
                message_id is None
                or not is_text_affinity(message_id[2])
                or message_id[5] != 1
                or sum(1 for column in message_columns if column[5] != 0) != 1
            ):
                raise RuntimeError(
                    "messages.id must be the unique textual primary key for Message "
                    "source ordering fences"
                )
            if message_created_at is None:
                raise RuntimeError(
                    "messages.created_at is required for Message source ordering fences"
                )

            table_objects = conn.exec_driver_sql(
                "SELECT type, sql FROM sqlite_master WHERE name = ?",
                (table_name,),
            ).all()
            if any(row[0] != "table" for row in table_objects):
                raise RuntimeError(
                    "message_source_generations name is occupied by a non-table object"
                )
            table_created = not table_objects
            legacy_table = False
            count_table = False
            if table_created:
                conn.exec_driver_sql(table_sql)
                table_objects = conn.exec_driver_sql(
                    "SELECT type, sql FROM sqlite_master WHERE name = ?",
                    (table_name,),
                ).all()
            if len(table_objects) != 1 or table_objects[0][0] != "table":
                raise RuntimeError(
                    "message_source_generations table definition is incompatible"
                )
            actual_table_sql = normalize_sql(table_objects[0][1])
            if not table_created and actual_table_sql == normalize_sql(legacy_table_sql):
                legacy_table = True
            elif not table_created and actual_table_sql == normalize_sql(count_table_sql):
                count_table = True
            elif actual_table_sql != normalize_sql(table_sql):
                raise RuntimeError(
                    "message_source_generations table definition is incompatible"
                )

            generation_columns = conn.exec_driver_sql(
                "PRAGMA table_info(message_source_generations)"
            ).all()
            actual_columns = [
                (
                    column[1],
                    str(column[2]).strip().upper(),
                    column[3],
                    None if column[4] is None else str(column[4]).strip(),
                    column[5],
                )
                for column in generation_columns
            ]
            expected_columns = [
                ("session_id", "TEXT", 1, None, 1),
                ("generation", "INTEGER", 1, "0", 0),
            ]
            if not legacy_table:
                expected_columns.append(("message_count", "INTEGER", 1, "0", 0))
            if not legacy_table and not count_table:
                expected_columns.append(("non_append_generation", "INTEGER", 1, "0", 0))
                expected_columns.extend((
                    ("tail_order_created_at", "", 0, None, 0),
                    ("tail_order_message_id", "TEXT", 0, None, 0),
                    ("tail_order_dirty", "INTEGER", 1, "0", 0),
                ))
            if actual_columns != expected_columns:
                raise RuntimeError(
                    "message_source_generations columns are incompatible"
                )

            actual_foreign_keys = {
                (
                    row[2],
                    row[3],
                    row[4],
                    str(row[5]).upper(),
                    str(row[6]).upper(),
                )
                for row in conn.exec_driver_sql(
                    "PRAGMA foreign_key_list(message_source_generations)"
                ).all()
            }
            if actual_foreign_keys != {
                ("sessions", "session_id", "id", "NO ACTION", "CASCADE")
            }:
                raise RuntimeError(
                    "message_source_generations owner foreign key is incompatible"
                )

            extra_indexes = [
                row[1]
                for row in conn.exec_driver_sql(
                    "PRAGMA index_list(message_source_generations)"
                ).all()
                if row[3] != "pk"
            ]
            if extra_indexes:
                raise RuntimeError(
                    "message_source_generations has unexpected indexes: "
                    + ", ".join(extra_indexes)
                )

            invalid_generation_rows = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM message_source_generations "
                "WHERE typeof(generation) != 'integer' OR generation < 0"
            ).scalar_one()
            orphan_generation_rows = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM message_source_generations AS generation "
                "LEFT JOIN sessions ON sessions.id = generation.session_id "
                "WHERE sessions.id IS NULL"
            ).scalar_one()
            if invalid_generation_rows or orphan_generation_rows:
                raise RuntimeError(
                    "message_source_generations contains invalid or ownerless rows"
                )

            expected_triggers = (
                legacy_trigger_sql
                if legacy_table
                else count_trigger_sql
                if count_table
                else trigger_sql
            )
            trigger_objects = {}
            for name, expected_sql in expected_triggers.items():
                rows = conn.exec_driver_sql(
                    "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
                    (name,),
                ).all()
                if len(rows) > 1:
                    raise RuntimeError(
                        f"duplicate SQLite objects use trigger name {name}"
                    )
                if rows:
                    row = rows[0]
                    if (
                        row[0] != "trigger"
                        or row[1] != trigger_tables[name]
                        or normalize_sql(row[2]) != normalize_sql(expected_sql)
                    ):
                        raise RuntimeError(
                            f"SQLite trigger {name} has an incompatible definition"
                        )
                    trigger_objects[name] = True
                else:
                    trigger_objects[name] = False

            if table_created and any(trigger_objects.values()):
                raise RuntimeError(
                    "message source-generation triggers exist without their table"
                )
            if not table_created and not all(trigger_objects.values()):
                missing_triggers = sorted(
                    name for name, exists in trigger_objects.items() if not exists
                )
                raise RuntimeError(
                    "existing message_source_generations table is missing required "
                    "triggers: "
                    + ", ".join(missing_triggers)
                )

            unexpected_generation_triggers = conn.exec_driver_sql(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = ?",
                (table_name,),
            ).all()
            if unexpected_generation_triggers:
                raise RuntimeError(
                    "message_source_generations has unexpected triggers"
                )

            missing_generation_rows = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM sessions "
                "LEFT JOIN message_source_generations "
                "ON message_source_generations.session_id = sessions.id "
                "WHERE message_source_generations.session_id IS NULL"
            ).scalar_one()
            if missing_generation_rows and not table_created:
                raise RuntimeError(
                    "existing message_source_generations table is missing session rows"
                )
            if table_created:
                conn.exec_driver_sql(
                    "INSERT INTO message_source_generations "
                    "(session_id, generation, message_count, non_append_generation, "
                    "tail_order_created_at, tail_order_message_id, tail_order_dirty) "
                    "SELECT sessions.id, 0, (SELECT COUNT(*) FROM messages "
                    "WHERE messages.session_id = sessions.id), 0, "
                    "(SELECT messages.created_at FROM messages "
                    "WHERE messages.session_id = sessions.id "
                    "ORDER BY (messages.created_at IS NOT NULL) DESC, "
                    "messages.created_at DESC, messages.id COLLATE BINARY DESC "
                    "LIMIT 1), "
                    "(SELECT messages.id FROM messages "
                    "WHERE messages.session_id = sessions.id "
                    "ORDER BY (messages.created_at IS NOT NULL) DESC, "
                    "messages.created_at DESC, messages.id COLLATE BINARY DESC "
                    "LIMIT 1), 0 FROM sessions"
                )
                missing_generation_rows = conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM sessions "
                    "LEFT JOIN message_source_generations "
                    "ON message_source_generations.session_id = sessions.id "
                    "WHERE message_source_generations.session_id IS NULL"
                ).scalar_one()
            if missing_generation_rows:
                raise RuntimeError(
                    "existing sessions could not be seeded with Message source generation"
                )

            if legacy_table:
                conn.exec_driver_sql(
                    "ALTER TABLE message_source_generations ADD COLUMN "
                    "message_count INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(typeof(message_count) = 'integer' AND message_count >= 0)"
                )
                conn.exec_driver_sql(
                    "UPDATE message_source_generations SET message_count = "
                    "(SELECT COUNT(*) FROM messages WHERE messages.session_id = "
                    "message_source_generations.session_id)"
                )
            if legacy_table or count_table:
                conn.exec_driver_sql(
                    "ALTER TABLE message_source_generations ADD COLUMN "
                    "non_append_generation INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(typeof(non_append_generation) = 'integer' "
                    "AND non_append_generation >= 0)"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE message_source_generations ADD COLUMN "
                    "tail_order_created_at"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE message_source_generations ADD COLUMN "
                    "tail_order_message_id TEXT"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE message_source_generations ADD COLUMN "
                    "tail_order_dirty INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(typeof(tail_order_dirty) = 'integer' "
                    "AND tail_order_dirty IN (0, 1))"
                )
                conn.exec_driver_sql(
                    "UPDATE message_source_generations SET "
                    "non_append_generation = generation, "
                    "tail_order_created_at = (SELECT messages.created_at "
                    "FROM messages WHERE messages.session_id = "
                    "message_source_generations.session_id "
                    "ORDER BY (messages.created_at IS NOT NULL) DESC, "
                    "messages.created_at DESC, messages.id COLLATE BINARY DESC "
                    "LIMIT 1), "
                    "tail_order_message_id = (SELECT messages.id "
                    "FROM messages WHERE messages.session_id = "
                    "message_source_generations.session_id "
                    "ORDER BY (messages.created_at IS NOT NULL) DESC, "
                    "messages.created_at DESC, messages.id COLLATE BINARY DESC "
                    "LIMIT 1), tail_order_dirty = 0"
                )
                old_trigger_sql = legacy_trigger_sql if legacy_table else count_trigger_sql
                for name in old_trigger_sql:
                    conn.exec_driver_sql(f"DROP TRIGGER {name}")

            if table_created or legacy_table or count_table:
                for expected_sql in trigger_sql.values():
                    conn.exec_driver_sql(expected_sql)

            table_row = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (table_name,),
            ).first()
            if (
                table_row is None
                or normalize_sql(table_row[0]) != normalize_sql(table_sql)
            ):
                raise RuntimeError(
                    "message_source_generations table definition is incompatible"
                )

            invalid_message_counts = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM message_source_generations "
                "WHERE typeof(message_count) != 'integer' OR message_count < 0 "
                "OR message_count != (SELECT COUNT(*) FROM messages "
                "WHERE messages.session_id = "
                "message_source_generations.session_id)"
            ).scalar_one()
            if invalid_message_counts:
                raise RuntimeError(
                    "message_source_generations message counts do not match Messages"
                )
            invalid_non_append_generations = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM message_source_generations "
                "WHERE typeof(non_append_generation) != 'integer' "
                "OR non_append_generation < 0"
            ).scalar_one()
            if invalid_non_append_generations:
                raise RuntimeError(
                    "message_source_generations contains invalid append fences"
                )
            invalid_tail_order_fences = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM message_source_generations AS generation "
                "WHERE typeof(tail_order_dirty) != 'integer' "
                "OR tail_order_dirty NOT IN (0, 1) "
                "OR (tail_order_dirty = 0 AND ("
                "tail_order_created_at IS NOT (SELECT messages.created_at "
                "FROM messages WHERE messages.session_id = generation.session_id "
                "ORDER BY (messages.created_at IS NOT NULL) DESC, "
                "messages.created_at DESC, messages.id COLLATE BINARY DESC LIMIT 1) "
                "OR tail_order_message_id IS NOT (SELECT messages.id "
                "FROM messages WHERE messages.session_id = generation.session_id "
                "ORDER BY (messages.created_at IS NOT NULL) DESC, "
                "messages.created_at DESC, messages.id COLLATE BINARY DESC LIMIT 1)))"
            ).scalar_one()
            if invalid_tail_order_fences:
                raise RuntimeError(
                    "message_source_generations contains inconsistent tail-order fences"
                )
            orphan_messages = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM messages "
                "LEFT JOIN message_source_generations "
                "ON message_source_generations.session_id = messages.session_id "
                "WHERE messages.session_id IS NOT NULL "
                "AND message_source_generations.session_id IS NULL"
            ).scalar_one()
            if orphan_messages:
                raise RuntimeError(
                    "Messages contain rows without a session source-generation row"
                )

            for name, expected_sql in trigger_sql.items():
                rows = conn.exec_driver_sql(
                    "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
                    (name,),
                ).all()
                if (
                    len(rows) != 1
                    or rows[0][0] != "trigger"
                    or rows[0][1] != trigger_tables[name]
                    or normalize_sql(rows[0][2]) != normalize_sql(expected_sql)
                ):
                    raise RuntimeError(
                        f"SQLite trigger {name} has an incompatible definition"
                    )

            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _ensure_legacy_message_snapshot_schema():
    """Fail closed when existing SQLite snapshot tables differ from the model."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "legacy message snapshot schema guard requires SQLite; startup aborted"
        )

    expected_primary_keys = {
        "legacy_message_snapshot_generations": ("migration_id",),
        "legacy_message_snapshot_rows": ("migration_id", "sequence"),
        "legacy_message_blob_snapshots": ("migration_id",),
        "legacy_message_blob_snapshot_rows": ("migration_id", "sequence"),
        "legacy_message_blob_snapshot_pages": (
            "migration_id",
            "request_cursor",
            "request_message_id",
            "request_body_offset",
            "request_chain_sha256",
            "request_body_chain_sha256",
        ),
    }
    expected_foreign_keys = {
        "legacy_message_snapshot_generations": {
            (("session_id",), "sessions", ("id",), "CASCADE"),
            (("incarnation_id",), "session_incarnations", ("id",), "RESTRICT"),
        },
        "legacy_message_snapshot_rows": {
            (
                ("migration_id",),
                "legacy_message_snapshot_generations",
                ("migration_id",),
                "CASCADE",
            ),
        },
        "legacy_message_blob_snapshots": {
            (("session_id",), "sessions", ("id",), "CASCADE"),
            (("incarnation_id",), "session_incarnations", ("id",), "RESTRICT"),
        },
        "legacy_message_blob_snapshot_rows": {
            (
                ("migration_id",),
                "legacy_message_blob_snapshots",
                ("migration_id",),
                "CASCADE",
            ),
            (
                ("body_id",),
                "message_body_manifests",
                ("body_id",),
                "CASCADE",
            ),
        },
        "legacy_message_blob_snapshot_pages": {
            (
                ("migration_id",),
                "legacy_message_blob_snapshots",
                ("migration_id",),
                "CASCADE",
            ),
        },
    }
    optional_text_bound_check_names = {
        "legacy_message_blob_snapshots": {
            "ck_legacy_message_blob_snapshot_session_id_bound",
            "ck_legacy_message_blob_snapshot_high_water_created_at_bound",
            "ck_legacy_message_blob_snapshot_high_water_message_id_bound",
            "ck_legacy_message_blob_snapshot_capture_message_id_bound",
        },
        "legacy_message_blob_snapshot_rows": {
            "ck_legacy_message_blob_snapshot_row_message_id_bound",
            "ck_legacy_message_blob_snapshot_row_role_bound",
            "ck_legacy_message_blob_snapshot_row_created_at_bound",
            "ck_legacy_message_blob_snapshot_row_metadata_bound",
            "ck_legacy_message_blob_snapshot_row_payload_bound",
        },
        "legacy_message_blob_snapshot_pages": {
            "ck_legacy_message_blob_snapshot_page_request_message_id_bound",
            "ck_legacy_message_blob_snapshot_page_response_message_id_bound",
        },
    }
    expected_tables = {
        table_name: Base.metadata.tables.get(table_name)
        for table_name in expected_primary_keys
    }
    if any(table is None for table in expected_tables.values()):
        raise RuntimeError(
            "legacy message snapshot models are not registered in SQLAlchemy metadata; "
            "startup aborted without a schema repair"
        )

    def normalize_sql_text(value):
        return " ".join(str(value).split())

    issues = []
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            existing_tables = set(inspector.get_table_names())

            for table_name, model_table in expected_tables.items():
                if table_name not in existing_tables:
                    issues.append(f"{table_name} is missing")
                    continue

                model_columns = {column.name: column for column in model_table.columns}
                reflected_columns = {
                    column["name"]: column
                    for column in inspector.get_columns(table_name)
                }
                missing_columns = sorted(set(model_columns) - set(reflected_columns))
                extra_columns = sorted(set(reflected_columns) - set(model_columns))
                if missing_columns or extra_columns:
                    issues.append(
                        f"{table_name} column set mismatch "
                        f"(missing={missing_columns}, extra={extra_columns})"
                    )

                for column_name in sorted(set(model_columns) & set(reflected_columns)):
                    model_column = model_columns[column_name]
                    reflected_column = reflected_columns[column_name]
                    expected_type = " ".join(str(model_column.type).split()).upper()
                    actual_type = " ".join(
                        str(reflected_column["type"]).split()
                    ).upper()
                    if actual_type != expected_type:
                        issues.append(
                            f"{table_name}.{column_name} has type {actual_type!r}; "
                            f"expected {expected_type!r}"
                        )
                    if reflected_column.get("nullable") != model_column.nullable:
                        expected_nullability = (
                            "NULLABLE" if model_column.nullable else "NOT NULL"
                        )
                        actual_nullability = (
                            "NULLABLE"
                            if reflected_column.get("nullable")
                            else "NOT NULL"
                        )
                        issues.append(
                            f"{table_name}.{column_name} is {actual_nullability}; "
                            f"expected {expected_nullability}"
                        )

                primary_key = inspector.get_pk_constraint(table_name)
                actual_primary_key = tuple(
                    primary_key.get("constrained_columns") or ()
                )
                expected_primary_key = expected_primary_keys[table_name]
                if actual_primary_key != expected_primary_key:
                    issues.append(
                        f"{table_name} primary key is {actual_primary_key!r}; "
                        f"expected {expected_primary_key!r}"
                    )

                actual_foreign_keys = set()
                for foreign_key in inspector.get_foreign_keys(table_name):
                    options = foreign_key.get("options") or {}
                    ondelete = options.get("ondelete")
                    actual_foreign_keys.add(
                        (
                            tuple(foreign_key.get("constrained_columns") or ()),
                            foreign_key.get("referred_table"),
                            tuple(foreign_key.get("referred_columns") or ()),
                            str(ondelete).strip().upper() if ondelete else None,
                        )
                    )
                if actual_foreign_keys != expected_foreign_keys[table_name]:
                    issues.append(
                        f"{table_name} foreign keys are "
                        f"{sorted(actual_foreign_keys, key=repr)!r}; "
                        f"expected "
                        f"{sorted(expected_foreign_keys[table_name], key=repr)!r}"
                    )

                expected_checks = {
                    (constraint.name, normalize_sql_text(constraint.sqltext))
                    for constraint in model_table.constraints
                    if isinstance(constraint, CheckConstraint)
                }
                optional_names = optional_text_bound_check_names.get(table_name, set())
                legacy_expected_checks = {
                    check for check in expected_checks if check[0] not in optional_names
                }
                actual_checks = {
                    (constraint.get("name"), normalize_sql_text(constraint.get("sqltext", "")))
                    for constraint in inspector.get_check_constraints(table_name)
                }
                if actual_checks not in (expected_checks, legacy_expected_checks):
                    issues.append(
                        f"{table_name} CHECK constraints do not match the model "
                        f"(reflected={sorted(actual_checks, key=repr)!r}, "
                        f"expected={sorted(expected_checks, key=repr)!r})"
                    )

                expected_unique = {
                    tuple(column.name for column in constraint.columns)
                    for constraint in model_table.constraints
                    if isinstance(constraint, UniqueConstraint)
                }
                expected_unique.update(
                    (column.name,) for column in model_table.columns if column.unique
                )
                actual_unique = {
                    tuple(constraint.get("column_names") or ())
                    for constraint in inspector.get_unique_constraints(table_name)
                }
                for index in inspector.get_indexes(table_name):
                    if not index.get("unique"):
                        continue
                    dialect_options = index.get("dialect_options") or {}
                    if dialect_options.get("sqlite_where") is not None:
                        actual_unique.add(
                            ("<partial>",) + tuple(index.get("column_names") or ())
                        )
                        continue
                    index_columns = tuple(index.get("column_names") or ())
                    if index_columns != actual_primary_key:
                        actual_unique.add(index_columns)
                if actual_unique != expected_unique:
                    issues.append(
                        f"{table_name} UNIQUE constraints do not match the model "
                        f"(reflected={sorted(actual_unique, key=repr)!r}, "
                        f"expected={sorted(expected_unique, key=repr)!r})"
                    )
    except Exception as exc:
        raise RuntimeError(
            "legacy message snapshot schema guard could not inspect SQLite schema; "
            f"startup aborted without a schema repair: {exc}"
        ) from exc

    if issues:
        raise RuntimeError(
            "legacy message snapshot schema guard failed; startup aborted without "
            "a schema repair or data migration: "
            + "; ".join(issues)
        )


def _ensure_legacy_message_blob_snapshot_storage_schema():
    """Install append-only guards for bounded legacy Message body snapshots."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError(
            "legacy Message blob snapshots require SQLite; startup aborted"
        )

    def normalize_trigger_sql(value):
        return re.sub(r"\s+", "", str(value)).casefold().replace(";", "").replace(
            "ifnotexists", ""
        )

    triggers = (
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshots_text_bounds_insert "
        "BEFORE INSERT ON legacy_message_blob_snapshots WHEN "
        "typeof(NEW.session_id) != 'text' OR length(NEW.session_id) NOT BETWEEN 1 AND 128 "
        "OR instr(NEW.session_id, char(0)) != 0 "
        "OR typeof(NEW.snapshot_id) != 'text' OR length(NEW.snapshot_id) != 32 "
        "OR instr(NEW.snapshot_id, char(0)) != 0 "
        "OR typeof(NEW.owner_principal_hash) != 'text' "
        "OR length(NEW.owner_principal_hash) != 64 "
        "OR instr(NEW.owner_principal_hash, char(0)) != 0 "
        "OR typeof(NEW.incarnation_id) != 'text' OR length(NEW.incarnation_id) != 32 "
        "OR instr(NEW.incarnation_id, char(0)) != 0 "
        "OR typeof(NEW.delete_epoch) != 'text' OR length(NEW.delete_epoch) NOT IN (0, 32) "
        "OR instr(NEW.delete_epoch, char(0)) != 0 "
        "OR typeof(NEW.ordering_version) != 'text' OR length(NEW.ordering_version) > 64 "
        "OR instr(NEW.ordering_version, char(0)) != 0 "
        "OR (NEW.high_water_created_at IS NOT NULL AND ("
        "typeof(NEW.high_water_created_at) != 'text' OR length(NEW.high_water_created_at) > 8192 "
        "OR instr(NEW.high_water_created_at, char(0)) != 0)) "
        "OR (NEW.high_water_message_id IS NOT NULL AND ("
        "typeof(NEW.high_water_message_id) != 'text' OR length(NEW.high_water_message_id) NOT BETWEEN 1 AND 512 "
        "OR instr(NEW.high_water_message_id, char(0)) != 0)) "
        "OR (NEW.capture_message_id IS NOT NULL AND ("
        "typeof(NEW.capture_message_id) != 'text' OR length(NEW.capture_message_id) NOT BETWEEN 1 AND 512 "
        "OR instr(NEW.capture_message_id, char(0)) != 0)) "
        "OR (NEW.capture_body_id IS NOT NULL AND (typeof(NEW.capture_body_id) != 'text' "
        "OR length(NEW.capture_body_id) != 32 OR instr(NEW.capture_body_id, char(0)) != 0)) "
        "OR typeof(NEW.capture_body_chain_sha256) != 'text' "
        "OR length(NEW.capture_body_chain_sha256) != 64 "
        "OR instr(NEW.capture_body_chain_sha256, char(0)) != 0 "
        "OR typeof(NEW.capture_chain_sha256) != 'text' "
        "OR length(NEW.capture_chain_sha256) != 64 "
        "OR instr(NEW.capture_chain_sha256, char(0)) != 0 "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot text exceeds its bound'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshots_text_bounds_update "
        "BEFORE UPDATE ON legacy_message_blob_snapshots WHEN "
        "typeof(NEW.session_id) != 'text' OR length(NEW.session_id) NOT BETWEEN 1 AND 128 "
        "OR instr(NEW.session_id, char(0)) != 0 "
        "OR typeof(NEW.snapshot_id) != 'text' OR length(NEW.snapshot_id) != 32 "
        "OR instr(NEW.snapshot_id, char(0)) != 0 "
        "OR typeof(NEW.owner_principal_hash) != 'text' "
        "OR length(NEW.owner_principal_hash) != 64 "
        "OR instr(NEW.owner_principal_hash, char(0)) != 0 "
        "OR typeof(NEW.incarnation_id) != 'text' OR length(NEW.incarnation_id) != 32 "
        "OR instr(NEW.incarnation_id, char(0)) != 0 "
        "OR typeof(NEW.delete_epoch) != 'text' OR length(NEW.delete_epoch) NOT IN (0, 32) "
        "OR instr(NEW.delete_epoch, char(0)) != 0 "
        "OR typeof(NEW.ordering_version) != 'text' OR length(NEW.ordering_version) > 64 "
        "OR instr(NEW.ordering_version, char(0)) != 0 "
        "OR (NEW.high_water_created_at IS NOT NULL AND ("
        "typeof(NEW.high_water_created_at) != 'text' OR length(NEW.high_water_created_at) > 8192 "
        "OR instr(NEW.high_water_created_at, char(0)) != 0)) "
        "OR (NEW.high_water_message_id IS NOT NULL AND ("
        "typeof(NEW.high_water_message_id) != 'text' OR length(NEW.high_water_message_id) NOT BETWEEN 1 AND 512 "
        "OR instr(NEW.high_water_message_id, char(0)) != 0)) "
        "OR (NEW.capture_message_id IS NOT NULL AND ("
        "typeof(NEW.capture_message_id) != 'text' OR length(NEW.capture_message_id) NOT BETWEEN 1 AND 512 "
        "OR instr(NEW.capture_message_id, char(0)) != 0)) "
        "OR (NEW.capture_body_id IS NOT NULL AND (typeof(NEW.capture_body_id) != 'text' "
        "OR length(NEW.capture_body_id) != 32 OR instr(NEW.capture_body_id, char(0)) != 0)) "
        "OR typeof(NEW.capture_body_chain_sha256) != 'text' "
        "OR length(NEW.capture_body_chain_sha256) != 64 "
        "OR instr(NEW.capture_body_chain_sha256, char(0)) != 0 "
        "OR typeof(NEW.capture_chain_sha256) != 'text' "
        "OR length(NEW.capture_chain_sha256) != 64 "
        "OR instr(NEW.capture_chain_sha256, char(0)) != 0 "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot text exceeds its bound'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_rows_text_bounds_insert "
        "BEFORE INSERT ON legacy_message_blob_snapshot_rows WHEN "
        "typeof(NEW.legacy_message_id) != 'text' "
        "OR length(NEW.legacy_message_id) NOT BETWEEN 1 AND 512 "
        "OR instr(NEW.legacy_message_id, char(0)) != 0 "
        "OR typeof(NEW.role) != 'text' OR length(NEW.role) > 8192 "
        "OR instr(NEW.role, char(0)) != 0 "
        "OR (NEW.created_at IS NOT NULL AND (typeof(NEW.created_at) != 'text' "
        "OR length(NEW.created_at) > 8192 OR instr(NEW.created_at, char(0)) != 0)) "
        "OR typeof(NEW.metadata_json) != 'text' OR length(NEW.metadata_json) NOT BETWEEN 1 AND 65536 "
        "OR instr(NEW.metadata_json, char(0)) != 0 "
        "OR 4 * (length(NEW.legacy_message_id) + length(NEW.role) "
        "+ COALESCE(length(NEW.created_at), 0) + length(NEW.metadata_json)) + 256 > 262144 "
        "OR typeof(NEW.body_id) != 'text' OR length(NEW.body_id) != 32 "
        "OR instr(NEW.body_id, char(0)) != 0 "
        "OR (NEW.body_sha256 IS NOT NULL AND (typeof(NEW.body_sha256) != 'text' "
        "OR length(NEW.body_sha256) != 64 OR instr(NEW.body_sha256, char(0)) != 0)) "
        "OR (NEW.row_hash IS NOT NULL AND (typeof(NEW.row_hash) != 'text' "
        "OR length(NEW.row_hash) != 64 OR instr(NEW.row_hash, char(0)) != 0)) "
        "OR typeof(NEW.status) != 'text' OR length(NEW.status) > 16 "
        "OR instr(NEW.status, char(0)) != 0 "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot row text exceeds its bound'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_rows_text_bounds_update "
        "BEFORE UPDATE ON legacy_message_blob_snapshot_rows WHEN "
        "typeof(NEW.legacy_message_id) != 'text' "
        "OR length(NEW.legacy_message_id) NOT BETWEEN 1 AND 512 "
        "OR instr(NEW.legacy_message_id, char(0)) != 0 "
        "OR typeof(NEW.role) != 'text' OR length(NEW.role) > 8192 "
        "OR instr(NEW.role, char(0)) != 0 "
        "OR (NEW.created_at IS NOT NULL AND (typeof(NEW.created_at) != 'text' "
        "OR length(NEW.created_at) > 8192 OR instr(NEW.created_at, char(0)) != 0)) "
        "OR typeof(NEW.metadata_json) != 'text' OR length(NEW.metadata_json) NOT BETWEEN 1 AND 65536 "
        "OR instr(NEW.metadata_json, char(0)) != 0 "
        "OR 4 * (length(NEW.legacy_message_id) + length(NEW.role) "
        "+ COALESCE(length(NEW.created_at), 0) + length(NEW.metadata_json)) + 256 > 262144 "
        "OR typeof(NEW.body_id) != 'text' OR length(NEW.body_id) != 32 "
        "OR instr(NEW.body_id, char(0)) != 0 "
        "OR (NEW.body_sha256 IS NOT NULL AND (typeof(NEW.body_sha256) != 'text' "
        "OR length(NEW.body_sha256) != 64 OR instr(NEW.body_sha256, char(0)) != 0)) "
        "OR (NEW.row_hash IS NOT NULL AND (typeof(NEW.row_hash) != 'text' "
        "OR length(NEW.row_hash) != 64 OR instr(NEW.row_hash, char(0)) != 0)) "
        "OR typeof(NEW.status) != 'text' OR length(NEW.status) > 16 "
        "OR instr(NEW.status, char(0)) != 0 "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot row text exceeds its bound'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_pages_text_bounds_insert "
        "BEFORE INSERT ON legacy_message_blob_snapshot_pages WHEN "
        "typeof(NEW.request_message_id) != 'text' OR length(NEW.request_message_id) > 512 "
        "OR instr(NEW.request_message_id, char(0)) != 0 "
        "OR typeof(NEW.response_message_id) != 'text' OR length(NEW.response_message_id) > 512 "
        "OR instr(NEW.response_message_id, char(0)) != 0 "
        "OR typeof(NEW.request_chain_sha256) != 'text' OR length(NEW.request_chain_sha256) != 64 "
        "OR instr(NEW.request_chain_sha256, char(0)) != 0 "
        "OR typeof(NEW.request_body_chain_sha256) != 'text' "
        "OR length(NEW.request_body_chain_sha256) != 64 "
        "OR instr(NEW.request_body_chain_sha256, char(0)) != 0 "
        "OR typeof(NEW.response_chain_sha256) != 'text' OR length(NEW.response_chain_sha256) != 64 "
        "OR instr(NEW.response_chain_sha256, char(0)) != 0 "
        "OR typeof(NEW.response_capture_chain_sha256) != 'text' "
        "OR length(NEW.response_capture_chain_sha256) != 64 "
        "OR instr(NEW.response_capture_chain_sha256, char(0)) != 0 "
        "OR typeof(NEW.status) != 'text' OR length(NEW.status) > 16 "
        "OR instr(NEW.status, char(0)) != 0 "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot page text exceeds its bound'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshots_update_guard "
        "BEFORE UPDATE ON legacy_message_blob_snapshots WHEN OLD.status = 'sealed' "
        "OR NEW.migration_id IS NOT OLD.migration_id "
        "OR NEW.snapshot_id IS NOT OLD.snapshot_id "
        "OR NEW.session_id IS NOT OLD.session_id "
        "OR NEW.owner_principal_hash IS NOT OLD.owner_principal_hash "
        "OR NEW.incarnation_id IS NOT OLD.incarnation_id "
        "OR NEW.delete_epoch IS NOT OLD.delete_epoch "
        "OR NEW.source_generation IS NOT OLD.source_generation "
        "OR NEW.source_message_count IS NOT OLD.source_message_count "
        "OR NEW.source_non_append_generation IS NOT OLD.source_non_append_generation "
        "OR NEW.ordering_version IS NOT OLD.ordering_version "
        "OR NEW.high_water_created_at IS NOT OLD.high_water_created_at "
        "OR NEW.high_water_message_id IS NOT OLD.high_water_message_id "
        "OR NEW.capture_cursor < OLD.capture_cursor "
        "OR NEW.capture_cursor > OLD.capture_cursor + 32 "
        "OR (SELECT COUNT(*) FROM legacy_message_blob_snapshot_rows "
        "WHERE migration_id = NEW.migration_id AND status = 'sealed') != NEW.capture_cursor "
        "OR NOT ((NEW.capture_message_id IS NULL AND NEW.capture_body_id IS NULL "
        "AND NEW.capture_body_offset = 0 AND NEW.capture_body_chain_sha256 = "
        "'0000000000000000000000000000000000000000000000000000000000000000') "
        "OR EXISTS (SELECT 1 FROM legacy_message_blob_snapshot_rows AS r "
        "JOIN message_body_manifests AS b ON b.body_id = r.body_id "
        "WHERE r.migration_id = NEW.migration_id AND r.sequence = NEW.capture_cursor "
        "AND r.legacy_message_id = NEW.capture_message_id "
        "AND r.body_id = NEW.capture_body_id AND r.status = 'capturing' "
        "AND b.session_id = NEW.session_id AND b.incarnation_id = NEW.incarnation_id "
        "AND b.owner_principal_hash = NEW.owner_principal_hash AND b.status = 'building' "
        "AND b.bytes_appended = NEW.capture_body_offset "
        "AND b.chain_sha256 = NEW.capture_body_chain_sha256)) "
        "OR (NEW.status = 'sealed' AND (NEW.capture_cursor != NEW.source_message_count "
        "OR NEW.capture_message_id IS NOT NULL)) "
        "OR (NEW.status = 'building' AND NEW.capture_cursor = NEW.source_message_count) "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot checkpoint is immutable or invalid'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshots_no_delete "
        "BEFORE DELETE ON legacy_message_blob_snapshots WHEN EXISTS ("
        "SELECT 1 FROM sessions WHERE id = OLD.session_id) BEGIN "
        "SELECT RAISE(ABORT, 'legacy Message snapshots are retained'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_rows_insert_guard "
        "BEFORE INSERT ON legacy_message_blob_snapshot_rows BEGIN "
        "SELECT RAISE(ABORT, 'legacy Message snapshot row is outside its source fence') "
        "WHERE NOT EXISTS (SELECT 1 FROM legacy_message_blob_snapshots AS g "
        "JOIN message_body_manifests AS b ON b.body_id = NEW.body_id "
        "JOIN messages AS m ON m.rowid = NEW.source_rowid "
        "WHERE g.migration_id = NEW.migration_id AND g.status = 'building' "
        "AND NEW.sequence >= g.capture_cursor AND NEW.sequence < g.capture_cursor + 32 "
        "AND b.session_id = g.session_id AND b.incarnation_id = g.incarnation_id "
        "AND b.owner_principal_hash = g.owner_principal_hash AND b.status = 'building' "
        "AND m.session_id = g.session_id AND m.id = NEW.legacy_message_id "
        "AND typeof(m.content) = 'text'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_rows_update_guard "
        "BEFORE UPDATE ON legacy_message_blob_snapshot_rows WHEN OLD.status = 'sealed' "
        "OR NEW.migration_id IS NOT OLD.migration_id "
        "OR NEW.sequence IS NOT OLD.sequence "
        "OR NEW.legacy_message_id IS NOT OLD.legacy_message_id "
        "OR NEW.source_rowid IS NOT OLD.source_rowid "
        "OR NEW.body_id IS NOT OLD.body_id OR NEW.role IS NOT OLD.role "
        "OR NEW.created_at IS NOT OLD.created_at "
        "OR NEW.metadata_json IS NOT OLD.metadata_json "
        "OR NEW.body_byte_length IS NOT OLD.body_byte_length "
        "OR NOT (OLD.status = 'capturing' AND NEW.status = 'sealed' "
        "AND length(NEW.body_sha256) = 64 AND length(NEW.row_hash) = 64 "
        "AND EXISTS (SELECT 1 FROM legacy_message_blob_snapshots AS g "
        "JOIN message_body_manifests AS b ON b.body_id = NEW.body_id "
        "WHERE g.migration_id = NEW.migration_id AND g.status = 'building' "
        "AND b.session_id = g.session_id AND b.incarnation_id = g.incarnation_id "
        "AND b.owner_principal_hash = g.owner_principal_hash "
        "AND b.status = 'writer_verified' AND b.byte_length = NEW.body_byte_length "
        "AND b.sha256 = NEW.body_sha256)) "
        "BEGIN SELECT RAISE(ABORT, 'legacy Message snapshot rows are immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_rows_no_delete "
        "BEFORE DELETE ON legacy_message_blob_snapshot_rows WHEN EXISTS ("
        "SELECT 1 FROM sessions AS s JOIN legacy_message_blob_snapshots AS g "
        "ON g.session_id = s.id WHERE g.migration_id = OLD.migration_id) BEGIN "
        "SELECT RAISE(ABORT, 'legacy Message snapshot rows are retained'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_pages_insert_guard "
        "BEFORE INSERT ON legacy_message_blob_snapshot_pages BEGIN "
        "SELECT RAISE(ABORT, 'legacy Message snapshot page receipt is not the current CAS result') "
        "WHERE NOT EXISTS (SELECT 1 FROM legacy_message_blob_snapshots AS g "
        "WHERE g.migration_id = NEW.migration_id "
        "AND g.capture_cursor = NEW.response_cursor "
        "AND COALESCE(g.capture_message_id, '') = NEW.response_message_id "
        "AND g.capture_body_offset = NEW.response_body_offset "
        "AND g.capture_body_chain_sha256 = NEW.response_chain_sha256 "
        "AND g.capture_chain_sha256 = NEW.response_capture_chain_sha256 "
        "AND g.status = NEW.status); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_pages_no_update "
        "BEFORE UPDATE ON legacy_message_blob_snapshot_pages BEGIN "
        "SELECT RAISE(ABORT, 'legacy Message snapshot page receipts are immutable'); END",
        "CREATE TRIGGER IF NOT EXISTS legacy_message_blob_snapshot_pages_no_delete "
        "BEFORE DELETE ON legacy_message_blob_snapshot_pages WHEN EXISTS ("
        "SELECT 1 FROM sessions AS s JOIN legacy_message_blob_snapshots AS g "
        "ON g.session_id = s.id WHERE g.migration_id = OLD.migration_id) BEGIN "
        "SELECT RAISE(ABORT, 'legacy Message snapshot page receipts are retained'); END",
    )
    with engine.begin() as conn:
        existing_text_bound_audits = (
            (
                "legacy_message_blob_snapshots",
                "SELECT COUNT(*) FROM legacy_message_blob_snapshots WHERE "
                "typeof(session_id) != 'text' OR length(session_id) NOT BETWEEN 1 AND 128 OR instr(session_id, char(0)) != 0 "
                "OR typeof(snapshot_id) != 'text' OR length(snapshot_id) != 32 OR instr(snapshot_id, char(0)) != 0 "
                "OR typeof(owner_principal_hash) != 'text' OR length(owner_principal_hash) != 64 "
                "OR instr(owner_principal_hash, char(0)) != 0 "
                "OR typeof(incarnation_id) != 'text' OR length(incarnation_id) != 32 OR instr(incarnation_id, char(0)) != 0 "
                "OR typeof(delete_epoch) != 'text' OR length(delete_epoch) NOT IN (0, 32) OR instr(delete_epoch, char(0)) != 0 "
                "OR typeof(ordering_version) != 'text' OR length(ordering_version) > 64 OR instr(ordering_version, char(0)) != 0 "
                "OR (high_water_created_at IS NOT NULL AND (typeof(high_water_created_at) != 'text' "
                "OR length(high_water_created_at) > 8192 OR instr(high_water_created_at, char(0)) != 0)) "
                "OR (high_water_message_id IS NOT NULL AND (typeof(high_water_message_id) != 'text' "
                "OR length(high_water_message_id) NOT BETWEEN 1 AND 512 OR instr(high_water_message_id, char(0)) != 0)) "
                "OR (capture_message_id IS NOT NULL AND (typeof(capture_message_id) != 'text' "
                "OR length(capture_message_id) NOT BETWEEN 1 AND 512 OR instr(capture_message_id, char(0)) != 0)) "
                "OR (capture_body_id IS NOT NULL AND (typeof(capture_body_id) != 'text' "
                "OR length(capture_body_id) != 32 OR instr(capture_body_id, char(0)) != 0)) "
                "OR typeof(capture_body_chain_sha256) != 'text' OR length(capture_body_chain_sha256) != 64 "
                "OR instr(capture_body_chain_sha256, char(0)) != 0 "
                "OR typeof(capture_chain_sha256) != 'text' OR length(capture_chain_sha256) != 64 "
                "OR instr(capture_chain_sha256, char(0)) != 0 "
                "OR typeof(status) != 'text' OR length(status) NOT BETWEEN 1 AND 16 OR instr(status, char(0)) != 0",
            ),
            (
                "legacy_message_blob_snapshot_rows",
                "SELECT COUNT(*) FROM legacy_message_blob_snapshot_rows WHERE "
                "typeof(legacy_message_id) != 'text' OR length(legacy_message_id) NOT BETWEEN 1 AND 512 "
                "OR instr(legacy_message_id, char(0)) != 0 "
                "OR typeof(role) != 'text' OR length(role) > 8192 OR instr(role, char(0)) != 0 "
                "OR (created_at IS NOT NULL AND (typeof(created_at) != 'text' "
                "OR length(created_at) > 8192 OR instr(created_at, char(0)) != 0)) "
                "OR typeof(metadata_json) != 'text' OR length(metadata_json) NOT BETWEEN 1 AND 65536 "
                "OR instr(metadata_json, char(0)) != 0 "
                "OR 4 * (length(legacy_message_id) + length(role) "
                "+ COALESCE(length(created_at), 0) + length(metadata_json)) + 256 > 262144 "
                "OR typeof(body_id) != 'text' OR length(body_id) != 32 OR instr(body_id, char(0)) != 0 "
                "OR (body_sha256 IS NOT NULL AND (typeof(body_sha256) != 'text' "
                "OR length(body_sha256) != 64 OR instr(body_sha256, char(0)) != 0)) "
                "OR (row_hash IS NOT NULL AND (typeof(row_hash) != 'text' "
                "OR length(row_hash) != 64 OR instr(row_hash, char(0)) != 0)) "
                "OR typeof(status) != 'text' OR length(status) NOT BETWEEN 1 AND 16 OR instr(status, char(0)) != 0",
            ),
            (
                "legacy_message_blob_snapshot_pages",
                "SELECT COUNT(*) FROM legacy_message_blob_snapshot_pages WHERE "
                "typeof(request_message_id) != 'text' OR length(request_message_id) > 512 "
                "OR instr(request_message_id, char(0)) != 0 "
                "OR typeof(response_message_id) != 'text' OR length(response_message_id) > 512 "
                "OR instr(response_message_id, char(0)) != 0 "
                "OR typeof(request_chain_sha256) != 'text' OR length(request_chain_sha256) != 64 "
                "OR instr(request_chain_sha256, char(0)) != 0 "
                "OR typeof(request_body_chain_sha256) != 'text' OR length(request_body_chain_sha256) != 64 "
                "OR instr(request_body_chain_sha256, char(0)) != 0 "
                "OR typeof(response_chain_sha256) != 'text' OR length(response_chain_sha256) != 64 "
                "OR instr(response_chain_sha256, char(0)) != 0 "
                "OR typeof(response_capture_chain_sha256) != 'text' "
                "OR length(response_capture_chain_sha256) != 64 "
                "OR instr(response_capture_chain_sha256, char(0)) != 0 "
                "OR typeof(status) != 'text' OR length(status) NOT BETWEEN 1 AND 16 OR instr(status, char(0)) != 0",
            ),
        )
        for table_name, statement in existing_text_bound_audits:
            invalid_rows = conn.exec_driver_sql(statement).scalar_one()
            if invalid_rows:
                raise RuntimeError(
                    f"{table_name} contains text outside its safe read bounds; "
                    "startup aborted without modifying snapshot data"
                )

        for statement in triggers:
            conn.exec_driver_sql(statement)

        required_text_bound_trigger_names = (
            "legacy_message_blob_snapshots_text_bounds_insert",
            "legacy_message_blob_snapshots_text_bounds_update",
            "legacy_message_blob_snapshot_rows_text_bounds_insert",
            "legacy_message_blob_snapshot_rows_text_bounds_update",
            "legacy_message_blob_snapshot_pages_text_bounds_insert",
        )
        for trigger_name in required_text_bound_trigger_names:
            expected_sql = next(
                statement for statement in triggers if f"TRIGGER IF NOT EXISTS {trigger_name} " in statement
            )
            trigger_rows = conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).all()
            if (
                len(trigger_rows) != 1
                or normalize_trigger_sql(trigger_rows[0][0])
                != normalize_trigger_sql(expected_sql)
            ):
                raise RuntimeError(
                    f"SQLite trigger {trigger_name} has an incompatible text bound"
                )


def init_db():
    """建表（首次启动时使用，后续通过 Alembic 迁移）。"""
    import app.db.models  # noqa: ensure models are loaded
    Base.metadata.create_all(bind=engine)
    _ensure_legacy_body_chunk_storage_schema()
    _ensure_legacy_message_snapshot_schema()
    _ensure_legacy_message_blob_snapshot_storage_schema()
    _ensure_session_message_revision_schema()
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

        # 为 tool_audit 添加 call_id/success/error_type/trace 列（幂等）
        for col_def in (
            "call_id VARCHAR(64)",
            "success BOOLEAN DEFAULT 1",
            "error_type VARCHAR(64)",
            "trace_id VARCHAR(64)",
            "result_hash VARCHAR(64)",
        ):
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE tool_audit ADD COLUMN {col_def}"
                    )
                )
            except Exception as e:
                _LOGGER.debug(f"DB migration skipped: {e}")

        # 为 token_usage 添加 trace/成本/延迟列（幂等）
        for col_def in (
            "trace_id VARCHAR(64)",
            "run_id VARCHAR(64)",
            "call_id VARCHAR(64)",
            "cached_tokens INTEGER DEFAULT 0",
            "cost_usd FLOAT DEFAULT 0",
            "latency_ms INTEGER",
            "status VARCHAR(32) DEFAULT 'ok'",
            "source VARCHAR(32) DEFAULT ''",
        ):
            try:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE token_usage ADD COLUMN {col_def}"
                    )
                )
            except Exception as e:
                _LOGGER.debug(f"DB migration skipped: {e}")

        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_token_usage_trace ON token_usage(trace_id)",
            "CREATE INDEX IF NOT EXISTS idx_tool_audit_trace ON tool_audit(trace_id)",
        ):
            try:
                conn.execute(__import__("sqlalchemy").text(index_sql))
            except Exception as e:
                _LOGGER.debug(f"DB index skipped: {e}")

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

        from app.db.schema_w3 import apply_w3_schema

        apply_w3_schema(conn)
        conn.commit()

    _ensure_message_source_generation_schema()
