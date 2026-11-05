"""Host-only owner binding for rolling-history database operations.

This module is a transaction boundary, not a route, tool, graph writer, or
authorization source for model/client input. Callers must be authenticated host
routes and must pass the user object returned by their ``current_user``
dependency while the selected archive scope is bound.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import hashlib
import json
import logging
import re
import sqlite3

logger = logging.getLogger(__name__)
from collections.abc import Callable, Iterator, Mapping
from threading import get_ident
from typing import Protocol, TypeVar
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.api.compaction_archive import get_compaction_scope
from app.api.context_events import ContextEventScope
from app.api.session_epoch import SESSION_DELETE_EPOCH_PREFIX
from app.api.session_owner import principal_digest_for_user
from app.api.tool_events import get_tool_event_scope
from app.db.database import engine


class ScopeOrigin(Enum):
    """Select the trusted host ContextVar used for owner binding."""

    COMPACTION = "compaction"
    TOOL = "tool"
    CHAT_APPEND = "chat_append"
    LEGACY_MESSAGE_MIGRATION = "legacy_message_migration"


class OperationMode(Enum):
    """Describe the SQL operation so writes acquire SQLite's reserved lock."""

    READ = "read"
    WRITE = "write"


class RollingHistoryOwnerScopeError(PermissionError):
    """Sanitized failure for missing or stale host ownership state."""


@dataclass(frozen=True)
class MaterializedRows:
    """Rows copied out of a query result before the operation can commit."""

    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


