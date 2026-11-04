"""Data-integrity GC for messages, chunks, observations, and checkpoints.

Dry-run by default. Apply mode deletes every orphan reported with the same
cutoff and rebuilds messages_fts; --vacuum additionally compacts the main DB.
Stop the backend before --apply (SQLite has no per-thread checkpoint age).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.gc import (  # noqa: E402
    DEFAULT_CHECKPOINT_DB,
    DEFAULT_GRACE_HOURS,
    DEFAULT_MAIN_DB,
    apply_gc_report,
    build_gc_report,
    write_report,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile orphaned message and checkpoint data.")
    parser.add_argument("--apply", action="store_true", help="delete reported orphans (default: dry-run)")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM the main DB after apply")
    parser.add_argument("--grace-hours", type=int, default=DEFAULT_GRACE_HOURS)
    parser.add_argument("--main-db", type=Path, default=DEFAULT_MAIN_DB)
    parser.add_argument("--checkpoint-db", type=Path, default=DEFAULT_CHECKPOINT_DB)
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.vacuum and not args.apply:
        print("--vacuum requires --apply", file=sys.stderr)
        return 2
    report = build_gc_report(args.main_db, args.checkpoint_db, args.grace_hours)
    if args.apply:
        report = apply_gc_report(report, vacuum=args.vacuum)
    target = write_report(report, args.report)
    print(json.dumps({"mode": report["mode"], "report": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
