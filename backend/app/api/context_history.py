"""Server-authoritative request history for owner-bound chat sessions.

The browser is a view over persisted history, not its authority. In
``history_mode="server"`` the host loads every saved user/assistant turn for the
authenticated owner and appends only the active turn supplied by the request.
This keeps long conversations bounded by server-side storage and compaction
instead of an in-memory request cap, and it prevents a client from injecting
fabricated assistant history into the model context.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.api.common import get_db_ctx
from app.api.session_owner import require_session_owner
from app.db.models import Message as MessageModel


_HISTORY_ROLES = frozenset({"user", "assistant"})


@dataclass(frozen=True, slots=True)
class AssembledRequestHistory:
    """Verified plain history plus per-message durable source identities."""

    messages: tuple[dict[str, Any], ...]
    source_message_ids: tuple[str | None, ...]
    persisted_count: int
    transient_start: int


def _message_role(message: Any) -> str | None:
    role = getattr(message, "role", None)
    return role if type(role) is str else None


def _message_content(message: Any) -> Any:
    return getattr(message, "content", None)


def _message_id(message: Any) -> str | None:
    value = getattr(message, "message_id", None)
    return value if type(value) is str and value else None


def _plain_text(content: Any, normalize: Callable[[Any], Any]) -> str | None:
    value = normalize(content)
    return value if type(value) is str else None


def _transient_turn(
    payload_messages: Sequence[Any],
    normalize: Callable[[Any], Any],
) -> list[dict[str, str]] | None:
    """Return quote system messages plus the active user turn, or None."""
    if not payload_messages:
        return None
    final = len(payload_messages) - 1
    if _message_role(payload_messages[final]) != "user":
        return None
    quote_start = final
    while quote_start > 0 and _message_role(payload_messages[quote_start - 1]) == "system":
        quote_start -= 1
    turn: list[dict[str, str]] = []
    for message in payload_messages[quote_start:]:
        content = _plain_text(_message_content(message), normalize)
        if content is None:
            return None
        turn.append({"role": _message_role(message) or "user", "content": content})
    return turn


def _persisted_rows(session_id: str, user: dict | None) -> list[tuple[str, str, str]] | None:
    """Read the owner's full plain history, or None when server mode is unavailable."""
    try:
        with get_db_ctx() as db:
            require_session_owner(db, session_id, user)
            rows = (
                db.query(MessageModel)
                .filter(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
                .all()
            )
            # Materialize while the rows are still attached: get_db_ctx commits
            # and closes the session, and expire_on_commit would detach them.
            history: list[tuple[str, str, str]] = []
            for row in rows:
                if row.role not in _HISTORY_ROLES or type(row.content) is not str:
                    continue
                if type(row.id) is not str or not row.id:
                    return None
                history.append((row.role, row.content, row.id))
            return history
    except PermissionError:
        return None
    except Exception:
        return None


def _find_last(history: Sequence[tuple[str, str, str]], message_id: str) -> int | None:
    for index in range(len(history) - 1, -1, -1):
        if history[index][2] == message_id:
            return index
    return None


def assemble_server_history(
    session_id: str | None,
    user: dict | None,
    payload_messages: Sequence[Any],
    *,
    normalize_content: Callable[[Any], Any],
) -> AssembledRequestHistory | None:
    """Build the model history from durable rows plus the active user turn.

    Returns ``None`` when server mode cannot be proven. The caller must then
    fail closed rather than treating a partial result as authoritative.
    """
    if type(session_id) is not str or not session_id:
        return None
    if not isinstance(payload_messages, Sequence) or not payload_messages:
        return None
    history = _persisted_rows(session_id, user)
    if history is None:
        return None

    turn = _transient_turn(payload_messages, normalize_content)
    if turn is None:
        return None
    turn_messages = turn
    current = payload_messages[-1]
    current_id = _message_id(current)

    if current_id is not None:
        existing = _find_last(history, current_id)
        if existing is not None:
            role, content, _ = history[existing]
            if (
                role != "user"
                or content != turn_messages[-1]["content"]
                or existing != len(history) - 1
            ):
                # An already-answered or edited turn cannot be safely replayed
                # from server history; retain the legacy path instead.
                return None
            messages = tuple({"role": row[0], "content": row[1]} for row in history)
            return AssembledRequestHistory(
                messages=messages,
                source_message_ids=tuple(row[2] for row in history),
                persisted_count=len(history),
                transient_start=len(history),
            )

    messages = tuple({"role": row[0], "content": row[1]} for row in history)
    source_ids: tuple[str | None, ...] = tuple(row[2] for row in history)
    transient: tuple[dict[str, Any], ...] = tuple(turn_messages)
    messages = messages + transient
    source_ids = source_ids + (None,) * len(transient)
    return AssembledRequestHistory(
        messages=messages,
        source_message_ids=source_ids,
        persisted_count=len(history),
        transient_start=len(history),
    )


_GRAPH_ROLES = {
    "human": "user",
    "user": "user",
    "ai": "assistant",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool",
}


def build_graph_message_source_ids(
    session_id: str | None,
    user: dict | None,
    messages: Sequence[Any],
) -> tuple[str | None, ...] | None:
    """Recover durable IDs for the persisted suffix of an existing graph state.

    The graph may start with a compacted checkpoint, may contain current-run
    tool messages, and may end with new assistant text that is not durable yet.
    Locate the last durable turn, then match backwards only across a contiguous
    persisted suffix. Everything else stays ``None``. ``None`` means the owner
    history itself could not be verified.
    """
    if type(session_id) is not str or not session_id:
        return None
    if not isinstance(messages, (list, tuple)) or not messages:
        return ()
    history = _persisted_rows(session_id, user)
    if history is None:
        return None
    source_ids: list[str | None] = [None] * len(messages)
    if not history:
        return tuple(source_ids)

    def matches(graph_index: int, durable_index: int) -> bool:
        role = _GRAPH_ROLES.get(getattr(messages[graph_index], "type", None))
        content = getattr(messages[graph_index], "content", None)
        durable_role, durable_content, _ = history[durable_index]
        return (
            role in _HISTORY_ROLES
            and type(content) is str
            and role == durable_role
            and content == durable_content
        )

    best: tuple[int, list[tuple[int, str]]] | None = None
    for end in range(len(messages) - 1, -1, -1):
        if not matches(end, len(history) - 1):
            continue
        graph_index = end
        durable_index = len(history) - 1
        assigned: list[tuple[int, str]] = []
        while graph_index >= 0 and durable_index >= 0:
            if not matches(graph_index, durable_index):
                break
            assigned.append((graph_index, history[durable_index][2]))
            graph_index -= 1
            durable_index -= 1
        if assigned and (best is None or len(assigned) > best[0]):
            best = (len(assigned), assigned)
        if best is not None and best[0] >= len(history):
            break
    if best is not None:
        for graph_index, durable_id in best[1]:
            source_ids[graph_index] = durable_id
    return tuple(source_ids)
