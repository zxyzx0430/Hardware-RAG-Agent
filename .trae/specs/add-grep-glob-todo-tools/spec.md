# 新增 12 个 Agent 工具 Spec

## Why

对比 Claude Code / OpenCode / OpenHands / aider / Codex CLI 五个竞品的工具源码（通过 5 个 subagent 深度调研）发现，我们的 Agent 缺少多个高频能力。本 spec 新增 12 个工具，分 5 类：

**代码导航类**（所有 coding agent 标配）：
1. `grep` — 正则搜文件内容
2. `glob` — 文件名匹配
3. `list_files` — 目录树浏览（OpenCode/OpenHands 标配，与 glob 互补：glob 已知模式精确匹配，list 探索未知目录结构）

**代码编辑类**（Claude Code/OpenCode/aider 标配）：
4. `multi_edit` — 单文件多处替换（一次调用原子完成）
5. `apply_patch` — unified diff 应用（大段重构紧凑表达）
6. `undo_edit` — git 快照撤销最近一次 Agent 编辑（aider 杀手锏，硬件场景修改驱动代码后能回滚是刚需）
7. `diagnostic` — LSP 诊断（pyright/ruff，生成代码后验证语法）— OpenCode 招牌特色

**信息获取类**：
8. `webfetch` — 抓取 URL 内容并按 prompt 提取
9. `view_image` — 读本地图片路径喂多模态模型（Codex CLI 工具，与现有 vision_analysis 互补：vision 接 URL/base64，view_image 接本地文件路径由 Agent 主动调用）

**任务管理类**：
10. `todo_write` — 会话级 TODO 清单，长任务先列再执行走一步划一步

**可复用技能类**（OpenHands AgentSkillsAction 招牌，契合 AGENTS.md「复用优先原则」）：
11. `list_skills` — 列出已注册的所有 skills（含签名+描述）
12. `call_skill` — 按名调用已注册 skill，传 args dict

## What Changes

### 新增工具（12 个）

| 工具 | 文件 | 功能 | 来源竞品 |
|------|------|------|---------|
| `grep` | `file_ops/grep.py` | 正则搜索文件内容，返回匹配行+行号 | Claude Code/OpenCode |
| `glob` | `file_ops/glob.py` | glob 模式匹配文件路径，按 mtime 排序 | Claude Code/OpenCode |
| `list_files` | `file_ops/list_files.py` | 目录树浏览，含文件大小/mtime | OpenCode/OpenHands |
| `multi_edit` | `file_ops/multi_edit.py` | 单文件多处 string replacement，一次原子完成 | Claude Code |
| `apply_patch` | `file_ops/apply_patch.py` | 应用 unified diff patch 到文件 | Claude Code/Codex CLI |
| `undo_edit` | `file_ops/undo_edit.py` | git 快照撤销最近一次 Agent 编辑（write/edit/multi_edit/apply_patch 后可回滚） | aider |
| `diagnostic` | `execution/diagnostic.py` | 调 ruff/pyright 拉 Python 诊断 | OpenCode |
| `webfetch` | `retrieval/webfetch.py` | 抓取 URL，按 prompt 提取内容 | Claude Code |
| `view_image` | `retrieval/view_image.py` | 读本地图片路径，转 base64 喂多模态模型分析 | Codex CLI |
| `todo_write` | `execution/todo_write.py` | 会话级 TODO 清单 + SSE 推送 | Claude Code |
| `list_skills` | `execution/skills.py` | 列出所有已注册 skills（含签名+描述） | OpenHands |
| `call_skill` | `execution/skills.py` | 按名调用 skill，传 args dict | OpenHands |

