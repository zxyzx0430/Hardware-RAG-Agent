"""Industrial-grade Tool Runtime — 5-module toolkit.

Public API:
    ToolSpec, RiskLevel, ConfirmationRule             — tool_spec
    ToolResultEnvelope, ErrorDetail, ResultMetadata   — tool_result_envelope
    PermissionClassifier                              — permission_classifier
    AuditRecorder                                     — audit_recorder
    ToolRouter, register                              — tool_router
"""
from src.agent.core.toolkit.tool_spec import (
    ConfirmationRule,
    RiskLevel,
    ToolSpec,
)
from src.agent.core.toolkit.tool_result_envelope import (
    ErrorDetail,
    ResultMetadata,
    ToolResultEnvelope,
)
from src.agent.core.toolkit.permission_classifier import PermissionClassifier
from src.agent.core.toolkit.audit_recorder import AuditRecorder
from src.agent.core.toolkit.tool_router import ToolRouter, register

__all__ = [
    "ToolSpec",
    "RiskLevel",
    "ConfirmationRule",
    "ToolResultEnvelope",
    "ErrorDetail",
    "ResultMetadata",
    "PermissionClassifier",
    "AuditRecorder",
    "ToolRouter",
    "register",
]
