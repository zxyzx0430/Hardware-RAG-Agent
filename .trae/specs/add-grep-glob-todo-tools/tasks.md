# Tasks

## 前置 bug 修复：vision_analysis SSE abort（阻塞 view_image 验证）

- [ ] Task 0: 修复 vision_analysis SSE abort bug（3 个根因）
  - [ ] SubTask 0.1: 修复配置链路 — vision_analysis.py 改 fallback 文案为 "vision provider 未配置：请在设置页选择视觉模型 provider"；agent_factory.py 的 _read_vision_config 在 vision_model="auto" 时从 provider models 列表自动查找支持 vision 的模型
  - [ ] SubTask 0.2: 前端 useChatStore 上传图片时检查 visionProviderId 为 null 则 toast 提示 "请在设置中配置视觉模型后再上传图片"
  - [ ] SubTask 0.3: 修复 base64 放大 — chat_routes.py _build_agent_messages 对 ContentPart[] 只提取 text 部分，丢弃 image_url data URI
  - [ ] SubTask 0.4: 修复 base64 放大 — sse_adapter.py tool_call/tool_result 事件 args/output 中超 1KB 的字段值截断为摘要（如 "[base64 image, 1234KB]"）
  - [ ] SubTask 0.5: 修复异常处理 — chat_routes.py Agent 路径 except Exception 改为 except (Exception, asyncio.CancelledError)，确保 BaseException 子类也被捕获并转 error SSE 事件
  - [ ] SubTask 0.6: 修复前端 SSE 静默 — client.ts 内层 catch 对错误信息含 "aborted" 的静默返回不触发 onError
  - [ ] SubTask 0.7: 端到端验证：上传图片 + 问 "这张图是什么"，确认 vision_analysis 正常工作不报 SSE abort

## 后端工具实现（独立可并行）

- [ ] Task 1: 实现 GrepTool（file_ops/grep.py）
  - [ ] SubTask 1.1: 创建 grep.py，继承 ToolSpec，args: pattern/path/include/max_results
  - [ ] SubTask 1.2: 用 Python re + pathlib 递归搜索，返回 [{file, line, content}]
  - [ ] SubTask 1.3: 加路径白名单校验（复用 read_file 的 path_guard）
  - [ ] SubTask 1.4: 加大文件保护（>1MB 跳过）、结果上限（100 行）
  - [ ] SubTask 1.5: 在 file_ops/__init__.py 导出

- [ ] Task 2: 实现 GlobTool（file_ops/glob.py）
  - [ ] SubTask 2.1: 创建 glob.py，继承 ToolSpec，args: pattern/path/max_results
  - [ ] SubTask 2.2: 用 pathlib.Path.glob() 匹配，按 mtime 倒序返回
  - [ ] SubTask 2.3: 加路径白名单校验、结果上限（100 个）
  - [ ] SubTask 2.4: 在 file_ops/__init__.py 导出

- [ ] Task 3: 实现 ListFilesTool（file_ops/list_files.py）
  - [ ] SubTask 3.1: 创建 list_files.py，继承 ToolSpec，args: path/max_depth(default=3)
  - [ ] SubTask 3.2: 用 pathlib 递归构建树形结构，每个文件含 name/size_bytes/modified_at
  - [ ] SubTask 3.3: 加忽略规则（__pycache__/.git/node_modules/.venv 自动跳过）
  - [ ] SubTask 3.4: 加结果大小限制（>200 节点截断）
  - [ ] SubTask 3.5: 加路径白名单校验
  - [ ] SubTask 3.6: description 明确说明「与 glob 区分：已知文件名模式用 glob，探索未知目录用 list_files」
  - [ ] SubTask 3.7: 在 file_ops/__init__.py 导出

- [ ] Task 4: 实现 MultiEditTool（file_ops/multi_edit.py）
  - [ ] SubTask 4.1: 创建 multi_edit.py，继承 ToolSpec，args: file_path/edits[{old_string,new_string,replace_all}]
  - [ ] SubTask 4.2: 实现原子替换：先读文件，按顺序在内存应用所有替换，任一失败则不写盘
  - [ ] SubTask 4.3: 加路径白名单校验
  - [ ] SubTask 4.4: 在 file_ops/__init__.py 导出

- [ ] Task 5: 实现 ApplyPatchTool（file_ops/apply_patch.py）
  - [ ] SubTask 5.1: 创建 apply_patch.py，继承 ToolSpec，args: file_path/patch
  - [ ] SubTask 5.2: 用 Python difflib 或 unidiff 库解析 unified diff
  - [ ] SubTask 5.3: 实现 patch 应用：校验 context 行匹配 → 应用变更 → 写盘
  - [ ] SubTask 5.4: 错误处理：invalid format / context mismatch
  - [ ] SubTask 5.5: 加路径白名单校验
  - [ ] SubTask 5.6: 在 file_ops/__init__.py 导出