### 不做（留 v2 spec）
- ❌ dispatch_subagent / Task 工具（Claude Code/OpenCode/OpenHands 三家都有，但需要 LangGraph subgraph，成本高，留 v2）
- ❌ search_codebase 语义搜索（Claude Code 强推荐，复用 ChromaDB，但需要新建 code collection + 索引脚本，留 v2）
- ❌ skills_register 动态创建 skill（让 agent 写 Python 代码到磁盘执行，风险高，留 v2；当前只做 list + call 预置 skills）
- ❌ hook 系统（Claude Code 设计，把硬编码权限门控外置成可配置脚本，留 v2）
- ❌ MCP client（Codex CLI 战略扩展层，把硬件工具做成 MCP server，留 v2）
- ❌ 持久化 shell / background bash（OpenHands CmdRunAction，对 T6 烧录有用但成本高，留 v2）
- ❌ repo map（aider PageRank 符号地图，成本中高，留 v2）
- ❌ todo_write 不持久化（会话内存级）

### 前置 bug 修复：vision_analysis SSE abort
本 spec 实现前先修复 vision_analysis 的 SSE abort bug（阻塞 view_image 端到端验证）。3 个根因：
1. **配置链路断裂**：visionProviderId 默认 null → 前端传 undefined → 后端 _resolve_provider 返回空 api_key → 工具立即 fallback
2. **base64 放大**：_build_agent_messages 用 str() 字符串化 ContentPart[]（base64 完整保留塞给 LLM）；sse_adapter tool_call 事件 args 携带完整 base64
3. **异常处理不全**：chat_routes.py except Exception 不捕获 BaseException（如 asyncio.CancelledError），异常逃出后 SSE 连接突然中断

### 预置 skills 目录
新建 `backend/src/agent/skills/` 目录，每个 .py 文件是一个 skill，含 `main(args: dict) -> dict` 函数和模块级 docstring。启动时扫描目录注册。初期预置：
- `parse_pin_table` — 从 datasheet 文本提取引脚表
- `calc_pullup_resistor` — 计算 I2C 上拉电阻值
- `format_gpio_init` — 生成 GPIO 初始化代码片段

## Impact

- **Affected specs**: industrial-tool-runtime（工具注册体系）
- **Affected code**:
  - `backend/src/agent/tools/groups/file_ops/` — 新增 grep.py、glob.py、list_files.py、multi_edit.py、apply_patch.py、undo_edit.py
  - `backend/src/agent/tools/groups/execution/` — 新增 diagnostic.py、todo_write.py、skills.py
  - `backend/src/agent/tools/groups/retrieval/` — 新增 webfetch.py、view_image.py
  - `backend/src/agent/skills/` — 新建目录，预置 3 个 skill
  - `backend/src/agent/agent_factory.py` — `_build_tool_specs` 注册 12 个新工具
  - `backend/src/agent/tools/groups/*/` `__init__.py` — 导出新工具
  - `backend/src/agent/sse_adapter.py` — 新增 `todo_update` SSE 事件
  - `backend/app/api/tool_routes.py` — `_static_tool_listing()` 同步 12 个新工具
  - `frontend/src/components/chat/*` — TODO 清单卡片 UI
  - `frontend/src/stores/useChatStore.ts` — SSE 处理器加 `todo_update` 分支
- **工具总数**：15 → 27
- **外部依赖**：ruff（pip install ruff）、pyright（pip install pyright）
- **git 依赖**：undo_edit 需要项目目录是 git 仓库（已是）

## ADDED Requirements

### Requirement: grep 工具

系统 SHALL 提供 `grep` 工具，用正则表达式搜索指定目录下的文件内容。

#### Scenario: 基础搜索
- **WHEN** Agent 调用 `grep(pattern="GPIO_NUM", path="e:\Desktop\agent\backend")`
- **THEN** 返回所有匹配行的文件路径、行号、匹配内容（最多 100 行）

#### Scenario: 文件类型过滤
- **WHEN** Agent 调用 `grep(pattern="def ", path="...", include="*.py")`
- **THEN** 只搜索 .py 文件

#### Scenario: 大文件保护
- **WHEN** 单文件超过 1MB
- **THEN** 跳过该文件并标注 "skipped large file"

#### Scenario: 敏感路径拦截
- **WHEN** path 命中 .git / .env / *.key / *.pem
- **THEN** 拒绝执行，返回 "path not allowed"

### Requirement: glob 工具

