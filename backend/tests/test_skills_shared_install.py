"""Isolated regressions for shared standard-skill storage and installs."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import shutil
import zipfile

import pytest

from src.skills.errors import SkillConflictError, SkillUnavailableError, SkillValidationError
from src.skills.repository import SkillRepository


def _external_staging_root(tmp_path: Path) -> Path:
    """Mirror the platform test helper so staging stays outside the source copy."""
    project_root = Path(__file__).resolve().parents[3]
    candidate = tmp_path.resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError:
        return candidate / "staging"
    while True:
        parent = candidate.parent
        try:
            parent.relative_to(project_root)
        except ValueError:
            return parent / "skill-staging" / tmp_path.name
        candidate = parent


def _repository(tmp_path: Path) -> SkillRepository:
    roots = tmp_path / "isolated"
    return SkillRepository(
        scope="project",
        project_root=roots / "project" / ".agents" / "skills",
        user_root=roots / "user" / ".agents" / "skills",
        legacy_root=roots / "legacy-skills",
        staging_root=_external_staging_root(tmp_path),
        claude_project_root=roots / "project" / ".claude" / "skills",
        claude_user_root=roots / "user" / ".claude" / "skills",
    )


def _manifest(name: str, description: str = "Synthetic instructions") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\nUse isolated test data only.\n".encode()


def test_hand_placed_standard_package_loads_without_host_state(tmp_path):
    repository = _repository(tmp_path)
    package = repository.project_root / "hand-placed"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_bytes(_manifest("hand-placed"))
    (package / "references").mkdir()
    (package / "references" / "raw.bin").write_bytes(b"\x00\xffraw")
    (package / "scripts").mkdir()
    script_text = "#!/bin/sh\nprintf 'read only\\n'\n"
    (package / "scripts" / "inspect.sh").write_bytes(script_text.encode("utf-8"))

    enabled = repository.list_enabled_skills()
    assert [item["id"] for item in enabled] == ["hand-placed"]
    assert (package / ".enabled").exists() is False
    assert (repository.project_root.parent / "skills-state").exists() is False

    snapshot = repository.snapshot_skills(["hand-placed"])[0]
    loaded = repository.load_skill("hand-placed", snapshot.content_hash)
    assert loaded["content"] == "Use isolated test data only."
    assert loaded["content_hash"] == snapshot.content_hash
    assert repository.read_skill_resource("hand-placed", "scripts/inspect.sh", snapshot.content_hash) == script_text
    assert (package / "references" / "raw.bin").read_bytes() == b"\x00\xffraw"

    user_scoped = repository.for_scope("user")
    assert user_scoped.root == repository.user_root
    assert user_scoped.roots == repository.roots
    assert user_scoped.staging_root == repository.staging_root
    assert user_scoped.claude_roots == repository.claude_roots
    injected = tmp_path / "explicit-injections"
    isolated = SkillRepository(
        tmp_path / "explicit-root",
        scope="project",
        staging_root=_external_staging_root(tmp_path),
        project_root=injected / "project" / "skills",
        user_root=injected / "user" / "skills",
        legacy_root=injected / "legacy" / "skills",
        claude_project_root=injected / "project" / ".claude" / "skills",
        claude_user_root=injected / "user" / ".claude" / "skills",
    )
    with pytest.raises(SkillValidationError) as error:
        isolated.for_scope("user")
    assert error.value.code == "SKILL_SCOPE_UNAVAILABLE"


def test_unmanaged_standard_defaults_enabled_but_state_and_legacy_rules_win(tmp_path):
    repository = _repository(tmp_path)
    standard = repository.project_root / "manual-skill"
    standard.mkdir(parents=True)
    (standard / "SKILL.md").write_bytes(_manifest("manual-skill"))

    invalid = repository.project_root / "bad-manual-skill"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_bytes(_manifest("bad-manual-skill", "   "))

    legacy = repository.legacy_root / "old-skill"
    legacy.mkdir(parents=True)
    (legacy / "skill.md").write_bytes(_manifest("old-skill"))

    skills = {item["id"]: item for item in repository.list_skills()}
    assert skills["manual-skill"]["enabled"] is True
    assert skills["bad-manual-skill"]["compatibility_status"] == "review_required"
    assert skills["bad-manual-skill"]["enabled"] is False
    assert skills["old-skill"]["enabled"] is False

    repository.set_enabled("manual-skill", False)
    assert repository.get_skill("manual-skill")["enabled"] is False
    with pytest.raises(SkillUnavailableError):
        repository.load_skill("manual-skill")


def test_same_hash_package_created_by_rename_race_is_not_rolled_back(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    raw_manifest = _manifest("atomic-skill")
    expected_hash = hashlib.sha256(
        b"SKILL.md\x00" + raw_manifest + b"\x00"
    ).hexdigest()
    real_rename = os.rename
    raced_destinations: list[Path] = []

    def race_with_identical_package(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.name.startswith(".skill-stage-") and destination_path.name == "atomic-skill":
            shutil.copytree(source_path, destination_path)
            raced_destinations.append(destination_path)
            raise FileExistsError("synthetic concurrent publish")
        return real_rename(source, destination)

    monkeypatch.setattr(os, "rename", race_with_identical_package)
    with pytest.raises(SkillConflictError):
        repository.install_package(
            {"SKILL.md": raw_manifest},
            {"owner": "test-owner", "repo": "test-repo", "commit_sha": "a" * 40, "path": ""},
        )

    destination = repository.project_root / "atomic-skill"
    assert raced_destinations == [destination]
    assert destination.is_dir()
    assert (destination / "SKILL.md").read_bytes() == raw_manifest
    assert repository._hash_directory(destination) == expected_hash
    assert not (repository.project_root.parent / "skills-state" / "atomic-skill.json").exists()


def test_scope_priority_and_user_install_rejects_visible_duplicate(tmp_path):
    repository = _repository(tmp_path)
    skill_id = "shared-skill"
    project_package = repository.project_root / skill_id
    user_package = repository.user_root / skill_id
    project_package.mkdir(parents=True)
    user_package.mkdir(parents=True)
    (project_package / "SKILL.md").write_bytes(_manifest(skill_id, "Project copy"))
    (user_package / "SKILL.md").write_bytes(_manifest(skill_id, "User copy"))
    user_manifest = (user_package / "SKILL.md").read_bytes()

    visible = repository.get_skill(skill_id)
    user_repository = repository.for_scope("user")
    actual_user = user_repository.get_skill(skill_id, scope="user")
    assert visible["scope"] == "project"
    assert visible["install_path"] == str(project_package)
    assert actual_user["enabled"] is True
    assert actual_user["scope"] == "user"
    assert actual_user["install_path"] == str(user_package)
    assert visible["scope_conflicts"][0]["scope"] == "user"

    with pytest.raises(SkillConflictError):
        user_repository.install_package(
            {"SKILL.md": _manifest(skill_id)},
            {"owner": "test-owner", "repo": "test-repo", "commit_sha": "a" * 40, "path": ""},
        )
    assert (user_package / "SKILL.md").read_bytes() == user_manifest
    assert (project_package / "SKILL.md").read_bytes() == _manifest(skill_id, "Project copy")

    user_only = user_repository.install_package(
        {"SKILL.md": _manifest("user-only-skill")},
        {"owner": "test-owner", "repo": "test-repo", "commit_sha": "e" * 40, "path": ""},
    )
    assert user_only["scope"] == "user"
    assert user_only["install_path"] == str(repository.user_root / "user-only-skill")
    assert user_only["content_hash"]
    assert user_only["claude_adapter"] is False


def test_claude_copy_is_owned_deduplicated_and_never_overwrites_or_deletes_edits_user_changes(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    adapter_root = repository.claude_roots["project"]
    adapter_root.mkdir(parents=True)
    occupied = adapter_root / "occupied-skill"
    occupied.mkdir()
    occupied_manifest = _manifest("occupied-skill")
    (occupied / "SKILL.md").write_bytes(occupied_manifest)

    with pytest.raises(SkillConflictError):
        repository.install_package(
            {"SKILL.md": occupied_manifest},
            {"owner": "test-owner", "repo": "test-repo", "commit_sha": "a" * 40, "path": ""},
            claude_adapter=True,
        )
    assert (occupied / "SKILL.md").read_bytes() == occupied_manifest
    assert not (repository.project_root / "occupied-skill").exists()

    original_symlink_to = Path.symlink_to

    def fail_managed_symlink(path, target, target_is_directory=False):
        if path.parent == adapter_root and path.name == "managed-copy":
            raise OSError("synthetic symlink permission failure")
        return original_symlink_to(path, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", fail_managed_symlink)
    manifest = _manifest("managed-copy")
    script = b"#!/bin/sh\nprintf 'preserved\\n'\n"
    installed = repository.install_package(
        {"SKILL.md": manifest, "scripts/run.sh": script},
        {"owner": "test-owner", "repo": "test-repo", "commit_sha": "b" * 40, "path": ""},
        claude_adapter=True,
    )
    managed_adapter = adapter_root / "managed-copy"
    assert installed["scope"] == "project"
    assert installed["install_path"] == str(repository.project_root / "managed-copy")
    assert installed["claude_adapter"] is True
    assert managed_adapter.is_dir() and not managed_adapter.is_symlink()
    assert (managed_adapter / "SKILL.md").read_bytes() == manifest
    assert (managed_adapter / "scripts" / "run.sh").read_bytes() == script
    assert [item["id"] for item in repository.list_skills()].count("managed-copy") == 1
    clean = repository.install_package(
        {"SKILL.md": _manifest("managed-cleanup")},
        {"owner": "test-owner", "repo": "test-repo", "commit_sha": "d" * 40, "path": ""},
        claude_adapter=True,
    )
    clean_adapter = adapter_root / "managed-cleanup"
    clean_state = repository.project_root.parent / "skills-state" / "managed-cleanup.json"
    assert clean["claude_adapter"] is True
    assert clean_adapter.exists()
    repository.delete_skill("managed-cleanup")
    assert not clean_adapter.exists()
    assert not (repository.project_root / "managed-cleanup").exists()
    assert not clean_state.exists()

    (managed_adapter / "scripts" / "run.sh").write_bytes(b"user-edited copy")
    with pytest.raises(SkillConflictError):
        repository.update_skill("managed-copy", content=manifest.decode().replace("test data only", "edited"))
    with pytest.raises(SkillConflictError):
        repository.delete_skill("managed-copy")
    assert (repository.project_root / "managed-copy" / "SKILL.md").read_bytes() == manifest
    assert (managed_adapter / "scripts" / "run.sh").read_bytes() == b"user-edited copy"
    assert (repository.project_root.parent / "skills-state" / "managed-copy.json").is_file()


def test_delete_clears_owned_state_for_reinstall_and_exports_standard_and_legacy(tmp_path):
    repository = _repository(tmp_path)
    skill_id = "exportable-skill"
    manifest = _manifest(skill_id)
    binary = b"\x00\x80\xffresource"
    script = b"#!/bin/sh\necho preserved\n"
    source = {"owner": "test-owner", "repo": "test-repo", "commit_sha": "c" * 40, "path": ""}
    installed = repository.install_package(
        {"SKILL.md": manifest, "references/raw.bin": binary, "scripts/run.sh": script},
        source,
    )
    state_path = repository.project_root.parent / "skills-state" / f"{skill_id}.json"
    assert installed["enabled"] is True
    assert state_path.is_file()
    installed_package = repository.project_root / skill_id
    assert not (installed_package / ".enabled").exists()
    assert not (installed_package / ".skill-import.json").exists()

    standard_zip = zipfile.ZipFile(io.BytesIO(repository.export_skill(skill_id, scope="project")))
    standard_root = f"{skill_id}/"
    assert standard_zip.read(standard_root + "SKILL.md") == manifest
    assert standard_zip.read(standard_root + "references/raw.bin") == binary
    assert standard_zip.read(standard_root + "scripts/run.sh") == script
    assert not any("skills-state" in name or name.endswith(".enabled") for name in standard_zip.namelist())

    repository.delete_skill(skill_id)
    assert not (repository.project_root / skill_id).exists()
    assert not state_path.exists()
    reinstalled = repository.install_package(
        {"SKILL.md": manifest, "references/raw.bin": binary, "scripts/run.sh": script},
        source,
    )
    assert reinstalled["content_hash"] == installed["content_hash"]

    legacy_id = "legacy-source"
    legacy_package = repository.legacy_root / legacy_id
    legacy_package.mkdir(parents=True)
    legacy_manifest = b"Legacy compatibility instructions.\n"
    legacy_resource = b"\x00legacy-bytes"
    (legacy_package / "skill.md").write_bytes(legacy_manifest)
    (legacy_package / "references").mkdir()
    (legacy_package / "references" / "raw.bin").write_bytes(legacy_resource)
    legacy_zip = zipfile.ZipFile(
        io.BytesIO(repository.export_skill(legacy_id, scope="legacy"))
    )
    exported_legacy_manifest = legacy_zip.read(f"{legacy_id}/SKILL.md")
    assert exported_legacy_manifest.startswith(b"---\n")
    assert b"name: legacy-source" in exported_legacy_manifest
    assert b"description:" in exported_legacy_manifest
    assert legacy_zip.read(f"{legacy_id}/references/raw.bin") == legacy_resource
    assert (legacy_package / "skill.md").read_bytes() == legacy_manifest
    assert (legacy_package / "references" / "raw.bin").read_bytes() == legacy_resource
