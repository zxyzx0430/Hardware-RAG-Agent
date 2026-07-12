# Checklist

## 前置 bug 修复：vision_analysis SSE abort
- [ ] vision_analysis.py fallback 文案改为明确提示 "vision provider 未配置：请在设置页选择视觉模型 provider"
- [ ] agent_factory.py _read_vision_config 在 vision_model="auto" 时从 provider models 列表自动查找支持 vision 的模型
- [ ] 前端 useChatStore 上传图片时检查 visionProviderId 为 null 则 toast 提示
- [ ] chat_routes.py _build_agent_messages 对 ContentPart[] 只提取 text 部分，丢弃 image_url data URI
- [ ] sse_adapter.py tool_call/tool_result 事件 args/output 中超 1KB 的字段值截断为摘要
- [ ] chat_routes.py Agent 路径 except 改为捕获 (Exception, asyncio.CancelledError)
- [ ] client.ts 内层 catch 对错误信息含 "aborted" 的静默返回不触发 onError
- [ ] 端到端验证：上传图片 + 问 "这张图是什么"，vision_analysis 正常工作不报 SSE abort

## 后端工具实现
- [ ] GrepTool: pattern/path/include/max_results 参数 + 正则搜索 + 行号返回
- [ ] GlobTool: pattern/path 参数 + glob 匹配 + mtime 倒序排序
- [ ] ListFilesTool: path/max_depth 参数 + 树形结构 + 文件大小/mtime + 忽略规则
- [ ] ListFilesTool description 明确说明与 glob 的区分（已知模式用 glob，探索未知用 list）
- [ ] MultiEditTool: file_path/edits 参数 + 原子替换（全成功才写盘）+ 失败回滚
- [ ] ApplyPatchTool: file_path/patch 参数 + unified diff 解析 + context 校验
- [ ] UndoEditTool: 无参数 + 检测 git 仓库 + 检测 agent author + git reset HEAD~1
- [ ] DiagnosticTool: file_path/checker 参数 + ruff + pyright 双 checker
- [ ] WebFetchTool: url/prompt 参数 + HTML 转 markdown + LLM 提取
- [ ] ViewImageTool: image_path/prompt 参数 + 图片扩展名白名单 + 大小上限 + 复用 vision_provider 凭证
- [ ] ViewImageTool description 明确说明与 vision_analysis 的区分（vision 接 URL/base64，view_image 接本地路径）
- [ ] TodoWriteTool: todos 参数 + session_id 隔离 + 模块级 dict 存储
- [ ] ListSkillsTool: 扫描 skills/ 目录 + 提取 docstring + main 签名
- [ ] CallSkillTool: name/args 参数 + 动态 import + 超时保护 + 异常捕获
- [ ] CallSkillTool description 含「先调 list_skills」提示
- [ ] 12 个工具都继承 ToolSpec，有 name/description/args_schema/risk_level
- [ ] 12 个工具的 description 清晰说明使用场景和与其他工具的区分

## 安全防护
- [ ] grep/glob/list_files/multi_edit/apply_patch/diagnostic/view_image 都拦截敏感路径（.git/.env/*.key/*.pem）
- [ ] grep 大文件保护（>1MB 跳过）
- [ ] grep/glob 结果数上限（100）
- [ ] list_files 忽略 __pycache__/.git/node_modules/.venv
- [ ] list_files 结果节点上限（200）
- [ ] webfetch 内容大小上限（50KB）
- [ ] view_image 图片大小上限（10MB）
- [ ] view_image 图片扩展名白名单（png/jpg/jpeg/gif/webp/bmp）
- [ ] multi_edit 原子性（任一替换失败则全回滚）
- [ ] apply_patch context 不匹配时不写盘
- [ ] undo_edit 只撤销 hardware-rag-agent author 的提交，不误撤用户提交
- [ ] call_skill 超时保护（30s）+ 异常捕获不传播主流程

## git 快照机制
- [ ] write_file 写盘成功后自动 git commit（author=hardware-rag-agent）
- [ ] edit_file 写盘成功后自动 git commit
- [ ] multi_edit 写盘成功后自动 git commit
- [ ] apply_patch 写盘成功后自动 git commit
- [ ] 非 git 仓库时降级（记日志跳过，不影响主流程）
- [ ] 自动快照提交用独立 author，不污染用户提交历史

## Skills 系统
- [ ] 新建 backend/src/agent/skills/ 目录
- [ ] 预置 parse_pin_table.py（含 main + docstring）
- [ ] 预置 calc_pullup_resistor.py（含 main + docstring）
- [ ] 预置 format_gpio_init.py（含 main + docstring）
- [ ] ListSkillsTool 能正确扫描并返回 skill 列表
- [ ] CallSkillTool 能正确动态 import 并调用
- [ ] skill 加载错误时跳过并标注，不崩溃
- [ ] skill 执行错误时返回错误信息，不传播主流程

## 后端集成
- [ ] agent_factory._build_tool_specs 注册 12 个新工具
- [ ] tool_routes._static_tool_listing() 同步 12 个新工具
- [ ] curl /api/tools 返回 27 个工具（原 15 + 新 12）

## SSE 事件
- [ ] sse_adapter 支持 todo_update 事件类型
- [ ] TodoWriteTool execute 成功后触发 todo_update SSE
- [ ] 后端日志打印 todo_update 事件内容

## 前端 UI
- [ ] useChatStore SSE 处理器加 todo_update 分支
- [ ] TodoCard 组件渲染 pending/in_progress/completed 三态
- [ ] 同会话新 TODO 替换旧卡片（不堆叠）
- [ ] pending=灰圈、in_progress=蓝转圈、completed=绿勾划线

## 端到端验证
- [ ] grep: Agent 用 grep 搜代码而非 run_command
- [ ] glob: Agent 用 glob 列文件而非 run_command
- [ ] list_files: Agent 用 list_files 探索陌生目录结构
- [ ] multi_edit: Agent 一次调用改多处
- [ ] apply_patch: Agent 生成 diff 并应用
- [ ] undo_edit: Agent 编辑后调 undo_edit 文件回滚
- [ ] diagnostic: Agent 写完代码后调 diagnostic 验证语法
- [ ] webfetch: Agent 抓取 URL 提取内容
- [ ] view_image: Agent 主动调 view_image 分析本地图片
- [ ] todo_write: 长任务时 Agent 先列 TODO 再逐步执行
- [ ] todo_write 前端可见：清单实时更新，走一步划一步
- [ ] list_skills: Agent 调 list_skills 看到 3 个预置 skill
- [ ] call_skill: Agent 调 calc_pullup_resistor 算出上拉电阻值
- [ ] 回归验证：短问题不触发 todo_write

## 文档
- [ ] architecture-map.md 工具清单更新到 27
- [ ] pitfalls.md 记录新工具踩坑（如有）
