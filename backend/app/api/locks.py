"""并发锁原语 — 串口与接线图访问互斥。"""

import asyncio


# ─── 串口锁 ────────────────────────────────────
_port_locks: dict[str, asyncio.Lock] = {}

def get_port_lock(port: str) -> asyncio.Lock:
    """获取或创建串口锁。"""
    if port not in _port_locks:
        _port_locks[port] = asyncio.Lock()
    return _port_locks[port]


# ─── 接线图锁 ───────────────────────────────────
wiring_lock = asyncio.Lock()