- [ ] Task 6: 实现 UndoEditTool（file_ops/undo_edit.py）
  - [ ] SubTask 6.1: 创建 undo_edit.py，继承 ToolSpec，args: 无（撤销最近一次即可）
  - [ ] SubTask 6.2: 检测当前工作目录是否 git 仓库（非 git 返回 "not a git repo"）
  - [ ] SubTask 6.3: 检查最近一次提交是否为 hardware-rag-agent author（非 agent 提交返回 "nothing to undo"）
  - [ ] SubTask 6.4: 执行 git reset --hard HEAD~1，返回 "reverted: <file_list>"
  - [ ] SubTask 6.5: 在 file_ops/__init__.py 导出

- [ ] Task 7: 改造 write_file/edit_file 加 git 自动快照
  - [ ] SubTask 7.1: 在 write_file/edit_file 写盘成功后，调 _git_snapshot(file_path, tool_name)
  - [ ] SubTask 7.2: 实现 _git_snapshot：git add <file> && git commit -m "agent edit: <tool>" --author="hardware-rag-agent <agent@local>"
  - [ ] SubTask 7.3: 非 git 仓库时降级（记日志跳过，不影响主流程）
  - [ ] SubTask 7.4: multi_edit / apply_patch 也复用 _git_snapshot

- [ ] Task 8: 实现 DiagnosticTool（execution/diagnostic.py）
  - [ ] SubTask 8.1: 创建 diagnostic.py，继承 ToolSpec，args: file_path/checker(default="ruff")
  - [ ] SubTask 8.2: 实现 ruff 检查：subprocess 跑 `ruff check file_path --output-format=json`，解析返回
  - [ ] SubTask 8.3: 实现 pyright 检查：subprocess 跑 `pyright --outputjson file_path`，解析返回
  - [ ] SubTask 8.4: 工具未安装时返回友好提示
  - [ ] SubTask 8.5: 加路径白名单校验
  - [ ] SubTask 8.6: 在 execution/__init__.py 导出

- [ ] Task 9: 实现 WebFetchTool（retrieval/webfetch.py）
  - [ ] SubTask 9.1: 创建 webfetch.py，继承 ToolSpec，args: url/prompt
  - [ ] SubTask 9.2: 用 httpx 抓取 URL，HTML 转 markdown（用 markdownify 或 html2text）
  - [ ] SubTask 9.3: 内容超 50KB 截断
  - [ ] SubTask 9.4: 用 LLM 按 prompt 提取关键信息（复用 _read_retrieval_config 的凭证）
  - [ ] SubTask 9.5: 在 retrieval/__init__.py 导出

- [ ] Task 10: 实现 ViewImageTool（retrieval/view_image.py）
  - [ ] SubTask 10.1: 创建 view_image.py，继承 ToolSpec，args: image_path/prompt
  - [ ] SubTask 10.2: 加图片扩展名白名单（.png/.jpg/.jpeg/.gif/.webp/.bmp）
  - [ ] SubTask 10.3: 加图片大小上限（10MB）
  - [ ] SubTask 10.4: 读取图片转 base64，调多模态模型（复用 vision_provider 凭证）
  - [ ] SubTask 10.5: 加路径白名单校验
  - [ ] SubTask 10.6: description 明确说明「与 vision_analysis 区分：vision 接 URL/base64 用户驱动，view_image 接本地路径 Agent 主动」
  - [ ] SubTask 10.7: 在 retrieval/__init__.py 导出

- [ ] Task 11: 实现 TodoWriteTool（execution/todo_write.py）
  - [ ] SubTask 11.1: 创建 todo_write.py，继承 ToolSpec，args: todos[{content,status}]
  - [ ] SubTask 11.2: 用模块级 dict 按 session_id 存 TODO 清单
  - [ ] SubTask 11.3: 在 execute 末尾调用 sse_adapter 推送 todo_update 事件
  - [ ] SubTask 11.4: 在 execution/__init__.py 导出