系统 SHALL 提供 `glob` 工具，用 glob 模式匹配文件路径。

#### Scenario: 基础匹配
- **WHEN** Agent 调用 `glob(pattern="**/*.py", path="e:\Desktop\agent\backend")`
- **THEN** 返回匹配文件路径列表，按修改时间倒序（最新在前）

#### Scenario: 限制结果数
- **WHEN** 匹配结果超过 100 个
- **THEN** 只返回前 100 个，末尾标注 "truncated, N more files"

#### Scenario: 敏感路径拦截
- **WHEN** path 命中 .git / .env
- **THEN** 拒绝执行

### Requirement: list_files 工具

系统 SHALL 提供 `list_files` 工具，列出指定目录的文件树（含文件大小和修改时间）。

#### Scenario: 基础列出
- **WHEN** Agent 调用 `list_files(path="e:\Desktop\agent\backend\src\agent\tools\groups")`
- **THEN** 返回该目录的树形结构，每个文件含 name/size_bytes/modified_at，目录含 name/children

#### Scenario: 递归深度控制
- **WHEN** Agent 调用 `list_files(path="...", max_depth=2)`
- **THEN** 只递归到 2 层深度，第 2 层目录标 "..." 不展开

#### Scenario: 默认深度
- **WHEN** Agent 不传 max_depth
- **THEN** 默认递归到 3 层

#### Scenario: 忽略规则
- **WHEN** 目录包含 __pycache__ / .git / node_modules / .venv
- **THEN** 这些目录被自动跳过，不出现在结果中

#### Scenario: 结果大小限制
- **WHEN** 树形结果超过 200 个节点
- **THEN** 截断并标注 "truncated, N more entries"

#### Scenario: 与 glob 区分
- **WHEN** Agent 已知目标文件名模式（如 "*.py"）
- **THEN** 应使用 glob 而非 list_files；list_files 用于探索未知目录结构

#### Scenario: 敏感路径拦截
- **WHEN** path 命中 .git / .env
- **THEN** 拒绝执行

### Requirement: multi_edit 工具

系统 SHALL 提供 `multi_edit` 工具，一次调用对同一文件做多处 string replacement，原子完成（全成功或全回滚）。

#### Scenario: 多处替换
- **WHEN** Agent 调用 `multi_edit(file_path="...", edits=[{old_string:"a", new_string:"x"}, {old_string:"b", new_string:"y"}])`
- **THEN** 按顺序应用所有替换，全部成功才写盘；任一失败则不写盘并返回失败信息

#### Scenario: 部分失败回滚
- **WHEN** 第 2 个 edit 的 old_string 在文件中找不到
- **THEN** 整个操作回滚，文件不被修改，返回 "edit 2 failed: old_string not found"

#### Scenario: 敏感路径拦截
- **WHEN** file_path 命中 .git / .env / *.key
- **THEN** 拒绝执行

### Requirement: apply_patch 工具

系统 SHALL 提供 `apply_patch` 工具，应用 unified diff 格式的 patch 到文件。

#### Scenario: 成功应用
- **WHEN** Agent 调用 `apply_patch(file_path="...", patch="--- a/file\n+++ b/file\n@@...")`
- **THEN** 应用 diff 到文件，返回成功信息和变更行数

#### Scenario: patch 格式错误
- **WHEN** patch 不是合法的 unified diff
- **THEN** 返回 "invalid patch format"，文件不被修改

#### Scenario: context 不匹配
- **WHEN** patch 中的 context 行与文件实际内容不匹配
- **THEN** 返回 "patch context mismatch at line N"，文件不被修改

#### Scenario: 敏感路径拦截
- **WHEN** file_path 命中 .git / .env / *.key
- **THEN** 拒绝执行

### Requirement: undo_edit 工具

系统 SHALL 提供 `undo_edit` 工具，撤销最近一次 Agent 对文件系统的编辑操作（write_file/edit_file/multi_edit/apply_patch），基于 git 快照机制。

