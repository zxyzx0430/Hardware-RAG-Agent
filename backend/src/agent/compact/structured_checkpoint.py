"""Bounded, grounded quotations; content shape never confers authority.

The host supplies source slots and, after authenticated persistence, reference
projections. This module has no DB access and does not authorize or publish.
"""
import json
import re


CHECKPOINT_FIELDS = (
    "goal", "constraints", "completed", "current_state", "decisions",
    "failed_attempts", "open_questions", "artifacts_and_evidence", "next_action",
)
_REQUIRED_CONTENT = {"goal", "current_state", "artifacts_and_evidence", "next_action"}
_BYTE_LIMIT = 8192
_SLOT = re.compile(r"s(?:[1-9]|[1-5][0-9]|6[0-4])\Z")
_UUID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _checkpoint_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate checkpoint field")
        result[key] = value
    return result


def validate_checkpoint_response(raw: str, source_slots: dict[str, str]) -> str | None:
    """Return canonical quoted content, or None without a free-text fallback.

Slots are host input, not model-selected durable IDs. Exact quotations ground
text, not semantic relevance, truth, completion, or permission.
"""
    try:
        if (type(raw) is not str or len(raw) > _BYTE_LIMIT
                or len(raw.encode("utf-8")) > _BYTE_LIMIT
                or type(source_slots) is not dict or not 1 <= len(source_slots) <= 64):
            return None
        if any(type(slot) is not str or not _SLOT.fullmatch(slot)
               or type(body) is not str or not body.strip() for slot, body in source_slots.items()):
            return None
        if sum(len(body.encode("utf-8")) for body in source_slots.values()) > 32768:
            return None
        document = json.loads(raw, object_pairs_hook=_checkpoint_json_object)
        if type(document) is not dict or set(document) != set(CHECKPOINT_FIELDS):
            return None
        for field in CHECKPOINT_FIELDS:
            claims = document[field]
            if (type(claims) is not list or len(claims) > 8
                    or (field in _REQUIRED_CONTENT and not claims)
                    or (field == "completed" and claims)):
                # User/assistant text cannot prove a terminal tool outcome.
                return None
            seen = set()
            for claim in claims:
                if type(claim) is not dict or set(claim) != {"text", "source"}:
                    return None
                quote, slot = claim["text"], claim["source"]
                if (type(quote) is not str or not quote.strip() or len(quote) > 1024
                        or type(slot) is not str or slot not in source_slots
                        or quote not in source_slots[slot] or (quote, slot) in seen):
                    return None
                seen.add((quote, slot))
        canonical = json.dumps({field: document[field] for field in CHECKPOINT_FIELDS},
                               ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return canonical if len(canonical.encode("utf-8")) <= _BYTE_LIMIT else None
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return None


def bind_checkpoint_sources(raw: str, source_slots: dict[str, str], references: dict[str, dict]) -> str | None:
    """Replace slots with host-supplied reference shapes; never grant access.

The caller must authenticate these references and their lifecycle separately.
No session/principal/auth flag or model-supplied evidence object is accepted.
"""
    try:
        validated = validate_checkpoint_response(raw, source_slots)
        if validated is None or type(references) is not dict or set(references) != set(source_slots):
            return None
        for reference in references.values():
            if (type(reference) is not dict or set(reference) != {
                    "batch_id", "request_id", "sequence", "version", "content_sha256"}
                    or type(reference["batch_id"]) is not str or not _UUID.fullmatch(reference["batch_id"])
                    or type(reference["request_id"]) is not str or not _UUID.fullmatch(reference["request_id"])
                    or type(reference["sequence"]) is not int or not 1 <= reference["sequence"] <= 64
                    or type(reference["version"]) is not int or reference["version"] != 1
                    or type(reference["content_sha256"]) is not str or not _HASH.fullmatch(reference["content_sha256"])):
                return None
        document = json.loads(validated)
        bound = {field: [{"text": claim["text"], "source": dict(references[claim["source"]])}
                         for claim in document[field]] for field in CHECKPOINT_FIELDS}
        result = json.dumps(bound, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return result if len(result.encode("utf-8")) <= _BYTE_LIMIT else None
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        return None
