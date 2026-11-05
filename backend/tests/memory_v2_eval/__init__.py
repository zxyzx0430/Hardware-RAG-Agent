"""Memory v2 evaluation harness (deterministic, product-independent).

This package owns scenario data, a reference backend used only to prove the
harness itself is consistent, metric computation and report rendering.  It
never imports Memory v1.1 extraction code and never mutates product modules.
"""

from __future__ import annotations

SCHEMA_VERSION = "memory-v2-eval/1"

__all__ = ["SCHEMA_VERSION"]
