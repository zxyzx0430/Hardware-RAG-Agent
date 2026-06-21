# AGENTS.md - Hardware RAG Agent

## Project Overview
Hardware RAG Agent — 面向嵌入式开发者的硬件知识库 AI Agent。
基于官方芯片手册做 RAG 检索，回答硬件参数/接线方案、生成驱动代码、审查代码问题。
用户自配 API Key + 自选模型，回答标注来源。

## Deploy Model

该项目是**本地自部署项目（self-hosted）**，用户下载后在自己的电脑上运行。
已计划开源到 GitHub。

### 安全立场

- **需要防护的**：本地风险（XSS、API Key 泄露、文件注入、SQL 注入）—— 用户浏览器扩展可能窃取凭证，恶意文档可能包含脚本
- **不需要管的**：网络攻击（DDoS、CSRF、HTTPS、CORS 硬化、暴力破解、请求频率限制）—— 服务只监听 127.0.0.1，不暴露到公网
- **性能方面**：不需要高并发优化、不需要分布式缓存、不需要CDN — 面向嵌入式开发者的硬件知识库 AI Agent。
基于官方芯片手册做 RAG 检索，回答硬件参数/接线方案、生成驱动代码、审查代码问题。
用户自配 API Key + 自选模型，回答标注来源。

## Role Division

Codex 和 Trae 权限对等，均可读写代码和文档。唯一区别：

| 角色 | 负责 |
|------|------|
| **Codex (00-control)** | 方向把控、接口契约最终裁决、PLUR 长期记忆维护、跨线程冲突协调、PR 审核与合并 |
| **Trae** | 以上除 00-control 之外的所有工作 |

双方共同维护：全部文档（api-contract.md / pitfalls.md / completed.md / threads/*.md / workflow 等）和全部代码。

## Shared Documents (All Threads)

> 开工写代码前，先读 docs/completed.md 了解项目当前进度。

## 踩坑文件使用规则

- **问题定位阶段**：先自己分析，卡住了再翻 pitfalls.md 看有没有前人踩过
- **修复阶段**：确定根因后，翻 pitfalls.md 的“下次注意”看有没有同类型教训
- **修复完成**：无重复则追加到 pitfalls.md

- docs/completed.md — 项目完成记录（各线程完成度/缺口/已知问题）
- docs/pitfalls.md — 踩坑唯一来源，开工先读，修 bug 后追加
- docs/api-contract.md — 接口契约，改接口先改文档
- docs/thread-map.md — 线程归属与范围（Codex 维护，Trae 参考）

## Thread Overview
| 线程 | 职责 |
|------|------|
| 00-control | 主控/契约/PLUR |
| 01-app | 布局/导航/主题 |
| 02-chat | SSE 流式聊天 |
| 03-knowledge | 知识库 RAG |
| 04-session | 持久化/设置 |
| 05-agent | LangGraph Agent |
| 06-sandbox | 沙箱执行 |
| 07-hardware | 硬件工作台 |
| 08-infra | Docker/CI/日志 |

## Development
前端访问 http://127.0.0.1:5173，Vite 自动把 /api/* 代理到后端。
- Backend: cd backend && python main.py --web --port 58080
- Frontend: cd frontend && npx vite --port 5173

## Project Structure
- backend/ — FastAPI + LangChain + ChromaDB
- frontend/ — React + TypeScript + Vite + Tailwind + Zustand
- scripts/ — Dev helper scripts
- data/ — Knowledge base PDFs and DB
- docs/ — Docs, roadmap, API contracts

## 沟通原则（重要）

### 理解需求的方式

用户不是技术人员，描述需求可能不精确、不完整、非技术用语。我的职责是先理解意图，再翻译成方案。

- **先理解，再行动**：收到需求后，先用大白话复述一遍我的理解，确认对了再动手
- **不问技术问题**：不问“用没用过 Git”、“懂不懂 SQL”这类问题。只说“项目里有些临时文件要不要删”
- **给选择，不给黑盒**：方案类的决定（装什么 skill、删什么文件），列成简单的选项让用户选
- **进度透明**：长时间任务每做完一步说一声在干什么，不要闷头跑完才告诉用户
- **主动发现**：用户说“帮我看看项目结构”，我要主动发现临时文件、.gitignore 缺漏、该删的旧代码，列出来让用户决定，而不是抛一堆技术细节
- **容忍模糊**：用户说“去github看看”，意思是“帮我找有用的工具”；用户说“看看代码有没有安全问题”，意思是“帮我检查一下会不会出事”。先猜再确认，不要让用户补充技术细节

## Language
- 回复默认中文
- 代码和注释用英文
