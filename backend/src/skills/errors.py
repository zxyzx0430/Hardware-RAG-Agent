"""Typed failures raised by the skill package repository and importer."""


class SkillError(Exception):
    code = "SKILL_ERROR"
    status_code = 400

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class SkillNotFoundError(SkillError):
    code = "SKILL_NOT_FOUND"
    status_code = 404


class SkillValidationError(SkillError):
    code = "SKILL_INVALID"
    status_code = 400


class SkillConflictError(SkillError):
    code = "SKILL_EXISTS"
    status_code = 409


class SkillUnavailableError(SkillError):
    code = "SKILL_UNAVAILABLE"
    status_code = 409


class SkillVersionChangedError(SkillError):
    code = "SKILL_VERSION_CHANGED"
    status_code = 409


class SkillResourceError(SkillError):
    code = "SKILL_RESOURCE_UNSUPPORTED"
    status_code = 400


class GitHubImportError(SkillError):
    code = "GITHUB_IMPORT_FAILED"
    status_code = 502
