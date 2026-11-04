"""Seed real owner-bound V3 message sources for compaction tests."""

from contextlib import contextmanager
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4


OWNER = {
    "anonymous": False,
    "provider": "synthetic",
    "api_key": "synthetic-owner-key",
}


def _require_confirmed_active_pointer(result, *, branch_id=None, expected_head=None):
    pointer = getattr(result, "pointer", None)
    branch = getattr(result, "branch", None)
    bound_head = getattr(result, "head", None)
    if (getattr(result, "outcome", None) != "readback_confirmed"
            or pointer is None or pointer.state != "active"
            or type(pointer.branch_id) is not str
            or branch is None or branch.branch_id != pointer.branch_id
            or bound_head is None or bound_head.head.branch_id != pointer.branch_id
            or (branch_id is not None and pointer.branch_id != branch_id)
            or (expected_head is not None
                and bound_head.head != expected_head.head)):
        raise AssertionError("Could not confirm the owner-bound V2 active branch")
    return result


def _initialize_empty_v2_history(context_archive_scope, state):
    """Create and read back the first V2 root only for an empty owner session."""
    from app.api.rolling_revision_store import (
        create_rolling_history_v2_active_branch_pointer,
        create_rolling_history_v2_root,
        read_rolling_history_v2_active_branch_pointer,
        read_rolling_history_v2_branch_head,
    )
    from app.db.models import Message as MessageModel

    with context_archive_scope.factory() as db:
        has_messages = db.query(MessageModel.id).filter(
            MessageModel.session_id == state["session_id"],
        ).first() is not None

    scope = state["v2_bootstrap_scope"]
    if scope is None:
        raise AssertionError("Empty V3 source session has no V2 bootstrap scope")
    with bind_v3_compaction_owner(OWNER, scope):
        pointer_snapshot = read_rolling_history_v2_active_branch_pointer()
        if (pointer_snapshot.outcome != "readback_confirmed"
                or pointer_snapshot.pointer is None):
            raise AssertionError("Could not read the owner-bound V2 active branch")
        if pointer_snapshot.pointer.state == "active":
            _require_confirmed_active_pointer(pointer_snapshot)
            state["v2_bootstrap_pending"] = False
            return
        if pointer_snapshot.pointer.state != "unset" or has_messages:
            raise AssertionError(
                "Cannot initialize a V2 root unless the owner message list is empty",
            )

        root = create_rolling_history_v2_root(
            idempotency_key="compaction-test-empty-v2-root-v1",
        )
        root_readback = read_rolling_history_v2_branch_head(root.branch.branch_id)
        if (root_readback.branch != root.branch
                or root_readback.head.head != root.head.head):
            raise AssertionError("Could not confirm the owner-bound V2 root head")

        create_rolling_history_v2_active_branch_pointer(
            expected_pointer=pointer_snapshot.pointer,
            target_expected_head=root_readback.head,
        )
        pointer_readback = read_rolling_history_v2_active_branch_pointer()
        _require_confirmed_active_pointer(
            pointer_readback,
            branch_id=root.branch.branch_id,
            expected_head=root_readback.head,
        )
    state["v2_bootstrap_pending"] = False


