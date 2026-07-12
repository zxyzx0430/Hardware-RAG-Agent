# Checklist

> 用于 Task 5 完成后的系统性验证。逐条核验，通过则打勾；未通过则创建新 Task 修复后重新验证。

---

## 扫描覆盖度

- [x] 扫描覆盖了所有 `backend/app/api/*.py` 文件（19 个路由模块）
- [x] 扫描覆盖了 `backend/src/**/*.py`（含 agent / llm / chunker 等子模块，45 个文件）
- [x] 扫描覆盖了所有 `frontend/src/stores/*.ts` 文件（11 个 store）
- [x] 扫描覆盖了 `frontend/src/components/**/*.{ts,tsx}` 核心组件（39 个）
- [x] 扫描覆盖了 `docs/api-contract.md`
- [x] 扫描覆盖了 `docs/architecture-map.md`
- [x] 扫描覆盖了 `docs/thread-map.md`
- [x] 扫描覆盖了 `AGENTS.md`（通过 always_applied_workspace_rules 提供）
- [x] 扫描覆盖了 `docs/completed.md`
- [x] 扫描覆盖了 `docs/pitfalls.md`

## 三类问题识别

- [x] 引用断裂类问题已专门扫描（函数调用 / import 路径 / 模块依赖 / 变量作用域）→ 发现 6 项（含 permissionMode / tool_router title / crud.py 白名单等）
- [x] 过时注释类问题已专门扫描（docstring / 参数说明 / 行号引用 / TODO 残留）→ 发现 8 项（含 spec §1.1 / SANDBOX_UNAVAILABLE / 06-sandbox-scan.md 等）
- [x] 不一致类问题已专门扫描（文档 vs 代码 / 架构图 vs 代码 / 命名规范 / 接口定义 vs 使用）→ 发现 18 项（含 host=0.0.0.0 / architecture-map 9 处 / api-contract 缺路由等）

## 特别检查项

- [x] Docker 沙箱残留引用已检查（sandbox_routes / CodeExecutorTool / code_executor 配置 / docker 依赖）→ 发现 6 处残留（crud.py 白名单 / architecture-map 9 处 / api-contract SANDBOX_UNAVAILABLE / .gitignore / completed.md / 06-sandbox-scan.md）
- [x] `chat_routes.py` L176 `# TODO: ReAct loop` 是否已过时（Agent 路径是否已取代）→ 已被取代，spec §1.1 描述过时（T2-3）
- [x] `docs/api-contract.md` 中 DEPRECATED 契约（§5.21-5.23）的代码侧残留 → §5.21-5.23 已正确标 DEPRECATED，但 §2.14 错误码表 SANDBOX_UNAVAILABLE 未同步（公共-5）
- [x] `docs/review/06-sandbox-scan.md` 是否需归档到 `docs/archive/` → 是，整文档过时（公共-10）

## 报告格式

- [x] 报告含「扫描元信息」区（commit hash `48a6e5a` / 173 文件 / 稳定区 120 + 活跃区 53）
- [x] 报告按 T2 / T3 / T5 / T6 / 公共区 分组
- [x] 每个发现标注优先级（[必须修复] 6 / [建议修改] 17 / [仅供参考] 7 / [问题] 2）
- [x] 每个发现标注稳定性（稳定区 / ⚠️ 活跃区待二次扫描）
- [x] 活跃区发现记录了扫描时点 commit hash（`48a6e5a`）
- [x] 报告顶部列出「🔴 阻断 demo」问题（3 条：useChatStore 崩 / tool_router 缺 title / host=0.0.0.0）
- [x] 报告含总结段落（整体评价 + 数量统计 + P0/P1/P2 修复顺序）
- [x] 报告开头声明了与 `docs/review/*-scan.md` 旧报告的关系

## 原则遵守

- [x] 扫描过程未修改任何代码文件
- [x] 扫描过程未修改任何现有文档
- [x] 报告写入 `docs/reports/consistency-scan-2026-06-30.md`（一次性报告目录）
- [x] 报告已 git commit（commit `27cbc31`，message: `docs: 新增代码与文档一致性扫描报告 2026-06-30 (T1 主控线程 32 项发现，3 项阻断 demo)`）

## 下游可用性

- [x] 每个发现标注了责任线程（T2 / T3 / T5 / T6 / 公共）
- [x] 每个发现含「建议」修复方向（不只是指出问题）
- [x] 总结段含「修复顺序建议」（P0/P1/P2 三级，T1 据此分发任务）
