"""
Hardware RAG Agent — BaseTool implementations for the ReAct Agent.

Tool list (v2):
  - wrappers.SearchDocsTool   : local KB retrieval (RAG)
  - wrappers.AuditPinsTool    : GPIO conflict / strapping check
  - web_search.WebSearchTool  : Tavily web search (optional)
  - build_firmware            : PlatformIO compile (Agent LLM writes code directly)
  - flash_firmware            : PlatformIO upload

tool_router.py is retained for the non-Agent path (CLI direct dispatch);
these BaseTool classes are the Agent path only.
"""
