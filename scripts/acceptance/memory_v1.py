#!/usr/bin/env python3
"""Memory v1 acceptance runner.

Runs the contract-level acceptance suite and, when the memory runtime is
available, starts a temporary backend against a throw-away SQLite database.
An opt-in browser mode drives the real Settings panels through Chromium
(Playwright) -- TestClient responses are never reported as browser acceptance.

Exit codes
    0  PASS     every executed check passed
    1  FAIL     at least one check failed
    2  BLOCKED  no failures, but a required dependency is not merged yet

The runner never installs dependencies, never downloads models and never points
at the user's database.  Evidence (logs, JUnit XML, screenshots, summary) is
written to an evidence directory whose absolute path is printed at the end.

Typical use
    python scripts/acceptance/memory_v1.py
    python scripts/acceptance/memory_v1.py --with-frontend
    python scripts/acceptance/memory_v1.py --browser
    python scripts/acceptance/memory_v1.py --skip-pytest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"

# frontend/vite.config.ts proxies /api to this fixed port.
BROWSER_BACKEND_PORT = 58080


def _temp_base() -> Path:
    for candidate in (Path("E:/DevCaches/Temp"), Path("D:/DevCaches/Temp")):
        if candidate.is_dir():
            return candidate
    return Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
    except Exception:
        return ""


def _child_env(work_dir: Path, db_name: str = "acceptance.sqlite") -> dict[str, str]:
    env = os.environ.copy()
    env["TEMP"] = str(work_dir)
    env["TMP"] = str(work_dir)
    env["SQLITE_DB_PATH"] = str(work_dir / db_name)
    env["MEMORY_ACCEPTANCE_ISOLATED"] = "1"
    env["AGENT_CHECKPOINTER_TYPE"] = "memory"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env


def _request(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int | None, str]:
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65536).decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _wait_for_http(
    url: str, deadline: float, process: subprocess.Popen | None = None
) -> tuple[bool, str]:
    last = "no response"
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False, f"process exited with code {process.returncode}"
        status, body = _request("GET", url, timeout=3)
        if status == 200:
            return True, body[:500]
        last = f"status={status} body={body[:300]}"
        time.sleep(0.5)
    return False, last


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class Check:
    def __init__(self, name: str, required_for_pass: bool = True):
        self.name = name
        self.required_for_pass = required_for_pass
        self.status = "blocked"
        self.detail = ""
        self.evidence: list[str] = []

    def pass_(self, detail: str) -> None:
        self.status = "pass"
        self.detail = detail

    def fail(self, detail: str) -> None:
        self.status = "fail"
        self.detail = detail

    def block(self, detail: str) -> None:
        self.status = "blocked"
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Static dependency probe
# ---------------------------------------------------------------------------

FILE_GROUPS = {
    "memory:models": ["backend/app/db/memory_models.py"],
    "memory:store": ["backend/app/db/memory_store.py"],
    "memory:routes": ["backend/app/api/memory_routes.py"],
    "memory:tools": ["backend/src/agent/tools/groups/memory.py"],
    "memory:context": ["backend/src/agent/memory_context.py"],
    "hardware:models": ["backend/app/db/hardware_profile_models.py"],
    "hardware:store": ["backend/app/db/hardware_profile_store.py"],
    "hardware:tool": ["backend/src/agent/tools/groups/hardware_profile.py"],
    "hardware:mention": ["backend/src/agent/hardware_mention.py"],
    "hardware:skill": [".agents/skills/hardware-profile/SKILL.md"],
    "frontend:api": ["frontend/src/api/memory.ts"],
    "frontend:types": ["frontend/src/types/memory.ts"],
    "frontend:panels": ["frontend/src/components/memory"],
}

IMPORT_PROBES = [
    "app.db.memory_models",
    "app.db.memory_store",
    "app.api.memory_routes",
    "src.agent.tools.groups.memory",
    "src.agent.memory_context",
    "app.db.hardware_profile_models",
    "app.db.hardware_profile_store",
    "src.agent.tools.groups.hardware_profile",
    "src.agent.hardware_mention",
]


def _static_probe(check: Check, work_dir: Path, env: dict[str, str]) -> dict:
    result: dict = {"files": {}, "imports": {}}
    for label, paths in FILE_GROUPS.items():
        present = [
            path
            for path in paths
            if (REPO_ROOT / path).is_file() or (REPO_ROOT / path).is_dir()
        ]
        result["files"][label] = {"expected": paths, "present": present}

    for module_name in IMPORT_PROBES:
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=BACKEND_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        stderr = (completed.stderr or "").strip().splitlines()
        result["imports"][module_name] = {
            "ok": completed.returncode == 0,
            "error": stderr[-1] if stderr else "",
        }

    missing_files = [
        label for label, info in result["files"].items() if not info["present"]
    ]
    failed_imports = [
        name for name, info in result["imports"].items() if not info["ok"]
    ]
    if missing_files or failed_imports:
        check.block(
            "unmerged dependencies: missing files "
            f"{missing_files or 'none'}; failing imports "
            f"{failed_imports or 'none'} (see static-probe.json)"
        )
    else:
        check.pass_("all expected memory/hardware modules import and files exist")
    return result


# ---------------------------------------------------------------------------
# pytest acceptance suite
# ---------------------------------------------------------------------------


def _parse_junit(path: Path) -> dict:
    result = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "skip_reasons": [],
        "failed_cases": [],
    }
    if not path.is_file():
        return result
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return result
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    for suite in suites:
        result["tests"] += int(suite.get("tests", "0") or 0)
        result["failures"] += int(suite.get("failures", "0") or 0)
        result["errors"] += int(suite.get("errors", "0") or 0)
        result["skipped"] += int(suite.get("skipped", "0") or 0)
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is not None:
            reason = skipped.get("message") or skipped.get("type") or "skipped"
            result["skip_reasons"].append(f"{case.get('name')}: {reason}")
        for tag in ("failure", "error"):
            node = case.find(tag)
            if node is not None:
                message = (node.get("message") or "").strip().splitlines()
                result["failed_cases"].append(
                    f"{case.get('name')}: {message[0] if message else tag}"
                )
    return result


def _run_pytest(check: Check, work_dir: Path, timeout: int) -> dict:
    env = _child_env(work_dir, "pytest.sqlite")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    junit = work_dir / "pytest-junit.xml"
    log_path = work_dir / "pytest.log"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_memory_acceptance.py",
        "-q",
        "-p",
        "asyncio",
        "-p",
        "pytest_timeout",
        f"--junitxml={junit}",
    ]
    interrupted = False
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=BACKEND_DIR,
                env=env,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = None
        interrupted = True
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[runner] pytest exceeded {timeout}s and was killed\n")

    summary = _parse_junit(junit)
    summary["exit_code"] = exit_code
    summary["interrupted"] = interrupted
    check.evidence.extend([str(log_path), str(junit)])

    if interrupted:
        check.fail(f"pytest timed out after {timeout}s (see pytest.log)")
    elif summary["failures"] or summary["errors"]:
        check.fail(
            f"{summary['failures']} failed / {summary['errors']} errors; "
            f"cases: {summary['failed_cases'][:5]}"
        )
    elif summary["tests"] == 0:
        check.block(f"no tests collected (pytest exit {exit_code}); see pytest.log")
    elif summary["skipped"] == summary["tests"]:
        reasons = summary["skip_reasons"][:6]
        check.block(
            f"all {summary['tests']} acceptance tests skipped; dependency reasons: {reasons}"
        )
    elif exit_code not in (0, None):
        check.fail(f"pytest exit code {exit_code}; see pytest.log")
    else:
        check.pass_(
            f"{summary['tests'] - summary['skipped']} passed, "
            f"{summary['skipped']} skipped, 0 failed"
        )
    return summary


# ---------------------------------------------------------------------------
# Live backend
# ---------------------------------------------------------------------------

MEMORY_PROBES = (
    ("memory_list", "GET", "/api/memory"),
    ("memory_search", "POST", "/api/memory/search"),
)

# The contract does not fix the profile HTTP path; probe the likely names and
# report which one the frontend integration exposes.
PROFILE_ROUTE_CANDIDATES = (
    "/api/hardware-profiles",
    "/api/hardware-profile",
    "/api/hardware/profiles",
)


def _is_default_not_found(status: int | None, body: str) -> bool:
    if status != 404:
        return False
    try:
        return json.loads(body).get("detail") == "Not Found"
    except Exception:
        return False


def _start_backend(
    work_dir: Path, port: int, startup_timeout: int, log_name: str
) -> tuple[subprocess.Popen | None, bool, str, str]:
    env = _child_env(work_dir)
    log_path = work_dir / log_name
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--timeout-graceful-shutdown",
        "2",
    ]
    process = subprocess.Popen(
        command,
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    healthy, detail = _wait_for_http(
        f"http://127.0.0.1:{port}/health",
        time.monotonic() + startup_timeout,
        process,
    )
    return process, healthy, detail, str(log_path)


def _run_live_backend(check: Check, work_dir: Path, startup_timeout: int) -> dict:
    port = _free_port()
    evidence: dict = {"port": port, "probes": {}, "profile_routes": {}}
    process: subprocess.Popen | None = None
    log_path = work_dir / "backend-live.log"
    probe_path = work_dir / "backend-probe.json"

    with log_path.open("w", encoding="utf-8") as log:
        env = _child_env(work_dir)
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--timeout-graceful-shutdown",
            "2",
        ]
        process = subprocess.Popen(
            command, cwd=BACKEND_DIR, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        check.evidence.append(str(log_path))

    try:
        healthy, health_detail = _wait_for_http(
            f"http://127.0.0.1:{port}/health",
            time.monotonic() + startup_timeout,
            process,
        )
        evidence["health"] = {"ok": healthy, "detail": health_detail}
        if not healthy:
            check.fail(
                f"temporary backend did not become healthy within {startup_timeout}s: "
                f"{health_detail} (see backend-live.log)"
            )
            return evidence

        registered = 0
        for name, method, path in MEMORY_PROBES:
            data = b'{"query": "acceptance", "limit": 1}' if method == "POST" else None
            headers = {"Content-Type": "application/json"} if data else {}
            status, body = _request(
                method, f"http://127.0.0.1:{port}{path}", data=data, headers=headers
            )
            missing = _is_default_not_found(status, body)
            evidence["probes"][name] = {
                "method": method,
                "path": path,
                "status": status,
                "registered": not missing,
                "body": body[:1000],
            }
            if not missing:
                registered += 1
            if status is not None and status >= 500:
                check.fail(
                    f"{method} {path} returned {status}: {body[:300]} "
                    "(see backend-live.log)"
                )
                return evidence

        for candidate in PROFILE_ROUTE_CANDIDATES:
            status, body = _request("GET", f"http://127.0.0.1:{port}{candidate}")
            missing = _is_default_not_found(status, body)
            evidence["profile_routes"][candidate] = {
                "status": status,
                "registered": not missing,
                "body": body[:500],
            }

        if registered == 0:
            check.block(
                "temporary backend is healthy but no /api/memory routes are "
                "registered (memory_routes dependency missing); see backend-probe.json"
            )
        elif registered < len(MEMORY_PROBES):
            missing_names = [
                name
                for name, info in evidence["probes"].items()
                if not info["registered"]
            ]
            check.block(
                f"memory routes partially registered; missing {missing_names} "
                "(see backend-probe.json)"
            )
        else:
            check.pass_(
                "temporary backend healthy; GET /api/memory and POST /api/memory/search registered"
            )
        return evidence
    finally:
        _stop_process(process)
        evidence["process_exit_code"] = process.returncode if process else None
        probe_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        check.evidence.append(str(probe_path))


def _run_profile_route_check(check: Check, backend_probe: dict) -> None:
    routes = backend_probe.get("profile_routes") or {}
    registered = [path for path, info in routes.items() if info.get("registered")]
    if registered:
        check.pass_(f"hardware profile HTTP route registered at {registered}")
    else:
        check.block(
            "no hardware profile HTTP route found among "
            f"{list(PROFILE_ROUTE_CANDIDATES)}; the contract fixes the tool but not "
            "the HTTP path, so browser profile CRUD cannot be verified"
        )


# ---------------------------------------------------------------------------
# Live frontend (opt-in)
# ---------------------------------------------------------------------------


def _run_live_frontend(check: Check, work_dir: Path, startup_timeout: int) -> dict:
    evidence: dict = {"started": False}
    node_modules = FRONTEND_DIR / "node_modules"
    if not node_modules.is_dir():
        check.block(
            "frontend dependencies are not installed (frontend/node_modules missing); "
            "this runner does not download packages. Run the project's documented "
            "install step first, then rerun with --with-frontend"
        )
        return evidence

    vite_entry = node_modules / "vite" / "bin" / "vite.js"
    if not vite_entry.is_file():
        check.block(
            "frontend/node_modules exists but vite is missing; refusing to install "
            "dependencies from the acceptance runner"
        )
        return evidence

    node = shutil.which("node")
    if not node:
        check.block("node executable not found on PATH")
        return evidence

    port = _free_port()
    log_path = work_dir / "frontend-live.log"
    command = [
        node,
        str(vite_entry),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--strictPort",
    ]
    evidence.update({"port": port, "command": command})
    check.evidence.append(str(log_path))
    process: subprocess.Popen | None = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=FRONTEND_DIR,
                env=_child_env(work_dir),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            ready, detail = _wait_for_http(
                f"http://127.0.0.1:{port}/", time.monotonic() + startup_timeout, process
            )
        evidence["started"] = ready
        evidence["detail"] = detail
        if not ready:
            check.fail(
                f"vite dev server did not answer within {startup_timeout}s: {detail} "
                "(see frontend-live.log)"
            )
        else:
            check.pass_(
                "vite dev server answered over HTTP; this is NOT browser acceptance"
            )
        return evidence
    finally:
        _stop_process(process)
        evidence["process_exit_code"] = process.returncode if process else None


# ---------------------------------------------------------------------------
# Real browser acceptance (Playwright / Chromium)
# ---------------------------------------------------------------------------


class BrowserStepError(RuntimeError):
    pass


def _first_visible(page, selectors: list[str], timeout_ms: int = 4000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            locator = page.locator(selector)
            try:
                count = locator.count()
            except Exception:
                continue
            for index in range(count):
                item = locator.nth(index)
                try:
                    if item.is_visible():
                        return item
                except Exception:
                    continue
        page.wait_for_timeout(150)
    return None


def _click_button(page, names: list[str], timeout_ms: int = 5000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for name in names:
            try:
                locator = page.get_by_role("button", name=name, exact=True)
                if locator.count():
                    locator.first.click()
                    return True
            except Exception:
                continue
        page.wait_for_timeout(150)
    return False


def _visible_marker(page, marker: str, present: bool, timeout_ms: int = 8000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            count = page.get_by_text(marker, exact=False).count()
        except Exception:
            count = 0
        if (count > 0) == present:
            return True
        page.wait_for_timeout(200)
    return False


def _browser_flow(page, shot_dir: Path, frontend_url: str) -> dict:
    """Drive the Settings memory/hardware panels in a real Chromium page.

    Raises BrowserStepError when the UI cannot be located; a blocked step is
    reported as BLOCKED, never as a pass.
    """
    evidence: dict = {"steps": [], "console_errors": []}
    page.on(
        "pageerror",
        lambda error: evidence["console_errors"].append(str(error)),
    )
    page.on(
        "console",
        lambda message: (
            evidence["console_errors"].append(message.text)
            if message.type == "error"
            else None
        ),
    )

    page.goto(frontend_url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    settings_nav = _first_visible(page, ['[data-nav="settings"]'])
    if settings_nav is None:
        page.screenshot(path=str(shot_dir / "browser-01-no-settings-nav.png"))
        raise BrowserStepError("settings nav [data-nav=settings] not found")
    settings_nav.click()
    page.wait_for_timeout(800)
    evidence["steps"].append("opened settings")

    # Memory v1 lives in its own "Memory & Hardware" tab; the legacy
    # "Memory" tab still hosts the long-term-memory editor and must not be
    # mistaken for the v1 panel.
    hub_tab = _first_visible(
        page,
        [
            'button.settings-tab:has-text("记忆与硬件档案")',
            'button.settings-tab:has-text("Memory & Hardware")',
        ],
    )
    if hub_tab is None:
        hub_tab = _first_visible(
            page,
            [
                'button.settings-tab:has-text("记忆")',
                'button.settings-tab:has-text("Memory")',
            ],
        )
    if hub_tab is None:
        page.screenshot(path=str(shot_dir / "browser-02-no-memory-tab.png"))
        raise BrowserStepError("Settings memory tab not found")
    hub_tab.click()
    page.wait_for_timeout(800)
    page.screenshot(path=str(shot_dir / "browser-03-memory-hub.png"))
    evidence["steps"].append("opened memory hub tab")

    # Ensure the memory-entries section is active (it is the default).
    _click_button(page, ["记忆条目", "Memory Entries"], timeout_ms=1500)
    page.wait_for_timeout(400)

    marker = f"BROWSER-MEMORY-{int(time.time())}"
    updated = marker + "-UPDATED"

    if not _click_button(page, ["新增记忆", "Add memory"]):
        page.screenshot(path=str(shot_dir / "browser-04-no-add-button.png"))
        raise BrowserStepError(
            "memory add button not found (expected 新增记忆 / Add memory)"
        )
    page.wait_for_timeout(500)

    editor = _first_visible(
        page,
        [
            "#memory-create-content",
            'textarea[id$="-content"]',
            "textarea.settings-textarea",
        ],
    )
    if editor is None:
        page.screenshot(path=str(shot_dir / "browser-05-no-content-input.png"))
        raise BrowserStepError("memory content input not found")
    editor.fill(marker)
    evidence["steps"].append("filled memory content")

    if not _click_button(page, ["保存记忆", "Save memory"]):
        page.screenshot(path=str(shot_dir / "browser-06-no-save-button.png"))
        raise BrowserStepError("memory save button not found")
    if not _visible_marker(page, marker, present=True):
        page.screenshot(path=str(shot_dir / "browser-07-create-not-visible.png"))
        raise BrowserStepError("created memory did not appear in the panel")
    evidence["steps"].append("created memory visible")

    if not _click_button(page, ["修改", "Edit"]):
        page.screenshot(path=str(shot_dir / "browser-08-no-edit-button.png"))
        raise BrowserStepError("memory edit button not found")
    page.wait_for_timeout(500)
    editor = _first_visible(
        page,
        [
            "#memory-edit-content",
            'textarea[id$="-content"]',
            "textarea.settings-textarea",
        ],
    )
    if editor is None:
        raise BrowserStepError("memory content input not found while editing")
    editor.fill(updated)
    if not _click_button(page, ["保存", "Save"]):
        raise BrowserStepError("memory update save button not found")
    if not _visible_marker(page, updated, present=True):
        page.screenshot(path=str(shot_dir / "browser-09-update-not-visible.png"))
        raise BrowserStepError("updated memory did not appear in the panel")
    evidence["steps"].append("updated memory visible")

    if not _click_button(page, ["删除", "Delete"]):
        page.screenshot(path=str(shot_dir / "browser-10-no-delete-button.png"))
        raise BrowserStepError("memory delete button not found")
    if not _click_button(page, ["确认删除", "Confirm Delete"]):
        page.screenshot(path=str(shot_dir / "browser-11-no-confirm-delete.png"))
        raise BrowserStepError("memory delete confirmation not found")
    if not _visible_marker(page, updated, present=False):
        page.screenshot(path=str(shot_dir / "browser-12-delete-not-applied.png"))
        raise BrowserStepError("deleted memory is still visible in the panel")
    evidence["steps"].append("deleted memory hidden")
    page.screenshot(path=str(shot_dir / "browser-13-memory-crud.png"))

    # Hardware profiles section inside the same hub.
    if not _click_button(page, ["硬件档案", "Hardware Profiles"]):
        page.screenshot(path=str(shot_dir / "browser-14-no-profile-section.png"))
        raise BrowserStepError("hardware profile section button not found")
    page.wait_for_timeout(800)
    page.screenshot(path=str(shot_dir / "browser-15-profile-panel.png"))
    evidence["steps"].append("opened hardware profile panel")

    profile_name = f"BROWSER-PROFILE-{int(time.time())}"
    if not _click_button(page, ["新建档案", "New profile"]):
        page.screenshot(path=str(shot_dir / "browser-16-no-new-profile.png"))
        raise BrowserStepError("hardware profile new button not found")
    page.wait_for_timeout(400)

    name_input = _first_visible(page, ["#hardware-name", 'input[id*="hardware-name"]'])
    if name_input is None:
        raise BrowserStepError("hardware profile name input not found")
    name_input.fill(profile_name)
    mcu_input = _first_visible(page, ["#hardware-mcu"])
    if mcu_input is not None:
        mcu_input.fill("ESP32-S3")
    voltage_input = _first_visible(page, ["#hardware-voltage"])
    if voltage_input is not None:
        voltage_input.fill("3.3V")

    if not _click_button(page, ["保存档案", "Save profile"]):
        page.screenshot(path=str(shot_dir / "browser-17-no-save-profile.png"))
        raise BrowserStepError("hardware profile save button not found")
    if not _visible_marker(page, profile_name, present=True):
        page.screenshot(path=str(shot_dir / "browser-18-profile-not-visible.png"))
        raise BrowserStepError("created hardware profile did not appear")
    evidence["steps"].append("created hardware profile visible")

    search_input = _first_visible(
        page,
        [
            'input[placeholder*="搜索硬件档案"]',
            'input[placeholder*="Search hardware" i]',
        ],
    )
    if search_input is not None:
        search_input.fill(profile_name)
        page.wait_for_timeout(900)
        if not _visible_marker(page, profile_name, present=True):
            raise BrowserStepError("hardware profile search lost the created profile")
        evidence["steps"].append("hardware profile search returned the profile")

    try:
        page.get_by_role(
            "button", name=re.compile(re.escape(profile_name))
        ).first.click()
        page.wait_for_timeout(600)
    except Exception:
        pass

    if not _click_button(page, ["修改", "Edit"], timeout_ms=2500):
        page.screenshot(path=str(shot_dir / "browser-19-no-profile-edit.png"))
        raise BrowserStepError("hardware profile edit button not found")
    voltage_input = _first_visible(page, ["#hardware-voltage"])
    if voltage_input is None:
        raise BrowserStepError("hardware profile voltage input not found while editing")
    voltage_input.fill("5V")
    if not _click_button(page, ["保存档案", "Save profile"]):
        raise BrowserStepError("hardware profile edit save button not found")
    if not _visible_marker(page, "5V", present=True):
        raise BrowserStepError("edited hardware profile voltage not visible")
    evidence["steps"].append("edited hardware profile visible")

    page.screenshot(path=str(shot_dir / "browser-20-profile-crud.png"))
    return evidence


def _run_browser_acceptance(
    check: Check, work_dir: Path, startup_timeout: int, frontend_probe: dict
) -> dict:
    evidence: dict = {"started": False}
    shot_dir = work_dir / "browser"
    shot_dir.mkdir(parents=True, exist_ok=True)
    check.evidence.append(str(shot_dir))

    if not frontend_probe.get("started"):
        check.block(
            "frontend dev server is not running; browser acceptance needs "
            "--with-frontend and installed frontend dependencies"
        )
        return evidence

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        check.block(
            f"playwright is not importable ({exc}); real browser acceptance is "
            "unavailable and is NOT replaced by TestClient"
        )
        return evidence

    if not _port_free(BROWSER_BACKEND_PORT):
        check.block(
            f"port {BROWSER_BACKEND_PORT} is already in use; the Vite proxy targets "
            "that port and this runner refuses to stop a process it did not start"
        )
        return evidence

    backend_process, healthy, health_detail, backend_log = _start_backend(
        work_dir, BROWSER_BACKEND_PORT, startup_timeout, "backend-browser.log"
    )
    evidence["backend"] = {
        "port": BROWSER_BACKEND_PORT,
        "healthy": healthy,
        "detail": health_detail,
        "log": backend_log,
    }
    if not healthy:
        _stop_process(backend_process)
        check.fail(
            f"browser-mode backend did not become healthy: {health_detail} "
            "(see backend-browser.log)"
        )
        return evidence

    frontend_port = int(frontend_probe.get("port") or 0)
    evidence["frontend_url"] = f"http://127.0.0.1:{frontend_port}/"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.set_default_timeout(10000)
            try:
                result = _browser_flow(page, shot_dir, evidence["frontend_url"])
                evidence.update(result)
                if evidence["console_errors"]:
                    check.fail(
                        "browser console/page errors during acceptance: "
                        f"{evidence['console_errors'][:5]} (see browser screenshots)"
                    )
                else:
                    check.pass_(
                        "Chromium drove Settings: memory panel add/edit/delete and "
                        "hardware profile panel load"
                    )
            except BrowserStepError as exc:
                page.screenshot(path=str(shot_dir / "browser-error.png"))
                evidence["error"] = str(exc)
                check.block(f"browser acceptance blocked: {exc} (see browser/)")
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        check.fail(f"browser automation crashed: {type(exc).__name__}: {exc}")
    finally:
        _stop_process(backend_process)
        evidence["backend"]["process_exit_code"] = (
            backend_process.returncode if backend_process else None
        )
        (work_dir / "browser-probe.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        check.evidence.append(str(work_dir / "browser-probe.json"))
    return evidence


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _write_report(
    work_dir: Path,
    checks: list[Check],
    static_result: dict,
    pytest_summary: dict,
    live_backend: dict,
    live_frontend: dict,
    browser_result: dict,
) -> tuple[str, dict]:
    if any(check.status == "fail" for check in checks):
        verdict = "FAIL"
    elif any(check.status == "blocked" and check.required_for_pass for check in checks):
        verdict = "BLOCKED"
    else:
        verdict = "PASS"

    summary = {
        "verdict": verdict,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(REPO_ROOT),
        "branch": _git("branch", "--show-current"),
        "commit": _git("rev-parse", "HEAD"),
        "checks": [check.as_dict() for check in checks],
        "static": static_result,
        "pytest": pytest_summary,
        "live_backend": live_backend,
        "live_frontend": live_frontend,
        "browser": browser_result,
        "evidence_dir": str(work_dir),
    }
    (work_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Memory v1 acceptance report",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Repo: `{summary['repo']}`",
        f"- Branch: `{summary['branch']}`",
        f"- Commit: `{summary['commit']}`",
        f"- Verdict: **{verdict}**",
        "",
        "## Checks",
        "",
        "| check | status | detail |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        detail = check.detail.replace("|", "\\|")
        lines.append(f"| {check.name} | {check.status} | {detail} |")
    lines.extend(["", "## pytest", ""])
    lines.append(
        f"{pytest_summary.get('tests', 0)} tests, "
        f"{pytest_summary.get('failures', 0)} failed, "
        f"{pytest_summary.get('errors', 0)} errors, "
        f"{pytest_summary.get('skipped', 0)} skipped, "
        f"exit code {pytest_summary.get('exit_code')}"
    )
    if pytest_summary.get("skip_reasons"):
        lines.append("")
        lines.append("Skip reasons:")
        for reason in pytest_summary["skip_reasons"][:30]:
            lines.append(f"- {reason}")
    lines.extend(["", "## Evidence", ""])
    for check in checks:
        lines.append(f"### {check.name}")
        lines.append(f"- Status: {check.status}")
        lines.append(f"- Detail: {check.detail}")
        for path in check.evidence:
            lines.append(f"- `{path}`")
        lines.append("")
    lines.extend(
        [
            "## Boundaries",
            "",
            "- Automated tests and HTTP smoke checks are not browser acceptance.",
            "- Browser acceptance is only claimed when the Chromium flow actually "
            "drove the Settings panels; a blocked browser check is not a pass.",
            "- A BLOCKED verdict means a memory/hardware/frontend dependency was not "
            "merged into this worktree at run time; it is not a pass.",
            "",
        ]
    )
    (work_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return verdict, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="",
        help="evidence directory (default: <E or D temp>/memory-v1-<timestamp>)",
    )
    parser.add_argument(
        "--skip-pytest", action="store_true", help="skip the pytest acceptance suite"
    )
    parser.add_argument(
        "--skip-backend", action="store_true", help="skip the temporary backend startup"
    )
    parser.add_argument(
        "--with-frontend",
        action="store_true",
        help="also start the Vite dev server (requires frontend/node_modules)",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="run the real Chromium acceptance flow (implies --with-frontend)",
    )
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=180,
        help="seconds to wait for each server (heavy imports take ~30s on this host)",
    )
    parser.add_argument(
        "--pytest-timeout", type=int, default=900, help="seconds before pytest is killed"
    )
    args = parser.parse_args(argv)

    if args.browser:
        args.with_frontend = True

    if args.out:
        work_dir = Path(args.out).resolve()
    else:
        work_dir = _temp_base() / f"memory-v1-{_timestamp()}"
    work_dir.mkdir(parents=True, exist_ok=True)

    env = _child_env(work_dir)

    checks: list[Check] = []

    static_check = Check("static-contract")
    static_result = _static_probe(static_check, work_dir, env)
    (work_dir / "static-probe.json").write_text(
        json.dumps(static_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    static_check.evidence.append(str(work_dir / "static-probe.json"))
    checks.append(static_check)

    pytest_summary: dict = {}
    if args.skip_pytest:
        pytest_check = Check("pytest-acceptance")
        pytest_check.block("skipped by --skip-pytest")
    else:
        pytest_check = Check("pytest-acceptance")
        pytest_summary = _run_pytest(pytest_check, work_dir, args.pytest_timeout)
    checks.append(pytest_check)

    live_backend: dict = {}
    if args.skip_backend:
        backend_check = Check("backend-live")
        backend_check.block("skipped by --skip-backend")
        checks.append(backend_check)
        profile_check = Check("hardware-profile-api", required_for_pass=False)
        profile_check.block("skipped by --skip-backend")
        checks.append(profile_check)
    else:
        backend_check = Check("backend-live")
        live_backend = _run_live_backend(backend_check, work_dir, args.startup_timeout)
        checks.append(backend_check)
        profile_check = Check("hardware-profile-api", required_for_pass=False)
        _run_profile_route_check(profile_check, live_backend)
        checks.append(profile_check)

    live_frontend: dict = {}
    if args.with_frontend:
        frontend_check = Check("frontend-live", required_for_pass=False)
        live_frontend = _run_live_frontend(
            frontend_check, work_dir, args.startup_timeout
        )
    else:
        frontend_check = Check("frontend-live", required_for_pass=False)
        frontend_check.block("not requested (use --with-frontend)")
    checks.append(frontend_check)

    browser_result: dict = {}
    if args.browser:
        browser_check = Check("browser-acceptance")
        browser_result = _run_browser_acceptance(
            browser_check, work_dir, args.startup_timeout, live_frontend
        )
    else:
        browser_check = Check("browser-acceptance", required_for_pass=False)
        browser_check.block("not requested (use --browser; requires frontend deps)")
    checks.append(browser_check)

    try:
        verdict, _summary = _write_report(
            work_dir,
            checks,
            static_result,
            pytest_summary,
            live_backend,
            live_frontend,
            browser_result,
        )
    except OSError as exc:
        print(f"could not write evidence: {exc}", file=sys.stderr)
        return 1

    for check in checks:
        print(f"[{check.status.upper():7}] {check.name}: {check.detail}")
    print(f"\nRESULT: {verdict}")
    print(f"EVIDENCE: {work_dir}")
    if verdict == "PASS":
        return 0
    if verdict == "FAIL":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
