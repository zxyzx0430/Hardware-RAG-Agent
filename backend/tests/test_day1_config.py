"""Week 1 Day 1 验收测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ENV_FILE, PROJECT_ROOT, get_env_value, greet, load_environment


def test_project_root_points_to_repo_root():
    """确认 pathlib 能正确定位到项目根目录。"""
    assert PROJECT_ROOT.is_absolute()
    assert (PROJECT_ROOT / "main.py").exists()


def test_load_environment_reads_env_file(tmp_path, monkeypatch):
    """确认 dotenv 能从指定 .env 读取变量。"""
    env_file = tmp_path / ".env"
    env_file.write_text("DAY1_NAME=hardware-rag-agent\n", encoding="utf-8")
    monkeypatch.delenv("DAY1_NAME", raising=False)

    used_path = load_environment(env_file)

    assert used_path == env_file
    assert get_env_value("DAY1_NAME") == "hardware-rag-agent"


def test_default_env_file_path_is_repo_env():
    """确认默认 .env 路径符合工程约定。"""
    assert ENV_FILE == PROJECT_ROOT / ".env"


def test_greet_uses_basic_type_hints():
    """确认类型注解示例函数可正常工作。"""
    assert greet("Leader") == "Hello, Leader!"


def test_get_env_value_returns_none_for_missing_key(monkeypatch):
    """`str | None` 表示值可能不存在。"""
    monkeypatch.delenv("DAY1_MISSING", raising=False)
    assert get_env_value("DAY1_MISSING") is None
