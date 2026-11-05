"""One-shot, request-bound installation of a preflighted public GitHub Skill."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import hashlib
import re
import threading
import time
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext
from src.skills.errors import SkillConflictError, SkillError, SkillValidationError
from src.skills.format import normalize_relative_path
from src.skills.github_import import GitHubSkillImporter, GitHubTransport
from src.skills.repository import SkillRepository

INSTALL_DECISION_SOURCE = "explicit_chat_skill_install"
INSTALL_GRANT_TTL_SECONDS = 30 * 60
_OPERATION_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_DIRECT_INSTALL_RE = re.compile(
    r"^\s*(?:(?:please|can you|could you)\s+|请(?:帮我)?|帮我|麻烦(?:帮我)?\s*)?"
    r"(?:从\s*(?:公共\s*)?GitHub\s*)?"
    r"(?:安装(?:这个技能|该技能|这个链接|该链接)?|install)"
    r"(?=$|[\s:：,，。]|并启用|后启用)",
    re.IGNORECASE,
)
_GITHUB_LINK_RE = re.compile(
    r"https://github\.com/(?:(?!https?://|以及)[^\s<>，。；、])+",
    re.IGNORECASE,
)
_URL_TRAILING_PUNCTUATION = ".,;!?，。；、)]}"
_ENABLE_RE = re.compile(
    r"(?:安装(?:这个技能|该技能|这个链接|该链接)?(?:并启用|后启用)|"
    r"同时启用|install\s+and\s+enable)",
    re.IGNORECASE,
)
_DISABLE_RE = re.compile(
    r"(?:不要.{0,12}启用|不(?:要|再|需要)?.{0,12}启用|先别.{0,12}启用|"
    r"do not\s+(?:install\s+and\s+)?enable|don't\s+(?:install\s+and\s+)?enable|without enabling)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExplicitInstallIntent:
    url: str
    enable: bool


@dataclass(frozen=True)
class InstallRequestBinding:
    principal_fingerprint: str
    session_id: str
    turn_fingerprint: str


@dataclass
class _GrantState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    consumed: bool = False


@dataclass(frozen=True)
class SkillInstallGrant:
    """Immutable host preview plus a single-use permission for this chat turn."""

    principal_fingerprint: str
    session_id: str
    turn_fingerprint: str
    operation_id: str
    owner: str
    repo: str
    commit_sha: str
    preview_path: str
    candidate_path: str
    skill_id: str
    compatibility_status: str
    enable_requested: bool
    expires_at: float
    _state: _GrantState = field(default_factory=_GrantState, repr=False, compare=False)

    @property
    def binding(self) -> InstallRequestBinding:
        return InstallRequestBinding(
            self.principal_fingerprint,
            self.session_id,
            self.turn_fingerprint,
        )

    def has_valid_preview(self) -> bool:
        if not all(isinstance(value, str) for value in (
            self.principal_fingerprint,
            self.session_id,
            self.turn_fingerprint,
            self.operation_id,
            self.owner,
            self.repo,
            self.commit_sha,
            self.preview_path,
            self.candidate_path,
            self.skill_id,
            self.compatibility_status,
        )) or not self.session_id or not isinstance(self.enable_requested, bool):
            return False
        try:
            preview_path = normalize_relative_path(self.preview_path, allow_root=True)
            candidate_path = normalize_relative_path(self.candidate_path, allow_root=True)
        except SkillValidationError:
            return False
        expected_operation_id = hashlib.sha256(
            f"{self.principal_fingerprint}\0{self.session_id}\0{self.turn_fingerprint}".encode("utf-8")
        ).hexdigest()
        return bool(
            _OPERATION_ID_RE.fullmatch(self.operation_id)
            and _constant_time_equal(self.operation_id, expected_operation_id)
            and _OPERATION_ID_RE.fullmatch(self.principal_fingerprint)
            and _OPERATION_ID_RE.fullmatch(self.turn_fingerprint)
            and _COMMIT_SHA_RE.fullmatch(self.commit_sha)
            and _OWNER_RE.fullmatch(self.owner)
            and self.owner not in {".", ".."}
            and _REPO_RE.fullmatch(self.repo)
            and self.repo not in {".", ".."}
            and _SKILL_ID_RE.fullmatch(self.skill_id)
            and candidate_path == self.candidate_path
            and preview_path == self.preview_path
            and (
                not preview_path
                or candidate_path == preview_path
                or candidate_path.startswith(preview_path + "/")
            )
            and self.compatibility_status == "supported"
            and (not self.enable_requested or self.compatibility_status == "supported")
        )

    def matches_request(
        self,
        principal_fingerprint: str | None,
        session_id: str,
        user_text: str,
    ) -> bool:
        if not principal_fingerprint or not self.has_valid_preview() or time.monotonic() >= self.expires_at:
            return False
        return (
            _constant_time_equal(self.principal_fingerprint, principal_fingerprint)
            and self.session_id == session_id
            and _constant_time_equal(self.turn_fingerprint, fingerprint_user_turn(user_text))
        )

    def claim_dispatch(
        self,
        tool: "SkillInstallTool",
        ctx: ToolContext,
        args: dict[str, Any],
    ) -> bool:
        binding = _ACTIVE_INSTALL_BINDING.get()
        if (
            type(tool) is not SkillInstallTool
            or ctx.request_tools.get(tool.name) is not tool
            or ctx.session_id != self.session_id
            or binding != self.binding
            or args != {}
            or not self.has_valid_preview()
            or time.monotonic() >= self.expires_at
        ):
            return False
        with self._state.lock:
            if self._state.consumed:
                return False
            self._state.consumed = True
            return True

    def was_claimed(self) -> bool:
        with self._state.lock:
            return self._state.consumed

    def execution_is_bound(self, ctx: ToolContext) -> bool:
        request_tool = ctx.request_tools.get("install_github_skill")
        return (
            self.was_claimed()
            and type(request_tool) is SkillInstallTool
            and request_tool._install_grant is self
            and ctx.session_id == self.session_id
            and _ACTIVE_INSTALL_BINDING.get() == self.binding
            and self.has_valid_preview()
            and time.monotonic() < self.expires_at
        )


_ACTIVE_INSTALL_BINDING: ContextVar[InstallRequestBinding | None] = ContextVar(
    "active_skill_install_binding", default=None,
)


@contextmanager
def activate_install_binding(grant: SkillInstallGrant | None):
    """Expose the server-held owner/turn binding only during this request stream."""
    binding = grant.binding if grant is not None else None
    token: Token[InstallRequestBinding | None] = _ACTIVE_INSTALL_BINDING.set(binding)
    try:
        yield
    finally:
        _ACTIVE_INSTALL_BINDING.reset(token)


def principal_fingerprint(user: Mapping[str, Any] | None) -> str | None:
    """Return an irreversible identity fingerprint; never retain the provider key."""
    if not isinstance(user, Mapping) or user.get("anonymous") is not False:
        return None
    provider = user.get("provider")
    api_key = user.get("api_key")
    if not isinstance(provider, str) or not provider or not isinstance(api_key, str) or not api_key:
        return None
    return hashlib.sha256(f"{provider}\0{api_key}".encode("utf-8")).hexdigest()


def fingerprint_user_turn(user_text: str) -> str:
    return hashlib.sha256(user_text.encode("utf-8")).hexdigest()


def parse_explicit_install_intent(user_text: Any) -> ExplicitInstallIntent | None:
    """Recognize only a direct imperative in the current plain-text user turn."""
    if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 8_192:
        return None
    first_line = user_text.splitlines()[0]
    if not _DIRECT_INSTALL_RE.match(first_line):
        return None
    links = _GITHUB_LINK_RE.findall(user_text)
    if len(links) != 1:
        return ExplicitInstallIntent(url="", enable=False)
    url = links[0].rstrip(_URL_TRAILING_PUNCTUATION)
    if not url:
        return ExplicitInstallIntent(url="", enable=False)
    enable = bool(_ENABLE_RE.search(user_text)) and not bool(_DISABLE_RE.search(user_text))
    return ExplicitInstallIntent(url=url, enable=enable)


@dataclass(frozen=True)
class InstallPreparation:
    tool: "SkillInstallTool | None" = None
    response: str | None = None


class SkillInstallService:
    """Prepare a fixed GitHub preview and execute it through the existing importer."""

    def __init__(
        self,
        repository: SkillRepository | None = None,
        transport: GitHubTransport | None = None,
    ) -> None:
        self.repository = repository or SkillRepository()
        self.transport = transport
        self.importer = GitHubSkillImporter(self.repository, transport)

    def prepare(
        self,
        user_text: str,
        user: Mapping[str, Any] | None,
        session_id: str,
    ) -> InstallPreparation | None:
        intent = parse_explicit_install_intent(user_text)
        if intent is None:
            return None
        if not intent.url:
            return InstallPreparation(response="请提供一个公共 GitHub 仓库或技能目录链接；没有执行安装。")
        principal = principal_fingerprint(user)
        if principal is None:
            return InstallPreparation(response="当前聊天没有可验证的登录主体；没有执行技能安装。请先完成认证后重试。")

        turn_fingerprint = fingerprint_user_turn(user_text)
        operation_id = hashlib.sha256(
            f"{principal}\0{session_id}\0{turn_fingerprint}".encode("utf-8")
        ).hexdigest()
        try:
            location = self.importer._parse_url(intent.url)
            existing = self.repository.find_import_by_operation_id(operation_id)
            if existing is not None:
                source = existing.get("source")
                if not isinstance(source, dict) or (
                    source.get("owner", "").casefold() != location.owner.casefold()
                    or source.get("repo", "").casefold() != location.repo.casefold()
                ):
                    raise SkillConflictError("This install operation ID belongs to a different GitHub source.")
                candidate_path = source.get("path")
                commit_sha = source.get("commit_sha")
                if not isinstance(candidate_path, str) or not isinstance(commit_sha, str):
                    raise SkillConflictError("The previous install record is incomplete.")
                grant = self._grant(
                    principal, session_id, turn_fingerprint, operation_id,
                    location.owner, location.repo, commit_sha, candidate_path,
                    candidate_path, str(existing.get("id", "")),
                    str(existing.get("compatibility_status", "")),
                    intent.enable,
                )
                if not grant.has_valid_preview():
                    raise SkillConflictError("The previous install record is not compatible with this operation.")
                return InstallPreparation(tool=SkillInstallTool(grant, self))

            preview_ref = None
            preview_path = None
            if location.tree_tail and _COMMIT_SHA_RE.fullmatch(location.tree_tail[0]):
                preview_ref = location.tree_tail[0]
                preview_path = "/".join(location.tree_tail[1:])
            preview = self.importer.preview(
                intent.url,
                ref=preview_ref,
                path=preview_path,
            )
            source = preview.get("source")
            candidates = preview.get("candidates")
            if not isinstance(source, dict) or not isinstance(candidates, list):
                raise SkillValidationError("GitHub preview returned an invalid result.", code="GITHUB_PREVIEW_INVALID")
            if len(candidates) != 1:
                if not candidates:
                    return InstallPreparation(response="该 GitHub 位置没有可导入的技能；没有执行安装。")
                choices = [
                    f"- {item.get('name') or '未命名'}：`{item.get('path') or '.'}`（{item.get('compatibility_status') or '未知兼容状态'}）"
                    for item in candidates if isinstance(item, dict)
                ]
                return InstallPreparation(
                    response="找到多个技能候选，请在新消息中给出其中一个候选的 GitHub 技能目录链接后重试；没有执行安装。\n"
                    + "\n".join(choices)
                )
            candidate = candidates[0]
            if not isinstance(candidate, dict):
                raise SkillValidationError("GitHub preview returned an invalid candidate.", code="GITHUB_PREVIEW_INVALID")
            if candidate.get("compatibility_status") != "supported":
                return InstallPreparation(
                    response=(
                        f"技能 `{candidate.get('name') or candidate.get('path') or '候选技能'}` 的兼容状态为 "
                        f"{candidate.get('compatibility_status') or '未知'}；请先在设置页检查并处理问题。没有执行安装。"
                    )
                )
            owner = source.get("owner")
            repo = source.get("repo")
            commit_sha = source.get("commit_sha")
            preview_path = source.get("path", "")
            candidate_path = candidate.get("path")
            skill_id = candidate.get("name")
            if not all(isinstance(value, str) for value in (owner, repo, commit_sha, preview_path, candidate_path, skill_id)):
                raise SkillValidationError("GitHub preview returned incomplete source data.", code="GITHUB_PREVIEW_INVALID")
            if owner.casefold() != location.owner.casefold() or repo.casefold() != location.repo.casefold():
                raise SkillValidationError("GitHub preview source did not match the requested repository.", code="GITHUB_PREVIEW_INVALID")
            grant = self._grant(
                principal, session_id, turn_fingerprint, operation_id,
                owner, repo, commit_sha, preview_path, candidate_path,
                skill_id, candidate["compatibility_status"], intent.enable,
            )
            if not grant.has_valid_preview():
                raise SkillValidationError("GitHub preview did not produce a safe pinned candidate.", code="GITHUB_PREVIEW_INVALID")
            self.repository.assert_available([skill_id])
            return InstallPreparation(tool=SkillInstallTool(grant, self))
        except SkillError as exc:
            if isinstance(exc, SkillConflictError):
                return InstallPreparation(response=f"该技能与本机已有安装冲突：{exc}。没有覆盖任何文件。")
            return InstallPreparation(response=f"无法预览该 GitHub 技能（{exc.code}）；没有执行安装。")

    def execute(self, grant: SkillInstallGrant) -> dict[str, Any]:
        if (
            not grant.was_claimed()
            or _ACTIVE_INSTALL_BINDING.get() != grant.binding
            or time.monotonic() >= grant.expires_at
        ):
            raise SkillValidationError(
                "Install execution is not bound to the active chat authorization.",
                code="SKILL_INSTALL_GRANT_INVALID",
                status_code=403,
            )
        lock = _OPERATION_LOCKS[int(grant.operation_id[:8], 16) % len(_OPERATION_LOCKS)]
        with lock:
            existing = self.repository.find_import_by_operation_id(grant.operation_id)
            if existing is not None:
                self._assert_same_operation_record(existing, grant)
                return _format_install_result(existing, already_installed=True)
            source = {
                "owner": grant.owner,
                "repo": grant.repo,
                "commit_sha": grant.commit_sha,
                "path": grant.preview_path,
            }
            try:
                installed = self.importer.import_skills(
                    source,
                    [grant.candidate_path],
                    operation_id=grant.operation_id,
                    enable=grant.enable_requested,
                )
            except Exception as exc:
                if isinstance(exc, SkillError) and isinstance(exc, SkillConflictError):
                    raced = self.repository.find_import_by_operation_id(grant.operation_id)
                    if raced is not None:
                        self._assert_same_operation_record(raced, grant)
                        return _format_install_result(raced, already_installed=True)
                code = exc.code if isinstance(exc, SkillError) else "SKILL_INSTALL_FAILED"
                status_code = exc.status_code if isinstance(exc, SkillError) else 500
                raise SkillValidationError(
                    f"The GitHub skill install failed ({code}); no existing package was overwritten.",
                    code=code,
                    status_code=status_code,
                ) from exc
            if len(installed) != 1:
                raise SkillValidationError("The install did not produce exactly one skill.", code="SKILL_INSTALL_FAILED")
            self._assert_same_operation_record(installed[0], grant)
            return _format_install_result(installed[0], already_installed=False)

    @staticmethod
    def _assert_same_operation_record(record: Mapping[str, Any], grant: SkillInstallGrant) -> None:
        source = record.get("source")
        if not isinstance(source, dict) or (
            record.get("id") != grant.skill_id
            or source.get("owner", "").casefold() != grant.owner.casefold()
            or source.get("repo", "").casefold() != grant.repo.casefold()
            or source.get("commit_sha") != grant.commit_sha
            or source.get("path") != grant.candidate_path
        ):
            raise SkillConflictError("This operation ID is already bound to a different pinned skill.")

    @staticmethod
    def _grant(
        principal: str,
        session_id: str,
        turn_fingerprint: str,
        operation_id: str,
        owner: str,
        repo: str,
        commit_sha: str,
        preview_path: str,
        candidate_path: str,
        skill_id: str,
        compatibility_status: str,
        enable_requested: bool,
    ) -> SkillInstallGrant:
        return SkillInstallGrant(
            principal_fingerprint=principal,
            session_id=session_id,
            turn_fingerprint=turn_fingerprint,
            operation_id=operation_id,
            owner=owner,
            repo=repo,
            commit_sha=commit_sha,
            preview_path=preview_path,
            candidate_path=candidate_path,
            skill_id=skill_id,
            compatibility_status=compatibility_status,
            enable_requested=enable_requested,
            expires_at=time.monotonic() + INSTALL_GRANT_TTL_SECONDS,
        )


class SkillInstallArgs(BaseModel):
    """The model cannot choose a source, path, commit, destination, or enable flag."""

    model_config = ConfigDict(extra="forbid", strict=True)


class SkillInstallTool(ToolSpec):
    name: str = "install_github_skill"
    description: str = (
        "Use once only when this tool is present and the current user explicitly asked to install the shown public GitHub Skill. "
        "The host already fixed the repository, candidate, commit SHA, path, destination, and whether enabling was requested; "
        "this tool accepts no arguments and cannot install other content. Do not claim success unless this tool returns success."
    )
    args_schema: type[BaseModel] = SkillInstallArgs
    # The exact user request is the authorization. ToolRouter still requires the
    # matching one-shot host grant before this bounded action can run.
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 90
    max_retries: int = 0
    _install_grant: SkillInstallGrant = PrivateAttr()
    _service: SkillInstallService = PrivateAttr()

    def __init__(self, grant: SkillInstallGrant, service: SkillInstallService):
        super().__init__()
        self._install_grant = grant
        self._service = service

    def authorize_dispatch(self, ctx: ToolContext, args: dict[str, Any]) -> bool:
        return self._install_grant.claim_dispatch(self, ctx, args)

    def request_binding(self) -> InstallRequestBinding:
        return self._install_grant.binding

    def matches_resume(self, principal: str | None, session_id: str, user_text: str) -> bool:
        return self._install_grant.matches_request(principal, session_id, user_text)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        if not self._install_grant.execution_is_bound(ctx):
            raise SkillValidationError("Install authorization is no longer valid.", code="SKILL_INSTALL_GRANT_INVALID", status_code=403)
        return await asyncio.to_thread(self._service.execute, self._install_grant)


_OPERATION_LOCKS = tuple(threading.Lock() for _ in range(64))


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _format_install_result(record: Mapping[str, Any], *, already_installed: bool) -> dict[str, Any]:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    enabled = bool(record.get("enabled"))
    status = "already installed" if already_installed else "installed"
    enabled_status = "enabled" if enabled else "disabled"
    skill_id = str(record.get("id") or "unknown")
    owner = str(source.get("owner") or "unknown")
    repo = str(source.get("repo") or "unknown")
    sha = str(source.get("commit_sha") or "unknown")
    path = str(source.get("path") or ".")
    return {
        "output": (
            f"Skill `{skill_id}` {status} from `{owner}/{repo}` at commit `{sha}` "
            f"(path `{path}`); current state: {enabled_status}. Skill files are stored as data and are not executed."
        ),
        "data": {
            "skill_id": skill_id,
            "repository": f"{owner}/{repo}",
            "commit_sha": sha,
            "path": path,
            "status": status.replace(" ", "_"),
            "enabled": enabled,
            "compatibility_status": record.get("compatibility_status"),
        },
    }
