"""Runner/report tests; full pytest is skipped to avoid nested runners."""

from __future__ import annotations

import json

from tests.memory_v2_eval import runner


def _unavailable_probe() -> dict:
    return {
        "available": False,
        "checks": [],
        "missing": ["memory_models", "memory_store"],
        "optional_missing": [],
        "summary": "Memory v2 core interfaces are not merged in this test",
    }


def test_runner_reports_blocked_when_product_v2_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.memory_v2_eval.product_probe.probe_product_v2", _unavailable_probe
    )
    code = runner.main(["--out", str(tmp_path), "--skip-pytest"])
    assert code == 2
    payload = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "BLOCKED"
    assert payload["product"]["status"] == "pending"
    assert payload["harness"]["metrics"]["all_gates_pass"] is True
    assert (tmp_path / "report.md").is_file()


def test_runner_passes_when_every_required_stage_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.memory_v2_eval.product_probe.probe_product_v2", _unavailable_probe
    )
    monkeypatch.setattr(
        runner,
        "run_product_stage",
        lambda probe: {
            "status": "pass",
            "reason": "fake product stage",
            "metrics": None,
            "scenarios": [],
        },
    )
    code = runner.main(["--out", str(tmp_path), "--skip-pytest"])
    assert code == 0
    payload = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"


def test_runner_fails_when_product_stage_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.memory_v2_eval.product_probe.probe_product_v2", _unavailable_probe
    )
    monkeypatch.setattr(
        runner,
        "run_product_stage",
        lambda probe: {
            "status": "fail",
            "reason": "forced product failure",
            "metrics": None,
            "scenarios": [],
        },
    )
    code = runner.main(["--out", str(tmp_path), "--skip-pytest"])
    assert code == 1
    payload = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "FAIL"


def test_skip_pytest_does_not_invoke_pytest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.memory_v2_eval.product_probe.probe_product_v2", _unavailable_probe
    )

    def _explode(*args, **kwargs):
        raise AssertionError("pytest must not run with --skip-pytest")

    monkeypatch.setattr(runner, "_run_pytest", _explode)
    assert runner.main(["--out", str(tmp_path), "--skip-pytest"]) == 2


def test_report_states_reference_harness_is_not_product_acceptance(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.memory_v2_eval.product_probe.probe_product_v2", _unavailable_probe
    )
    runner.main(["--out", str(tmp_path), "--skip-pytest"])
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "not product acceptance" in report
    assert "not browser acceptance" in report
    assert "No LLM judge" in report
    assert "pending" in report


def test_real_model_is_opt_in_and_pending_without_product():
    skipped = runner.run_real_model_stage(False, {"available": False})
    assert skipped["status"] == "skipped"
    pending = runner.run_real_model_stage(True, {"available": False})
    assert pending["status"] == "pending"
    assert "no network call" in pending["detail"]
