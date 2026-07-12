"""Shared constants for chunkers (hybrid / agent / multimodal).

Centralizes the inline fenced-code-block regex and sub-split separators that
were duplicated across HybridChunker, AgentChunker, and MultimodalChunker.
"""

import re

# Pattern for inline fenced code blocks (```...```).
# Used by placeholder protection so code blocks are never fragmented by
# RecursiveCharacterTextSplitter. Identical in all three chunkers.
INLINE_CODE_RE: re.Pattern = re.compile(r"```[\s\S]*?```")

# Separators for sub-splitting — kept in sync across HybridChunker /
# AgentChunker / MultimodalChunker.
#
# Notes:
# - "\n```\n" is absent because code blocks are protected with placeholders
#   (INLINE_CODE_RE) before splitting.
# - "\n\n**Q" (FAQ bold-question heading) and "\n\n|" (table boundary) are
#   placed before "\n\n" so FAQ questions and markdown tables stay attached
#   to their preceding heading/intro.
#
# (MultimodalChunker previously had an extra "\n<!-- PAGE:" entry at the
# start, but page markers are replaced with placeholders before sub-splitting
# runs, so that separator was dead code and is omitted here.)
SUB_SPLIT_SEPARATORS: list[str] = [
    "\n## ", "\n### ", "\n#### ",
    "\n\n**Q", "\n\n|",
    "\n\n", "\n", "。", ".", " ", "",
]
