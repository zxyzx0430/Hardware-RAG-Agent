"""The application servers only bind to loopback addresses."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.local_network import validate_local_bind_host


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost", " LOCALHOST "])
def test_validate_local_bind_host_accepts_loopback(host: str) -> None:
    assert validate_local_bind_host(host) == host.strip()


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.50", "10.0.0.8", "agent-host", "", "   ", None],
)
def test_validate_local_bind_host_rejects_non_loopback(host) -> None:
    with pytest.raises(ValueError, match="only supports local binding"):
        validate_local_bind_host(host)


def test_app_main_refuses_non_loopback_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "isolated-app.db"))
    from app import main as app_main

    server = Mock()
    monkeypatch.setattr(app_main.settings, "host", "0.0.0.0")
    monkeypatch.setattr(app_main.uvicorn, "run", server)

    with pytest.raises(ValueError, match="only supports local binding"):
        app_main.main()

    server.assert_not_called()


def test_backend_cli_refuses_non_loopback_host_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    module_path = backend_root / "main.py"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    spec = importlib.util.spec_from_file_location("hardware_rag_backend_entry", module_path)
    assert spec is not None and spec.loader is not None
    backend_entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend_entry)

    server = Mock()
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=server))
    monkeypatch.setattr(sys, "argv", ["main.py", "--web", "--host", "192.168.1.50"])

    with pytest.raises(SystemExit) as exc_info:
        backend_entry.main()

    assert exc_info.value.code == 2
    server.assert_not_called()
