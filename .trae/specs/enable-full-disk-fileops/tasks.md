# Tasks

- [x] Task 1: 移除 path_guard 的 allowed-dir 限制，仅保留 DENY_PATTERNS 黑名单
  - [x] SubTask 1.1: `path_guard.py` 的 `_check_access` 移除 `is_in_allowed_dir` 调用，仅保留 `matches_deny_pattern`
  - [x] SubTask 1.2: 扩展 `DENY_PATTERNS` 新增系统敏感目录（`Windows`、`System32`、`$Recycle.Bin`、`Boot`、`bootmgr`）
  - [x] SubTask 1.3: `validate_path` 保留 `..` 遍历检查 + deny pattern 检查，移除 allowed-dir 检查

- [x] Task 2: 移除 permission_classifier 的 path_guard 调用
  - [x] SubTask 2.1: `_decide_medium` 不再调用 `_path_denied`，直接按 permission_mode 路由（acceptEdits → allow，default → ask）
  - [x] SubTask 2.2: 删除 `_path_denied` 方法 + `_FILE_WRITE_TOOLS` / `_PATH_ARG_FIELD` 常量

- [x] Task 3: 工具 execute 内部加防御性 DENY 检查 + description 更新
  - [x] SubTask 3.1: `read_file.py` execute 内调用 `matches_deny_pattern`，命中则返回错误；description 改为"可读取任意本地路径"
  - [x] SubTask 3.2: `write_file.py` execute 内调用 `matches_deny_pattern`，命中则返回错误；description 改为"可写入任意本地路径"
  - [x] SubTask 3.3: `edit_file.py` execute 内调用 `matches_deny_pattern`，命中则返回错误；description 同步更新
  - [x] SubTask 3.4: `run_command.py` description 明确告知可执行 PowerShell/cmd 命令（打开应用、读写文件等）

- [x] Task 4: 更新系统提示词
  - [x] SubTask 4.1: `prompts.py` 新增"本地文件操作"指引段落，告知 LLM 可以读写任意路径 + 执行终端命令打开应用
  - [x] SubTask 4.2: 工具选择规则中补充第 9 条"用户让你操作本地文件/打开应用时，用 read_file/write_file/edit_file/run_command"

- [x] Task 5: 重启后端验证无导入错误
  - 验证：python -c 导入 path_guard / permission_classifier / read_file / write_file / edit_file / run_command / prompts 全部 OK
  - DENY_PATTERNS 已包含 .git / .vscode / .idea / .claude / settings.json / .env / *.key / *.pem / *credentials* / Windows / System32 / $Recycle.Bin / Boot / bootmgr

- [x] Task 6: 开 2 个 subagent 并行检查
  - [x] SubTask 6.1: Subagent A — 完成度检查（逐条核对 checklist.md）→ 12/12 PASS，5 层权限审查链路完整
  - [x] SubTask 6.2: Subagent B — 缺陷检查（边界情况测试）→ 发现 1 个阻断性缺陷（`..` 遍历未拦截，validate_path 无调用方），已修复
- [x] Task 7: 修复 Subagent B 发现的阻断性缺陷
  - [x] SubTask 7.1: read_file/write_file/edit_file 的 execute 改用 `validate_path`（含 `..` 拦截 + realpath 解析 + deny pattern）
  - [x] SubTask 7.2: 6 项测试全部通过（`..` 拦截 / .env 拦截 / System32 拦截 / 正常文件读 / 反斜杠 Windows 拦截 / 空路径处理）
  - [x] SubTask 7.3: 更新 pitfalls.md 记录"`validate_path` 无调用方"踩坑

# Task Dependencies
- Task 2 depends on Task 1（permission_classifier 改动依赖 path_guard 改动）
- Task 3-4 可与 Task 1-2 并行
- Task 5 依赖 Task 1-4 全部完成
- Task 6 依赖 Task 5 完成
