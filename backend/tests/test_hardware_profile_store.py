"""Store tests for hardware profiles (temp SQLite only)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.hardware_profile_models import HardwareProfile
from app.db.hardware_profile_store import (
    HardwareProfileSecretError,
    HardwareProfileStore,
    HardwareProfileValidationError,
)


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'profiles.sqlite'}")
    HardwareProfile.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    try:
        yield HardwareProfileStore(factory)
    finally:
        engine.dispose()


def _create_board(store, **overrides):
    payload = {
        "name": "ESP32-S3-DevKitC",
        "aliases": ["esp32s3"],
        "mcu": "ESP32-S3",
        "package": "QFN56",
        "voltage": "3.3V",
        "clock": "240MHz",
        "pins": {"GPIO4": "LED"},
        "peripherals": ["UART"],
        "toolchain": "esp-idf 5.x",
        "sources": ["manual p12"],
        "rejected": ["5V logic"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return store.upsert(payload)


def test_upsert_creates_profile(store):
    record = _create_board(store)

    assert record.id.startswith("hp_")
    assert record.name == "ESP32-S3-DevKitC"
    assert record.mcu == "ESP32-S3"
    assert record.aliases == ("esp32s3",)
    assert record.pins == {"GPIO4": "LED"}
    assert record.confidence == 0.9
    assert record.use_count == 1
    assert record.last_used_at is not None
    assert store.get(record.id) == record


def test_upsert_by_name_merges_only_non_empty_fields(store):
    first = _create_board(store)

    merged = store.upsert({
        "name": "esp32-s3-devkitc",
        "aliases": ["S3 board"],
        "mcu": "",
        "voltage": "",
        "clock": "240 MHz",
        "confidence": 0.4,
    })

    assert merged.id == first.id
    assert merged.name == "esp32-s3-devkitc"
    assert merged.voltage == "3.3V"  # empty incoming value never clears
    assert merged.clock == "240 MHz"
    assert merged.mcu == "ESP32-S3"
    assert set(merged.aliases) == {"esp32s3", "S3 board"}
    assert merged.confidence == 0.9  # never decreases
    assert merged.use_count == 2


def test_upsert_by_alias_reuses_existing_profile(store):
    first = store.upsert({"name": "STM32 Blue Pill", "aliases": ["bluepill"]})

    merged = store.upsert({"name": "BluePill V2", "aliases": ["BluePill"]})

    assert merged.id == first.id
    assert merged.name == "BluePill V2"
    assert store.search("bluepill")[0].id == first.id


def test_upsert_merges_pins_lists_and_rejected(store):
    first = _create_board(store)

    merged = store.upsert({
        "name": "ESP32-S3-DevKitC",
        "pins": {"GPIO4": "", "GPIO5": "BOOT"},
        "peripherals": ["uart", "I2C"],
        "sources": ["manual p12", "schematic rev B"],
        "rejected": ["5V logic", "assumed 5V tolerant"],
    })

    assert merged.id == first.id
    assert merged.pins == {"GPIO4": "LED", "GPIO5": "BOOT"}
    assert set(merged.peripherals) == {"UART", "I2C"}
    assert set(merged.sources) == {"manual p12", "schematic rev B"}
    assert set(merged.rejected) == {"5V logic", "assumed 5V tolerant"}


def test_upsert_never_lowers_confidence(store):
    _create_board(store, confidence=0.8)

    merged = store.upsert({"name": "ESP32-S3-DevKitC", "confidence": 0.2})

    assert merged.confidence == 0.8


def test_search_matches_name_alias_and_mcu(store):
    _create_board(store)
    store.upsert({"name": "STM32F103C8T6", "aliases": ["bluepill"], "mcu": "STM32F103"})
    store.upsert({"name": "ESP32-C3-DevKitM", "aliases": ["c3"], "mcu": "ESP32-C3"})

    by_name = store.search("stm32f103c8t6")
    assert [item.name for item in by_name] == ["STM32F103C8T6"]

    by_alias = store.search("bluepill")
    assert [item.name for item in by_alias] == ["STM32F103C8T6"]

    by_mcu = store.search("ESP32-C3")
    assert [item.name for item in by_mcu] == ["ESP32-C3-DevKitM"]

    broad = store.search("esp32", limit=2)
    assert len(broad) == 2
    assert {item.name for item in broad} == {"ESP32-S3-DevKitC", "ESP32-C3-DevKitM"}


def test_search_rejects_blank_query_and_bad_limit(store):
    assert store.search("   ") == []
    with pytest.raises(HardwareProfileValidationError):
        store.search("esp32", limit=0)
    with pytest.raises(HardwareProfileValidationError):
        store.search("x" * 201)


def test_record_use_increments(store):
    record = _create_board(store)

    used = store.record_use(record.id)

    assert used.use_count == record.use_count + 1
    assert store.record_use("hp_missing") is None


def test_secret_like_content_is_rejected(store):
    with pytest.raises(HardwareProfileSecretError):
        store.upsert({
            "name": "ESP32-S3-DevKitC",
            "sources": ["api_key=sk-live-abcdefghijklmnopqrstuvwxyz"],
        })

    assert store.search("ESP32") == []


def test_invalid_payloads_are_rejected(store):
    with pytest.raises(HardwareProfileValidationError):
        store.upsert({"name": ""})
    with pytest.raises(HardwareProfileValidationError):
        store.upsert({"name": "Board", "clock": "x" * 201})
    with pytest.raises(HardwareProfileValidationError):
        store.upsert({"name": "Board", "pins": ["GPIO4"]})
    with pytest.raises(HardwareProfileValidationError):
        store.upsert({"name": "Board", "confidence": 2})
    with pytest.raises(HardwareProfileValidationError):
        store.upsert({"name": "Board\nX"})


def test_merged_alias_overflow_is_rejected(store):
    _create_board(store, aliases=[f"alias{i}" for i in range(20)])

    with pytest.raises(HardwareProfileValidationError):
        store.upsert({"name": "ESP32-S3-DevKitC", "aliases": ["one-more"]})

    stored = store.search("ESP32-S3-DevKitC")
    assert len(stored) == 1
    assert len(stored[0].aliases) == 20
    assert "one-more" not in stored[0].aliases


def test_store_creates_table_lazily(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lazy.sqlite'}")
    factory = sessionmaker(bind=engine)
    try:
        lazy_store = HardwareProfileStore(factory)
        record = lazy_store.upsert({"name": "Lazy Board"})
        assert lazy_store.get(record.id).name == "Lazy Board"
    finally:
        engine.dispose()
