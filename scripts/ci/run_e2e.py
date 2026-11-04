"""Run W5 E2E skeletons with an isolated data root.

This script is owned by W5. W1 owns .github/workflows/ci.yml; wiring this
runner into CI is a separate change.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
TEMP_BASE = Path(os.environ.get("W5_E2E_TEMP_ROOT", "E:/DevCaches/Temp"))


def _environment(run_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "W5_E2E": "1",
        "SQLITE_DB_PATH": str(run_root / "e2e.db"),
        "AGENT_CHECKPOINTER_TYPE": "memory",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "ANONYMIZED_TELEMETRY": "False",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONUTF8": "1",
        "TEMP": str(run_root),
        "TMP": str(run_root),
    })
    return env


def _run(command: list[str], env: dict[str, str]) -> int:
    return subprocess.run(command, cwd=BACKEND, env=env).returncode


def main() -> int:
    TEMP_BASE.mkdir(parents=True, exist_ok=True)
    run_root = Path(tempfile.mkdtemp(prefix="w5-e2e-", dir=TEMP_BASE))
    env = _environment(run_root)
    print(f"W5 E2E root: {run_root}")

    init = _run(
        [sys.executable, "-c", "from app.db.database import init_db; init_db()"],
        env,
    )
    if init:
        return init

    return _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            "-p",
            "pytest_timeout",
        ],
        env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
