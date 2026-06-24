# 问题追踪与排期

> 由 00-control 于 2026-06-21 整理，基于 Hermes 代码审查 + 补充扫描。

## 优先级定义
- P0: 上线前必须修（功能阻断）
- P1: 本周修（重构/规范）
- P2: 下阶段修（优化）

## 问题列表

| ID | 优先级 | 模块 | 所属线程 | 问题 | 修法概要 |
|----|--------|------|---------|------|---------|
| #7 | P0 | frontend | 07-hardware | monitor 路径缺 /api/ 前缀 | endpoints.ts monitor 路径改为 /api/monitor/{port} |
| #A | P0 | docs | 08-infra | 没有 README.md | 写安装/启动/配置说明 |
| #1 | P1 | backend | 02-chat | routes.py 57KB 一个文件 | 按域拆 5 个路由文件 |
| #10 | P1 | frontend | 04-session | useChatStore.ts 41KB | 拆成 message/session/bookmark/export |
| #9 | P1 | frontend | 07-hardware | WorkbenchPanel.tsx 52KB | 按 tab 拆独立组件 |
| #4 | P1 | backend | 07-hardware | stub 工具返回不带入参 | 返回信息包含 query/top_k 等 |
| #2 | P1 | backend | 04-session | ChatRequest 字段冗余 | 移除 model/provider/base_url 字段 |
| #11 | P2 | frontend | 04-session | 持久化缺 beforeunload | 加最后刷盘 |
| #12 | P2 | build | 08-infra | requirements.txt 全写死 | 改 >= 版本 |
| #14 | P2 | build | 08-infra | 没有 pre-commit | 配 ruff/mypy hook |
| #8 | P2 | frontend | 01-app | globals.css 111KB | 拆 CSS Module |