def seed_v3_message_sources(
    context_archive_scope, monkeypatch, messages, *, transient_tail=False, request_id=None,
):
    """Persist aligned plain-chat sources in one stable owner-bound V3 session."""
    from app.api import crud, rolling_history_owner_scope
    from app.db import database
    from app.api.compaction_archive import make_compaction_scope
    from app.api.session_epoch import read_session_delete_epoch
    from app.api.session_owner import bind_new_session_owner, require_session_owner
    from app.db.models import Message as MessageModel, Session, SessionOwnerBinding
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    monkeypatch.setattr(rolling_history_owner_scope, "engine", context_archive_scope.engine)
    monkeypatch.setattr(database, "engine", context_archive_scope.engine)
    state = getattr(context_archive_scope, "_compaction_v3_source_state", None)
    if state is None:
        from sqlalchemy import event

        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        event.listen(context_archive_scope.engine, "connect", enable_sqlite_foreign_keys)
        # Ensure schema setup cannot reuse a pre-listener pooled connection.
        context_archive_scope.engine.dispose()
        database._ensure_message_source_generation_schema()
        fixture_session_id = context_archive_scope.scope.session_id
        session_id = fixture_session_id
        with context_archive_scope.factory.begin() as db:
            fixture_session = db.get(Session, fixture_session_id)
            binding = None
            if fixture_session is not None:
                try:
                    # Reuse only a session whose active owner binding proves its
                    # current incarnation; a bare fixture Session is not owned.
                    binding = require_session_owner(db, fixture_session_id, OWNER)
                except PermissionError:
                    binding = None

            if binding is None:
                # context_archive_scope creates a host-only Session row. Keep that
                # row intact and seed the owner history under an isolated identity.
                binding = None
                for _ in range(8):
                    candidate_id = f"compaction-{uuid4().hex}"
                    with db.no_autoflush:
                        session_exists = db.get(Session, candidate_id) is not None
                        binding_exists = (
                            db.query(SessionOwnerBinding)
                            .filter(SessionOwnerBinding.session_id == candidate_id)
                            .first()
                            is not None
                        )
                    if session_exists or binding_exists:
                        continue

                    session_id = candidate_id
                    session = Session(id=session_id)
                    db.add(session)
                    binding = bind_new_session_owner(
                        db, session, OWNER, datetime.now(timezone.utc),
                    )
                    break
                if binding is None:
                    raise RuntimeError("Could not allocate an isolated V3 source session")
            else:
                # require_session_owner has already checked principal and that
                # the bound incarnation exists and has not been retired.
                session_id = fixture_session_id
            has_messages = db.query(MessageModel.id).filter(
                MessageModel.session_id == session_id,
            ).first() is not None
            incarnation_id = binding.incarnation_id
        session_epoch = read_session_delete_epoch(session_id)
        if session_epoch is None:
            raise RuntimeError("Could not read the V3 source session epoch")
        bootstrap_scope = None
        if not has_messages:
            bootstrap_scope = make_compaction_scope(OWNER, session_id, session_epoch)
            if bootstrap_scope is None:
                raise AssertionError("Could not create the host V2 bootstrap scope")
            if request_id is not None:
                bootstrap_scope = replace(bootstrap_scope, request_id=request_id)
        state = {
            "session_id": session_id,
            "incarnation_id": incarnation_id,
            "session_epoch": session_epoch,
            "ids_by_signature": defaultdict(list),
            "ids_by_object": {},
            "v2_bootstrap_scope": bootstrap_scope,
            "v2_bootstrap_pending": bootstrap_scope is not None,
        }
        setattr(context_archive_scope, "_compaction_v3_source_state", state)
    else:
        current_epoch = read_session_delete_epoch(state["session_id"])
        if current_epoch is None or current_epoch != state.get("session_epoch"):
            raise RuntimeError(
                "V3 source session epoch changed; cached owner/source IDs are stale",
            )
        with context_archive_scope.factory() as db:
            binding = require_session_owner(db, state["session_id"], OWNER)
        if binding.incarnation_id != state.get("incarnation_id"):
            raise RuntimeError(
                "V3 source session incarnation changed; cached owner/source IDs are stale",
            )

    if state.get("v2_bootstrap_pending"):
        _initialize_empty_v2_history(context_archive_scope, state)

    ids_by_signature = state["ids_by_signature"]
    occurrence_by_signature = defaultdict(int)
    message_ids = []
    seen_objects = set()
    for index, message in enumerate(messages):
        if transient_tail and index == len(messages) - 1:
            if type(message) is not HumanMessage or type(message.content) is not str:
                raise ValueError("A transient V3 suffix must be a plain-text HumanMessage")
            message_ids.append(None)
            continue
        if (type(message) is SystemMessage
                and ("hwrag_context_checkpoint" in message.additional_kwargs
                     or "hwrag_midrun_summary" in message.additional_kwargs
                     or (type(message.content) is str
                         and message.content.startswith("[历史摘要]: ")))):
            message_ids.append(None)
            continue
        if type(message) is HumanMessage:
            role = "user"
        elif type(message) is AIMessage and not message.tool_calls and not message.invalid_tool_calls:
            role = "assistant"
        else:
            raise ValueError("V3 source setup accepts only plain chat messages")
        if type(message.content) is not str:
            raise ValueError("V3 source setup requires plain-text message content")
        signature = (role, message.content)
        occurrence = occurrence_by_signature[signature]
        occurrence_by_signature[signature] += 1
        object_key = id(message)
        if object_key in seen_objects:
            raise ValueError("V3 source setup requires distinct message objects")
        seen_objects.add(object_key)
        cached_message = state["ids_by_object"].get(object_key)
        if cached_message is not None and cached_message[0] is message:
            message_ids.append(cached_message[1])
            continue
        source_ids = ids_by_signature[signature]
        if occurrence == len(source_ids):
            message_id = f"compaction-{uuid4().hex}"
            response = crud.append_message_v1(
                state["session_id"],
                crud.MessageAppendV1(
                    id=message_id,
                    expected_incarnation_id=state["incarnation_id"],
                    role=role,
                    content=message.content,
                ),
                user=OWNER,
            )
            if response.get("success") is not True or response["data"].get("status") != "saved":
                raise AssertionError("Could not persist an owner-bound V3 source message")
            source_ids.append(message_id)
        message_id = source_ids[occurrence]
        state["ids_by_object"][object_key] = (message, message_id)
        message_ids.append(message_id)

    epoch = read_session_delete_epoch(state["session_id"])
    if epoch is None or epoch != state["session_epoch"]:
        raise RuntimeError("V3 source session epoch changed while seeding messages")
    scope = make_compaction_scope(OWNER, state["session_id"], epoch)
    if scope is None:
        raise AssertionError("Could not create the host compaction scope")
    if request_id is not None:
        scope = replace(scope, request_id=request_id)
    return OWNER, scope, tuple(message_ids)


