"""Join host-owned async work without losing cancellation semantics."""

from __future__ import annotations

import asyncio
from typing import Any


async def join_owned_task(awaitable: Any) -> Any:
    """Shield and join a host-owned async operation before propagating cancel."""
    task = asyncio.create_task(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def join_native_exit_tasks(error: BaseException) -> None:
    """Join native exit tasks attached to cancellation exceptions, if present."""
    pending = list(error.args)
    seen: set[int] = set()
    tasks: list[asyncio.Task] = []
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, asyncio.Task):
            tasks.append(value)
        elif isinstance(value, (tuple, list, set)):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
    for task in tasks:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                raise
        except BaseException:
            pass