#### Scenario: 撤销最近一次编辑
- **WHEN** Agent 调 write_file 后调用 `undo_edit()`
- **THEN** 执行 `git reset --hard HEAD~1`（或 `git checkout -- <changed_files>`），文件回到编辑前状态，返回 "reverted: <file_list>"

#### Scenario: 无可撤销
- **WHEN** Agent 未做任何编辑，或最近一次操作已撤销
- **THEN** 返回 "nothing to undo"

#### Scenario: 非 git 仓库
- **WHEN** 工作目录不是 git 仓库
- **THEN** 返回 "undo not available: not a git repo"

#### Scenario: 自动快照机制
- **WHEN** write_file/edit_file/multi_edit/apply_patch 执行成功
- **THEN** 这些工具在写盘后自动 `git add <file> && git commit -m "agent edit: <tool_name>"`，为 undo_edit 准备回滚点

#### Scenario: 隔离用户提交
- **WHEN** 用户已有 git 提交历史
- **THEN** Agent 的自动快照提交用独立 author 标记（`--author="hardware-rag-agent <agent@local>"`），不污染用户提交历史

### Requirement: diagnostic 工具

系统 SHALL 提供 `diagnostic` 工具，调用 ruff/pyright 对指定文件拉诊断，返回语法错误和类型错误。

#### Scenario: Python 语法检查
- **WHEN** Agent 调用 `diagnostic(file_path="main.py", checker="ruff")`
- **THEN** 运行 `ruff check file_path --output-format=json`，解析结果返回 [{line, col, code, message, severity}]

#### Scenario: 类型检查
- **WHEN** Agent 调用 `diagnostic(file_path="main.py", checker="pyright")`
- **THEN** 运行 `pyright --outputjson file_path`，解析结果返回诊断列表

#### Scenario: 默认 checker
- **WHEN** Agent 不传 checker 参数
- **THEN** 默认用 ruff（轻量、快速）

#### Scenario: 工具未安装
- **WHEN** ruff/pyright 未安装
- **THEN** 返回 "checker not installed: run 'pip install ruff'"

#### Scenario: 敏感路径拦截
- **WHEN** file_path 命中 .git / .env
- **THEN** 拒绝执行

### Requirement: webfetch 工具

系统 SHALL 提供 `webfetch` 工具，抓取指定 URL 内容并按 prompt 提取关键信息。

#### Scenario: 基础抓取
- **WHEN** Agent 调用 `webfetch(url="https://docs.espressif.com/...", prompt="提取 ESP32-S3 GPIO 电气参数")`
- **THEN** 抓取 URL，把 HTML 转 markdown，用 LLM 按 prompt 提取关键信息返回

#### Scenario: URL 无效
- **WHEN** URL 无法访问或返回非 200
- **THEN** 返回 "fetch failed: HTTP 404"

#### Scenario: 内容过大
- **WHEN** 抓取内容超过 50KB
- **THEN** 截断到 50KB 并标注 "truncated"

### Requirement: view_image 工具

系统 SHALL 提供 `view_image` 工具，读取本地图片文件路径，转 base64 喂给多模态模型分析。与现有 vision_analysis 互补：vision_analysis 接 URL/base64（用户上传驱动），view_image 接本地文件路径（Agent 主动驱动）。

#### Scenario: 基础识别
- **WHEN** Agent 调用 `view_image(image_path="e:\Desktop\agent\data\schematics\esp32_pinout.png", prompt="这张图的引脚定义是什么")`
- **THEN** 读取本地图片文件，转 base64，调多模态模型按 prompt 分析，返回分析结果

#### Scenario: 文件不存在
- **WHEN** image_path 指向的文件不存在
- **THEN** 返回 "file not found: <path>"

#### Scenario: 非图片文件
- **WHEN** image_path 指向的文件不是图片（扩展名不是 .png/.jpg/.jpeg/.gif/.webp/.bmp）
- **THEN** 返回 "not an image file: supported formats are png/jpg/jpeg/gif/webp/bmp"

#### Scenario: 图片过大
- **WHEN** 图片文件超过 10MB
- **THEN** 拒绝读取，返回 "image too large: max 10MB"

