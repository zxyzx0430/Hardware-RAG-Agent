"""Candidate normalization and review policy for Memory v2.

Model-derived candidates are always ``proposed`` and must be reviewed before
they enter context. Explicit user requests still write ``active`` directly.
This module is pure: it never touches the database and never imports the
projection package, so WP2 and tests can reuse ``normalize_candidate``.
"""

from __future__ import annotations

import json
from typing import Any

from app.db.memory_models import (
    DEFAULT_MEMORY_STATUS,
    DEFAULT_PRINCIPAL_ID,
    HARDWARE_PROFILE_KIND,
    MEMORY_KINDS,
    MEMORY_TIERS,
    SCOPE_TO_TIER,
    TIER_TO_SCOPE,
)
from app.db.memory_store import (
    DEFAULT_CONFIDENCE,
    MAX_CONTENT_CHARS,
    MAX_EVIDENCE_CHARS,
    MAX_PROJECT_ID_CHARS,
    MemorySecretError,
    MemoryValidationError,
    contains_secret,
)

PROPOSED_STATUS = "proposed"
ACTIVE_STATUS = "active"
REJECTED_STATUS = "rejected"
SUPERSEDED_STATUS = "superseded"

# Model-derived candidates stay in the durable tiers; working memory is
# produced by checkpoint projection, not by candidate extraction.
CANDIDATE_TIERS = ("user", "project")

REVIEW_ACTIONS = {"approve": ACTIVE_STATUS, "reject": REJECTED_STATUS}

LEGAL_TRANSITIONS = frozenset(
    {
        (PROPOSED_STATUS, ACTIVE_STATUS),
        (PROPOSED_STATUS, REJECTED_STATUS),
        (ACTIVE_STATUS, SUPERSEDED_STATUS),
    }
)

_HARDWARE_MARKERS = {"hardware_profile", "hardware"}


class IllegalTransitionError(MemoryValidationError):
    """The requested review transition is not allowed from the current state."""

    code = "MEMORY_INVALID_TRANSITION"


def is_hardware_candidate(kind: Any, structured: Any = None) -> bool:
    """Hardware profiles belong to their own store and never become memory."""
    if kind == HARDWARE_PROFILE_KIND:
        return True
    if isinstance(structured, dict):
        marker = structured.get("type") or structured.get("kind")
        if isinstance(marker, str) and marker.strip().lower() in _HARDWARE_MARKERS:
            return True
    return False


def normalize_text(content: str) -> str:
    """Normalized text used for duplicate detection across tiers."""
    return " ".join((content or "").split()).casefold()


def _normalize_content(content: Any) -> str:
    if not isinstance(content, str) or not content.strip():
        raise MemoryValidationError("content must be a non-empty string")
    if len(content) > MAX_CONTENT_CHARS:
        raise MemoryValidationError(
            f"content must be at most {MAX_CONTENT_CHARS} characters"
        )
    if "\x00" in content:
        raise MemoryValidationError("content must not contain NUL characters")
    if contains_secret(content):
        raise MemorySecretError("content looks like a credential and was not stored")
    return content


def _normalize_kind(kind: Any) -> str:
    if kind is None:
        return "fact"
    if not isinstance(kind, str) or kind not in MEMORY_KINDS:
        raise MemoryValidationError("kind must be one of: " + ", ".join(MEMORY_KINDS))
    return kind


def _normalize_tier(tier: Any) -> str:
    if not isinstance(tier, str) or tier not in MEMORY_TIERS:
        raise MemoryValidationError("tier must be one of: " + ", ".join(MEMORY_TIERS))
    return tier


