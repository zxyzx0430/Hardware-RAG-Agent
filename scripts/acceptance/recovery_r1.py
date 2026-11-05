#!/usr/bin/env python3
"""Recovery R1 acceptance runner.

Runs the contract-level acceptance suite and, when the recovery runtime is
available, starts a temporary backend (and optionally the frontend dev server)
against a throw-away SQLite database.

Exit codes
    0  PASS     every executed check passed
    1  FAIL     at least one check failed
    2  BLOCKED  no failures, but a required dependency is not merged yet

The runner never installs dependencies, never downloads models and never points
at the user's database.  Evidence (logs, JUnit XML, summary) is written to an
evidence directory whose absolute path is printed at the end.

Typical use
    python scripts/acceptance/recovery_r1.py
    python scripts/acceptance/recovery_r1.py --with-frontend
    python scripts/acceptance/recovery_r1.py --skip-pytest
"""

from __future__ import annotations

import argparse
import json
import os
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


def _child_env(work_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TEMP"] = str(work_dir)
    env["TMP"] = str(work_dir)
    env["SQLITE_DB_PATH"] = str(work_dir / "acceptance.sqlite")
    env["RECOVERY_ACCEPTANCE_ISOLATED"] = "1"
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
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65536).decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _wait_for_http(
    url: str,
    deadline: float,
    process: subprocess.Popen | None = None,
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
    "storage:run_store": ["backend/app/db/run_store.py"],
    "storage:recovery_models": ["backend/app/db/recovery_models.py"],
    "runtime:run_events": [
        "backend/src/agent/run_events.py",
        "backend/app/api/run_events.py",
    ],
    "runtime:runs_routes": [
        "backend/app/api/runs.py",
        "backend/app/api/runs_routes.py",
    ],
    "frontend:run_recovery": ["frontend/src/utils/runRecovery.ts"],
}

IMPORT_PROBES = [
    "app.db.run_store",
    "app.db.recovery_models",
    "src.agent.run_events",
    "app.api.runs",
    "src.agent.recovery",
]


def _static_probe(check: Check, work_dir: Path, env: dict[str, str]) -> dict:
    result: dict = {"files": {}, "imports": {}}
    for label, paths in FILE_GROUPS.items():
        present = [path for path in paths if (REPO_ROOT / path).is_file()]
        result["files"][label] = {"expected": paths, "present": present}
    for module_name in IMPORT_PROBES:
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=BACKEND_DIR,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        stderr = (completed.stderr or "").strip().splitlines()
        result["imports"][module_name] = {
            "ok": completed.returncode == 0,
            "error": stderr[-1] if stderr else "",
        }

    missing_files = [
        label
        for label, info in result["files"].items()
        if not info["present"]
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
        check.pass_("all expected recovery modules import and files exist")
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
    env = _child_env(work_dir)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    junit = work_dir / "pytest-junit.xml"
    log_path = work_dir / "pytest.log"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_recovery_acceptance.py",
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
        reasons = summary["skip_reasons"][:5]
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

RUN_ENDPOINT_PROBES = (
    ("run_status", "GET", "/api/runs/__acceptance_probe__"),
    ("run_events", "GET", "/api/runs/__acceptance_probe__/events"),
    ("run_cancel", "POST", "/api/runs/__acceptance_probe__/cancel"),
)


def _is_default_not_found(status: int | None, body: str) -> bool:
    if status != 404:
        return False
    try:
        return json.loads(body).get("detail") == "Not Found"
    except Exception:
        return False


def _run_live_backend(check: Check, work_dir: Path, startup_timeout: int) -> dict:
    env = _child_env(work_dir)
    port = _free_port()
    log_path = work_dir / "backend-live.log"
    probe_path = work_dir / "backend-probe.json"
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
    process: subprocess.Popen | None = None
    evidence: dict = {"port": port, "command": command, "probes": {}}
    check.evidence.append(str(log_path))
    check.evidence.append(str(probe_path))
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=BACKEND_DIR,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + startup_timeout
            healthy, health_detail = _wait_for_http(
                f"http://127.0.0.1:{port}/health", deadline, process
            )
            evidence["health"] = {"ok": healthy, "detail": health_detail}
            if not healthy:
                check.fail(
                    f"temporary backend did not become healthy within {startup_timeout}s: "
                    f"{health_detail} (see backend-live.log)"
                )
                return evidence

            registered = 0
            for name, method, path in RUN_ENDPOINT_PROBES:
                data = b"{}" if method == "POST" else None
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
                        f"{method} {path} returned {status}: {body[:300]} (see backend-live.log)"
                    )
                    return evidence

        if registered == 0:
            check.block(
                "temporary backend is healthy but no /api/runs recovery routes are "
                "registered (runtime dependency missing); see backend-probe.json"
            )
        elif registered < len(RUN_ENDPOINT_PROBES):
            missing_names = [
                name
                for name, info in evidence["probes"].items()
                if not info["registered"]
            ]
            check.block(
                f"recovery routes partially registered; missing {missing_names} "
                "(see backend-probe.json)"
            )
        else:
            check.pass_("temporary backend healthy; run status/events/cancel routes registered")
        return evidence
    finally:
        _stop_process(process)
        evidence["process_exit_code"] = process.returncode if process else None
        probe_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
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
            deadline = time.monotonic() + startup_timeout
            ready, detail = _wait_for_http(
                f"http://127.0.0.1:{port}/", deadline, process
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
                "vite dev server answered over HTTP; this is NOT browser acceptance "
                "and no recovery interaction was exercised here"
            )
        return evidence
    finally:
        _stop_process(process)
        evidence["process_exit_code"] = process.returncode if process else None


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
        "evidence_dir": str(work_dir),
    }
    (work_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Recovery R1 acceptance report",
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
        for reason in pytest_summary["skip_reasons"][:20]:
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
            "- Hardware flash/build execution is not exercised by this runner.",
            "- A BLOCKED verdict means the storage/runtime/frontend dependency was not "
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
        help="evidence directory (default: <E or D temp>/recovery-r1-<timestamp>)",
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
        "--startup-timeout",
        type=int,
        default=180,
        help="seconds to wait for each server (heavy imports take ~30s on this host)",
    )
    parser.add_argument(
        "--pytest-timeout", type=int, default=600, help="seconds before pytest is killed"
    )
    args = parser.parse_args(argv)

    if args.out:
        work_dir = Path(args.out).resolve()
    else:
        work_dir = _temp_base() / f"recovery-r1-{_timestamp()}"
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
    else:
        backend_check = Check("backend-live")
        live_backend = _run_live_backend(backend_check, work_dir, args.startup_timeout)
    checks.append(backend_check)

    live_frontend: dict = {}
    if args.with_frontend:
        frontend_check = Check("frontend-live", required_for_pass=False)
        live_frontend = _run_live_frontend(frontend_check, work_dir, args.startup_timeout)
    else:
        frontend_check = Check("frontend-live", required_for_pass=False)
        frontend_check.block("not requested (use --with-frontend)")
    checks.append(frontend_check)

    try:
        verdict, _summary = _write_report(
            work_dir,
            checks,
            static_result,
            pytest_summary,
            live_backend,
            live_frontend,
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
