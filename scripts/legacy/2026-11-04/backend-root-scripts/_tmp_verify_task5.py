"""Temporary verification script for Task 5 — delete after run."""
import sys

sys.path.insert(0, r"e:\Desktop\agent\backend")

from src.agent.core.toolkit.permission_classifier import PermissionClassifier
from src.agent.permission_matcher import PermissionMatcher

clf = PermissionClassifier()
matcher = PermissionMatcher()

cases = [
    ("ls; rm -rf /", "deny"),
    ("ls && git status", "allow"),
    ("Get-ChildItem | Remove-Item -Recurse", "deny"),
]

print("=== PermissionMatcher.check (raw verdict) ===")
for cmd, _ in cases:
    verdict = matcher.check("run_command", {"command": cmd})
    print(f"  {cmd!r:55} -> {verdict}")

print("\n=== PermissionClassifier._grade_command (final decision) ===")
all_pass = True
for cmd, expected in cases:
    result = clf._grade_command({"command": cmd})
    status = "PASS" if result == expected else "FAIL"
    if result != expected:
        all_pass = False
    print(f"  [{status}] {cmd!r:50} -> {result} (expected {expected})")

print("\nALL_PASS" if all_pass else "\nSOME_FAILED")