class RollingHistorySqlHandle(Protocol):
    """Restricted synchronous SQL surface exposed to trusted host callbacks."""

    def execute(self, statement: str, parameters: object = ()) -> int | MaterializedRows:
        """Execute one business statement and return only materialized data."""

    def read_message_content_chunk(
        self,
        session_id: str,
        message_id: str | int,
        rowid: int,
        byte_offset: int,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        """Read a bounded raw UTF-8 chunk from one bound legacy Message."""

    def read_message_text_chunk(
        self,
        session_id: str,
        message_id: str | int,
        rowid: int,
        column_name: str,
        byte_offset: int,
        max_bytes: int,
    ) -> tuple[int | None, bytes]:
        """Read <=8192 raw UTF-8 bytes from sources/tool_calls/activity.

        SQL NULL returns ``(None, b"")`` only for the size-only request at
        offset zero; content reads and other NULL requests fail closed.
        """


@dataclass(frozen=True)
class _OwnerIdentity:
    origin: ScopeOrigin
    scope: ContextEventScope
    scope_fields: tuple[str, str, str, str]
    session_id: str
    principal_hash: str
    request_id: str
    delete_epoch: str
    incarnation_id: str
    canonical_namespace_digest: str


@dataclass(frozen=True)
class RollingHistoryOwnerIdentity:
    """Immutable, key-free view of the current host-bound owner identity."""

    session_id: str
    principal_hash: str
    delete_epoch: str
    incarnation_id: str
    request_id: str
    canonical_namespace_digest: str


@dataclass(frozen=True)
class _TransactionState:
    connection: Connection
    transaction: object
    driver_connection: sqlite3.Connection


class _SqlOperationHandle:
    """Expose checked SQL and restricted legacy Message body/metadata readers."""

    __slots__ = (
        "__execute",
        "__read_message_content_chunk",
        "__read_message_text_chunk",
    )

    def __init__(
        self,
        execute,
        read_message_content_chunk,
        read_message_text_chunk,
    ):
        self.__execute = execute
        self.__read_message_content_chunk = read_message_content_chunk
        self.__read_message_text_chunk = read_message_text_chunk

    def execute(self, statement: str, parameters: object = ()) -> int | MaterializedRows:
        return self.__execute(statement, parameters)

    def read_message_content_chunk(
        self,
        session_id: str,
        message_id: str | int,
        rowid: int,
        byte_offset: int,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        return self.__read_message_content_chunk(
            session_id, message_id, rowid, byte_offset, max_bytes,
        )

    def read_message_text_chunk(
        self,
        session_id: str,
        message_id: str | int,
        rowid: int,
        column_name: str,
        byte_offset: int,
        max_bytes: int,
    ) -> tuple[int | None, bytes]:
        return self.__read_message_text_chunk(
            session_id,
            message_id,
            rowid,
            column_name,
            byte_offset,
            max_bytes,
        )


class _ActiveSqlOperationLifecycle:
    """Hold the mutable lifetime flag behind a private, controlled interface."""

    __slots__ = ("__is_active",)

    def __init__(self) -> None:
        self.__is_active = True

    @property
    def is_active(self) -> bool:
        return self.__is_active

    def deactivate(self) -> None:
        self.__is_active = False


@dataclass(frozen=True)
class _ActiveSqlOperation:
    handle: _SqlOperationHandle
    transaction: _TransactionState
    mode: OperationMode
    owner_identity: _OwnerIdentity
    user: dict | None
    thread_id: int
    __lifecycle: _ActiveSqlOperationLifecycle = field(
        default_factory=_ActiveSqlOperationLifecycle,
        repr=False,
        compare=False,
    )

    @property
    def is_active(self) -> bool:
        return self.__lifecycle.is_active

    def _deactivate(self) -> None:
        self.__lifecycle.deactivate()


_OWNER_IDENTITY: ContextVar[_OwnerIdentity | None] = ContextVar(
    "rolling_history_owner_identity", default=None,
)
_CHAT_APPEND_SCOPE: ContextVar[ContextEventScope | None] = ContextVar(
    "rolling_history_chat_append_scope", default=None,
)
_LEGACY_MESSAGE_MIGRATION_SCOPE: ContextVar[ContextEventScope | None] = ContextVar(
    "rolling_history_legacy_message_migration_scope", default=None,
)
_ACTIVE_SQL_OPERATION: ContextVar[_ActiveSqlOperation | None] = ContextVar(
    "rolling_history_active_sql_operation", default=None,
)
_SESSION_ID = re.compile(r".{1,128}\Z", re.DOTALL)
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_UNAVAILABLE = "Rolling history owner scope is unavailable"
_NAMESPACE_SCHEMA = "rolling-history-owner-namespace-v1"
_MEMORY_CLEAR_EPOCH_KEY = "_longTermMemoryClearEpoch"
_NO_MEMORY_CLEAR_EPOCH_CHECK = object()
_LEGACY_MESSAGE_TEXT_FIELDS = frozenset({"sources", "tool_calls", "activity"})
_T = TypeVar("_T")
_BUSINESS_SQL = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE"})
_DENIED_SQLITE_ACTIONS = frozenset({
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
})


def _current_scope(origin: ScopeOrigin) -> ContextEventScope:
    if type(origin) is not ScopeOrigin:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    if origin is ScopeOrigin.COMPACTION:
        scope = get_compaction_scope()
    elif origin is ScopeOrigin.TOOL:
        scope = get_tool_event_scope()
    elif origin is ScopeOrigin.CHAT_APPEND:
        scope = _CHAT_APPEND_SCOPE.get()
    elif origin is ScopeOrigin.LEGACY_MESSAGE_MIGRATION:
        scope = _LEGACY_MESSAGE_MIGRATION_SCOPE.get()
    else:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    if type(scope) is not ContextEventScope:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    return scope


def _scope_fields(scope: ContextEventScope) -> tuple[str, str, str, str]:
    fields = (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
    session_id, request_id, principal_hash, delete_epoch = fields
    if (
        type(session_id) is not str
        or _SESSION_ID.fullmatch(session_id) is None
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
        or type(request_id) is not str
        or _REQUEST_ID.fullmatch(request_id) is None
        or type(principal_hash) is not str
        or _HASH.fullmatch(principal_hash) is None
        or type(delete_epoch) is not str
        or (delete_epoch != "" and _GENERATION.fullmatch(delete_epoch) is None)
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    return fields


def _host_principal(user: dict | None) -> str:
    try:
        principal_hash = principal_digest_for_user(user)
    except Exception:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
    if type(principal_hash) is not str or _HASH.fullmatch(principal_hash) is None:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    return principal_hash


def _canonical_namespace_digest(
    session_id: str,
    principal_hash: str,
    delete_epoch: str,
    incarnation_id: str,
) -> str:
    identity = {
        "delete_epoch": delete_epoch,
        "incarnation_id": incarnation_id,
        "namespace_schema": _NAMESPACE_SCHEMA,
        "principal_hash": principal_hash,
        "session_id": session_id,
    }
    encoded = json.dumps(
        identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@contextmanager
def _sqlite_transaction(begin_sql: str) -> Iterator[_TransactionState]:
    connection = engine.connect()
    state = None
    try:
        # This is the first SQL in the module-owned transaction. The engine's
        # connect hook may have initialized a new DBAPI connection beforehand.
        connection.exec_driver_sql(begin_sql)
        transaction = connection.get_transaction()
        try:
            driver_connection = connection.connection.driver_connection
        except Exception:
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
        if transaction is None or not isinstance(driver_connection, sqlite3.Connection):
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
        state = _TransactionState(connection, transaction, driver_connection)
        _assert_transaction_active(state)
        yield state
        _assert_transaction_active(state)
        connection.commit()
    except BaseException:
        try:
            if not connection.closed and (
                connection.in_transaction()
                or (state is not None and state.driver_connection.in_transaction)
            ):
                connection.rollback()
        except Exception:
            try:
                connection.invalidate()
            except Exception:
                pass
        raise
    finally:
        connection.close()


def _assert_transaction_active(state: _TransactionState) -> None:
    """Require the same live SQLAlchemy and SQLite transaction throughout."""
    connection = state.connection
    try:
        current_transaction = connection.get_transaction()
        current_driver = connection.connection.driver_connection
        driver_active = state.driver_connection.in_transaction
    except Exception:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
    if (
        connection.closed
        or current_transaction is not state.transaction
        or getattr(state.transaction, "is_active", None) is not True
        or not connection.in_transaction()
        or current_driver is not state.driver_connection
        or driver_active is not True
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)


def _validate_database_binding(
    connection: Connection,
    session_id: str,
    principal_hash: str,
    delete_epoch: str,
    expected_incarnation_id: str | None = None,
) -> str:
    session_row = connection.execute(
        text("SELECT 1 FROM sessions WHERE id = :session_id"),
        {"session_id": session_id},
    ).first()
    if session_row is None:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    owner_row = connection.execute(
        text(
            "SELECT owner_principal_hash, incarnation_id "
            "FROM session_owner_bindings WHERE session_id = :session_id"
        ),
        {"session_id": session_id},
    ).first()
    if (
        owner_row is None
        or type(owner_row.owner_principal_hash) is not str
        or owner_row.owner_principal_hash != principal_hash
        or type(owner_row.incarnation_id) is not str
        or _GENERATION.fullmatch(owner_row.incarnation_id) is None
        or (
            expected_incarnation_id is not None
            and owner_row.incarnation_id != expected_incarnation_id
        )
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    incarnation_row = connection.execute(
        text(
            "SELECT retired_at FROM session_incarnations WHERE id = :incarnation_id"
        ),
        {"incarnation_id": owner_row.incarnation_id},
    ).first()
    if incarnation_row is None or incarnation_row.retired_at is not None:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    epoch_key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(
        session_id.encode("utf-8"),
    ).hexdigest()
    epoch_row = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"),
        {"key": epoch_key},
    ).first()
    database_epoch = "" if epoch_row is None else epoch_row.value
    if (
        type(database_epoch) is not str
        or (database_epoch != "" and _GENERATION.fullmatch(database_epoch) is None)
        or database_epoch != delete_epoch
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    return owner_row.incarnation_id


def _validate_memory_clear_epoch(value: object) -> str:
    """Accept only a known legacy-unset or lowercase UUID-hex epoch value."""
    if (
        type(value) is not str
        or (value != "" and _GENERATION.fullmatch(value) is None)
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    return value


def _read_memory_clear_epoch(connection: Connection) -> str:
    """Read the manual-memory clear marker through the active owner connection."""
    row = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"),
        {"key": _MEMORY_CLEAR_EPOCH_KEY},
    ).first()
    # A missing row is the existing legacy-unset state. A stored NULL or any
    # malformed value is unknown and must not be treated as that state.
    value = "" if row is None else row.value
    return _validate_memory_clear_epoch(value)


def _require_memory_clear_epoch(
    connection: Connection,
    expected_memory_clear_epoch: str,
) -> None:
    if _read_memory_clear_epoch(connection) != expected_memory_clear_epoch:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)


def _validate_business_statement(statement: str, mode: OperationMode) -> None:
    if type(statement) is not str or not statement or len(statement) > 65536 or "\x00" in statement:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    match = re.match(r"\s*([A-Za-z]+)(?=\s|\(|$)", statement)
    keyword = "" if match is None else match.group(1).upper()
    if (
        not keyword
        or keyword not in _BUSINESS_SQL
        or (mode is OperationMode.READ and keyword != "SELECT")
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)


def _sqlite_authorizer(
    mode: OperationMode,
    *,
    allow_database_encoding_read: bool = False,
):
    denied_actions = set(_DENIED_SQLITE_ACTIONS)
    if mode is OperationMode.READ:
        denied_actions.update((sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE))

    def authorize(action, first_argument, second_argument, database_name, trigger_name):
        if action in denied_actions:
            if (
                action == sqlite3.SQLITE_PRAGMA
                and allow_database_encoding_read
                and type(first_argument) is str
                and first_argument.lower() == "encoding"
                and second_argument is None
            ):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (second_argument or first_argument or "").lower()
            if function_name == "load_extension":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize


@contextmanager
def _restricted_sql_handle(
    state: _TransactionState,
    mode: OperationMode,
    user: dict | None,
) -> Iterator[RollingHistorySqlHandle]:
    owner_identity = _OWNER_IDENTITY.get()
    if (
        type(state) is not _TransactionState
        or type(mode) is not OperationMode
        or type(owner_identity) is not _OwnerIdentity
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    _assert_transaction_active(state)

    def execute(statement: str, parameters: object = ()) -> int | MaterializedRows:
        if (
            type(active_operation) is not _ActiveSqlOperation
            or active_operation.handle is not handle
            or active_operation.transaction is not state
            or active_operation.mode is not mode
            or active_operation.owner_identity is not _OWNER_IDENTITY.get()
            or active_operation.thread_id != get_ident()
            or active_operation.is_active is not True
        ):
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
        _assert_transaction_active(state)
        _validate_business_statement(statement, mode)
        result = state.connection.exec_driver_sql(statement, parameters)
        try:
            _assert_transaction_active(state)
            if result.returns_rows:
                columns = tuple(str(column) for column in result.keys())
                _assert_transaction_active(state)
                rows = tuple(tuple(row) for row in result.fetchall())
                _assert_transaction_active(state)
                materialized = MaterializedRows(columns=columns, rows=rows)
            else:
                rowcount = result.rowcount
                materialized = 0 if rowcount is None else int(rowcount)
        finally:
            result.close()
        _assert_transaction_active(state)
        return materialized

    def read_message_content_chunk(
        session_id: str,
        message_id: str | int,
        rowid: int,
        byte_offset: int,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        if (
            type(session_id) is not str
            or _SESSION_ID.fullmatch(session_id) is None
            or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
            or session_id != owner_identity.session_id
            or not (
                (
                    type(message_id) is str
                    and _SESSION_ID.fullmatch(message_id) is not None
                    and not any(ord(char) < 32 or ord(char) == 127 for char in message_id)
                )
                or (
                    type(message_id) is int
                    and -(1 << 63) <= message_id <= (1 << 63) - 1
                )
            )
            or type(rowid) is not int
            or not (-(1 << 63) <= rowid <= (1 << 63) - 1)
            or type(byte_offset) is not int
            or not (0 <= byte_offset <= (1 << 63) - 1)
            or type(max_bytes) is not int
            or not (0 <= max_bytes <= 8192)
        ):
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

        blob = None
        try:
            identity_view = _require_active_rolling_history_sql_handle(
                handle, OperationMode.WRITE,
            )
            if (
                owner_identity.origin is not ScopeOrigin.LEGACY_MESSAGE_MIGRATION
                or identity_view.session_id != session_id
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            _assert_transaction_active(state)

            metadata_cursor = state.driver_connection.execute(
                "SELECT id, session_id, typeof(content) "
                "FROM messages WHERE rowid = ?",
                (rowid,),
            )
            try:
                row = metadata_cursor.fetchone()
            finally:
                metadata_cursor.close()
            _assert_transaction_active(state)
            if (
                row is None
                or type(row[0]) is not type(message_id)
                or row[0] != message_id
                or type(row[1]) is not str
                or row[1] != session_id
                or type(row[2]) is not str
                or row[2] != "text"
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

            encoding_cursor = state.driver_connection.execute("PRAGMA encoding")
            try:
                encoding_row = encoding_cursor.fetchone()
            finally:
                encoding_cursor.close()
            _assert_transaction_active(state)
            if (
                encoding_row is None
                or type(encoding_row[0]) is not str
                or encoding_row[0].upper() != "UTF-8"
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

            blobopen = getattr(state.driver_connection, "blobopen", None)
            if not callable(blobopen):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            blob = blobopen(
                "messages",
                "content",
                rowid,
                readonly=True,
                name="main",
            )
            total_byte_length = blob.size()
            if type(total_byte_length) is not int or total_byte_length < 0:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if byte_offset > total_byte_length:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if max_bytes == 0 or byte_offset == total_byte_length:
                return total_byte_length, b""

            blob.seek(byte_offset)
            expected_length = min(max_bytes, total_byte_length - byte_offset)
            raw_utf8_bytes = blob.read(expected_length)
            if (
                type(raw_utf8_bytes) is not bytes
                or len(raw_utf8_bytes) != expected_length
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            return total_byte_length, raw_utf8_bytes
        except RollingHistoryOwnerScopeError:
            raise
        except Exception:
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
        finally:
            close_failed = False
            if blob is not None:
                try:
                    blob.close()
                except Exception:
                    close_failed = True
            try:
                identity_view = _require_active_rolling_history_sql_handle(
                    handle, OperationMode.WRITE,
                )
                if (
                    owner_identity.origin is not ScopeOrigin.LEGACY_MESSAGE_MIGRATION
                    or identity_view.session_id != session_id
                ):
                    raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
                _assert_transaction_active(state)
            except Exception:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
            if close_failed:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    def read_message_text_chunk(
        session_id: str,
        message_id: str | int,
        rowid: int,
        column_name: str,
        byte_offset: int,
        max_bytes: int,
    ) -> tuple[int | None, bytes]:
        if (
            type(column_name) is not str
            or column_name not in _LEGACY_MESSAGE_TEXT_FIELDS
        ):
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
        if (
            type(session_id) is not str
            or _SESSION_ID.fullmatch(session_id) is None
            or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
            or session_id != owner_identity.session_id
            or not (
                (
                    type(message_id) is str
                    and _SESSION_ID.fullmatch(message_id) is not None
                    and not any(ord(char) < 32 or ord(char) == 127 for char in message_id)
                )
                or (
                    type(message_id) is int
                    and -(1 << 63) <= message_id <= (1 << 63) - 1
                )
            )
            or type(rowid) is not int
            or not (-(1 << 63) <= rowid <= (1 << 63) - 1)
            or type(byte_offset) is not int
            or not (0 <= byte_offset <= (1 << 63) - 1)
            or type(max_bytes) is not int
            or not (0 <= max_bytes <= 8192)
        ):
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

        blob = None
        try:
            identity_view = _require_active_rolling_history_sql_handle(
                handle, OperationMode.WRITE,
            )
            if (
                owner_identity.origin is not ScopeOrigin.LEGACY_MESSAGE_MIGRATION
                or identity_view.session_id != session_id
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            _assert_transaction_active(state)

            metadata_cursor = state.driver_connection.execute(
                f"SELECT id, session_id, typeof({column_name}) "
                "FROM messages WHERE rowid = ?",
                (rowid,),
            )
            try:
                row = metadata_cursor.fetchone()
            finally:
                metadata_cursor.close()
            _assert_transaction_active(state)
            if (
                row is None
                or type(row[0]) is not type(message_id)
                or row[0] != message_id
                or type(row[1]) is not str
                or row[1] != session_id
                or type(row[2]) is not str
                or row[2] not in {"text", "null"}
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if row[2] == "null":
                if byte_offset != 0 or max_bytes != 0:
                    raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
                return None, b""

            encoding_cursor = state.driver_connection.execute("PRAGMA encoding")
            try:
                encoding_row = encoding_cursor.fetchone()
            finally:
                encoding_cursor.close()
            _assert_transaction_active(state)
            if (
                encoding_row is None
                or type(encoding_row[0]) is not str
                or encoding_row[0].upper() != "UTF-8"
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

            blobopen = getattr(state.driver_connection, "blobopen", None)
            if not callable(blobopen):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            blob = blobopen(
                "messages",
                column_name,
                rowid,
                readonly=True,
                name="main",
            )
            total_byte_length = blob.size()
            if type(total_byte_length) is not int or total_byte_length < 0:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if byte_offset > total_byte_length:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if max_bytes == 0 or byte_offset == total_byte_length:
                return total_byte_length, b""

            blob.seek(byte_offset)
            expected_length = min(max_bytes, total_byte_length - byte_offset)
            raw_utf8_bytes = blob.read(expected_length)
            if (
                type(raw_utf8_bytes) is not bytes
                or len(raw_utf8_bytes) != expected_length
            ):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            return total_byte_length, raw_utf8_bytes
        except RollingHistoryOwnerScopeError:
            raise
        except Exception:
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
        finally:
            close_failed = False
            if blob is not None:
                try:
                    blob.close()
                except Exception:
                    close_failed = True
            try:
                identity_view = _require_active_rolling_history_sql_handle(
                    handle, OperationMode.WRITE,
                )
                if (
                    owner_identity.origin is not ScopeOrigin.LEGACY_MESSAGE_MIGRATION
                    or identity_view.session_id != session_id
                ):
                    raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
                _assert_transaction_active(state)
            except Exception:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
            if close_failed:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    try:
        state.driver_connection.set_authorizer(
            _sqlite_authorizer(
                mode,
                allow_database_encoding_read=(
                    owner_identity.origin is ScopeOrigin.LEGACY_MESSAGE_MIGRATION
                ),
            )
        )
    except Exception:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
    handle = _SqlOperationHandle(
        execute,
        read_message_content_chunk,
        read_message_text_chunk,
    )
    active_operation = _ActiveSqlOperation(
        handle, state, mode, owner_identity, user, get_ident(),
    )
    try:
        active_token = _ACTIVE_SQL_OPERATION.set(active_operation)
        try:
            yield handle
        finally:
            try:
                active_operation._deactivate()
            finally:
                _ACTIVE_SQL_OPERATION.reset(active_token)
    finally:
        try:
            state.driver_connection.set_authorizer(None)
        except Exception:
            try:
                state.connection.invalidate()
            except Exception:
                pass
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None


@contextmanager
def bootstrap_rolling_history_owner_scope(
    user: dict | None,
    origin: ScopeOrigin,
) -> Iterator[None]:
    """Bind verified host identity while an authenticated route does its work.

    ``user`` must come from that route's ``current_user`` dependency. Scope data
    is read only from the selected, already-bound host accessor; callers cannot
    supply a scope object or an authorization boolean.
    """
    if _OWNER_IDENTITY.get() is not None:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    initial_scope = _current_scope(origin)
    initial_fields = _scope_fields(initial_scope)
    session_id, request_id, scope_principal, delete_epoch = initial_fields
    principal_hash = _host_principal(user)
    if scope_principal != principal_hash:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    try:
        with _sqlite_transaction("BEGIN") as transaction:
            _assert_transaction_active(transaction)
            current_scope = _current_scope(origin)
            if current_scope is not initial_scope or _scope_fields(current_scope) != initial_fields:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if _host_principal(user) != principal_hash:
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            incarnation_id = _validate_database_binding(
                transaction.connection, session_id, principal_hash, delete_epoch,
            )
            _assert_transaction_active(transaction)
    except RollingHistoryOwnerScopeError:
        raise
    except Exception:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None

    identity = _OwnerIdentity(
        origin=origin,
        scope=initial_scope,
        scope_fields=initial_fields,
        session_id=session_id,
        principal_hash=principal_hash,
        request_id=request_id,
        delete_epoch=delete_epoch,
        incarnation_id=incarnation_id,
        canonical_namespace_digest=_canonical_namespace_digest(
            session_id, principal_hash, delete_epoch, incarnation_id,
        ),
    )
    token = _OWNER_IDENTITY.set(identity)
    try:
        yield None
    finally:
        _OWNER_IDENTITY.reset(token)


def get_rolling_history_owner_identity() -> RollingHistoryOwnerIdentity:
    """Return the current bootstrap-bound identity for host-side correlation.

    This reads only the private identity captured by
    ``bootstrap_rolling_history_owner_scope`` and rechecks the exact host scope
    binding. It is not a fresh database authorization check. Every subsequent
    write must still use a restricted owner operation. The compatible
    ``run_rolling_history_owner_operation`` checks owner, incarnation, and delete
    epoch before invoking business SQL, but does not validate the manual-memory
    clear epoch. Use the strict sibling operation when that epoch must be pinned.
    """
    identity = _OWNER_IDENTITY.get()
    if (
        type(identity) is not _OwnerIdentity
        or type(identity.origin) is not ScopeOrigin
        or type(identity.scope) is not ContextEventScope
        or type(identity.scope_fields) is not tuple
        or len(identity.scope_fields) != 4
        or any(type(value) is not str for value in identity.scope_fields)
        or type(identity.session_id) is not str
        or type(identity.request_id) is not str
        or type(identity.principal_hash) is not str
        or type(identity.delete_epoch) is not str
        or type(identity.incarnation_id) is not str
        or type(identity.canonical_namespace_digest) is not str
        or _GENERATION.fullmatch(identity.incarnation_id) is None
        or _HASH.fullmatch(identity.canonical_namespace_digest) is None
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    current_scope = _current_scope(identity.origin)
    current_fields = _scope_fields(current_scope)
    identity_fields = (
        identity.session_id,
        identity.request_id,
        identity.principal_hash,
        identity.delete_epoch,
    )
    if (
        current_scope is not identity.scope
        or identity.scope_fields != current_fields
        or identity.scope_fields != identity_fields
        or current_fields[0] != identity.session_id
        or current_fields[1] != identity.request_id
        or current_fields[2] != identity.principal_hash
        or current_fields[3] != identity.delete_epoch
        or _canonical_namespace_digest(
            identity.session_id,
            identity.principal_hash,
            identity.delete_epoch,
            identity.incarnation_id,
        ) != identity.canonical_namespace_digest
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    return RollingHistoryOwnerIdentity(
        session_id=identity.session_id,
        principal_hash=identity.principal_hash,
        delete_epoch=identity.delete_epoch,
        incarnation_id=identity.incarnation_id,
        request_id=identity.request_id,
        canonical_namespace_digest=identity.canonical_namespace_digest,
    )


def _require_active_rolling_history_sql_handle(
    handle: RollingHistorySqlHandle,
    expected_mode: OperationMode,
) -> RollingHistoryOwnerIdentity:
    """Require this exact handle in its live owner callback and transaction."""
    active_operation = _ACTIVE_SQL_OPERATION.get()
    owner_identity = _OWNER_IDENTITY.get()
    if (
        type(handle) is not _SqlOperationHandle
        or type(expected_mode) is not OperationMode
        or type(active_operation) is not _ActiveSqlOperation
        or active_operation.handle is not handle
        or type(active_operation.transaction) is not _TransactionState
        or active_operation.mode is not expected_mode
        or type(owner_identity) is not _OwnerIdentity
        or active_operation.owner_identity is not owner_identity
        or active_operation.thread_id != get_ident()
        or active_operation.is_active is not True
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    _assert_transaction_active(active_operation.transaction)
    identity = get_rolling_history_owner_identity()
    if (
        type(identity) is not RollingHistoryOwnerIdentity
        or identity.session_id != owner_identity.session_id
        or identity.principal_hash != owner_identity.principal_hash
        or identity.request_id != owner_identity.request_id
        or identity.delete_epoch != owner_identity.delete_epoch
        or identity.incarnation_id != owner_identity.incarnation_id
        or identity.canonical_namespace_digest != owner_identity.canonical_namespace_digest
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    _validate_owner_operation_binding(
        active_operation.transaction,
        owner_identity,
        active_operation.user,
    )
    _assert_transaction_active(active_operation.transaction)
    return identity


def _validate_owner_operation_binding(
    transaction: _TransactionState,
    identity: _OwnerIdentity,
    user: dict | None,
) -> None:
    """Recheck the bound request and its active database owner on one transaction."""
    _assert_transaction_active(transaction)
    scope = _current_scope(identity.origin)
    fields = _scope_fields(scope)
    if (
        scope is not identity.scope
        or fields != identity.scope_fields
        or fields
        != (
            identity.session_id,
            identity.request_id,
            identity.principal_hash,
            identity.delete_epoch,
        )
        or _host_principal(user) != identity.principal_hash
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    incarnation_id = _validate_database_binding(
        transaction.connection,
        identity.session_id,
        identity.principal_hash,
        identity.delete_epoch,
        expected_incarnation_id=identity.incarnation_id,
    )
    if incarnation_id != identity.incarnation_id:
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    _assert_transaction_active(transaction)


def run_rolling_history_owner_operation(
    user: dict | None,
    mode: OperationMode,
    callback: Callable[[RollingHistorySqlHandle], _T],
) -> _T:
    """Run one synchronous host callback through a restricted SQL handle.

    The callback must execute all operation SQL through the supplied handle and
    return materialized data. Transaction and connection control statements are
    refused; direct live database handles and handles inside supported containers
    are rejected. Its result is returned only after commit. This compatible owner
    operation does not validate the manual-memory clear epoch. Atomicity covers
    this callback only, not separate operations or a whole graph.
    """
    return _run_rolling_history_owner_operation(
        user,
        mode,
        callback,
        expected_memory_clear_epoch=_NO_MEMORY_CLEAR_EPOCH_CHECK,
    )


def run_chat_append_owner_write_operation(
    user: dict | None,
    session_id: str,
    expected_incarnation_id: str,
    callback: Callable[[RollingHistorySqlHandle, ContextEventScope], _T],
) -> _T:
    """Run one authenticated CHAT_APPEND callback in one owner WRITE transaction.

    ``user`` must be the authenticated host principal returned by the route's
    ``current_user`` dependency. The owner principal hash is derived here; the
    caller cannot supply an owner ID or a ContextEventScope. The scope is made
    from this session, the authenticated principal, the transactionally read
    delete epoch, and a server-generated request ID, then bound only while the
    callback runs. The expected incarnation must match the active database
    owner binding before the callback starts and again before commit.

    All callback SQL must go through the supplied restricted handle. In
    particular, a Message write and its rolling-history append must use this
    same handle and transaction. Exceptions from the callback propagate
    unchanged; the enclosing transaction rolls back on failure.
    """
    if (
        _OWNER_IDENTITY.get() is not None
        or _CHAT_APPEND_SCOPE.get() is not None
        or type(session_id) is not str
        or _SESSION_ID.fullmatch(session_id) is None
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
        or type(expected_incarnation_id) is not str
        or _GENERATION.fullmatch(expected_incarnation_id) is None
        or not callable(callback)
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    principal_hash = _host_principal(user)
    epoch_key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(
        session_id.encode("utf-8"),
    ).hexdigest()
    result: _T
    with _sqlite_transaction("BEGIN IMMEDIATE") as transaction:
        _assert_transaction_active(transaction)
        epoch_row = transaction.connection.execute(
            text("SELECT value FROM settings WHERE key = :key"),
            {"key": epoch_key},
        ).first()
        delete_epoch = "" if epoch_row is None else epoch_row.value
        incarnation_id = _validate_database_binding(
            transaction.connection,
            session_id,
            principal_hash,
            delete_epoch,
            expected_incarnation_id=expected_incarnation_id,
        )
        if incarnation_id != expected_incarnation_id:
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

        scope = ContextEventScope(
            session_id=session_id,
            request_id=uuid4().hex,
            principal_hash=principal_hash,
            session_epoch=delete_epoch,
        )
        scope_fields = _scope_fields(scope)
        identity = _OwnerIdentity(
            origin=ScopeOrigin.CHAT_APPEND,
            scope=scope,
            scope_fields=scope_fields,
            session_id=session_id,
            principal_hash=principal_hash,
            request_id=scope.request_id,
            delete_epoch=delete_epoch,
            incarnation_id=incarnation_id,
            canonical_namespace_digest=_canonical_namespace_digest(
                session_id, principal_hash, delete_epoch, incarnation_id,
            ),
        )
        scope_token = _CHAT_APPEND_SCOPE.set(scope)
        identity_token = _OWNER_IDENTITY.set(identity)
        try:
            _validate_owner_operation_binding(transaction, identity, user)
            with _restricted_sql_handle(transaction, OperationMode.WRITE, user) as handle:
                _assert_transaction_active(transaction)
                result = callback(handle, scope)
            _assert_transaction_active(transaction)
            if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if _contains_live_database_handle(result, transaction.connection):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            _validate_owner_operation_binding(transaction, identity, user)
            _assert_transaction_active(transaction)
        finally:
            _OWNER_IDENTITY.reset(identity_token)
            _CHAT_APPEND_SCOPE.reset(scope_token)

    return result


def run_legacy_message_migration_owner_write_operation(
    user: dict | None,
    session_id: str,
    expected_incarnation_id: str,
    callback: Callable[[RollingHistorySqlHandle], _T],
) -> _T:
    """Run one legacy Message migration callback in its own owner WRITE scope.

    The owner principal is derived from the authenticated host user. The
    session, delete epoch, and expected incarnation are bound before the
    callback starts and revalidated before commit. The callback receives a
    restricted handle with bounded legacy Message body and metadata readers;
    all work uses this same BEGIN IMMEDIATE transaction and SQLite connection.
    """
    if (
        _OWNER_IDENTITY.get() is not None
        or _CHAT_APPEND_SCOPE.get() is not None
        or _LEGACY_MESSAGE_MIGRATION_SCOPE.get() is not None
        or type(session_id) is not str
        or _SESSION_ID.fullmatch(session_id) is None
        or any(ord(char) < 32 or ord(char) == 127 for char in session_id)
        or type(expected_incarnation_id) is not str
        or _GENERATION.fullmatch(expected_incarnation_id) is None
        or not callable(callback)
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

    principal_hash = _host_principal(user)
    epoch_key = SESSION_DELETE_EPOCH_PREFIX + hashlib.sha256(
        session_id.encode("utf-8"),
    ).hexdigest()
    result: _T
    with _sqlite_transaction("BEGIN IMMEDIATE") as transaction:
        _assert_transaction_active(transaction)
        epoch_row = transaction.connection.execute(
            text("SELECT value FROM settings WHERE key = :key"),
            {"key": epoch_key},
        ).first()
        delete_epoch = "" if epoch_row is None else epoch_row.value
        incarnation_id = _validate_database_binding(
            transaction.connection,
            session_id,
            principal_hash,
            delete_epoch,
            expected_incarnation_id=expected_incarnation_id,
        )
        if incarnation_id != expected_incarnation_id:
            raise RollingHistoryOwnerScopeError(_UNAVAILABLE)

        scope = ContextEventScope(
            session_id=session_id,
            request_id=uuid4().hex,
            principal_hash=principal_hash,
            session_epoch=delete_epoch,
        )
        scope_fields = _scope_fields(scope)
        identity = _OwnerIdentity(
            origin=ScopeOrigin.LEGACY_MESSAGE_MIGRATION,
            scope=scope,
            scope_fields=scope_fields,
            session_id=session_id,
            principal_hash=principal_hash,
            request_id=scope.request_id,
            delete_epoch=delete_epoch,
            incarnation_id=incarnation_id,
            canonical_namespace_digest=_canonical_namespace_digest(
                session_id, principal_hash, delete_epoch, incarnation_id,
            ),
        )
        scope_token = _LEGACY_MESSAGE_MIGRATION_SCOPE.set(scope)
        identity_token = _OWNER_IDENTITY.set(identity)
        try:
            _validate_owner_operation_binding(transaction, identity, user)
            with _restricted_sql_handle(transaction, OperationMode.WRITE, user) as handle:
                _assert_transaction_active(transaction)
                result = callback(handle)
            _assert_transaction_active(transaction)
            if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if _contains_live_database_handle(result, transaction.connection):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
        finally:
            try:
                _validate_owner_operation_binding(transaction, identity, user)
                _assert_transaction_active(transaction)
            finally:
                _OWNER_IDENTITY.reset(identity_token)
                _LEGACY_MESSAGE_MIGRATION_SCOPE.reset(scope_token)

    return result


def run_rolling_history_owner_write_operation_strict_memory_epoch(
    user: dict | None,
    expected_memory_clear_epoch: str,
    callback: Callable[[RollingHistorySqlHandle], _T],
) -> _T:
    """Run a host-only write pinned to a trusted manual-memory clear epoch.

    The expected epoch must come from a host snapshot or collector, never from
    model/client input. The marker is checked before and after the callback in
    the same ``BEGIN IMMEDIATE`` transaction; the owner binding is rechecked after
    the callback, and the result is returned only after that transaction commits.
    """
    expected_epoch = _validate_memory_clear_epoch(expected_memory_clear_epoch)
    return _run_rolling_history_owner_operation(
        user,
        OperationMode.WRITE,
        callback,
        expected_memory_clear_epoch=expected_epoch,
    )


def _run_rolling_history_owner_operation(
    user: dict | None,
    mode: OperationMode,
    callback: Callable[[RollingHistorySqlHandle], _T],
    *,
    expected_memory_clear_epoch: object,
) -> _T:
    identity = _OWNER_IDENTITY.get()
    strict_memory_epoch = expected_memory_clear_epoch is not _NO_MEMORY_CLEAR_EPOCH_CHECK
    if (
        identity is None
        or type(identity) is not _OwnerIdentity
        or type(mode) is not OperationMode
        or not callable(callback)
        or (strict_memory_epoch and mode is not OperationMode.WRITE)
    ):
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
    expected_epoch = ""
    if strict_memory_epoch:
        expected_epoch = _validate_memory_clear_epoch(expected_memory_clear_epoch)

    begin_sql = "BEGIN IMMEDIATE" if mode is OperationMode.WRITE else "BEGIN"
    try:
        with _sqlite_transaction(begin_sql) as transaction:
            _validate_owner_operation_binding(transaction, identity, user)
            if strict_memory_epoch:
                _require_memory_clear_epoch(
                    transaction.connection,
                    expected_epoch,
                )

            with _restricted_sql_handle(transaction, mode, user) as handle:
                _assert_transaction_active(transaction)
                result = callback(handle)
            _assert_transaction_active(transaction)
            if hasattr(result, "__await__") or hasattr(result, "__aiter__"):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            if _contains_live_database_handle(result, transaction.connection):
                raise RollingHistoryOwnerScopeError(_UNAVAILABLE)
            _assert_transaction_active(transaction)
            if strict_memory_epoch:
                _validate_owner_operation_binding(transaction, identity, user)
                _require_memory_clear_epoch(
                    transaction.connection,
                    expected_epoch,
                )
    except RollingHistoryOwnerScopeError:
        raise
    except Exception:
        logger.exception("Rolling history owner operation failed")
        raise RollingHistoryOwnerScopeError(_UNAVAILABLE) from None
    return result


def _contains_live_database_handle(value: object, connection: Connection, seen=None) -> bool:
    """Reject live query/connection handles, including inside basic containers."""
    if value is connection or isinstance(value, (Connection, sqlite3.Connection)):
        return True
    if isinstance(value, Iterator):
        return True

    value_type = type(value)
    module = getattr(value_type, "__module__", "")
    if (
        module.startswith("sqlalchemy.engine.")
        and any(callable(getattr(value, name, None)) for name in ("fetchone", "fetchall"))
    ):
        return True
    if module == "sqlite3" and value_type.__name__ == "Cursor":
        return True
    if module.startswith("sqlalchemy.pool.") and callable(getattr(value, "cursor", None)):
        return True

    if seen is None:
        seen = set()
    object_id = id(value)
    if object_id in seen:
        return False
    seen.add(object_id)
    if isinstance(value, Mapping):
        return any(
            _contains_live_database_handle(item, connection, seen)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_live_database_handle(item, connection, seen) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return any(
            _contains_live_database_handle(object.__getattribute__(value, field.name), connection, seen)
            for field in fields(value)
        )
    return False
