"""Tool-level tests for hardware_profile (temp SQLite only)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.hardware_profile_models import HardwareProfile
from app.db.hardware_profile_store import HardwareProfileStore
from src.agent.core.toolkit.tool_spec import RiskLevel
from src.agent.exceptions import ToolContext
from src.agent.tools.groups.hardware_profile import HardwareProfileTool


@pytest.fixture
def tool(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tool.sqlite'}")
    HardwareProfile.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    try:
        yield HardwareProfileTool(store=HardwareProfileStore(factory))
    finally:
        engine.dispose()


async def _call(tool, **args):
    return await tool.execute(args, ToolContext())


async def test_upsert_then_search_summary(tool):
    saved = await _call(
        tool,
        action="upsert",
        profile={
            "name": "ESP32-S3-DevKitC",
            "aliases": ["esp32s3"],
            "mcu": "ESP32-S3",
            "pins": {"GPIO4": "LED"},
            "sources": ["manual p12"],
        },
    )
    assert "error" not in saved
    assert saved["profile"]["name"] == "ESP32-S3-DevKitC"

    result = await _call(tool, action="search", query="esp32s3")

    assert result["profiles"][0]["id"] == saved["profile"]["id"]
    assert result["profiles"][0]["aliases"] == ["esp32s3"]
    assert "pins" not in result["profiles"][0]
    assert "ESP32-S3-DevKitC" in result["output"]


async def test_get_returns_full_profile_and_bumps_use_count(tool):
    profile_id = (
        await _call(
            tool,
            action="upsert",
            profile={
                "name": "STM32F103C8T6",
                "aliases": ["bluepill"],
                "mcu": "STM32F103",
                "pins": {"PA9": "USART1_TX"},
                "rejected": ["5V logic"],
            },
        )
    )["profile"]["id"]

    first = await _call(tool, action="get", id=profile_id)
    second = await _call(tool, action="get", id=profile_id)

    assert first["profile"]["pins"] == {"PA9": "USART1_TX"}
    assert first["profile"]["rejected"] == ["5V logic"]
    assert second["profile"]["use_count"] == first["profile"]["use_count"] + 1


async def test_get_missing_profile_returns_none(tool):
    result = await _call(tool, action="get", id="hp_missing")

    assert result["profile"] is None
    assert "未找到" in result["output"]


async def test_upsert_merges_second_call(tool):
    first = await _call(tool, action="upsert", profile={"name": "Board A", "voltage": "3.3V"})
    second = await _call(tool, action="upsert", profile={"name": "Board A", "mcu": "ESP32"})

    assert second["profile"]["id"] == first["profile"]["id"]
    assert second["profile"]["voltage"] == "3.3V"
    assert second["profile"]["mcu"] == "ESP32"


async def test_search_requires_query_and_valid_limit(tool):
    missing = await _call(tool, action="search")
    bad_limit = await _call(tool, action="search", query="esp32", limit=99)

    assert missing["error"] == "invalid_arguments"
    assert bad_limit["error"] == "invalid_arguments"


async def test_unsupported_action_is_rejected(tool):
    result = await _call(tool, action="delete")

    assert result["error"] == "invalid_arguments"


async def test_secret_is_rejected_and_not_stored(tool):
    result = await _call(
        tool,
        action="upsert",
        profile={"name": "Board", "sources": ["token=ghp_abcdefghijklmnopqrstuvwxyz0123"]},
    )

    assert result["error"] == "secret_rejected"
    assert (await _call(tool, action="search", query="Board"))["profiles"] == []


async def test_oversized_field_is_rejected(tool):
    result = await _call(
        tool, action="upsert", profile={"name": "Board", "clock": "9" * 201}
    )

    assert result["error"] == "invalid_arguments"


def test_tool_contract_metadata(tool):
    assert tool.name == "hardware_profile"
    assert tool.risk_level == RiskLevel.LOW