#### Scenario: 复用 vision_provider 凭证
- **WHEN** 调用 view_image
- **THEN** 复用 _read_vision_config 的 provider/base_url/api_key 配置，不引入新凭证

#### Scenario: 敏感路径拦截
- **WHEN** image_path 命中 .git / .env / *.key
- **THEN** 拒绝执行

### Requirement: todo_write 工具

系统 SHALL 提供 `todo_write` 工具，维护会话级 TODO 清单。Agent 在长任务（3+ 步骤）时自主使用。

#### Scenario: 创建清单
- **WHEN** Agent 调用 `todo_write(todos=[{content:"搜手册", status:"pending"}, {content:"生成代码", status:"pending"}, {content:"审计引脚", status:"pending"}])`
- **THEN** 替换当前会话整个 TODO 列表，通过 SSE 推送 `todo_update` 事件

#### Scenario: 更新状态
- **WHEN** Agent 完成第一步，调用 `todo_write(todos=[{content:"搜手册", status:"completed"}, {content:"生成代码", status:"in_progress"}, ...])`
- **THEN** 更新清单，SSE 推送新状态，前端已完成项划线、进行中项高亮

#### Scenario: 会话隔离
- **WHEN** 不同会话同时使用 todo_write
- **THEN** 各会话维护独立清单（基于 ToolContext.session_id）

#### Scenario: 不持久化
- **WHEN** 用户关闭浏览器或切换会话
- **THEN** TODO 清单清空，下次打开不恢复

### Requirement: list_skills 工具

系统 SHALL 提供 `list_skills` 工具，列出所有已注册的 skills。Skills 存放在 `backend/src/agent/skills/` 目录，每个 .py 文件是一个 skill。

#### Scenario: 基础列出
- **WHEN** Agent 调用 `list_skills()`
- **THEN** 扫描 `backend/src/agent/skills/` 目录，对每个 .py 文件提取模块 docstring + main 函数签名，返回 [{name, description, args_schema, file}]

#### Scenario: 空目录
- **WHEN** skills 目录为空或不存在
- **THEN** 返回 "no skills registered"

#### Scenario: 加载错误
- **WHEN** 某 skill 文件有语法错误或 import 失败
- **THEN** 跳过该 skill 并在结果中标注 "failed to load: <error>"

### Requirement: call_skill 工具

系统 SHALL 提供 `call_skill` 工具，按名调用已注册 skill，传入 args dict。

#### Scenario: 基础调用
- **WHEN** Agent 调用 `call_skill(name="calc_pullup_resistor", args={"scl_freq": 400000, "cap_pf": 10})`
- **THEN** 动态 import `backend/src/agent/skills/calc_pullup_resistor.py`，调用 `main(args)`，返回结果

#### Scenario: skill 不存在
- **WHEN** name 指向的 skill 不存在
- **THEN** 返回 "skill not found: <name>，call list_skills 查看可用 skills"

#### Scenario: skill 执行错误
- **WHEN** skill main 函数抛出异常
- **THEN** 捕获异常返回 "skill execution failed: <error>"，不传播给 Agent 主流程

#### Scenario: 超时保护
- **WHEN** skill 执行超过 30 秒
- **THEN** 中断执行返回 "skill timeout: 30s exceeded"

#### Scenario: 与 list_skills 联动
- **WHEN** Agent 不确定有哪些 skills 可用
- **THEN** description 提示 "先调 list_skills 查看可用 skills"

### Requirement: todo_update SSE 事件

系统 SHALL 新增 `todo_update` SSE 事件类型。

#### Scenario: 事件结构
- **WHEN** todo_write 执行成功
- **THEN** SSE 流推送 `{type: "todo_update", todos: [{content, status}], ts}`

#### Scenario: 前端渲染
- **WHEN** 前端收到 todo_update 事件
- **THEN** 在消息流渲染 TODO 卡片（pending=灰圈、in_progress=蓝转圈、completed=绿勾划线）

## MODIFIED Requirements

### Requirement: 工具注册

