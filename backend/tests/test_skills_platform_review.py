"""Independent integration review of portable skill storage boundaries."""

import pytest

from src.skills.errors import SkillError, SkillUnavailableError, SkillValidationError, SkillVersionChangedError
from src.skills.format import validate_path_set
from src.skills.format import parse_skill_document
from src.skills.repository import SkillRepository


def make_package(tmp_path, *, filename="SKILL.md", resource=False):
    root = tmp_path / "skills"
    package = root / "synthetic-safe"
    package.mkdir(parents=True)
    content = "---\nname: synthetic-safe\ndescription: Synthetic review instructions\n---\nUse test data only.\n"
    (package / filename).write_text(content, encoding="utf-8")
    (package / ".enabled").write_text("", encoding="utf-8")
    if resource:
        (package / "references").mkdir()
        (package / "references" / "note.md").write_text("Synthetic note", encoding="utf-8")
    return SkillRepository(root), package


@pytest.mark.parametrize("filename,expected_format", [("SKILL.md", "standard"), ("skill.md", "legacy")])
def test_single_manifest_is_not_ambiguous_on_windows(tmp_path, filename, expected_format):
    repo, _ = make_package(tmp_path, filename=filename)
    skill = repo.get_skill("synthetic-safe")
    assert skill["format"] == expected_format
    assert skill["enabled"] is True
    assert not any(issue["code"] == "SKILL_FILE_AMBIGUOUS" for issue in skill["issues"])
    assert repo.load_skill("synthetic-safe")["content"].strip() == "Use test data only."


def test_snapshot_hash_does_not_grant_access_after_disable(tmp_path):
    repo, _ = make_package(tmp_path, resource=True)
    skill = repo.get_skill("synthetic-safe")
    repo.set_enabled("synthetic-safe", False)
    with pytest.raises(SkillUnavailableError):
        repo.load_skill("synthetic-safe", skill["content_hash"])
    with pytest.raises(SkillUnavailableError):
        repo.read_skill_resource("synthetic-safe", "references/note.md", skill["content_hash"])


@pytest.mark.parametrize("paths", [["folder/note.md", "folder"], ["folder", "folder/note.md"]])
def test_file_directory_collision_is_order_independent(paths):
    with pytest.raises(SkillValidationError):
        validate_path_set(paths)


def test_nfkc_portability_collision_is_rejected():
    with pytest.raises(SkillValidationError):
        validate_path_set(["references/A.md", "references/Ａ.md"])


def test_wrong_case_reference_does_not_depend_on_windows(tmp_path):
    repo, _ = make_package(tmp_path, resource=True)
    skill = repo.get_skill("synthetic-safe")
    with pytest.raises(SkillError):
        repo.read_skill_resource("synthetic-safe", "references/NOTE.md", skill["content_hash"])


def test_recursive_yaml_cannot_create_non_serializable_skill_metadata():
    raw = "---\nname: synthetic-safe\ndescription: Synthetic\nx: &a [*a]\n---\nHello"
    with pytest.raises(SkillValidationError):
        parse_skill_document(raw, directory_name="synthetic-safe", filename="SKILL.md")


def test_bad_package_does_not_break_valid_package_listing(tmp_path):
    repo, _ = make_package(tmp_path)
    bad = repo.root / "synthetic-bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text(
        "---\nname: synthetic-bad\ndescription: Synthetic\nx: &a [*a]\n---\nHello",
        encoding="utf-8",
    )
    (bad / ".enabled").write_text("", encoding="utf-8")
    listed = {skill["id"]: skill for skill in repo.list_skills()}
    assert listed["synthetic-safe"]["enabled"] is True
    assert listed["synthetic-bad"]["compatibility_status"] == "review_required"
    assert listed["synthetic-bad"]["enabled"] is False
    assert [skill["id"] for skill in repo.list_enabled_skills()] == ["synthetic-safe"]


def test_reference_edit_invalidates_the_request_snapshot(tmp_path):
    repo, package = make_package(tmp_path, resource=True)
    snapshot = repo.snapshot_skills(["synthetic-safe"])[0]
    (package / "references" / "note.md").write_text("Synthetic changed note", encoding="utf-8")
    with pytest.raises(SkillVersionChangedError):
        repo.load_skill(snapshot.id, snapshot.content_hash)
    with pytest.raises(SkillVersionChangedError):
        repo.read_skill_resource(snapshot.id, "references/note.md", snapshot.content_hash)
