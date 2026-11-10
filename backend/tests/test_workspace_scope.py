from src.agent.exceptions import ToolContext
from src.agent.path_guard import PROJECT_ROOT, resolve_agent_path, validate_path
from src.agent.request_context import activate_tool_context


def test_external_workspace_changes_relative_path_resolution(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    with activate_tool_context(ToolContext(workspace_root=str(external))):
        resolved = resolve_agent_path("firmware/main.c")
        assert resolved == str(external / "firmware" / "main.c")
        ok, reason = validate_path("firmware/main.c", is_write=True)
        assert ok, reason
        outside = PROJECT_ROOT / "README.md"
        ok, _ = validate_path(str(outside), is_write=False)
        assert ok is False