async def autocompact_with_v3_sources(context_archive_scope, monkeypatch, messages, llm_client, context_window):
    """Run the production compactor with real, ordered owner source IDs."""
    from src.agent.compact.autocompact import autocompact_messages

    owner, scope, source_message_ids = seed_v3_message_sources(
        context_archive_scope, monkeypatch, messages,
    )
    with bind_v3_compaction_owner(owner, scope):
        result = await autocompact_messages(
            messages, llm_client, context_window, source_message_ids=source_message_ids,
        )
    return owner, scope, source_message_ids, result


@contextmanager
def bind_recovery_v3_sources(
    context_archive_scope, monkeypatch, messages, *, request_id=None, seeded_sources=None,
):
    """Bind persisted owner sources and yield their IDs for recovery calls."""
    if seeded_sources is None:
        owner, scope, source_message_ids = seed_v3_message_sources(
            context_archive_scope,
            monkeypatch,
            messages,
            request_id=request_id or context_archive_scope.scope.request_id,
        )
    else:
        owner, scope, source_message_ids = seeded_sources
    with bind_v3_compaction_owner(owner, scope):
        yield source_message_ids


@contextmanager
def bind_v3_compaction_owner(owner, scope):
    """Bind the matching host archive and owner-store capabilities."""
    from app.api.compaction_archive import bind_compaction_archive
    from app.api.rolling_history_owner_scope import ScopeOrigin
    from app.api.rolling_revision_store import rolling_history_v2_owner_scope

    with (bind_compaction_archive(scope),
          rolling_history_v2_owner_scope(owner, ScopeOrigin.COMPACTION)):
        yield
