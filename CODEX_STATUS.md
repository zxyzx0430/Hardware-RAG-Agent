# CODEX_STATUS.md — Hardware RAG Agent 进度报告
# 更新日期：2026-06-15

## 今天完成了什么
- ✅ **Week 1 Day 1：环境搭建 + FastAPI 骨架 + LLM API 调用**
- 项目目录结构搭建完成
- 配置系统：`.env` + `settings.py` + 热重载（`reload()` / `save_to_env()`）
- LLM 调用模块：`src/llm/client.py` — OpenAI-compatible，支持同步/流式、多轮历史
- CLI 对话工具：`main.py` — 交互式对话，支持 `/help`, `/clear`, `/model`, `/system` 等命令
- Web 骨架：FastAPI 工厂模式，`GET /` + `GET /health` + `GET /v1/models`
- 依赖安装完成：FastAPI / LangChain / ChromaDB / OpenAI / pytest 等
- **20 个 pytest 全部通过**（8 配置测试 + 12 LLM 测试）

## 当前卡在哪里
- 无阻塞。等待包工头确认后进入 Day 2 任务。

## 需要包工头决策的
- API Key / Base URL 配置：生产用哪个 relay/模型？
- 项目位置：当前在 `C:\Users\奶茶丸\Documents\agent`，是否需要迁移到 `E:\Desktop\hardware-rag-agent`？
- 依赖中有 chromadb、docling（有 PyTorch/cpp 依赖），是否需要提前确认安装没问题？
