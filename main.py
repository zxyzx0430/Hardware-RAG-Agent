#!/usr/bin/env python3
"""
Hardware RAG Agent — 主入口。

两种运行模式：
  1. CLI 模式（默认）：终端交互式对话
  2. Web 模式：FastAPI + uvicorn 服务

用法：
  python main.py                    # CLI 对话模式
  python main.py --web              # 启动 Web 服务
  python main.py --web --port 8080  # 指定端口
"""

import sys
import asyncio
import argparse
from typing import Optional, List

# ─── 确保 src 可导入 ───
sys.path.insert(0, ".")

from src.config.settings import settings
from src.llm.client import LLMClient, ChatMessage

# ─── 默认系统提示词 ───
DEFAULT_SYSTEM_PROMPT = """你是 Hardware RAG Agent——硬件知识 AI 助手。
你可以回答关于芯片参数、接线方案、驱动代码、器件对比和硬件排错的问题。
请基于提供的硬件文档给出准确、有来源标注的回答。
如果你不确定，请明确说"我不确定"，不要编造信息。"""


# ════════════════════════════════════════════
# CLI 模式
# ════════════════════════════════════════════

class CLIChat:
    """交互式 CLI 对话工具。"""

    def __init__(self, client: Optional[LLMClient] = None):
        self.client = client or LLMClient()
        self.history: List[ChatMessage] = []
        self.system_prompt = DEFAULT_SYSTEM_PROMPT

    def _print_welcome(self):
        print("=" * 60)
        print("  Hardware RAG Agent — CLI 对话工具")
        print(f"  模型: {self.client.model}  |  Base URL: {self.client.base_url}")
        print("  输入 /help 查看命令  |  /quit 退出")
        print("=" * 60)

    def _print_help(self):
        print("""
命令列表：
  /help         显示此帮助
  /quit         退出对话
  /clear        清空当前对话历史
  /model        显示当前模型配置
  /history      显示对话历史摘要
  /system <p>   设置系统提示词
        """.strip())

    async def run(self):
        self._print_welcome()
        while True:
            try:
                user_input = input("\n🧑 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n再见！")
                break

            if not user_input:
                continue

            if user_input.startswith("/"):
                cmd = user_input[1:].lower()
                if cmd == "quit" or cmd == "exit":
                    print("再见！")
                    break
                elif cmd == "help":
                    self._print_help()
                    continue
                elif cmd == "clear":
                    self.history.clear()
                    print("✅ 对话历史已清空")
                    continue
                elif cmd == "model":
                    print(f"  Model:     {self.client.model}")
                    print(f"  Base URL:  {self.client.base_url}")
                    print(f"  Temp:      {self.client.temperature}")
                    print(f"  Max Tokens:{self.client.max_tokens}")
                    continue
                elif cmd == "history":
                    print(f"  对话轮次: {len(self.history) // 2}")
                    for i, msg in enumerate(self.history, 1):
                        preview = msg.content[:80].replace("\n", " ")
                        print(f"  [{i}] {msg.role}: {preview}...")
                    continue
                elif cmd.startswith("system "):
                    self.system_prompt = user_input[7:].strip()
                    print("✅ 系统提示词已更新")
                    continue
                else:
                    print(f"⚠️ 未知命令: {user_input}")
                    continue

            # ── 调用 LLM ──
            print("\n🤖 Agent: ", end="", flush=True)
            full_response = []
            try:
                async for token in self.client.chat_stream(
                    user_message=user_input,
                    system_prompt=self.system_prompt,
                    history=self.history,
                ):
                    print(token, end="", flush=True)
                    full_response.append(token)
                print()
            except asyncio.TimeoutError:
                print("\n⚠️ 请求超时")
                continue
            except Exception as e:
                print(f"\n⚠️ 错误: {e}")
                continue

            # ── 保存到历史 ──
            response_text = "".join(full_response)
            self.history.append(ChatMessage(role="user", content=user_input))
            self.history.append(ChatMessage(role="assistant", content=response_text))


# ════════════════════════════════════════════
# Web 模式（FastAPI 骨架）
# ════════════════════════════════════════════

def create_app():
    """创建 FastAPI 应用（工厂模式，方便测试）。"""
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import HTMLResponse
    from fastapi.templating import Jinja2Templates
    from pathlib import Path

    app = FastAPI(title="Hardware RAG Agent", version="0.1.0")

    # 模板（后续周次实现前端时用）
    templates_dir = Path(__file__).parent / "templates"
    if templates_dir.exists():
        templates = Jinja2Templates(directory=str(templates_dir))
    else:
        templates = None

    @app.get("/")
    async def root():
        return {"status": "ok", "message": "Hardware RAG Agent API", "version": "0.1.0"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/v1/models")
    async def list_models():
        """获取可用模型列表（供前端下拉选择）。"""
        client = LLMClient()
        models = await client.list_models()
        return {"models": models}

    return app


# ════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Hardware RAG Agent")
    parser.add_argument("--web", action="store_true", help="启动 Web 服务模式")
    parser.add_argument("--port", type=int, default=None, help="Web 服务端口")
    parser.add_argument("--host", type=str, default=None, help="Web 服务主机")
    args = parser.parse_args()

    if args.web:
        # ── Web 模式 ──
        import uvicorn
        host = args.host or settings.host
        port = args.port or settings.port
        print(f"🌐 启动 Web 服务: http://{host}:{port}")
        print(f"📖 API 文档: http://{host}:{port}/docs")
        app = create_app()
        uvicorn.run(app, host=host, port=port)
    else:
        # ── CLI 模式 ──
        async def _run_cli():
            cli = CLIChat()
            await cli.run()

        try:
            asyncio.run(_run_cli())
        except KeyboardInterrupt:
            print("\n\n再见！")


if __name__ == "__main__":
    main()
