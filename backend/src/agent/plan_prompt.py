"""System-prompt blocks for plan authoring, plan mode and approved-plan execution."""

from __future__ import annotations

import re
from typing import Any

MAX_APPROVED_PLAN_CHARS = 65_536
MAX_STEP_DETAIL_CHARS = 1_000
MAX_SUMMARY_CHARS = 2_000

_SOURCE_CITATION_RE = re.compile(r"\[\^\d+\](?:\(src\d+\))?|\[src\d+\]", re.IGNORECASE)


def strip_plan_source_citations(text: str) -> str:
    """Remove inline source markers from a plan field.

    Plans describe actions, not evidence claims; citations belong in ordinary
    chat answers. This also cleans older plans written before the prompt rule.
    """
    cleaned = _SOURCE_CITATION_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]+([，。；：、）])", r"\1", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


_PLAN_MODE_PROMPT = """## Plan mode (read-only)
You are in plan mode. Only read-only research tools are available. Do not write files, run commands, install or import skills, flash hardware, or write memory/todos.
Research the request enough to make a concrete plan. Your deliverable for any multi-step task that would write files, run commands, build, or touch hardware is a `submit_plan` tool call, not a prose plan. Do NOT write the plan steps as a normal assistant message and do NOT ask your questions only in text. Call `submit_plan` exactly once when you have enough evidence, and put every unresolved question in its `open_questions` field. Only answer directly for a simple informational question that needs no changes; do not force a plan for that.
After `submit_plan` succeeds, stop and wait for the user's decision. A plan is a proposal, not a permission grant: the user must approve a specific version before execution, and every tool call still follows the normal permission policy.

### Scanning discipline
- Start from the exact file or directory the user named. Prefer `grep`/`glob` with a specific pattern and a narrow `path` over scanning the repository root or a home directory.
- `list_files`/`glob`/`grep` skip dependency, build and cache directories by default (`node_modules`, `.venv`, `venv`, `.git`, `.pio`, `.platformio`, `dist`, `build`, `__pycache__`, `.pytest_cache`, ...). Keep those defaults unless the user explicitly asked to inspect one of those directories.
- Do not read whole large files. Locate the relevant section with `grep`, then read a small `offset`/`limit` window; plan mode caps each read at 3000 characters.
- A plan-mode turn has a bounded tool-output budget (~48000 estimated tokens across all tool calls). If a scan is large or the budget is nearly used, stop and submit a plan: put missing information in `open_questions` and name the directory or file the user should narrow down, instead of repeating broad scans.
- Never retry a failed broad scan with the same scope; narrow it or ask the user in `open_questions`.
- Once you can name the files, ordered steps and verification for the task, call `submit_plan`; do not keep scanning for completeness."""


_PLAN_AUTHORING_PROMPT = """## Plan authoring (every permission mode)
When the user asks for a plan, a proposal, a design, or to think it through before changing anything, research with read-only tools first, then call `submit_plan` exactly once with a complete plan. Do not write the plan as a normal assistant message, and put every unresolved question in `open_questions`.
`submit_plan` works in every permission mode: do NOT call `enter_plan_mode` just to submit a plan. After `submit_plan` succeeds, stop and wait for the user's decision; never start executing on your own.
If the user only asks a simple informational question, or explicitly tells you to just do the task, answer or proceed without forcing a plan.
Plan template: `title`; `summary` (conclusion first; Markdown allowed); `steps` (1-12, each with title/detail/verification; detail and verification may use Markdown); `risks`; `open_questions`.
Never include `[srcN]` or `[^N]` source citations inside any plan field; citations belong in ordinary chat answers, not in plans."""


def build_plan_authoring_prompt() -> str:
    """Return the plan-authoring section appended in every permission mode."""
    return _PLAN_AUTHORING_PROMPT


def build_plan_mode_prompt() -> str:
    """Return the plan-mode section appended to the system prompt."""
    return _PLAN_MODE_PROMPT


def _clip(value: Any, limit: int) -> str:
    text = strip_plan_source_citations(str(value or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_steps(steps: Any) -> list[str]:
    if not isinstance(steps, list):
        return []
    lines: list[str] = []
    for index, step in enumerate(steps[:12], start=1):
        if not isinstance(step, dict):
            continue
        lines.append(f"{index}. {_clip(step.get('title'), 200)}")
        detail = _clip(step.get("detail"), MAX_STEP_DETAIL_CHARS)
        if detail:
            lines.append(f"   {detail}")
        verification = _clip(step.get("verification"), 300)
        if verification:
            lines.append(f"   Verification: {verification}")
    return lines


def _render_list(label: str, values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return []
    lines = [f"{label}:"]
    lines.extend(f"- {_clip(value, 300)}" for value in values[:10])
    return lines


def format_plan_for_prompt(plan: dict[str, Any]) -> str:
    """Render one approved plan within a small prompt budget."""
    lines = [
        f"Title: {_clip(plan.get('title'), 200)}",
        f"Summary: {_clip(plan.get('summary'), MAX_SUMMARY_CHARS)}",
        "Steps:",
        *_render_steps(plan.get("steps")),
        *_render_list("Risks", plan.get("risks")),
        *_render_list("Open questions", plan.get("open_questions")),
    ]
    rendered = "\n".join(lines)
    return rendered[:MAX_APPROVED_PLAN_CHARS]


def build_approved_plan_prompt(plan: dict[str, Any]) -> str:
    """Return the approved-plan block, explicitly not a permission grant."""
    version = plan.get("version", 1)
    body = format_plan_for_prompt(plan)
    file_line = (
        f"\nA durable copy is saved at: {plan['file_path']}"
        if plan.get("file_path") else ""
    )
    return (
        f"## User-approved plan (v{version})\n"
        "The user approved this plan for the current session. Follow its steps in order, "
        "adapting only when evidence requires it.\n"
        "This approval is NOT a permission grant: file writes, commands, MCP, builds, and "
        "flashing still follow the normal permission policy.\n"
        f"{file_line}\n"
        "<approved_plan>\n"
        f"{body}\n"
        "</approved_plan>"
    )
