"""Validate golden datasets or enforce thresholds on an existing eval report.

W1 owns .github/workflows/ci.yml; this script is the W5-owned gate entrypoint.
It never calls a model and never needs API keys.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from tests.rag_eval.gate import (  # noqa: E402
    GENERAL_DATASET,
    PDF_DATASET,
    GateResult,
    evaluate_report,
    validate_datasets,
)


def _print(results: list[GateResult]) -> None:
    for result in results:
        for message in result.messages:
            print(f"FAIL: {message}")
        if result.ok:
            print(f"PASS: {result.sample_count} samples")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        help="Existing golden_eval JSON report; repeat for general and PDF runs",
    )
    parser.add_argument("--min-samples", type=int, default=35)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    if args.report:
        results = [
            evaluate_report(
                json.loads(Path(path).read_text(encoding="utf-8")),
                require_complete=not args.allow_partial,
            )
            for path in args.report
        ]
    else:
        results = [
            validate_datasets(
                [GENERAL_DATASET, PDF_DATASET],
                minimum_total=args.min_samples,
            )
        ]

    _print(results)
    payload = {
        "ok": all(result.ok for result in results),
        "results": [
            {
                "ok": result.ok,
                "sample_count": result.sample_count,
                "messages": list(result.messages),
            }
            for result in results
        ],
    }
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    print("eval gate:", "PASS" if payload["ok"] else "FAIL")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
