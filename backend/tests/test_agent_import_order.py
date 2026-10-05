"""Agent exports must be available regardless of import order."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("import_order", ["factory_first", "routes_first"])
def test_agent_route_exports_survive_import_order(import_order: str) -> None:
    if import_order == "factory_first":
        imports = """
import src.agent.agent_factory as factory
import app.api.chat_routes as routes
"""
    else:
        imports = """
import app.api.chat_routes as routes
import src.agent.agent_factory as factory
"""

    code = f"""
{imports}
assert routes._AGENT_PATH_AVAILABLE is True, routes._AGENT_PATH_AVAILABLE
assert callable(routes.create_hardware_agent)
assert callable(factory.create_hardware_agent)
"""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    for name in tuple(env):
        if "API_KEY" in name or "API_TOKEN" in name:
            env[name] = ""

    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0, (
        f"import order {import_order!r} failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
