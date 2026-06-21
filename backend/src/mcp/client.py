"""MCP Client — stdio 传输层实现"""
import asyncio
import json
import logging
import uuid
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class MCPTool:
    """MCP 工具描述"""
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)

class MCPClient:
    """MCP 协议客户端，通过 stdio 传输层与 MCP Server 通信"""

    def __init__(self, server_config: dict):
        self.command = server_config.get("command", "")
        self.args = server_config.get("args", [])
        self.env = server_config.get("env", {})
        self.name = server_config.get("name", "unknown")
        self._process: Optional[asyncio.subprocess.Process] = None
        self._tools: list[MCPTool] = []
        self._request_id = 0
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._process and self._process.returncode is None

    @property
    def tools(self) -> list[MCPTool]:
        return self._tools

    async def connect(self) -> bool:
        """启动 MCP Server 子进程并完成握手"""
        try:
            cmd = [self.command] + self.args
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**__import__('os').environ, **self.env},
            )

            # 发送 initialize 请求
            result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hwrag-agent", "version": "1.0.0"},
            })

            if result:
                # 发送 initialized 通知
                await self._send_notification("notifications/initialized", {})
                self._connected = True

                # 发现工具
                await self._discover_tools()
                logger.info(f"MCP Server '{self.name}' 连接成功，发现 {len(self._tools)} 个工具")
                return True

            logger.error(f"MCP Server '{self.name}' 握手失败")
            await self.disconnect()
            return False

        except Exception as e:
            logger.error(f"MCP Server '{self.name}' 连接失败: {e}")
            await self.disconnect()
            return False

    async def _discover_tools(self):
        """发现 MCP Server 提供的工具"""
        result = await self._send_request("tools/list", {})
        if result and "tools" in result:
            self._tools = [
                MCPTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                )
                for t in result["tools"]
            ]

    async def call_tool(self, tool_name: str, args: dict) -> dict:
        """调用 MCP Server 的工具"""
        # P1: ensure stdio process is alive before calling, reconnect if crashed
        await self._ensure_alive()
        if not self.connected:
            return {"error": "MCP Server 未连接"}

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": args,
        })

        if result and "content" in result:
            # 提取文本内容
            texts = []
            for item in result["content"]:
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
            return {"output": "\n".join(texts), "raw": result}

        return {"output": str(result), "raw": result}

    async def reconnect(self):
        """Restart stdio process after a crash (P1)."""
        try:
            if self._process and self._process.returncode is None:
                self._process.terminate()
                await self._process.wait()
        except Exception:
            pass
        self._connected = False
        self._process = None
        await self.connect()

    async def _ensure_alive(self):
        """Ensure stdio process is alive, reconnect if crashed (P1)."""
        if not self._process or self._process.returncode is not None:
            await self.reconnect()

    async def _send_request(self, method: str, params: dict) -> Optional[dict]:
        """发送 JSON-RPC 请求"""
        if not self._process or not self._process.stdin:
            return None

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

        try:
            message = json.dumps(request) + "\n"
            self._process.stdin.write(message.encode())
            await self._process.stdin.drain()

            # 读取响应（带超时）
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=30.0
            )

            if not response_line:
                return None

            response = json.loads(response_line.decode())

            if "error" in response:
                logger.error(f"MCP 错误: {response['error']}")
                return None

            return response.get("result")

        except asyncio.TimeoutError:
            logger.error(f"MCP 请求超时: {method}")
            return None
        except Exception as e:
            logger.error(f"MCP 请求失败: {method} - {e}")
            return None

    async def _send_notification(self, method: str, params: dict):
        """发送 JSON-RPC 通知（无 id，不期望响应）"""
        if not self._process or not self._process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        try:
            message = json.dumps(notification) + "\n"
            self._process.stdin.write(message.encode())
            await self._process.stdin.drain()
        except Exception as e:
            logger.error(f"MCP 通知失败: {method} - {e}")

    async def disconnect(self):
        """优雅关闭 MCP Server"""
        self._connected = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass
            self._process = None
        self._tools = []
