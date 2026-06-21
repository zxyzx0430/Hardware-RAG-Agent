"""
配置系统：.env + pydantic-settings + 热重载。
支持 settings.reload() 运行时刷新配置。

Week 1 对照：
- Day 1：配置文件读取、路径管理、类型注解的工程化版本
- Day 3：LLM 默认 API Key / Base URL / Model 从这里进入程序
- Day 5：Web 服务 Host / Port 从这里读取
- Day 7：这一文件需要被 pytest 覆盖，见 `tests/test_settings.py`
"""
from pathlib import Path
from typing import Optional
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict


# 这里是 Day 1 “路径管理”的正式工程版。
# 用 pathlib 锁定项目根目录，后面 `.env`、`data/`、数据库路径都从这里出发。
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """
    应用配置，优先级：显式传入 > .env 文件 > 环境变量 > 默认值。

    你可以把它理解成“全项目公共设置中心”。
    程序里凡是“会变、会配置、不能写死”的东西，原则上都应该往这里收。
    """

    model_config = ConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Day 3：这些是 LLM 默认配置。
    # 如果运行时没有从请求头传新的值，就会回退到这里。
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.7, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")

    # Day 5：FastAPI 服务启动时需要的监听地址和端口。
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    # 这些属于后续周次要用到的默认路径。
    # 现在先收口到配置层，后面扩展时不需要到处找字符串。
    chroma_persist_dir: str = Field(
        default=str(ROOT_DIR / "data" / "chroma"), alias="CHROMA_PERSIST_DIR"
    )

    # ── SQLite ──
    sqlite_db_path: str = Field(
        default=str(ROOT_DIR / "data" / "chat_history.db"), alias="SQLITE_DB_PATH"
    )

    # Embedding
    # 不配置则跳过向量化功能
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_base_url: str = Field(default="https://api.openai.com/v1", alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")

    # 附件文本截断上限（字符数）
    max_attachment_chars: int = Field(default=8000, alias="MAX_ATTACHMENT_CHARS")

    # 日志级别
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 跟踪实际使用的 .env 路径，供 reload() 和 save_to_env() 使用
        env_file = kwargs.get("_env_file")
        if env_file:
            self._config_path = Path(env_file)
        else:
            self._config_path = ROOT_DIR / ".env"

        # 将相对路径转换为基于 ROOT_DIR 的绝对路径
        chroma = Path(self.chroma_persist_dir)
        if not chroma.is_absolute():
            self.chroma_persist_dir = str(ROOT_DIR / chroma)

        sqlite = Path(self.sqlite_db_path)
        if not sqlite.is_absolute():
            self.sqlite_db_path = str(ROOT_DIR / sqlite)

    def reload(self) -> "Settings":
        """
        运行时热重载配置（从 .env 重新读取）。

        对应 Day 3 / Day 5 的一个关键思想：
        配置不是只能在程序启动前决定，运行中也可能刷新。
        """
        new = Settings(_env_file=self._config_path)
        for key, val in new.model_dump().items():
            setattr(self, key, val)
        return self

    def save_to_env(self, overrides: Optional[dict] = None) -> None:
        """
        智能写入 .env 文件：
        - 保留原注释和空行
        - 只更新 VALUE
        - 不存在的 key 追加到末尾

        这一块让配置修改更像“产品行为”，而不是粗暴覆盖文件。
        """
        overrides = overrides or {}
        env_path = self._config_path
        if not env_path.exists():
            env_path.write_text("", encoding="utf-8")

        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
        existing_keys = set()
        new_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            if "=" not in stripped:
                new_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            existing_keys.add(key)
            if key in overrides:
                new_lines.append(f"{key}={overrides[key]}\n")
            else:
                new_lines.append(line)

        # 追加不存在的 key
        for key, val in overrides.items():
            if key not in existing_keys:
                new_lines.append(f"{key}={val}\n")

        env_path.write_text("".join(new_lines), encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    """
    获取全局单例配置。

    作用是避免程序每次用配置时都重新解析 `.env`。
    """
    return Settings()


# 便捷引用
settings = get_settings()
