"""
配置系统：.env + pydantic-settings + 热重载。
支持 settings.reload() 运行时刷新配置。
"""
from pathlib import Path
from typing import Optional
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict


# ─── 项目根目录 ───
ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用配置，优先级：显式传入 > .env 文件 > 环境变量 > 默认值。"""

    model_config = ConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── LLM ──
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.7, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")

    # ── Server ──
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    # ── Chroma ──
    chroma_persist_dir: str = Field(
        default=str(ROOT_DIR / "data" / "chroma"), alias="CHROMA_PERSIST_DIR"
    )

    # ── SQLite ──
    sqlite_db_path: str = Field(
        default=str(ROOT_DIR / "data" / "chat_history.db"), alias="SQLITE_DB_PATH"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 跟踪实际使用的 .env 路径，供 reload() 和 save_to_env() 使用
        env_file = kwargs.get("_env_file")
        if env_file:
            self._config_path = Path(env_file)
        else:
            self._config_path = ROOT_DIR / ".env"

    def reload(self) -> "Settings":
        """运行时热重载配置（从 .env 重新读取）。"""
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
    """获取全局单例配置。"""
    return Settings()


# 便捷引用
settings = get_settings()
