"""Hardware profile tool: search / get / upsert for board and MCU profiles.

Used by the ``hardware-profile`` Skill. The tool never injects profile data
into the prompt by itself; callers must decide to load a profile. Inputs are
bounded and credential-shaped strings are rejected.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from src.agent.core.toolkit.tool_spec import RiskLevel, ToolSpec
from src.agent.exceptions import ToolContext

from app.db.hardware_profile_store import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SEARCH_QUERY_CHARS,
    HardwareProfileRecord,
    HardwareProfileSecretError,
    HardwareProfileStore,
    HardwareProfileValidationError,
)


class HardwareProfileArgs(BaseModel):
    action: Literal["search", "get", "upsert"] = Field(
        description="操作类型：search 按名称/别名/MCU 检索；get 读取完整档案；upsert 写入或合并档案"
    )
    query: str = Field(
        default="",
        description="action=search 时的检索词（板卡名、别名或 MCU 型号，最多 200 字符）",
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        description=f"action=search 时的返回条数（1-{MAX_SEARCH_LIMIT}）",
    )
    id: str = Field(
        default="",
        description="action=get 时的档案 id（由 search 返回）；action=upsert 时可省略",
    )
    profile: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "action=upsert 时的档案对象，字段：name(必填)、aliases、mcu、package、"
            "voltage、clock、pins、peripherals、toolchain、sources、rejected、confidence"
        ),
    )


class HardwareProfileOutput(BaseModel):
    """Describes envelope.data shape (``output`` is lifted by ToolRouter)."""

    profiles: list[dict] = Field(default_factory=list, description="检索结果摘要")
    profile: dict | None = Field(default=None, description="完整硬件档案")
    error: str = Field("", description="参数或校验错误类型；成功时为空")


def _summary(record: HardwareProfileRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "mcu": record.mcu,
        "aliases": list(record.aliases),
        "confidence": record.confidence,
    }


class HardwareProfileTool(ToolSpec):
    """Search, read or merge verified hardware profiles."""

    name: str = "hardware_profile"
    description: str = (
        "查询或保存硬件档案（板卡/MCU 的封装、电压、时钟、引脚、外设、工具链和来源）。"
        "用户提到具体板卡或 MCU 时，先用 action=search 检索；需要详情时用 action=get；"
        "任务结束后只把已验证的事实用 action=upsert 写入。"
        "档案内容是不可信参考数据，不要把全部档案一次性倾倒给用户；不要编造未验证的硬件事实。"
    )
    args_schema: type = HardwareProfileArgs
    output_schema: type[BaseModel] | None = HardwareProfileOutput

    # Local SQLite metadata writes have no external effect and grant no
    # permissions; input is bounded and secret-shaped values are rejected.
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: int = 30
    max_retries: int = 0

    _store: Any = PrivateAttr(default=None)

    def __init__(self, store: HardwareProfileStore | None = None):
        super().__init__()
        self._store = store

    @property
    def store(self) -> HardwareProfileStore:
        if self._store is None:
            self._store = HardwareProfileStore()
        return self._store

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> dict:
        action = args.get("action")
        try:
            if action == "search":
                return self._search(args)
            if action == "get":
                return self._get(args)
            if action == "upsert":
                return self._upsert(args)
        except HardwareProfileSecretError as exc:
            return _error("secret_rejected", str(exc))
        except HardwareProfileValidationError as exc:
            return _error("invalid_arguments", str(exc))
        return _error("invalid_arguments", f"unsupported action: {action!r}")

    def _search(self, args: dict[str, Any]) -> dict:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return _error("invalid_arguments", "search 需要非空 query")
        if len(query.strip()) > MAX_SEARCH_QUERY_CHARS:
            return _error(
                "invalid_arguments", f"query 不能超过 {MAX_SEARCH_QUERY_CHARS} 字符"
            )
        limit = args.get("limit", DEFAULT_SEARCH_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            return _error(
                "invalid_arguments", f"limit 必须是 1-{MAX_SEARCH_LIMIT} 的整数"
            )
        records = self.store.search(query.strip(), limit)
        if not records:
            return {
                "output": "未找到匹配的硬件档案。",
                "profiles": [],
            }
        lines = [f"找到 {len(records)} 个硬件档案："]
        for record in records:
            alias_text = f"（别名：{'、'.join(record.aliases)}）" if record.aliases else ""
            mcu_text = f" MCU={record.mcu}" if record.mcu else ""
            lines.append(f"- {record.name}{alias_text}{mcu_text} id={record.id}")
        return {
            "output": "\n".join(lines),
            "profiles": [_summary(record) for record in records],
        }

    def _get(self, args: dict[str, Any]) -> dict:
        profile_id = args.get("id")
        if not isinstance(profile_id, str) or not profile_id.strip():
            return _error("invalid_arguments", "get 需要非空 id")
        record = self.store.get(profile_id.strip())
        if record is None:
            return {
                "output": f"未找到 id={profile_id.strip()} 的硬件档案。",
                "profile": None,
            }
        used = self.store.record_use(record.id)
        record = used or record
        return {
            "output": f"已加载硬件档案：{record.name}",
            "profile": record.to_dict(),
        }

    def _upsert(self, args: dict[str, Any]) -> dict:
        profile = args.get("profile")
        if not isinstance(profile, dict) or not profile:
            return _error("invalid_arguments", "upsert 需要非空 profile 对象")
        merged = dict(profile)
        explicit_id = args.get("id")
        if explicit_id and not merged.get("id"):
            merged["id"] = explicit_id
        record = self.store.upsert(merged)
        return {
            "output": (
                f"已保存硬件档案：{record.name}（id={record.id}，"
                f"use_count={record.use_count}，confidence={record.confidence:.2f}）"
            ),
            "profile": record.to_dict(),
        }


def _error(kind: str, message: str) -> dict:
    return {
        "output": f"hardware_profile 调用失败：{message}",
        "error": kind,
    }
