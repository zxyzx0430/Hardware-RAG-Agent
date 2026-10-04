"""HTTP API for local Markdown skills and pinned GitHub imports."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool, StrictStr

from app.api.dependencies import current_user, current_user_optional
from app.api.errors import sanitize_error
from src.skills.errors import SkillError
from src.skills.format import LEGACY_FILENAME, parse_skill_document
from src.skills.github_import import GitHubSkillImporter
from src.skills.repository import SkillRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["skills"])

# Resolve to <backend>/data/skills; kept as a module constant for isolated API tests.
SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "skills"


class SkillCreateRequest(BaseModel):
    content: str


class SkillPatchRequest(BaseModel):
    content: StrictStr | None = None
    enabled: StrictBool | None = None


class GitHubPreviewRequest(BaseModel):
    url: StrictStr
    ref: StrictStr | None = None
    path: StrictStr | None = None


class GitHubSourceRequest(BaseModel):
    owner: StrictStr
    repo: StrictStr
    commit_sha: StrictStr
    path: StrictStr = ""


class GitHubImportRequest(BaseModel):
    source: GitHubSourceRequest
    paths: list[StrictStr]


def _repository() -> SkillRepository:
    return SkillRepository(SKILLS_DIR)


def _ok(data):
    return {"success": True, "data": data}


def _skill_error(error: SkillError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "success": False,
            "error": {"code": error.code, "message": sanitize_error(str(error))},
        },
    )


def _internal_error(error: Exception, operation: str) -> JSONResponse:
    logger.exception("skill %s failed", operation)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {"code": "SKILL_ERROR", "message": sanitize_error(str(error))},
        },
    )


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex


def _create_legacy_skill(repository: SkillRepository, content: str) -> dict:
    parsed = parse_skill_document(
        content,
        directory_name=None,
        filename=LEGACY_FILENAME,
    )
    metadata_name = parsed.metadata.get("name")
    skill_id = (
        _slugify(metadata_name)
        if isinstance(metadata_name, str) and metadata_name.strip()
        else uuid.uuid4().hex
    )
    return repository.create_legacy_skill(skill_id, content)


@router.get("/skills")
async def list_skills(user: dict = Depends(current_user_optional)) -> dict:
    """List metadata and instruction text for locally installed skills."""
    try:
        skills = await asyncio.to_thread(_repository().list_skills)
        return _ok({"skills": skills})
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001 - isolate malformed package failures
        return _internal_error(exc, "list")


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, user: dict = Depends(current_user_optional)) -> dict:
    try:
        skill = await asyncio.to_thread(_repository().get_skill, skill_id)
        return _ok(skill)
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "get")


@router.post("/skills")
async def create_skill(payload: SkillCreateRequest, user: dict = Depends(current_user)) -> dict:
    """Preserve the existing editor flow by creating a legacy skill package."""
    try:
        skill = await asyncio.to_thread(
            _create_legacy_skill,
            _repository(),
            payload.content,
        )
        return _ok(skill)
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "create")


@router.patch("/skills/{skill_id}")
async def update_skill(
    skill_id: str,
    payload: SkillPatchRequest,
    user: dict = Depends(current_user),
) -> dict:
    try:
        skill = await asyncio.to_thread(
            _repository().update_skill,
            skill_id,
            content=payload.content,
            enabled=payload.enabled,
        )
        return _ok(skill)
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "update")


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, user: dict = Depends(current_user)) -> dict:
    try:
        await asyncio.to_thread(_repository().delete_skill, skill_id)
        return _ok({"id": skill_id})
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "delete")


@router.patch("/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, user: dict = Depends(current_user)) -> dict:
    try:
        enabled = await asyncio.to_thread(_repository().toggle_enabled, skill_id)
        return _ok({"id": skill_id, "enabled": enabled})
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "toggle")


@router.post("/skills/github/preview")
async def preview_github_skill(
    payload: GitHubPreviewRequest,
    user: dict = Depends(current_user),
) -> dict:
    try:
        importer = GitHubSkillImporter(_repository())
        preview = await asyncio.to_thread(
            importer.preview,
            payload.url,
            ref=payload.ref,
            path=payload.path,
        )
        return _ok(preview)
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "GitHub preview")


@router.post("/skills/github/import")
async def import_github_skills(
    payload: GitHubImportRequest,
    user: dict = Depends(current_user),
) -> dict:
    try:
        importer = GitHubSkillImporter(_repository())
        skills = await asyncio.to_thread(
            importer.import_skills,
            payload.source.model_dump(),
            payload.paths,
        )
        return _ok({"skills": skills})
    except SkillError as exc:
        return _skill_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _internal_error(exc, "GitHub import")
