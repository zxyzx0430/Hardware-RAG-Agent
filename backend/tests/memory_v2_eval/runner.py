"""Memory v2 evaluation runner (importable core; the CLI is a thin wrapper)."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import compute_metrics
from .model import (
    Dataset,
    ScenarioSchemaError,
    default_dataset_path,
    load_dataset,
)
from .reference_backend import run_reference
from .reporting import utc_timestamp, write_report

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
PYTEST_FILES = (
    "tests/test_memory_v2_eval_schema.py",
    "tests/test_memory_v2_eval_metrics.py",
    "tests/test_memory_v2_eval_harness.py",
    "tests/test_memory_v2_eval_runner.py",
)


def _temp_base() -> Path:
    for candidate in (Path("E:/DevCaches/Temp"), Path("D:/DevCaches/Temp")):
        if candidate.is_dir():
            return candidate
    return Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _git(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return completed.stdout.strip()
    except Exception:
        return ""


def _child_env(work_dir: Path, db_name: str = "memory-v2.sqlite") -> dict[str, str]:
    env = os.environ.copy()
    env["TEMP"] = str(work_dir)
    env["TMP"] = str(work_dir)
    env["SQLITE_DB_PATH"] = str(work_dir / db_name)
    env["MEMORY_ACCEPTANCE_ISOLATED"] = "1"
    env["AGENT_CHECKPOINTER_TYPE"] = "memory"
    env["HWRAG_APP_DATA_DIR"] = str(work_dir / "app-data")
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(BACKEND_DIR)
    return env


def _apply_isolation(work_dir: Path, env: dict[str, str]) -> None:
    for key in (
        "TEMP",
        "TMP",
        "SQLITE_DB_PATH",
        "MEMORY_ACCEPTANCE_ISOLATED",
        "AGENT_CHECKPOINTER_TYPE",
        "HWRAG_APP_DATA_DIR",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "PYTHONPATH",
    ):
        os.environ[key] = env[key]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, deadline: float, process: subprocess.Popen | None = None) -> bool:
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _parse_junit(path: Path) -> dict[str, Any]:
    result = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "failed_cases": [],
        "skip_reasons": [],
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


def _run_pytest(work_dir: Path, timeout: int) -> dict[str, Any]:
    env = _child_env(work_dir, "pytest.sqlite")
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    junit = work_dir / "pytest-junit.xml"
    log_path = work_dir / "pytest.log"
    command = [
        sys.executable,
        "-m",
        "pytest",
        *PYTEST_FILES,
        "-q",
        "-p",
        "asyncio",
        "-p",
        "pytest_timeout",
        f"--junitxml={junit}",
    ]
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
        exit_code: int | None = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = None
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[runner] pytest exceeded {timeout}s and was killed\n")
    summary = _parse_junit(junit)
    summary["exit_code"] = exit_code
    summary["log"] = str(log_path)
    summary["junit"] = str(junit)
    if exit_code is None:
        summary["status"] = "fail"
        summary["detail"] = f"pytest timed out after {timeout}s"
    elif summary["failures"] or summary["errors"]:
        summary["status"] = "fail"
        summary["detail"] = (
            f"{summary['failures']} failed / {summary['errors']} errors: "
            f"{summary['failed_cases'][:5]}"
        )
    elif summary["tests"] == 0:
        summary["status"] = "blocked"
        summary["detail"] = f"no tests collected (exit {exit_code})"
    elif exit_code != 0:
        summary["status"] = "fail"
        summary["detail"] = f"pytest exit code {exit_code}"
    else:
        summary["status"] = "pass"
        summary["detail"] = (
            f"{summary['tests'] - summary['skipped']} passed, "
            f"{summary['skipped']} skipped"
        )
    return summary


def _run_frontend_probe(work_dir: Path, timeout: int) -> dict[str, Any]:
    node_modules = FRONTEND_DIR / "node_modules"
    vite_entry = node_modules / "vite" / "bin" / "vite.js"
    if not vite_entry.is_file():
        return {
            "status": "blocked",
            "detail": (
                "frontend/node_modules or vite is missing; the runner does not install "
                "dependencies and no browser acceptance is claimed"
            ),
        }
    node = shutil.which("node")
    if not node:
        return {"status": "blocked", "detail": "node executable not found on PATH"}
    port = _free_port()
    log_path = work_dir / "frontend-live.log"
    process: subprocess.Popen | None = None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [node, str(vite_entry), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
                cwd=FRONTEND_DIR,
                env=_child_env(work_dir),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            ready = _wait_http(
                f"http://127.0.0.1:{port}/", time.monotonic() + timeout, process
            )
        if not ready:
            return {
                "status": "fail",
                "detail": f"vite did not answer within {timeout}s (log: {log_path})",
            }
        return {
            "status": "pass",
            "detail": (
                "vite answered over HTTP; this is a server probe, not browser acceptance "
                f"(log: {log_path})"
            ),
            "port": port,
        }
    finally:
        _stop_process(process)


def run_harness(scenarios: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results = [run_reference(scenario) for scenario in scenarios]
    metrics = compute_metrics(results, scenarios)
    return results, metrics


def run_product_stage(probe: dict[str, Any]) -> dict[str, Any]:
    """Product evaluation is fail-closed until a merged adapter is bound.

    The deterministic reference harness must not be reported as product
    acceptance.  When the probe finds the frozen v2 interfaces, an integration
    owner still has to bind the store/context adapter; until then this stage is
    pending rather than silently passing.
    """
    if not probe.get("available"):
        return {
            "status": "pending",
            "reason": probe.get("summary", "Memory v2 interfaces are missing"),
            "metrics": None,
            "scenarios": [],
        }
    return {
        "status": "pending",
        "reason": (
            "Memory v2 interfaces were detected, but the deterministic product "
            "adapter is not bound in this evaluation worktree. Bind the frozen "
            "store/context interface before claiming product gates."
        ),
        "metrics": None,
        "scenarios": [],
    }


def run_real_model_stage(requested: bool, probe: dict[str, Any]) -> dict[str, Any]:
    if not requested:
        return {
            "status": "skipped",
            "detail": "opt-in only; deterministic fake extraction is the v2.0 gate",
        }
    if not probe.get("available"):
        return {
            "status": "pending",
            "detail": (
                "real-model evaluation needs the merged Memory v2 extraction interface; "
                "no network call was made"
            ),
        }
    return {
        "status": "pending",
        "detail": (
            "real-model evaluation is opt-in and informational; the deterministic "
            "product adapter must be bound first. No network call was made."
        ),
    }


def _overall_verdict(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "fail" for check in checks):
        return "FAIL"
    if any(check["status"] in ("blocked", "pending") and check.get("required", True) for check in checks):
        return "BLOCKED"
    return "PASS"


def _environment(work_dir: Path, env: dict[str, str]) -> dict[str, Any]:
    return {
        "repo": str(REPO_ROOT),
        "backend": str(BACKEND_DIR),
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "temp": env["TEMP"],
        "sqlite_db_path": env["SQLITE_DB_PATH"],
        "checkpointer": env["AGENT_CHECKPOINTER_TYPE"],
        "app_data_dir": env.get("HWRAG_APP_DATA_DIR", ""),
        "evidence_dir": str(work_dir),
    }


def _dataset_summary(dataset: Dataset, path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "count": len(dataset.scenarios),
        "categories": sorted(dataset.categories),
        "unique_categories": len(set(dataset.categories)),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Memory v2 evaluation runner")
    parser.add_argument(
        "--out", default="", help="evidence directory (default: E/D temp/memory-v2-<ts>)"
    )
    parser.add_argument("--skip-pytest", action="store_true", help="skip eval pytest suite")
    parser.add_argument(
        "--with-frontend", action="store_true", help="start Vite as an HTTP probe (not browser acceptance)"
    )
    parser.add_argument(
        "--run-real-model",
        action="store_true",
        help="opt-in real-model evaluation; never enabled by default",
    )
    parser.add_argument("--pytest-timeout", type=int, default=600)
    parser.add_argument("--frontend-timeout", type=int, default=90)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    work_dir = (
        Path(args.out).resolve()
        if args.out
        else _temp_base() / f"memory-v2-{_timestamp()}"
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    env = _child_env(work_dir)
    _apply_isolation(work_dir, env)

    checks: list[dict[str, Any]] = []
    evidence: dict[str, str] = {}

    # 1. Dataset schema ---------------------------------------------------
    dataset_path = default_dataset_path()
    dataset: Dataset | None = None
    try:
        dataset = load_dataset(dataset_path)
        dataset_check = {
            "name": "dataset-schema",
            "status": "pass",
            "detail": f"{len(dataset.scenarios)} scenarios, "
            f"{len(set(dataset.categories))} categories",
            "required": True,
        }
    except ScenarioSchemaError as exc:
        dataset_check = {
            "name": "dataset-schema",
            "status": "fail",
            "detail": str(exc),
            "required": True,
        }
    checks.append(dataset_check)

    # 2. Reference harness ------------------------------------------------
    harness_results: list[dict[str, Any]] = []
    harness_metrics: dict[str, Any] = {}
    if dataset is not None:
        harness_results, harness_metrics = run_harness(list(dataset.scenarios))
        failed = [result["scenario_id"] for result in harness_results if not result["passed"]]
        if failed:
            harness_status = "fail"
            harness_detail = f"reference scenario checks failed: {failed}"
        elif not harness_metrics.get("all_gates_pass"):
            harness_status = "fail"
            harness_detail = (
                "reference metric gates failed: "
                f"{[gate['name'] for gate in harness_metrics['gates'] if gate['status'] != 'pass']}"
            )
        else:
            harness_status = "pass"
            harness_detail = (
                f"{len(harness_results)} scenarios pass; all metric gates pass. "
                "Reference harness only, not product acceptance."
            )
    else:
        harness_status = "blocked"
        harness_detail = "dataset validation failed; harness not run"
    checks.append(
        {
            "name": "reference-harness",
            "status": harness_status,
            "detail": harness_detail,
            "required": True,
        }
    )
    harness_results_path = work_dir / "reference-scenario-results.json"
    harness_results_path.write_text(
        json.dumps(harness_results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    evidence["reference_scenario_results"] = str(harness_results_path)

    # 3. Product probe ----------------------------------------------------
    from .product_probe import probe_product_v2

    probe = probe_product_v2()
    probe_path = work_dir / "product-probe.json"
    probe_path.write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence["product_probe"] = str(probe_path)
    product_stage = run_product_stage(probe)
    checks.append(
        {
            "name": "product-v2",
            "status": product_stage["status"],
            "detail": product_stage["reason"],
            "required": True,
        }
    )

    # 4. pytest -----------------------------------------------------------
    if args.skip_pytest:
        pytest_summary = {
            "status": "skipped",
            "detail": "skipped by --skip-pytest",
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }
        pytest_required = False
    else:
        pytest_summary = _run_pytest(work_dir, args.pytest_timeout)
        pytest_required = True
        evidence["pytest_log"] = pytest_summary.get("log", "")
        evidence["pytest_junit"] = pytest_summary.get("junit", "")
    checks.append(
        {
            "name": "pytest-eval",
            "status": pytest_summary["status"],
            "detail": pytest_summary["detail"],
            "required": pytest_required,
        }
    )

    # 5. Frontend (optional) ---------------------------------------------
    if args.with_frontend:
        frontend_summary = _run_frontend_probe(work_dir, args.frontend_timeout)
        evidence["frontend_log"] = str(work_dir / "frontend-live.log")
        frontend_summary["required"] = False
    else:
        frontend_summary = {
            "status": "skipped",
            "detail": "not requested (--with-frontend)",
            "required": False,
        }
    checks.append(
        {
            "name": "frontend-live",
            "status": frontend_summary["status"],
            "detail": frontend_summary["detail"],
            "required": False,
        }
    )

    # 6. Real model (opt-in) ---------------------------------------------
    real_model = run_real_model_stage(args.run_real_model, probe)
    checks.append(
        {
            "name": "real-model",
            "status": real_model["status"],
            "detail": real_model["detail"],
            "required": False,
        }
    )

    verdict = _overall_verdict(checks)
    payload = {
        "schema_version": "memory-v2-eval/1",
        "generated_at": utc_timestamp(),
        "verdict": verdict,
        "environment": _environment(work_dir, env),
        "git": {
            "branch": _git("branch", "--show-current"),
            "commit": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "dataset": _dataset_summary(dataset, dataset_path) if dataset else {"path": str(dataset_path)},
        "checks": checks,
        "harness": {
            "status": harness_status,
            "metrics": harness_metrics,
            "scenarios": harness_results,
        },
        "product": product_stage,
        "pytest": pytest_summary,
        "frontend": frontend_summary,
        "real_model": real_model,
        "evidence": evidence,
    }
    evidence["report"] = str(work_dir / "report.md")
    evidence["metrics"] = str(work_dir / "metrics.json")
    report_path, metrics_path = write_report(work_dir, payload)

    for check in checks:
        print(f"[{check['status'].upper():8}] {check['name']}: {check['detail']}")
    print(f"\nRESULT: {verdict}")
    print(f"EVIDENCE: {work_dir}")
    if verdict == "PASS":
        return 0
    if verdict == "FAIL":
        return 1
    return 2


__all__ = ["main", "run_harness", "run_product_stage", "run_real_model_stage"]
