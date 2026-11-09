import pytest

from src.llm import model_catalog


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    model_catalog.clear_cache()
    yield
    model_catalog.clear_cache()


def test_normalize_capability_keeps_effort_levels():
    capability = model_catalog.normalize_capability({
        "reasoning": True,
        "thinkingFormat": "zai",
        "thinkingCanDisable": True,
        "thinkingEffortSupported": True,
        "thinkingRange": ["high", "medium"],
    })

    assert capability == {
        "reasoning": True,
        "can_disable": True,
        "effort_supported": True,
        "efforts": ["high", "medium"],
        "format": "zai",
    }


def test_normalize_capability_uses_default_efforts_without_range():
    capability = model_catalog.normalize_capability({
        "reasoning": True,
        "thinkingEffortSupported": True,
    })

    assert capability["efforts"] == ["low", "medium", "high"]


def test_catalog_omits_models_without_capabilities():
    catalog = model_catalog.normalize_catalog([
        {"id": "combo"},
        {"id": "deepseek", "capabilities": {
            "reasoning": True,
            "thinkingEffortSupported": False,
        }},
    ])

    assert set(catalog) == {"deepseek"}
    assert catalog["deepseek"]["effort_supported"] is False


@pytest.mark.asyncio
async def test_resolve_effort_uses_cached_catalog_and_rejects_unknown_level():
    model_catalog.remember_catalog(
        provider="p",
        base_url="https://example.test/v1",
        api_key="key",
        catalog={
            "glm": {
                "reasoning": True,
                "can_disable": True,
                "effort_supported": True,
                "efforts": ["low", "high"],
                "format": "zai",
            },
        },
    )

    assert await model_catalog.resolve_reasoning_effort(
        provider="p",
        base_url="https://example.test/v1",
        api_key="key",
        model="glm",
        requested="high",
    ) == "high"
    assert await model_catalog.resolve_reasoning_effort(
        provider="p",
        base_url="https://example.test/v1",
        api_key="key",
        model="glm",
        requested="medium",
    ) is None
    assert await model_catalog.resolve_reasoning_effort(
        provider="p",
        base_url="https://example.test/v1",
        api_key="key",
        model="unknown",
        requested="high",
    ) is None
