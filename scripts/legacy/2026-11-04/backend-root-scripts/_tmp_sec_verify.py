"""Temporary security-fix verification. Deleted after run."""
from src.agent.path_guard import validate_path, matches_deny_pattern
from src.agent.risk_classifier import classify_risk

def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return cond

results = []

# 1. SSH private key blocked, returns id_rsa
ok, reason = validate_path(r"C:\Users\x\.ssh\id_rsa", False)
results.append(check(f"#1 id_rsa -> ({ok}, {reason!r})", not ok and reason == "path matches deny pattern: id_rsa"))

# 2. SAM blocked
ok, reason = validate_path(r"C:\Windows\System32\config\SAM", False)
results.append(check(f"#2 SAM -> ({ok}, {reason!r})", not ok))

# 3. System32 dir itself blocked
ok, reason = validate_path(r"C:\Windows\System32", False)
results.append(check(f"#3 System32 dir -> ({ok}, {reason!r})", not ok))

# 4. 8.3 short name detected
m = matches_deny_pattern(r"C:\WINDOWS~1\SYSTEM32~1")
results.append(check(f"#4 8.3 short -> {m!r}", m is not None))

# 5. cat .env not LOW
r = classify_risk("run_command", {"command": r"cat C:\x\.env"})
results.append(check(f"#5 cat .env -> {r}", r != "low"))

# 6. git branch -D not LOW
r = classify_risk("run_command", {"command": "git branch -D feature"})
results.append(check(f"#6 git branch -D -> {r}", r != "low"))

# 7. git restore MEDIUM/HIGH
r = classify_risk("run_command", {"command": "git restore ."})
results.append(check(f"#7 git restore -> {r}", r in ("medium", "high")))

# Extra: ensure normal project file still allowed
ok, reason = validate_path(r"C:\proj\src\main.py", False)
results.append(check(f"#extra main.py -> ({ok}, {reason!r})", ok))

# Extra: .ssh dir itself blocked (trailing-slash component fix)
ok, reason = validate_path(r"C:\Users\x\.ssh", False)
results.append(check(f"#extra .ssh dir -> ({ok}, {reason!r})", not ok))

print("\n=== %d/%d passed ===" % (sum(results), len(results)))
