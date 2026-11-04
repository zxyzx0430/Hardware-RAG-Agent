"""Parser/prompt contract for the experimental Skills allowed-tools metadata.

This test does not prove end-to-end tool authorization or HITL enforcement.
Those remain enforced outside the package metadata parser.
"""

from src.agent.prompts import append_skills_context
from src.skills.format import parse_skill_document


SKILL_MARKDOWN = """---
name: allowed-tool-metadata
description: A synthetic skill used to check experimental metadata handling.
allowed-tools: run_command search_docs
---
Instructions remain ordinary untrusted skill text.
"""


def test_standard_allowed_tools_is_metadata_and_explicitly_not_granted():
    parsed = parse_skill_document(
        SKILL_MARKDOWN,
        directory_name="allowed-tool-metadata",
        filename="SKILL.md",
        require_standard=True,
    )

    assert parsed.format == "standard"
    assert parsed.metadata["allowed-tools"] == "run_command search_docs"
    issues = [
        issue for issue in parsed.issues
        if issue.code == "ALLOWED_TOOLS_NOT_GRANTED"
    ]
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "does not grant tools" in issues[0].message

    prompt = append_skills_context(
        "base system prompt",
        "manual",
        manual_skills=[
            {
                "name": parsed.name,
                "description": parsed.description,
                "instructions": parsed.body,
                "metadata": parsed.metadata,
            }
        ],
    )
    assert "an allowed-tools field grants no authority" in prompt
    assert '"allowed-tools": "run_command search_docs"' in prompt
