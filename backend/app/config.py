"""
Week 1 Day 1：演示 pathlib、dotenv 和基础类型注解。

对应原计划：
- Day 1 / 任务 3：写 `app/config.py`，用 pathlib 管理路径
- Day 1 / 任务 3：用 python-dotenv 读取 `.env`
- Day 1 / 任务 4：练习 `def greet(name: str) -> str`
"""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import os


# 这个模块故意保持轻量，专门对应学习计划里的 Day 1 目标。
# 你以后看到这里，就能立刻联想到：这是“地基日”，不是业务逻辑日。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_environment(env_file: Optional[Path] = None) -> Path:
    """加载 .env，并返回实际使用的文件路径。"""
    target = env_file or ENV_FILE
    load_dotenv(dotenv_path=target, override=False)
    return target


def get_env_value(key: str, default: str | None = None) -> str | None:
    """读取环境变量，展示 `str | None` 的常见用法。"""
    return os.getenv(key, default)


def greet(name: str) -> str:
    """Day 1 类型注解练习函数。"""
    return f"Hello, {name}!"
