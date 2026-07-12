"""Skill 路由 — /api/skills CRUD for Agent skill markdown files.

A skill is a markdown file with optional YAML frontmatter:
    ---
    name: my-skill
    description: Does X
    triggers:
      - keyword
    ---
    # Instructions
    ...

Storage layout:
    data/skills/{skill_id}/skill.md    # markdown content
    data/skills/{skill_id}/.enabled     # flag file: present = enabled
"""

import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import current_user
from app.api.errors import sanitize_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["skills"])

# ── Constants ──────────────────────────────────────────────────────────────
# Resolve to <backend>/data/skills (this file lives in backend/app/api/).
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "skills"
SKILL_FILENAME = "skill.md"
ENABLED_FLAG = ".enabled"
FRONTMATTER_SEPARATOR = "---"
# Reject skill ids that could escape the skills directory (path traversal).
_SKILL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic models
# ═══════════════════════════════════════════════════════════════════════════

class SkillCreateRequest(BaseModel):
    content: str


# ═══════════════════════════════════════════════════════════════════════════
# Helpers — storage / parsing
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_skills_dir() -> None:
    """Ensure the skills storage directory exists."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _skill_dir(skill_id: str) -> Path:
    """Return the directory path for a skill."""
    return SKILLS_DIR / skill_id


def _validate_skill_id(skill_id: str) -> None:
    """Reject skill ids that could escape the skills directory."""
    if not _SKILL_ID_PATTERN.match(skill_id):
        raise HTTPException(
            400,
            detail=_error_body("SKILL_INVALID_ID", "Invalid skill id"),
        )


def _slugify(name: str) -> str:
    """Convert a skill name to a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter; return (meta, body). No frontmatter → ({}, content)."""
    if not content.startswith(FRONTMATTER_SEPARATOR):
        return {}, content
    parts = content.split(FRONTMATTER_SEPARATOR, 2)
    if len(parts) < 3:
        return {}, content
    return _parse_yaml_front(parts[1], parts[2])


def _parse_yaml_front(yaml_text: str, body: str) -> tuple[dict, str]:
    """Parse YAML text and return (meta, body)."""
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter: {e}")
    return meta, body.lstrip("\n")


def _is_enabled(skill_id: str) -> bool:
    """Check whether a skill is enabled."""
    return (_skill_dir(skill_id) / ENABLED_FLAG).exists()


def _read_skill(skill_id: str) -> Optional[dict]:
    """Read a skill from disk; return None if the markdown file is missing."""
    path = _skill_dir(skill_id) / SKILL_FILENAME
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    meta, _body = _parse_frontmatter(content)
    return _build_skill_info(skill_id, meta, content)


def _build_skill_info(skill_id: str, meta: dict, content: str) -> dict:
    """Assemble the skill info dict returned to clients."""
    return {
        "id": skill_id,
        "name": str(meta.get("name", skill_id)),
        "description": str(meta.get("description", "")),
        "enabled": _is_enabled(skill_id),
        "content": content,
    }


def _list_skill_ids() -> list[str]:
    """List all skill directory names."""
    if not SKILLS_DIR.exists():
        return []
    return [p.name for p in SKILLS_DIR.iterdir() if p.is_dir()]


def _write_skill(skill_id: str, content: str) -> None:
    """Write skill markdown to disk and mark enabled by default."""
    d = _skill_dir(skill_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / SKILL_FILENAME).write_text(content, encoding="utf-8")
    (d / ENABLED_FLAG).write_text("", encoding="utf-8")


def _toggle_enabled(skill_id: str) -> bool:
    """Flip the enabled flag and return the new state."""
    flag = _skill_dir(skill_id) / ENABLED_FLAG
    if flag.exists():
        flag.unlink()
        return False
    flag.write_text("", encoding="utf-8")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Helpers — error envelopes
# ═══════════════════════════════════════════════════════════════════════════

def _error_body(code: str, message: str) -> dict:
    """Build a standard error response body."""
    return {"success": False, "error": {"code": code, "message": message}}


def _internal_error(e: Exception) -> HTTPException:
    """Build a sanitized 500 error."""
    return HTTPException(500, detail=_error_body("SKILL_ERROR", sanitize_error(str(e))))


def _not_found(skill_id: str) -> HTTPException:
    """Build a 404 error for a missing skill."""
    return HTTPException(404, detail=_error_body("SKILL_NOT_FOUND", f"Skill not found: {skill_id}"))


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/skills")
async def list_skills(user: dict = Depends(current_user)) -> dict:
    """List all installed skills."""
    try:
        _ensure_skills_dir()
        skills = [_read_skill(sid) for sid in _list_skill_ids()]
        return {"success": True, "data": {"skills": [s for s in skills if s]}}
    except Exception as e:
        logger.exception("list skills failed")
        raise _internal_error(e)


@router.post("/skills")
async def create_skill(payload: SkillCreateRequest, user: dict = Depends(current_user)) -> dict:
    """Create a new skill from markdown content."""
    try:
        _ensure_skills_dir()
        meta, _body = _parse_frontmatter(payload.content)
        name = str(meta.get("name", "")).strip()
        skill_id = _slugify(name) if name else uuid.uuid4().hex
        if (_skill_dir(skill_id) / SKILL_FILENAME).exists():
            raise HTTPException(409, detail=_error_body("SKILL_EXISTS", f"Skill already exists: {skill_id}"))
        _write_skill(skill_id, payload.content)
        return {"success": True, "data": _build_skill_info(skill_id, meta, payload.content)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, detail=_error_body("SKILL_INVALID", str(e)))
    except Exception as e:
        logger.exception("create skill failed")
        raise _internal_error(e)


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, user: dict = Depends(current_user)) -> dict:
    """Delete a skill directory."""
    _validate_skill_id(skill_id)
    d = _skill_dir(skill_id)
    if not d.exists():
        raise _not_found(skill_id)
    try:
        shutil.rmtree(d)
        return {"success": True}
    except Exception as e:
        logger.exception("delete skill failed: %s", skill_id)
        raise _internal_error(e)


@router.patch("/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, user: dict = Depends(current_user)) -> dict:
    """Toggle a skill's enabled/disabled flag."""
    _validate_skill_id(skill_id)
    if not (_skill_dir(skill_id) / SKILL_FILENAME).exists():
        raise _not_found(skill_id)
    try:
        enabled = _toggle_enabled(skill_id)
        return {"success": True, "data": {"id": skill_id, "enabled": enabled}}
    except Exception as e:
        logger.exception("toggle skill failed: %s", skill_id)
        raise _internal_error(e)
