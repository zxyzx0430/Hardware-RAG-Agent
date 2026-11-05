"""Host-side ownership checks for caller-managed session transactions."""

import re
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.api.compaction_archive import compaction_principal
from app.db.models import Session, SessionIncarnation, SessionOwnerBinding


_INCARNATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_OWNERSHIP_UNAVAILABLE = "Session ownership is unavailable"


def principal_digest_for_user(user: dict | None) -> str:
    """Derive persistence authority only from the host-verified user mapping."""
    digest = compaction_principal(user)
    if digest is None:
        raise PermissionError("A verified non-anonymous principal is required")
    return digest


def _require_session_id(session_id: object) -> str:
    if type(session_id) is not str or not session_id.strip():
        raise PermissionError(_OWNERSHIP_UNAVAILABLE)
    return session_id


def _load_active_owner(
    db: DBSession,
    session_id: str,
    user: dict | None,
) -> tuple[SessionOwnerBinding, SessionIncarnation]:
    principal = principal_digest_for_user(user)
    valid_session_id = _require_session_id(session_id)

    with db.no_autoflush:
        session_row = db.query(Session).filter(Session.id == valid_session_id).first()
        if session_row is None:
            raise PermissionError(_OWNERSHIP_UNAVAILABLE)

        binding = (
            db.query(SessionOwnerBinding)
            .filter(SessionOwnerBinding.session_id == valid_session_id)
            .first()
        )
        if binding is None or binding.owner_principal_hash != principal:
            raise PermissionError(_OWNERSHIP_UNAVAILABLE)

        incarnation_id = binding.incarnation_id
        if type(incarnation_id) is not str or _INCARNATION_ID.fullmatch(incarnation_id) is None:
            raise PermissionError(_OWNERSHIP_UNAVAILABLE)

        incarnation = (
            db.query(SessionIncarnation)
            .filter(SessionIncarnation.id == incarnation_id)
            .first()
        )
        if incarnation is None or incarnation.retired_at is not None:
            raise PermissionError(_OWNERSHIP_UNAVAILABLE)

    return binding, incarnation


def require_session_owner(
    db: DBSession,
    session_id: str,
    user: dict | None,
) -> SessionOwnerBinding:
    """Require an existing session's active binding for this verified principal."""
    binding, _ = _load_active_owner(db, session_id, user)
    return binding


def bind_new_session_owner(
    db: DBSession,
    session: Session,
    user: dict | None,
    now: datetime,
) -> SessionOwnerBinding:
    """Bind only a newly pending Session, flushing rows in FK-safe order."""
    principal = principal_digest_for_user(user)
    if type(session) is not Session or session not in db.new:
        raise ValueError("A new pending Session instance is required")

    session_id = session.id
    if type(session_id) is not str or not session_id.strip():
        raise ValueError("A new Session requires an explicit non-empty id")

    if any(
        type(pending) is Session and pending is not session and pending.id == session_id
        for pending in db.new
    ):
        raise PermissionError(_OWNERSHIP_UNAVAILABLE)

    with db.no_autoflush:
        existing_session = db.query(Session).filter(Session.id == session_id).first()
        existing_binding = (
            db.query(SessionOwnerBinding)
            .filter(SessionOwnerBinding.session_id == session_id)
            .first()
        )
    if existing_session is not None or existing_binding is not None:
        raise PermissionError(_OWNERSHIP_UNAVAILABLE)

    # Every write remains in the caller's transaction; any later failure requires
    # the caller to roll back that entire business transaction.
    db.flush([session])

    incarnation = SessionIncarnation(created_at=now)
    db.add(incarnation)
    db.flush([incarnation])
    incarnation_id = incarnation.id
    if type(incarnation_id) is not str or _INCARNATION_ID.fullmatch(incarnation_id) is None:
        raise RuntimeError("A valid session incarnation id was not generated")

    binding = SessionOwnerBinding(
        session_id=session_id,
        owner_principal_hash=principal,
        incarnation_id=incarnation_id,
        created_at=now,
    )
    db.add(binding)
    db.flush([binding])
    return binding


def retire_session_owner(
    db: DBSession,
    session_id: str,
    user: dict | None,
    retired_at: datetime,
) -> SessionIncarnation:
    """Retire the active incarnation and remove its binding without committing."""
    if not isinstance(retired_at, datetime):
        raise ValueError("A retirement timestamp is required")

    binding, incarnation = _load_active_owner(db, session_id, user)
    incarnation.retired_at = retired_at
    db.flush([incarnation])
    db.delete(binding)
    db.flush([binding])
    return incarnation
