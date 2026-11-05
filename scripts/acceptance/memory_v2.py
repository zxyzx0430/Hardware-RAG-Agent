#!/usr/bin/env python3
"""Memory v2 evaluation runner.

Runs a deterministic, product-independent scenario harness, probes the merged
Memory v2 interfaces, optionally runs the evaluation pytest suite, and writes
``report.md`` + ``metrics.json`` under an isolated evidence directory.

Exit codes
    0  PASS     product v2 gates and every executed check passed
    1  FAIL     an executed check failed
    2  BLOCKED  no failures, but Memory v2 or a required dependency is not merged

Never installs dependencies, never calls a real model by default, never points
at the user's database.  Use ``--run-real-model`` to opt in; absent a merged
product extractor the real-model stage stays pending and makes no network call.

Typical use
    python scripts/acceptance/memory_v2.py
    python scripts/acceptance/memory_v2.py --skip-pytest
    python scripts/acceptance/memory_v2.py --with-frontend
    python scripts/acceptance/memory_v2.py --run-real-model
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":  # keep Chinese status text readable in PowerShell
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from tests.memory_v2_eval.runner import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main())