- [ ] Task 12: 实现 Skills 系统（execution/skills.py + skills/ 目录）
  - [ ] SubTask 12.1: 新建 backend/src/agent/skills/ 目录
  - [ ] SubTask 12.2: 预置 3 个 skill：parse_pin_table.py、calc_pullup_resistor.py、format_gpio_init.py，每个含 main(args: dict) -> dict + 模块 docstring
  - [ ] SubTask 12.3: 实现 ListSkillsTool（execution/skills.py 内）：扫描 skills 目录，提取 docstring + main 签名，返回 [{name, description, args_schema, file}]
  - [ ] SubTask 12.4: 实现 CallSkillTool（execution/skills.py 内）：动态 import skill 模块，调用 main(args)
  - [ ] SubTask 12.5: 加超时保护（30s）和异常捕获（不传播给 Agent 主流程）
  - [ ] SubTask 12.6: CallSkillTool description 加「先调 list_skills 查看可用 skills」提示
  - [ ] SubTask 12.7: 在 execution/__init__.py 导出 ListSkillsTool + CallSkillTool

## 后端集成

- [ ] Task 13: 注册 12 个新工具到 agent_factory
  - [ ] SubTask 13.1: 在 _build_tool_specs 的 file_ops 组加 GrepTool + GlobTool + ListFilesTool + MultiEditTool + ApplyPatchTool + UndoEditTool
  - [ ] SubTask 13.2: 在 execution 组加 DiagnosticTool + TodoWriteTool + ListSkillsTool + CallSkillTool
  - [ ] SubTask 13.3: 在 retrieval 组加 WebFetchTool + ViewImageTool
  - [ ] SubTask 13.4: 在 tool_routes._static_tool_listing() 同步 12 个工具
  - [ ] SubTask 13.5: 重启后端，curl /api/tools 确认返回 27 个工具

## SSE 事件

- [ ] Task 14: 新增 todo_update SSE 事件
  - [ ] SubTask 14.1: 在 sse_adapter.py 加 todo_update 事件类型
  - [ ] SubTask 14.2: TodoWriteTool execute 成功后触发该事件
  - [ ] SubTask 14.3: 后端日志打印事件内容

## 前端 UI

- [ ] Task 15: 前端消费 todo_update 事件并渲染 TODO 卡片
  - [ ] SubTask 15.1: useChatStore.ts SSE 处理器加 todo_update 分支
  - [ ] SubTask 15.2: 创建 TodoCard.tsx 组件（pending=灰圈、in_progress=蓝转圈、completed=绿勾划线）
  - [ ] SubTask 15.3: 同会话新 TODO 替换旧卡片（不堆叠）
  - [ ] SubTask 15.4: agent-browser 验证长任务时 Agent 先列清单走一步划一步

## 端到端验证

- [ ] Task 16: 端到端验证 12 个工具
  - [ ] SubTask 16.1: grep 验证：问"GPIO_NUM_GPIO 在哪定义"，确认 Agent 调 grep
  - [ ] SubTask 16.2: glob 验证：问"项目里有哪些 Python 文件"，确认 Agent 调 glob
  - [ ] SubTask 16.3: list_files 验证：让 Agent 探索陌生目录结构
  - [ ] SubTask 16.4: multi_edit 验证：让 Agent 一次改多处的重构任务
  - [ ] SubTask 16.5: apply_patch 验证：让 Agent 生成 diff patch 应用
  - [ ] SubTask 16.6: undo_edit 验证：Agent 编辑后调 undo_edit，确认文件回滚
  - [ ] SubTask 16.7: diagnostic 验证：让 Agent 写完 Python 代码后调 diagnostic 检查
  - [ ] SubTask 16.8: webfetch 验证：让 Agent 抓取 ESP32 官方文档页
  - [ ] SubTask 16.9: view_image 验证：放一张引脚图，让 Agent 主动调 view_image 分析
  - [ ] SubTask 16.10: todo_write 验证：长任务（搜手册+生成代码+审计引脚）先列 TODO 再逐步执行
  - [ ] SubTask 16.11: list_skills + call_skill 验证：让 Agent 调 list_skills 看到预置 skills，再调 calc_pullup_resistor 算上拉电阻
  - [ ] SubTask 16.12: 回归验证：短问题不触发 todo_write

## 文档

- [ ] Task 17: 更新文档
  - [ ] SubTask 17.1: 更新 architecture-map.md 工具清单从 15 → 27
  - [ ] SubTask 17.2: 追加 pitfalls.md 记录新工具踩坑（如有）

# Task Dependencies

- Task 0（vision bug 修复）独立，先做，阻塞 Task 16.9（view_image 验证）
- Task 1-6, 8-12 互相独立，可并行（11 个工具文件独立）
- Task 7 依赖 Task 4/5 完成（multi_edit/apply_patch 复用 _git_snapshot）
- Task 13 依赖 Task 1-6, 8-12 完成
- Task 14 依赖 Task 11 完成（TodoWriteTool 触发 SSE）
- Task 15 依赖 Task 14 完成（前端消费 SSE）
- Task 16 依赖 Task 0 + 13 + 15 完成
- Task 17 依赖 Task 16 验证通过
