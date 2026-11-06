"""Build a disposable context-stress project under E:/DevCaches/Temp.

Never points at the user's database/config. Run directly:
    D:/python/python.exe backend/tests/context_stress/build_stress_project.py
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("HWRAG_STRESS_ROOT", r"E:/DevCaches/Temp/hwrag-context-stress"))
PROJECT = ROOT / "project"
MARKER = PROJECT / ".stress-ready"
DENIED_FILE = PROJECT / "denied" / "secret.txt"
JUNCTION = PROJECT / "src" / "loop_back"

IGNORED_DIRS = (
    "node_modules", ".venv", ".pio", ".platformio", ".git",
    "dist", "build", "target", "__pycache__", ".pytest_cache",
)

SMALL_FILE_COUNTS = {
    "src": 250,
    "docs": 100,
    "node_modules": 100,
    ".venv": 50,
    ".pio": 50,
    ".platformio": 30,
    ".git": 50,
    "dist": 50,
    "build": 50,
    "target": 50,
    "wide": 150,
}


def _user() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _remove_deny() -> None:
    user = _user()
    if DENIED_FILE.exists() and user:
        subprocess.run(
            ["icacls", str(DENIED_FILE), "/remove:d", user],
            capture_output=True, text=True, check=False,
        )


def _remove_junction() -> None:
    if JUNCTION.exists() or JUNCTION.is_symlink():
        try:
            if os.path.isjunction(JUNCTION):
                os.rmdir(JUNCTION)
        except OSError:
            pass


def cleanup() -> None:
    """Remove only the known stress root after checking containment."""
    resolved_root = ROOT.resolve()
    resolved_project = PROJECT.resolve() if PROJECT.exists() else PROJECT.absolute()
    if resolved_root not in resolved_project.parents and resolved_project != resolved_root:
        raise RuntimeError(f"refusing to clean outside the stress root: {resolved_project}")
    _remove_deny()
    _remove_junction()
    if PROJECT.exists():
        shutil.rmtree(PROJECT, ignore_errors=True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_small_files(rng: random.Random) -> None:
    for directory, count in SMALL_FILE_COUNTS.items():
        base = PROJECT / directory
        for index in range(count):
            depth = rng.randint(0, 4)
            target_dir = base
            for level in range(depth):
                target_dir = target_dir / f"d{level}"
            suffix = ".js" if directory == "node_modules" else (
                ".py" if directory in {".venv", "src"} else ".txt"
            )
            _write(
                target_dir / f"file-{index:04d}{suffix}",
                f"token-{directory}-{index}\nneedle line {index}\n"
                + ("def demo():\n    return 1\n" if suffix == ".py" else ""),
            )


def _build_special_files(rng: random.Random) -> None:
    _write(PROJECT / "README.md", "# Stress project\nneedle in readme\n")
    _write(PROJECT / "unicode_目录" / "文件-测试.md", "needle unicode\n")
    chain = PROJECT / "deep"
    for level in range(41):
        chain = chain / f"d{level}"
    _write(chain / "leaf.txt", "needle deep leaf\n")
    _write(PROJECT / "big_1mb.txt", ("needle 1mb line\n" * 70_000)[:1_048_576])
    _write(PROJECT / "big_10mb.txt", ("needle 10mb line\n" * 700_000)[:10_485_760])
    _write(PROJECT / "long_line.txt", "needle " + "x" * (2 * 1024 * 1024))
    (PROJECT / "binary.bin").write_bytes(bytes(rng.randrange(256) for _ in range(4096)) * 4)
    (PROJECT / "nul_binary.bin").write_bytes(b"\x00\x01\x02" * 2048)


def _build_permission_denied() -> str:
    _write(DENIED_FILE, "needle denied\n")
    user = _user()
    if not user:
        return "unavailable"
    result = subprocess.run(
        ["icacls", str(DENIED_FILE), "/deny", f"{user}:(R)"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return f"unavailable: {result.stderr.strip()[:200]}"
    try:
        DENIED_FILE.read_text(encoding="utf-8")
    except PermissionError:
        return "denied"
    except OSError as exc:
        return f"denied-other: {exc}"
    return "not-enforced"


def _build_junction() -> str:
    if os.name != "nt":
        return "unavailable"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(JUNCTION), str(PROJECT)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return f"unavailable: {result.stderr.strip()[:200]}"
    return "junction" if os.path.isjunction(JUNCTION) else "unavailable"


def _count_regular_files(root: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            isjunction = getattr(os.path, "isjunction", None)
            if os.path.islink(full) or (isjunction and isjunction(full)):
                continue
            kept.append(name)
        dirnames[:] = kept
        total += len(filenames)
    return total


def build(force: bool = False) -> dict:
    if MARKER.exists() and not force:
        return {"project": str(PROJECT), "reused": True}
    cleanup()
    PROJECT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261107)
    _build_small_files(rng)
    _build_special_files(rng)
    file_count = _count_regular_files(PROJECT)
    permission = _build_permission_denied()
    junction = _build_junction()
    status = {
        "project": str(PROJECT),
        "reused": False,
        "small_files_target": sum(SMALL_FILE_COUNTS.values()),
        "file_count": file_count,
        "permission_denied": permission,
        "junction": junction,
        "big_files": ["big_1mb.txt", "big_10mb.txt", "long_line.txt", "binary.bin", "nul_binary.bin"],
    }
    MARKER.write_text(str(status), encoding="utf-8")
    return status


if __name__ == "__main__":
    print(build(force="--force" in sys.argv))
