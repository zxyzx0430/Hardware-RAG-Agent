"""Security verification for all fixes."""
import sys
sys.path.insert(0, ".")

from src.agent.path_guard import validate_path, matches_deny_pattern
from src.agent.risk_classifier import classify_risk
from src.agent.tools.groups.execution.run_command import _FILE_WRITE_RE
from src.agent.agent_factory import _build_local_file_ops_tools

passed = 0
failed = 0

def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")

print("=== path_guard black-list ===")
ok, _ = validate_path(r"C:\Users\x\.ssh\id_rsa", False)
check("id_rsa blocked", not ok)

ok, _ = validate_path(r"C:\Windows\System32\config\SAM", False)
check("System32\SAM blocked", not ok)

ok, _ = validate_path(r"C:\Windows\System32", False)
check("System32 dir itself blocked", not ok)

ok, _ = validate_path(r"C:\WINDOWS~1\SYSTEM32~1", False)
check("8.3 short name blocked", not ok)

ok, _ = validate_path(r"C:\proj\src\main.py", False)
check("normal file allowed", ok)

ok, _ = validate_path(r"C:\Users\x\.aws\credentials", False)
check(".aws/credentials blocked", not ok)

ok, _ = validate_path(r"C:\Users\x\.kdbx\keep.kdbx", False)
check("*.kdbx blocked", not ok)

print("\n=== risk_classifier ===")
check("cat .env not LOW", classify_risk("run_command", {"command": "cat C:\\x\\.env"}) != "low")
check("git branch -D not LOW", classify_risk("run_command", {"command": "git branch -D feature"}) != "low")
check("git restore is MEDIUM+", classify_risk("run_command", {"command": "git restore ."}) != "low")
check("git checkout -- is MEDIUM+", classify_risk("run_command", {"command": "git checkout -- file.py"}) != "low")

print("\n=== _FILE_WRITE_RE ===")
check("echo 'a > b' not detected", _FILE_WRITE_RE.search('echo "a > b"') is None)
check("git checkout -- detected", _FILE_WRITE_RE.search("git checkout -- file.py") is not None)
check("git restore detected", _FILE_WRITE_RE.search("git restore .") is not None)
check("Copy-Item detected", _FILE_WRITE_RE.search("Copy-Item a.py b.py") is not None)
check("Remove-Item detected", _FILE_WRITE_RE.search("Remove-Item old.py") is not None)
check("Out-File detected", _FILE_WRITE_RE.search("echo hi | Out-File f.txt") is not None)
check("[IO.File] short namespace detected", _FILE_WRITE_RE.search("[IO.File]::WriteAllText('x','y')") is not None)
check("python -c open detected", _FILE_WRITE_RE.search('python -c "open(\'x\',\'w\').write(\'y\')"') is not None)
check("normal ls not detected", _FILE_WRITE_RE.search("ls -la") is None)

print("\n=== apply_patch disabled ===")
tools = _build_local_file_ops_tools()
names = [t.name for t in tools]
check("apply_patch not registered", "apply_patch" not in names)
check("8 tools (was 9)", len(names) == 8)

print(f"\n=== Summary: {passed} PASS, {failed} FAIL ===")
sys.exit(1 if failed else 0)
