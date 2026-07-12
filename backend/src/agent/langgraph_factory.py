"""LangGraph Studio factory — no-arg agent builder for `langgraph dev`.

Reads LLM config from settings + registers default tools, then delegates
to create_hardware_agent_from_config. Enables LangGraph Studio's hot-reload
agent inspection UI without running the full FastAPI backend.

Spec: migrate-langchain-1x-new-api §Task 4.
"""
from __future__ import annotations

import logging

from src.agent.agent_factory import (
    AgentConfig,
    create_hardware_agent_from_config,
    ensure_default_tools_registered,
)
from src.agent.core.toolkit.tool_router import _TOOL_REGISTRY
from src.config.settings import settings

logger = logging.getLogger(__name__)


async def create_agent_for_studio() -> object:
    """Build a hardware RAG agent for LangGraph Studio (`langgraph dev`).

    No-arg factory: reads LLM config from settings, registers all default
    tools, delegates to create_hardware_agent_from_config. HITL disabled
    (Studio inspects graph topology, not interactive permission flow).
    """
    ensure_default_tools_registered()
    tools = list(_TOOL_REGISTRY.values())
    config = _build_studio_config(tools)
    logger.info("studio_agent_built model=%s tools=%d", config.model, len(tools))
    return await create_hardware_agent_from_config(config)


def _build_studio_config(tools: list) -> AgentConfig:
    """Construct AgentConfig from settings for LangGraph Studio."""
    return AgentConfig(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        tools=tools,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        enable_hitl=False,
    )
