"""测试配置系统。"""
import os
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import Settings, ROOT_DIR


class TestSettings:
    """测试 Settings 配置系统。"""

    def test_default_values(self):
        """测试默认值是否合理。"""
        s = Settings()
        assert s.llm_base_url == "https://api.openai.com/v1"
        assert s.llm_model == "gpt-4o-mini"
        assert s.llm_temperature == 0.7
        assert s.llm_max_tokens == 4096
        assert s.host == "0.0.0.0"
        assert s.port == 8000

    def test_env_file_override(self, tmp_path):
        """测试 .env 文件覆盖默认值。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LLM_MODEL=gpt-4o\nLLM_TEMPERATURE=0.5\n", encoding="utf-8"
        )
        s = Settings(_env_file=str(env_file))
        assert s.llm_model == "gpt-4o"
        assert s.llm_temperature == 0.5
        # 未覆盖的字段仍为默认值
        assert s.llm_base_url == "https://api.openai.com/v1"

    def test_env_var_override(self, monkeypatch):
        """测试环境变量覆盖。"""
        monkeypatch.setenv("LLM_MODEL", "gpt-4-turbo")
        monkeypatch.setenv("LLM_BASE_URL", "https://custom.api.com/v1")
        s = Settings()
        assert s.llm_model == "gpt-4-turbo"
        assert s.llm_base_url == "https://custom.api.com/v1"

    def test_reload(self, tmp_path):
        """测试运行时热重载。"""
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_TEMPERATURE=0.3\n", encoding="utf-8")
        s = Settings(_env_file=str(env_file))
        assert s.llm_temperature == 0.3

        # 修改 .env 后 reload
        env_file.write_text("LLM_TEMPERATURE=0.9\n", encoding="utf-8")
        s.reload()
        assert s.llm_temperature == 0.9

    def test_save_to_env_adds_new_key(self, tmp_path):
        """测试 save_to_env 追加新 key。"""
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_KEY=old_value\n", encoding="utf-8")
        s = Settings(_env_file=str(env_file))
        # 给实例的 _config_path 改成临时路径
        object.__setattr__(s, "_config_path", env_file)
        s.save_to_env({"NEW_KEY": "new_value"})
        content = env_file.read_text(encoding="utf-8")
        assert "NEW_KEY=new_value" in content
        assert "EXISTING_KEY=old_value" in content

    def test_save_to_env_updates_existing(self, tmp_path):
        """测试 save_to_env 更新已有 key 的值。"""
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_TEMPERATURE=0.3\n", encoding="utf-8")
        s = Settings(_env_file=str(env_file))
        object.__setattr__(s, "_config_path", env_file)
        s.save_to_env({"LLM_TEMPERATURE": "1.0"})
        content = env_file.read_text(encoding="utf-8")
        assert "LLM_TEMPERATURE=1.0" in content
        assert "LLM_TEMPERATURE=0.3" not in content

    def test_ROOT_DIR_is_absolute(self):
        """确保 ROOT_DIR 指向项目根目录。"""
        assert ROOT_DIR.is_absolute()
        assert (ROOT_DIR / "main.py").exists()

    def test_chroma_persist_dir_default(self):
        """Chroma 默认持久化路径正确。"""
        s = Settings()
        expected = str(ROOT_DIR / "data" / "chroma")
        assert s.chroma_persist_dir == expected
