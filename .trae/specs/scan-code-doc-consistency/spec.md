# 代码与文档一致性扫描 Spec

> Change-ID: scan-code-doc-consistency
> Owner: T1 主控线程
> Date: 2026-06-30
> Mode: Spec（仅产出报告，不改代码）

---

## Why

项目处于 Trae 6 线程并行冲刺阶段（6.30 - 7.15 字节创造力大赛 demo），T2 / T3 / T5 / T6 同时修改代码，已完成模块与新模块之间存在引用断裂、过时注释、文档与代码不一致的风险。2026-06-30 单日已产生 5+ 条踩坑（sse_adapter 字段丢失 / Agent 路径未触发 / streaming tool_calls 分块 / 受保护导入 NameError / DeepEval 伪异步），表明代码处于高频变动期。

作为主控线程（T1）需要在 demo 冲刺前半段获取一份全景一致性快照，识别需各线程修复的问题，避免 demo 演示时暴露回归缺陷。

## What Changes

- **新增**：一份按 Trae 线程归属分组的代码与文档一致性扫描报告 `docs/reports/consistency-scan-2026-06-30.md`
- **不修改任何代码**：T1 主控线程不直接写代码（依据 AGENTS.md「Role Division」），只识别问题并标注责任线程，由对应 Trae 线程后续修复
- **不修改现有文档**：扫描结果以独立报告形式归档到 `docs/reports/`（一次性报告目录，不长期维护）
- **二次扫描机制**：对活跃线程区域的扫描结果标注「⚠️ 待二次扫描」，并记录扫描时点 commit hash，由 T1 在对应线程宣布该区域稳定后重新触发

## Impact

- **Affected specs**：无（本任务输出报告，不改 spec）
- **Affected code**：无代码改动；扫描覆盖以下范围
  - 后端 Python：`backend/app/api/*.py`、`backend/src/**/*.py`
  - 前端 TS：`frontend/src/stores/*.ts`、`frontend/src/components/**/*.{ts,tsx}`
  - 关键文档：`docs/api-contract.md`、`docs/architecture-map.md`、`docs/thread-map.md`、`AGENTS.md`、`docs/completed.md`、`docs/pitfalls.md`
- **Affected threads**：输出报告按 T2 / T3 / T5 / T6 归属分组，每个发现标注责任线程，T1 据此分发修复任务

## ADDED Requirements

### Requirement: 三类问题识别

扫描 SHALL 识别并标注以下三类问题：

**1. 引用断裂（reference break）**
- 函数调用关系异常：调用方引用了已删除 / 重命名的函数
- 变量作用域冲突：跨模块 import 的符号在目标模块不存在或已重命名
- 模块依赖错误：import 路径与实际文件路径不一致；循环依赖
- **特别检查**：`docs/completed.md` 2026-06-30 记录的 Docker 沙箱删除是否仍有残留引用（`sandbox_routes` / `CodeExecutorTool` / `code_executor` 配置项 / `docker==7.1.0` 依赖）

**2. 过时注释（stale comment）**
- 函数 docstring 描述与实际行为不符
- 参数说明缺失或类型注解与文档不一致
- 注释中提到的行号 / 文件路径与当前代码不符
- TODO / FIXME 标注的功能已实现但 TODO 未删除（例如 `chat_routes.py` L176 的 `# TODO: ReAct loop` 是否已被 Agent 路径取代）

**3. 不一致（inconsistency）**
- **文档 vs 代码**：`docs/api-contract.md` 描述的接口（路径 / 方法 / 请求体 / 响应）与实际路由实现冲突
- **架构图 vs 代码**：`docs/architecture-map.md` 列出的路由表 / store 表 / 组件表与实际代码不符
- **线程归属 vs 实际**：`docs/thread-map.md` 声称的文件归属与 AGENTS.md「Trae 6 线程系统」定义冲突
- **命名规范**：违反 AGENTS.md「代码规范（防屎山）」（函数 > 10 行、文件 > 300 行、魔法数字、缺类型注解、max-params > 3）
- **接口定义 vs 使用**：前端 store action 调用的 API 路径与后端路由定义不匹配

### Requirement: 增量扫描策略

