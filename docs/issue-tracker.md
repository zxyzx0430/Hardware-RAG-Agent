# 问题追踪与排期

> 由 00-control 于 2026-06-21 整理，基于 Hermes 代码审查 + 补充扫描。

## 优先级定义
- P0: 上线前必须修（功能阻断）
- P1: 本周修（重构/规范）
- P2: 下阶段修（优化）

## 问题列表

> 状态列由 2026-06-29 代码核实填充，核实方式见每条末尾括号。

| ID | 优先级 | 模块 | 所属线程 | 问题 | 修法概要 | 状态（2026-06-29 核实） |
|----|--------|------|---------|------|---------|------------------------|
| #7 | P0 | frontend | 07-hardware | monitor 路径缺 /api/ 前缀 | endpoints.ts monitor 路径改为 /api/monitor/{port} | 已修复（endpoints.ts L23-24 已加 /api/ 前缀） |
| #A | P0 | docs | 08-infra | 没有 README.md | 写安装/启动/配置说明 | 待办（根目录确认无 README.md） |
| #1 | P1 | backend | 02-chat | routes.py 57KB 一个文件 | 按域拆 5 个路由文件 | 已修复（拆为 9 个 *routes.py：chat/kb/hardware/build/tool/sandbox/mcp/search/feedback） |
| #10 | P1 | frontend | 04-session | useChatStore.ts 41KB | 拆成 message/session/bookmark/export | 已修复（拆为 8 个 store：useChat/useSession/useBookmark/useSettings/useKnowledge/useSerial/useLog/useApp） |
| #9 | P1 | frontend | 07-hardware | WorkbenchPanel.tsx 52KB | 按 tab 拆独立组件 | 未修复（移到 workbench/ 子目录但未拆分，目录下仅 1 文件 WorkbenchPanel.tsx） |
| #4 | P1 | backend | 07-hardware | stub 工具返回不带入参 | 返回信息包含 query/top_k 等 | 已修复（src/agent/tool_router.py 的 dispatch 有参数 Schema 校验 + 超时控制，非 stub） |
| #2 | P1 | backend | 04-session | ChatRequest 字段冗余 | 移除 model/provider/base_url 字段 | 未修复（chat_routes.py L42/49/50 仍保留 model/provider/base_url，实际用 header x-api-key/x-model 替代，字段冗余但未删） |
| #11 | P2 | frontend | 04-session | 持久化缺 beforeunload | 加最后刷盘 | 未修复（frontend/src 下无 beforeunload 引用） |
| #12 | P2 | build | 08-infra | requirements.txt 全写死 | 改 >= 版本 | 部分修复（已补全 aiofiles/python-multipart 等依赖，但用 == 锁版本而非 >=；review-result.md P2 又建议用 pip-compile 锁版本，两份文档矛盾，需 00-control 裁决） |
| #14 | P2 | build | 08-infra | 没有 pre-commit | 配 ruff/mypy hook | 待办（无 .husky 目录） |
| #8 | P2 | frontend | 01-app | globals.css 111KB | 拆 CSS Module | 未修复（globals.css 2610 行，未拆分） |

### 核实汇总

- 已修复：4 项（#7、#1、#10、#4）
- 未修复：5 项（#9、#2、#11、#8、#A 部分）
- 部分修复：1 项（#12）
- 待办：2 项（#A、#14）

### 注意事项

- **#12 与 review-result.md 矛盾**：issue-tracker 说"改 >= 版本"，review-result.md P2 说"用 pip-compile 锁版本"。实际代码用 `==` 锁版本（部分 `>=`）。需 00-control 裁决策略。
- **#2 的字段冗余**：ChatRequest 的 model/provider/base_url 字段虽保留但实际不用（用 header 替代）。删除会影响前端兼容性，需评估后再动。
