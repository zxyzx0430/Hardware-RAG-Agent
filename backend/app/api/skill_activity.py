"""Pure, body-free projections for persisted read-only Skill activity."""

import json
import math
import re
import unicodedata


_SKILLS = {"load_skill": "instructions", "read_skill_resource": "resource"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.IGNORECASE | re.ASCII)
_EXTENSIONS = {"md", "txt", "json", "yaml", "yml", "csv", "toml"}


def _matches(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_path(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        return False
    if any(char in value for char in '\\:<>"|?*'):
        return False
    parts = value.split("/")
    if len(parts) < 2 or parts[0].lower() not in {"references", "assets"}:
        return False
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((" ", "."))
                or unicodedata.normalize("NFC", part) != part
                or _DEVICE.fullmatch(part)
                or any(unicodedata.category(char) in {"Cc", "Cf"} for char in part)):
            return False
    filename = parts[-1]
    return filename.rfind(".") > 0 and filename.rsplit(".", 1)[-1].lower() in _EXTENSIONS


def _record(value):
    if isinstance(value, str):
        if len(value) > 16384:
            return None
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return None
    return value if isinstance(value, dict) else None


def _safe_trace(name, value):
    if not isinstance(value, dict):
        return None
    if not _matches(value.get("skill_id"), _ID) or value.get("stage") != _SKILLS[name]:
        return None
    trace = {"skill_id": value["skill_id"], "stage": value["stage"]}
    if _matches(value.get("content_hash"), _HASH):
        trace["content_hash"] = value["content_hash"]
    if name == "read_skill_resource" and _valid_path(value.get("path")):
        trace["path"] = value["path"]
    return trace


def _result_conflicts(value, trace):
    """Reject explicit conflicting envelopes, never derive provenance from them."""
    result = _record(value)
    if result is None:
        return False
    if ("data" in result or "success" in result) and (
        result.get("success") is not True or not isinstance(result.get("data"), dict)
        or result["data"].get("success") is not True
    ):
        return True
    for record in (result, result.get("data")):
        if record is None:
            continue
        if not isinstance(record, dict):
            return True
        if record.get("error") is not None:
            return True
        if "success" in record and record["success"] is not True:
            return True
        for key in ("skill_id", "stage", "path", "content_hash"):
            if key in record and record[key] != trace.get(key):
                return True
    return False


def _project_step(step):
    if not isinstance(step, dict):
        return None
    name = step.get("name")
    if isinstance(name, str) and name in _SKILLS and step.get("type") != "tool":
        return None
    if step.get("type") == "tool" and not isinstance(name, str):
        return None
    if step.get("type") != "tool" or name not in _SKILLS:
        if "skill_trace" not in step:
            return step
        return {key: value for key, value in step.items() if key != "skill_trace"}

    projected = {"type": "tool", "name": name}
    association_valid = all(
        key not in step or _matches(step[key], _CALL_ID) for key in ("id", "call_id")
    )
    for key in ("id", "call_id"):
        if _matches(step.get(key), _CALL_ID):
            projected[key] = step[key]
    for key in ("duration", "startTime"):
        value = step.get(key)
        if type(value) in (int, float) and 0 <= value <= 9007199254740991 and math.isfinite(value):
            projected[key] = value

    status = step.get("status")
    if not isinstance(status, str) or status not in {"pending", "running", "done", "error"}:
        status = "error"
    original = step.get("skill_trace")
    trace = _safe_trace(name, original)
    args = _record(step.get("args"))
    matches_call = bool(trace and args and args.get("skill_id") == trace["skill_id"])
    if name == "read_skill_resource":
        matches_call = matches_call and "path" in trace and args.get("path") == trace["path"]
    complete = bool(trace and "content_hash" in trace)
    if name == "read_skill_resource":
        complete = complete and "path" in trace
    success = bool(
        status == "done" and complete and matches_call and association_valid
        and original.get("success") is True and original.get("error") is None
        and step.get("error") is None
        and ("success" not in step or step["success"] is True)
        and not _result_conflicts(step.get("result"), trace)
    )
    projected["status"] = "error" if status == "done" and not success else status
    safe_args = {"skill_id": trace["skill_id"]} if matches_call else {}
    if matches_call and name == "read_skill_resource":
        safe_args["path"] = trace["path"]
    projected["args"] = json.dumps(safe_args, ensure_ascii=False, separators=(",", ":"))
    if trace:
        projected["skill_trace"] = {**trace, "success": success}
    if "result" in step or status in {"done", "error"}:
        projected["result"] = (
            "Skill instructions loaded" if name == "load_skill" else "Skill resource loaded"
        ) if success else "Skill operation failed"
    return projected


def project_skill_activity(activity):
    """Return a non-mutating projection; GET callers must not persist it."""
    if not isinstance(activity, dict):
        return None
    if "steps" not in activity:
        return activity
    steps = activity["steps"]
    if not isinstance(steps, list):
        return {**activity, "steps": []}
    return {**activity, "steps": [_project_step(step) for step in steps]}
