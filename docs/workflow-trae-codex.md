# Hardware RAG Agent — Trae × Codex 多工具协作规范

> 本文件定义 Trae（开发 IDE）和 Codex（00-control 主控线程）的分工协议。
> 双方开工前必须阅读本文件，理解各自的职责边界和交接方式。

## 1. 角色定义

| 工具 | 角色 | 职责 |
|------|------|------|
| **Codex** | 主控/架构师 | 维护路线图、接口契约、线程归属、PLUR 记忆、审核合并 |
| **Trae** | 执行者 | 按交接文档实现功能代码，保持契约对齐，更新线程上下文文件 |

### 1.1 Codex 做

- 维护 docs/api-contract.md（接口契约唯一来源）
- 维护 docs/pitfalls.md（踩坑记录）
- 维护 docs/thread-map.md + docs/threads/*.md（线程归属）
- 维护 C:\Users\奶茶丸\.plur\（长期记忆）
- 写交接文档 docs/handoff/，派任务给 Trae
- 审核 Trae 的代码（diff + 契约 + 踩坑复用检查）
- 合并 feature 分支到 main

### 1.2 Codex 不做

- 不下场写功能代码（01-app ~ 08-infra）
- 不替 Trae 调试实现细节

### 1.3 Trae 做

- 按交接文档实现指定功能
- 保持 docs/api-contract.md 同步更新（改接口前先写文档）
- 修复错误后写 docs/pitfalls.md
- 更新对应 docs/threads/XX-name.md 的"当前状态"
- 重要决策写进 PLUR

### 1.4 Trae 不做

- 不擅自改架构决策（技术栈、分库、Agent 框架等）
- 不碰 PLUR 命令行（直接在文件中追加记录即可）
- 不修改 main 分支

---

## 2. Git 分支策略

### 2.1 分支命名

\\\
main                    ← Codex 维护，稳定分支
feature/XX-task-name    ← Trae 写代码的分支
fix/XX-bug-description  ← Trae 修 bug 的分支
\\\

- \XX\ = 线程编号（01 ~ 08）
- 示例：\eature/02-chat-sse\、\ix/03-kb-upload-timeout\

### 2.2 工作流

\\\
1. Codex: 写交接文档 → docs/handoff/YYMMDD-XX-task-name.md
2. Trae:  读交接文档 + api-contract.md + pitfalls.md + 对应 threads/*.md
3. Trae:  git checkout -b feature/XX-task-name main
4. Trae:  实现代码，更新 api-contract.md / pitfalls.md / threads/*.md
5. Trae:  git commit + git push
6. Trae:  通知 Codex "XX 任务完成，分支名 feature/XX-task-name"
7. Codex: git diff main...feature/XX-task-name 审核
8. Codex:  审核通过 → 合并到 main；不通过 → 写 review 到 docs/handoff/
9. Codex:  写 PLUR 记录本回合决策
\\\

### 2.3 注意事项

- Trae **绝不直接 push main**
- 多人（多线程）同时开发时，Trae 先 rebase main 再提 PR
- 合并冲突由 Codex 裁决

---

## 3. 共享上下文文件

所有文件路径相对仓库根目录 \E:\\Desktop\\agent\\\。

### 3.1 双方必须读写的

| 文件 | 谁维护 | Codex 开工前 | Trae 开工前 |
|------|--------|-------------|-------------|
| \docs/api-contract.md\ | Codex | 读 | 读 + 改接口前先更新 |
| \docs/pitfalls.md\ | 双方 | 读 | 读 + 修复后追加 |
| \docs/thread-map.md\ | Codex | 读 | 读 |
| \docs/threads/*.md\ | 对应线程 | 读 | 读 + 更新状态 |
| \docs/handoff/*.md\ | Codex | 写 | 读 |

### 3.2 必须从 PLUR 获取的

Codex 开工前先执行：

\\\powershell
plur inject "Hardware RAG Agent 00-control 主控线程" --fast --json
\\\

Trae 的重要决策应该追加到 PLUR，做法是直接在文件中记录（不需要命令行）：

\\\
# 新增一条到 C:\Users\奶茶丸\.plur\engrams.yaml 或 Codex 下次会说读取到
# 格式：记录日期、决策内容、原因。
\\\

### 3.3 不上 git 的

\docs/handoff/\ 目录下的交办单不 commit 到 GitHub，它们是 Trae 和 Codex 之间的临时通信。Codex 审完合并后，交办单可以归档或删除。

---

## 4. 契约对齐红线

Trae 在实现时必须遵守以下不可逾越的规则：

### 4.1 接口契约铁律

1. **任何新接口先写 docs/api-contract.md 再写代码**。没写进文档的接口视为不存在。
2. 修改已有接口的路径、字段名、请求体、响应体、状态码、错误码 → 必须先改文档。
3. 接口状态按 \draft → agreed → mocked → implemented → verified\ 流转。
4. 联调以文档为准，前后端理解不一致时先改文档再改代码。

### 4.2 踩坑复用

Trae 修复错误后必须写 \docs/pitfalls.md\，格式：

\\\md
## YYYY-MM-DD - 简短标题

- 错误现象：
- 错误原因：
- 修复方式：
- 下次注意：
\\\

### 4.3 线程上下文更新

Trae 完成任务后必须更新 \docs/threads/XX-name.md\ 的以下章节：
- \## 当前状态\：已完成的 / 正在做的 / 阻塞的
- \## 接口契约\：更新对应接口状态
- \## 踩坑记录\：记录本次踩坑
- \## 决策记录\：新增重要决策

### 4.4 不要做

- 不要改 \docs/thread-map.md\（那是 Codex 管的）
- 不要在 \docs/handoff/\ 以外的地方留交接笔记
- 不要直接改 \C:\Users\奶茶丸\.plur\ 的 YAML 结构（只追加上级允许的记录）

---

## 5. 交接文档模板

Codex 写 \docs/handoff/YYMMDD-XX-task-name.md\，模板：

\\\md
# 交接单 YYMMDD-XX-task-name

## 任务
- **目标线程**：02-chat
- **任务**：实现 SSE 流式聊天接口
- **优先级**：高

## 接口契约参考
- api-contract.md 5.1 POST /api/chat（状态：agreed）
- api-contract.md 5.2 GET /api/models（状态：agreed）

## 需修改文件
- backend/app/api/chat_routes.py（新建）
- backend/app/api/models_routes.py（新建）
- backend/main.py（注册路由）
- frontend/src/api/endpoints.ts（添加端点）

## 约束条件
- 不使用 LangChain/LangGraph，用原生 FastAPI SSE
- 响应格式按 api-contract.md 2.6 标准格式
- 认证 Header 按 2.5 节

## 踩坑提醒
- 看 pitfalls.md 2026-06-21 安全基线改造，API Key 传输已经改为 Bearer token
- SSE 事件格式按 5.1 节，不要自己发明
\\\

---

## 6. Review 模板

Codex 审核 Trae 代码时，在 \docs/handoff/\ 下写 review 笔记：

\\\md
# Review YYMMDD-XX-task-name

## 通过条件
- [ ] 接口契约对齐？具体哪几个接口？
- [ ] 没有重复踩坑？
- [ ] 线程上下文文件已更新？
- [ ] 没有越界（干了不该这个线程干的事）？

## 问题列表
1. [P0] 接口字段命名不一致：xxx -> yyy
2. [P1] 没写 pitfalls
3. [P2] 小问题建议优化

## 结论
通过 / 需修改 / 打回
\\\

---

## 7. 紧急情况处理

### 7.1 Trae 卡住了

Trae 在 \docs/handoff/\ 下写一个 \BLOCKED-YYMMDD.md\，说清楚：
- 卡在哪里
- 尝试了哪些方案
- 需要 Codex 做什么决策

Codex 收到后在 24 小时内回复。

### 7.2 Codex 不在线

Trae 可以自行决策，但必须：
1. 在对应 \docs/threads/*.md\ 的 \## 决策记录\ 中写明
2. 在 \docs/handoff/\ 下写 \DECISION-YYMMDD.md\ 说明决策内容和理由
3. Codex 上线后审核，不对的由 Trae 修

### 7.3 接口契约紧急修改

如果联调时发现契约有问题，Trae 可以先改 \docs/api-contract.md\ 再改代码，但改完必须通知 Codex。

---

## 8. PLUR 记忆管理

### 8.1 必须写 PLUR 的场景

- 技术栈变更（如：从 HTMX 换 React）
- 架构决策（如：拆 SQLite + MinIO）
- 阶段完成（如：V1 全部验证通过）
- 重要踩坑（其他线程也可能遇到的那种）
- 新人（Codex 新实例）需要知道的上下文

### 8.2 如何写入

Codex 用命令行写：

\\\powershell
plur inject "Hardware RAG Agent <主题> <决策内容>" --fast --json
\\\

Trae 直接在 \C:\Users\奶茶丸\.plur\engrams.yaml\ 文件末尾追加记录，格式：

\\\yaml
- id: "DIT-YYMMDD-XXX"
  created: "2026-06-21"
  scope: "Hardware RAG Agent"
  description: "Trae: 决策内容简述"
  constraint: "具体约束或规则"
\\\

---

## 9. 文件不上 git 清单

以下文件/目录不提交到 GitHub（已配置在 \.gitignore\）：

| 路径 | 原因 |
|------|------|
| \docs/handoff/\ | Trae 与 Codex 的临时通信，审完即焚 |
| \	mp.txt\ 和 \*.tmp\ | 写入中转文件 |
| \.vscode/\、\.idea/\ | IDE 个人设置 |
| \Thumbs.db\、\Desktop.ini\ | Windows 系统文件 |
| \ackend/data/chroma_db/\ | 向量数据库运行时生成 |
| \ackend/logs/\ | 运行时日志 |
| \.history/\ | VSCode 本地历史 |
