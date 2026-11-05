"""Request-lifecycle, read-only host capability for rolling Skill history.

This module does not register a tool or accept an authorization identity from
model/client input. A trusted host caller must pass the authenticated ``user``
injected into its route by ``current_user`` and the host-selected
``ScopeOrigin``. The host scope itself is read only from its bound ContextVar.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from collections.abc import Iterator
from dataclasses import dataclass

from app.api.compaction_archive import get_compaction_scope
from app.api.context_events import ContextEventScope
from app.api.rolling_history import (
    RollingHistoryEvent,
    RollingHistoryReference,
    RollingHistorySkillProvenance,
    read_rolling_skill_event,
)
from app.api.rolling_history_owner_scope import (
    RollingHistoryOwnerIdentity,
    RollingHistoryOwnerScopeError,
    ScopeOrigin,
    bootstrap_rolling_history_owner_scope,
    get_rolling_history_owner_identity,
)
from app.api.tool_events import get_tool_event_scope


_UNAVAILABLE = "Rolling Skill read capability is unavailable"


@dataclass(frozen=True)
class RollingHistorySkillRead:
    """Complete Skill event body with its database-verified provenance."""

    body: str
    provenance: RollingHistorySkillProvenance


class _RevocableReadState:
    """Shared mutable state retained by copied ContextVar contexts."""

    __slots__ = (
        "lock",
        "revoked",
        "host_user",
        "origin",
        "scope",
        "scope_fields",
        "owner_identity",
    )

    def __init__(
        self,
        host_user: dict,
        origin: ScopeOrigin,
        scope: ContextEventScope,
        scope_fields: tuple[str, str, str, str],
        owner_identity: RollingHistoryOwnerIdentity,
    ) -> None:
        self.lock = Lock()
        self.revoked = False
        self.host_user: dict | None = host_user
        self.origin: ScopeOrigin | None = origin
        self.scope: ContextEventScope | None = scope
        self.scope_fields = scope_fields
        self.owner_identity: RollingHistoryOwnerIdentity | None = owner_identity

    def revoke(self) -> None:
        """Serialize revocation against reads, then drop captured authority."""
        with self.lock:
            self.revoked = True
            self.host_user = None
            self.origin = None
            self.scope = None
            self.scope_fields = ()
            self.owner_identity = None


_ACTIVE_READ_STATE: ContextVar[_RevocableReadState | None] = ContextVar(
    "rolling_history_skill_read_state",
    default=None,
)


def _unavailable() -> RollingHistoryOwnerScopeError:
    return RollingHistoryOwnerScopeError(_UNAVAILABLE)


def _scope_for_origin(origin: ScopeOrigin) -> ContextEventScope:
    if type(origin) is not ScopeOrigin:
        raise _unavailable()
    scope = (
        get_compaction_scope()
        if origin is ScopeOrigin.COMPACTION
        else get_tool_event_scope()
    )
    if type(scope) is not ContextEventScope:
        raise _unavailable()
    return scope


def _scope_fields(scope: ContextEventScope) -> tuple[str, str, str, str]:
    if type(scope) is not ContextEventScope:
        raise _unavailable()
    fields = (scope.session_id, scope.request_id, scope.principal_hash, scope.session_epoch)
    if any(type(value) is not str for value in fields):
        raise _unavailable()
    return fields


def _owner_fields(identity: RollingHistoryOwnerIdentity) -> tuple[str, str, str, str]:
    if type(identity) is not RollingHistoryOwnerIdentity:
        raise _unavailable()
    return (
        identity.session_id,
        identity.request_id,
        identity.principal_hash,
        identity.delete_epoch,
    )


class _RollingHistorySkillReadCapability:
    """Only exposes a complete, provenance-checked read operation."""

    __slots__ = ("__state",)

    def __init__(self, state: _RevocableReadState) -> None:
        self.__state = state

    def read(
        self,
        reference: RollingHistoryReference,
    ) -> RollingHistorySkillRead | None:
        """Read one complete Skill event using its exact immutable reference."""
        if type(reference) is not RollingHistoryReference:
            raise _unavailable()

        state = self.__state
        with state.lock:
            if state.revoked or _ACTIVE_READ_STATE.get() is not state:
                raise _unavailable()

            origin = state.origin
            scope = state.scope
            host_user = state.host_user
            owner_identity = state.owner_identity
            if (
                origin is None
                or scope is None
                or host_user is None
                or owner_identity is None
            ):
                raise _unavailable()

            current_scope = _scope_for_origin(origin)
            if (
                current_scope is not scope
                or _scope_fields(current_scope) != state.scope_fields
            ):
                raise _unavailable()

            # Rebind and validate the owner for this read only. The yielded
            # capability scope does not keep owner bootstrap active.
            with bootstrap_rolling_history_owner_scope(host_user, origin):
                current_scope = _scope_for_origin(origin)
                if (
                    current_scope is not scope
                    or _scope_fields(current_scope) != state.scope_fields
                ):
                    raise _unavailable()
                current_owner_identity = get_rolling_history_owner_identity()
                if (
                    _owner_fields(current_owner_identity) != state.scope_fields
                    or current_owner_identity != owner_identity
                ):
                    raise _unavailable()

                # The reader opens an owner-restricted READ transaction and
                # rechecks owner, incarnation, and delete epoch before returning.
                event = read_rolling_skill_event(reference, host_user, origin)
            if event is None:
                return None
            if (
                type(event) is not RollingHistoryEvent
                or event.event_type not in ("skill_instruction", "skill_resource")
                or type(event.content) is not str
                or type(event.skill_provenance) is not RollingHistorySkillProvenance
            ):
                raise _unavailable()
            return RollingHistorySkillRead(
                body=event.content,
                provenance=event.skill_provenance,
            )


def get_rolling_history_skill_read_capability() -> _RollingHistorySkillReadCapability:
    """Return this context's active, revocable Skill-history read capability.

    This host-internal getter accepts no identity or scope input. Context copies
    share the same revocable state, so a capability obtained from a copied
    context stops working when the originating scope exits.
    """
    state = _ACTIVE_READ_STATE.get()
    if type(state) is not _RevocableReadState:
        raise _unavailable()

    with state.lock:
        if (
            state.revoked
            or _ACTIVE_READ_STATE.get() is not state
            or state.host_user is None
            or state.origin is None
            or state.scope is None
            or state.owner_identity is None
        ):
            raise _unavailable()
        return _RollingHistorySkillReadCapability(state)


@contextmanager
def rolling_history_skill_read_scope(
    authenticated_user: dict | None,
    origin: ScopeOrigin,
) -> Iterator[_RollingHistorySkillReadCapability]:
    """Yield a request-bound read capability to trusted host code only.

    ``authenticated_user`` must be the value injected into the route by
    ``current_user`` and ``origin`` must select the active TOOL scope. This
    function deliberately accepts neither a ContextEventScope nor any principal,
    session, request, or epoch fields from the caller. The entry bootstrap
    captures a database-validated owner identity and exits before this scope
    yields; each read temporarily bootstraps and compares that identity under
    the shared lock. Nested and reentrant capability scopes are rejected.
    """
    if _ACTIVE_READ_STATE.get() is not None:
        raise _unavailable()
    if (authenticated_user is None or type(origin) is not ScopeOrigin
            or origin is not ScopeOrigin.TOOL):
        raise _unavailable()

    with bootstrap_rolling_history_owner_scope(authenticated_user, origin):
        scope = _scope_for_origin(origin)
        scope_fields = _scope_fields(scope)
        owner_identity = get_rolling_history_owner_identity()
        if _owner_fields(owner_identity) != scope_fields:
            raise _unavailable()

    # Retain only the captured, database-validated identity and revocable state.
    # Each read bootstraps this owner temporarily while holding the shared lock.
    state = _RevocableReadState(
        host_user=authenticated_user,
        origin=origin,
        scope=scope,
        scope_fields=scope_fields,
        owner_identity=owner_identity,
    )
    capability = _RollingHistorySkillReadCapability(state)
    token = _ACTIVE_READ_STATE.set(state)
    try:
        yield capability
    finally:
        # ContextVar copies keep this same state object. Revoke it under the
        # shared lock before resetting this context.
        try:
            state.revoke()
        finally:
            _ACTIVE_READ_STATE.reset(token)
