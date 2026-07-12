# Tasks

> 执行顺序：Task 1 → (Task 2 ∥ Task 3 ∥ Task 4) → Task 5 → Task 6
> Task 2 / 3 / 4 可并行（独立扫描区域），建议派发给不同 sub-agent 并行执行

---

- [x] Task 1: 准备扫描环境
  - [x] SubTask 1.1: 获取当前 git short commit hash 作为扫描时点 → `48a6e5a`
  - [x] SubTask 1.2: 列出待扫描文件清单（backend/src 45 个 .py / frontend 39 个组件 / scripts 64 个脚本）
  - [x] SubTask 1.3: 报告骨架由 Task 5 汇总时统一写入（避免并行 sub-agent 写入冲突）

- [x] Task 2: 稳定区扫描（第一批，可并行）→ sub-agent A 返回 17 项发现（2 必须修复 / 9 建议修改 / 5 仅供参考 / 1 问题）
  - [ ] SubTask 2.1: 扫描 sandbox 残留引用
    - 检查 `backend/app/main.py` 是否仍 import `sandbox_router`
    - 检查 `backend/src/agent/tool_router.py` 是否仍注册 `CodeExecutorTool`
    - 检查 `frontend/src/components/settings/SettingsPage.tsx` 是否仍含 `code_executor` 选项
    - 检查 `frontend/src/stores/useSettingsStore.ts` 是否仍含 `code_executor` 配置
    - 检查 `backend/requirements.txt` 是否仍含 `docker==7.1.0`
    - 全局 grep `sandbox` / `code_executor` / `CodeExecutor` 关键字
  - [ ] SubTask 2.2: 扫描 `docs/api-contract.md` 中 DEPRECATED 契约的残留引用
  - [ ] SubTask 2.3: 扫描非活跃线程文件（01-app / 04-session / 08-infra 对应稳定模块）的引用断裂与过时注释
  - [ ] SubTask 2.4: 检查 `docs/review/06-sandbox-scan.md` 是否需归档

- [ ] Task 3: 活跃区扫描（第二批，可并行，标注「⚠️ 待二次扫描」）
  - [ ] SubTask 3.1: 扫描 T2 边界
    - `backend/src/agent/*.py`：函数调用关系 / import 路径 / docstring 一致性
    - `chat_routes.py` L121+ Agent 接入区：与 spec `2026-06-30-agent-react-design.md` 的接入点描述是否一致
    - 特别检查 `chat_routes.py` L176 `# TODO: ReAct loop` 是否已被 Agent 路径取代（若已取代则 TODO 是过时注释）
  - [ ] SubTask 3.2: 扫描 T3 边界
    - `scripts/reindex_*` / `scripts/audit_*`：与 `data/` 目录实际结构是否一致
    - `data/benchmark/` 下的 baseline 文件路径是否在脚本中正确引用
  - [ ] SubTask 3.3: 扫描 T5 边界
    - `frontend/src/stores/useChatStore.ts`：action 调用的 API 路径与后端路由定义比对
    - `frontend/src/components/chat/*`：组件 props 与 store state 类型一致性
  - [ ] SubTask 3.4: 扫描 T6 边界
    - `frontend/src/components/workbench/*`：与 `useWorkbenchBridge.ts` / `useSerialStore.ts` 的接口一致性
    - `backend/app/api/hardware_routes.py` + `build_routes.py`：路由签名与 `docs/api-contract.md` 比对

- [x] Task 4: 公共文档一致性扫描（可并行）→ sub-agent C 返回 8 项发现（2 必须修复 / 5 建议修改 / 1 仅供参考）
  - [ ] SubTask 4.1: `docs/api-contract.md` vs 实际路由实现对比
    - 遍历 `backend/app/api/*.py` 所有 `@router.{get,post,...}` 装饰器
    - 比对路径 / 方法 / 请求体 schema / 响应 schema
    - 标注契约中有但代码无、代码有但契约无的端点
  - [ ] SubTask 4.2: `docs/architecture-map.md` vs 实际代码对比
    - 路由表：architecture-map 列出的 11 个路由模块是否与 `backend/app/api/` 实际文件一致
    - store 表：列出的 8 个 store 是否与 `frontend/src/stores/` 实际文件一致
    - 组件清单：列出的核心组件是否与 `frontend/src/components/` 实际文件一致
  - [ ] SubTask 4.3: `docs/thread-map.md` vs `AGENTS.md`「Trae 6 线程系统」一致性
    - 文件归属声明是否冲突
    - Codex 8 线程 vs Trae 6 线程的映射关系是否准确
  - [ ] SubTask 4.4: `docs/completed.md` / `docs/pitfalls.md` 中提到的行号 / 文件路径是否仍准确
    - 抽查近 10 条 pitfalls 的「修复方式」中提到的文件:行号是否仍指向对应代码
    - 检查 completed.md 中提到的「已删除文件」是否真的不存在

- [x] Task 5: 汇总与报告生成
  - [x] SubTask 5.1: 按线程归属合并所有发现（T2 4 项 / T3 1 项 / T5 6 项 / T6 1 项 / 公共区 21 项，共 33 项）
  - [x] SubTask 5.2: 为每个发现标注优先级（6 必须修复 / 17 建议修改 / 7 仅供参考 / 2 问题）和稳定性
  - [x] SubTask 5.3: 在报告顶部列出「🔴 阻断 demo」3 条（useChatStore 崩 / tool_router 缺 title / host=0.0.0.0）
  - [x] SubTask 5.4: 撰写总结段落（整体评价 + 数量统计 + P0/P1/P2 修复顺序）
  - [x] SubTask 5.5: 完整报告写入 `docs/reports/consistency-scan-2026-06-30.md`（396 行）

- [x] Task 6: 提交与归档
  - [x] SubTask 6.1: git add 报告文件并 commit → `27cbc31`（commit message: `docs: 新增代码与文档一致性扫描报告 2026-06-30 (T1 主控线程 32 项发现，3 项阻断 demo)`）
  - [x] SubTask 6.2: T1 分发建议已写入报告「建议修复顺序」章节（P0/P1/P2 三级，按 Trae 线程归属分发）

---

# Task Dependencies

- Task 1 必须先完成（环境准备 + 报告骨架）
- Task 2 / Task 3 / Task 4 互相独立，可并行执行（建议派发 3 个 sub-agent 同时跑）
- Task 5 依赖 Task 2 + Task 3 + Task 4 全部完成
- Task 6 依赖 Task 5

---

# 并行执行建议

| Sub-Agent | 负责任务 | 扫描重点 |
|-----------|---------|---------|
| Agent A | Task 2 | 稳定区 + sandbox 残留 + DEPRECATED 契约 |
| Agent B | Task 3 | 活跃区 T2/T3/T5/T6 边界文件 |
| Agent C | Task 4 | 公共文档（api-contract / architecture-map / thread-map / pitfalls） |

3 个 sub-agent 并行扫描，主控线程汇总后写报告（Task 5）。