def _normalize_project_id(project_id: Any) -> str:
    if project_id is None:
        return ""
    if not isinstance(project_id, str):
        raise MemoryValidationError("project_id must be a string")
    value = project_id.strip()
    if len(value) > MAX_PROJECT_ID_CHARS:
        raise MemoryValidationError("project_id is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise MemoryValidationError("project_id contains control characters")
    return value


def _normalize_principal_id(principal_id: Any) -> str:
    if principal_id is None:
        return DEFAULT_PRINCIPAL_ID
    if not isinstance(principal_id, str) or not principal_id.strip():
        raise MemoryValidationError("principal_id must be a non-empty string")
    return principal_id.strip()


def _normalize_confidence(confidence: Any) -> float:
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise MemoryValidationError("confidence must be a number between 0 and 1")
    value = float(confidence)
    if not 0.0 <= value <= 1.0:
        raise MemoryValidationError("confidence must be between 0 and 1")
    return value


def _normalize_mapping(value: Any, field_name: str, max_chars: int) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MemoryValidationError(f"{field_name} must be a JSON object")
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise MemoryValidationError(f"{field_name} must be JSON-serializable") from exc
    if len(serialized) > max_chars:
        raise MemoryValidationError(
            f"{field_name} must serialize to at most {max_chars} characters"
        )
    if contains_secret(serialized):
        raise MemorySecretError(f"{field_name} looks like credentials and was not stored")
    return value


def normalize_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one model-derived candidate; always ``status=proposed``.

    Raises ``MemoryValidationError``/``MemorySecretError`` on invalid input.
    The returned mapping can be passed to ``MemoryStore.upsert_proposed``.
    """
    if not isinstance(payload, dict):
        raise MemoryValidationError("proposal must be a JSON object")
    content = _normalize_content(payload.get("content"))
    kind = _normalize_kind(payload.get("kind"))
    structured = payload.get("structured") or {}
    if not isinstance(structured, dict):
        raise MemoryValidationError("structured must be a JSON object")
    if is_hardware_candidate(kind, structured):
        raise MemoryValidationError("hardware profiles are stored outside generic memory")

    tier = payload.get("tier")
    if tier is None:
        scope = payload.get("scope")
        tier = SCOPE_TO_TIER.get(scope) if isinstance(scope, str) else "project"
    tier = _normalize_tier(tier)
    if tier not in CANDIDATE_TIERS:
        raise MemoryValidationError(
            "model-derived candidates must use the user or project tier"
        )

    project_id = _normalize_project_id(payload.get("project_id"))
    if tier == "project" and not project_id:
        raise MemoryValidationError("project candidates require project_id")
    if tier == "user":
        project_id = ""

    principal_id = _normalize_principal_id(payload.get("principal_id"))
    evidence = _normalize_mapping(
        payload.get("evidence"), "evidence", MAX_EVIDENCE_CHARS
    )
    confidence = _normalize_confidence(payload.get("confidence", DEFAULT_CONFIDENCE))
    source = payload.get("source") or "agent"
    if not isinstance(source, str) or not source.strip():
        raise MemoryValidationError("source must be a non-empty string")

    return {
        "content": content,
        "kind": kind,
        "tier": tier,
        "scope": TIER_TO_SCOPE[tier],
        "scope_key": project_id if tier == "project" else "",
        "project_id": project_id,
        "principal_id": principal_id,
        "structured": structured,
        "evidence": evidence,
        "confidence": confidence,
        "source": source.strip(),
        "status": PROPOSED_STATUS,
    }


def review_target(current_status: Any, action: Any) -> str:
    """Map an approve/reject action to the target status.

    Illegal transitions raise ``IllegalTransitionError``; the API renders that
    as 409 while malformed actions stay 400.
    """
    if not isinstance(action, str) or action.strip().lower() not in REVIEW_ACTIONS:
        raise MemoryValidationError("action must be one of: approve, reject")
    normalized_action = action.strip().lower()
    target = REVIEW_ACTIONS[normalized_action]
    current = current_status if isinstance(current_status, str) else DEFAULT_MEMORY_STATUS
    if (current, target) not in LEGAL_TRANSITIONS:
        raise IllegalTransitionError(
            f"cannot transition memory entry from {current} to {target}"
        )
    return target


def is_legal_transition(current_status: Any, target_status: Any) -> bool:
    return (current_status, target_status) in LEGAL_TRANSITIONS


__all__ = [
    "ACTIVE_STATUS",
    "CANDIDATE_TIERS",
    "IllegalTransitionError",
    "LEGAL_TRANSITIONS",
    "PROPOSED_STATUS",
    "REJECTED_STATUS",
    "REVIEW_ACTIONS",
    "SUPERSEDED_STATUS",
    "is_hardware_candidate",
    "is_legal_transition",
    "normalize_candidate",
    "normalize_text",
    "review_target",
]
