"""Mention-hint tests: one alias line, never profile content."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.hardware_profile_models import HardwareProfile
from app.db.hardware_profile_store import HardwareProfileStore
from src.agent.hardware_mention import build_hardware_mention_hint, match_hardware_aliases


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'mention.sqlite'}")
    HardwareProfile.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    try:
        yield HardwareProfileStore(factory)
    finally:
        engine.dispose()


def _seed(store):
    store.upsert({
        "name": "ESP32-S3-DevKitC",
        "aliases": ["esp32s3", "ESP32-S3"],
        "mcu": "ESP32-S3",
        "voltage": "3.3V",
        "pins": {"GPIO4": "LED"},
        "peripherals": ["UART"],
        "sources": ["manual p12"],
        "rejected": ["5V logic"],
    })
    store.upsert({"name": "STM32 Blue Pill", "aliases": ["bluepill"], "mcu": "STM32F103"})


def test_matching_alias_case_insensitive_whole_token():
    assert match_hardware_aliases("我的 EsP32S3 板子", ["esp32s3"]) == ["esp32s3"]
    assert match_hardware_aliases("ESP32S3X 不是这个型号", ["esp32s3"]) == []
    assert match_hardware_aliases("board esp32s3_extra", ["esp32s3"]) == []


def test_cjk_adjacent_alias_matches(store):
    _seed(store)

    hint = build_hardware_mention_hint("我的ESP32-S3开发板串口打不开", store=store)

    assert "ESP32-S3" in hint
    assert "hardware-profile" in hint


def test_hint_has_no_profile_fields_or_values(store):
    _seed(store)

    hint = build_hardware_mention_hint("esp32s3 的 GPIO 怎么接？", store=store)

    assert "esp32s3" in hint
    assert "\n" not in hint
    for leaked in ("GPIO4", "LED", "UART", "manual p12", "5V logic", "3.3V"):
        assert leaked not in hint
    assert "ESP32-S3-DevKitC" not in hint


def test_no_mention_returns_empty(store):
    _seed(store)

    assert build_hardware_mention_hint("帮我看看这个 C 代码", store=store) == ""
    assert build_hardware_mention_hint("", store=store) == ""
    assert build_hardware_mention_hint("   ", store=store) == ""


def test_alias_from_another_profile(store):
    _seed(store)

    hint = build_hardware_mention_hint("bluepill 的 BOOT0 怎么接", store=store)

    assert "bluepill" in hint
    assert "ESP32" not in hint


def test_unknown_alias_is_ignored(store):
    _seed(store)

    assert build_hardware_mention_hint("raspberry pi pico", store=store) == ""


def test_store_failure_returns_empty():
    class BrokenStore:
        def list_aliases(self):
            raise RuntimeError("db down")

    assert build_hardware_mention_hint("esp32s3", store=BrokenStore()) == ""


def test_alias_with_control_chars_is_skipped():
    assert build_hardware_mention_hint("bad\nalias", store=_FakeStore(["bad\nalias"])) == ""


def test_longer_alias_is_preferred():
    hint = build_hardware_mention_hint(
        "ESP32-S3 and S3", store=_FakeStore(["S3", "ESP32-S3"])
    )

    assert "ESP32-S3、S3" in hint


class _FakeStore:
    def __init__(self, aliases):
        self._aliases = aliases

    def list_aliases(self):
        return [(f"hp_{index}", alias) for index, alias in enumerate(self._aliases)]