扫描 SHALL 采用增量策略，按代码稳定性分两批处理：

**第一批（稳定区，立即扫描）**：
- 已废弃模块的残留引用（sandbox 相关，T4 未启动的 Dockerfile / README 等）
- T1 不写代码，故非 T2 / T3 / T5 / T6 边界内的文件视为稳定
- `docs/api-contract.md` 中标记 DEPRECATED 的契约
- 非活跃线程文件（01-app / 04-session / 08-infra 对应的稳定模块）

**第二批（活跃区，标注「⚠️ 待二次扫描」）**：
- T2 边界：`backend/src/agent/*` + `chat_routes.py` L121+ Agent 区
- T3 边界：`data/` + `scripts/reindex_*` + `scripts/audit_*`
- T5 边界：`frontend/src/stores/useChatStore.ts` + `frontend/src/components/chat/*`
- T6 边界：`frontend/src/components/workbench/*` + `backend/app/api/hardware_routes.py` + `build_routes.py`

对第二批区域：扫描结果在报告中标注「⚠️ 活跃区，待二次扫描」，并记录扫描时点（git commit hash 短 SHA），由 T1 在对应线程宣布稳定后重新触发。

### Requirement: 报告格式与优先级标注

报告 SHALL 遵循以下结构（优先级标注遵循 chinese-code-review 技能规范）：

```
# 代码与文档一致性扫描报告（2026-06-30）

## 扫描元信息
- 扫描时点 commit: <git short hash>
- 扫描文件数: N（稳定区 M / 活跃区 K）
- 扫描人: T1 主控线程

## 🔴 阻断 demo 问题（若有）
（顶部置顶，最多 3 条，需立即修复）

## 按线程归属分组

### T2 - Agent 全链路

#### [必须修复] <问题标题>
- 文件: <path>:<line>
- 类别: 引用断裂 / 过时注释 / 不一致
- 现象: <描述>
- 建议: <修复方向>
- 稳定性: 稳定区 / ⚠️ 活跃区待二次扫描

#### [建议修改] ...
#### [仅供参考] ...
#### [问题] ...（需作者解释意图）

### T3 - 数据加 RAG
...
### T5 - 前端体验
...
### T6 - 硬件工作台
...
### 跨线程公共区
（api-contract.md / architecture-map.md / AGENTS.md 等公共文档的不一致）

## 总结
- 整体评价（一句话）
- [必须修复] 数量: X
- [建议修改] 数量: Y
- [仅供参考] 数量: Z
- [问题] 数量: W
- 建议修复顺序: <按 demo 优先级排序>
```

优先级定义：

| 标记 | 含义 | 何时使用 |
|------|------|---------|
| **[必须修复]** | 不修不能合 / 阻断 demo | import 失败、路由 404、数据丢失风险、安全漏洞 |
| **[建议修改]** | 本次或下次迭代修复 | 性能问题、可维护性、缺校验、违反代码规范 |
| **[仅供参考]** | 不改也行 | 命名优化、风格建议、替代方案 |
| **[问题]** | 需作者解释意图 | 不确定作者意图，先问再评 |

### Requirement: 不修改代码原则

扫描过程 SHALL NOT 修改任何代码文件。所有发现以报告形式记录，由 T1 分发给对应 Trae 线程修复。

扫描 sub-agent 若发现需立即修复的阻断性问题（如 `import` 失败导致模块加载不了、路由完全不可用），SHALL 在报告中标注「🔴 阻断 demo」并置于报告顶部，但不自行修复。

### Requirement: 与既有扫描报告的关系

`docs/review/01-app-scan.md` ~ `08-infra-scan.md` 是 Codex 8 线程体系的旧扫描报告（按 Codex 线程分）。本扫描不覆盖这些报告本身，但 SHALL：
- 在新报告开头声明：本报告与 `docs/review/*-scan.md` 并存，前者按 Trae 6 线程分组、聚焦一致性，后者按 Codex 8 线程分组、聚焦子系统
- 若扫描中发现 `docs/review/06-sandbox-scan.md` 仍引用已删除的 sandbox 代码，标注为「过时文档，建议归档到 `docs/archive/`」

## REMOVED Requirements

无（本 spec 纯新增，不删除既有需求）