`agent_factory._build_tool_specs` SHALL：
- 在 file_ops 组注册 `GrepTool`、`GlobTool`、`ListFilesTool`、`MultiEditTool`、`ApplyPatchTool`、`UndoEditTool`
- 在 execution 组注册 `DiagnosticTool`、`TodoWriteTool`、`ListSkillsTool`、`CallSkillTool`
- 在 retrieval 组注册 `WebFetchTool`、`ViewImageTool`

工具总数从 15 增加到 27。

### Requirement: 文件编辑工具自动 git 快照

write_file / edit_file / multi_edit / apply_patch 工具 SHALL 在写盘成功后自动 git commit，为 undo_edit 准备回滚点。

#### Scenario: 自动快照
- **WHEN** 任一文件编辑工具执行成功
- **THEN** 执行 `git add <file> && git commit -m "agent edit: <tool_name>" --author="hardware-rag-agent <agent@local>"`

#### Scenario: 非 git 仓库降级
- **WHEN** 工作目录不是 git 仓库
- **THEN** 跳过 git 快照，记日志 "snapshot skipped: not a git repo"，不影响工具主流程

### Requirement: vision_analysis 配置链路修复

vision_analysis 工具 SHALL 在配置未完成时给出明确提示，而非静默 fallback "未配置视觉模型或调用出错"。

#### Scenario: vision_provider_id 为空时明确报错
- **WHEN** 前端 payload 的 vision_provider_id 为 null/undefined/空字符串
- **THEN** 后端 vision_analysis 工具返回明确提示 "vision provider 未配置：请在设置页选择视觉模型 provider"，而非模糊的 "未配置视觉模型或调用出错"

#### Scenario: vision_model 为 "auto" 时自动解析
- **WHEN** 前端传 vision_model="auto" 且 vision_provider_id 已配置
- **THEN** 后端从该 provider 的 models 列表中自动查找支持 vision 的模型（含 gpt-4o/claude/sonnet/glm-4v/qwen-vl 等关键词）

#### Scenario: 前端上传图片时检查 vision 配置
- **WHEN** 用户在前端上传图片但 visionProviderId 为 null
- **THEN** 前端显示 toast 提示 "请在设置中配置视觉模型后再上传图片"，但仍允许发送（图片存入消息但 Agent 无法分析）

### Requirement: base64 数据放大修复

系统 SHALL 防止 base64 图片数据在 Agent 链路中被放大导致 SSE 流中断。

#### Scenario: _build_agent_messages 丢弃图片 data URI
- **WHEN** user 消息的 content 是 ContentPart[] 且含 image_url 类型（data:image/...;base64,...）
- **THEN** _build_agent_messages 只提取 text 部分拼接为字符串，丢弃 image_url 部分（Agent 路径 LLM 不需要图片原文，vision_analysis 工具会自己处理）

#### Scenario: tool_call SSE 事件截断大字段
- **WHEN** tool_call 事件的 args 中任一字段值超过 1KB
- **THEN** 该字段值截断为摘要（如 "[base64 image, 1234KB]"），完整 args 不进 SSE 事件

#### Scenario: tool_result SSE 事件同样截断
- **WHEN** tool_result 事件的 output 中任一字段值超过 1KB
- **THEN** 同样截断为摘要

### Requirement: Agent SSE 异常处理修复

Agent SSE 路径 SHALL 捕获所有异常（包括 BaseException 子类），确保 SSE 流正常关闭。

#### Scenario: 捕获 BaseException
- **WHEN** Agent 路径抛出 asyncio.CancelledError 或其他 BaseException 子类
- **THEN** chat_routes.py 的 except 块捕获并转为 error SSE 事件，而非让连接突然中断

#### Scenario: 前端 SSE 读取异常静默处理
- **WHEN** 前端 reader.read() 抛 "aborted" 类错误且 controller.signal.aborted 为 false
- **THEN** client.ts 检查错误信息是否含 "aborted"，是则静默返回不触发 onError（避免给用户显示 "SSE 读取异常: BodyStreamBuffer was aborted"）
