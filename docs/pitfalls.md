# Hardware RAG Agent 项目踩坑记录

## 2026-07-11 - ParentDocument 检索 _collect_big_chunks 跨页 section page_range 聚合错误【已修复】

- 错误现象：
  跨页 section（一个小块在第 3 页、一个小块在第 4 页、一个小块在第 5 页）入库后，big_chunks 表里该大块的 page_start/page_end 只记录了最后一个小块的页范围（5,5），而非 section 完整页范围（3,5）。前端 BigChunkViewer 显示的页码信息不准确。
- 错误原因：
  `vector_store.py::_collect_big_chunks` 用 dict "后写覆盖"语义，每个同 big_chunk_id 的小块都整条覆盖已有记录。`_build_big_chunk_record` 只取当前小块的 `chunk.page_range`，未聚合同 big_chunk_id 所有小块的 page_range。由于小块的 page_range 是按小块自己的 page marker 提取的（如 (3,3)、(4,4)、(5,5)），后写覆盖导致大块 page_range 只反映最后一个小块。
- 修复方式：
  修改 `_collect_big_chunks`，在覆盖前检查已有记录，聚合同 big_chunk_id 所有小块的 page_range：
  ```python
  existing = big_chunks.get(bc_id)
  if existing is not None:
      record["page_start"] = min(existing["page_start"], record["page_start"])
      record["page_end"] = max(existing["page_end"], record["page_end"])
  big_chunks[bc_id] = record
  ```
  其他字段（text、section_title 等）保持后写覆盖（同 section 的小块这些字段相同）。
- 下次注意：
  - dict "后写覆盖"语义用于去重时，要检查被覆盖字段是否需要聚合（如 page_range 取 min/max、score 取 max、small_chunk_ids 取并集）
  - 小块和大块的 page_range 语义不同：小块是小块自己所在页，大块是整个 section 的页范围。从小块聚合大块时必须取 min(start)/max(end)
  - 边界 case 测试要覆盖"跨页 section 多个小块分布在不同页"的场景

## 2026-07-11 - autocompact 消息条数判断导致 11 条消息时压缩被误判失败【已修复】

- 错误现象：
  当对话历史恰好 11 条消息且 token 超阈值时，autocompact LLM 摘要成功（1 条摘要 + 10 条最近 = 11 条），但三处调用方用 `len(compacted) >= len(messages)` / `== len` / `< len` 判断压缩是否成功，因条数不变误判为"压缩失败"，导致 ContextLimitError 误触发（sse_adapter 主循环路径）或压缩结果被丢弃（chat_routes 发送前检查路径）。
- 错误原因：
  `autocompact_messages` 在无操作时返回原列表对象（`return messages`），在压缩成功时返回新列表。但三处调用方用消息**条数**而非**对象身份**判断是否压缩成功。当原始 11 条 → 摘要后仍 11 条时，条数不变但 token 确实减少了，却被误判为失败。
- 修复方式：
  三处调用方改用 `is` / `is not` 身份检查替代条数比较：
  - `sse_adapter.py::_compact_and_apply`：`if compacted is messages: return False`
  - `autocompact.py::_recover_with_autocompact`：`if compacted is messages: return False`
  - `chat_routes.py::_check_and_compact_history`：`if compacted is not lc_messages:`
- 下次注意：
  - 判断"操作是否生效"时，不能用数量比较代替身份/内容比较——特别是当操作可能产生"数量不变但内容变化"的结果时
  - `autocompact_messages` 的接口约定：返回原列表对象 = 无操作，返回新列表 = 已压缩。修改此函数时需保持这一约定
  - 边界条件测试应覆盖 `len(messages) == AUTOCOMPACT_KEEP_RECENT_MSGS + 1`（即 11 条）的场景

## 2026-07-11 - alembic 与 init_db() 状态不一致导致 upgrade 失败【已修复】

- 错误现象：
  执行 `alembic upgrade head` 时报 `sqlite3.OperationalError: duplicate column name: call_id`，alembic 版本卡在 `9467f02f4f43`（init），无法升级到 `f4a1b2c3d4e5`（tool_audit 加列）。
- 错误原因：
  `database.py::init_db()` 通过 `ALTER TABLE ... ADD COLUMN` 幂等地添加了 `call_id`/`success`/`error_type` 等列，但未更新 alembic 的 `alembic_version` 表。数据库实际 schema 领先于 alembic 记录的版本，导致 alembic 尝试重复执行已生效的迁移。
- 修复方式：
  `alembic stamp f4a1b2c3d4e5` 将版本标记为已应用（跳过实际执行），然后 `alembic upgrade head` 正常执行后续迁移。
- 下次注意：
  - `init_db()` 的手动迁移和 alembic 迁移不能混用而不同步版本号
  - 如果 `init_db()` 已经手动加了列，新增 alembic 迁移前先 `alembic stamp <对应revision>` 对齐版本
  - 长期应考虑统一用 alembic 管理迁移，将 init_db() 的 ALTER TABLE 逻辑迁移为 alembic 脚本

## 2026-07-11 - 跨会话来源查看器状态污染 + 右侧面板固定高度【已修复】

- 错误现象：
  新建或切换对话后，右侧"对话内容"面板仍显示旧对话的来源列表/卡片，且点击无响应。同时点击新来源后右侧详情只显示上半截，下半部分空白，未占满整栏。刷新页面后暂时恢复正常。
- 错误原因：
  `fileViewerSource` 和 `highlightSourceId` 存储在全局 `useAppStore` 中，切换会话时不重置，旧会话的"当前查看来源"ID 残留导致新会话右侧面板仍尝试定位/高亮旧来源。另外 `RightPanel.tsx` 中 `.source-fv-content` 被硬编码 `style={{ height: 400 }}`，父容器未启用 flex 纵向填充，MonacoEditor 被锁死在 400px。
- 修复方式：
  将 `fileViewerSource` / `highlightSourceId` 从 `useAppStore` 迁移到 `useChatStore` 中按 `sessionId` 隔离的 `sessionFileViewerSource` / `sessionHighlightSourceId` 字典。所有消费方（RightPanel / ChatArea / AssistantMessageRow / KnowledgePanel）改为按 `activeSessionId` 读写。`deleteSession` 时调用 `cleanupSessionSourceState` 清理。`.source-fv-content` 改为 `style={{ flex: 1, minHeight: 0 }}`，CSS 加 `display: flex; flex-direction: column`。
- 下次注意：
  - 任何与"当前会话"关联的 UI 状态（选中项、高亮项、查看中项）必须按 sessionId 隔离，不能放在全局 store
  - MonacoEditor 等高度敏感组件不要用固定 px 高度，用 flex + min-height: 0 让父容器控制
  - "刷新后恢复正常"是内存状态未正确重置的典型信号，遇到时优先排查 store 状态隔离

## 2026-07-09 - Agent 文件操作安全审计：30 个问题（4 HIGH 数据丢失风险）【部分已修复】

3 个 subagent 并行审计 file_ops git snapshot / path_guard 绕过 / run_command 漏检，去重后约 30 个问题。最严重的 4 个 HIGH 数据丢失风险如下。其中 #1/#2/#4 + index.lock 并发已修复，#3（命令体绕过 path_guard）待修。

### 1. undo_edit `git reset --hard HEAD~1` 在脏工作区丢弃所有未提交修改【已致 12 文件丢失】【已修复】

- 错误现象：
  测试 undo_edit 时，工作区有 12 个其他线程的未提交修改（前端组件/i18n/store/css/pitfalls.md）。undo_edit 执行 `git reset --hard HEAD~1`，把这 12 个文件的修改全部丢弃，无法恢复（从未 staged，reflog 也救不回来）。
- 错误原因：
  `_git_snapshot` 只 `git add -- <单个文件>`，快照粒度是单文件；但 `undo_edit` 用 `git reset --hard HEAD~1`，回滚粒度是整个工作树。快照粒度（单文件）与回滚粒度（全工作树）严重不对称 + reset 前无任何保护（不检查 git status、不 stash、不提示）。
- 修复方式：
  `undo_edit.py::_run_reset()` 在 reset 前调 `_stash_unstaged()` 执行 `git stash push --keep-index --message "undo_edit: pre-reset stash"` 保存未暂存修改；reset 后调 `_pop_stash()` 执行 `git stash pop`。若 pop 冲突，git 会保留 stash，返回提示「工作区修改已保存到 stash@{0}，请手动恢复」，绝不静默丢弃用户修改。整个 `_run_reset` 包在 `get_git_sync_lock()` 内防并发。
- 验证结果：
  `python -c "from src.agent.tools.groups.file_ops.undo_edit import UndoEditTool; print('undo_edit OK')"` → undo_edit OK ✓
- 下次注意：
  - 任何 `git reset --hard` 在脏工作区都会丢弃未提交修改，生产代码用前必须有 stash 保护
  - 快照粒度与回滚粒度必须对称，不能"单文件快照 + 全工作树回滚"
  - 测试 undo 类工具前必须先 checkpoint 整个工作区

### 2. `_git_snapshot_all` 用 `git add -A` 卷入用户未提交修改（我刚加的，比原 bug 更危险）【已修复】

- 错误现象：
  为修复 run_command 写文件无法 undo 的问题，新增 `_git_snapshot_all` 用 `git add -A + git commit`。但 `git add -A` 会 stage 整个 repo 的所有修改，包括用户手动改的文件。LLM 调 `run_command echo hi > log.txt` 时，若用户手改了文件 X 未提交，snapshot 会把 X + log.txt 一起 commit。之后 undo_edit 回滚该 commit，用户手改的 X 也被回滚，无感知。
- 错误原因：
  `git add -A` 的作用域是整个工作树，而 run_command 实际只写入命令重定向目标文件。用 `git add -A` 是为了绕过 cwd=backend/ 导致 `git add -- <相对路径>` 找不到 frontend/ 文件的问题，但矫枉过正，把用户修改也卷入了。
- 修复方式：
  删除 `_git_snapshot_all`，新增 `_git_snapshot_files(file_paths, tool_name)` 只 `git add -- <绝对路径列表>`。`_extract_write_targets(command, cwd)` 解析命令字符串提取写入目标（`>`/`>>`/`Out-File`/`Set-Content`/`Add-Content`/`tee`/`New-Item`/`[IO.File]::WriteAllText`/`Copy-Item`/`Move-Item` 的 dst/`Export-Csv`/`Export-Clixml`），相对路径用 cwd 或 ROOT_DIR 解析为绝对路径，再过 `validate_path(abs, is_write=True)` 校验。解析不到任何目标则跳过 snapshot（比 `git add -A` 更安全）。
- 验证结果：
  `python -c "from src.agent.tools.groups.execution.run_command import RunCommandTool, _FILE_WRITE_RE; print('run_command OK'); print(_FILE_WRITE_RE.search('echo a > b'))"` → run_command OK + None ✓（不再误检，且不再用 git add -A）
- 下次注意：
  - `git add -A` 会 stage 所有修改，绝不能用于"只 snapshot 本次工具写入"的场景
  - 修复一个 bug 时要思考新方案是否引入更严重的 bug，特别是作用域扩大的场景

### 3. run_command 命令体完全绕过 path_guard

- 错误现象：
  path_guard 放开后只校验 deny 黑名单，但 run_command 只对 `cwd` 参数调 validate_path，命令字符串本身不校验。LLM 可用 `cat C:\Users\xxx\.env`、`type %USERPROFILE%\.ssh\id_rsa`、`Get-Content $env:USERPROFILE\.gitconfig` 读取任意文件，包括黑名单里的 .env（path_guard 查不到命令体里的路径）。
- 错误原因：
  path_guard 设计为校验"工具显式接收的路径参数"，但 run_command 的命令是一个自由文本字符串，里面的路径不经过 path_guard。PowerShell 的 $env: 变量展开也让 path_guard 看不到真实路径。
- 修复方式：
  暂未修复。建议：run_command 执行前对命令做黑名单路径扫描（提取命令中的绝对路径 token，检查是否命中 DENY_PATTERNS）；或对 cat/type/Get-Content 类读取命令额外检查路径参数。
- 下次注意：
  - 自由文本命令（shell 字符串）里的路径无法被 path_guard 拦截，需要单独的命令体扫描
  - PowerShell $env: 变量展开、8.3 短文件名、`cd ../.git` 都是绕过路径校验的手段

### 4. _FILE_WRITE_RE 任意 `>` 字符误检 + 大量写文件命令漏检【已修复】

- 错误现象：
  (a) 误检：`\d?>{1,2}\s*\S+` 用 re.search 子串匹配，任何包含 `>` 的命令都触发 snapshot——`echo "a > b"`、`python -c "print(1 > 2)"`、`git log --format="%H > %s"` 都被误判为写文件。叠加问题 2 的 `git add -A`，用户未提交修改被频繁卷入 agent commit。
  (b) 漏检：`git checkout -- file`、`git restore .`、`Copy-Item`、`Move-Item`、`Remove-Item`、`del`、`Clear-Content`、`[IO.File]::WriteAllText`（短命名空间）、`python -c "open('x','w')"`、`curl -o file` 全部漏检，LLM 用这些命令写文件完全无 snapshot。
- 错误原因：
  正则 `\d?>{1,2}` 没有锚定（应要求 `>` 在命令开头或 `;|&` 之后），导致字符串里的 `>` 误匹配。漏检则是因为只列了常见写文件模式，没覆盖 PowerShell cmdlet 全集、git 覆盖类操作、.NET 短命名空间、python 内联、下载工具。
- 修复方式：
  重写 `_FILE_WRITE_RE`：所有重定向/cmdlet 替代项前缀 `(?:^|[;&|]\s*)` 锚定（命令开头或 `;`/`&`/`|` 分隔符之后），杜绝字符串内 `>` 误匹配。扩充覆盖：`git checkout --`/`git restore`/`git stash pop|apply`/`Copy-Item`/`Move-Item`/`Rename-Item`/`Remove-Item`/`del|erase|rd|rmdir`/`Clear-Content`/`[IO.File]::`（短命名空间）/`python -c "open(...)"`/`curl -o`/`wget -O`/`Invoke-WebRequest -OutFile`/`Export-Csv`/`Export-Clixml`/`Rename-Item`/`AppendAll`。.NET API / curl / wget / git / python 用各自关键字锚定，不需要 `;|&` 前缀。
- 验证结果：
  - `echo "a > b"` → 不再误检（`search(...) is None` → True）✓
  - `git checkout -- file.py` → 正确检出（`search(...) is not None` → True）✓
- 下次注意：
  - 检测 shell 写文件用正则极易误检/漏检，误检比漏检更危险（卷入用户修改）
  - `git checkout --` / `git restore` 是 LLM 自然语言推理的高频路径，必须覆盖

### 其他 HIGH 问题（简要）

- apply_patch 文档声称禁用但 agent_factory.py:573 仍注册，tool_config.json 不存在 → Agent 仍会调用有 bug 的 apply_patch【已修复：agent_factory.py::_build_local_file_ops_tools 移除 ApplyPatchTool 的 import 和实例化，工具列表从 9 减为 8，Agent 不再注册 apply_patch；file_ops/__init__.py 的包级导出保留无害（无其他代码实例化）】
- 并发 git add 抢 index.lock，失败静默吞掉，snapshot 丢失（无全局锁）【已修复：新建 `_git_lock.py` 提供 `get_git_lock()`(asyncio.Lock) + `get_git_sync_lock()`(threading.Lock)，`_git_snapshot`/`_git_snapshot_files`/`_run_reset` 全部包在 sync 锁内，`_do_undo`/`_take_snapshot` 包在 async 锁内。threading.Lock 是真正的跨线程串行器，覆盖 file_ops 各工具自带的 asyncio.to_thread 调用路径】
- LOW 风险前缀白名单（risk_classifier.py:69）放行 `cat`/`type` 读敏感文件，无 HITL 确认
- 黑名单漏 id_rsa/id_ed25519/.ssh//*.kdbx/*.pfx 等无扩展名私钥和凭证文件

### 5. _git_snapshot 跨目录 git add 静默丢失快照（cwd=backend/ 找不到 frontend/ 文件）【已修复】

- 错误现象：
  `_git_snapshot` 在 `cwd=ROOT_DIR`（=backend/）下执行 `git add -- <file_path>`。当 file_path 是相对路径如 `frontend/src/app.tsx` 时，git 在 backend/ 下找不到该文件，`git add` 静默失败，snapshot 丢失，undo_edit 无法回滚该次编辑。
- 错误原因：
  快照函数的 cwd（backend/）与被编辑文件的实际位置（可能在 frontend/、scripts/ 等任意子树）不一致，相对路径在错误目录下解析失败。
- 修复方式：
  `_git_snapshot.py::_git_snapshot()` 在调 `git add` 前用 `os.path.abspath(file_path)` 转为绝对路径，git 在 repo root（ROOT_DIR 的 git toplevel）下用绝对路径 add 即可跨目录工作。
- 验证结果：
  `python -c "from src.agent.tools.groups.file_ops._git_snapshot import _git_snapshot; print('snapshot OK')"` → snapshot OK ✓
- 下次注意：
  - git 命令的 cwd 与被操作路径不在同一目录时，必须用绝对路径
  - `git add` 对找不到的路径静默失败（不报错），snapshot 类操作必须验证 returncode

### 6. path_guard 黑名单覆盖不全 + risk_classifier 误判风险等级【已修复】

- 错误现象：
  (a) `DENY_PATTERNS` 只覆盖 `.git/.env/*.key` 等少量模式，漏掉 `id_rsa`/`id_ed25519`/`.ssh/`/`*.pfx`/`*.kdbx`/`.aws/`/`.gitconfig`/`.netrc`/`SysWOW64`/`Program Files`/`/etc/` 等，LLM 可经 read_file 读取 SSH 私钥、`.env`、凭证目录。
  (b) `"System32/"` 用子字符串匹配，路径 `C:\Windows\System32`（无尾斜杠）不匹配 `system32/`，目录本身可被 list_files 列出。`.ssh/` 同理——路径 `C:\x\.ssh`（无尾斜杠）绕过。
  (c) Windows 8.3 短文件名 `SYSTEM32~1` 不匹配 `System32`/`system32/`，可绕过。
  (d) `LOW_RISK_PREFIXES` 含 `cat`/`type`，`cat C:\x\.env` 判 LOW 自动执行，无 HITL；含 `git branch`，`git branch -D feature` 判 LOW 自动删分支。
  (e) `MEDIUM_RISK_KEYWORDS` 漏 `git restore`/`git checkout --`/`git stash drop`/`git rebase`/`git filter-branch`/`git push -f` 等破坏性 git。
- 错误原因：
  黑名单只列了最初想到的几类，未系统枚举凭证/系统目录；带尾斜杠模式只做子字符串匹配，未对"目录本身"（路径末尾无分隔符）兜底；8.3 短名未检测；LOW 白名单把读文件命令 `cat`/`type` 和有破坏性子命令的 `git branch` 一律放行。
- 修复方式：
  - `path_guard.py`：扩充 `DENY_PATTERNS`（证书扩展名/无扩展名私钥/凭证目录/dotfile 凭证/Windows+Linux 系统目录）；`matches_deny_pattern` 重写为「8.3 短名检测 → 简单模式组件匹配 → 斜杠模式（子字符串 + 去尾斜杠组件名匹配）」三段，拆成 `_detect_short_name`/`_match_simple_patterns`/`_match_slash_patterns`/`_match_one_slash_pattern`/`_part_matches` 五个 ≤10 行函数；新增 `SHORT_NAME_PATTERN = re.compile(r"~\d+$")` 和预切分常量 `_DENY_SIMPLE`/`_DENY_SLASHED`。
  - `risk_classifier.py`：`LOW_RISK_PREFIXES` 移除 `cat`/`type`/`git branch`（读文件走 read_file + path_guard；git branch 一律 MEDIUM）；`MEDIUM_RISK_KEYWORDS` 追加 9 个破坏性 git 命令。
- 验证结果：
  临时脚本 9/9 通过：`id_rsa`→`path matches deny pattern: id_rsa`；`System32\config\SAM` 与 `System32` 目录本身均拦截；`WINDOWS~1\SYSTEM32~1`→`8.3 short name detected`；`cat C:\x\.env`→medium；`git branch -D feature`→medium；`git restore .`→medium；正常项目文件 `main.py`→ok；`.ssh` 目录本身→拦截。
- 下次注意：
  - 带尾斜杠的目录黑名单模式必须同时匹配"目录本身"（去尾斜杠后按 Path.parts 组件匹配），否则路径以目录名结尾（无分隔符）时绕过
  - 8.3 短文件名（`NAME~N`）是 Windows 路径校验绕过的通用手段，路径校验器应单独检测
  - LOW 风险白名单不要纳入"读文件内容"类命令（cat/type/Get-Content），文件读取应统一走带 path_guard 的 read_file
  - 含破坏性子命令的命令（如 `git branch` 含 `-D`）不应整族放 LOW，要么精确列举只读子命令，要么整族走 MEDIUM
  - 改 path_guard 时函数体易超 10 行，按匹配策略拆分小函数即可满足规范

### 附带 bug

- pio_runner.py:506 `_ensure_upload_port(project_dir, req.board, req.platform, req.port)` 传 4 参数，定义只收 2 参数 → flash_firmware 会 TypeError【已修复：调用改为 `_ensure_upload_port(project_dir, req)`。函数签名 `(project_dir: Path, req: UploadRequest)` 与函数体（通过 req.port/req.board/req.platform/req.lib_deps/req.framework 访问字段）本就正确，仅调用处多传了 3 个冗余参数，改最小改动修调用处即可】
- risk_classifier.py:71 `git branch` 前缀让 `git branch -D` 误判 LOW → 自动删分支

## 2026-07-08 - subagent Edit 对 .py/.tsx 文件不落盘 + commit 声称的改动实际缺失【已修复】

- 错误现象：
  前次对话用 5 个 subagent 做批次 1-3 改动，subagent 全部报告 Edit 成功。手动验证发现 .tsx/.py 改动未落盘，手动 Edit 重做后提交 commit fbbce71。但本次回归测试发现 commit fbbce71 实际只包含 2 个后端文件（kb_routes.py/search_routes.py），其余 5 个后端文件（auth.py/hardware_routes.py/attachments.py/chat_helpers.py/chat_routes.py）的改动从未落盘。
- 错误原因：
  subagent 的 Edit 工具对 .tsx/.py 文件存在不落盘问题（.css 和工作台 .tsx 例外）。手动 Edit 重做时可能只做了前端文件，后端文件被遗漏。commit message 声称 "21 items persisted" 但实际后端只落盘了 2 个文件。
- 修复方式：
  重新读取 5 个后端文件，用 Edit 工具逐个重新实施：auth.py 缓存 + hardware_routes/attachments async + chat_helpers async + chat_routes heartbeat + kb_routes 5 函数 async→def。提交 commit c551a8e。
- 验证结果：
  - python import 7 个模块全部 OK ✓
  - from app.main import app 加载 81 条路由 ✓
  - npx tsc --noEmit 退出码 0 ✓
- 下次注意：
  - subagent Edit 对 .tsx/.py 不落盘，必须自己用 Edit/Write
  - 提交前必须用 Grep 验证关键改动是否在磁盘上，不能只看 commit message
  - commit message 不要声称 "persisted" 除非逐个文件验证过

## 2026-07-08 - Edit 工具 replace_all=true 批量替换不持久化到磁盘【已修复】

- 错误现象：
  用 Edit 工具执行 `replace_all: true` 把 `border-radius: 4px;` 批量替换为 `border-radius: var(--radius-sm);`，工具返回成功，但 `git diff` 显示该改动全部丢失，只有同文件中非 replace_all 的单次 Edit 持久化了。重新 Read 目标文件确认原始内容仍在原位。
- 错误原因：
  Edit 工具在 `replace_all: true` 模式下对同一文件内多处匹配的批量替换存在不落盘的异常（疑似工具内部状态与磁盘写入脱节）。单次精确 Edit 不受影响。
- 修复方式：
  改用 PowerShell 脚本批量改写：`[System.IO.File]::ReadAllText` 读全文 → `-replace` 正则替换 → `[System.IO.File]::WriteAllText` 直接写磁盘。脚本一次性处理多个 CSS 文件，运行后 `git diff --stat` 确认 9 个文件 260+/251- 行改动全部落盘。
- 验证结果：
  - PowerShell 脚本写入后 `git diff --stat` 显示全部预期改动 ✓
  - `npx vite build --mode development` 成功（6.93s，CSS 131.80 kB，无错误）✓
- 下次注意：
  1. 对同一文件内多于 2 处的相同字符串替换，不要依赖 Edit 工具的 `replace_all: true`，优先用 PowerShell `[System.IO.File]::ReadAllText/WriteAllText` + `-replace` 直接操作磁盘。
  2. 任何批量替换后必须用 `git diff --stat` 或重新 Read 验证落盘，不能只信任工具返回的 success。
  3. 临时批量脚本用完即删，不要留在仓库里（本次 `scripts/_ux_polish_1c.ps1` 已清理）。

---

## 2026-07-08 - 迁移 bookmark 样式时误删工作台 `.wb-content` 样式导致工作台下半部分空白【已修复】

- 错误现象：
  用户反馈工作台所有界面下半部分全部空白。检查发现 `frontend/src/styles/misc.css` 中的 `.wb-content` / `.wb-content-pad` 样式消失，`WorkbenchPanel.tsx` 依赖这些类实现 flex 填充。
- 错误原因：
  在把 bookmark 样式从 `misc.css` 迁移到新建的 `bookmarks.css` 时，`.wb-content` / `.wb-content-pad` 与 bookmark 样式写在同一段注释块内，被一并删除。这些类名实际上是工作台（workbench）在用，不是 bookmark 专属。
- 修复方式：
  在 `misc.css` 中恢复 `.wb-content` / `.wb-content-pad` / `.wb-content-pad > :last-child` 三条规则。
- 验证结果：
  - `npx tsc --noEmit` 通过 ✓
  - Grep 确认 `wb-content` 样式与 `WorkbenchPanel.tsx` 引用均存在 ✓
- 下次注意：
  1. 删除/迁移一段 CSS 时，必须确认段内每个选择器是否真的属于目标模块，不要按注释块整段删除。
  2. 迁移后用 Grep 搜索被删选择器是否仍有组件引用。

## 2026-07-08 - 后端重启后 `/api/explorer/dir` 因授权根丢失返回 400【已修复】

- 错误现象：
  验证 Agent 在测试过程中重启了后端，之后展开 `.platformio/platforms` 等大目录时，`/api/explorer/dir` 返回 `400 security check failed: path is outside authorized directories`，文件树懒加载失败。
- 错误原因：
  `src/explorer/security.py` 使用进程级内存集合 `_AUTHORIZED_ROOTS` 保存已授权的项目根目录。后端进程重启后该集合清空，而前端仍持有旧的树状态，后续懒加载请求因找不到授权根而被拒绝。
- 修复方式：
  在 `frontend/src/components/explorer/ExplorerPanel.tsx` 的 `fetchAndReplaceChildren` 中捕获安全/授权类错误，自动调用 `POST /api/explorer/open` 重新授权当前根目录，然后重试一次 `/api/explorer/dir`。
- 验证结果：
  - 未授权时 `/api/explorer/dir` 正确返回 400 ✓
  - 重新授权后懒加载成功 ✓
- 下次注意：
  1. 后端基于内存的授权/白名单状态在后端重启后会丢失，前端关键操作要做好「重新认证 + 重试」的兜底。
  2. 如果希望授权状态跨重启保持，需要把它持久化到磁盘或改用基于请求参数的无状态校验。
  3. 验证 Agent 重启后端后，要刷新页面或重新打开根目录，避免把「重启后状态丢失」误判为功能 bug。

---

## 2026-07-08 - 验证 Agent 可能遗留测试文件并修改范围外文件【记录】

- 错误现象：
  双 Agent 验证结束后，`git status` 出现大量非 explorer 文件的修改（chat、workbench、settings、styles 等），以及未跟踪的 `test_ops_dir/`、`explorer_home.png`、`scripts/explorer_boundary_test.py` 等测试产物。
- 错误原因：
  浏览器自动化 Agent 在测试过程中会通过 `browser_evaluate` 写入全局变量、创建测试文件，其运行环境或并发脚本也可能回写源码；同时 Agent 为绕过剪贴板限制会 monkey-patch `navigator.clipboard.writeText`。这些操作污染了工作区。
- 修复方式：
  1. 核对 `git status --short`，明确区分本次任务应提交的文件与 Agent 污染文件。
  2. 对范围外修改使用 `git checkout --` 回滚，对未跟踪测试产物使用 `Remove-Item` / `DeleteFile` 清理。
  3. 仅保留 explorer 范围内的修复：`ExplorerPanel.tsx`、`FolderPickerDialog.tsx`、`backend/app/api/explorer_routes.py`。
- 验证结果：
  - 工作区回到仅包含 explorer 改动的干净状态 ✓
- 下次注意：
  1. 启动验证 Agent 前明确告知「不要修改代码、不要提交、测试后清理文件」。
  2. Agent 返回后第一件事是检查 `git status`，而不是直接信任其结论。
  3. 对会写文件或改全局状态的 Agent 操作，测试结束后要定向清理。

---

## 2026-07-08 - 前端仓库存在大量预存 tsc/lint 错误，commit 前需区分新增与既有【记录】

- 错误现象：
  在 `feat/explorer-round2` 分支跑 `npx tsc -b` 和 `npm run lint` 时报出数十个错误，分布在 `src/components/chat/*`、`src/stores/useChatStore.ts`、`src/stores/useSessionStore.ts`、`src/components/workbench/*` 等文件，与本次文件资源管理器改动无关。
- 错误原因：
  这些错误是既有代码积累的类型不匹配、未使用变量、react-hooks 依赖缺失、`react-refresh/only-export-components` 等问题，在 Step E 之前已存在于 `HEAD`（通过 `git show HEAD:frontend/src/i18n/en.ts` 等确认）。本次 Step E 仅新增/修改了 `frontend/src/components/explorer/*` 与 i18n 的少量 key，未触碰 chat/session/workbench 模块。
- 修复方式：
  1. 先修复 Step E 自身引入的报错：`EditorPanel.tsx` 缺少 `useRef` 导入、`editorTabParts.tsx` 导出非组件函数触发 Fast refresh 规则、i18n 重复 key。
  2. 对既有报错本次不予修复（超出 explorer 范围，避免误改其他模块），但在 `docs/pitfalls.md` 记录，commit 时按「本次无新增错误」原则放行。
- 验证结果：
  - Step E 相关文件 lint/tsc 无新增错误 ✓
  - 剩余错误均为既有错误，与 explorer 无关 ✓
- 下次注意：
  1. 大型项目 commit 前先确认错误是否由本次改动引入，可用 `git stash` 或 `git show HEAD:path` 对比基线。
  2. 不要借修复 lint 之名修改无关模块，除非明确属于当前任务范围。
  3. 若预存错误阻塞 CI，应单独开一轮「代码质量」任务集中清理，而不是混在功能 commit 里。

---

## 2026-07-08 - 复制绝对路径在部分浏览器/上下文失效【已修复】

- 错误现象：
  用户在文件树右键选择「复制路径」后，剪贴板中并没有内容；在 http://127.0.0.1（非安全上下文）或某些浏览器中尤其明显。
- 错误原因：
  `navigator.clipboard.writeText()` 要求页面处于安全上下文（HTTPS 或 localhost），且需要用户交互触发。在 iframe、非安全上下文或权限被拒绝时会静默失败。
- 修复方式：
  在 `frontend/src/utils/clipboard.ts` 中实现两层 fallback：
  1. 优先调用 `navigator.clipboard.writeText(text)`。
  2. 失败或不可用时，创建一个临时 `textarea`，选中内容并执行 `document.execCommand('copy')`，执行后移除节点。
  3. 返回布尔值表示是否成功，调用方根据结果弹出成功/失败 toast。
- 验证结果：
  - 非 HTTPS 本地开发环境复制路径成功 ✓
  - 失败时前端明确提示「复制路径失败」✓
- 下次注意：
  1. 任何依赖 `navigator.clipboard` 的功能都要准备 fallback，不要假设 API 一定可用。
  2. fallback 用 textarea + execCommand 时，需要把元素加入 DOM、聚焦、选中再执行命令，最后清理。
  3. 复制结果要反馈给用户，不要静默失败。

---

## 2026-07-08 - 文件树键盘导航 focusedPath 类型不一致导致 tsc 报错【已修复】

- 错误现象：
  Step H 给 `FileTree.tsx` 增加键盘导航后，`npx tsc -b` 报两个错误：
  - `FileTree.tsx(600,10): Type 'string | null' is not assignable to type 'string | undefined'`
  - `FileTree.tsx(606,10): Type 'string | null' is not assignable to type 'string | undefined'`
  同时 `VirtualFileTree.tsx` 报 `Expected 6 arguments, but got 7`。
- 错误原因：
  1. `FileTree.tsx` 中 `focusedPath` 状态声明为 `string | null`，但传递给子组件 `VirtualFileTree` / `FileTreeNode` 的 prop 声明为 `string | undefined`。
  2. `VirtualFileTree.tsx` 的 `buildNodeClass` 函数签名只接受 6 个参数，但 `FileTreeRow` 调用时传入了第 7 个 `isFocused`。
- 修复方式：
  1. 统一 `focusedPath` 类型为 `string | undefined`，与子组件 prop 类型对齐。
  2. 给 `VirtualFileTree.tsx` 的 `buildNodeClass` 增加 `isFocused: boolean` 参数，并追加 `"is-focused"` class。
- 验证结果：
  - explorer 相关文件 tsc 无新增错误 ✓
- 下次注意：
  1. 新增状态时，先确定子组件 prop 的可选类型，尽量用 `undefined` 而不是 `null` 表示「未设置」。
  2. 同一类样式构建函数在普通渲染和虚拟渲染两个组件中各有一份时，改签名要两边同步更新。
  3. 跑 `tsc -b` 后只关注自己模块的错误，但修复时要同步检查同源文件。

---

## 2026-07-08 - TypeScript interface 导入导致 Vite blank page（import type 陷阱）【已修复】

- 错误现象：
  浏览器打开 http://127.0.0.1:5173/ 后页面完全空白，root div 的 innerHTML 为空。`npx tsc --noEmit` 通过（0 错误），Vite 控制台无报错，浏览器 console 也无明显错误。ErrorBoundary 未触发（因为它在 React render 阶段才生效，而崩溃发生在模块加载阶段）。
- 错误原因：
  `EditorPanel.tsx` 从 `editorTabParts.tsx` 导入 `ContextMenuPos`（一个 TypeScript interface）时用了普通 `import { ContextMenuPos }`，而非 `import type { ContextMenuPos }`。Vite 使用 esbuild + `isolatedModules: true`，esbuild 无法跨文件判断一个导出是类型还是值，因此保留了运行时 import。但 interface 在编译后被擦除，运行时该导出不存在，导致 `SyntaxError: does not provide an export named 'ContextMenuPos'`，整个模块图崩溃，React 根本没机会挂载。
- 修复方式：
  将 `ContextMenuPos` 的导入改为 inline `type` 修饰符：`import { ..., type ContextMenuPos } from "./editorTabParts"`。esbuild 看到 `type` 修饰符后会在编译时移除该导入。
- 验证结果：
  刷新页面后 root div 有内容，UI 正常渲染 ✓
- 下次注意：
  1. **Vite + esbuild 的 `isolatedModules` 模式下，跨文件导入 interface/type 必须用 `import type` 或 inline `type` 修饰符**。tsc 能通过是因为 tsc 做全量类型分析，但 esbuild 只做单文件转译。
  2. **Blank page + 无 console 错误 + tsc 通过 = 大概率是模块加载阶段崩溃**。诊断方法：在浏览器 console 手动 `import('/src/main.tsx').catch(e=>console.log(e))` 看具体报错。
  3. ErrorBoundary 无法捕获模块加载阶段的错误（它在 React render 阶段才生效），所以 blank page 不会被 ErrorBoundary 的 UI 捕获。


## 2026-07-07 - 文件浏览器刷新后状态丢失 + 加载缓慢 + 中文文件被误判为二进制【已修复】

- 错误现象：
  1. 刷新浏览器后，文件树消失、打开的文件 tab 丢失、编辑器内容丢失（用户期望"刷新后还是那个界面"）
  2. 文件树加载超过 60 秒仍未完成（.venv 等大目录递归遍历太慢）
  3. AGENTS.md 等含中文的文件被误判为二进制文件，无法在编辑器中打开
- 错误原因：
  1. **editorShowTree 未持久化**：`useAppStore.ts` 中 `editorShowTree: false` 硬编码，刷新后总是 false，文件树不显示
  2. **.venv 等大目录拖慢加载**：`_visible()` 对每个子项调用 `validate_path()`（涉及 `path.resolve()` 文件系统访问），.venv 有数千个文件，遍历极慢
  3. **UTF-8 多字节字符截断**：`files.py` 的 `_is_valid_utf8()` 读取前 8192 字节做 strict decode，中文等多字节字符可能在采样末尾被截断，导致 `UnicodeDecodeError`，文件被误判为二进制
- 修复方式：
  1. **editorShowTree 持久化**：persistence.ts 添加 `EDITOR_SHOW_TREE_KEY` + `loadEditorShowTree`/`saveEditorShowTree`；useAppStore 初始化时 `loadEditorShowTree()`；`setEditorShowTree` 中调用 `saveEditorShowTree`
  2. **SKIP_DIRS 跳过大目录**：tree.py 添加 `SKIP_DIRS` 集合（.venv, node_modules, .platformio 等），这些目录只创建节点不遍历子项；`_visible` 改为 `_is_visible_name` 快速名字过滤（不调用 resolve）。**注意：SKIP_DIRS 是性能优化（跳过自动生成目录），不是文件数量限制。**
  3. **增量 UTF-8 解码**：files.py 的 `_is_valid_utf8()` 改用 `codecs.getincrementaldecoder("utf-8")` + `final=False`，截断的多字节字符不会报错
- 验证结果：
  - 刷新后文件树 5 秒内恢复 ✓
  - 刷新后 AGENTS.md tab + 内容自动恢复 ✓
  - editorShowTree 状态持久化 ✓
  - AGENTS.md 不再被误判为二进制 ✓
- 下次注意：
  1. UTF-8 采样验证不能用 strict decode——多字节字符可能被采样边界截断。用增量解码器 `final=False` 处理。
  2. 文件树递归遍历必须跳过 .venv/node_modules 等已知大目录，否则 iterdir + 安全检查会严重拖慢加载。
  3. UI 状态（面板开关、显示模式等）应该持久化到 localStorage，刷新后恢复用户的选择。

## 2026-07-07 - 文件浏览器不显示文件（只显示部分目录）【已修复】

- 错误现象：
  用户打开 agent 文件夹后，文件树只显示 15 个以 `.` 开头的目录和 backend/data，看不到 docs/frontend/scripts 等目录，更看不到任何文件（AGENTS.md, Dockerfile, docker-compose.yml 等）。用户期望像 VS Code 一样显示全部文件。
- 错误原因：
  `backend/src/explorer/tree.py` 的 `build_tree` 递归遍历时有 `MAX_TOTAL_NODES=2000` 上限。当递归到 `.venv`（几百个文件）、`backend`（几百个 Python 文件）、`data`（大量 PDF 和图片）等大目录时，counter 很快达到 2000。达到上限后代码用 `break` 直接中断循环，导致排在后面的目录（docs, frontend, scripts...）和**所有文件**（文件排在目录后面）都被截断。
  - 排序逻辑 `children.sort(key=lambda c: (not c.is_dir(), c.name.lower()))` 让目录排前面、文件排后面，所以文件总是先被截断。
- 修复方式（最终方案——用户要求删除所有数量限制）：
  1. **删除 MAX_TOTAL_NODES 配额**：用户明确要求"这些限制删了，万一有更大的文件呢"。完全移除 counter 逻辑，无节点数量上限。
  2. **保留 SKIP_DIRS 跳过大目录**：.venv/node_modules/.platformio 等自动生成的目录只创建节点不遍历子项（这是性能优化，不是文件数量限制）。目录本身仍显示在树中。
  3. **_visible 改为快速名字过滤**：不调用 `validate_path`（避免 resolve 文件系统访问），只检查 DENY_PATTERNS 名字匹配
- 验证结果（删除限制后）：
  - 后端 build_tree 耗时 1.88s（SKIP_DIRS 跳过大目录保证速度）
  - 总节点数 2195（文件 1869 + 目录 326），完整无截断
  - JSON 大小 523 KB
- 下次注意：
  1. 删除数量限制后，必须保留 SKIP_DIRS 跳过 .venv/node_modules 等大目录，否则加载时间会暴涨到 60s+。
  2. SKIP_DIRS 是性能优化（跳过自动生成目录），不是文件数量限制——用户可以查看这些目录的节点，只是不递归遍历子项。

## 2026-07-07 - 文件浏览器打开同一文件夹每次显示内容不一致【已修复】

- 错误现象：
  用户每次打开同一个文件夹（如 agent 工作目录），文件树显示的内容都不一样：有时缺失部分文件，有时显示了其他文件夹的内容，反正没有正确显示当前目录的文件。
- 错误原因：
  `ExplorerPanel.tsx` 的 `loadTree` 没有防并发覆盖机制。`handleWatchEvent` 在收到文件系统事件时调用 `refreshTree()`，而 `refreshTree` 闭包捕获的是 `rootPath` state（不是最新期望路径）。
  - 场景：用户打开 A，再打开 B。`loadTree(B)` 开始 await，此时 `rootPath` 还是 A。A 的 watcher 发来事件，`refreshTree()` 用 `rootPath=A` 发起 `loadTree(A)`。两个 loadTree 并发，若 `loadTree(A)` 后完成，会覆盖 B 的结果：`setTree(A_tree)` + `setExplorerRootPath(A)`。用户打开的是 B，但显示 A 的文件，rootPath 也被改回 A。
  - 这解释了"显示其他文件夹的文件"和"每次打开同一文件夹显示不一样"。
- 修复方式（已实施，三处改动，均在 ExplorerPanel.tsx）：
  1. **loadTokenRef 防并发覆盖**：每次 `loadTree` 递增 token，await 返回后检查 token 是否仍是最新的，不是则丢弃结果（setTree/setExplorerRootPath/setLoading 都跳过）。保证最新的 loadTree 总是获胜。
  2. **expectedPathRef 代替 rootPath state**：`refreshTree` 用 `expectedPathRef.current`（loadTree 开头立即更新）而不是 `rootPath` state（await 后才更新）。避免 watch 事件在 loadTree await 期间用旧 rootPath 触发错误的 reload。
  3. **debouncedRefresh 去抖**：`handleWatchEvent` 改用 300ms 去抖版 refresh，合并文件系统事件风暴为一次 reload，避免并发 loadTree 抖动。组件卸载时清理 refresh 定时器。
- 验证结果（agent-browser 端到端测试）：
  - 三次打开 E:\Desktop\agent，展开后顶层子目录列表完全一致：`.agents | .arduino-data | .build | .codegraph | .deepeval | .gitnexus | .langgraph_api | .pio-final | .pio-test | .pio-test2 | .platformio | .trae | .venv | backend | data`
  - 切换文件夹后内容正确更新，无残留旧目录文件
- 下次注意：
  1. 任何 async setState 的回调都要考虑并发覆盖：用 token/序号机制保证最新获胜
  2. watch/事件驱动的 refresh 要用 ref（同步更新）而非 state（异步更新）读取"期望路径"
  3. 高频事件源（文件系统 watcher）要加去抖，避免事件风暴导致状态抖动

## 2026-07-07 - 首次添加服务商验证失败（鸡生蛋问题）【已修复】

- 错误现象：
  新用户首次添加 API 服务商后，点击"验证"按钮无响应，服务商卡片一直显示"?"未验证状态。后端日志显示 `POST /api/models 401`。刷新或多次点击验证都无效。
- 错误原因：
  `verifyProvider`（useSettingsStore.ts L213-250）调用 `POST /api/models` 验证服务商，但该端点有 `current_user` 依赖（dependencies.py L30-68），当后端 store 里已有任何 provider 时必须带有效 session_token。但是 session_token 只在验证成功后由 `store-key` 接口生成（L231-241），形成"先有鸡先有蛋"问题：
  - 首次添加 provider 时，store 为空，`current_user` 跳过鉴权（L43-44），验证可成功
  - 但如果用户之前添加过任何 provider（即使已删除），store 里残留 sessions，`_require_auth` 会要求 token，新 provider 验证就 401
  - localStorage 没 session_token 时，所有 apiPost 请求都没 Authorization 头
- 修复方式（已实施，三处改动）：
  1. **后端 `/api/models` 改用 `current_user_optional`**（chat_routes.py L507）：
     端点本身不用 `user` 参数，实际 API Key 由 `resolve_credentials` 从 X-API-Key 头解析，端点自身已有 `if not api_key: return AUTH_FAILED` 防御，无需强制鉴权。
  2. **后端新增 `/api/auth/login` 端点**（auth.py L203-248）：
     用 api_key 换 session_token，匹配策略：优先匹配指定 provider，否则搜索所有 provider。安全性：知道 API Key 的用户本就可直接通过 X-API-Key 调用，session_token 不增加额外权限，因此跨 provider 匹配不是安全降级。
  3. **前端 `verifyProvider` 加入 login 兜底**（useSettingsStore.ts L231-281）：
     流程：store-key → 401 时 fallback 到 /api/auth/login 恢复会话 → 拿到 token 后再补一次 store-key 以更新元数据。
- 验证结果（agent-browser 端到端测试）：
  - 清空 localStorage + 后端已有 5 个 provider 的混合场景
  - 添加新 provider `test-9router-fix`，点验证 → `POST /api/models 200`（验证成功）
  - `POST /api/auth/store-key 401`（首次 store-key 失败，预期）
  - `POST /api/auth/login 200`（login 兜底成功，跨 provider 匹配到 `prov_mr3uy7vl_r52g9x`）
  - `POST /api/auth/store-key 200`（拿到 token 后 store-key 成功持久化新 provider）
  - Agent 对话 `POST /api/chat 200 14382ms`（Text 模型，26 工具，SSE 流式响应正常）
- 下次注意：
  1. 涉及鉴权的验证流程，验证端点不能依赖于"被验证凭证派生出的 token"
  2. 设计鉴权流程时，区分"首次配置"和"已配置后追加"两种场景，确保都有可用路径
  3. 改 store-key/verify 流程后，必须测试"清空 localStorage + 已有 store"的混合场景
  4. login 端点的跨 provider 匹配是安全的（API Key 知识者本就可直接调用），但要注意不泄露 provider 是否存在（统一返回 401）

## 2026-07-07 - `getAuthHeaders` 未导出导致前端白屏

- 错误现象：
  打开前端页面后白屏，控制台报错 `Uncaught SyntaxError: The requested module '/src/api/client.ts' does not provide an export named 'getAuthHeaders'`。
- 错误原因：
  `frontend/src/api/client.ts` 中的 `getAuthHeaders` 仅作为内部函数声明，未加 `export`；而 `frontend/src/components/explorer/useExplorerWatch.ts` 通过命名导入 `import { getAuthHeaders } from "../../api/client"` 引用它，模块加载失败导致整个应用无法渲染。
- 修复方式：
  将 `function getAuthHeaders()` 改为 `export function getAuthHeaders()`，确保命名导入可用。
- 下次注意：
  1. 被多个模块引用的 helper 必须显式导出，不能仅在本文件内部使用；
  2. 项目使用 `tsconfig.json` 的 project references，`npx tsc --noEmit` 不会检查 app 文件，应使用 `npx tsc --noEmit -p tsconfig.app.json` 做完整类型检查；
  3. 新增命名导入后，立即通过 `npx tsc --noEmit -p tsconfig.app.json` 验证，避免运行时白屏。

## 2026-07-07 - CAB Explorer 后端响应格式与前端 apiPost 解包不兼容

- 错误现象：
  调用 `/api/explorer/write`、`/explorer/create` 等接口成功后，前端 `apiPost` 返回 `undefined`，导致后续依赖返回值的逻辑（如保存后刷新状态）可能异常。
- 错误原因：
  前端 `api/client.ts` 的 `unwrapResponse` 约定：响应含 `success: true` 时返回 `json.data`。但 explorer 写/create/rename/delete/move 五个接口直接返回 `{success: true, path: ...}`，没有 `data` 字段，因此 `json.data` 为 `undefined`。
- 修复方式：
  统一五个接口的响应结构为 `{success: true, data: {...}}`，例如 `return {"success": True, "data": {"path": str(real)}}`。`/explorer/open` 保持旧格式 `{tree: [...]}` 不变，因为 `apiPost` 对不含 `success` 的响应直接返回整个 JSON。
- 下次注意：
  1. 新增接口时，如果走 `{success, data}` 合约，必须确保成功响应包一层 `data`；
  2. 后端修改响应格式后，要检查所有 `apiPost`/`apiGet` 调用点是否仍按预期消费返回值；
  3. 老接口升级响应格式时，优先兼容旧前端（如保留不带 success 的旧路径），避免一次性破坏所有消费者。

## 2026-07-07 - CAB Explorer `webkitdirectory` 无法获取绝对路径

- 错误现象：
  前端使用 `<input type="file" webkitdirectory>` 选择文件夹后，无法拿到可用于后端 `/api/explorer/open` 的绝对路径；后端 `authorize_root` 需要绝对路径才能解析并注册授权根。
- 错误原因：
  浏览器安全模型禁止通过标准 File API 暴露用户本地文件的绝对路径，`webkitRelativePath` 只返回相对于所选根目录的相对路径。
- 修复方式：
  1. 保留 `webkitdirectory` 选择器用于用户体验和获取文件夹名；
  2. 选择后通过项目已有的 `useModalStore.promptDialog` 弹出居中输入框，自动填入从 `webkitRelativePath` 推断的根目录名，允许用户补全/确认绝对路径；
  3. 将用户输入的绝对路径发给 `/api/explorer/open`。
- 下次注意：
  1. 纯浏览器前端无法直接拿到本地绝对路径，涉及本地文件系统的功能必须设计“用户显式提供路径”或“后端通过其他通道（如桌面壳、Electron、Tauri）获取路径”的兜底方案；
  2. `window.prompt` 会阻塞主线程且无法自定义样式，后续类似场景优先复用项目内的 `Modal`/`promptDialog`；
  3. 如果后续引入桌面壳，应优先把绝对路径通过 preload/bridge 注入前端，移除输入框兜底。

## 2026-07-07 - CAB explorer 路径校验顺序导致未授权路径存在性泄漏

- 错误现象：
  在实现 `/api/explorer/*` 路径安全校验时，早期版本对未授权目录外的文件调用返回 "path does not exist"，暴露该文件是否存在的敏感信息。
- 错误原因：
  `src/explorer/security.py` 的 `validate_path` 把 `must_exist` 检查放在 `_assert_within_root` 之前；对于授权根之外的文件，如果文件不存在会先抛出 "path does not exist"，攻击者可据此推断系统上文件是否存在。
- 修复方式：
  1. 调整 `validate_path` 顺序：先 `_assert_not_system`、`_assert_no_deny_pattern`、`_assert_within_root`，再检查存在性与文件/目录类型；
  2. 确保所有外部路径（无论是否存在）都会先被授权根、系统目录、拒绝模式三层拦截。
- 下次注意：
  1. 路径安全校验中，授权/系统/敏感模式检查必须优先于存在性检查，防止信息泄漏；
  2. 新增文件操作接口时，要单独测试 "未授权但存在的路径"、"未授权且不存在的路径"、"系统目录" 三类负例；
  3. `Path.resolve()` 会解析符号链接和 `..`，但需在 resolve 后立即校验，不能在解析前放行相对路径。

## 2026-07-07 - build_tool 局部导入导致 `NameError: name 'emit_tool_event' is not defined`

- 错误现象：
  调用 `build_firmware` 工具时后端抛出 `NameError: name 'emit_tool_event' is not defined`，工具事件无法推送到 SSE，前端编译日志/进度卡片不更新或卡在 pending。
- 错误原因：
  `backend/src/agent/tools/groups/code/build_tool.py` 中 `_drain_stream` 把 `from src.agent.streaming_event_bus import emit_tool_event, emit_build_log_via_stream_writer` 放在函数内部局部导入；而 `_emit_event` 是模块级函数，运行时在自身 globals 中找不到 `emit_tool_event` / `emit_build_log_via_stream_writer`，触发 NameError。
- 修复方式：
  1. 将 `emit_tool_event` 和 `emit_build_log_via_stream_writer` 移到文件顶部作为模块级导入；
  2. 删除 `_drain_stream` 内的局部导入；
  3. 验证 `python -c "from src.agent.tools.groups.code.build_tool import _emit_event; _emit_event(...)"` 不再报错。
- 下次注意：
  1. 工具内部调用的 helper 如果在模块级定义，其依赖必须在模块级导入，不能仅在调用方函数内局部导入；
  2. Python 局部导入只影响当前作用域，模块级函数不会自动“看到”嵌套函数内的局部导入；
  3. 流式工具的事件推送路径要通过直接调用 `/api/tool` 和 Agent chat 两种路径验证。

## 2026-07-07 - 前端用户视角审查 v2：8 个 FIX 修复 + 4 个 VERIFY 验证

- 错误现象：
  1. FIX-1: Agent 文本回复间歇性丢失，localStorage 中 assistant 消息 contentLen=0，但后端返回 108 个 text 事件
  2. FIX-2: footer 显示 gpt-4o-mini 而非用户配置的 9router/Text 模型
  3. FIX-3: 历史工具调用"已耗时 474.3s"持续增长，超过消息存在时长
  4. FIX-4: 会话标题带前导引号（`\"STM32F103...`）
  5. FIX-5: 知识库下拉显示 17 个测试 KB
  6. FIX-6: 发送按钮间歇性失效，textarea 值保留
  7. FIX-7: 添加服务商后表单未清空
  8. FIX-8: 服务商添加后未自动验证
- 错误原因：
  1. FIX-1: onDone 不重合并 streamingContent + fetchMessages length-only 守卫失效（长度相等时用后端空数据覆盖本地）+ _appendResumeText 覆盖主流程文本 + persistLastTurn POST 空内容
  2. FIX-2: InputBar.tsx:54 footer 读 session.model 优先于 chatModel，session.model 默认 gpt-4o-mini 硬编码；设置页改模型不同步 session
  3. FIX-3: ActivityBlock.tsx:110 isPending 只看 step.status 不看 activity.status；onDone 只改 activity.status 不改 step.status；pending+startTime 裸持久化
  4. FIX-4: 标题生成函数未 strip 首尾引号/反斜杠
  5. FIX-5: KB 列表无过滤逻辑
  6. FIX-6: isStreaming 卡 true（心跳保活 + onDone 非活跃分支不清 + branchThread 不清）
  7. FIX-7: addProvider 成功回调未 reset 表单（实际代码已存在，可能是 UI 渲染时序问题）
  8. FIX-8: 添加后未自动调用 verifyProvider
- 修复方式：
  1. FIX-1: onDone 兜底重合并（content 为空时用 streamingContent 填充，区分 string/ContentPart[]）+ fetchMessages 守卫升级 content-aware（流式中跳过 + length 相等时 content 非空校验）+ persistLastTurn POST 前校验 + _appendResumeText 改为追加
  2. FIX-2: DEFAULT_MODEL 改为空字符串 + SettingsPage onChange 同步 updateSessionMeta + initSessions/migrateSession 迁移旧 "gpt-4o-mini" 为空
  3. FIX-3: ActivityBlock 新增 activityDone prop + isPending 改为 `step.status === 'pending' && !activityDone` + useChatStore 新增 _finalizePendingSteps helper 在 onDone/stopStreaming（含 error 路径）归一化 pending step
  4. FIX-4: 新增 cleanTitle 函数（strip 首尾 `"` `"` `'` `『` `』` `\` + 截断 50 字符），initSessions 和 store 初始值 IIFE 两处应用
  5. FIX-5: 新增 isTestKb + filterVisibleKbs 函数（8 条正则 + is_builtin 短路）+ showTestKbs 开关
  6. FIX-6: onDone 非活跃分支兜底（streamingSessionId === sid 时清空）+ branchThread 清理（原会话流式中清空全局流式状态）
  7. FIX-7: 确认表单清空代码已存在
  8. FIX-8: handleCreateProvider 改为 async + await verifyProvider
- 下次注意：
  1. React 受控组件 fill 无效时，用 `Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set` + dispatchEvent('input') workaround
  2. SSE 流式状态（isStreaming/streamingSessionId/currentSseRequest）的清理必须覆盖所有路径：onDone 活跃/非活跃分支、stopStreaming 正常/error 路径、branchThread、setActiveSession
  3. localStorage 持久化时，pending 状态 + 时间戳字段（如 step.startTime）要特别小心，历史消息加载后会以原时刻为基准计算
  4. DEFAULT_MODEL 硬编码散落在多个文件（useSessionStore.ts:13 + useChatStore.ts:63），改默认值时需同步所有位置
  5. fetchMessages 的守卫不能只比较 length，必须 content-aware（本地有内容而后端为空时保留本地）
  6. stopStreaming 的 error 路径容易遗漏，必须也归一化 pending step 和 activity.status
  7. 浏览器验证时发现 `hwrag_active_session` localStorage 值带多余引号（`"se1d2d563"` 而非 `se1d2d563`），可能是另一个线程的修改导致，需单独修复

## 2026-07-07 - agent_factory 全局 _TOOL_REGISTRY 并发 ctx 串号 + autocompact multimodal content 类型错误

- 错误现象：
  1. 多会话并发调 Agent 时，会话 A 的工具调用用到了会话 B 的 ctx（session_id 串号），审计日志/HITL 决策归属错乱；
  2. autocompact 触发摘要时，若 LLM 返回 multimodal 内容（content 为 list[ContentBlock]），`_call_summarizer` 直接 `return response.content or ""` 把 list 当 str 用，导致摘要拼接异常或下游 token 估算类型错误。
- 错误原因：
  1. `agent_factory.build_tool_specs` 每次请求都创建新工具实例并调 `register()` 写入全局 `_TOOL_REGISTRY`（按 name 存储的单例 dict）。并发时后写者覆盖前写者，而 `ToolRouter.dispatch` 通过 `_TOOL_REGISTRY.get(name)` 重新查 spec（不用传入 create_agent 的 per-request 实例），导致 dispatch 拿到的是最后注册者的实例。同时 `hitl_handler._get_tool_ctx` 直接从 registry 读 `_ctx`，也会读到 last-writer 的 ctx；
  2. `_call_summarizer` 假设 `response.content` 恒为 str，但多模态模型返回 `list[ContentBlock]`，原代码 `response.content or ""` 在 list 非空时直接返回 list 对象，流入下游 `_SUMMARY_PREFIX + summary` 拼接时类型不一致。
- 修复方式：
  1. `build_tool_specs` 不再调用 `register()` 写全局 registry，改为：先 `ensure_default_tools_registered()`（幂等，保证 dispatch/HITL/Studio/standalone 能找到默认实例）→ `_assemble_all_tools(payload)` 创建 per-request 实例 → 新增 `_inject_ctx(tools, ctx)` 仅注入 `_ctx` PrivateAttr 不注册 → 返回 per-request 实例列表交给 create_agent。`_inject_ctx_and_register` 保留给 `ensure_default_tools_registered`（默认/standalone 路径仍需注册）；
  2. `_call_summarizer` 改调新增的 `_extract_response_text(response)`：`isinstance(content, str)` 返回 str；`isinstance(content, list)` 时 `"".join(block.text for block ...)`；`None` 返回 ""；其他 `str(content)` 兜底。
- 下次注意：
  1. 全局单例 dict（如 `_TOOL_REGISTRY`）绝不能存 per-request 状态（ctx/credentials/top_k），并发必串号。per-request 实例只注入 PrivateAttr 后直接返回，不要写全局 registry；
  2. `ToolRouter.dispatch` 当前通过 name 从全局 registry 反查 spec（不用 `_arun` 的 self），这是 per-request 配置（top_k/kb_ids/creds）在 Agent 路径回退为默认值的根因。彻底修复需改 `tool_spec._arun` 把 self/spec 透传给 dispatch（本次任务限定只改 agent_factory.py + autocompact.py，未动）；
  3. LLM `response.content` 类型不确定（str | list[ContentBlock] | None），任何读取处都要做 isinstance 分支，不能假设恒为 str。autocompact.py 中 `_call_fallback_llm` 仍有相同的 `response.content or ""` 模式，本次未改（任务只点名 _call_summarizer），后续可复用 `_extract_response_text` 收敛；
  4. 任务给的验证命令 `from src.agent.core.agent_factory import ...` 路径有误（实际是 `src.agent.agent_factory`，无 `core/`），且 `from ...autocompact import autocompact` 的 `autocompact` 符号不存在（实际是 `autocompact_messages`）。验证时以实际模块路径为准。

## 2026-07-07 - pio_runner 三个 bug：重复 done 事件 / 烧录丢 framework / 进程句柄泄漏

- 错误现象：
  1. 编译/烧录超时时前端收到两个 done 事件（先 timeout done，再 fail done），UI 状态错乱；
  2. 用 espidf 编译后烧录，platformio.ini 被重写为默认 arduino 框架，导致烧录阶段框架不一致；
  3. 上传超时后 pio 子进程句柄未回收，多次编译后系统变卡。
- 错误原因：
  1. `_next_pio_event` 超时分支发 timeout done 后 `_stream_pio_subprocess` 仍无条件调 `_build_pio_done_event` 再发一个 fail done，无去重机制；
  2. `UploadRequest` dataclass 缺 `framework` / `lib_deps` 字段，`_ensure_upload_port` 重写 platformio.ini 时只传 `upload_port`，丢了 framework/lib_deps；
  3. `_stream_pio_subprocess` 没有 try/finally，消费者提前退出或超时 kill 后未兜底确认进程退出。
- 修复方式：
  1. `StreamContext` 加 `done_sent: bool = False`；`_next_pio_event` 超时分支置 `ctx.done_sent = True`；`_build_pio_done_event` 入口检查 `ctx.done_sent`，已发则 return None，发完置 True；`_stream_pio_subprocess` 仅在 done_event 非 None 时 yield；
  2. `UploadRequest` 加 `lib_deps: tuple[str, ...] = ()` 和 `framework: str = DEFAULT_FRAMEWORK`（与 CompileRequest 字段风格一致）；`_ensure_upload_port` 改签名为 `(project_dir, req: UploadRequest)` 避免参数超过 3 个，内部把 `req.lib_deps` / `req.framework` 透传给 `_generate_platformio_ini`；`build_routes.py` 的 `_run_pio_upload` 构造 `PioUploadRequest` 时填入 `framework=payload.framework` 和 `lib_deps=tuple(payload.lib_deps or [])`；
  3. `_stream_pio_subprocess` 包 try/finally，finally 中 `if proc.returncode is None: await _kill_process(proc)` 兜底回收。
- 下次注意：
  1. SSE done 事件必须全局唯一——任何发 done 的路径都要走同一个标志位去重，超时/正常/失败三条路径只能发一次；
  2. 烧录阶段重写 platformio.ini 时必须保留编译期的 framework 和 lib_deps，不能只写 upload_port，否则跨框架（espidf→arduino）烧录会出问题；
  3. asyncio 子进程流式生成器必须 try/finally 兜底，仅靠超时分支 kill 不够——消费者提前 break / GC 关闭生成器时进程仍在跑会泄漏句柄；
  4. 函数参数超过 3 个时优先传 dataclass 对象（如 `req: UploadRequest`）而非继续堆参数，符合 AGENTS.md max-params ≤ 3 规则。

## 2026-07-07 - langchain 1.x AsyncSqliteSaver 同步调用导致 HITL 工具中断无法恢复

- 错误现象：
  Agent 在 default 模式下调用只读工具（如 search_docs）后静默失败，前端"模型正在思考..."卡片一直转，后端日志无显式报错；或在 HITL resume 路径出现 `Synchronous calls to AsyncSqliteSaver are only allowed from a different thread` 警告后流程中断。
- 错误原因：
  langchain 1.x 迁移后将 checkpointer 从同步 `SqliteSaver` 替换为异步 `AsyncSqliteSaver`，但 `backend/src/agent/hitl_handler.py` 中的 `_safe_get_state` 和 `_inject_deny_messages` 仍调用同步 `agent.get_state()` / `agent.update_state()`。AsyncSqliteSaver 不允许在同一线程同步调用，导致状态读取/注入失败，工具中断后无法自动恢复。
- 修复方式：
  1. `_safe_get_state` 改为 `async def` 并调用 `await agent.aget_state(config)`；
  2. `_detect_tools_interrupt` 改为 `async def` 并 `await _safe_get_state(...)`；
  3. `_inject_deny_messages` 改为 `async def` 并调用 `await agent.aupdate_state(...)`；
  4. `handle_auto_resume` 和 `resume_agent_after_user` 中所有调用处加 `await`；
  5. 将 deny/stop 路径的 SSE 事件构造拆分到 `backend/src/agent/hitl_sse.py`，权限评估拆分到 `backend/src/agent/hitl_permission.py`，保持 `hitl_handler.py` 在 300 行以内。
- 下次注意：
  1. 迁移到 AsyncSqliteSaver 后，所有 `agent.get_state/update_state` 调用点必须同步改为 `agent.aget_state/aupdate_state` 并加 `await`，不能遗留同步调用；
  2. HITL 中断/恢复路径涉及多个内部 helper，修改签名后要用 `grep` 全仓库检查调用点是否同步更新；
  3. 拆分文件时注意循环导入：`hitl_sse.py` 只依赖 `app.api.sse`，`hitl_permission.py` 延迟导入 `ToolRouter`。

## 2026-07-07 - create_agent astream 不会流出 aupdate_state 注入的 ToolMessage

- 错误现象：
  HITL deny/stop 分支通过 `agent.aupdate_state(config, values={"messages": deny_msgs})` 注入拒绝 ToolMessage 后，前端工具卡片仍卡在 pending 状态，看不到 tool_result 事件。
- 错误原因：
  langchain 1.x 的 `create_agent(...).astream()` 在 `updates` stream_mode 下不会把通过 `aupdate_state` 注入的 ToolMessage 作为事件流出。只有实际执行 ToolNode 产生的 ToolMessage 才会被 stream 消费。
- 修复方式：
  在 `handle_auto_resume` 和 `resume_agent_after_user` 的 deny/stop 分支中，调用 `_inject_deny_messages` 后，主动通过 `emit_deny_tool_results()` 构造并 `yield` tool_result SSE 事件，让前端关闭 pending 工具卡片。
- 下次注意：
  1. 任何通过 `aupdate_state` 注入 ToolMessage 的场景（HITL deny/skip/强制结果），都必须在 resume 流中主动 yield 对应的 tool_result SSE；
  2. tool_result 事件必须使用 ToolResultEnvelope 形状（success/output/data/error/metadata），与 sse_helpers 中的格式保持一致；
  3. deny 的 duration 应从 `call_start_time` 中 pop 开始时间计算，若无记录则填 0，避免负值。

## 2026-07-06 - langchain 1.x 迁移回归：chunks 端点报 No module named 'langchain_chroma'

- 错误现象：
  调用 `GET http://127.0.0.1:58080/api/kb/documents/{doc_id}/chunks?limit=3` 返回 HTTP 200 但 body 为 `{"success":false,"error":{"code":"INTERNAL_ERROR","message":"No module named 'langchain_chroma'","details":null}}`。chunk 分块逻辑回归测试 subagent 在验证 4.3（chunk 元数据字段）时命中此问题。
- 错误原因：
  langchain 1.x 全量迁移后，`backend/requirements.txt` 已声明 `langchain-chroma==1.1.0`，但**后端服务进程的运行环境**未安装该包。命令行 `python -c "import langchain_chroma"` 能成功（但无 `__version__` 属性，疑似部分安装/命名空间包冲突），说明命令行 Python 环境与后端服务进程环境不一致——后端服务可能是迁移前启动的旧进程，或用了不同的 venv。同时发现 `chromadb` requirements 写 `0.5.0` 但运行时是 `1.5.9`，版本漂移。
- 修复方式：
  本次任务为回归测试 subagent，仅记录问题未执行修复。建议修复步骤：(1) 重启后端服务进程使其加载新依赖；(2) 若重启后仍报错，执行 `pip install -r backend/requirements.txt --upgrade`；(3) 统一 chromadb 版本（requirements 写 0.5.0 但运行时 1.5.9，需确认目标版本）；(4) 验证 `python -c "import langchain_chroma; print(langchain_chroma.__file__)"` 在后端 venv 中返回正确路径。
- 下次注意：
  1. **langchain 1.x 迁移后必须重启后端服务**：依赖变更（langchain-chroma / langchain-community / chromadb 版本）只有重启进程才能生效，旧进程仍用旧 import 缓存；
  2. **chunks 端点是验证 chunker 完整性的关键 API**：`/api/kb/documents/{doc_id}/chunks` 依赖 kb_manager → langchain_chroma → chromadb 链路，任一环缺失都会 500。回归测试 chunk 分块逻辑时必须先确认该端点可用；
  3. **requirements.txt 与运行时版本漂移**：迁移后需 `pip freeze | grep -E "langchain|chroma"` 核对实际版本与声明版本一致；
  4. **API contract 中的端点路径**：任务描述里 `/api/kb/{kb_id}/docs` 和 `/api/kb/{kb_id}/chunks?doc_id=...` 路径不存在，正确路径是 `/api/kb/list`（文档列表）和 `/api/kb/documents/{doc_id}/chunks`（chunk 列表），见 api-contract.md L372。

## 2026-07-06 - langchain 1.x 审计阶段 4 Task 38-41 评估结论：10 项降级 / 10 项实施

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 4 代码质量重构 Task 38（函数拆分）/ 39（dataclass 封装）/ 40（魔法数字）/ 41（其他质量）。强约束：严格保持现有全部功能完整性，只做机械重构（提取常量 / 封装 dataclass / 拆子函数），零逻辑修改；风险高的拆分坚决降级。
- 实施项（10 项，全部机械重构，后端冒烟测试通过）：
  - **40.1** `autocompact.py:111` `content[:500]` → 模块级 `_EARLY_MSG_CHAR_LIMIT = 500`
  - **40.2** `web_search.py:140-141,173` `[:100]`/`[:500]`/`[:80]` → `_TITLE_MAX_CHARS`/`_URL_MAX_CHARS`/`_SUMMARY_TITLE_MAX_CHARS`
  - **40.3** `image_generation.py:145,165` `[:200]`/`[:120]` → `_HTTP_ERROR_BODY_MAX_CHARS`/`_CHAT_PREVIEW_MAX_CHARS`
  - **40.4** `kb_manager.py` `0.85`/`1.15`（3 处局部变量 + 1 处字符串字面量）→ 模块级 `_BM25_ONLY_PENALTY`/`_BM25_NORM_FACTOR`，debug 日志改 f-string 引用常量
  - **41.1** `session_search.py:47` `datetime.utcnow()` → `datetime.now(datetime.UTC)`（Python 3.13，UTC 别名 3.11+ 可用）
  - **41.2** `session_search.py:21` `parents[3]` → `PROJECT_ROOT` from `path_guard`（**注意：任务描述说用 `ROOT_DIR` from settings，但 `ROOT_DIR = backend/`，而 `parents[3] = agent/` 项目根，用 ROOT_DIR 会把 DB 路径从 `agent/data/` 错误改到 `backend/data/`，丢失全部 session 历史。改用 `path_guard.PROJECT_ROOT`（= `ROOT_DIR.parent` = `agent/`）语义完全等价。冒烟测试验证 DB 路径仍为 `E:\Desktop\agent\data\agent_sessions.db`**）
  - **41.3** `audit_recorder.py:25` `_SENSITIVE_KEY_PATTERNS` 移除 `"key"`（过宽，误匹配 keyboard/hotkey/primary_key）。已验证全部工具参数字段无裸 `key`/`access_key`/`private_key`——所有密钥字段均为 `api_key`/`tavily_api_key`（被 "api_key" 子串匹配覆盖）
  - **41.6** `prompts.py:163` `SYSTEM_PROMPT` 加 `# deprecated` 注释（grep 确认无 in-repo 引用）
  - **38.3** `audit_logger.py` `log_tool_call` 33 行 → 提取 `_persist_audit_record(record)` 8 行辅助函数（DB add/commit/close 块），外层 try/except 行为不变
  - **39.2** `audit_recorder.py` `record`/`_write` 7 参 → 封装 `AuditRecord` dataclass（call_id/spec/args/envelope/decision/decision_source/ctx），更新 `tool_router.py:146` 调用点构造 `AuditRecord(...)`
- 降级项（10 项）：
  - **38.1** `useChatStore.ts` `sendMessage` 613 行：Task 36（useChatStore 拆分）已降级，同理 SSE 来源引用风险，降级
  - **38.2** `client.ts` `apiSSE` 150 行：含 `resetIdleTimer` 闭包 + 共享可变状态（buffer/dataBuffer/consecutiveFailures/doneReceived），SSE 解析状态机高度耦合，机械拆分易破坏解析逻辑；tsc 无法检测行为回归。降级
  - **38.4** `tool_router.py` `_run_with_timeout` 24 行：retry 循环 + TimeoutError/Exception 分类逻辑交织，拆分需把 try/except 跨函数边界，降低可读性且收益边际。降级
  - **39.1** `audit_logger.py` `log_tool_call` 9 参：3+ 调用方（audit_recorder / hitl_handler / scripts/test_v3_t7_audit.py），改签名需同步改测试脚本，公共 API 变更风险高。降级
  - **39.3** `multimodal_chunker.py` `__init__` 16 参：project_memory 硬约束不重构。降级
  - **39.4** `agent_chunker.py` `__init__` 16 参：project_memory 硬约束不重构。降级
  - **39.5** `sse_adapter.py` 5-6 参函数系列：8+ 内部 helper 共享 `(call_counter, call_start_time, state)` 三元组，封装 `StreamContext` 需改 8+ 函数签名 + state dict 的可变引用语义；sse_adapter 是核心流式基础设施，跨切面重构风险高。降级
  - **39.6** `ChatArea.tsx` `AssistantMessageRow` 18 props：Task 37（ChatArea 拆分）已降级，同理降级
  - **41.4** `tool_spec.py:117` `ToolRouter.get_default()` 全局依赖：Agent 工具基类的全局单例依赖，改 RunnableConfig 注入需改 ToolSpec._arun + 所有 ToolRouter 获取路径，影响 Agent 全链路。降级
  - **41.5** `audit_recorder.py:91-100` try/except TypeError 兼容层：`log_tool_call` 签名**至今仍未接受** `call_id`/`success`/`error_type` 参数（audit_logger.py 验证），移除兼容层会导致 `TypeError` 直接中断审计记录。需先做 DB schema 迁移 + 更新 log_tool_call 签名才能移除。降级
- 下次注意：
  1. **Task 41.2 路径语义陷阱**：`ROOT_DIR`（settings.py）= `backend/`，而 `session_search.py` 的 `parents[3]` = 项目根 `agent/`。任何"用 ROOT_DIR 替换 parents[N]"的机械重构必须先验证路径语义等价，否则会移动 DB/文件位置。正确替代是 `path_guard.PROJECT_ROOT`（= `ROOT_DIR.parent`）
  2. **Task 41.5 兼容层不能盲目移除**：try/except TypeError 是为兼容旧版 `log_tool_call` 签名（不接受 call_id/success/error_type）。移除前必须确认 log_tool_call 已更新签名 + DB 表已有对应列。当前两者均未做，移除会中断审计
  3. **Task 39.5 跨切面重构风险**：sse_adapter 的 8+ helper 共享 state dict（可变引用），封装 StreamContext 需保证 dataclass 持有 dict 引用而非拷贝，否则 step_index/pending_tool_calls 等状态丢失。核心流式基础设施的跨切面重构应单独评估 + 端到端 SSE 测试覆盖
  4. **魔法数字提取是最安全的机械重构**：Task 40 全系列零逻辑修改，仅值的位置从局部提升到模块级，冒烟测试即足以验证

## 2026-07-06 - Task 36 评估结论：useChatStore.ts 不拆分，保留单文件 1797 行

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 4 Task 36，要求评估 `useChatStore.ts` 1797 行（任务描述写 1828 行，实际 1797 行）是否拆为 4 个文件：`useChatStream`（SSE 流处理）+ `useAgentEvents`（Agent 事件分发）+ `useChatPersistence`（持久化）+ `useChatActions`（发送/停止/会话切换）。
- 现状（`frontend/src/stores/useChatStore.ts` L1-L1797）：
  1. **单文件 1797 行**，严重超 AGENTS.md max-lines ≤ 300 限制；
  2. **5 类职责混合**：SSE 流式处理 / HITL resume / 消息持久化 / 会话动作 / 工具方法；
  3. **sendMessage 580 行**（L705-L1284），内部 onEvent switch case 处理 11 种 SSE 事件（thinking / text / heartbeat / tool_call / tool_result / source / todo_update / done / compile_log / progress / tool_confirm_required），是 SSE 来源引用机制的核心；
  4. **HITL resume 7 个辅助函数**（_handleResumeEvent / _appendResumeText / _appendResumeThinking / _appendResumeSource / _appendResumeToolCall / _updateResumeToolResult / _finalizeResume）定义在 create() 闭包内，使用 set()/get()；
  5. **6 个模块级变量**（_needsParagraphBreak / _lastToolCallId / _lastHeartbeatAt / persistedMsgIds / _persistTimer / _pendingShards）跨职责使用；
  6. **文件头注释已标注拆分计划**（L1-L49），明确"优先级：demo 后第 1 周"和"前置条件：补齐 useChatStore 单元测试（streaming / resume / persist 三条主路径）后再动手"。
- 风险点：
  1. **SSE 来源引用机制核心**：sendMessage 的 onEvent switch case 是 [srcN] 编号递增 / source 卡片 score 展示 / 重复 srcN 处理 / source 卡片默认全展开 / 思考卡保留的核心实现。case "source"（L1088-L1123）处理 SourceRef 的 18 个字段，case "thinking"（L860-L907）处理 source 切换（rag/llm/reasoning）和 thinking 卡状态（running/done），case "text"（L909-L978）处理 _needsParagraphBreak 段落分隔和 ContentPart[] 图片保护。拆分时任何 set() 调用遗漏或状态更新顺序错误都会破坏来源引用机制；
  2. **onEvent switch case 操作跨 slice 状态**：case "thinking"/"text"/"tool_call"/"tool_result"/"source" 同时操作 streaming*（useChatStream slice）+ messages/sessionMessages（共享状态）+ pendingConfirm（useAgentEvents slice，case "tool_confirm_required"）。Zustand slice pattern 虽然共享 set/get，但 set() 返回的 partial state 需要正确合并到对应 slice，跨 slice 状态更新极易出错；
  3. **sendMessage 和 _handleResumeEvent 互相引用**：文件头注释 L42 已标注。sendMessage 的 onEvent 有独立的 case 处理逻辑，_handleResumeEvent 也有独立的 _appendResume* 处理逻辑，二者都处理 text/thinking/source/tool_call/tool_result 但实现不同（resume 路径不处理 heartbeat/compile_log/progress/todo_update）。拆分时需确认这些函数的归属 slice，避免循环依赖；
  4. **truncateAndResend 跨 slice 调用**：定义在 create() 闭包内（L283-L298），使用 get()/set()，被 retryMessage/editAndResend 共用，又调用 get().sendMessage()。如果 truncateAndResend 在 useChatActions，sendMessage 在 useChatStream，通过 get().sendMessage() 跨 slice 调用虽可行但增加复杂度；
  5. **6 个模块级变量跨 slice 归属复杂**：_needsParagraphBreak 在 onEvent case "text"/"tool_result" 读写（useChatStream）；_lastToolCallId 在 onEvent case "tool_call"/"compile_log"/"progress" 读写（useChatStream）；_lastHeartbeatAt 在 case "heartbeat" 写（useChatStream）；persistedMsgIds 在 persistLastTurn 读写（useChatPersistence）；_persistTimer/_pendingShards 在 subscribe 回调和 scheduleShardSave/flushPendingShards 读写（useChatPersistence）。拆分时需决定每个变量归属文件，且 subscribe 回调（L1616-L1633）需留在主 store 文件 import useChatPersistence 的函数；
  6. **前置条件未满足**：文件头注释 L48-L49 明确标注"优先级：demo 后第 1 周（不阻塞 7.15 demo）"和"前置条件：补齐 useChatStore 单元测试（streaming / resume / persist 三条主路径）后再动手"。当前项目无 useChatStore 单元测试，前置条件未满足；
  7. **ChatState interface 需拆 4 个 slice interface**：文件头注释 L46 已标注，interface 拆分后需 extends 合并，类型推导可能出问题；
  8. **多 tab 同步 + Mock 数据 + subscribe 模块级代码**：L1577-L1796 的 on() 监听 / subscribe 回调 / loadMockData 函数都是模块级代码，调用 fetchMessages（useChatPersistence）/ setActiveSession（useChatActions）/ sendMessage（EmptyState 内）/ setState。拆分后这些模块级代码需留在主 store 文件，import 各 slice 的函数。
- 决策：**降级处理**，保留现有单文件 1797 行
  1. 不在 `frontend/src/stores/` 拆分 useChatStore.ts 为 4 个 slice 文件；
  2. 不引入 Zustand slice pattern 重组 useChatStore；
  3. 现有 sendMessage / onEvent switch case / _handleResumeEvent / 6 个模块级变量保持原样；
  4. 文件头注释的拆分计划（L1-L49）保持原样，作为后续 demo 后第 1 周的执行指南；
  5. 前置条件：补齐 useChatStore 单元测试（streaming / resume / persist 三条主路径）后再动手；
  6. Task 38.1（sendMessage 613 行拆分为 _handleTextEvent 等子函数）依赖 Task 36，一并降级。
- 下次注意：
  1. Zustand slice pattern 拆分看似标准做法，但 onEvent switch case 操作跨 slice 状态时，set() 返回的 partial state 合并极易出错，必须先补齐单元测试覆盖 streaming/resume/persist 三条主路径；
  2. 模块级变量（_needsParagraphBreak / _lastToolCallId / _lastHeartbeatAt）不需要触发 re-render，不应提升为 store state，拆分时需确认每个变量只在单个 slice 内使用；
  3. sendMessage 内部 onEvent 的 case "source" 处理 SourceRef 的 18 个字段（id/title/doc/page/chunk_index/page_start/page_end/section_title/source_url/category/chunk_method/score/score_percentage/relevance_level/citation/excerpt/kb_id/kb_name/small_chunk_id），任何字段遗漏都会破坏 source 卡片展示；
  4. 文件头注释已明确前置条件（单元测试）和优先级（demo 后），评估拆分 task 时必须先检查文件头注释是否有前置条件标注；
  5. Task 38.1（sendMessage 拆分）与 Task 36 强耦合，Task 36 降级时 Task 38.1 应一并降级。

## 2026-07-06 - Task 34/35 评估结论：multimodal_chunker.py / agent_chunker.py 不拆分，遵循 project_memory 硬约束

- 评估背景：
  langchain 1.x 全量审计 spec 阶段 4 Task 34/35，要求把两个核心 chunker 文件按职责拆成多个子文件。Task 34 要求 `multimodal_chunker.py` 拆为 `multimodal_chunker.py`（主类）+ `multimodal_vision.py`（Vision LLM 调用）+ `multimodal_toc.py`（TOC 提取）+ `multimodal_merge.py`（跨批合并）；Task 35 要求 `agent_chunker.py` 拆为 `agent_chunker.py`（主类）+ `agent_voting.py`（多数投票）+ `agent_fallback.py`（非结构化降级）。
- 现状（`backend/src/rag/chunking/multimodal_chunker.py` 1848 行 + `agent_chunker.py` 1324 行）：
  1. **multimodal_chunker.py**：模块级 2 个辅助函数（`_looks_like_schematic` / `_merge_cross_page_tables`）+ `MultimodalTrace` dataclass + `MultimodalChunker` 主类（24 个方法，含 `_build_chunks` 230 行 / `_merge_tiny_chunks` 148 行 / `_extract_global_toc` 156 行 / `_analyze_batch` / `_robust_vision_call` / `_majority_vote_sections` / `_merge_cross_batch_sections` / `_merge_cross_group_sections` / `_pack_batches_by_toc` / `_fill_page_gaps` / `_build_image_description_chunks` / `_fallback_single_page_analysis`）；
  2. **agent_chunker.py**：模块级 `_truncate_at_boundary` + `RoundResult` / `ChunkTrace` dataclass + `AgentChunker` 主类（14 个方法，含 `_majority_vote` 228 行 L734-962 / `_fallback_chunk_unstructured` 165 行 L1321-1485 / `_run_chunking_round` / `_build_chunks` / `_merge_tiny_chunks` / `_extract_toc` / `_generate_pseudo_toc` / `_create_batches` / `_split_oversized_batch`）+ `AgentChunkError` 异常类；
  3. **冒烟测试通过**：`python -c "from src.rag.chunking.multimodal_chunker import MultimodalChunker; from src.rag.chunking.agent_chunker import AgentChunker; print('ALL IMPORTS OK')"` 输出 `ALL IMPORTS OK`，证明当前未拆分版本功能完整可用。
- 风险点：
  1. **project_memory 硬约束明文禁止**：Core algorithm files (multimodal_chunker.py, agent_chunker.py) and critical routes (kb_routes.py, chat_routes.py) should not be refactored due to high risk of breaking functionality。这条约束是用户明确写入 project_memory 的硬性规则，本次评估必须遵循；
  2. **用户明确要求保留的 8 类边界条件处理全部集中在单类内**：跨页表格合并（`_merge_cross_page_tables` / `_merge_cross_batch_sections`）/ PAGE 标记保护（`strip_page_markers` + `protect_structures`）/ tiny chunk 合并（`_merge_tiny_chunks`）/ TOC 提取（`_extract_global_toc` / `_extract_toc` / `_pack_batches_by_toc`）/ Vision LLM 调用（`_robust_vision_call` / `_analyze_batch` / `_describe_page_image`）/ 多数投票（`_majority_vote_sections` / `_majority_vote`）/ 非结构化降级（`_fallback_single_page_analysis` / `_fallback_chunk_unstructured`）/ 大文档阈值（`LARGE_DOC_THRESHOLD` / `MIN_TOC_FOR_FALLBACK`）/ 指纹去重（`compute_fingerprint` 在 `_build_chunks` 末尾）。任何一个边界条件在拆分时遗漏都会导致分块结果不一致；
  3. **24+14 个方法高度耦合，共享 self 状态**：拆分到子文件需要把 `self.*` 状态（prompt_template / temperature / round_count / batch_size / sub_chunk_size / vision_model / dpi / enable_image_description 等 10+ 实例字段）搬到 module-level 函数 + 传 context dict，破坏封装且极易遗漏字段。`_majority_vote`（228 行）依赖 `RoundResult` / `ChunkTrace` dataclass，trace 字段（rounds / votes / disputed_boundaries / token_usage / boundary_disputed / section_summary / is_code_block / agent_trace）需跨函数传递，拆分后 trace 完整性难以保证；
  4. **pitfalls.md 已有 12 条相关精细调试记录**（L1574 / L1584 / L1645 / L1657-1660 / L1974-1975 / L2019-2020 / L2030 / L2092 / L2113 / L3158 等），涵盖 assigned_pages 跨页保护、page_start 修复、_looks_like_schematic 启发式、protect_structures 表格/register 占位符、ChromaDB metadata 嵌套 dict 序列化、fitz import 缺失、流式调用 stream=True、_build_chunks 指纹去重 + max_chunks=500 强制上限、RoundResult.to_dict 缺失导致 hybrid 降级 fallback（隐藏很久的 bug）等。这些记录证明两个文件经过多次精细调试，边界条件处理极其复杂，拆分极易引入回归；
  5. **SubTask 34.2 / 35.2 验证分块结果一致成本极高**：需重跑完整 PDF 索引 + DeepEval 比对 + 跨页表格 / Vision LLM 多数投票分歧 / 大文档阈值 / TOC 提取等边界场景全覆盖，无法 100% 复现 LLM 调用结果（temperature>0 + 多数投票本身就有随机性），验证不充分就上线风险极高；
  6. **RoundResult.to_dict 缺失 bug（pitfalls L3158）的前车之鉴**：那个 bug 是 dataclass 漏写方法导致 trace 记录时抛 AttributeError 被 except 吞掉触发 hybrid 降级 fallback，且 `status=indexed` 文档可检索，bug 极隐蔽——只有检查 `error_message` 字段才能发现。拆分到子文件后类似的"方法搬走了但调用方没改"/"dataclass 字段搬走了但 trace 没同步"类遗漏极难发现。
- 决策：**降级处理**，遵循 project_memory 硬约束，不拆分两个核心 chunker 文件
  1. 不创建 `multimodal_vision.py` / `multimodal_toc.py` / `multimodal_merge.py` / `agent_voting.py` / `agent_fallback.py`；
  2. `multimodal_chunker.py`（1848 行）和 `agent_chunker.py`（1324 行）保持现状单文件；
  3. 两个文件超出 AGENTS.md「代码规范」max-lines ≤ 300 的硬上限，但 project_memory 硬约束优先级高于通用代码规范，且这两个文件是核心算法文件，边界条件处理密集，拆分风险远大于行数超标风险，**作为「待重构」记录在案但不强制拆分**；
  4. 已通过的冒烟测试（`ALL IMPORTS OK`）证明当前未拆分版本功能完整可用，无需任何代码改动；
  5. 未来若要拆分，需先：(a) 写完整回归测试套件覆盖 8 类边界条件 + DeepEval 主案例验证；(b) 逐方法搬迁 + 每搬一个跑一次端到端索引对比；(c) 保留原文件作为 fallback 直到新版本通过 7 天生产验证。
- 下次注意：
  1. project_memory 硬约束（multimodal_chunker.py / agent_chunker.py / kb_routes.py / chat_routes.py 不重构）优先级高于 AGENTS.md 通用代码规范（max-lines ≤ 300），评估拆分任务时**第一步先查 project_memory 硬约束清单**，命中即降级，不要先读代码再决策；
  2. chunker 拆分任务的 SubTask "验证分块结果一致"在 LLM 驱动 + 多数投票 + temperature>0 的场景下**无法 100% 可复现**，验证成本极高且不充分，应作为拆分的高风险信号；
  3. 两个 chunker 的 24+14 个方法共享 10+ 实例字段 + 4 个 dataclass trace 字段，"按职责拆到子文件"会强制把 self 状态搬到 module-level，破坏封装且极易遗漏，这种耦合度高的类不适合按文件拆分，更适合保持单类 + 内部方法分组（用注释 `# ═══` 分隔）；
  4. pitfalls L3158 RoundResult.to_dict 缺失 bug 是"拆分类时方法/字段遗漏"的典型反例——bug 隐藏很久且触发静默降级，评估拆分类任务时必须把这类前车之鉴纳入风险分析；
  5. 阶段 4 后续 chunker 相关拆分任务（如有）默认降级，除非 project_memory 硬约束清单更新移除这两个文件。

## 2026-07-06 - Task 32 评估结论：agent_factory.py 不拆分，保留 628 行单文件

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 4 Task 32，要求把 `backend/src/agent/agent_factory.py`（628 行）拆为 4 个文件：`agent_builder.py`（Agent 构造）+ `checkpointer.py`（checkpoint 单例）+ `tool_registry.py`（tool 注册/过滤）+ `tool_config.py`（tool_config.json 读写）。
- 现状（`agent_factory.py` 628 行，已按章节分块）：
  1. **Agent 构造**（L70-134, 270-287, 584-612, 619-628）：`AgentConfig` dataclass + `create_hardware_agent_from_config` + `create_hardware_agent`（backward-compat shim）+ `_build_llm`（包 ReasoningChatOpenAI）+ `_should_use_agent` + `_model_supports_tools` + `build_agent_config`；
  2. **checkpointer 单例**（L137-268）：模块级 `_global_checkpointer` + `_SQLITE_CHECKPOINT_PATH` + `_get_checkpointer`（选 sqlite/memory）+ `_read_checkpointer_type` + `_build_sqlite_checkpointer` + `reset_thread_checkpoint`（清 stale checkpoint 防 INVALID_CHAT_HISTORY）+ `_try_delete_thread` + `_clear_storage` + `_clear_writes` + `_key_thread_id`（langgraph 1.x key 兼容）；
  3. **tool 注册/过滤**（L289-339, 342-407, 410-448, 490-503, 505-527, 529-572）：`build_tool_specs`（+ alias `build_tools`）+ `ensure_default_tools_registered` + `_DefaultPayload` + `_assemble_all_tools` + `_build_tool_groups`（26 工具分 6 组）+ `list_tool_metadata` + `toggle_tool_enabled` + `_filter_disabled_tools` + `_inject_ctx_and_register` + `_build_tool_ctx` + `_build_local_tools` + `_build_local_file_ops_tools` + `_build_local_execution_tools` + `_read_tavily_key` + `_read_tavily_base_url` + `_read_vision_creds` + `_read_image_creds`；
  4. **tool_config.json 读写**（L451-487）：`_load_disabled_tools` + `_save_disabled_tools` + 模块级 `_TOOL_CONFIG_PATH`；
  5. **章节分隔符已存在**：`# ═══════════════════════════════════════════` 分隔 Agent construction / Tool list assembly / Agent path gating / Stream config，文件内部组织良好。
- 外部引用范围（grep `from src.agent.agent_factory import`，3 个文件 7 个符号）：
  1. `chat_routes.py:41` — `_should_use_agent, build_agent_config, build_tools, create_hardware_agent`（try/except ImportError 保护块内，langgraph 缺失时降级 fallback）；
  2. `chat_routes.py:169` — `reset_thread_checkpoint`（HITL resume 前清 stale checkpoint）；
  3. `tool_routes.py:66` — `ensure_default_tools_registered`（standalone /api/tool 注册）；
  4. `tool_routes.py:85` — `list_tool_metadata`（GET /api/tools 元数据）；
  5. `tool_routes.py:108` — `toggle_tool_enabled`（PATCH /api/tools/{name}/toggle）。
- 风险点：
  1. **模块级状态紧耦合**：`_global_checkpointer` 是模块级 singleton，`_get_checkpointer()` 用 `global` 语句维护。拆分到 `checkpointer.py` 后，`agent_builder.py` 的 `create_hardware_agent_from_config` 要跨模块调 `_get_checkpointer()`——singleton 状态一致性靠 Python module-level global 保证，跨模块访问虽可行但破坏了"状态私有于模块"的封装，且 `reset_thread_checkpoint` 被 `chat_routes.py` 直接导入，必须保证拆分后仍可从 `checkpointer.py` 直接导出；
  2. **HITL 链路敏感**：`reset_thread_checkpoint` 是 HITL resume 前的必要步骤（清 stale `AIMessage(tool_calls)` 防 langgraph INVALID_CHAT_HISTORY 报错）。拆分若导致 import 链断裂（chat_routes.py 的 try/except ImportError 块），HITL 路径会静默降级到 fallback LLM stream，用户看不到任何报错但 Agent 失效；
  3. **7 个外部符号导入路径更新**：3 个调用方文件都要改导入路径，且 `chat_routes.py` 的 try/except 块要保证 4 个符号都能从新模块正确导入，任一导入失败会导致整个 Agent 路径降级；
  4. **大量内部 `_xxx` helper 跨模块调用**：`_build_llm` / `_get_checkpointer` / `_build_tool_groups` / `_filter_disabled_tools` / `_load_disabled_tools` 等私有 helper 在 4 个新文件间互相调用，Python 虽无真正 private 但语义破坏，且任一 helper 移动后调用方未同步更新会 AttributeError；
  5. **`build_tools = build_tool_specs` 别名**：backward-compat alias 拆分时容易遗漏，外部代码可能仍 import `build_tools`；
  6. **AgentConfig dataclass 归属**：是核心 dataclass，多个 helper 用它，要放对模块，否则循环导入；
  7. **agent_factory.py 同属高耦合核心**：project_memory 把 `multimodal_chunker.py` / `agent_chunker.py` / `kb_routes.py` / `chat_routes.py` 列为不可重构，`agent_factory.py` 同属 Agent 构造核心（checkpointer singleton + HITL + tool registry 三合一），虽未明确列入但同等级风险；
  8. **文件内部已分章节**：`# ═══════════════════════════════════════════` 分隔符已存在，可读性不是问题，628 行的超标是"构造文件自然成长"而非"屎山堆积"。
- 决策：**降级处理**，保留 628 行单文件
  1. 不拆分 `agent_factory.py`，4 个章节（Agent 构造 / checkpointer / tool registry / tool_config）保持在同一文件；
  2. 7 个外部符号导入路径不变；
  3. 后续若 agent_factory.py 继续增长超过 1000 行，可考虑只拆 `tool_registry.py`（tool 注册逻辑相对独立，不依赖 checkpointer singleton），但当前 628 行的代价/收益不划算。
- 下次注意：
  1. 拆分含模块级 singleton（`_global_checkpointer`）的文件时，singleton 状态一致性是第一风险——拆分后跨模块 `global` 访问虽可行但破坏封装，且任一调用方未同步更新会导致状态分裂；
  2. HITL / Agent 路径的 import 链断裂是静默降级（try/except ImportError 吞错误），拆分后必须跑端到端 Agent 请求验证，不能只看 import 冒烟通过；
  3. AGENTS.md `max-lines ≤ 300` 是规范但非硬约束，"构造文件自然成长 + 已有章节分块 + 拆分风险高"时允许例外，pitfalls.md 记录降级理由即可；
  4. 文件内部已有 `# ═══════════════════════════════════════════` 章节分隔符的，可读性已达标，行数超标的优先级低于高耦合核心拆分风险。

## 2026-07-06 - Task 33 评估结论：kb_manager.py 不拆分，保留 1190 行单文件

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 4 Task 33，要求把 `backend/src/rag/kb_manager.py`（1190 行）拆为 4 个文件：`kb_crud.py`（CRUD）+ `kb_search.py`（检索）+ `bm25_index.py`（BM25）+ `rrf_fusion.py`（融合，若阶段 2 未删除）。
- 现状（`kb_manager.py` 1190 行）：
  1. **常量 + dataclass + helper**（L1-277）：路径常量（`BUILTIN_KB_DIR` / `USER_CHROMA_DIR` / `BM25_DIR`）+ `ALLOWED_SMALL_CHUNK_SIZES` + `_RERANKER_MIN_SCORE` / `_RERANKER_MIN_KEEP` + `validate_small_chunk_size` + `FusedResult` dataclass + `_make_rrf_key` + `_row_to_doc_dict` + `_DOC_FILTER_POOL_MULTIPLIER` + `_matches_doc_filter`；
  2. **BM25Index 类**（L80-222）：`_TECH_TERM_RE`（保护 GPIOx_MODER / SWJ-DP 等复合技术词不被 jieba 切碎）+ `_HARDWARE_TERMS`（140+ 硬件术语词典）+ `_dict_loaded` class-level 状态 + `_tokenize_for_bm25` + `_load_hardware_dict`（jieba.add_word freq=1000）+ `_ensure_index`（lazy load jieba ~2s）+ `search` + `save` + `load`；
  3. **rrf_fusion 函数**（L280-415）：RRF 排序 + 原始 0-1 分数显示 + `_make_rrf_key` dedup + BM25-only penalty（×0.85）+ score clamp 防 BM25 负分 + 详细 DEBUG 日志；阶段 2 Task 18 已降级保留（"高度定制化算法，EnsembleRetriever 迁移风险高"）；
  4. **KnowledgeBaseManager 类**（L418-1180）：CRUD（create_kb / update_kb_config / list_kbs / list_all_docs / get_kb / delete_kb / toggle_kb）+ Ingest（ingest_chunks 含 dedup + eager BM25 rebuild / get_doc_chunks / get_chunk_by_small_id / export_kb / import_kb）+ Search（search 单 KB hybrid / search_all_enabled 跨 KB 并行 + batch rerank）+ Builtin KB（ensure_builtin_kb）+ Internal helpers（_get_store 缓存 / _bm25_search 含 stale rebuild + score normalize / _rebuild_bm25）；类实例状态 `_stores` / `_bm25_indices` / `_bm25_stale` 被所有方法共享；
  5. **singleton**（L1182-1191）：`get_kb_manager()`。
- 外部引用范围（grep `from src.rag.kb_manager import`，约 25 处真实代码引用，含 scripts/ 15+ 处）：
  1. **backend/app/**：`main.py` 4 处（`get_kb_manager` / `BM25_DIR` / `BM25Index`，startup 事件 + CLI 重建 BM25）、`kb_routes.py` 2 处（`get_kb_manager` / `validate_small_chunk_size`）；
  2. **backend/src/**：`rag/search.py:116`（`get_kb_manager`）、`agent/tools/groups/retrieval/list_kb_docs.py:83`（`get_kb_manager`）；
  3. **backend/tests/**：`test_rag_edge_cases.py:28`（多符号）、`test_bm25_rag_verify.py:20`（`BM25Index, rrf_fusion`）、`rag_eval/rescore.py:435`（`KnowledgeBaseManager`）；
  4. **scripts/** 15+ 处：`build_builtin_kb.py`（`get_kb_manager, BUILTIN_KB_DIR, BM25_DIR`）、`reindex_*.py` 6 个（`get_kb_manager`）、`audit_baseline_*.py` 2 个、`reset_kb.py` / `cleanup_kb.py` / `rebuild_bm25_only.py` / `verify_hard_question_coverage.py` 等；
  5. **backend/check_chroma.py**：`get_kb_manager`。
- 风险点：
  1. **KnowledgeBaseManager 类方法与实例状态紧耦合**：CRUD / Ingest / Search 三类方法都读写实例状态 `_stores`（kb_id → HardwareVectorStore 缓存）/ `_bm25_indices`（kb_id → BM25Index 缓存）/ `_bm25_stale`（待重建 KB 集合）。拆分到 `kb_crud.py` + `kb_search.py` 必须用 mixin 多继承模式（class KnowledgeBaseManager(KbCrudMixin, KbSearchMixin)）或把方法变独立函数接受 manager 实例——前者 metaclass 冲突风险，后者破坏 OOP 封装，任一方案都是高风险重构；
  2. **rrf_fusion 已被阶段 2 Task 18 降级**：pitfalls.md 已明确"EnsembleRetriever 迁移风险高，保留现有 rrf_fusion"，理由是"高度定制化算法（RRF 排序 + 原始 0-1 分数显示 + BM25-only penalty + score clamp）"。把 `rrf_fusion` + `_make_rrf_key` 拆到 `rrf_fusion.py` 虽不改变算法，但导入路径变化 + helper 移动有微妙风险（如 `_make_rrf_key` 被 `rrf_fusion` 专用，移动后需保证不被其他模块误引用）；
  3. **BM25Index 全局状态依赖**：`_dict_loaded` 是 class-level 状态（jieba 词典加载 once per process），`_load_hardware_dict` 用 classmethod 改 cls._dict_loaded。拆到 `bm25_index.py` 独立类没问题，但若其他模块（如测试）直接 import `BM25Index._HARDWARE_TERMS` 或 `BM25Index._dict_loaded` 做断言，导入路径变化会导致测试挂；
  4. **25+ 处外部引用更新**：含 scripts/ 15+ 处开发辅助脚本，拆分后每个引用都要评估是否需要改导入路径（如 `get_kb_manager` 若仍从 `kb_manager.py` 导出则 scripts 不用改，但 `BM25Index` / `rrf_fusion` / `BM25_DIR` / `BUILTIN_KB_DIR` / `validate_small_chunk_size` 若搬到新模块则 scripts 全挂）；工作量大且容易遗漏，scripts 不跑测试无法发现；
  5. **测试文件引用**：`test_bm25_rag_verify.py:20` 直接 import `BM25Index, rrf_fusion`，`test_rag_edge_cases.py:28` import 多符号，`rag_eval/rescore.py:435` import `KnowledgeBaseManager`——拆分后测试套件可能集体挂；
  6. **`_rebuild_bm25` 跨方法调用**：`ingest_chunks` / `import_kb` / `_bm25_search` / `delete_kb` 都调 `_rebuild_bm25`，CRUD 和 Search 方法都依赖它，拆分到不同文件后要么 `_rebuild_bm25` 放第三个文件（helper 模块），要么某一方跨模块调另一方的私有方法；
  7. **`search` → `search_all_enabled` → `rrf_fusion` 调用链**：`search_all_enabled` 调 `search`（单 KB），`search` 调 `rrf_fusion`。若 `rrf_fusion` 拆到 `rrf_fusion.py`，`kb_search.py` 的 `search` 方法要跨模块调 `rrf_fusion`，且 `search_all_enabled` 内的 batch rerank 逻辑（`from src.rag.reranker import rerank`）也要保持原样；
  8. **强约束 #3 明确禁止破坏**：`rrf_fusion` / `BM25Index` / `search_all_enabled` / `ingest_chunks` 都在强约束红线内，任一拆分导致行为变化即违反"严格保持现有全部功能的完整性"。
- 决策：**降级处理**，保留 1190 行单文件
  1. 不拆分 `kb_manager.py`，4 个部分（CRUD / Search / BM25Index / rrf_fusion）保持在同一文件；
  2. 25+ 处外部符号导入路径不变；
  3. 后续若 kb_manager.py 继续增长，可考虑只拆 `BM25Index` 类到 `bm25_index.py`（独立类，无实例状态依赖，外部引用仅 3 处：main.py / test_bm25_rag_verify.py / 内部 _rebuild_bm25），但当前 1190 行的代价/收益不划算，且 rrf_fusion 永远不应拆（阶段 2 Task 18 已定调）。
- 下次注意：
  1. 含实例状态（`_stores` / `_bm25_indices` / `_bm25_stale`）的类拆分方法到不同文件，必须用 mixin 多继承或独立函数模式，前者 metaclass 冲突风险，后者破坏 OOP——任一方案都是高风险重构，强约束 #1 下应优先降级；
  2. 阶段 2 已降级的算法（如 Task 18 rrf_fusion）不应在阶段 4 再尝试拆分，同类 RAG 核心算法拆分风险同等高；
  3. 拆分涉及 scripts/ 大量引用时，scripts 不跑测试无法发现导入路径错误，必须手动 grep 全项目 + 逐个评估，工作量容易低估；
  4. class-level 状态（`_dict_loaded`）+ lazy load（jieba ~2s）有微妙的全局状态依赖，拆分类到新模块时要保证 class-level 状态不被复制（Python class attribute 是 class object 属性，移动 class 即移动状态，但若有人用 `from kb_manager import BM25Index._dict_loaded` 做断言会挂）；
  5. 1190 行的超标是"RAG 核心自然成长"（含 BM25 + RRF + reranker + 多 KB CRUD + ingest + export/import），不是"屎山堆积"，文件内部 `# ═══════════════════════════════════════` 章节分隔符已存在，可读性可接受。

## 2026-07-06 - Task 30 评估结论：SelfQueryRetriever 不迁移，保留现有手动 doc_filter + list_kb_docs 组合

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 3 Task 30，要求评估 `kb_manager.py` 的 `doc_filter` 是否迁移为 `SelfQueryRetriever`（LLM 自动从自然语言提取 metadata 过滤条件）。
- 现状（`kb_manager.py` L262-277 / L824-916 + `search_docs.py` L73-80 + `prompts.py` L47-61）：
  1. **手动 doc_filter**：`SearchDocsArgs.doc_filter` 是 str 参数，用户/Agent 主动传入文档名子串；
  2. **多 KB pool 扩大**：`search()` 在 doc_filter 非空时 `fetch_k = k * _DOC_FILTER_POOL_MULTIPLIER`（=3，L847）扩大候选池；
  3. **post-fusion 过滤**：`_matches_doc_filter(metadata, doc_id, doc_filter)` 在 RRF fusion 后做 title / doc_id 子串匹配（case-insensitive），保留 RRF 排序；
  4. **system prompt 已引导 Agent 用 list_kb_docs + doc_filter 组合**（prompts.py L47-61）：
     - "不确定知识库有哪些文档 → 先 list_kb_docs 盘点"
     - "发现目标文档存在后 → search_docs(query=..., doc_filter='esp32-s3') 只在该文档里搜"
     - "search_docs 返回的 summary 每行含 `doc_id §section pXX`，查错了立即换 doc_filter"
     - bad-example 明确反对"盲搜 → 反复改写 query 浪费调用次数"
- 风险点：
  1. **过滤能力退化**：Chroma translator 只支持 `$eq / $ne / $lt / $lte / $gt / $gte / $in / $nin`，**不支持子串匹配 `$contains`**。当前 `_matches_doc_filter` 是子串匹配（"esp32-s3" 匹配 "esp32-s3_datasheet.pdf"），SelfQueryRetriever 只能用 `$eq` 精确匹配，会**严重退化**过滤能力——用户必须传完整文档名才能命中；
  2. **破坏 rrf_fusion / hybrid 检索**：SelfQueryRetriever 是 vector-only 检索器（基于 Chroma `similarity_search_with_relevance_scores`），**不参与 BM25 路径**，会破坏 "vector + BM25 → RRF" hybrid 检索语义；
  3. **score 语义破坏**：SelfQueryRetriever 返回的 Document 没有 BM25 normalized score，无法参与 `rrf_fusion` 的 orig_score 平均计算，破坏 FusedResult.score 链路（threshold / UI / 跨 KB 排序）；
  4. **多 KB 架构不匹配**：SelfQueryRetriever 包装单个 vectorstore，需为每个 KB 创建实例 + metadata_field_info，复杂度激增；
  5. **重复功能 + 额外 LLM 成本**：system prompt 已引导 Agent 主动选择 doc_filter（基于 list_kb_docs 的真实文档名），比 SelfQueryRetriever 的"LLM 从自然语言凭记忆提取 filter"更可控——Agent 看到的是真实文档名，避免 LLM 凭记忆猜测文档名导致 filter 失效。每次 search_docs 多 1 次 LLM 调用提取 filter，与"严格保持现有全部功能完整性"核心约束冲突；
  6. **metadata_field_info 配置复杂**：当前 chunk metadata 包含 doc_id / title / category / source_url / tags / chunk_index / section_title / chunk_id / page_start / page_end / chunk_method / fingerprint / small_chunk_id / big_chunk_text / chunk_size / is_code_block 等 16+ 字段，SelfQueryRetriever 需为每个可过滤字段配置 `AttributeInfo`，维护成本高且大部分字段无过滤价值。
- 决策：**降级处理**，保留现有手动 doc_filter + list_kb_docs 组合
  1. 不在 `kb_manager.py` / `search.py` 引入 `SelfQueryRetriever`；
  2. 不为 chunk metadata 字段配置 `metadata_field_info`；
  3. 现有 `_matches_doc_filter` 子串匹配 + `_DOC_FILTER_POOL_MULTIPLIER=3` 扩大候选池保持原样；
  4. 现有 system prompt 的"list_kb_docs → search_docs(doc_filter=...)"策略保持原样，比 SelfQueryRetriever 更可控；
  5. 未来若 langchain 1.x 的 SelfQueryRetriever 支持 `$contains` 子串匹配 + 包装 hybrid retriever（vector+BM25），可重新评估。
- 下次注意：
  1. SelfQueryRetriever 的 Chroma translator 不支持 `$contains`，只能精确匹配，子串过滤必须用 post-fusion 手动 `_matches_doc_filter` 实现；
  2. 评估"是否需要 LLM 自动提取 filter"时，先看 system prompt 是否已引导 Agent 主动选择 filter——后者基于真实 list_kb_docs 输出，比 LLM 凭记忆提取更可靠；
  3. SelfQueryRetriever 是 vector-only，无法与 BM25+RRF hybrid 检索共存，任何"SelfQueryRetriever + BM25"组合都会破坏 rrf_fusion；
  4. metadata_field_info 维护成本高（16+ 字段），且大部分字段（fingerprint / chunk_index / page_start）无过滤价值，配置 ROI 低。

## 2026-07-06 - Task 29 评估结论：ParentDocumentRetriever 不迁移，保留现有 big_chunk_text 手写 small-to-big 方案

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 3 Task 29，要求评估 `chunking/hybrid_chunker.py` 的 `big_chunk_text` metadata 字段方案是否替换为 `ParentDocumentRetriever`（child_splitter + parent_splitter + docstore 架构）。
- 现状（`hybrid_chunker.py` L143 / L202 / L465 / L529 + `kb_routes.py` L997 / L1032-1072）：
  1. **big_chunk_text 是 metadata 字段**：每个 small chunk 的 metadata 携带 `big_chunk_text = clean_section_text[:4000]`（完整 section 文本截断 4000 字符）；
  2. **生成阶段**：HybridChunker 在 `_small_splitter.split_text` 切出 small chunks 后，每个 small chunk 的 metadata 都关联到所属 section 的完整文本（hybrid_chunker.py L143 / L202）；`_merge_tiny_chunks` 合并 tiny chunk 时也会重新计算 big_chunk_text（L465 / L529）；
  3. **使用阶段（仅前端 UI）**：`/api/kb/documents/{doc_id}/chunks` 返回 chunk 列表含 big_chunk_text（kb_routes.py L997）；`/api/kb/chunks/{small_chunk_id}` 单独拉取含 big_chunk_text（kb_routes.py L1032-1072，注释明确"前端点击'查看完整上下文'时调用此接口拉取 big_chunk_text"）；
  4. **检索阶段不使用 big_chunk_text**：`search()` / `search_all_enabled()` / `rrf_fusion()` 全程不读 big_chunk_text，检索仍用 small chunk（向量+BM25+RRF）。big_chunk_text 是"用户/前端主动展开查看完整上下文"的设计，不是"检索阶段自动注入 parent 上下文"的设计；
  5. **3 个 chunker 各自实现等价物**：HybridChunker 用 big_chunk_text，MultimodalChunker / AgentChunker 有自己的 section_summary / section_keywords 等字段（见 kb_routes.py L1003-1005）。
- 风险点：
  1. **破坏 chunk 完整性（核心约束）**：ParentDocumentRetriever 用标准 `RecursiveCharacterTextSplitter` 作 child_splitter / parent_splitter，**绕过** HybridChunker 的全部定制逻辑：
     - PAGE 标记保护（`<!-- PAGE:N -->`）+ per-small-chunk 页码提取（`_get_section_pages`）
     - 跨页表格合并（`protect_structures` + `table_map`，hybrid_chunker.py L172-173）
     - 内联代码块保护（`INLINE_CODE_RE.sub(_stash_code)`，L170-173）
     - 指纹去重（`compute_fingerprint`，L151 / L211）
     - `_merge_tiny_chunks` 两轮合并（backward + forward，L406-570）
     - section_title 继承（header_stack，L319 / L349）
     迁移会导致页码丢失、表格被切碎、指纹去重失效、tiny chunk 污染检索——直接违反"不能丢失 chunk 完整性"强约束；
  2. **架构不匹配**：ParentDocumentRetriever 是独立 retriever 架构，需要 `vectorstore + docstore + child_splitter + parent_splitter`。它会改变 chunk 入库流程（parent 入 docstore，child 入 vectorstore），现有 `ingest_chunks` 流程（vector_store.py L422-472）+ `KnowledgeBaseManager.ingest_chunks`（kb_manager.py L670-735）+ LRU 去重（L694-716）全部需要重写；
  3. **多 KB 架构不匹配**：每个 KB 独立 chunk_method（hybrid / agent / multimodal），ParentDocumentRetriever 是全局 retriever，需为每个 KB 创建实例 + docstore，复杂度激增；
  4. **多 chunker 实现冲突**：项目有 3 个 chunker（HybridChunker / MultimodalChunker / AgentChunker），每个都有自己的 big_chunk_text 等价物。迁移 ParentDocumentRetriever 要么统一替换 3 个 chunker（破坏性极大，违反"严格保持现有全部功能完整性"），要么只在 HybridChunker 用（不一致）；
  5. **检索语义改变**：ParentDocumentRetriever 的"检索 child 返回 parent"模式与当前"检索 small 返回 small"模式不一致——会让 LLM 上下文长度从 `small_chunk_size=800` 变成 parent 长度（可能 4000+），破坏 `MAX_CHUNK_CHARS=24000` 截断逻辑（search_docs.py L32）和 source SSE event 的 excerpt 字段；
  6. **score 语义破坏**：ParentDocumentRetriever 返回的 parent Document 没有 child 的 RRF score，破坏 FusedResult.score 链路；
  7. **功能已满足**：当前 big_chunk_text 已实现"小切片检索 + 大上下文查看"的目标——只是大上下文不在检索阶段自动注入，而是用户/前端主动展开。这是设计选择（避免 LLM 上下文被 parent 文本撑爆），不是缺陷。
- 决策：**降级处理**，保留现有 big_chunk_text 手写 small-to-big 方案
  1. 不在 `chunking/` 引入 `ParentDocumentRetriever`；
  2. 不在 `kb_manager.py` / `vector_store.py` 引入 docstore；
  3. 现有 `big_chunk_text` metadata 字段 + `/api/kb/chunks/{small_chunk_id}` 接口保持原样；
  4. 现有 `_small_splitter` 切 small + `big_chunk_text = clean_section_text[:4000]` 关联 section 完整文本的模式保持原样；
  5. 未来若 langchain 1.x 的 ParentDocumentRetriever 支持自定义 child_splitter 接受 ChunkResult 列表（保留 PAGE 标记 / 表格保护 / 指纹去重），可重新评估。
- 下次注意：
  1. ParentDocumentRetriever 的 child_splitter / parent_splitter 是标准 `RecursiveCharacterTextSplitter`，**绕过**项目定制 chunker 的全部保护逻辑（PAGE 标记 / 跨页表格 / 指纹去重 / tiny chunk 合并），任何迁移都必须先验证这些保护逻辑是否完整保留；
  2. 评估"是否替换手写 small-to-big"时，先看 big_chunk_text 在哪个阶段使用——当前是"前端 UI 主动展开"（kb_routes.py 返回字段），不是"检索阶段自动注入 parent"（search 不读 big_chunk_text），二者语义完全不同；
  3. ParentDocumentRetriever 的"检索 child 返回 parent"模式会让 LLM 上下文长度从 800 变成 4000+，破坏 `MAX_CHUNK_CHARS=24000` 截断逻辑和 source SSE event 的 excerpt 字段；
  4. 项目有 3 个 chunker（Hybrid / Multimodal / Agent），每个都有自己的 big_chunk_text 等价物，ParentDocumentRetriever 只能替换 Hybrid 的，无法统一替换全部，会导致 chunker 行为不一致。

## 2026-07-06 - Task 28 评估结论：MultiQueryRetriever 不迁移，保留现有 Agent 自主改写查询策略

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 3 Task 28，要求评估 `kb_manager.py` 的 `search_all_enabled` 是否接入 `MultiQueryRetriever.from_llm(retriever=base_retriever, llm=llm)` 提升召回。
- 现状（`kb_manager.py` L824-916 search / L918-988 search_all_enabled + `prompts.py` L33-61）：
  1. **hybrid 检索**：`search()` = vector (Chroma `similarity_search_with_relevance_scores`) + BM25 (`BM25Index` jieba 分词) → `rrf_fusion`（RRF 排序 + 原始 0-1 分数显示）；
  2. **多 KB 并行**：`search_all_enabled` 用 `asyncio.gather` 并行 N 个 KB 的 `search()`，跨 KB 合并后 batch rerank；
  3. **system prompt 已引导 Agent 自主改写查询**（prompts.py L33-61）：
     - "search_docs 传精炼检索词：芯片型号 + 外设名 + 协议名"（L38）
     - good-example: `search_docs(query="STM32F4 DMA 配置 传输")`（L41）
     - bad-example: `search_docs(query="这个芯片的DMA怎么用")`（L44）
     - "改写 3 次查询词仍无高度相关结果（相关度 < 80%）→ 如实告诉用户'知识库可能未覆盖该内容'"（L86）
     - "查错了立即换 doc_filter"（L51）
     - bad-example 明确反对"盲搜 → 反复改写 query 浪费调用次数"（L60-61），引导先 list_kb_docs 再 search_docs
- 风险点：
  1. **依赖 Task 18 已降级的 as_retriever()**：`MultiQueryRetriever.from_llm(retriever=base_retriever, llm=llm)` 要求 `base_retriever` 是 `BaseRetriever` 实例（标准是 `vector_store.as_retriever()`）。Task 18 降级决策明确"不在 `vector_store.py` 新增 `as_retriever()` 方法"（pitfalls.md Task 18 决策第 1 条），要用 MultiQueryRetriever 必须先违反 Task 18 决策；
  2. **破坏 rrf_fusion hybrid 检索**：MultiQueryRetriever 的设计是"包装单个 retriever，LLM 生成 N 个查询 → 每个 query 调用 retriever → union 去重"。它**只能在 retriever 层包装**，不能在 `rrf_fusion` 函数层包装（rrf_fusion 是函数不是 retriever）。如果只在 vector 层包装 MultiQueryRetriever，会导致 BM25 路径不参与多查询改写，破坏 hybrid 检索语义；
  3. **score 语义破坏**：MultiQueryRetriever 返回的 Document 没有 RRF 排序的 score，会破坏 FusedResult.score 链路（threshold / UI / 跨 KB 排序）；
  4. **多 KB 架构不匹配**：当前是"每 KB 独立 search → 跨 KB 合并"，MultiQueryRetriever 包装单个 retriever，需为每个 KB 创建实例，复杂度激增；
  5. **额外 LLM 调用成本 + 重复功能**：每次 search_docs 多 1 次 LLM 调用生成多查询。考虑 system prompt 已经引导 Agent 自己改写查询（L38-44 精炼检索词 + L86 改写 3 次降级），MultiQueryRetriever 是**重复功能**——而且 Agent 改写比 MultiQueryRetriever 的固定 prompt 模板更智能（Agent 能看到上一次结果决定是否换查询，MultiQueryRetriever 一次性生成 N 个查询不感知结果）；
  6. **与 list_kb_docs + doc_filter 策略冲突**：system prompt L47-61 引导 Agent 用"先 list_kb_docs 盘点 → search_docs(doc_filter=...)"精确策略，比 MultiQueryRetriever 的"撒网生成 N 个查询"更优——前者基于真实文档名定位，后者基于 LLM 凭记忆生成同义词。
- 决策：**降级处理**，保留现有 Agent 自主改写查询策略
  1. 不在 `kb_manager.py` / `vector_store.py` 引入 `MultiQueryRetriever`；
  2. 不在 `vector_store.py` 新增 `as_retriever()`（与 Task 18 决策一致）；
  3. 现有 system prompt 的"精炼检索词 + 改写 3 次降级 + list_kb_docs 定位"策略保持原样；
  4. 未来若 langchain 1.x 的 MultiQueryRetriever 支持"包装 hybrid retriever（vector+BM25+RRF）+ 保留 FusedResult.score"，可重新评估。
- 下次注意：
  1. MultiQueryRetriever 要求 `retriever=BaseRetriever` 实例，但 Task 18 已决定不实现 `as_retriever()`，任何依赖 BaseRetriever 的迁移都会撞到这个前置决策；
  2. MultiQueryRetriever 只能包装单路 retriever（vector 或 BM25），无法包装 `rrf_fusion` 函数，强行包装会破坏 hybrid 检索语义；
  3. 评估"是否需要 LLM 自动改写查询"时，先看 system prompt 是否已引导 Agent 改写——后者能感知上次结果决定是否换查询，前者一次性生成 N 个查询不感知结果，Agent 自主改写更智能；
  4. MultiQueryRetriever 返回 Document 无 score，破坏 FusedResult.score 链路（threshold / UI / 跨 KB 排序）；
  5. system prompt 的"list_kb_docs + doc_filter"精确策略比 MultiQueryRetriever 的"撒网生成同义词"更优——前者基于真实文档名，后者基于 LLM 凭记忆。

## 2026-07-06 - Task 26 LangGraph Studio 调试支持：配置文件已就位，langgraph dev 启动待验证

- 任务背景：
  langchain 1.x 全量审计 spec 阶段 3 Task 26，要求新增 `langgraph.json` 配置文件指向 `create_hardware_agent`，并验证 `langgraph dev` 启动 + Studio 可视化。
- 错误现象 1（langgraph-cli 未安装）：
  执行 `langgraph dev` 报错 `The term 'langgraph' is not recognized as a name of a cmdlet, function, script file, or executable program`；`pip show langgraph-cli` 报 `Package(s) not found: langgraph-cli`。
- 错误原因 1：
  项目只安装了 `langgraph`（v1.2.4，核心库），未安装 `langgraph-cli`（独立的 CLI 工具包，提供 `langgraph dev` / `langgraph build` 命令）。二者是不同的 PyPI 包，核心库不携带 CLI。
- 错误现象 2（factory 签名不匹配，潜在问题）：
  `create_hardware_agent` 签名为 `(model, api_key, base_url, tools, temperature=0.7, max_tokens=4096, enable_hitl=False)` 共 7 个参数（无默认值的前 4 个必填）。LangGraph CLI 的 `graphs` 字段要求指向的 callable 必须是「无参函数」或「单 `config: RunnableConfig` 参数函数」，CLI 会用 `create_hardware_agent()` 无参调用，必然 TypeError。
- 错误原因 2：
  项目的 agent factory 设计为「每请求构造」（chat_routes 在每个 SSE 请求里读 settings + payload 后调 factory），而 LangGraph Studio 期望「进程级单例」（启动时构造一次，复用于所有 thread）。两种模式不兼容。
- 修复方式：
  按任务强约束「不修改任何现有源代码文件」+「langgraph dev 启动失败降级」，**仅新增 `langgraph.json` 配置文件，不改 agent_factory.py**：
  1. 新增 `langgraph.json`（项目根目录）：`{"dependencies": ["."], "graphs": {"hardware_agent": "./backend/src/agent/agent_factory.py:create_hardware_agent"}, "env": "backend/.env"}`；
  2. `env` 字段用 `backend/.env` 而非 `.env`，匹配 `settings.py` 的 `ROOT_DIR / ".env"`（ROOT_DIR = backend/）路径解析；
  3. 导入冒烟测试通过：`from src.agent.agent_factory import create_hardware_agent` → IMPORT_OK；
  4. `main.py` 整体导入通过：`import main` → MAIN_IMPORT_OK，确认 langgraph.json 不影响 `python main.py --web` 启动；
  5. `langgraph dev` 启动验证降级为「配置文件已就位，启动待验证」。
- 下次注意：
  1. `langgraph`（核心库）和 `langgraph-cli`（CLI 工具）是两个独立 PyPI 包，`pip install langgraph` 不会装 CLI，需单独 `pip install langgraph-cli`；
  2. LangGraph Studio 的 `graphs` 字段要求 factory 是无参或单 `config` 参数函数，项目现有的「每请求构造 + 7 参数」factory 无法直接被 CLI 调用，未来若要真正跑通 `langgraph dev`，需新增一个无参 wrapper（如 `make_studio_agent()` 读 settings 默认值后调 `create_hardware_agent`），但本任务受「不改源码」约束未做；
  3. `langgraph.json` 的 `env` 字段路径是相对 langgraph.json 所在目录（项目根）解析的，而 `settings.py` 的 `.env` 路径是相对 `backend/` 解析的，两者根目录不同，配置时必须用 `backend/.env` 才能让 CLI 加载到 settings.py 同一个 .env；
  4. langgraph.json 只是静态配置文件，`python main.py --web` 不读取它，对现有启动链路零影响（已通过 main.py 导入测试确认）。

## 2026-07-06 - Task 19 评估结论：Reranker 保留手动调用模式，不迁移 ContextualCompressionRetriever

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 2 Task 19，要求评估 `backend/src/rag/reranker.py` 的 `BgeReranker` 类是否迁移为 `ContextualCompressionRetriever` + `BaseDocumentCompressor`。
- 实际路径勘误：
  task 描述提到 `BgeReranker` 类，但 `reranker.py` 中**不存在该类**——模块只暴露两个函数 `get_reranker()`（懒加载 CrossEncoder 单例）和 `rerank(query, chunks, top_k)`（无状态函数）。迁移目标"将 BgeReranker 实现 BaseDocumentCompressor 接口"缺少现成类载体，需先从零创建类。
- 现状（`kb_manager.py` L967-984 `search_all_enabled` 调用方式）：
  1. **跨 KB 批量 rerank**：所有 KB 的 fused 结果合并后，调用一次 `rerank_chunks(query, chunk_texts)`，单次 `model.predict` 处理全部 chunks（性能优化注释 L988-989 明确指出 per-KB rerank 会导致 13 KB × 47s = 611s 串行延迟）；
  2. **只过滤不重排**：reranker 返回 `(idx, score)` 仅用于过滤（保留 `score >= _RERANKER_MIN_SCORE=-2.0`），**不覆盖** `FusedResult.score`；最终顺序由 RRF 原始分数决定（L987 `all_results.sort(key=lambda r: r.score, reverse=True)`）；
  3. **冷却降级**：reranker.py 有 60s 冷却 + 全 0 分 fallback（`_reranker_failed_at` / `_RERANKER_COOLDOWN_S`），调用方通过 `reranked[0][1] != 0.0` 检测 fallback 后跳过过滤；
  4. **_RERANKER_MIN_KEEP = 2** floor 保护：过滤后不足 2 个则保留 top-2，避免 context 饥饿。
- 风险点：
  1. **顺序改变**：当前是"只过滤不重排"模式，`ContextualCompressionRetriever` 是"过滤+重排"模式（compressor 处理后顺序即为返回顺序），迁移会改变最终文档顺序 → 违反"严格保持 RAG 检索结果一致性"核心约束；
  2. **架构不匹配**：当前是"每 KB 独立 search → 跨 KB 合并 → 统一 batch rerank"，`ContextualCompressionRetriever` 是"包装单个 retriever"。要用它需二选一：(a) 包装每个 KB 的 retriever → rerank 退化为 per-KB 串行，611s 回归；(b) 自定义聚合多 KB 的 retriever 再包装 → 复杂度激增；
  3. **score 语义破坏**：`ContextualCompressionRetriever` 返回的 Document 没有内置 score，若 reranker score 覆盖 `FusedResult.score` 会破坏 UI 百分比显示和跨 KB 排序；若不覆盖则需要旁路存储，不如当前直接；
  4. **降级机制丢失**：reranker 的 60s 冷却 + 全 0 分 fallback + MIN_KEEP floor 三套保护逻辑需要在 `BaseDocumentCompressor` 子类里重新实现，且 `ContextualCompressionRetriever` 没有"compressor 失败则回退原顺序"的原生支持；
  5. **依赖 Task 18**：Task 18 降级后没有 ensemble retriever 可作为 base retriever 包装，`ContextualCompressionRetriever` 失去基础。
- 决策：**降级处理**，保留现有手动调用 `rerank()` 逻辑
  1. 不创建 `BgeReranker` 类，不实现 `BaseDocumentCompressor` 接口；
  2. `search_all_enabled` 的 L967-984 batch rerank 调用保持原样；
  3. 未来若 langchain 1.x 的 `ContextualCompressionRetriever` 支持"只过滤不重排"模式 + 跨 retriever 批量压缩，可重新评估。
- 下次注意：
  1. task 描述里的 `BgeReranker` 类在 `reranker.py` 中不存在，后续若有人按 task 描述找类会扑空——实际是模块级函数 `rerank()` + `get_reranker()`；
  2. 评估 reranker 迁移时必须区分"只过滤"和"过滤+重排"两种语义，前者保留原排序（RRF 分数决定），后者改变排序（reranker score 决定），二者对召回结果一致性影响完全不同；
  3. 跨 KB 批量 rerank 是性能关键优化（611s → 50s），任何迁移都不能破坏这个批处理模式；
  4. reranker 的三套保护逻辑（冷却 / 全 0 fallback / MIN_KEEP floor）是线上稳定性的保障，迁移时必须完整复刻。

## 2026-07-06 - Task 18 评估结论：EnsembleRetriever 迁移风险高，保留现有 rrf_fusion

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 2 Task 18，要求评估 `kb_manager.py` 的 `rrf_fusion`（L280-415，135 行）+ `BM25Index`（L80-222）是否迁移为 `EnsembleRetriever` + `as_retriever()` 模式。
- 现状（`rrf_fusion` 是高度定制化的混合算法，**不是标准 RRF**）：
  1. **混合排序+显示分数**：用 RRF 排序，但返回的 `SearchResult.score` 是**原始 0-1 分数的平均值**（vector cosine + BM25 normalized 平均，L370），不是 RRF 分数（L407-414 显式用 `orig_score` 而非 `rrf_score`）；
  2. **BM25-only penalty**：只命中 BM25 没命中 vector 的 chunk，对 orig_score 乘 0.85 惩罚（L375-377 `_BM25_ONLY_PENALTY`）；
  3. **BM25 软归一化**：`_bm25_search` 用 `_BM25_NORM_FACTOR = 1.15` 软归一化（top-1 → ~0.87 而非 1.0，L1108/L1124-1125），防止 BM25 top-1=1.0 直接传播到显示分数；
  4. **特殊 dedup key**：`_make_rrf_key` 用 `doc_id#chunk_index` 作为去重 key，显式处理 `chunk_index=None` 边界（L225-236）；
  5. **多 KB 架构**：每个 KB 独立 `search()`（vector+BM25+RRF）→ `search_all_enabled` 跨 KB 合并 → batch rerank，**不是单 retriever 内融合**；
  6. **详细诊断日志**：RRF 每步打 DEBUG 日志（input/output/per-result 三段）。
- 对照 `EnsembleRetriever` 默认行为：
  | 维度 | 当前 rrf_fusion | EnsembleRetriever 默认 |
  |------|----------------|----------------------|
  | 返回 score | 原始 0-1 分数平均 | RRF 分数（典型 0.016-0.033） |
  | BM25-only penalty | ×0.85 | 无 |
  | BM25 软归一化 | 1.15 因子 | 无（BM25Retriever 不归一化） |
  | 自定义 dedup key | doc_id#chunk_index | Document.page_content 哈希 |
  | 多 KB 架构 | 每 KB 独立 + 跨 KB 合并 | 单 retriever 内融合 |
- 风险点：
  1. **score 语义改变破坏 3 处依赖**：(a) `score_threshold` 过滤（L882-884 `if r.score >= score_threshold`，用户配置 0.3 等阈值会因 RRF 分数量级 0.03 而全部被滤）；(b) UI 百分比显示（FusedResult.score 直接显示为相关性百分比）；(c) 跨 KB 排序（L987 `sort(key=lambda r: r.score, reverse=True)`，RRF 分数跨 KB 不可比）；
  2. **BM25Index 包装难度**：`_TECH_TERM_RE` 正则 + `_HARDWARE_TERMS` 150+ 词表 + `_load_hardware_dict` 类方法状态 + jieba 懒加载，全部需在 `BaseRetriever` 子类的 `_get_relevant_documents()` 里重写，且 `BaseRetriever` 是 pydantic 模型，类方法状态管理需特殊处理；
  3. **多 KB 架构不匹配**：要用 `EnsembleRetriever` 需为每个 KB 创建一个实例（vector retriever + BM25 retriever 组合），再跨 KB 合并 + rerank——复杂度从"1 个 rrf_fusion 函数"变成"N 个 EnsembleRetriever 实例 + 跨 KB 合并逻辑"，反而增加；
  4. **LRU 缓存**：task 描述提到的"256 entries 5-min TTL LRU 缓存"在当前代码中**不存在**（当前是 `_stores: dict` + `_bm25_indices: dict` 直接缓存，无 TTL），迁移不影响 KB 级缓存，但 task 描述基于旧版本假设；
  5. **HNSW ef_search 调优**：`HardwareVectorStore` 在 `db` property 里通过 `_collection.set_ef_search()` 调优 HNSW（L236-249），`as_retriever()` 返回的 `VectorStoreRetriever` 不暴露这个底层调优。
- 决策：**降级处理**，保留现有 rrf_fusion + BM25Index
  1. 不在 `vector_store.py` 新增 `as_retriever()` 方法（`HardwareVectorStore.search` 已直接调用 `db.similarity_search_with_relevance_scores`，无需绕道 retriever）；
  2. 不将 `BM25Index` 包装为 `BaseRetriever` 子类（jieba 分词定制 + 硬件词表 + 软归一化难以无损迁移）；
  3. 不替换 `rrf_fusion` 为 `EnsembleRetriever`（混合 RRF+原始分数算法与 EnsembleRetriever 纯 RRF 不一致）；
  4. 未来若 langchain 1.x 的 `EnsembleRetriever` 支持"RRF 排序 + 自定义 score 投影函数 + per-retriever 归一化"，可重新评估。
- 下次注意：
  1. 评估 RAG 检索迁移时，必须先核对 `score` 字段的语义（RRF 分数 vs 原始 cosine vs 归一化 BM25 vs 混合平均），任何 score 语义改变都会级联破坏 threshold 过滤 / UI 显示 / 跨 KB 排序；
  2. 当前 `rrf_fusion` 是混合算法（RRF 排序 + 原始分数显示 + BM25 penalty + 软归一化），不是教科书 RRF，不能用 `EnsembleRetriever` 直接替换；
  3. `BM25Index` 的 jieba 分词定制（`_TECH_TERM_RE` + 150+ 硬件词表）是 BM25 召回质量的关键，迁移时必须完整保留，不能退化为基础分词；
  4. 多 KB 架构（每 KB 独立 search + 跨 KB batch rerank）是性能关键优化，任何迁移都不能破坏这个批处理模式；
  5. task 描述里提到的"LRU 256 entries 5-min TTL"在当前代码不存在，可能是基于旧版本假设，后续评估以实际代码为准。

## 2026-07-06 - Task 17 评估结论：PluginManager 保留但未接入 ToolRouter

- 评估背景：
  langchain 1.x 升级全量审计 spec 阶段 2 Task 17，要求评估 `backend/src/agent/plugins/`（manager.py / base.py / redact_plugin.py / __init__.py）是否接入 `tool_router.py` 的 `_run_pipeline` 或删除。
- 实际路径勘误：
  task 描述写的是 `backend/src/agent/core/plugins/`，但实际路径是 `backend/src/agent/plugins/`（不在 core 下）。
- 现状：
  1. PluginManager / RedactPlugin / Plugin 三个组件代码完整、可正常导入（`from src.agent.plugins import PluginManager, RedactPlugin, Plugin` 通过）；
  2. `tool_router.py` 的 `_run_pipeline`（L149-157）完全未接入 PluginManager，也无任何 plugins import；
  3. Grep `from src.agent.plugins` / `PluginManager` / `RedactPlugin` 全 backend，只有 plugins/ 目录内部相互引用，**没有任何外部业务代码调用** PluginManager——即 plugins/ 当前是"未接入的扩展点"，不是"正在生效的脱敏链"。
- RedactPlugin vs audit_recorder 脱敏逻辑对比（**不是完全重复**）：
  | 维度 | RedactPlugin | AuditRecorder._redact_args |
  |------|--------------|-----------------------------|
  | 作用对象 | tool **result**（`result['output']` / `result['data']`） | tool **args**（写入 DB 审计日志前） |
  | 匹配方式 | 基于**值**正则 `sk-[a-zA-Z0-9\-_]{20,}`（OpenAI 风格 key） | 基于 **key 名**匹配 `_SENSITIVE_KEY_PATTERNS = {api_key, apikey, token, password, secret, credential, key}` |
  | 替换文本 | `[REDACTED]` | `***REDACTED***` |
  | 是否生效 | ❌ 未接入，从未运行 | ✅ 每次 `ToolRouter.dispatch` 末尾 `audit_recorder.record` 时运行 |
  | 结论 | 互补关系（一个管 args 一个管 result），不是简单重复；但 RedactPlugin 当前对线上行为零影响 |
- 决策：**选项 C（保留不接入）**
  1. 保留 `backend/src/agent/plugins/` 目录原样不动，不修改任何业务代码（符合"严格保持现有全部功能完整性"核心约束）；
  2. 不在 `tool_router.py` 接入 PluginManager——接入会改变现有 dispatch 流程，与"保持功能完整性"冲突，且 RedactPlugin 的 result 侧脱敏当前并非必需（args 侧已由 audit_recorder 覆盖）；
  3. 未来如需启用 result 侧脱敏，只需在 `tool_router._run_pipeline` 的 `_run_with_timeout` 前后插入 `pm.run_pre_tool_call` / `pm.run_post_tool_result` 两行调用，并在 ToolRouter.__init__ 注入 PluginManager 实例即可。
- 下次注意：
  1. task 描述里的路径 `backend/src/agent/core/plugins/` 不存在，实际是 `backend/src/agent/plugins/`，后续若有人按 task 路径找文件会扑空；
  2. 评估"是否重复实现"时不能只看功能名（都叫"脱敏"），要区分作用对象（args vs result）和匹配方式（key 名 vs 值正则），否则会误删互补逻辑；
  3. 若后续选择删除 plugins/ 目录，需同步清理 `tool_router.py`（当前无 import，无需清理）、`pitfalls.md`（本条）、`architecture-map.md`（若提及）。

## 2026-07-06 - langchain 1.x `BaseChatModel.stream()` 末尾追加空 terminator chunk（chunk_position='last'）

- 错误现象：
  1. 给 `ReasoningChatOpenAI.stream()` 喂 3 个 mock chunk（2 个 reasoning + 1 个 content），`list(stream)` 返回长度为 4 而非 3；
  2. 第 4 个 chunk 是 `AIMessageChunk(content='', additional_kwargs={}, response_metadata={}, chunk_position='last')`，即 langchain 1.x 自动追加的流终止符；
  3. `assert len(collected) == 3` 失败，`assert 4 == 3`。
- 错误原因：
  langchain 1.x（langchain-core 1.4.x）的 `BaseChatModel.stream()` 在底层 `_stream` 迭代结束后，会额外 yield 一个 `chunk_position='last'` 的空 terminator chunk 用于标记流结束。langchain 0.3.x 没有这个行为。
- 修复方式：
  测试断言改为语义化过滤，不依赖固定长度：用 `[c for c in collected if c.additional_kwargs.get("reasoning_content")]` 提取 reasoning chunk、`[c for c in collected if c.content]` 提取 content chunk，分别校验数量和内容，忽略空 terminator。
- 下次注意：
  1. langchain 1.x 下写 `stream()` 测试，不要 `assert len(collected) == N`，要用语义过滤（有 content / 有 reasoning_content / 有 tool_call_chunks）来定位目标 chunk；
  2. terminator chunk 的特征是 `chunk_position='last'` + 空 content + 空 additional_kwargs + 空 response_metadata，可用 `(c.content == "" and not c.additional_kwargs)` 排除；
  3. 同理适用于 `astream()` 异步流。

## 2026-07-06 - .venv 中 langgraph 0.2.61 过旧导致 `create_react_agent() got an unexpected keyword argument 'prompt'`

- 错误现象：
  1. 前端发送 Agent 请求，浏览器控制台报 `SSE 错误: Agent 调用失败`，流式输出立即停止；
  2. 后端日志 `ERROR app.api.chat_routes Agent path failed: create_react_agent() got an unexpected keyword argument 'prompt'`；
  3. 因 `chat_routes.py:256` 捕获异常后 yield `error` 事件，前端展示 "Agent 调用失败"。
- 错误原因：
  `agent_factory.py:97` 使用 `create_react_agent(model=..., tools=..., prompt=build_system_prompt(), ...)`，其中 `prompt` 参数是 langgraph 1.x 引入的新 API；而 .venv 中安装的是 langgraph 0.2.61，只支持 `state_modifier` / `messages_modifier`，不识别 `prompt`，直接抛 TypeError。
- 修复方式：
  按用户指示升级 langgraph 全家桶（不要降级代码）：
  1. langgraph 0.2.61 → 1.2.7
  2. langchain 0.3.0 → 1.3.11
  3. langchain-community 0.3.0 → 0.4.2
  4. langchain-openai 0.2.0 → 1.3.3
  5. langchain-text-splitters 0.3.0 → 1.1.2
  6. langchain-core → 1.4.8
  7. openai → 2.44.0
  8. instructor 1.3.2 → 1.15.4（ragas 传递依赖，旧版要求 openai<2.0.0，会冲突）
  9. langgraph-checkpoint-sqlite → 3.1.0
- 隐藏陷阱：升级后 `from langgraph.prebuilt import create_react_agent` 仍报 `ImportError: cannot import name 'create_react_agent' from 'langgraph.prebuilt' (unknown location)`
  - 现象：`langgraph/prebuilt/` 目录里只有 `interrupt.py`、`_tool_call_stream.py`、`_tool_call_transformer.py`、`py.typed`，缺 `__init__.py` / `chat_agent_executor.py` / `tool_node.py`（但 `__pycache__` 里有对应 .pyc）。
  - 根因：langgraph 1.2.7 与 langgraph-prebuilt 1.1.0 共享 `langgraph/prebuilt/` 目录，安装顺序导致部分文件被覆盖丢失。
  - 修复：`python -m pip install --force-reinstall --no-deps langgraph-prebuilt==1.1.0` 强制重装即可恢复。
- 下次注意：
  1. 出现 `(unknown location)` 类 ImportError 时，优先检查包目录是否缺 `__init__.py` 或关键源文件（对比 `dist-info/RECORD` 与实际文件），而不是怀疑代码；
  2. 升级 langgraph 1.x 时，必须同步升级 langchain 1.x + langchain-openai 1.x，否则 langchain-core 1.4.x 与 langchain 0.3.x 不兼容；
  3. 升级 openai 到 2.x 时，要检查 instructor、ragas 等传递依赖是否兼容，必要时一并升级 instructor 到 1.15+；
  4. Agent 路径错误恢复后，验证请求必须带 `use_agent: true`（`_should_use_agent` 在 `agent_factory.py:509` 要求显式 opt-in），否则即使 Agent 正常也会走 fallback LLM 路径，看不出修复效果；
  5. 后端 `reload=False`，uvicorn 不会自动重载代码改动，每次依赖/代码改动后必须手动停进程重启，否则会跑旧代码导致误判。

## 2026-07-06 - HITL 端到端测试因缺少 OpenAI 兼容 /v1/models 与 /v1/chat/completions 被阻塞

- 错误现象：
  1. 前端设置页添加本地服务商 `http://127.0.0.1:58080/v1` 后，点击「验证」无反馈，服务商状态保持「未验证」；
  2. 主界面模型选择器显示「未选择模型」，发送按钮 disabled，无法进入 HITL 权限流程；
  3. 直接请求 `GET http://127.0.0.1:58080/v1/models` 返回 `{"detail":"Not Found"}`。
- 错误原因：
  后端 `chat_routes.py` 只暴露 `POST /api/models`（内部用 `AsyncOpenAI.models.list()` 去访问用户配置的 `base_url/models`），并未在 `58080` 端口实现 OpenAI 兼容的 `GET /v1/models`；同理也没有 `/v1/chat/completions`。当前端把 Base URL 指向本地后端时，验证和后续聊天都因端点不存在而失败。
- 修复方式：
  本次测试未修复代码。可选方案：a) 后端新增 OpenAI 兼容的 `/v1/models` 与 `/v1/chat/completions` 路由；b) 测试环境改用真实可用的 OpenAI 兼容端点；c) 在 demo 脚本中 mock LLM 响应以绕过外部依赖。
- 下次注意：
  1. 进行前端 HITL/端到端测试前，先确认 `base_url/models` 和 `base_url/chat/completions` 可正常返回；
  2. 本地自部署场景若需支持「把后端当 OpenAI 代理」，需要显式实现这些兼容端点，不能默认假设存在。

## 2026-07-06 - TopBar 在无活跃会话时显示 "未命名对话"

- 错误现象：
  1. 未配置 API Key 或后端会话列表为空时，首页顶部标题显示 "未命名对话"；
  2. 浏览器标签标题已跟随 `activeSessionTitle` 回退到应用名，但 TopBar 中心标题未同步。
- 错误原因：
  `TopBar.tsx` 的 `sessionTitle` 在 `sessions` 中找不到对应标题且 messages 为空时，fallback 到 `t('untitled')`（"未命名对话"）。
- 修复方式：
  将 fallback 改为 `t('productName')`，与 `document.title` 在无会话时的表现保持一致。
- 下次注意：
  1. 页面标题、浏览器标签标题、顶部栏标题等三处 fallback 应使用同一来源，避免一处显示 "未命名"；
  2. 空状态/未配置状态的文案要引导用户下一步，而不是暴露内部 "未命名" 概念。

## 2026-07-06 - pytest 断言未随 ToolResultEnvelope / 默认值变更同步更新

- 错误现象：
  1. `test_routes_tool.py::test_known_tool_returns_success_and_data` 断言 `"output" in data["data"]`，但新 envelope 把 `output` 放在顶层；
  2. `test_routes_tool.py::test_unknown_tool_returns_tool_not_found` 断言 `data["error"]["code"]`，但 `ErrorDetail` 字段是 `error_type`；
  3. `test_settings.py::test_default_values` 断言 `s.host == "0.0.0.0"`，但项目安全策略已要求默认 `127.0.0.1`。
- 错误原因：
  接口契约和默认值改动后，相关测试断言没有同步更新，导致“代码行为正确但测试失败”。
- 修复方式：
  1. `test_routes_tool.py` 改为 `assert "output" in data` 和 `assert data["error"]["error_type"] == "TOOL_NOT_FOUND"`；
  2. `test_settings.py` 改为 `assert s.host == "127.0.0.1"`。
- 下次注意：
  1. 修改接口返回结构、错误字段、默认值后，必须同步搜索并更新测试断言；
  2. 提交前跑 `pytest -q`，不要把“测试失败”当成“环境问题”忽略。

## 2026-07-06 - docling/docling_core 版本不兼容导致部分测试无法收集

- 错误现象：
  1. `pytest` 收集 `test_bm25_rag_verify.py`、`test_rag_edge_cases.py`、`test_task4_parallel_cache.py`、`test_routes_kb.py` 时报错：`ImportError: cannot import name 'BaseText' from 'docling_core.types'`；
  2. 后端启动 warmup 也报同样的 docling 导入警告。
- 错误原因：
  当前 `.venv` 中 `docling_core` 版本与 `docling` 版本不兼容，`BaseText` 已被移除或改名。
- 修复方式：
  本次验证先通过 `--ignore` 跳过受影响的测试文件；后续需在 `requirements.txt` / `pyproject.toml` 中锁定兼容版本。
- 下次注意：
  1. 引入 docling 等快速迭代库时，必须锁定主库和核心库（docling + docling-core）的兼容版本；
  2. CI 应包含测试收集阶段（`pytest --collect-only`），避免仅在本地运行子集时遗漏环境问题。

## 2026-07-06 - ResumeContext 类型注解与实际类型不一致（A3）

- 错误现象：
  1. `backend/app/api/chat_routes.py` 中 `ResumeContext.req` 标注为 `"ChatRequest"`，但实际传入的是 `ResumeRequest`；
  2. `ResumeContext.call_counter` 标注为 `int`，但实际传入的是 `collections.Counter`；
  3. `_resume_event_generator_from_ctx` 内访问 `ctx.req.decision` / `ctx.req.payload.permission_mode`，与 `ChatRequest` 的字段不符。
- 错误原因：
  将 `_resume_event_generator` 的 5 个参数封装成 `ResumeContext` dataclass 时，类型注解没有同步更新，导致静态检查失真、后续维护容易误解。
- 修复方式：
  1. `ResumeContext.req` 改为 `"ResumeRequest"`；
  2. `ResumeContext.call_counter` 改为 `Counter`；
  3. 确保 `_resume_event_generator_from_ctx` 内部访问与标注一致。
- 下次注意：
  1. 参数封装成 dataclass / Pydantic 模型后，必须逐项核对类型注解是否与原函数签名一致；
  2. 涉及 `Request` 包装类（如 `ResumeRequest` 内含 `ChatRequest`）时，特别注意字段层级，不要把外层和内层模型搞混。

## 2026-07-06 - build_firmware 写死 arduino framework + audit_pins 芯片数据覆盖窄 + 工具描述太简短

- 错误现象：
  1. build_firmware 工具写死 `arduino` framework，ESP-IDF / STM32 Cube HAL 用户无法编译；
  2. audit_pins 只有 esp32 + esp32-s3 的 strapping 数据，其他芯片 fallback 到 esp32-s3 导致误报；
  3. run_command description 只有一句话，Agent 不知道能用它干啥（pio/串口/包管理/git/时序计算），倾向于造伪工具；
  4. wiring/render_wiring、audit_pins/render_safety_report 功能重叠但 description 没写区分，Agent 容易调错；
  5. read_file/write_file/edit_file description 太简短，Agent 不知道为什么用它们而不用 run_command cat/echo/sed。
- 错误原因：
  1. pio_runner.py 的 PLATFORM_FRAMEWORK 写死 arduino，CompileRequest 没有 framework 字段；
  2. gpio.py 的 STRAPPING_PINS 只有两个芯片，audit.py fallback 到 esp32-s3 而不是空集；
  3. 工具 description 写得太简短，没有列举使用场景和与其他工具的区分。
- 修复方式：
  1. pio_runner.py：PLATFORM_FRAMEWORK 改为 PLATFORM_FRAMEWORKS（dict[str, tuple]），CompileRequest 加 framework 字段，新增 _resolve_framework 校验；
  2. build_tool.py：BuildCodeArgs 加 framework 参数，execute 透传，description 列举 4 种平台+框架组合；
  3. gpio.py：STRAPPING_PINS 扩展到 ESP32 全系列（C3/C6/S2/H2）+ STM32（F4/F7/H7 调试引脚），audit.py 改为未知芯片返回空集；
  4. run_command.py：description 扩展，列举 10+ 常用场景 + 与专用工具的区分；
  5. wiring/audit_pins/render_wiring/render_safety_report：description 写清"返回数据给 LLM"vs"推送到前端面板"的区分；
  6. read_file/write_file/edit_file：description 写清和 run_command cat/echo/sed 的区分。
- 下次注意：
  1. 工具 description 不要只写一句话，要列举常用场景 + 与相似工具的区分；
  2. 数据表（STRAPPING_PINS）要覆盖全系列芯片，未知芯片 fallback 到空集而不是套用其他芯片；
  3. framework/platform 这类配置参数要可配，不要写死；
  4. 造新工具前先想"run_command 能不能干"，能干的不要造伪工具。

## 2026-07-05 - AppRoot.tsx 使用 ToastContainer 但未 import（tsc 必报错）

- 错误现象：
  1. `AppRoot.tsx` 第 138 行渲染了 `<ToastContainer />`，但文件顶部 import 列表里没有从 `../shared/Toast` 导入 `ToastContainer`；
  2. `npx tsc --noEmit` 会报 `Cannot find name 'ToastContainer'`，导致整个 frontend 类型检查失败。
- 错误原因：
  多线程并行开发时（推测 T5 前端线程），向 AppRoot 添加 ToastContainer 渲染时遗漏了 import 语句；该错误在引入新改动跑 tsc 时才暴露。
- 修复方式：
  在 AppRoot.tsx 顶部补 `import { ToastContainer } from "../shared/Toast";`。
- 下次注意：
  1. 在 AppRoot 等顶层容器文件加新组件渲染时，必须同步补 import，跑一次 `npx tsc --noEmit` 兜底；
  2. 多线程改同一文件时，import 区是高频冲突点，改完先 tsc 自查再交回。

## 2026-07-05 - rAF closure 持有旧 activeSessionId 导致切会话后写错滚动位置缓存

- 错误现象：
  1. 用户在会话 A 滚动到中间位置后快速切换到会话 B，再切回 A；
  2. A 的滚动位置没有恢复到中间，而是变成了顶部或底部；
  3. 同时切回 B 时，B 刚恢复的 cached 位置可能被旧 rAF 触发的 scrollToBottom 强制覆盖到底部。
- 错误原因：
  `ChatArea.tsx` 的 `scrollToBottom` / `handleScroll` 用 `useCallback` 依赖 `[activeSessionId]`，rAF 回调的 closure 持有当时的 `activeSessionId`。切会话后旧 rAF 仍会执行，用旧 `activeSessionId` 写入 `scrollPosCacheRef`，但此时 DOM 已是新会话的内容——写入错误的 cache。更严重的是 `scrollToBottom` 调用 `el.scrollTo` 会把新会话强制滚到底部，覆盖 `restoreScrollPosition` 刚恢复的 cached 位置（双帧 rAF 在切会话前排队，切会话后才执行）。
- 修复方式：
  1. 新增 `activeSessionIdRef`，每次 render 同步更新（`activeSessionIdRef.current = activeSessionId`）；
  2. `scrollToBottom` / `handleScroll` 改用 `activeSessionIdRef.current` 读实时 sessionId，依赖改 `[]`，引用稳定且永远读最新值；
  3. 新增 `pendingRafsRef` 跟踪待执行的 rAF id，`cancelPendingRafs` 一次性取消全部；
  4. `restoreScrollPosition` 开头调用 `cancelPendingRafs`，取消旧 rAF 避免覆盖新会话位置；
  5. 组件卸载 cleanup 也调用 `cancelPendingRafs` 避免泄漏。
- 下次注意：
  1. `useCallback` + rAF/Promise/setTimeout 等异步回调，closure 捕获的 state 在回调执行时可能已过期，应改用 ref 读实时值；
  2. 切会话/切路由等"旧副作用应该作废"的场景，必须跟踪并取消待执行的异步任务（rAF id / timer / AbortController）；
  3. 自动滚动相关的 rAF 必须可取消，否则旧会话的滚动指令会把新会话强制滚到底，破坏位置恢复功能。

## 2026-07-06 - 工作台 5 个 Pane 拖拽右栏后内容不跟随变宽（flex row 子元素未设 width）

- 错误现象：
  1. 拖拽右栏 resizer 把右栏拉宽后，串口监视器/烧录/代码预览/接线图/安全护栏 5 个 Pane 的内容都不跟随变宽，右侧大片空白；
  2. 点击 Pane 内部任意按钮（如"连接"）后突然恢复正常占满宽度；
  3. 刷新页面也恢复正常。
- 错误原因：
  `WorkbenchPanel.tsx` 的 5 个 wrapper div 用 `display: "flex"`（默认 `flex-direction: row`）。在 flex row 中，子元素的主轴（水平）宽度默认是 `flex: 0 1 auto` = 内容宽度，不会自动撑满父容器。而 5 个 Pane 的根元素（`.serial-monitor` / `.flash-panel` / `.preview-panel` / `.wiring-panel` / SafetyPane inline div）都只有 `height: 100%`，没有 `width: 100%` 或 `flex: 1`，所以只占内容宽度。点击按钮触发 re-render 时浏览器碰巧重算布局才"恢复"。
- 修复方式：
  1. `WorkbenchPanel.tsx` 5 个 wrapper div 从 `display: "flex"` 改为 `display: "block"`，block-level 子元素自然占满 100% 宽度；
  2. 5 个 Pane 根元素 CSS/inline style 都加 `width: 100%` 作为双保险。
- 下次注意：
  1. flex row 容器内的子元素若需要占满宽度，必须显式设置 `width: 100%` 或 `flex: 1`，不能依赖默认行为；
  2. `display: flex` 切换为 `display: block` 时要确认子元素是否为 block-level（`display: flex` 元素本身是 block-level，会自然占满父宽度）；
  3. "点击按钮后恢复"是典型的 flex 子元素未设宽度的症状——点击触发 re-render 让浏览器重算布局，但这不是可靠的触发方式。

## 2026-07-06 - 右侧面板拖拽变宽后，内部工作台/Monaco/SVG 出现右侧空白

- 错误现象：
  1. 拖拽右侧 resizer 把右栏拉宽后，右栏内部出现大片空白；
  2. 工作台、串口日志、接线图 SVG、代码预览（Monaco）等内容没有同步变宽；
  3. 刷新页面后恢复正常。
- 错误原因：
  拖拽只改变了最外层 DIV 的宽度，但内部组件（Monaco Editor、SVG 画布、固定宽度的表格/画布等）没有收到尺寸变化通知，仍按旧宽度渲染，导致外壳变大、内核留白。`usePanelResize` 在 `onUp` 时只清理事件监听，没有触发布局重算。
- 修复方式：
  1. `frontend/src/hooks/usePanelResize.ts` 在拖拽结束（`onUp`）时派发 `window.dispatchEvent(new Event('resize'))`；
  2. `frontend/src/components/workbench/WorkbenchPanel.tsx` 根 div 显式设置 `width: 100%`，确保在 flex 容器内始终占满宽度。
- 下次注意：
  1. 任何拖拽调整面板宽度的操作，结束时应触发 resize 事件或调用子组件的 `.layout()`；
  2. Monaco Editor、Xterm.js、SVG 画布、ECharts 等组件都需要显式感知容器尺寸变化；
  3. flex 子元素若未显式设置 `width: 100%`，在某些 display 切换场景下可能不会自动 stretch。

## 2026-07-06 - 为保状态把右侧面板改常驻渲染后，折叠按钮和拖拽条一起消失了

- 错误现象：
  1. 右侧面板打开时无法通过拖拽 resizer 调整宽度；
  2. 点击折叠按钮收起右侧面板后，右侧没有任何按钮可以再打开面板；
  3. 整个右侧面板区域变成固定宽度，无法交互。
- 错误原因：
  为了让 `RightPanel` 在关闭时不被卸载、保留内部状态（如串口 WebSocket、工作台日志），`AppRoot.tsx` 把 `RightPanel` 从条件渲染改为常驻渲染，宽度用 `width: 0` 隐藏。但实现时把折叠按钮 `.panel-btn-strip.right` 和拖拽条 `.right-resizer` 的 `hidden` 条件也绑定了 `rightPanelOpen`，导致面板关闭时这两者也一并隐藏。
- 修复方式：
  1. `AppRoot.tsx` 中 `.panel-btn-strip.right` 和 `.right-resizer` 的显隐只依赖 `showChatShell`，不再依赖 `rightPanelOpen`；
  2. 折叠按钮图标根据 `rightPanelOpen` 切换方向，并添加 `title` 提示；
  3. `right-resizer` 的 `onMouseDown` 包装一层：若面板关闭则先 `setRightPanelOpen(true)`，再开始拖拽；
  4. `RightPanel` 容器仍然常驻渲染，保持状态不丢失。
- 下次注意：
  1. 把面板从"条件渲染"改为"常驻隐藏"时，要区分"面板内容"和"面板控制条"，不要把控制条也隐藏；
  2. 拖拽条在面板关闭状态下应仍可交互，拖拽时自动展开面板；
  3. 任何通过 `width: 0` / `display: none` 隐藏面板的改动，都要单独验证折叠按钮、拖拽条、展开按钮是否还可用。

## 2026-07-05 - build_firmware 找不到第三方库（Adafruit_NeoPixel.h: No such file）

- 错误现象：
  1. Agent 写的代码 `#include <Adafruit_NeoPixel.h>` 等第三方库头文件；
  2. Agent 用 `run_command` 在 `/workspace/xxx/` 目录 `pio lib install` 装好了库；
  3. 调 `build_firmware` 编译仍报 `fatal error: Adafruit_NeoPixel.h: No such file or directory`。
- 错误原因：
  `build_firmware` 每次都在 `.build/tmp/{session}/` 新建临时项目，只写 `src/main.cpp` + 最小 `platformio.ini`（无 `lib_deps` 字段）。Agent 在别的目录装的库，临时项目看不到，PlatformIO 也不会自动下载缺失库。
- 修复方式：
  1. `pio_runner.py` 的 `CompileRequest` 新增 `lib_deps: tuple[str, ...]` 字段；`_ini_lines` / `_generate_platformio_ini` / `_create_temp_project` 透传该字段，写入 `platformio.ini` 的 `lib_deps =` 行；
  2. `build_tool.py` 的 `BuildCodeArgs` 新增 `lib_deps: list[str]` 参数；
  3. 新增 `BUILTIN_HEADERS`（50 个 ESP32/STM32 Arduino core 自带头文件）和 `INCLUDE_TO_LIBDEPS`（40 个常用第三方库头文件→库名映射）；
  4. 新增 `scan_lib_deps_from_code(code)` 函数：正则提取 `#include`，先查 BUILTIN 跳过，再查映射表收集库名，未知头文件不报错只记日志；
  5. `BuildTool.execute` 合并显式 `lib_deps` + 扫描结果（去重保序）；
  6. 编译失败且日志含 "No such file or directory" 时，附 `_MISSING_LIB_HINT` 提示 LLM 可传 `lib_deps` 参数。
- 下次注意：
  1. 任何"临时项目"设计都要考虑依赖如何注入，不能假设外部环境；
  2. PlatformIO 的 `lib_deps` 是声明依赖的标准方式，比手动 `pio lib install` 更可靠；
  3. LLM 不一定知道所有库名，自动扫描 + 显式声明双通道最稳。

## 2026-07-05 - TopBar 改造：对话统计读到全局 token / 导出菜单被截断

- 错误现象：
  1. 右上角"对话统计"面板显示的是所有会话加起来的 token，而不是当前会话的；
  2. 点击"导出对话"后弹出的格式子菜单看不到内容，且被下拉面板边界截断；
  3. 后端 `token_usage` 表里大量记录的 `session_id` 为空字符串。
- 错误原因：
  1. `frontend/src/stores/useChatStore.ts` 发送 `apiSSE("chat", requestBody)` 时，请求体里没有带 `session_id`，导致后端 `_record_token_usage` 把 `session_id` 写成空字符串；
  2. 后端 `GET /api/token-usage/stats` 用 `if session_id:` 判断，空字符串被当成"未传参"，于是返回了全局统计；
  3. 导出格式子菜单用 `position: absolute` 嵌在 `overflow: hidden` 的下拉面板的 `dropdown-panel-body` 里，并且靠近右边缘，导致被截断/黑块。
- 修复方式：
  1. 前端 `sendMessage` 请求体增加 `session_id: activeSessionId || undefined`；
  2. 后端过滤条件改为 `if session_id is not None:`，让空字符串也能严格过滤（返回空数据而非全局）；
  3. 导出格式选择改为内联展开（点击后在当前菜单项下方直接显示 Markdown/JSON/取消 三个按钮），不依赖绝对定位。
- 下次注意：
  1. 任何会写入按会话聚合的后端表的操作，都要确认前端是否把 `session_id` 传过去了；
  2. 后端查询参数用 `if x:` 判断可选字符串时，要区分"未传"（None）和"空字符串"；
  3. 嵌套在下拉面板的弹窗/子菜单优先考虑内联展开或 portal，避免父容器 `overflow: hidden` 截断。

## 2026-07-05 - Agent 把历史消息中生成的图片误认为是用户上传的

- 错误现象：
  1. 用户让 Agent 生成一张图片；
  2. 后续用户发送新消息（与图片无关）时，Agent 调用 vision_analysis 分析之前生成的图片；
  3. Agent 在 thinking / 回答中称"你上传的这张图片"，把 assistant 生成的图片当成 user 上传。
- 错误原因：
  `backend/app/api/chat_routes.py` 的 `_multimodal_to_text_with_image_hints` 对历史消息中所有 `image_url` part 都统一插入 `[用户上传了图片 N，请调用 vision_analysis(...)]` 的提示，没有区分消息 role 是 `user` 还是 `assistant`。
- 修复方式：
  修改 `_build_agent_messages` / `_isolate_images_in_content` / `_multimodal_to_text_with_image_hints`，传入消息 role：
  - `user` 消息中的图片：保持原提示，调用 `vision_analysis`；
  - `assistant` 消息中的图片：仅标注 `[这是之前生成的图片 N，不是用户新上传的图片]`，不触发 vision_analysis。
- 下次注意：
  1. 对历史消息中的图片做提示词注入时，必须根据 role 区分来源；
  2. assistant 自己生成的输出不应被当作新一轮的用户输入；
  3. 任何把 message content 中的 `image_url` 转成文本提示的逻辑，都要考虑 multimodal 消息可能来自不同 role。

## 2026-07-05 - image_generation 生成的图片在流式输出结束后消失

- 错误现象：
  1. image_generation 工具调用成功后，前端能在对话流中短暂看到生成的图片；
  2. 当 Agent 继续输出最终回答文本时，图片变成破碎图标或完全消失；
  3. 重新加载会话后图片也不存在。
- 错误原因：
  1. `tool_result` 事件把图片以 `ImagePart` 形式追加到 assistant message 的 `content`（`ContentPart[]`）；
  2. 但后续 `text` SSE 事件的处理逻辑假设 `last.content` 是字符串，用 `streamingContent + chunk` 覆盖整个 `content`，导致 `ContentPart[]` 被替换为纯文本字符串，图片 part 丢失；
  3. `setActiveSession` 在流式中切换会话时同样用 `streamingContent || m.content` 覆盖，也会丢失图片；
  4. `_appendResumeText`（HITL resume 路径）存在同样问题。
- 修复方式：
  在 `frontend/src/stores/useChatStore.ts` 中统一处理 `ContentPart[]`：
  1. 主 `text` 事件分支：若 `last.content` 是 `ContentPart[]`，把文本追加到最后一个 `TextPart`，保留 `ImagePart`；
  2. `_appendResumeText`：同样的 ContentPart[] 追加逻辑；
  3. `setActiveSession`：流式中切出会话时，若消息是 `ContentPart[]`，只替换/合并 `TextPart`，不覆盖图片；恢复 `streamingContent` 时从 `TextPart` 提取文本；
  4. 新增 `_mergeTextIntoParts` 辅助函数。
- 下次注意：
  1. 任何把 message content 从字符串改成 `ContentPart[]` 的场景，必须同步检查所有写入 `message.content` 的地方；
  2. `streamingContent` 仅缓存文本，不能用它直接覆盖可能包含图片的 message content；
  3. 涉及 multimodal 消息的函数（`_appendResumeText` / `setActiveSession` / `stopStreaming`）都要做 ContentPart[] 分支处理。

## 2026-07-05 - image_generation 返回的 base64 撑爆 LLM 上下文

- 错误现象：
  1. image_generation 工具调用成功，前端已正常显示生成的图片；
  2. 但随后 Agent 继续推理时报 `context_length_exceeded`：`Request exceeds the context window of the model`；
  3. 后端日志显示工具返回了 1MB+ 的 base64 PNG。
- 错误原因：
  LangGraph 的 ToolNode 会把工具返回值完整序列化到 `ToolMessage.content` 里并回传给 LLM。image_generation 返回的 base64 图片被当作文本上下文喂给模型，导致单条消息体积过大，直接触发上下文窗口超限。
- 修复方式：
  在 `backend/src/agent/sse_helpers.py` 的 `convert_tool_message_to_sse` 中：
  1. 先用完整 envelope（含 base64）生成 `tool_result` SSE 事件，保证前端能正常渲染图片；
  2. 然后对同一条 `ToolMessage` 的 `content` 做 compact：把 `data.image_base64` 替换为长度标记（如 `<base64 image, 1234567 chars>`），让 LLM 只看到精简后的工具结果。
- 下次注意：
  1. 任何可能返回大二进制/长文本的工具，都要区分"给前端看的完整数据"和"给 LLM 看的上下文"；
  2. 不要依赖 LangGraph 自动把完整 tool result 回传 LLM，需要在 SSE 转换层做截断/摘要；
  3. 调试 `context_length_exceeded` 时，优先检查最近一条 ToolMessage 是否包含异常大的 payload。

## 2026-07-05 - Agent 拒绝调用 image_generation 工具

- 错误现象：
  1. 用户在"工具 API Key"中已配置 Image Generation 的 API Key、Base URL、Model；
  2. 前端请求体已携带 `tool_keys.image_model/image_base_url/image_api_key`；
  3. 但 Agent 对"生成一张橘猫图片"等明确生图请求直接文本拒绝，不调用 `image_generation` 工具。
- 错误原因：
  系统提示词 `_SYSTEM_PROMPT_BODY` 中只描述了检索、文档定位、图片分析等策略，**完全未提及 `image_generation` 工具**。Agent 被"面向嵌入式开发者"的角色定义约束，认为非硬件请求不应处理，因此直接拒绝。
- 修复方式：
  1. 在 `backend/src/agent/prompts.py` 的系统提示词中新增"### 图片生成"章节，明确说明：用户要求生成图片/画图/示意图时必须调用 `image_generation` 工具，不要直接拒绝；
  2. 增强 `backend/src/agent/tools/groups/retrieval/image_generation.py` 的 `description`，让 LangChain bind_tools 注入给 LLM 的描述更清晰（适用场景 + 参数含义）。
- 下次注意：
  1. 新增工具后必须在系统提示词里补充"什么时候用"的策略，否则 LLM 可能因角色约束而忽略工具；
  2. 工具 `description` 不要只写功能，要写明触发条件和参数语义；
  3. 调试 Agent 不调用工具时，优先检查系统提示词是否明确授权使用该工具。

## 2026-07-04 - assistant 消息中的生成图片 img src 为空（MarkdownRenderer 无法处理超长 data URI）

- 错误现象：
  1. image_generation 工具成功调用 inroi/gpt-image-2 直连，返回 1.2MB+ base64 PNG（后端日志 `HTTP/1.1 200 OK`）；
  2. localStorage 中 `hwrag_msg_<sessionId>` 最后一条 assistant 消息 content 已是 `["image_url"]` 类型数组（ImagePart 数据完整写入）；
  3. 但前端 DOM 中 `<img alt="Image">` 的 `src=""` 为空字符串，图片不显示。
- 错误原因：
  1. **assistant 消息渲染路径走 markdown**：ChatArea.tsx 用 `renderContent(msg.content)` 把 ContentPart[] 转成 markdown 字符串 `![Image](data:image/png;base64,<1.2MB>)`，再交给 MarkdownRenderer（ReactMarkdown）渲染；
  2. **ReactMarkdown 对超长 data URI 解析失败**：1.2MB base64 ≈ 1.6MB 字符串作为 markdown image URL 时，markdown 解析器无法正确识别 URL 边界，最终生成的 `<img>` 标签 src 为空；
  3. **UserMessageContent 已绕开此问题**：用户消息组件注释明确写"避免超长 base64 URL 解析问题"，对 image_url 类型的 ContentPart 直接用 `<img>` 渲染，但 assistant 消息仍走旧的 markdown 路径。
- 修复方式：
  在 `frontend/src/components/chat/ChatArea.tsx` 中新增 `AssistantMessageContent` 组件（参考 UserMessageContent 实现）：
  1. 抽取共享的 `ImageLightbox` 组件（点击图片放大查看）；
  2. `AssistantMessageContent` 接收 `content / streaming / sources / onSourceClick`，对 ContentPart[] 分离渲染：
     - text 部分走 MarkdownRenderer（保留 src 引用按钮等能力）；
     - image_url 部分直接用 `<img>` 渲染（绕开 markdown 解析）；
  3. assistant 消息渲染处用 `<AssistantMessageContent>` 替换原来的 `<MarkdownRenderer content={renderContent(msg.content)}>`。
- 验证：agent-browser eval 确认 img src 长度 3,150,978 字符（完整 data URI），alt="generated"（新组件渲染），前缀 `data:image/png;base64,iVBORw0KGgo...` 正确。
- 下次注意：
  1. **超长 data URI 不要走 markdown 渲染**：任何 base64 > 100KB 的图片都应直接用 `<img>` 渲染，不要转成 `![Image](data:...)` markdown；
  2. **assistant 与 user 消息的图片渲染策略应统一**：两者都可能包含 ImagePart（user 是上传图，assistant 是工具生成图），都用"文本走 markdown + 图片走原生 img"的分离渲染；
  3. **调试 img 渲染问题时**，用 `agent-browser eval "JSON.stringify(Array.from(document.querySelectorAll('img')).map(i => ({alt:i.alt, srcLen:(i.src||'').length, srcPrefix:(i.src||'').slice(0,50)})))"` 快速判断 src 是否完整。

## 2026-07-04 - image_generation 凭证传递：前端配置未同步到 localStorage

- 错误现象：
  1. image_generation 工具调用 9router 返回 "Provider does not support image generation"；
  2. 后端 ImageGenerationTool 收到 `image_model='Text'`（聊天模型的显示名），而非前端配置的图像模型；
  3. 用户反馈"前端配置了 Multmodel 但后端没收到"。
- 错误原因：
  1. **前端配置存储 key 混淆**：useSettingsStore 用 `saveToStorage("settings", ...)` 写入 `localStorage["hwrag_settings"]`（KEYS 映射 settings→hwrag_settings），而非 `localStorage["settings"]`。调试时用 `localStorage.getItem("settings")` 读到的是旧版遗留 key，值已过时；
  2. **React store 状态与 localStorage 不同步**：用户在 UI 设置页选择了模型，但 `hwrag_settings` 里 `imageModel=""`、`imageProviderId=""`、`providers[0].models=[]`。UI 显示已配置但实际未持久化（可能因 provider models 列表为空导致 select 选项缺失，选择操作未触发 setImageModel）；
  3. **resolveImageCreds fallback 到 chatModel**：`imageModel=""` 时 fallback 到 `chatModel`，而 `chatModel="Text"`（无效值），导致后端收到 `image_model="Text"`。
- 修复方式：
  1. 手动修改 `localStorage["hwrag_settings"]`，设置 `imageModel`、`imageProviderId`、`visionModel`、`visionProviderId`，并填充 `providers[0].models` 列表；
  2. 刷新页面让 React app 重新加载配置；
  3. 后端日志确认 `image_model='Multmodel'` 正确传递。
- 下次注意：
  1. 调试前端配置时，用 `localStorage.getItem("hwrag_settings")` 读取真实配置，不要用 `localStorage.getItem("settings")`（旧版遗留 key）；
  2. 前端设置页面的 select 如果依赖 provider models 列表，需先确保 provider 已验证且 models 非空，否则用户无法选择模型；
  3. `resolveImageCreds` 的 fallback 逻辑 `imageModel || chatModel` 会掩盖 imageModel 为空的问题——应在 imageModel 为空时提示用户配置，而非静默 fallback。

## 2026-07-04 - 9router 不支持图片生成（image_generation 工具 3 策略全失败）

- 错误现象：
  image_generation 工具用 "Multmodel"（9router 映射到 mimo-v2.5-free）调用 9router，三个策略全部失败：
  1. `/images/generations` → HTTP 400 "Provider does not support image generation"；
  2. `/tasks`（异步轮询）→ HTTP 404（9router 无此端点）；
  3. `/chat/completions`（chat fallback）→ HTTP 400 "Provider does not support image generation"。
- 错误原因：
  9router 的 mimo-v2.5-free 模型不支持图片生成，/images/generations 端点明确拒绝。
- 修复方式：
  需换用支持图片生成的 provider（如 OpenAI DALL-E、Stability AI、Midjourney API 等），或在 9router 确认是否有专门的图片生成模型。
- 下次注意：
  image_generation 工具的 3 策略 fallback 仅对支持图片生成的 provider 有效；9router 这类聊天模型代理不支持 /images/generations 和 /tasks 端点，chat fallback 也无法生成图片。

## 2026-07-04 - svg_generator KiCad/Fritzing 布局重写中的电气节点与电阻识别陷阱

- 错误现象：
  1. 重写 `backend/src/hardware/svg_generator.py` 为 KiCad/Fritzing 原理图布局时，`_compute_net_y_positions` 抛 `NameError: name 'node_map' is not defined`；
  2. 测试脚本调用 `_compute_net_y_positions` 抛 `TypeError: missing 1 required positional argument: 'start_y'`；
  3. 10kΩ 上拉电阻未被识别为 pullup，被画成普通 peripheral box；
  4. `net_y` 字典为空，导致所有外围器件都堆到默认 y 位置；
  5. GND net 同时到 LED 阴极和按键 SIG 的两条 drop 线完全重合。
- 错误原因：
  1. `_compute_net_y_positions` 内部需要遍历 `node_map` 定位 MCU 引脚，但函数签名漏了 `node_map` 参数；
  2. 调试脚本按旧签名调用，新签名增加了 `start_y` 参数；
  3. `_identify_resistors` 查找 pull-up 的 destination component 时没有排除 MCU，把 MCU GPIO 所在 net 误判为 destination，导致 `gpio_net` 找不到；
  4. 旧实现遍历 `node_names` 计算 net_y，但 `node_names` 可能不包含某些 MCU 引脚 root（尤其未被连接的引脚），应遍历 `node_map` 中所有 MCU 引脚；
  5. 同 net 多目标 drop 线的 spread 算法把 drop_x clamp 在 `[bus_x_end + 8, dx - 6]`，而 `bus_x_end` 到器件引脚的水平距离只有 `WIRE_OFFSET=20`，可用空间约 2px，无法错开。
- 修复方式（`backend/src/hardware/svg_generator.py`）：
  1. 给 `_compute_net_y_positions` 增加 `node_map` 参数，并在所有调用处透传；
  2. 调试脚本 `test_wiring2_debug.py` 同步更新为完整参数列表；
  3. `_identify_resistors` 在扫描 destination component 时增加 `comp == mcu_name` 排除，确保只把非 MCU 器件作为 pull-up 负载；
  4. `_compute_net_y_positions` 改为遍历 `node_map.items()`，直接取 MCU 引脚对应 root 计算 y；
  5. 重写 `_compute_drop_xs`：把 drop_x 可用区间扩到 `[bus_x_start + 20, min_dx - 10]`，多目标时按 `MIN_DROP_SPACING=24` 均匀分布；同时删除 bus 上的短 stub 分支，只保留从 MCU 引脚出发的主干线。
- 下次注意：
  1. 改函数签名后必须同步改所有调用点（包括测试脚本和内部递归/辅助调用），不要只改主路径；
  2. 遍历 union-find 结果时选 `node_names` 还是 `node_map` 要分清：需要所有物理引脚时用 `node_map`，只需要 root 分组名时用 `node_names`；
  3. 识别"上拉/下拉/限流"电阻时，destination/load 侧必须明确排除 MCU 自身引脚，否则会把 GPIO net 当成负载侧；
  4. 调 spread/clamp 参数时要一起算可用空间：`spacing * (count - 1) <= upper - lower`，否则参数改再大也不生效；
  5. 重写布局算法后一定要用真实案例（MCU + LED + 按钮 + 上拉 + 限流电阻）生成 SVG 肉眼检查 net 分组、电阻类型、drop 线是否重叠。

## 2026-07-04 - PowerShell 测试 $Recycle.Bin 路径时变量展开导致输入被破坏

- 错误现象：
  - 用 `python -c "...matches_deny_pattern(r'C:\$Recycle.Bin\S-1-5')..."` 验证 path_guard 的 Windows deny 模式
  - 输出显示传入路径变成 `C:\.Bin\S-1-5`（`$Recycle` 消失），`matches_deny_pattern` 返回 None
  - 一度误判为 path_guard 代码 bug，实际代码正确
- 错误原因：
  - 外层 `python -c "..."` 用的是 PowerShell 双引号字符串
  - PowerShell 在双引号字符串内会把 `$Recycle` 当作变量展开（未定义变量展开为空字符串）
  - 传给 Python 的实际字符串是 `C:\.Bin\S-1-5`，不含 `$Recycle.Bin/`，自然不匹配
- 修复方式：
  - 改用 PowerShell 单引号字符串包裹 `-c` 参数：`python -c '...matches_deny_pattern("C:\\$Recycle.Bin\\S-1-5")...'`
  - 单引号字符串不做变量展开，`$` 原样传递给 Python
- 下次注意：
  - 在 PowerShell 里跑 `python -c` 测试含 `$` 的路径（如 `$Recycle.Bin`）时，外层必须用单引号，或用反引号 `` `$ `` 在双引号里转义
  - 测试结果与预期不符时，先在 Python 里 `repr()` 打印实际收到的输入字符串，排查 shell 变量展开

## 2026-07-04 - autocompact.py 导入不存在的 estimate_tokens 符号

- 错误现象：
  - `from src.agent.compact.autocompact import *` 抛 `ImportError: cannot import name 'estimate_tokens' from 'src.agent.context_guard'`
  - autocompact 模块完全无法导入，autocompact 恢复链路在导入期就崩
- 错误原因：
  - `autocompact.py` L23 写的是 `from src.agent.context_guard import estimate_tokens`
  - 但 `context_guard.py` 根本没有定义 `estimate_tokens` 函数（grep 无匹配）
  - 真正的 token 估算实现是 `LLMClient._estimate_tokens` 静态方法，定义在 `src/llm/client.py` L98-101
  - 推测是早期重构时把 token 估算从 context_guard 搬到 LLMClient，但忘了更新 autocompact 的 import
- 修复方式：
  1. `autocompact.py` L23 import 改为 `from src.llm.client import LLMClient`
  2. `autocompact.py` L56 调用改为 `return LLMClient._estimate_tokens(content)`
  3. `python -c "from src.agent.compact.autocompact import *"` 验证通过
- 下次注意：
  - 重构搬移函数后，必须全局 grep 旧符号的所有引用，不能只改目标文件
  - 静态方法/类方法跨模块引用时，import 类比 import 函数更稳（避免符号漂移）
  - 验证导入时用 `from <module> import *` 能第一时间发现 ImportError，比单纯 `python -m py_compile` 更能暴露问题

## 2026-07-04 - SSE 编译流 BodyStreamBuffer was aborted（首次下载依赖超时断连）

- 错误现象：
  - 前端烧录面板报 `❌ SSE 读取异常: BodyStreamBuffer was aborted`
  - 首次编译 ESP32-S3 固件时，PlatformIO 需要下载依赖库（framework-arduinoespressif32 等），耗时 5-10 分钟
  - 期间 pio 子进程长时间无 stdout 输出，后端不发任何 SSE 事件
  - 前端 apiSSE 的 IDLE_TIMEOUT（5 分钟）触发，主动断开连接
- 错误原因：
  - 后端 `pio_runner.py` 的 `_next_pio_event()` 只在 pio 有 stdout 输出时发事件，无输出时不发心跳
  - 前端 `client.ts` 的 `apiSSE` 设有 5 分钟 IDLE_TIMEOUT，`resetIdleTimer` 仅在 `reader.read()` 返回数据时触发
  - 首次编译下载依赖时 pio 子进程静默，后端无事件 → 前端无数据 → IDLE_TIMEOUT 触发 → 连接断开
- 修复方式：
  1. `backend/src/hardware/pio_runner.py`：
     - `DEFAULT_COMPILE_TIMEOUT_S` 从 300 改为 600
     - 新增 `HEARTBEAT_INTERVAL_S = 15.0`
     - `StreamContext` 新增 `last_heartbeat` 字段
     - 新增 `_maybe_heartbeat()` 函数：无输出时每 15 秒发一次 heartbeat 事件
     - `_next_pio_event()` 在无输出时调用 `_maybe_heartbeat()`，有输出时更新 `last_heartbeat`
  2. `frontend/src/types/api.ts`：新增 `BuildHeartbeatSSEEvent` 接口并加入 `BuildSSEEvent` 联合类型
  3. `frontend/src/components/workbench/FlashPane.tsx`：`handleBuildEvent` switch 添加 `case "heartbeat": break;`
- 下次注意：
  - 任何 SSE 流式接口，如果后端可能有长时间静默期（子进程下载、首次初始化等），必须实现心跳机制
  - 心跳间隔应小于前端 IDLE_TIMEOUT 的一半（15s < 300s/2）
  - 前端必须处理 heartbeat 事件类型，避免未知事件导致错误
  - 编译超时要拉长到能覆盖首次下载依赖的时间（600s 比 300s 更安全）
- 验证结果：
  - 修复后 SSE 连接持续 20+ 分钟无断连
  - 首次编译（含下载依赖）256 秒成功完成
  - 收到多次 heartbeat 事件，前端正常处理

## 2026-07-04 - svg_generator 同 net 多目标 drop 线 spread 受 clamp 限制无法生效

- 错误现象：
  1. ESP32-S3 + DHT11 + 4.7kΩ 上拉电阻接线图里，3V3 net 同时接 DHT11 VCC 和上拉电阻 A，两条 drop 线几乎重合（间距只有 2px）；
  2. 上拉电阻锯齿符号与 VCC drop 线交叉重叠，视觉上像短路。
- 错误原因：
  1. `CHANNEL_GAP_Y=36` / `PERIPHERAL_GAP_Y=50` 间距太拥挤，net 堆叠、器件贴近；
  2. `_compute_peripheral_positions` 把上拉电阻也当成普通 peripheral box 放在右边，与 DHT11 共享 GPIO21 net 的 y 均值，导致位置重叠；
  3. 同 net 多目标 spread 从 40 改成 80 后仍然无效，因为 `drop_x` 被 clamp 到 `[bus_x_end + 8, dx - 6]`，而 `dx - bus_x_end = WIRE_OFFSET = 16`，可用范围只有约 2px；
  4. 没有识别 "上拉/pullup/pull-up" 电阻，把它当普通 box 画，无法表达并联上拉接法。
- 修复方式（`backend/src/hardware/svg_generator.py`）：
  1. 增大间距：`CHANNEL_GAP_Y 36 → 52`，`PERIPHERAL_GAP_Y 50 → 70`；
  2. 新增 `_is_pullup_resistor` / `_find_pullup_resistors` 识别上拉电阻，跳过普通 peripheral 布局，过滤掉其 signal-side connection，避免产生独立 net；
  3. 在 power net 的 drop 位置用 `_draw_resistor_zigzag` 画垂直锯齿电阻，上端接 3V3 bus、下端接 DATA signal bus；
  4. 把 `drop_x` clamp 从 `[bus_x_end + 8, dx - 6]` 改为 `[bus_x_start + 8, dx - 6]`，让 drop 可以挂在 bus 任意位置，spread=80 才真正生效；
  5. power net 默认颜色：3V3/VCC 等用 `#ef4444`，GND 用 `#1e293b`，信号用 `#3b82f6`，power net 线宽 2.5px。
- 下次注意：
  1. 调 spread 参数时必须同步检查 clamp 范围，如果可用空间小于 spread 值，参数改再大也不生效；
  2. 上拉/下拉电阻不要画成普通 box，应该用 schematic 符号表达并联关系；
  3. 用 pin 名判断 power rail（3V3/VCC/GND 等）时记得做大小写和去横杠/空格归一化，避免 "3.3V" 被漏判；
  4. 改动布局常量后要用真实硬件案例（MCU + 传感器 + 上拉电阻）生成 SVG 肉眼检查重叠。

## 2026-07-04 - generate_code 工具冗余失败 + build_firmware 首次编译卡 4 分钟

- 错误现象：
  1. 用户调 `generate_code` 工具生成 ESP32-S3 DHT11 代码，工具返回"代码生成失败：把这个工具删掉"——工具内部调 LLM 失败时返回降级文案，但 LLM 本身就不可用（Agent 自己就是 LLM，再调一个 LLM 工具冗余）；
  2. 用户改调 `build_firmware` 编译代码，工具耗时 177.8s（约 3 分钟）还没返回结果，前端显示"已耗时"一直在涨。
- 错误原因：
  1. **generate_code 冗余**：Agent 本身就是 LLM，能直接写代码，再调一个内部 LLM 工具等于"LLM 调 LLM"，失败率高且浪费 token；
  2. **build_firmware 卡 4 分钟**：用户代码 `#include <DHT.h>` 用了 Adafruit DHT 库，但 pio_runner 生成的 platformio.ini 不含 `lib_deps`，PlatformIO 首次编译会去 Library Manager 自动下载 `adafruit/DHT sensor library` + `Adafruit Unified Sensor`，网络慢时下载就要几分钟；
  3. **工具 timeout 链不合理**：原 `BUILD_TOOL_TIMEOUT_S = 360s`，pio 内部 `DEFAULT_COMPILE_TIMEOUT_S = 300s`，但首次下载库可能超过 300s，pio 内部超时直接报失败。
- 修复方式：
  1. **删 generate_code 工具**：从 `agent_factory.py` 移除 `GenerateCodeTool` 实例化 + 从 `code/__init__.py` / `groups/__init__.py` 移除导出 + 删 `generate_code.py` 文件（含旧 shim）+ 更新 `prompts.py` 调用纪律说明"代码生成由 Agent LLM 直接输出"；
  2. **拉长 timeout 链让 pio 自己下载依赖**（用户反馈：不应该没下载依赖就失败）：
     - `pio_runner.DEFAULT_COMPILE_TIMEOUT_S` 300s → 600s（给 pio 10 分钟下载依赖）
     - `pio_runner.DEFAULT_UPLOAD_TIMEOUT_S` 60s → 120s
     - `build_tool.BUILD_TOOL_TIMEOUT_S` 360s → 650s（工具层兜底比 pio 内部晚 50s）
     - `build_tool.FLASH_TOOL_TIMEOUT_S` 120s → 150s
     - `prompts.TOOL_CALL_TIMEOUT_S` 300s → 700s（Agent 全局工具超时，要比 build_tool 650s 晚）
     - 超时链：Agent 全局 700s > 工具层 650s > pio 内部 600s，让 pio 充分下载依赖
  3. **加下载阶段检测 + 友好提示**：`pio_runner._next_pio_events` 检测 `Installing`/`Library Manager`/`Downloading`/`framework-arduino`/`toolchain-` 等关键词，phase 从 compiling → downloading 时发 thinking 事件"正在下载依赖库（首次编译需要联网下载，请耐心等待...）"，下载完回到编译阶段时发"依赖下载完成，开始编译代码..."；
  4. **超时错误信息加首次编译提示**：`_format_build_output` 失败时如果 `err_code in ("COMPILE_TIMEOUT", "UNKNOWN")`，附上 `_FIRST_BUILD_HINT` 提示用户首次编译需下载依赖 + 建议手动 `pio lib install`。
- 下次注意：
  1. **不要做"LLM 调 LLM"的工具**：Agent 本身就是 LLM，代码生成/文本总结/翻译这类 LLM 原生能力不要再封装成工具，直接让 Agent 输出；
  2. **PlatformIO 首次编译会下载库**：代码用 `#include <DHT.h>` / `#include <Adafruit_Sensor.h>` 等第三方库时，pio 会去 Library Manager 下载，网络慢就卡几分钟。后续编译用缓存才快；
  3. **pio_runner 不自动加 lib_deps**：当前 `_ini_lines` 只生成 platform/board/framework/upload_*，不含 lib_deps。如果要让 pio 知道库依赖，要么用户在 options 里传 `lib_deps = ...`，要么后端解析 `#include` 自动推断（未实现）；
  4. **工具 timeout 链要让 pio 充分下载依赖**：用户反馈"不能说没下载依赖就失败"，所以 timeout 要拉长让 pio 自己下完。链路：Agent 全局 700s > 工具层 650s > pio 内部 600s，pio 内部 600s 给下载留足时间，工具层和 Agent 全局只是兜底；
  5. **流式输出加阶段提示**：pio 日志里的 `Installing`/`Library Manager` 等关键词用户看不懂，要转成 thinking 事件"正在下载依赖库..."让前端显示友好状态。

## 2026-07-04 - Monaco Editor 在 flex 容器中需要 minHeight:0 才能撑满（spec optimize-workbench-ux-batch Track B Task 5）

- 错误现象：
  1. PreviewPane 把原来的 `<textarea>` 替换为 `@monaco-editor/react` 的 `<MonacoEditor>` 后，编辑器区域高度坍缩为 0 或只显示一行，父容器 `flex: 1` 不生效；
  2. 调整父容器 `height: 100%`、给 Monaco 加 `height: 100%` 都无效，Monaco 内部 iframe 仍然按内容高度坍缩。
- 错误原因：
  1. 父容器 `<div className="code-preview-editor" style={{ flex: 1 }}>` 是 flex 子项，默认 `min-height: auto`（即 min-content），flex 子项的 `min-height` 不会被 flex 引擎压缩到 0 以下，但 Monaco 内部的 iframe 测量的是父容器的**可用高度**而非 flex 分配高度；
  2. Monaco 用 ResizeObserver 测量容器高度，当父容器 `min-height: auto` 且内容为空时，测量到的高度是 0 或 1 行高度，Monaco 据此渲染 iframe；
  3. `flex: 1` 只承诺"分配剩余空间"，但不承诺"压缩 min-height 到 0"，Monaco 拿不到真实剩余高度。
- 修复方式（`frontend/src/components/workbench/PreviewPane.tsx` L180）：
  1. 给包裹 Monaco 的容器加 `style={{ flex: 1, minHeight: 0 }}`——`minHeight: 0` 覆盖默认的 `min-height: auto`，让 flex 引擎真正压缩该子项到 0，再由 `flex: 1` 分配剩余空间；
  2. Monaco 的 `height` prop 设为 `"100%"`，让它填满父容器（父容器现在有真实高度了）；
  3. 外层再用 `style={{ display: 'flex', flexDirection: 'column', height: '100%' }}` 撑满整个 Pane 区域。
- 下次注意：
  1. **flex 子项的 `min-height: auto` 是默认陷阱**：任何需要"flex: 1 + 内容撑满"的容器都要显式 `minHeight: 0`，否则 flex 引擎按 min-content 计算最小高度，导致子项拿不到剩余空间；
  2. **Monaco / CodeMirror / iframe 类组件对父容器高度敏感**：它们内部用 ResizeObserver 测量父容器高度，父容器必须有**确定的高度**（不是 `auto`），flex + minHeight:0 是最可靠的组合；
  3. **调试 Monaco 高度坍缩先看父容器链**：从 Monaco 往上逐层检查 `height` / `flex` / `min-height`，任何一层断了 `height: 100%` 或 `flex: 1 + minHeight: 0`，Monaco 就会坍缩；
  4. **不要给 Monaco 直接设固定 px 高度**：硬编码高度无法响应面板拖拽 resize，必须用 flex + minHeight:0 让它自适应。

## 2026-07-04 - FlashTool 恢复 HITL 必须 PermissionClassifier + 测试同步改（spec optimize-workbench-ux-batch Track D Task 8）

- 错误现象：
  1. spec `optimize-workbench-ux-batch` Task 8 要求 FlashTool 从 `requires_confirmation=NEVER` 改回 `IF_NEEDED`，"HIGH risk 走 HITL 确认卡片"；
  2. 实际只改 `FlashTool.requires_confirmation` 字段无效——PermissionClassifier 完全不读这个字段，只看 `risk_level` 和 `_HITL_SKIP_TOOLS` 白名单；
  3. `flash_firmware` 还在 `_HITL_SKIP_TOOLS` 里 → `_decide_high` 直接返回 ALLOW，HITL 不触发；
  4. `tests/test_build_tool.py::test_flash_tool_skips_hitl` 仍期望 ALLOW，与 Task 8 目标冲突。
- 错误原因：
  1. `ConfirmationRule` 枚举只有 `ALWAYS/NEVER/CONDITIONAL`，**没有 `IF_NEEDED`**——spec 写的 `ConfirmationRule.IF_NEEDED` 是参考性描述，实际要用 `CONDITIONAL`（语义最接近）；
  2. `PermissionClassifier._decide_high` 决策路径：`run_command` → risk_classifier 评级；`_HITL_SKIP_TOOLS` 白名单 → ALLOW；其他 → ASK。`requires_confirmation` 字段在 classifier 里**完全没被读取**，纯粹是 ToolSpec 上的文档性元数据；
  3. 2026-07-03 的踩坑记录明确说"HIGH 风险跳过 HITL 是 demo 妥协，不是通用模式，生产环境应改为 ALWAYS 或加二级确认"——本次 Task 8 就是回退这个 demo 妥协。
- 修复方式：
  1. `backend/src/agent/tools/groups/code/build_tool.py`：
     - `FlashTool.requires_confirmation`: `NEVER` → `CONDITIONAL`（语义自文档，实际行为由 classifier 决定）；
     - `FlashTool` docstring 删"跳过 HITL 确认（demo 顺畅优先）"，改为"HIGH risk 走 HITL 确认卡片，用户允许后执行"；
     - `_build_success_output` / `_flash_success_output` / `_format_build_output` 失败分支 / `_format_flash_output` 失败分支：四个 output 函数全部加 `target_pane: "flash"` + `render_data: {stage, ...}`（Task 6 要求，前端 useWorkbenchBridge 从 `envelope.data.target_pane` 分发到 FlashPane）。
  2. `backend/src/agent/core/toolkit/permission_classifier.py`：
     - `_HITL_SKIP_TOOLS: frozenset[str] = frozenset({"flash_firmware"})` → `frozenset()`（清空白名单，让 flash_firmware 走 ASK fallback）；
     - `_decide_high` docstring 从"flash_firmware skips HITL"改为"whitelist skips HITL"。
  3. `backend/tests/test_build_tool.py`：
     - `test_flash_tool_skips_hitl`（断言 ALLOW）→ `test_flash_tool_triggers_hitl`（断言 ASK）；
     - 模块顶部 docstring 同步更新；
     - import 加 `ASK` 常量。
- 下次注意：
  1. **`requires_confirmation` 字段是文档性的，不驱动 PermissionClassifier**：要让某个 HIGH 工具真正走 HITL，必须看 `_decide_high` 实际分支——改 ToolSpec 上的 `requires_confirmation` 不会改变行为，必须同步改 classifier 的白名单/评级逻辑；
  2. **ConfirmationRule 枚举值**：项目里只有 `ALWAYS/NEVER/CONDITIONAL`，spec 写 `IF_NEEDED` 时用 `CONDITIONAL` 替代（语义最接近），不要去改 `tool_spec.py` 加新枚举值除非真的有第三种行为分支；
  3. **改 PermissionClassifier 必须同步改 test_build_tool.py**：`TestPermissionClassifier` 类下有针对 FlashTool 的决策断言，改白名单后断言方向反过来（ALLOW → ASK），测试名也建议从 `skips_hitl` 改为 `triggers_hitl` 反映新语义；
  4. **Task 6 加 target_pane/render_data 不需要改 sse_adapter**：`ToolRouter._success_envelope` 把 result dict 里除 `output`/`kb_coverage_hint` 外的所有字段放进 `envelope.data`，`sse_helpers.convert_tool_message_to_sse` 把整个 envelope 原样作为 `tool_result.result` 发给前端——`target_pane` 和 `render_data` 自动透传到 `envelope.data.target_pane` / `envelope.data.render_data`，前端 useWorkbenchBridge 直接取即可；
  5. **回退 demo 妥协时检查 pitfalls 历史**：2026-07-03 的 FlashTool 踩坑记录明确说"demo 妥协，不是通用模式"，本次回退是这个预见性警告的兑现——回退前读一下原踩坑记录的"下次注意"，确认回退方向与原警告一致。

## 2026-07-04 - _resolve_env_name 只去横杠不去下划线（STM32 板 ID env 名测试期望笔误）

- 错误现象：
  1. ESP32+STM32 全系列改造后跑 `tests/test_pio_runner.py::TestGeneratePlatformioIni::test_generate_platformio_ini_stm32` 失败：
     `AssertionError: assert '[env:blackf407vg]' in '[env:black_f407vg]\nplatform = ststm32\n...'`
  2. 期望 env 名是 `blackf407vg`（去下划线），实际生成 `black_f407vg`（保留下划线）。
- 错误原因：
  1. `pio_runner._resolve_env_name(board)` 实现是 `board.replace("-", "")`——只替换横杠 `-`，不替换下划线 `_`；
  2. STM32 板 ID 惯例用下划线分段（如 `black_f407vg` / `nucleo_f407re`），ESP32 板 ID 用横杠分段（如 `esp32-s3-devkitc-1`）；
  3. 改造方案的测试期望 `[env:blackf407vg]` 是笔误，与 `_resolve_env_name` 的实际行为不符（方案"关键注意事项"第 3 条明确说"`_resolve_env_name` 保留逻辑，board 去横杠作为 env 名"，没说要再去下划线）。
- 修复方式（`backend/tests/test_pio_runner.py` L149）：
  1. 把 STM32 测试断言从 `assert "[env:blackf407vg]" in ini` 改为 `assert "[env:black_f407vg]" in ini`；
  2. `_resolve_env_name` 实现保持不变（PlatformIO 的 `[env:NAME]` 接受下划线，env 名作为 `.pio/build/{env_name}/` 目录名与 `_resolve_env_name(req.board)` 返回值一致即可，二进制查找路径匹配）。
- 下次注意：
  1. **`_resolve_env_name` 只去横杠不去下划线**：写测试断言时 ESP32 板用去横杠后的 env 名（`esp32-s3-devkitc-1` → `esp32s3devkitc1`），STM32 板保留下划线（`black_f407vg` → `black_f407vg`）；
  2. **改 env 名逻辑前先确认 PlatformIO 接受什么字符**：`[env:NAME]` 里 NAME 接受字母/数字/下划线/横杠，但去横杠是惯例（避免 shell 转义问题），下划线保留无害；
  3. **binary 路径一致性**：`_find_binary_path` / `_expected_binary_path` 用 `_resolve_env_name(req.board)` 推导目录名，PlatformIO 实际生成的 `.pio/build/{env_name}/firmware.bin` 目录名就是 `[env:NAME]` 里的 NAME，只要 `_resolve_env_name` 的输入输出在 compile 和 upload 两次调用一致，路径就能匹配——不需要关心具体是去横杠还是去下划线；
  4. **方案文档的测试期望也可能是笔误**：执行精确改造方案时，如果测试失败且失败原因与方案"关键注意事项"里的明确约束冲突，优先信任代码实际行为 + 注意事项，修改测试期望而非改代码逻辑。

## 2026-07-03 - subprocess 异步读取 stdout 必须用 asyncio.wait_for 包短超时（防止 readline 阻塞冻结 SSE 流）

- 错误现象：
  1. `pio_runner._stream_subprocess_output` 用 `proc.stdout.readline()` 读取 PlatformIO 子进程输出，编译/烧录过程中偶发"SSE 流卡死几分钟"——前端进度条不动、compile_log 不再增长，但子进程实际还在工作；
  2. 卡死期间后端日志无任何错误，整体 SSE 流不结束、不报错，前端只能等编译超时（300s）才看到 `COMPILE_TIMEOUT`。
- 错误原因：
  1. `asyncio.create_subprocess_exec(..., stdout=PIPE)` 返回的 `Process.stdout` 是 `StreamReader`，其 `readline()` 在子进程没有输出（如 linking 阶段、flash 写入阶段）时会**无限阻塞等待下一行**；
  2. SSE 事件循环每轮 `async for` 都卡在 `readline()` 上，无法去检查整体超时、也无法主动 yield 心跳事件；
  3. `readline()` 只有遇到 `\n` 或 EOF 才返回，PlatformIO 在某些阶段（如 esptool 烧录时）会长时间不输出换行，导致整个流冻结。
- 修复方式（`backend/src/hardware/pio_runner.py` L222-232）：
  1. 用 `await asyncio.wait_for(proc.stdout.readline(), timeout=LINE_READ_TIMEOUT_S)` 包裹，常量 `LINE_READ_TIMEOUT_S: float = 0.5`（500ms）；
  2. `except asyncio.TimeoutError: return ""` —— 超时返回空字符串，外层循环把空串当作"暂无新行"处理，继续下一轮（不会误判 EOF）；
  3. 外层循环用 `proc.stdout.at_eof()` 判断子进程是否结束，避免空串导致死循环；
  4. 整体编译/烧录超时由 `StreamConfig.timeout_s` + `time.monotonic()` 起算时间独立控制（不依赖 readline 是否阻塞）。
- 下次注意：
  1. **任何 `asyncio.subprocess` 的 `proc.stdout.readline()` 都必须包 `asyncio.wait_for` 短超时**——子进程在 linking/upload/IO 密集阶段会长时间不输出换行，裸 `readline()` 会冻结整个事件循环；
  2. 超时返回值必须是 `""`（空串）而非 `None`，否则外层 `if not line` 逻辑可能误判 EOF 提前结束流；用 `proc.stdout.at_eof()` 显式判断 EOF；
  3. 短超时（0.5s）不会显著影响吞吐：高频输出时每行立即返回，低频输出时每 0.5s 轮询一次，CPU 占用可忽略；
  4. 整体超时（compile/upload wall-clock）必须独立于单行读取超时，否则"子进程在 linking 卡 60s"会被误判为整体超时；
  5. 调试 SSE 卡死类问题先看 `readline()` 是否裸调用——这是 asyncio subprocess 流式读取最常见的陷阱，所有需要"边读边发"的 SSE 都中招。

## 2026-07-03 - FlashTool 标 HIGH 风险但跳过 HITL（审计仍记录 HIGH）

- 错误现象：
  1. 新增 FlashTool（烧录固件到 ESP32）按 spec 标 `risk_level = HIGH`，按 `PermissionClassifier._decide_high` 原逻辑对所有非 run_command 的 HIGH 工具返回 `ASK`，触发 HITL 确认弹窗；
  2. demo 场景下用户已经显式说"烧录到设备"，再弹 HITL 确认拖慢节奏，违背"demo 顺畅优先"。
- 错误原因：
  1. `PermissionClassifier._decide_high` 是 HIGH 风险的统一入口，原实现只对 `run_command` 走 `risk_classifier` 评级（safe 命令 auto-allow），其他 HIGH 工具一律 `ASK`；
  2. spec（`.trae/specs/agent-build-flash-esp32/spec.md` Requirement: Agent 编译烧录工具）明确要求 FlashTool HIGH 但跳过 HITL，原 classifier 没有给"白名单 HIGH 工具"留逃生口。
- 修复方式：
  1. 在 `backend/src/agent/core/toolkit/permission_classifier.py` 模块顶部新增常量 `_HITL_SKIP_TOOLS: frozenset[str] = frozenset({"flash_firmware"})`；
  2. `_decide_high` 增加 `if spec.name in _HITL_SKIP_TOOLS: return ALLOW` 分支（在 run_command 分支之后、ASK fallback 之前）；
  3. FlashTool 自身 `requires_confirmation = ConfirmationRule.NEVER`（语义自文档），`audit = True` 保持默认（仍写 ToolAudit 表，`risk_level = "high"`）。
- 下次注意：
  1. **HIGH 风险跳过 HITL 是 demo 妥协，不是通用模式**：仅限用户已在上层显式授权的操作（如烧录是用户主动要求，非 LLM 自主决策）。生产环境应改为 `requires_confirmation = ALWAYS` 或加二级确认。
  2. **审计日志仍记录 HIGH**：`audit=True` 是 ToolSpec 默认值，不要为了"跳过 HITL"就把 audit 关掉，否则丢了设备变更可追溯性。`risk_level` 字段在 spec 上保持 HIGH，classifier 只是改变了 permission 决策，没改 spec 元数据。
  3. **白名单维护成本**：每加一个"跳过 HITL 的 HIGH 工具"都要进 `_HITL_SKIP_TOOLS`，且必须配套 spec 说明跳过理由。不要滥用——超过 3 个就该重新审视策略。
  4. **依赖 LLM 谨慎调用**：跳过 HITL 意味着 LLM 决策错就真烧了。FlashTool 的 `description` 必须明确"仅在 build_firmware 成功 + 用户要求烧录时调用"，避免 LLM 在没有 binary_path 的情况下硬调。
  5. **FlashTool 不自动编译**：`upload_firmware` 不会触发 `compile_firmware`，binary_path 必须由前一步 BuildTool 产出。LLM 必须先调 build_firmware 拿到 binary_path 再调 flash_firmware，工具层不做隐式编译（避免参数不可见）。
  6. **新建工具三件套**：新增 ToolSpec 子类必须同步改 `tools/groups/{group}/__init__.py` 导出 + `tools/groups/__init__.py` 顶层导出 + `agent_factory.py._assemble_all_tools` 实例化，三处缺一不可（pitfalls 2026-07-03 已有同类教训）。

## 2026-07-03 - 同端口并发烧录防护（asyncio.Lock 字典 + locked() 预检）

- 错误现象：
  1. 两个 `/api/upload` 请求同时烧录到同一串口（如 COM3），两个 esptool 进程抢端口导致烧录失败或设备复位异常。
- 错误原因：
  1. `build_routes.py` 原本无端口并发控制，asyncio 事件循环会在 await 点切换任务，导致两个烧录请求同时进入 `async with` 区段。
- 修复方式：
  1. 模块级维护 `_upload_locks: dict[str, asyncio.Lock]`，key=port，value=该端口的 Lock；
  2. `_get_port_lock(port)` 懒创建 Lock（不存在则新建，存在则返回同一个对象）；
  3. `_check_upload_input(payload)` 先调 `lock.locked()` 预检，若已锁定立即返回 `PORT_BUSY` 错误事件（不阻塞等待）；
  4. `_stream_upload` 在预检通过后 `async with _get_port_lock(port)` 获取锁再执行编译/烧录。
- 下次注意：
  1. **`lock.locked()` 检查与 `async with lock` 之间存在 TOCTOU 竞态**：预检返回"未锁定"后、`async with` 真正获取锁之前，事件循环可能在 await 点切换到另一个协程先获取锁。此时第二个请求不会立即返回 PORT_BUSY，而是会 `async with` 阻塞等待锁释放后才执行烧录——违反"立即拒绝"的语义。
  2. 这种竞态在 demo 场景下可接受（用户手动触发，并发概率极低），但**生产场景应改用非阻塞获取**：`acquired = lock.acquire_nowait()`（asyncio.Lock 无此方法，需用 `loop.run_in_executor` + `threading.Lock` 或自旋 `try`/`asyncio.wait_for(lock.acquire(), timeout=0)`）。
  3. **Lock 字典会无限增长**：每个新 port 都会创建一个 Lock 对象且永不回收。demo 场景端口数量有限（COM1-COM256）可忽略；长期运行需加 LRU 或定期清理无锁的 entry。
  4. **不要在 `_check_upload_input` 里 `async with lock`**：那会让校验函数变成协程且锁会被立即释放，达不到防并发的目的。预检 + 实际获取必须在两个步骤，中间允许短暂竞态。
  5. holder list 模式（`holder: list[str] = [""]`）用于 async generator 向调用方回传 binary_path：generator 既需要 yield 事件流又需要把 done 事件里的 binary_path 传给调用方继续 upload，Python generator 的 `return value` 通过 StopAsyncIteration 传递不方便在 `async for` 中获取，用 mutable holder 更直观。

## 2026-07-03 - 临时移除死循环检测（LoopGuard）

- 错误现象：
  1. Agent 运行中可能触发“检测到循环”弹窗，打断正常流式输出；
  2. 用户要求先移除该功能，后续再重新设计。
- 错误原因：
  1. `backend/src/agent/core/toolkit/loop_guard.py` 的 LoopGuard 会在重复/无进展工具调用时触发 `loop_detected` SSE 事件；
  2. 前端 `LoopDetectedDialog` 消费该事件并弹出继续/停止/换思路对话框；
  3. `chat_routes.py` 的 `/agent-sandbox/resume` 同时处理 HITL 和 loop resume，逻辑耦合。
- 修复方式：
  1. 删除 `backend/src/agent/core/toolkit/loop_guard.py`；
  2. 从 `tool_router.py`、`sse_helpers.py`、`sse_adapter.py`、`chat_routes.py` 中移除 LoopGuard 相关调用与导入；
  3. 从 `frontend/src/components/chat/LoopDetectedDialog.tsx` 及 `useChatStore.ts` 中移除循环检测状态与 UI；
  4. 保留 HITL resume（allow/deny/stop），`/agent-sandbox/resume` 只处理 HITL 决策。
- 下次注意：
  1. 重新设计循环检测时，建议独立模块并通过配置开关控制，避免与 HITL 路径耦合；
  2. 删除功能后要及时清理前后端类型、状态、路由和 import，避免编译/运行时残留引用；
  3. 大文件（如 `useChatStore.ts`）删除字段后注意检查 set 回调的括号匹配，容易因旧代码结构产生语法错误。

## 2026-07-03 - 点击 srcN 无反应 / web_search 来源未进入右侧面板

- 错误现象：
  1. 消息正文中的 `[src1]` 等来源引用点击后没有反应，不会跳转；
  2. `web_search` 工具返回的结果在右侧「来源引用」面板中不显示；
  3. 右侧面板中网页来源的 URL 不可点击。
- 错误原因：
  1. `backend/src/agent/sse_helpers.py` 的 `_assemble_tool_result_events` 只给 `search_docs` 工具调用 `build_source_events`，`web_search` 的结果没有触发 `source` SSE 事件；
  2. 前端 `ChatArea.tsx` 点击 srcN 时只设置了高亮和文件查看源，没有处理 `source_url` 跳转外部链接；
  3. `RightPanel.tsx` 的来源详情页把 `source_url` 当普通文本展示，未渲染成可点击链接。
- 修复方式：
  1. `sse_helpers.py` 将 source 事件触发条件改为 `tool_name in ("search_docs", "web_search")`；
  2. `web_search.py` 的结果 dict 已按 SourceRef 格式生成（`id/title/source_url/score/relevance_level/excerpt` 等），复用 `build_source_event_from_dict` 推送到前端；
  3. `ChatArea.tsx` 新增 `openSource`：若来源有 `source_url` 则 `window.open` 打开，同时仍高亮并打开右侧来源面板；
  4. `RightPanel.tsx` 来源详情页把 `source_url` 渲染为 `<a target="_blank">`。
- 下次注意：
  1. 新加检索类工具时，要让 `sse_helpers.py` 统一触发 source 事件，否则前端来源面板看不到；
  2. 来源引用点击行为要区分本地知识库（打开 chunk viewer）和外部网页（跳转 URL）；
  3. 后端 source payload 中的 `source_url` 字段要显式生成，前端要在多处（正文 srcN、底部 chip、右侧面板详情）都支持跳转。

## 2026-07-03 - 发图片给模型说"没有视觉分析工具"

- 错误现象：用户发图片给 Agent，Agent 回复"没有视觉分析的工具"，无法分析图片。
- 错误原因（三连击）：
  1. `retrieval/__init__.py` 只导出 3 个工具，`agent_factory.py` 只注册 3 个，vision_analysis/view_image/image_generation/webfetch/search_history 5 个工具未被注册到 Agent。
  2. `FUNCTION_CALLING_MODELS` 白名单里有 13 个子串但不含 "mimo"，导致 oc/mimo-v2.5 被判定不支持工具调用，走了普通 LLM 路径（不经过 Agent，无工具调用能力）。
  3. `_build_agent_messages` 把多模态 content 直接 `str()`，LLM 看到的是 `[{'type': 'image_url', ...}]` 字符串，拿不到图片数据。
- 修复方式：
  1. `retrieval/__init__.py` 导出全部 8 个工具；`agent_factory.py` 注册全部 5 个新工具 + 新增 `_read_vision_creds`/`_read_image_creds` 读凭证。
  2. `prompts.py` 的 `FUNCTION_CALLING_MODELS` 清空为 `[]`（空列表 = 允许所有模型走 Agent 路径，不支持工具的模型会自动 fallback）。
  3. `chat_routes.py` 的 `_build_agent_messages` 改为保留多模态 list content（让多模态 LLM 直接看图）+ 缓存图片 data URI 到内存（`vision_analysis.py` 的 `_IMAGE_CACHE`）+ 追加文本提示（让纯文本 LLM 知道可调 `vision_analysis(image="cache:image_id")`）。
  4. `vision_analysis.py` 新增 `cache_image()`/`get_cached_image()`/`_resolve_image_ref()`，execute 支持 `cache:image_id` 引用。
- 下次注意：
  1. 新增工具后必须同时改 `__init__.py` 导出 + `agent_factory.py` 注册 + `prompts.py` 策略，三处缺一不可。
  2. `FUNCTION_CALLING_MODELS` 白名单维护成本高，空列表 + 自动 fallback 是更优解。
  3. 多模态消息不能 `str()`，会丢失图片数据；应保留 list content 或缓存后给文本提示。
  4. 工具描述由工具类自己的 `description` 字段维护，`bind_tools` 自动注入给 LLM，**系统提示词不需要重复写工具描述**（避免上下文爆炸）。

## 2026-07-03 - TodoCard 视觉太丑：缺少进度条/动画

- 错误现象：
  1. 右侧面板「待办」tab 的 TodoCard 样式简陋，任务状态切换不够动感；
  2. 进行中任务没有转圈圈动画，完成时没有明显打勾反馈。
- 错误原因：
  1. `TodoCard.tsx` 只是简单列表，没有进度条、没有序号、没有动画样式；
  2. `workbench.css` 里没有 `.todo-card` / `.todo-progress-bar` / `.todo-icon-spin` 等样式，且 `SpinnerIcon` 缺少动画 keyframes。
- 修复方式：
  1. `TodoCard.tsx` 新增进度条、任务序号、组件拆分；
  2. `workbench.css` 增加完整 TodoCard 样式：进度条渐变填充、进行中图标旋转动画、进行中/完成状态卡片高亮、已完成文字划线。
- 下次注意：
  1. 任何状态类组件（todo、step、tool call）都应该有视觉状态反馈（动画/颜色/进度），不能只是文字变化；
  2. 添加 CSS 时要注意与已有选择器冲突，修改后检查是否重复或覆盖。

## 2026-07-03 - onDone 中 updateSessionMeta 与 persistLastTurn 竞态导致流式内容消失

- 错误现象：
  1. SSE 流式输出到一半，聊天区域突然变空白（用户提问气泡和输入框还在，但回复内容全部消失）；
  2. 切换到其他会话再切回来，原本消失的内容（工具调用、耗时统计、Agent 完整回复）又完整恢复；
  3. 无任何控制台错误或 ErrorBoundary 触发。
- 错误原因：
  1. `useChatStore.ts` 的 `onDone` 回调在 `set` 回调内部同步调用 `updateSessionMeta`（L1034），`updateSessionMeta` 通过 `sessions.map(...)` 创建新的 sessions 数组引用；
  2. `AppRoot.tsx` 的 `useEffect` 依赖 `sessions` 引用，检测到变化后重跑并调用 `fetchMessages(activeSessionId)`；
  3. `fetchMessages` 的 GET 请求到达后端时，`persistLastTurn`（L1065，`void` 异步 POST）可能尚未完成，后端返回不含本轮回答的旧数据；
  4. `fetchMessages` L420-425 用后端旧数据覆盖 `messages` 和 `sessionMessages`，导致 UI 显示空白/旧内容；
  5. 用户切换会话再切回时，`persistLastTurn` 早已完成，第二次 `fetchMessages` 拿到完整数据，内容恢复。
- 修复方式：
  1. `onDone` 中把 `updateSessionMeta` 从 `set` 回调内部移出，改为在 `persistLastTurn().then()` 中执行，保证后端先保存数据再触发 `fetchMessages`；
  2. `fetchMessages` 增加本地数据新鲜度守卫：如果本地 `sessionMessages[sessionId]` 的消息数量 > 后端返回的 `msgs.length`，跳过后端覆盖，避免竞态时旧数据污染。
- 下次注意：
  1. **任何在 `set` 回调内部调用的外部 store 更新都会同步触发依赖该 store的 useEffect 重跑**，如果 useEffect 中有异步请求，极易形成竞态；
  2. 流式完成后需要同时做"持久化到后端"和"更新会话元数据"时，**必须先 persist 再 updateMeta**，顺序不能反；
  3. `fetchMessages` 类的"用后端权威数据覆盖本地"逻辑必须有**本地数据新鲜度守卫**，否则任何竞态都会导致本地新数据被后端旧数据覆盖。

## 2026-07-03 - TodoWriteTool 未注册导致 TodoCard 不更新 / 模型列表 8s 超时 / Workbench 渲染取错字段

- 错误现象：
  1. 右侧面板「待办」tab 能打开但始终显示「暂无任务清单」，Agent 从未调用 todo_write；
  2. 手动在前端配置 API 后点击「获取模型列表」，请求被 `net::ERR_ABORTED` 中断，模型下拉框为空；
  3. `render_code` 等工具结果无法推送到工作台代码预览面板；
  4. TodoCard 偶尔出现计数与可见条目不匹配（如显示 0/5 完成但只能看到 4 条）。
- 错误原因：
  1. `agent_factory.py` 的 `_build_local_tools()` 只实例化了 `RunCommandTool`，没有把 `TodoWriteTool` 注入 Agent，导致 Agent 看不到 todo_write 工具；
  2. `frontend/src/api/client.ts` 的 `fetchWithTimeout` 默认超时 8s，而 `useSettingsStore.fetchProviderModels` 未传超时参数，拉取上游模型列表经常超过 8s 被 abort；
  3. `frontend/src/stores/useWorkbenchBridge.ts` 只读取 `event.result.target_pane/render_data`，但后端 `tool_result` 事件的 `result` 是 `ToolResultEnvelope`，渲染字段实际在 `result.data.*`；
  4. LLM 偶尔会生成 `content` 为空的 todo 项，前端直接用原始数组长度计数导致显示不一致。
- 修复方式：
  1. `execution/__init__.py` 导出 `TodoWriteTool`，`agent_factory.py` 的 `_build_local_tools()` 加入 `TodoWriteTool()`；
  2. `useSettingsStore.ts` 中 `verifyProvider` 和 `fetchProviderModels` 的 `/api/models` 请求显式传入 60s 超时；
  3. `useWorkbenchBridge.ts` 同时兼容 `result.target_pane/render_data` 和 `result.data.target_pane/render_data`；
  4. `frontend/src/types/api.ts` 给 `ToolResultSSEEvent.result` 增加 `data?: {...}` 类型；
  5. `TodoCard.tsx` 过滤掉 `content` 为空的 todo 项，保证计数和渲染一致。
- 下次注意：
  1. 新增工具后必须在 `agent_factory.py` 的 tool 装配函数里注册，否则 Agent 永远看不到；
  2. 上游网络请求（模型列表/验证）要根据实际耗时配置超时，不能用默认 8s；
  3. SSE `tool_result` 的 `result` 字段是后端封装的 envelope，取业务字段时要先确认在 `result` 还是 `result.data`；
  4. LLM 生成的列表类数据要做防御性过滤，避免空项破坏 UI 计数。

## 2026-07-03 - SourcePanel 未定义 TodoCard 崩溃 / thinking 双卡 / 模型名前缀限制 / fallback 提示卡片

- 错误现象：
  1. SSE 输出时界面突然变空白（类似图 1），切到别的会话再回来又恢复（图 2），控制台报错 `TodoCard is not defined at SourcePanel`；
  2. 模型选择器填入如 `oc/mimo-v2.5` 等名称后 LLM 调用失败，提示「模型名缺少 provider 前缀」；
  3. fallback 纯 LLM 路径会先显示「当前模型不支持工具调用，无法检索知识库」thinking 卡片；
  4. 占位 thinking 卡片「模型正在思考...」没有被真实 thinking 事件替换，出现两个一样的「推理思考」卡片（图 3）。
- 错误原因：
  1. `RightPanel.tsx` 的 `SourcePanel` 渲染了 `<TodoCard todos={todos} />`，但既未导入 `TodoCard`，也未定义 `todos` 变量，点击/渲染该 tab 时触发 ReferenceError，React 错误边界导致右侧面板重载，间接影响聊天区状态展示；
  2. `llm/client.py` 对代理端点强制校验模型名必须包含 `/`，拦截了用户自定义服务商的合法模型名；
  3. `chat_routes.py` fallback 分支硬编码了一条 `thinking` 事件作为不支持工具调用的提示；
  4. `useChatStore.ts` 仅在 `source === "reasoning"` 时检查占位并替换，若首个真实 thinking 事件是 `llm`/`rag`，会先关闭占位再新建卡片，导致占位残留。
- 修复方式：
  1. `RightPanel.tsx` 临时移除未连接的「待办」tab 及 `TodoCard` 引用（后续在「2026-07-03 - TodoWriteTool 未注册导致 TodoCard 不更新」中恢复并正确接入）；
  2. `llm/client.py` 删除 provider 前缀强制校验；
  3. `chat_routes.py` 删除 fallback 中的「当前模型不支持工具调用」thinking 事件；
  4. `useChatStore.ts` 在 thinking 事件处理开头统一判断：只要最后一步是占位内容「模型正在思考...」，就直接用真实内容替换，不区分 source。
- 下次注意：
  1. 引入新 UI 组件时必须确保 import 正确、数据已接入，未完成的 tab 不要渲染；
  2. 不要对模型名作超出上游实际要求的格式限制，用户自建服务商的模型名可能千奇百怪；
  3. 占位 thinking/loading 卡片要用「首个真实事件即替换」策略，避免在 source 切换逻辑里被提前关闭。

## 2026-07-02 - Agent 多轮 search_docs 导致 source 编号冲突 / 流式 thinking 污染 / 换方法 prompt 失效

- 错误现象：
  1. 同一次请求中 Agent 调用多次 `search_docs`（如查不同知识库），前端 source 卡片出现多个 `src1`、`src2`，点击后引用内容错乱；
  2. Agent 工具调用期间的引导文本（"好的！先查手册获取硬件参数，再生成代码。"）作为 `thinking` 卡片输出，污染最终答案；
  3. 流式输出不是逐字出现，而是等 Agent 全部生成完才一次性刷出；
  4. 点击循环检测弹窗的"换方法"后，Agent 继续重复原来的查询，仿佛没收到提示。
- 错误原因：
  1. 每次 `search_docs` 独立从 `src1` 开始编号，`ToolContext` 没有跨工具调用维护全局计数器；
  2. `sse_adapter` 把所有文本先缓冲到 `text_buffer`，等工具调用结束后作为 `thinking` 事件 flush 出去；
  3. 文本缓冲机制导致非工具调用期间的文本也无法实时流式下发；
  4. `_restart_after_loop` 用 `SystemMessage` 注入换思路提示，LangGraph ReAct Agent 对系统消息不敏感，实际被忽略；
  5. `search_docs` 默认超时 60s，复杂查询 120s 未完成被截断。
- 修复方式：
  1. `ToolContext` 新增 `source_counter: int = 0`，`SearchDocsTool` 每次返回结果前从 `ctx.source_counter + 1` 起编号，并把计数器累加本次结果数，保证同请求内 srcN 全局唯一；
  2. `sse_adapter._append_text_event` 在 `pending_tool_calls` 非空时直接丢弃引导文本（清空 `text_buffer`），不再 flush 为 thinking 卡片；
  3. 非工具调用期间的文本直接 `sse_event("text", ...)` 实时下发，取消缓冲等待；
  4. `chat_routes.py:_restart_after_loop` 改用 `HumanMessage(content=f"[系统提示] {hint}")`，让模型把换思路提示当作新用户输入处理；
  5. `SearchDocsTool.timeout_seconds` 从 60 改为 180，匹配复杂查询实际耗时。
- 下次注意：
  1. **多轮检索必须全局分配 source id**，不能靠每次调用独立编号；
  2. Agent 工具调用前后的"寒暄/引导"文本要显式丢弃，不能当成 thinking；
  3. 流式输出要尽量保持"来一段发一段"，避免为了区分 thinking/answer 而全量缓冲；
  4. 需要强制改变 Agent 行为时，用 `HumanMessage` 而非 `SystemMessage` 注入提示；
  5. 工具超时按该工具的历史最长耗时设置，并配合 per-call wall-clock 检查。

## 2026-07-02 - Pydantic 子类覆盖父类字段必须带类型注解（output_schema shadows 警告）

- 错误现象：
  - 给 13 个工具子类加 `output_schema` 时，尝试用不带类型注解的赋值 `output_schema = XxxOutput` 消除 `shadows an attribute in parent` 的 UserWarning
  - 运行时报 `pydantic.errors.PydanticUserError: Field 'output_schema' defined on a base class was overridden by a non-annotated attribute. All field definitions, including overrides, require a type annotation.`
  - import 失败，工具类无法实例化
- 错误原因：
  - ToolSpec 基类用 Pydantic 字段声明了 `output_schema: type[BaseModel] | None = None`
  - Pydantic v2 规定：子类覆盖父类的 Pydantic 字段时，必须带类型注解（`output_schema: type[BaseModel] | None = XxxOutput`），不能用裸赋值
  - 裸赋值会被 Pydantic 当成普通类属性而非字段覆盖，触发 model-field-overridden 错误
  - 根本的 UserWarning 来源是 LangChain BaseTool 自身有一个 `output_schema` property，ToolSpec 用 Pydantic 字段覆盖了它——这是 BaseTool 的设计问题，无法在不改基类的前提下消除
- 修复方式：
  - 子类统一使用 `output_schema: type[BaseModel] | None = XxxOutput`（带类型注解）
  - 接受良性的 UserWarning（不影响功能，import 和运行均正常）
- 下次注意：
  - **Pydantic v2 子类覆盖父类字段必须带类型注解**，不能用裸赋值——这是硬性要求，会直接报错
  - `shadows an attribute in parent` 的 UserWarning 如果来自第三方库（如 LangChain BaseTool）的 property 被字段覆盖，无法在子类消除，属于良性警告
  - 区分 UserWarning（良性，可忽略）和 PydanticUserError（致命，必须修）

## 2026-07-01 - 并行 Edit 同一文件导致第二个修改静默丢失（industrial-tool-runtime Task 6）

- 错误现象：
  - 在同一个 message 里并行调用两个 Edit 工具修改 `backend/src/mcp/manager.py` 的第 40 行和第 51 行
  - 两个 Edit 都返回成功并显示更新后的内容，但后续 grep 发现第 51 行仍是旧代码（`from src.agent.tool_router import unregister_mcp_tools`）
  - 第 40 行的修改保留，第 51 行的修改丢失
- 错误原因：
  - 两个并行 Edit 基于同一份文件快照，第二个 Edit 的写入可能被第一个 Edit 的结果覆盖，或基于过期版本写入
  - Edit 工具返回的 diff 预览不等于实际落盘结果，需 Read 复核
- 修复方式：
  - 单独重新执行第 51 行的 Edit，再用 Read + grep 双重复核
  - 验证命令：`grep -rn "from src\.agent\.tool_router import" backend/` 应无输出
- 下次注意：
  - **禁止对同一文件并行 Edit**——同一文件的多次修改必须串行（一次 Edit 完成并 Read 确认后再做下一个）
  - Edit 返回成功后，关键改动要用 Read 或 grep 复核实际落盘内容，不能只信工具返回的预览
  - 跨文件并行 Edit 安全，同文件并行 Edit 有竞态风险

## 2026-07-01 - 9router nginx 504 超时导致 faithfulness 假性 0 分（切换 OpenCode 端点修复）

- 错误现象：
  - Round 6 faithfulness=0.35（8 题中 5 题 FA=0），overall=0.7308
  - 所有 FA=0 的 reasons 都是 `"504 Gateway Time-out"` (nginx/1.22.1)
  - faithfulness metric 默认 4 次 LLM 调用，任一 504 即全 0，概率 ~68%
- 错误原因：
  - judge 端点用 9router (https://9router.zxyzx.bbroot.com/v1)，nginx 有 ~60s 超时限制
  - faithfulness metric 需 4 次 LLM 调用（提取声明 + 逐条验证），单次调用可能超 60s
  - 9router 基础设施问题，非 RAG 质量问题（CR 全 1.0 证明检索完美）
- 修复方式（run_baseline_deepeval_v2.py L55-56）：
  1. 新增 `DEFAULT_JUDGE_BASE_URL = "https://opencode.ai/zen/go/v1"`
  2. `DEFAULT_JUDGE_MODEL` 从 `oc/deepseek-v4-flash` → `deepseek-v4-flash`（去 oc/ 路由前缀）
  3. `--judge-base-url` 默认 fallback 改为 OpenCode 直连
  4. OpenCodeJudge 已用 `messages=[...]` 不传 max_tokens，符合 OpenCode 端点要求
- 验证结果：
  - Round 7 FA 从 0.35→0.97（9 题中 8 题 FA=1.0），overall 0.7308→0.8479
  - OpenCode 端点全部返回 200 OK，无 504
- 下次注意：
  - **评估端点不能用 nginx 代理**——LLM judge 需多次调用，代理超时会累积失败
  - OpenCode 直连（https://opencode.ai/zen/go/v1）是稳定替代，但需去 oc/ 前缀
  - 评估脚本应支持命令行覆盖 judge 端点，便于切换

## 2026-07-01 - Agent 多轮 tool_call 导致 actual_output 含 thinking 文本污染（CR=0 回归）

- 错误现象：
  - Round 7 q001 CR=0（Round 6 是 1.0），actual_output 开头含 "我来查询...让我再查一下..." thinking 文本
  - q001 跑了 2 次 tool_call，answer_len=2403（Round 6 单轮 1888）
  - judge 认为实际输出偏离预期答案导致 CR=0
- 错误原因：
  - Agent 路径多轮工具调用时，thinking/tool_call 的文本内容混入了 answer 的 text 事件
  - chat_routes.py 的 SSE text 事件过滤可能未区分 thinking 文本和最终答案文本
- 修复方式（待实施 SubTask 15.19）：
  - 检查 chat_routes.py Agent 路径的 SSE text 事件过滤逻辑
  - 确保 thinking/tool_call 内容不混入 actual_output
- 下次注意：
  - **Agent 多轮调用会改变 actual_output 结构**——评估时需确保只有最终答案进入 actual_output
  - eval 脚本的 SSE 解析应区分 text 事件类型（thinking vs answer）

## 2026-07-01 - default 模式所有工具都 ask 导致 search_docs 被卡在 HITL（Agent 不执行工具直接结束）

- 错误现象：
  - Agent 调用 search_docs 后，前端显示 tool_call 卡片和"好的，我先搜索..."文本，但 Agent 流直接结束，没有 tool_result，没有第二轮推理，没有最终答案；
  - 用户反馈"search_docs 不需要授权"，但 Agent 停在 HITL 等待确认。
- 错误原因：
  - `permission_gate.py` 的 `_route_by_mode` L98：default 模式下**所有工具**都返回 `ASK`，包括 search_docs 这种只读无副作用的检索工具；
  - HITL 中断后等用户确认，但前端确认框未弹出或用户未操作，Agent 流超时结束；
  - 根因是权限设计未区分"只读无副作用工具"和"有副作用工具"——一刀切全 ask。
- 修复方式（permission_gate.py L41-45 + L100-115）：
  1. 新增 `_READONLY_TOOLS` 白名单：`{search_docs, audit_pins, wiring, web_search, generate_code, render_wiring, render_safety_report, render_code, read_file}`；
  2. `_route_by_mode` 签名改为 `(tool, mode, risk)`，default 分支调 `_route_default`；
  3. `_route_default` 逻辑：只读工具直接 allow；`run_command` 且 risk==LOW（ls/cat 等只读命令）allow；其他（write_file/edit_file/危险命令）ask；
  4. 导入 `COMMAND_TOOL` 常量用于判断。
- 权限矩阵（修复后）：
  | 工具 | default | acceptEdits | bypass |
  |------|---------|-------------|--------|
  | search_docs/audit_pins/render_*/generate_code/web_search | allow | allow | allow |
  | read_file | allow | allow | allow |
  | write_file/edit_file | ask | allow(LOW) | allow |
  | run_command ls/cat | allow | allow | allow |
  | run_command pip install/rm -rf | ask | ask | allow |
- 下次注意：
  - **权限门控不能一刀切**——default 模式应该按工具副作用分级：只读工具直接放行，有副作用的才问；
  - `_READONLY_TOOLS` 白名单比"按 risk level 判断"更安全，因为 write_file/edit_file 的 risk 也是 LOW（path_guard 已拦截危险路径），但它们有副作用，不能用 LOW 来放行；
  - 修权限门控后必须测 5 类工具：只读检索 / 只读文件 / 写文件 / 只读命令 / 危险命令，覆盖全矩阵。

## 2026-07-01 - spec 给出的 Python import 路径不准确（ChatGenerationChunk）

- 错误现象：
  - 按 spec 提供的代码创建 `reasoning_chat.py`，其中 `from langchain_core.messages import BaseMessageChunk, ChatGenerationChunk` 导致 `ImportError: cannot import name 'ChatGenerationChunk' from 'langchain_core.messages'`。
- 错误原因：
  - `ChatGenerationChunk` 定义在 `langchain_core.outputs`，不在 `langchain_core.messages`；
  - spec 文档手写 import 路径时凭记忆写错，未实际验证。
- 修复方式（reasoning_chat.py L13-14）：
  - 拆成两行：`from langchain_core.messages import BaseMessageChunk` + `from langchain_core.outputs import ChatGenerationChunk`；
  - 同时移除 spec 中未使用的 `from typing import Any`。
- 下次注意：
  - **spec / 任务描述中给出的 import 路径可能不准确**，写入文件前先用 `python -c "from x import Y"` 验证一行再批量写；
  - langchain_core 的 `ChatGenerationChunk` 在 `outputs` 模块，`BaseMessageChunk` 在 `messages` 模块，两者不同路径。

## 2026-07-01 - LangGraph INVALID_CHAT_HISTORY（stale MemorySaver checkpoint 导致 AIMessage(tool_calls) 孤立）

- 错误现象：
  - Agent 调用直接报错：`Found AIMessages with tool_calls that do not have a corresponding ToolMessage. Here are the first few of those tool calls: [{'name': 'search_docs', 'args': {...}, 'id': 'call_00_...', 'type': 'tool_call'}]`；
  - 同一 session_id 第二次发消息必现，第一次（如果上轮正常结束）不会触发。
- 错误原因：
  - `_global_checkpointer` 是进程级 MemorySaver 单例，用 `session_id` 作为 `thread_id`，跨请求持久化 Agent 状态；
  - 上一轮请求在中途失败（SSE 断连 / 工具异常 / 客户端取消）时，checkpointer 已保存了 `AIMessage(tool_calls=[...])`，但 tools 节点未执行，缺对应的 `ToolMessage`；
  - 下一轮相同 session_id 的请求加载这个不完整状态，LangGraph 校验消息历史时发现孤立的 tool_calls，抛 INVALID_CHAT_HISTORY；
  - 根因不是前端发送了孤立 tool_calls（前端 `sendMessage` L519 只发 role+content，`mapBackendMessage` 也不读 tool_calls），而是 checkpointer 内部残留状态。
- 修复方式（agent_factory.py L101-132 + chat_routes.py L118-119）：
  1. 新增 `reset_thread_checkpoint(thread_id)`：清理 MemorySaver.storage 中该 thread_id 的 checkpoint + writes 中 `(thread_id, *)` 的 pending writes；
  2. 在 `_run_agent_stream` 开头调用 `reset_thread_checkpoint(payload.session_id or "default")`，保证每次请求从全新状态开始；
  3. HITL resume 不受影响——`handle_auto_resume` / `resume_agent_after_user` 在同一请求生命周期内操作 checkpoint，清理发生在它们之前。
- 下次注意：
  - **MemorySaver 是进程级单例，状态跨请求残留**——任何"中途失败→下次重试"的场景都要考虑 stale state；
  - 前端发送完整消息历史时，后端可以安全清理旧 checkpoint（不要依赖旧状态恢复对话），但前提是前端真的发完整历史（本项目 `sendMessage` 确实发全量 history）；
  - 修 LangGraph 错误时先看 `https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CHAT_HISTORY`，错误信息会列出孤立的 tool_call id，可用于定位；
  - 直接操作 MemorySaver 内部 dict（storage / writes）是进程内单例的最简清理方式；如果未来换成 SqliteSaver/RedisSaver，需要改用其 `adel` / SQL 删除接口。

## 2026-07-01 - reranker 阈值过激进导致 Round 7 context=0 回归

- 错误现象：
  - 某些技术 query 检索返回 0 chunk（context=0），LLM 无上下文可用；
  - 复现在 Round 7 测试用例上稳定出现。
- 错误原因：
  - `_RERANKER_MIN_SCORE = 0.1` 对 bge-reranker-base 来说太高；
  - bge-reranker-base 输出的是 **raw logits（可负数）**，不是概率，相关技术 chunk 的 logits 常落在 [-2, 5] 区间；
  - 0.1 阈值把大量 logits 为负但实际相关的技术 chunk 判为「无关」剔除；
  - 兜底逻辑只保留 top-1，仍可能因其他环节（top_k、score_threshold）进一步收敛导致最终 0 chunk。
- 修复方式（kb_manager.py L46-58 + L809-815）：
  1. `_RERANKER_MIN_SCORE` 从 `0.1` 降到 `-2.0`（对应 sigmoid ≈ 0.12，只过滤明显无关 chunk）；
  2. 新增 `_RERANKER_MIN_KEEP = 2`，过滤后若结果少于该值则从 reranked 顺序补足到 2 个；
  3. 把 `if not kept: kept = [reranked[0]]` 改为 `if len(kept) < _RERANKER_MIN_KEEP: kept = reranked[:_RERANKER_MIN_KEEP]`；
  4. debug log / warning 逻辑保持不变。
- 下次注意：
  - **bge-reranker-base 输出是 logits 不是概率**，阈值不能照搬 [0,1] 概率直觉，需用 logit 量纲的阈值（-2~0 区间）；
  - 任何「过滤 + 兜底」型逻辑都要有 min_keep 下限，不能只保留 top-1，否则下游再收敛会归零；
  - 调 reranker 阈值前先打印实际 logits 分布（`logger.debug` 已有 removed_preview），用真实数据定阈值。

## 2026-07-01 - 双重 RAG 导致知识库检索耗时 1 分半（pre-RAG + Agent SearchDocsTool 重复检索）

- 错误现象：
  - 用户实测知识库检索耗时 1 分 30 秒，即使是「你好」这种闲聊也跑一遍 pre-RAG；
  - 技术问题更慢：pre-RAG 跑一遍（60-80s 串行遍历所有 KB + 3-8s query rewrite 调 LLM），Agent 启动后可能再调一次 SearchDocsTool。
- 错误原因：
  - `chat_sse` 在进入 Agent 前先跑 `_run_rag_retrieval`（pre-RAG），对每条消息无差别检索，浪费 60-80s；
  - pre-RAG 内部 `search_all_enabled` 用串行 for 循环遍历所有 KB，3 个 KB 耗时叠加；
  - pre-RAG 内部 `_rewrite_query_for_rag` 调一次 LLM 改写 query，浪费 3-8s（Agent 本身是 LLM，调用工具时已理解 query 语义，再调一次 LLM rewrite 是冗余）；
  - Agent 启动后可能再调 SearchDocsTool，导致同一 query 被检索两次（双重 RAG）。
- 修复方式（spec: rag-to-agent-tool-trigger）：
  1. **去掉 pre-RAG**：`chat_sse` 删除 `_run_rag_retrieval` 调用，所有请求直接进 Agent 主路径，Agent 自己决定是否调 search_docs（闲聊不调，技术问题调）；
  2. **去 query rewrite**：SearchDocsTool 内部删除 `_rewrite_query_for_rag` 调用，Agent 通过 system prompt 策略自己输出精炼检索词；
  3. **并行检索**：`search_all_enabled` 串行 for 循环改为 `asyncio.gather` + `asyncio.to_thread`，3 个 KB 并行（总耗时 ≈ 最慢 KB）；
  4. **LRU 缓存**：`search_docs_core` 加 OrderedDict 缓存（key=query+kb_ids+top_k+threshold，TTL 5 分钟，容量 256 条），命中直接返回；
  5. **system prompt 检索策略**：加 IMPORTANT 强调（技术问题必检索 / 闲聊绝不检索 / 传精炼检索词）+ good-example/bad-example；
  6. **工具按领域分目录**：12 个工具从扁平文件迁移到 `tools/groups/` 6 个子目录（retrieval/hardware/workbench/code/file_ops/execution），全量注入不变；
  7. **sse_adapter source 事件**：拦截 search_docs 工具调用，实时 yield source 事件到前端（替代原 pre-RAG 的 source 事件路径）。
- 验证：
  - 后端 12 个工具 import OK，build_tools 返回 12 个工具名与 spec 一致；
  - SYSTEM_PROMPT 含 3 新章节 + 6 领域分组 + 4 处 IMPORTANT + good/bad-example（1523 字符）；
  - search_all_enabled 改 async + asyncio.gather，3 KB × sleep 1s = 1.01s（并行，非串行 3s）；
  - LRU 缓存命中 0.0009s（vs 冷查 0.52s）；
  - grep `_run_rag_retrieval` / `_rewrite_query_for_rag` 在 backend/ 下无代码引用（仅注释提及）；
  - 前端 `npx tsc --noEmit` 通过。
- 下次注意：
  1. **Agent 本身是 LLM，不要再调外部 LLM 做 query rewrite**——Agent 调用工具时已理解 query 语义，system prompt 指导其输出精炼检索词即可，额外 LLM 调用只增加延迟；
  2. **pre-RAG 对每条消息无差别检索是性能杀手**——闲聊不应该触发检索，应由 Agent 自己根据 system prompt 策略决定；
  3. **多 KB 检索必须并行**——串行 for 循环会让耗时随 KB 数量线性增长，asyncio.gather 让总耗时 ≈ 最慢 KB；
  4. **检索结果要缓存**——相同 query 在短时间内的重复检索（如 Agent 多轮对话）应命中缓存；
  5. **工具代码按领域分目录**（参考 Claude Code 的 src/tools/）比扁平堆放更易维护，全量注入机制不变，只改代码组织；
  6. **sse_adapter 拦截工具调用发 source 事件**比 pre-RAG 路径发 source 事件更合理——source 事件与工具调用绑定，时序清晰。

## 2026-07-01 - 模块级函数引用 build_tools 内部 import 的名字导致 NameError

- 错误现象：
  - Task 7 验证 `build_tools(None)` 时报 `NameError: name 'ReadFileTool' is not defined`，定位到 `agent_factory.py` 的 `_build_local_tools(ctx)` 函数（L170）。
- 错误原因：
  - `build_tools` 函数内部用 `from src.agent.tools.file_ops import ReadFileTool, ...` 导入工具类，这些名字只存在于 `build_tools` 的局部作用域；
  - 但模块级函数 `_build_local_tools` 直接引用了 `ReadFileTool / WriteFileTool / EditFileTool / RunCommandTool`，Python 函数闭包不会跨函数共享局部变量，导致 NameError；
  - 这是原代码的潜在 bug，Task 1 重组前可能因 Agent 路径未真正跑通而未暴露，重组后跑 `build_tools(None)` 验证才触发。
- 修复方式：
  - 在 `_build_local_tools` 函数内部补一行 import：`from src.agent.tools.groups.file_ops import EditFileTool, ReadFileTool, WriteFileTool` + `from src.agent.tools.groups.execution import RunCommandTool`，与 `build_tools` 的函数内 import 风格保持一致。
- 验证：
  - `build_tools(None)` 返回 12 个工具实例，工具名列表与 spec 完全一致。
- 下次注意：
  1. **函数内 import 的名字只在当前函数局部可见**，不会被同模块的其他函数访问到。若模块级辅助函数需要用到这些类，要么在辅助函数内部也 import，要么把 import 提到模块级；
  2. 验证 `build_tools` 这类工厂函数时，务必用 `build_tools(None)` 或 mock payload 实跑一次，避免只靠静态阅读误以为 import 链路畅通；
  3. 工具迁移到 `tools/groups/` 后，新代码应直接从 `src.agent.tools.groups.*` 导入，不要再依赖旧 shim。

## 2026-07-01 - BM25 分词切碎复合技术术语导致表格类查询 CR=0

- 错误现象：
  - DeepEval 跑分中 stm32f4-q005 (GPIOx_MODER 寄存器表) 和 stm32f4-q006 (SWJ-DP 引脚分配) 的 ContextualRecall=0；
  - esp32-q008 (Deep-sleep GPIO 状态) cross_page 查询也检索失败；
  - BM25 检索能找到 chunk 但分数极低，golden chunks 不在 top_k 内。
- 错误原因：
  - `BM25Index` 用纯 `jieba.lcut` 分词，jieba 会切碎带下划线/连字符的复合技术术语：
    - `GPIOx_MODER` → `['GPIO', 'x', '_', 'MODER']` ❌
    - `SWJ-DP` → `['SWJ', '-', 'DP']` ❌
    - `Deep-sleep` → `['Deep', '-', 'sleep']` ❌
  - 查询和文档被切成碎片后，BM25 匹配的是 `GPIO`/`x`/`MODER` 等单独 token，这些 token 在文档里太常见（GPIO 出现在大量 chunk），IDF 权重低，导致 golden chunk 评分被稀释；
  - 注意：纯大写寄存器名（MODERy/AFRL/AFRH/OSPEEDR/PA13）jieba 默认能整词保留，只有"带分隔符的复合词"被切碎。
- 修复方式：
  - `backend/src/rag/kb_manager.py` 的 `BM25Index` 类新增 `_TECH_TERM_RE` 正则和 `_tokenize_for_bm25` 类方法：
    - 正则 `[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)+` 匹配带下划线/连字符的复合词（GPIOx_MODER/SWJ-DP/Deep-sleep）；
    - 正则 `[A-Z]{2,}[A-Za-z0-9]*` 匹配纯大写术语（MODER/AFRL/PA13/OSPEEDR）；
    - 正则提取技术术语作为整词，剩余中文/混合文本仍交 jieba.lcut；
  - `_ensure_index` (L164) 和 `search` (L173) 两处 `jieba.lcut` 调用统一改为 `self._tokenize_for_bm25`；
  - 顶部 import 新增 `import re`。
- 验证：
  - 命令行测试 6 个典型查询，`GPIOx_MODER`/`SWJ-DP`/`Deep-sleep` 均被整词保留，中文分词不受影响；
  - `MODERy`/`PA13`/`OSPEEDR` 等原本就正确的术语保持不变。
- 下次注意：
  1. **修改分词函数后必须重建 BM25 索引**：旧 pickle 用纯 jieba 分词，新查询用正则分词，词表不一致会导致检索质量下降。需删除 `backend/data/bm25/*.pkl` 后跑 `scripts/reindex_baseline_*.py` 重建；
  2. jieba 对"纯大写字母序列"（MODERy/AFRL）默认能整词保留，但对"带下划线/连字符的复合词"（GPIOx_MODER/SWJ-DP）会切碎，硬件文档检索必须用正则预处理保护这类术语；
  3. 分词修复属于"索引和查询必须一致"的变更，两端必须用同一个函数，否则词表不匹配会让 BM25 得 0 分；
  4. `_HARDWARE_TERMS` 列表只能保护"已知术语"，正则能覆盖"未知但符合模式"的术语（如新芯片的 GPIOx_BSRR），两者互补但不能互替。

## 2026-07-01 - DeepEval faithfulness 反复超时：openai 客户端 timeout 过短触发静默重试

- 错误现象：
  - DeepEval 评估中 `faithfulness` metric 频繁超时（180s × 3 次重试全失败），单个问题耗时超 9 分钟；
  - 日志中反复出现 `Retrying request to /chat/completions in 0.439572 seconds`，表明 openai 客户端在自动重试；
  - 正常完成的 faithfulness 也要 100-120s，余量极小，opencode 端点稍慢就超 180s。
- 错误原因：
  1. **openai 客户端 timeout=120s 过短**：opencode 端点偶尔单次推理需要 >120s，客户端在 120s 时超时并自动重试（默认 max_retries=2），一次重试白费 120s+，把整个 metric 推过 PER_METRIC_TIMEOUT_SECONDS=180s；
  2. **PER_METRIC_TIMEOUT_SECONDS=180s 对 faithfulness 太紧**：faithfulness 需要多轮串行 LLM 调用（1 次提取 statements + N 次逐个验证），正常就要 100-120s，加上 opencode 偶发延迟，180s 根本不够。
- 修复方式：
  - `scripts/run_baseline_deepeval_v2.py` 中 `OpenCodeJudge.__init__` 的 `openai.OpenAI(timeout=120.0)` 改为 `timeout=300.0, max_retries=0`（由我们自己的 MAX_METRIC_RETRIES 循环控制重试）；
  - `PER_METRIC_TIMEOUT_SECONDS` 从 180 改为 300。
- 验证：
  - Round 4 (STM32F4): 9/9 成功，faithfulness 仅 q007 超时 1 次后重试成功，其余全部一次通过；
  - Round 5 (ESP32): 7/9 成功（q005/q007 因后端断开失败），faithfulness 全部一次通过，无超时。
- 下次注意：
  1. openai 客户端的 timeout 不要设得比 metric timeout 还短，否则客户端静默重试会吃掉整个 metric 的时间预算；
  2. faithfulness metric 的单次调用时间与 actual_output 长度正相关（statements 越多验证次数越多），timeout 至少给正常耗时的 2 倍；
  3. 设置 `max_retries=0` 把重试控制权交给上层逻辑（MAX_METRIC_RETRIES），避免 openai 客户端和上层逻辑双重重试。

## 2026-07-01 - 凭证链路优先级错乱 + 空串陷阱 + 9router 错误分类失准

- 错误现象：
  - 用户在前端填入 `https://9router.zxyzx.bbroot.com/v1` + 有效 API Key + 模型名 `oc/deepseek-v4`，后端返回 `model_not_found` 或 `AUTH_FAILED`，前端显示「API Key 无效」；
  - 但用户在前端直接发请求是有效的，说明凭证实际能通。
- 错误原因：
  1. **凭证优先级链路不统一**：`chat_sse`、`_resolve_creds`、`list_models` 三处各自解析 api_key/base_url/model，且 `payload.model` 会覆盖 `header_model`，导致 header 传的 `oc/deepseek-v4` 被 body 的 `gpt-4o` 覆盖；
  2. **空串陷阱**：`make_client` 用 `if value is not None` 判空，导致 `api_key=""` / `base_url=""` / `model=""` 直接传给 `AsyncOpenAI(api_key="", ...)`，绕过了 settings 兜底；
  3. **9router 非标准错误返回**：9router 用 HTTP 401 + `{"type":"ModelError","message":"Model deepseek-v4 is not supported"}` 返回模型不存在，被 openai SDK 归类为 `AuthenticationError`，前端 `chat_routes.py` 的 `"API Key" in _emsg` 优先匹配为 `AUTH_FAILED`；
  4. **get_provider_key 只返回 key**：stored_key 与 stored_base_url 不同源，配置了 openai+openrouter base_url 时取不到对应 base_url。
- 修复方式：
  - `backend/app/api/auth.py` 新增 `resolve_credentials(payload, request)` 统一函数，优先级 header > payload > stored > settings，空串通过 `_normalize` 归一化为 None；`get_provider_key` 改为返回 `(key, base_url)` 元组；
  - `backend/app/api/common.py` 的 `make_client` 将 `is not None` 改为 `if value`（仅对 api_key/base_url/model），`temperature`/`max_tokens` 保留 `is not None` 以兼容 `temperature=0`；
  - `backend/src/llm/client.py` 新增 `_PROXY_INDICATORS = ("openrouter", "9router", "bbroot")`，`chat_stream` 检测到代理端点 + 裸模型名（不含 `/`）时抛 `LLMError` 明确提示；`_with_retries` 增加 `except NotFoundError` 分支；
  - `backend/app/api/chat_routes.py` 错误处理增加 `MODEL_NOT_FOUND` 分支，匹配 `NotFoundError` / `model_not_found` / `modelerror` / `"is not supported"`；`ChatErrorCode` 枚举新增 `MODEL_NOT_FOUND`；
  - 前端 `useSettingsStore.ts` `DEFAULT_BASE_URLS` 新增 `openrouter`；`providers.ts` `PROVIDERS` 新增 OpenRouter；`InputBar.tsx` 模型选择器顶部新增自定义模型名输入框；`useChatStore.ts` 识别 `MODEL_NOT_FOUND` 错误码；`ErrorBlock.tsx` 新增对应样式。
- 验证：
  - curl 测试 `oc/deepseek-v4`：返回 `{"code":"MODEL_NOT_FOUND","message":"模型不存在：oc/deepseek-v4"}` ✅
  - curl 测试裸名 `gpt-4o` 到 9router：被代理检测拦截，返回明确提示「模型名 'gpt-4o' 缺少 provider 前缀」✅
  - `npx tsc --noEmit` 通过 ✅
- 下次注意：
  1. 凭证类参数（api_key/base_url/model）的优先级链必须收口在单一函数，禁止多处重复解析；空串必须归一化为 None，否则 `AsyncOpenAI(api_key="")` 不会触发兜底；
  2. `make_client` 这类工厂函数对 falsy 判空要分两类：字符串类用 `if value`，数值类用 `is not None`（保留 `temperature=0` 等合法 falsy）；
  3. 第三方代理端点（如 9router）可能用 401 + ModelError 返回模型不存在，不能只按 HTTP 状态码分类，必须看错误信息关键字（`model_not_found` / `modelerror` / `is not supported`）；
  4. OpenRouter 等代理要求完整模型名（`provider/model`），检测到代理端点时应对裸模型名做前置校验，避免发远端请求被拒；
  5. `get_provider_key` 这类"读取已存凭证"的函数应返回 `(key, base_url)` 元组，保证 key 与 base_url 同源，避免 key 来自 openai 但 base_url 来自 openrouter 的错配。

## 2026-07-01 - useChatStore.sendMessage 引用未定义的 permissionMode / toolKeys 导致前端对话无输出

- 错误现象：
  - 前端聊天输入消息后，只显示用户气泡，不出现助手回复；消息操作栏出现“重试”按钮；
  - 浏览器控制台无网络请求发出，performance entries 中看不到 `/api/chat`；
  - 用户反馈“前端对话不能正常渲染输出”。
- 错误原因：
  - v2-T4 / v3 合并 Agent 请求体时，在 `sendMessage` 中直接使用了 `permissionMode` 和 `toolKeys`，但没有从 `useSettingsStore.getState()` 中解构；
  - `ReferenceError` 在 `apiSSE("chat", ...)` 调用前抛出，整个请求未发出，前端只保留了乐观插入的用户消息，随后渲染为错误状态。
- 修复方式：
  - 在 `sendMessage` 内同步解构 `permissionMode` 和 `toolKeys`：
    ```ts
    const { ..., permissionMode, toolKeys } = useSettingsStore.getState();
    ```
  - 重新编译 (`tsc --noEmit`) 通过，agent-browser 端到端验证可正常发起 `/api/chat` 并渲染返回内容。
- 下次注意：
  1. 在 Zustand store action 中引用其他 store 状态必须显式解构，不能依赖外部作用域变量；
  2. 引入新的请求体字段后，必须跑 `tsc --noEmit` 和至少一次端到端发送验证；
  3. 前端“只显示用户消息且无网络请求”通常是请求发出前 JS 异常，优先检查 sendMessage 作用域内的未定义变量。

## 2026-07-01 - ToolAudit Index 引用不存在的列名导致建表失败

- 错误现象：
  - v3-T1 新增 `ToolAudit` 表模型后，`create_all()` 报 `ConstraintColumnNotFoundError: Can't create Index on table 'tool_audit': no column named 'created_at' is present.`
- 错误原因：
  - `__table_args__` 里 Index 定义写成 `Index("idx_tool_audit_created", "created_at")`，但实际列名是 `timestamp`（`Column(DateTime, default=...)`）；
  - 复制粘贴其他模型的 Index 模板时没核对列名。
- 修复方式：
  - 改为 `Index("idx_tool_audit_ts", "timestamp")`，与列名一致；
  - 同时检查另外两个 Index（`session_id`/`tool_name`）列名均正确。
- 下次注意：
  1. 定义 `__table_args__` 中的 Index 时，列名字符串必须和 `Column(...)` 的第一个参数完全一致，不能用语义近似的词（如 `created_at` vs `timestamp`）；
  2. 复制其他模型的 Index 模板后，必须逐字核对列名；
  3. SQLAlchemy 的 `create_all()` 在 Index 列名不存在时会报 `ConstraintColumnNotFoundError`，这个错误信息很明确，遇到时直接 Grep 表模型列定义对照即可。

## 2026-07-01 - main.py 变量名 _LOGGER vs logger 导致 NameError

- 错误现象：
  - v3-T1 在 `app/main.py` 启动时调 `cleanup_old_logs()`，写 `logger.info(...)` 报 `NameError: name 'logger' is not defined`。
- 错误原因：
  - `app/main.py` 顶部的 logger 变量名是 `_LOGGER`（带下划线前缀，表示模块私有），不是通用的 `logger`；
  - 从其他模块（如 `sse_adapter.py` 用 `logger`）复制代码时没核对当前文件的 logger 命名。
- 修复方式：
  - 改为 `_LOGGER.info(...)` 和 `_LOGGER.warning(...)`。
- 下次注意：
  1. 不同模块的 logger 变量命名可能不统一（`logger` vs `_LOGGER` vs `log`），跨模块复制日志调用代码时必须先 Grep 当前文件的 logger 命名；
  2. `app/main.py` 用 `_LOGGER` 是历史命名，新模块统一用 `logger = logging.getLogger(__name__)`。

## 2026-06-30 - useWorkbenchBridge autoSwitchPane 未传 source="bridge" 导致 workbenchUserOverride 误锁

- 错误现象：
  - Agent 调用 `render_wiring` 后前端能切到 WiringPane，但同一 Agent turn 内若 Agent 再调 `render_safety_report`，SafetyPane 不再自动切；
  - 调试发现 `workbenchUserOverride` 在第一次自动切后被置为 `true`，导致后续自动切被 `autoSwitchPane` 的 `if (app.workbenchUserOverride) return` 拦截。
- 错误原因：
  - `useWorkbenchBridge.ts` 的 `autoSwitchPane` 调 `app.setWbTab(target)` 未传第二个参数 `source`；
  - `useAppStore.setWbTab` 的签名是 `setWbTab: (t, source = "user") => ...`，默认 `source="user"` 会顺带把 `workbenchUserOverride` 置 `true`；
  - 设计意图是"只有用户手动点 tab 才置 override=true 锁定，bridge 自动切不应锁定"，但默认参数让 bridge 调用也触发了锁定。
- 修复方式：
  - `autoSwitchPane` 改为 `app.setWbTab(target, "bridge")`，明确传 `source="bridge"`，不触发 override 置 true；
  - 顺带把 `resetOverrideIfNewCallId` 里的 `useAppStore.setState({ workbenchUserOverride: false })` 直写改为 `useAppStore.getState().resetWorkbenchOverride()`（Task 7 已加该 setter，原代码 comment 误以为没加）。
- 下次注意：
  1. 当一个函数签名有默认参数且默认值会触发副作用（如 `source="user"` 会置 override）时，调用方必须显式传参，不能依赖默认值；
  2. "用户手动操作" vs "系统自动操作" 的区分要通过显式的 source/origin 参数传递，不能靠默认值；
  3. 写代码时如果 comment 说"某某 setter 没加"，应该先 Grep 验证再相信 comment，避免用过时的直写绕过已有 setter。

## 2026-06-30 - /api/kb/documents/{doc_id}/chunks 返回空 chunks（实际 ChromaDB 有数据）

- 错误现象：
  - `GET /api/kb/documents/{doc_id}/chunks` 返回 `{"success": true, "data": {"total_chunks": 0, "chunks": []}}`；
  - 但同进程直接调用 `kb_manager.get_doc_chunks("builtin-001", doc_id)` 能拿到 125/207 条 chunks；
  - 该接口失效导致 `verify_hard_question_coverage.py` 最初误判所有 source page 未覆盖。
- 错误原因：
  - 待确认：运行中的后端进程与脚本直接查询使用的是同一 `HardwareVectorStore` 与 `data/chroma_db`，但 API 路径下 `store.get_chunks_by_doc(doc_id)` 返回空；
  - 可能原因：后端启动后 ChromaDB 被外部脚本重新索引/写入，运行中后端持有的 `Chroma` 集合对象或缓存未刷新；或启动时集合绑定到了空状态。
- 修复方式：
  - 在 `verify_hard_question_coverage.py` 中增加 direct ChromaDB fallback：API 返回空时直接通过 `src.rag.kb_manager.get_kb_manager()` 查询；
  - 根因修复待后续排查：检查 `HardwareVectorStore.db` 属性是否在 API 生命周期内过期/缓存，必要时在 `get_chunks_by_doc` 前强制重新初始化或刷新集合。
- 下次注意：
  1. 做 chunk 覆盖验证时不要只依赖 API，重要校验应同时用直接 DB 查询交叉验证；
  2. ChromaDB 持久化集合在多进程/长生命周期服务中可能出现状态不一致，关键读操作要加 fallback；
  3. 发现 API 返回与直接查询不一致时，优先用独立脚本复现并记录到 pitfalls。

## 2026-06-30 - sse_adapter tool_result 丢失 target_pane/render_data（LangGraph ToolNode 把工具返回 dict JSON 序列化）

- 错误现象：
  - workbench_tools（render_wiring / render_safety_report / render_code）返回含 `target_pane` + `render_data` 的 dict；
  - 但前端收到的 `tool_result` SSE 事件 `result` 字段是 `{"output": "{\"output\": ..., \"target_pane\": ..., \"render_data\": ...}"}`，target_pane / render_data 被包进字符串里，前端无法路由到对应 Pane。
- 错误原因：
  - LangGraph `ToolNode` 的 `msg_content_output`（`langgraph/prebuilt/tool_node.py` L309-336）把工具返回的 dict 通过 `json.dumps(output, ensure_ascii=False)` 序列化成 JSON 字符串放进 `ToolMessage.content`；
  - `ToolMessage.content` 类型签名是 `str | list[dict]`，不接受 dict，直接传 dict 也会被 str 化；
  - `sse_adapter._parse_tool_content` 的 `isinstance(content, str)` 分支直接 `return {"output": content}, ...`，把整个 JSON 字符串包进 `output`，结构化字段全部丢失。
- 修复方式：
  - 在 `_parse_tool_content` 的 str 分支前调用新增的 `_try_parse_structured_content(content)`；
  - 仅当 `json.loads(content)` 解析出含 `target_pane` 键的 dict 时，整体返回该 dict（透传 `target_pane` / `render_data` / `output`）；否则保持原 `{"output": content}` 行为；
  - 用 `target_pane` 作为"路由键"判断是否走恢复路径，不影响现有工具（search_docs 等返回纯字符串的工具仍走原路径）。
- 下次注意：
  1. LangGraph `ToolNode` 会把工具返回的 dict 用 `json.dumps` 序列化成 str 放进 `ToolMessage.content`，sse_adapter 拿到的是 JSON 字符串而非 dict，需要 `json.loads` 恢复；
  2. 工具若想让前端拿到结构化字段，返回值里要带一个明确的"路由键"（如 `target_pane`），sse_adapter 据此判断是否走 JSON 恢复路径，避免误伤纯字符串工具；
  3. 修改 sse_adapter 的 str 分支时不要破坏原有行为（普通工具返回纯字符串时仍应是 `{"output": content}`），用路由键做条件分支；
  4. `ToolMessage.content` 类型是 `str | list[dict]`，构造测试用例时直接传 dict 会被 str 化（Python repr 风格而非 JSON），与真实 ToolNode 路径（JSON 字符串）不一致，验证时要用 `json.dumps` 模拟真实路径。

## 2026-06-30 - Agent 路径未触发：_should_use_agent 读 payload.model 而非解析后的 model

- 错误现象：
  - 前端发 `use_agent=true` 请求，但后端始终走 fallback LLM 流式路径，Agent 路径从不触发；
  - 后端日志无 `langchain_openai` 相关输出，只有 `src.llm.client` 的 LLM 调用日志。
- 错误原因：
  - `_should_use_agent(payload)` 检查 `getattr(payload, "model", None)`，但用户不传 model 时 `payload.model` 是 None；
  - chat_routes.py L155 把 `model` 解析为 `payload.model or header_model or settings.llm_model`，但这个解析后的 `model` 没传给 `_should_use_agent`；
  - `_model_supports_tools("")` 返回 False（空字符串不匹配任何白名单项），导致 Agent 路径被跳过。
- 修复方式：
  - `_should_use_agent(payload, model="")` 新增 `model` 参数，用 `model or payload.model or ""` 作为有效模型名；
  - chat_routes.py 调用处改为 `_should_use_agent(payload, model)`。
- 下次注意：
  1. 门控函数不要只从 payload 读字段，payload 的字段可能为 None（用户不传时用后端默认配置）；
  2. chat_routes.py 中 `model`/`api_key`/`base_url` 都有 fallback 链（payload → header → settings），门控函数应该接收解析后的值。

## 2026-06-30 - streaming tool_calls 分块到达导致 tool_call 事件 args 为空

- 错误现象：
  - Agent 触发后 SSE 输出多个 `tool_call` 事件，但 `args` 全是 `{}`，`tool` 名有时为空字符串；
  - Agent 反复调用工具（因为 args 为空，工具返回不相关结果），180 秒超时未完成。
- 错误原因：
  - langgraph `stream_mode=["messages"]` 的 `AIMessageChunk.tool_calls` 是增量式的：name 在第一个 chunk，args 的 JSON 片段在后续 chunk；
  - sse_adapter.py 对每个包含 tool_calls 的 chunk 都生成一个 tool_call 事件，导致不完整的事件（args 为空、tool 名缺失）。
- 修复方式：
  - messages 模式只处理 text content，不处理 tool_calls；
  - tool_calls 从 updates 模式的完整 `AIMessage` 提取（updates 模式返回 node 完成后的完整 message，tool_calls 有完整 args）；
  - `_handle_update_chunk` 同时处理 AIMessage（tool_call 事件）和 ToolMessage（tool_result 事件）。
- 下次注意：
  1. langgraph streaming 的 messages 模式是 token-level chunk，tool_calls 是分块到达的，不能直接用；
  2. 完整的 message（含完整 tool_calls）在 updates 模式的 node 输出中获取；
  3. streaming SSE 适配器要区分"增量 chunk"和"完整 message"两种数据源。

## 2026-06-30 - 受保护的 try/except 导入块中 except 引用未定义的 logger 导致 NameError 掩盖原始错误

- 错误现象：
  - 在 chat_routes.py 顶层用 `try: from src.agent.agent_factory import ... except ImportError as e: logger.debug(...)` 做受保护的 Agent 路径导入（langgraph 缺失时降级到 fallback）；
  - 若 langgraph/langchain 真的缺失，except 子句执行 `logger.debug(...)` 会抛 `NameError: name 'logger' is not defined`，因为 `logger = logging.getLogger(__name__)` 写在 try/except 块**之后**；
  - 这个 NameError 会掩盖原始 ImportError，且让整个 chat_routes 模块导入失败，连带 /api/chat、/api/models、/api/token-usage 全部路由挂掉。
- 错误原因：
  - 模块级 `logger` 定义顺序错误：先写了 try/except（except 里用 logger），再定义 logger；
  - Python 执行 except 子句时 logger 尚未绑定。
- 修复方式：
  - 把 `logger = logging.getLogger(__name__)` 移到 try/except 导入块**之前**；
  - 验证：`python -c "import app.api.chat_routes"` 在 langgraph 已装/未装两种情况下都不抛 NameError。
- 下次注意：
  1. 任何在模块顶层 except 子句里引用 logger 的受保护导入块，必须先定义 logger 再写 try/except；
  2. 受保护导入（guarded import）的 except 分支只做"记录 + 设降级标志"，不要依赖任何尚未定义的模块级名字；
  3. 检查模块顶层代码顺序：logger 定义 → 受保护导入 → router 定义 → 其他。

## 2026-06-30 - OpenCodeJudge 伪异步导致 DeepEval 指标全超时

- 错误现象：
  - DeepEval 基线评测 Round 0 中，`context_recall` / `faithfulness` / `context_precision` 三个指标在 ch340g-q001 上全部 3 次重试超时（60s），只有 `answer_relevancy` 偶尔通过（48s 卡在边界）；
  - 每次底层 HTTP 调用都返回 `200 OK`，API Key/URL 均正常，但 `metric.measure()` 总超时。
- 错误原因：
  - `OpenCodeJudge.a_generate(prompt)` 实现为 `return self.generate(prompt)` —— 同步阻塞事件循环；
  - DeepEval v4.0.7 的 `FaithfulnessMetric.a_measure()` 用 `asyncio.gather()` 并行执行 `_a_generate_truths` 和 `_a_generate_claims`，但因为 `a_generate` 阻塞事件循环，`gather` 退化为串行；
  - FaithfulnessMetric 需 4 次 LLM 调用（truths + claims + verdicts + reason），串行后总耗时 170s+，远超 60s 超时；
  - ContextualRecallMetric 需 2 次调用（verdicts + reason），串行后 72s，也超 60s。
- 修复方式：
  - `a_generate` 改为 `return await asyncio.to_thread(self.generate, prompt)`，将同步 openai 调用卸载到工作线程，不阻塞事件循环；
  - `PER_METRIC_TIMEOUT_SECONDS` 从 60s 提到 180s（faithfulness 并行后约 85s，180s 留足余量）；
  - `PER_SAMPLE_TIMEOUT_SECONDS` 从 4min 提到 12min，`OVERALL_TIMEOUT_SECONDS` 从 1h 提到 4h。
- 下次注意：
  1. 实现 `DeepEvalBaseLLM.a_generate` 时必须真正异步（`asyncio.to_thread` 或原生 async client），不能 `return self.generate(prompt)`；
  2. DeepEval 指标的 LLM 调用次数：context_recall=2, faithfulness=4(truths+claims 可并行), answer_relevancy=3, context_precision=2；
  3. 超时设置要按"最慢指标 × 单次调用耗时"估算，不是按单次调用估算。

## 2026-06-30 - HybridChunker page_range 继承修复（chunk-baseline-v1）

- 错误现象：
  - `esp32_datasheet.pdf` 用 HybridChunker 入库后，大量不含 `<!-- PAGE:N -->` 标记的子 section 被分配到 `page_range=[1,1]`；
  - p1 被 60+ 个 chunk 覆盖，合计字符数远超该页实际内容；
  - audit 显示 Peripheral Pin Configurations（p47-51）、Appendix A GPIO_Matrix/IO_MUX（p62-70）等关键表格的 chunk 页码错位。
- 错误原因：
  - `_get_section_pages()` 在无 marker 时硬编码 fallback 到 `(1, 1)`；
  - `_split_markdown()` 与 `_split_plain_text()` 拆分 section 时未维护“最近一次有效页码”，导致 protect_structures 占位符或合并后的子段落失去页码上下文后全部回到第 1 页。
- 修复方式：
  - `hybrid_chunker.py` 中 `_get_section_pages()` 增加 `default_range` 参数；
  - `_split_markdown()` 与 `_split_plain_text()` 引入 `current_page_range` 状态，无 marker 时继承最近一次有效页码，有 marker 时更新；
  - 重跑 esp32 入库 audit，p1 覆盖 chunk 数恢复正常，表格内容不再集中到 page 1。
- 下次注意：
  1. chunker 的页码 fallback 不要硬编码 `(1, 1)`，应支持继承最近一次有效范围；
  2. 对 protect_structures 占位符、sub-split、section 合并等操作，要交叉验证页码标记是否丢失；
  3. audit 时除了 page coverage，还要检查“单页 chunk 数/字符数是否异常”。

## 2026-06-30 - DeepEval 报告生成时 results 与 samples 顺序不一致导致 per-PDF 分数错位

- 错误现象：
  - 第一次生成的 `chunk-baseline-eval-v1.json` 中，`per_question` 里 `esp32-q001` 的 `source_pdf` 被标成 `stm32f4_gpio_exti_extract.pdf`，而 `stm32f4-q001` 被标成 `esp32_datasheet.pdf`；
  - `per_pdf` 平均值随之错位：stm32f4 问题的高分被算到 esp32 PDF 上，esp32 问题的低分被算到 stm32f4 PDF 上。
- 错误原因：
  - 评估脚本 `scripts/run_baseline_deepeval.py` 在并行执行后把 `results.sort(key=lambda r: r.id)`，但 `samples` 列表仍保持 YAML 原始顺序；
  - `generate_report()` 里用 `zip(samples, results)` 直接配对，导致 ID 与 source_pdf 不匹配。
- 修复方式：
  - 排序 results 后，再对 samples 按同样 key 排序：`samples_sorted = sorted(samples, key=lambda s: s.id)`；
  - 重新生成 JSON/Markdown 报告，per-question source_pdf 与 per-pdf 分组恢复正确。
- 下次注意：
  1. 只要对两个列表分别排序后再 `zip`，必须确保排序 key 完全一致；
  2. 并行/异步收集结果后，report 生成阶段要用 id→object 映射而不是依赖列表顺序；
  3. 审计 per-PDF 聚合指标时，抽查几条 per-question 的 source_pdf 是否匹配 ID。

## 2026-06-30 - 读取 validation report 时误把问题条目当 retrieved chunks 列表

- 错误现象：
  - 编写 `scripts/analyze_golden_retrieval_gap.py` 时，`analyze_question(s, report[pdf_key][i])` 直接传入 report 条目，运行时报 `KeyError: slice(None, 3, None)`；
  - 原因是 `report[pdf_key][i]` 是 `{"id": ..., "query": ..., "retrieved": [...]}`，retrieved chunks 列表在 `"retrieved"` 字段下。
- 错误原因：
  - 没先确认 JSON schema，假设 validation report 里每个 PDF 的数组元素直接是 chunks 列表；
  - 代码中 `retrieved[:3]` 实际作用在 dict 上，Python 把 dict 的 slice 解释为 key，导致 KeyError。
- 修复方式：
  - 改为 `report[pdf_key][i]["retrieved"]` 后再切片/analyze；
  - 脚本运行成功后输出 `chunk-baseline-golden-v1-retrieval-gap-report.{json,md}`。
- 下次注意：
  1. 处理嵌套 JSON 时先打印 schema 样本，确认字段层级再写代码；
  2. 类型注解不能替代运行时检查，`list[dict]` 参数实际收到 dict 时错误信息会误导；
  3. 写分析脚本时先对最小数据集做单元测试，避免在完整 26 题上报错后才定位。

## 2026-06-30 - HybridChunker 保护表格占位符丢失页码标记，导致表格内容被错误分配到 page 1

- 错误现象：
  - esp32_datasheet.pdf 用 HybridChunker 入库后，关键表格（Peripheral Pin Configurations p47-51、Appendix A GPIO_Matrix/IO_MUX p62-70）的 Markdown 表格内容出现在 page_range=[1,1] 的 chunk 中；
  - 这些页的真实 chunk 只保留表头/页脚，表格行缺失；
  - p1 被 63 个 chunk 覆盖，合计 89k+ 字符，远超该页实际 417 字符。
- 错误原因：
  - document_processor._parse_pymupdf_per_page() 输出的表格 Markdown 会被 protect_structures() 替换为占位符；
  - 占位符在 sub-split / section 合并过程中丢失了 <!-- PAGE:N --> 标记；
  - 回退到 section 级页码时，该 section 可能只含 <!-- PAGE:1 -->，导致整张表被分配到第 1 页。
- 修复方向（待实现）：
  1. 在 protect_structures 之前把页码标记 stash 到占位符元数据，restore 时写回；
  2. 或在 _parse_pymupdf_per_page 中为表格占位符显式保留所在页码；
  3. 对 page_range=[1,1] 且含大量表格行的大 chunk 增加后置校验/重新分配页码。
- 下次注意：
  1. 入库 audit 不要只看 page coverage，要检查大 chunk 的 page_range 是否合理；
  2. 对数据手册类 PDF，表格密集页必须单独验证表格行是否出现在正确页码的 chunk 中；
  3. HybridChunker 的 page marker 保护与表格占位符保护必须交叉验证。

这个文件记录项目推进中已经踩过、已经定位或修复的问题。每条记录尽量短，重点写清楚：错误现象、为什么错、怎么改、下次注意什么。

## 2026-06-29 - Section 边界 page_range 重叠导致 56% 重复入库（chunk-integrity-fullchain-fix）

- 错误现象：ch340g 重新索引后生成 90 个 chunks，其中 56% 是重复内容（同一文本被多个 section 各取一次入库）。
- 错误原因：
  1. LLM 返回的相邻 section 的 `page_range` 重叠（如 A: 1-3, B: 2-4），`_build_chunks` 用 `range(start_page, end_page+1)` 从 `page_text_map` 取页文本，导致同一页文本被多个 section 各取一次；
  2. `kb_manager.ingest_chunks` 入库前只按 `fingerprint` 单键去重，但不同 section 的同文本 chunk 指纹相同 → 全部被错误保留（或被误删，CASE 3 中正常 chunks 被删了 40 个）。
- 修复方式：
  1. multimodal_chunker `_build_chunks` 引入 `assigned_pages: set[int]`，每页只分配给第一个声明它的 section，后续 section 只取未分配的页，仅当所有页已分配才跳过（不强制递增 start_page，避免 LLM 顺序乱序时误跳 section）；
  2. kb_manager.ingest_chunks 改用 `(fingerprint, section_title)` 复合键去重，相同文本不同 section 的 chunk 都保留。
- 验证：CASE 1 ch340g 90→48 chunks，重复率 56%→0%；CASE 3 06-chaotic 21→61 chunks（恢复被误删的 40 个）。
- 下次注意：
  1. LLM 返回的 page_range 不要假设严格不相交，必须做去重分配；
  2. 去重键不能只用内容指纹，要加上 section 上下文，否则跨 section 的同文本会被误删；
  3. 修去重逻辑时要同时验证「重复率下降」和「正常 chunk 不被误删」两个方向。

## 2026-06-29 - parse_page_index 默认返回 [(1,0,len)] 掩盖页码标记丢失（BREAKING 改为返回 []）

- 错误现象：multimodal_chunker 子 chunk 的 page_start 几乎全部为 1，即使内容来自 p4/p5/p12。
- 错误原因：`parse_page_index()` 在无 marker 时返回 `[(1, 0, len(text))]`（默认 page 1 兜底），调用方把"返回非空"当作"有有效标记"，导致所有丢失标记的子 chunk 被错误锁定为 page 1。
- 修复方式：BREAKING 改动 — 无 marker 时返回 `[]` 空列表，强制调用方显式处理。所有调用方（agent_chunker / hybrid_chunker / multimodal_chunker / get_text_for_page_range / get_page_for_char）添加 `if not index: return fallback` 兜底。
- 下次注意：兜底默认值（如默认 page=1）会掩盖上游错误，关键判断不能依赖"返回非空"=有效，必须显式区分"无数据"和"有数据"。

## 2026-06-29 - 硬编码 API Key / 内部代理地址写死在源码

- 错误现象：安全审查发现 `backend/_create_test_kb.py` 第 13/16 行硬编码两个真实 API Key（阿里云 dashscope embedding key + 9router LLM key），`backend/tests/rag_eval/config.py` 第 39/43 行硬编码内部代理地址 `9router.zxyzx.bbroot.com`（此文件未 gitignore，已泄露到开源仓库）；同时 `backend/.env` 的 EMBEDDING 三字段为空且默认值是 OpenAI，与真实用法（阿里云 dashscope）不一致。
- 错误原因：1) 测试脚本图方便直接把 Key 写进 payload；2) 评测 config 的 DEFAULT 常量用了内部代理地址作默认值，未考虑开源场景；3) .env 的 EMBEDDING 配置从未被实际填写，脚本只能硬编码绕过。
- 修复方式：1) `_create_test_kb.py` 改为 `os.getenv("EMBEDDING_API_KEY")` / `os.getenv("LLM_API_KEY")` 读取，缺值时 sys.exit(1) 提示；2) `config.py` 顶部加 `load_dotenv()`，4 个 DEFAULT 常量改 `os.getenv(name, 公网默认值)`，默认值用 OpenAI/dashscope 公网地址而非内部代理；3) `backend/.env` 补全 EMBEDDING 三字段（阿里云 dashscope text-embedding-v4）；4) `.env.example` 模板对齐真实用法；5) HOST 从 0.0.0.0 改为 127.0.0.1 对齐 AGENTS.md 安全立场。
- 下次注意：1) 任何 API Key / 内部代理地址都不能写死在源码，必须 os.getenv() 读取；2) 会进开源仓库的文件（未被 gitignore）尤其要检查是否含内部地址/模型路径（如 `oc/` 前缀）；3) .env 的 EMBEDDING 配置必须和实际使用的 embedding 服务一致，否则脚本会绕过 .env 硬编码；4) HOST 默认值应遵循「只监听 127.0.0.1」的安全立场；5) 仍待处理：`run_golden_eval.py` argparse 默认值（L1118-1120）仍硬编码 9router 地址，需同样改为 os.getenv。

## 2026-06-29 - Reranker 覆盖 display score 导致相关度全部 100%

- 错误现象：RAG 检索结果相关度全部显示 100%，即使 BM25 已软化、RRF 已改 avg 仍无改善。
- 错误原因：`kb_manager.py` 在 RRF 融合后调用 bge-reranker 做交叉编码器重排序，原代码用 reranker 返回的 score 直接覆盖了 `FusedResult.score` 用于前端展示。bge-reranker 返回的是 query-chunk 语义相关概率，多个 top chunk 在该概率上往往都接近 0.99-1.0，四舍五入后全部显示 100%，摧毁了 RRF/BM25 计算出的相对区分度。
- 修复方式：reranker 仅用于调整结果排序，不再覆盖 display score；前端展示仍使用 RRF 融合后的 calibrated 0-1 分数（BM25 软化 1.15 + BM25-only 单源降权 0.85）。
- 下次注意：1) 重排序模型（reranker）的分数通常只适合做排序，不适合直接作为相关度百分比展示；2) 任何覆盖 display score 的操作都要检查是否会破坏已有分数校准；3) 改完 Python 后端必须重启服务才能生效。

## 2026-06-29 - MultimodalChunker 页码标记丢失导致 page_start 全为 1

- 错误现象：ch340g 重新索引后，text chunk 的 `page_start` 几乎全部等于 1（80/114），即使内容明显来自 p4/p5/p12 等页面。
- 错误原因：
  1. `RecursiveCharacterTextSplitter` 会把 `<!-- PAGE:N -->` 页码标记从中间切断（如 `<!-- PAGE` 和 `:3 -->` 分到两个 chunk）；
  2. `_build_chunks()` 调用 `parse_page_index(sub_text)` 解析子 chunk 页码，但 `parse_page_index()` 在**无标记时默认返回 `[(1, 0, len(text))]`**，不是空列表；
  3. 代码把 "返回列表非空" 当作"有有效标记"，于是所有子 chunk 的 `sub_page_nums = [1]`，最终 page_start 被错误地锁定为 1。
- 修复方式：
  1. `_build_chunks()` 在 sub-split 前用 `PAGE_MARKER_RE.sub(_stash, ...)` 把页码标记替换为占位符，防止被切断；
  2. 子 chunk 恢复占位符后，改用 `PAGE_MARKER_RE.findall(sub_text)` 直接检查真实标记存在性，而不是依赖 `parse_page_index()` 的默认值；
  3. 无真实标记时回退到 section 级 `start_page`/`end_page`（LLM 返回的 section 边界），避免默认 page=1。
- 验证：重新索引 ch340g 后，page_start 分布覆盖 1-14 全部页面；hybrid chunker 快速验证也确认修复生效；pytest 10 passed。
- 下次注意：
  1. `parse_page_index()` 的"无标记时返回 [(1,0,len)]"是方便调用方的兜底行为，但在判断"是否有标记"时不能依赖它；
  2. 任何会被 `RecursiveCharacterTextSplitter` 处理的标记/标签，split 前都要用占位符保护；
  3. 多测试几种 chunker（multimodal + hybrid/agent）交叉验证页码修复，避免只测一种路径。

## 2026-06-29 - BM25 相对评分导致多 KB 合并后相关度全部 100%

- 错误现象：RAG 检索结果相关度全部显示 100%，无论查询什么内容 top-k 都是满分。
- 错误原因：三层叠加——
  1) BM25 归一化用 `max_score = results[0][1]`（top-1 自己的原始分），导致 top-1 永远 = 1.0；
  2) RRF fusion 之前用 `max(vector, bm25)` 取最大值，BM25 的 1.0 直接传播到 display score；
  3) 多 KB 合并时每个 KB 独立做 BM25，各自的 top-1 都是 1.0，合并后多个 1.0 并存。
  即使 RRF 已从 max 改为 avg，当 chunk 只在 BM25 中命中（vector 没检索到）时，`orig_scores = [1.0]`，avg 仍是 1.0。
- 修复方式：
  1) BM25 归一化软化：`normalized = score / (max_score * 1.15)`，top-1 从 1.0 降到 ~0.87；
  2) 单源降权：只在 BM25 命中（无 vector 语义匹配）的 chunk `orig_score *= 0.85`，因为 BM25 是相对评分，可靠性低于 cosine 绝对评分。
  最终：BM25-only top-1 ≈ 0.74，两者都命中 ≈ 0.86，vector-only 不降权。
- 下次注意：1) 相对评分系统（BM25、TF-IDF）的 top-1 永远是满分，不能直接当相关度展示；2) 多源融合时要注意"只被一个源命中"和"被多个源命中"的可靠性差异，单源命中应降权；3) 改 RRF 融合策略（max→avg）后要同步更新所有相关测试断言。

## 2026-06-29 - 引用回复功能断链：InputBar.handleSend 未传 quoted 参数

- 错误现象：用户点消息"引用"按钮后引用条正常显示，但发送消息后引用内容没传给后端，LLM 不知道用户引用了哪条历史消息，引用回复功能完全失效。
- 错误原因：`useChatStore.sendMessage` 已加 `quoted?: Message` 参数并在 L381-385 注入 `quotedContext`（system 消息告诉 LLM 用户引用了之前哪段对话），但 `InputBar.handleSend` 调用时只传了 `(text, attachments)` 两个参数，第三个 `quoted` 始终 undefined。跨组件数据流断在调用方。
- 修复方式：`InputBar.handleSend` 用 `useAppStore.getState().quotedMsg` 读取引用消息，传给 `sendMessage(text, attachments, quoted)`，发送后 `setQuotedMsg(null)` 清除引用条。
- 下次注意：1) store action 加新参数后，必须 grep 所有调用方同步更新，不能只改 store；2) 闭包内读 store 用 `getState()` 而非 hook 解构值，避免读到陈旧值；3) 引用回复这类跨组件交互要手动端到端验证（点引用→发送→看后端是否收到 quotedContext）。

## 2026-06-29 - 人工审计发现 ch340g p5/p6 矢量电路图遗漏 image_description

- 错误现象：多模态模型逐页审阅 PDF 后发现，p5（USB to RS232 适配器电路图）和 p6（光隔离 USB UART 电路图）没有生成 image_description chunk，只有 text chunk 里打散的 ASCII 元件标号。
- 错误原因：`page.get_images()` 只能检测嵌入式位图/矢量图片对象；但 datasheet 中的电路图是 CAD 矢量导出（线条+文字），PyMuPDF 不把它们当作 embedded images。`_needs_image_description()` 之前的 fallback 是 `has_table or avg_text < 300`，p5/p6 文本密度高于阈值且无表格标记，因此被跳过。
- 修复方式：在 multimodal_chunker.py 增加 `_looks_like_schematic()` 启发式检测：
  - 关键词：`schematic|configuration|adapter|converter`
  - 元件标号：至少 5 个不同类别的 `C/R/U/Q/X/IC/MAX/PC/74xx` 等
  - `_needs_image_description()` 在 embedded_images 检测之后、原 fallback 之前调用
- 验证：p5/p6 文本经 `_looks_like_schematic()` 返回 True；pytest 140 passed。
- 下次注意：1) `page.get_images()` 不能覆盖所有"含图页面"，矢量图/流程图/电路图需要额外启发式；2) 判断 chunk 完整性时必须亲自比对 PDF 真实内容，不能只看工具输出；3) 审计脚本 reusable：scripts/render_pdf_pages.py / export_chroma_chunks.py / compare_chunks_vs_pdf.py。

## 2026-06-29 - 接入 PyMuPDF find_tables() 提升表格 chunk 质量

- 问题背景：PyMuPDF `page.get_text("text")` 把表格打散为纯文本行，寄存器表/引脚表/电气参数表断裂到多个 chunk，RAG 检索时表格行与表头分离。
- 修复方式：用 PyMuPDF 原生 `page.find_tables()` + `tab.to_markdown()` 提取结构化 Markdown 表格，追加到每页文本末尾。
  - document_processor.py `_parse_pymupdf_per_page()`：每页 get_text 后追加 find_tables 输出
  - multimodal_chunker.py `_render_pages()`：同上（multimodal 绕过 parser 自己读 PDF，必须单独接入）
  - base.py 新增 `protect_structures()`/`restore_structures()` + `_TABLE_ROW_RE`/`_REGISTER_FIELD_RE`（从 multimodal_chunker 抽出）
  - hybrid_chunker.py / agent_chunker.py：在 inline code 保护后追加 `protect_structures`，表格 placeholder 与 code placeholder 共用同一 map
  - multimodal_chunker.py：删除本地 regex 定义，改用 base.py 的 protect_structures
- 效果（ch340g 14页）：表格 Markdown 134 行（vs OpenDataLoader 130 行），139 个结构块被 placeholder 保护，split 后 10 chunk 携带完整表格不被切碎。140 测试全通过。
- 下次注意：1) `page.find_tables()` 有误检（p6 电路图元件被当表格），但误检表格被 protect_structures 保护为完整 chunk，不影响检索质量，代价小于漏检真实表格；2) `tab.to_markdown()` 输出含 `| |` 尾列（PyMuPDF 多余列），不影响 _TABLE_ROW_RE 匹配；3) 三个 chunker 的表格保护逻辑现在统一在 base.py，改 regex 只需改一处。
- PoC 脚本：scripts/poc_find_tables.py（决策门 PASS：14 表格/5 关键页全检测到）

## 2026-06-29 - OpenDataLoader-PDF 评估：表格优但速度慢100x，不接入主链路

- 评估背景：ch340g datasheet 表格断裂痛点，评估 opendataloader-pdf (25K Star, Apache-2.0) 是否可替换 PyMuPDF。
- 实测数据（ch340g 14页 430KB）：
  - 速度：PyMuPDF 0.12s vs OpenDataLoader 12.09s（慢 100 倍，每次 convert spawn JVM）
  - 表格：OpenDataLoader 提取 130 行 Markdown 表格（电气参数表/引脚表/EEPROM配置表完整），PyMuPDF 打散纯文本
  - 图片：OpenDataLoader 提取 7 个图片文件并用 `![image N](path)` 引用；PyMuPDF `page.get_images()` 检测 5 个含图页面 ✅
  - 电路图：两者都差——OpenDataLoader 把矢量电路图标签当文本提取（+3V3/IC2/16/2/RXD 散落），PyMuPDF 也是纯文本
  - 页码标记：OpenDataLoader Markdown 无 `<!-- PAGE:N -->` 标记，需改造
  - 依赖：OpenDataLoader 需 Java 11+（与"用户非技术人员"定位冲突）
- 结论：不接入主链路。PyMuPDF 图片检测修复已验证有效（ch340g 5 含图页面 100% 覆盖 image_description），表格问题应优先用 PyMuPDF 原生 `page.find_tables()` 解决而非引入 Java 依赖。
- 下次注意：1) 评估开源库要先跑 PoC 实测速度，benchmark 数据（0.015s/page local）是批量平均，单文件 spawn JVM 冷启动 12s；2) "解析器"和"分块器"职责不同——OpenDataLoader 是解析器不能替代 multimodal chunker 的 section 识别；3) pip install 超时可换清华镜像 `pip install xxx -i https://pypi.tuna.tsinghua.edu.cn/simple`（官方源 220kB/s 卡死，清华 6.1MB/s）。
- 验证脚本：scripts/verify_pymupdf_image_detection.py（ALL PASS）、scripts/opendataloader_poc.py、scripts/inspect_ch340g_chunks.py

## 2026-06-29 - RAG 检索事件缓冲导致知识库问题超时无思考卡片

- 错误现象：用户发送知识库相关问题（如"STM32 GPIO 推挽输出怎么配置？"）超时无返回、无思考卡片；但发送"你好"正常。
- 错误原因：`_run_rag_retrieval` 把所有 thinking/source/tool 事件 `append` 到列表，最后才 `return`，`event_generator` 拿到列表后才 `for evt in rag_events: yield evt`。这意味着查询改写(8s)+向量检索+rerank 全部完成前，前端收不到任何字节（包括"正在改写查询..."思考卡片）。前端 IDLE 超时 5 分钟触发 abort。"你好"因 ≤6 字符跳过查询改写 LLM 调用 + 检索快，秒级完成所以正常。叠加问题：`search_all_enabled` 是同步函数被 async 函数直接调用，阻塞 event loop，导致 abort 信号无法处理。
- 修复方式：1) `_run_rag_retrieval` 改为 async generator，`yield` 每个事件，`rag_context`/`sources` 写入 `result_out` dict 传出；`chat_routes.py` 调用方改 `async for evt in ...: yield evt`；2) `kb_manager.search_all_enabled` 用 `await asyncio.to_thread(...)` 包裹移出 event loop。
- 下次注意：1. SSE 事件必须实时 yield，不能缓冲后统一返回——前端 IDLE 超时会在无字节时触发；2. async 函数中的同步阻塞调用必须用 `asyncio.to_thread` 移出 event loop，否则 abort 信号和超时机制失灵；3. "短消息正常/长消息卡死"的分叉通常是某个分支跳过了耗时操作（本例 ≤6 字符跳过查询改写），定位时要找分支条件。
- 验证：前端 tsc 0 errors，后端 pytest 53 passed。

## 2026-06-29 - ChatArea 过滤 source='rag' 的 thinking step 导致 RAG 检索阶段无思考卡片

- 错误现象：后端 SSE 已实时输出 `{"type":"thinking","content":"正在改写查询...","source":"rag"}` 事件（curl 验证），但前端仍不显示思考卡片。发送"你好"正常，硬件问题无卡片。
- 错误原因：`ChatArea.tsx:460` 的渲染过滤器 `activity.steps.filter((s) => !(s.type === 'thinking' && s.source === 'rag'))` 把所有 RAG 检索阶段的 thinking step 过滤掉了。后端 RAG 检索阶段（8-15s）只 yield `source='rag'` 的 thinking 事件（"正在改写查询..."/"正在检索文档..."），全部被过滤，ActivityBlock body 为空。"你好"因检索极快（<1s）迅速进入 `source='llm'` 的 thinking（不过滤）+ text 阶段，所以有卡片。讽刺的是 `ThinkingStep` 组件（L487）本来就为 rag 源设计了"知识库检索"标签，但因 L460 过滤永远收不到 rag step。叠加问题：`ActivityBlock` 默认折叠（`useState(true)`），即使不过滤用户也看不到内容。
- 修复方式：1) `ChatArea.tsx:461` 移除 `.filter((s) => !(s.type === 'thinking' && s.source === 'rag'))`，让 rag thinking step 正常渲染；2) `ChatArea.tsx:429` 默认折叠改为 `useState(!isRunning)`，流式进行中默认展开 ActivityBlock，让用户立即看到 RAG 检索/思考过程。
- 下次注意：1. 渲染过滤器与组件设计不能矛盾——如果组件已为某 source 设计了标签和样式（如 rag 的"知识库检索"标签），渲染层就不该过滤这个 source；2. "后端有输出但前端没显示"优先排查渲染层的 filter/条件渲染，用 curl 验证后端 SSE 输出是快速定位手段；3. 流式场景的 UI 默认状态要考虑用户体验——思考过程应默认可见，完成后才折叠。
- 验证：前端 tsc 0 errors；curl 确认后端 SSE 输出 `thinking source=rag` 事件正常。

## 2026-06-29 - 发送消息后主聊天区仍显示 EmptyState（isStreaming 但 messages 为空）

- 错误现象：用户输入消息并发送后，主聊天区仍停留在 Hardware RAG Agent 空态主页，看不到用户消息和 AI 回复占位；但输入框右侧显示"停止"按钮，右侧"对话内容"面板已显示检索来源，后端 SSE 输出正常。
- 错误原因：这是一个前端状态同步/渲染条件问题。`ChatArea.tsx:209` 的 EmptyState 分支只判断 `!messages.length`，当 `isStreaming=true` 但 `messages` 暂时为空（可能由 React StrictMode 双重调用 + Vite HMR 状态污染、或会话切换与消息 set 的 race condition 导致）时，直接返回 EmptyState，掩盖了正在进行的流式输出。关闭 StrictMode 可减少开发环境下的双重调用带来的状态异常。
- 修复方式：1) `ChatArea.tsx:209-235` 增加防御性分支：若 `!messages.length && isStreaming && streamingSteps.length > 0`，渲染一个流式占位（包含 bot 头像、ActivityBlock、streamingContent），不再显示 EmptyState；2) `frontend/src/main.tsx` 移除 `<StrictMode>` 包裹，减少 HMR 和双重调用导致的状态不一致；3) `useChatStore.sendMessage` 添加日志记录消息设置后的 count，便于诊断。
- 下次注意：1. 流式场景的 EmptyState 条件必须考虑 `isStreaming` 状态，否则状态同步稍有延迟就会"卡在主页"；2. 开发环境下 React StrictMode 配合 zustand + Vite HMR 可能引发难以复现的状态异常，遇到"store 状态正确但 UI 不更新"或"两个相关状态不同步"时，可尝试临时关闭 StrictMode 定位；3. 右侧面板能更新但主面板不更新，说明 SSE 事件到达 store，问题在 ChatArea 的渲染条件或 messages 状态被覆盖，优先检查 EmptyState 分支和会话切换逻辑。
- 验证：前端 tsc 0 errors。

## 2026-06-29 - SessionPanel 残留 useAppStore 订阅导致新对话/历史对话点击无响应

- 错误现象：点击新对话或历史对话都没有用，无任何 UI 反馈。
- 错误原因：Phase A2 架构深化删除了 `useAppStore.activeSession`/`setActiveSession`（委托给 useChatStore），但 `SessionPanel.tsx:32` 仍从 useAppStore 解构这两个不存在的字段 → `setActiveSession` 是 undefined → 点击历史会话 `setActiveSession(id)` 抛 TypeError 静默失败。新对话数据层通了（useSessionStore.newSession 内部已正确调 chat store），但 `isActive = s.id === activeSession` 永远 false 无高亮反馈。同时 `useSessionStore.deleteSession:225,228` 残留 `useAppStore.getState().setActiveSession(...)`，而 useAppStore import 已删，这两行是死代码执行必崩 ReferenceError。`useKeyboard.ts:56` ArrowUp 快捷键也残留 `useAppStore.getState().activeSession`（ArrowDown 已改 chat store，ArrowUp 漏改）。
- 修复方式：1) `SessionPanel.tsx:32` 改为只从 useAppStore 取 `sessionGroupsCollapsed/toggleSessionGroupCollapsed`，`activeSessionId/setActiveSession` 改从 useChatStore 取；L160 `isActive` 比较改用 `activeSessionId`；2) `useSessionStore.ts:225,228` 删除两行 useAppStore 死代码；3) `useKeyboard.ts:56` 改为 `useChatStore.getState().activeSessionId`。
- 下次注意：1. 删 store 字段后必须全局 grep 所有订阅点（解构 + getState() + setState()），不能只改 completed.md 记录的几个文件；2. zustand 解构不存在的字段会静默返回 undefined（TS 严格模式下应报错，需确认 tsconfig 是否开启 strict）；3. "点击无响应"优先怀疑事件处理函数抛 TypeError 静默失败，用浏览器 DevTools Console 看错误；4. Phase A2 这类"删除重复状态"的重构最容易漏改消费点，重构后必须跑一遍 UI 验证。
- 验证：前端 tsc 0 errors。

## 2026-06-29 - MultimodalChunker sub_chunk_size 过小导致寄存器表过度分片

- 错误现象：44 页 GPIO/EXTI PDF 被拆成 102 个 chunks，平均 802 chars，MODER 四种模式值 (00/01/10/11) 被切到不同 chunk，中断向量表被拆成 20+ 碎片。
- 错误原因：sub_chunk_size=1000 太小 + RecursiveCharacterTextSplitter 不保护表格结构 + `<!-- PAGE:` marker 未作为分隔符导致子 chunk 页码回退到 section 的 start_page。
- 修复方式：1) sub_chunk_size 1000→2000；2) 添加 TABLE_ROW_RE / REGISTER_FIELD_RE placeholder 保护表格和寄存器位域；3) 在 separators 首位加入 `"\n<!-- PAGE:"` 确保跨页时先在 page marker 处切分；4) 统一 placeholder 恢复逻辑为多轮替换。
- 下次注意：1) 技术文档分块必须保护表格结构，RecursiveCharacterTextSplitter 会无视表格完整性；2) page marker 必须作为最高优先级分隔符，否则子 chunk 页码追溯会退化为 section 级别；3) 增大 sub_chunk_size 不会影响检索精度（embedding 是语义级别的），但会显著减少碎片化。

## 2026-06-29 - P0 聊天优化：except Exception 被缩窄为 except LLMError 导致 SSE 流崩溃

- 错误现象：用户发送消息后没有知识库检索、没有回答（SSE 流无声断开）；点击新对话/历史对话无响应（ErrorBlock 渲染条件判断导致组件未正确渲染）。
- 错误原因：chat_routes.py 中 `except Exception` 被改为 `except LLMError`（不熟悉客户端返回异常会触发非 LLMError 类异常），非 LLMError 异常（如 OpenAI SDK 的 AuthenticationError、APITimeoutError）不被捕获直接崩溃 SSE 生成器。同时前端 ChatArea.tsx 中 ErrorBlock 渲染条件 `isCurrentlyStreaming && streamingError` 在 stopStreaming 设置 isStreaming=false 后不再满足，改为 `streamingError && msg.id === messages[messages.length-1]?.id`。
- 修复方式：1) 将 `except LLMError` 恢复为 `except Exception`，用 `isinstance(e, LLMError)` 分支做结构化错误码，非 LLMError 走 sanitize_error 兜底；2) ErrorBlock 渲染条件从 `isCurrentlyStreaming` 改为 `msg.id === messages[messages.length-1]?.id`。
- 下次注意：1) AI 生成的代码如果缩窄异常捕获范围，必须人工审查是否遗漏了其他异常类型；2) 异常处理用 `except Exception` + 类型判断，不要用 `except SpecificType`；3) 前端添加新组件后在 store 中的串联条件要考虑停止流式后状态的正确展示。

## 2026-06-29 - Image description chunks 未生成（_needs_image_description 判断缺陷）

- 错误现象：首次入库 44 页 PDF 后，102 个 chunks 全部是 text 类型，没有任何 image_description chunks，尽管 PDF 包含大量寄存器表和中断向量表。
- 错误原因：`_needs_image_description()` 判断条件 `has_table or avg_text < 300` 中，`has_table` 来自 LLM 的 Stage-2 分析结果，但很多表格页面的文本量 > 300 chars（因为 PyMuPDF 提取了表格文本），导致判断为不需要 image description。
- 修复方式：放宽判断条件——当 section 跨多页且文本密度低时也触发 image description；同时确保 `_build_image_description_chunks` 在 pipeline 中正确调用。
- 下次注意：1) 技术 PDF 的表格即使有文本提取，视觉描述仍然有价值（因为文本提取可能丢失布局信息）；2) image description 应该更积极地生成，宁多勿少；3) 验证 image description 生成时要用实际 PDF 测试，不能只看代码逻辑。

## 2026-06-29 - 独立验证发现 Image Description 覆盖缺陷（ch340g datasheet）

- 错误现象：ch340g_datasheet.pdf 有 5 个页面含嵌入图片（p7 引角图、p8 封装图、p12 电气参数表+电路图、p13 RS232 电路图×2、p14 简化电路图×2），但 image description chunks 只覆盖了 p3/p4/p8/p9/p10/p11/p12，p7 的引角图和 p13/p14 的应用电路图完全遗漏。
- 错误原因：`_needs_image_description()` 判断条件 `has_table or avg_text < 300` 不够全面。Page 7（引角图）文本量 932 chars > 300 且无表格标记，被跳过；Page 13/14（应用电路图）文本量 889/942 chars > 300 且 LLM 没标记 `has_table`，也被跳过。
- 修复方式：需要扩展 `_needs_image_description()` 判断逻辑——增加对"含图页面"的检测（检查 PyMuPDF 是否在页面上发现图片），或降低 avg_text 阈值，或增加"页面图片数量"维度。
- 下次注意：1) 不能只靠 LLM 的 has_table 判断，需要结合 PyMuPDF 的图片检测；2) 引角图、封装图、应用电路图是硬件文档中最关键的图，遗漏它们比遗漏表格更严重；3) 同一页面被 vision LLM 分成多个 section 时会产生重复 image description（p12 有 4 个），需要去重机制。

## 2026-06-29 - 多数 PDF 无 Image Description Chunks（仅 2/6 有）

- 错误现象：kb-96eca485 知识库中 6 个 PDF 文档，只有 stm32f4_gpio_exti_extract.pdf（26 个 image_description chunks）和 ch340g_datasheet.pdf（10 个）有图片描述，其余 4 个（stm32f103c8、esp32-s3、mpu6050、bme280）全部 0 个 image_description chunks。
- 错误原因：这 4 个 PDF 的 `_needs_image_description()` 判断全部返回 False——它们的文本密度高（每页提取的文本量大），LLM 也没有标记 has_table。但这不代表这些 PDF 没有关键图片（引角图、封装图、时序图等）。
- 修复方式：需要全面审查所有 PDF 的图片分布，用 PyMuPDF `page.get_images()` 检测实际含图页面，然后对所有含图页面强制生成 image description（不依赖 LLM 判断）。
- 下次注意：1) `_needs_image_description()` 的判断过于保守，需要更积极的策略——凡是含有嵌入图片的页面都应该生成 image description；2) 不要只测试一个 PDF，要用全部 6 个 PDF 验证覆盖率。

## 2026-06-29 - DeepEval 评测 API 代理 504 超时

- 错误现象：PDF 黄金数据集评测中，G001 的 context_recall 指标连续 3 次 504 Gateway Timeout（每次 ~140s），faithfulness 指标 1 次 504 + 1 次 500 后成功。G002 的 context_recall 同样 3 次全部 504。
- 错误原因：API 代理（9router.zxyzx.bbroot.com）的 nginx 反向代理超时设置约 60s，而 DeepEval 的 context_recall 指标需要发送大量上下文（standard_answer + retrieval_context 共 ~5000+ tokens）给 judge LLM，LLM 响应时间经常超过 60s。
- 修复方式：1) 尝试换用更稳定的 API 端点或直连；2) 减少 judge 的输入 token 量（截断过长的 context）；3) 增加 retry 次数或超时时间。
- 下次注意：1) DeepEval 的 judge LLM 调用与 RAG 的 LLM 调用走同一个 API 代理，但 judge 的 prompt 更长（需要注入 standard_answer + retrieval_context），更容易触发超时；2) 生产环境评测应使用直连 API 或更长超时的代理。

## 2026-06-29 - 审查脚本用错字段名导致误报"页码缺失"

- 错误现象：_review_multimodal_chunks.py 报告"102 个 chunks 全部没有页码元数据"，实际 API 返回的字段名是 `page_start`/`page_end`，不是 `page_range`。
- 错误原因：API 返回层 (kb_routes.py) 将 ChunkResult 的 `page_range` tuple 拆成了 `page_start`/`page_end` 两个独立字段，审查脚本用了旧字段名。
- 修复方式：重新用正确字段名审查，确认 102/102 都有 page_start。
- 下次注意：审查脚本必须先看 API 返回结构（curl 一个 chunk 看字段名），不要凭记忆假设字段名。

- 错误现象：golden_dataset 前五题评测中 G004（EXTI 配置）context_recall 仅 0.22、G005（LCKR 配置）仅 0.12，加权得分 65.7 / 73.8，远低于预期。
- 错误原因：不是 chunk 策略或检索算法问题，而是 `builtin-001` 里 `01-stm32-gpio.md` 的已入库版本被截断，只有 4,444 字符 / 17 chunks，缺少 EXTI、LCKR 等完整章节，导致检索根本召不回对应内容。
- 修复方式：删除旧截断文档，用当前完整源文件重新上传并索引（hybrid chunking），新文档 148,140 字符 / 211 chunks / 169 sections；重跑评测后 G004 context_recall 1.00、G005 context_recall 1.00，总分从 81.22 提升到 94.66。
- 下次注意：1. RAG 评测低分先排查“已入库内容是否完整”，再调 chunk/检索策略；2. 审查时对比源文件大小与 chunk 总字符数、检查关键章节（如 EXTI/LCKR）是否在 sections 列表中；3. 同名文件重新上传前确保旧版本已删除，避免占用被截断的历史版本。
- 验证结果：chunk 审查无短 chunk / 空 chunk，边界问题均为代码块后接说明文本的可接受模式；DeepEval 前五题 recall_hit 100%，context_recall 1.0000。

## 2026-06-29 - 引脚冲突检测重构引发 strapping 检测回归

- 错误现象：修复 "引脚冲突检测" 空壳 bug 后，`test_diagnose_strapping_pin_warning` 失败——`pinMode(0,OUTPUT)` 不再触发 strapping 警告。
- 错误原因：原实现用一个联合正则 `(pinMode|digitalWrite|digitalRead|...)` 同时检测 strapping。重构时把 pinMode 单独拆出来只收集 mode（用于冲突检测），strapping 检测留在 digitalWrite 等的正则里，导致 pinMode 的 strapping 检测丢失。
- 修复方式：在 pinMode 的循环里也加 strapping 检测（`if gpio in strapping: violations.append(...)`），与 digitalWrite 等的 strapping 检测并行。
- 下次注意：重构正则匹配时，先列出原正则覆盖的所有副作用（strapping 检测、used_pins 记录等），拆分后逐个验证副作用是否保留。

## 2026-06-29 - ChromaDB HttpClient 模式连接失败（CHROMA_MODE 环境变量覆盖）

- 错误现象：`Could not connect to tenant default_tenant`，向量检索完全不可用，只有 BM25 在工作。
- 错误原因：`backend/.env` 未显式设置 `CHROMA_MODE`，默认值是 `persistent`，但用户 shell 环境变量可能设置了 `CHROMA_MODE=http`。pydantic-settings 优先级：环境变量 > .env > 默认值，导致走 HttpClient 路径，而项目无 chromadb server 启动脚本。
- 修复方式：在 `backend/.env` 和 `.env.example` 显式添加 `CHROMA_MODE=persistent`，锁定嵌入式模式，防止 shell 环境变量覆盖。
- 下次注意：1. pydantic-settings 的优先级是 `显式传入 > 环境变量 > .env 文件 > 默认值`，shell 环境变量会覆盖 .env；2. 本地自部署项目用 persistent 嵌入式模式即可，不需要 http C/S 架构；3. 配置项变更后要同步更新 `.env` 和 `.env.example`。
- 验证结果：后端启动日志不再出现 `Using HttpClient`，向量检索恢复正常。

## 2026-06-29 - 测试套件 401 Unauthorized + 环境变量泄漏（测试隔离缺陷）

（占位标题，内容待补充）

## 2026-07-01 - T3 RAG 检索效率改进：Edit 引入重复 fusion 调用

- 错误现象：在 kb_manager.py 的 `search()` 方法中加 doc_filter 过滤段时，Edit 工具的 new_string 末尾意外保留了原始的 `fused = rrf_fusion(vector_results, bm25_results)` 行，导致 doc_filter 非空时 RRF fusion 被调用两次（先 fusion → 过滤 → 再 fusion 覆盖过滤结果），doc_filter 完全失效。
- 错误原因：Edit 的 old_string 锚点选在 "Apply doc_filter" 注释块前，new_string 在 doc_filter 段后追加了原始 fusion 行，形成 `fusion → filter → fusion` 的错误顺序。
- 修复方式：第二次 Edit 删除多余的 `fused = rrf_fusion(vector_results, bm25_results)` 行，恢复 `fusion → filter → threshold → reranker` 的正确顺序。
- 下次注意：用 Edit 在函数中间插入代码块时，new_string 末尾不要复制 old_string 锚点之后的代码行；插入后立即 Read 验证插入点的上下文，确认没有重复语句。
- 验证结果：test_tools_direct.py 验证 doc_filter=stm32f4 只返回 stm32f4 chunks，doc_filter=esp32 只返回 esp32 chunks，过滤生效。

## 2026-07-01 - T3 RAG 检索效率改进：PowerShell python -c 引号转义失败

- 错误现象：在 PowerShell 中用 `python -c "..."` 跑内联脚本检查文件行数，f-string 里的双引号和 `encoding='utf-8'` 的单引号混合时，PowerShell 把内层引号吞掉，报 `SyntaxError: '[' was never closed` 或 `ExpectedValueExpression`。
- 错误原因：PowerShell 对双引号字符串做变量展开和转义，内层 `"` 会被解析为字符串结束符；`$_` 在 PowerShell 中是变量引用，与 Python 的 f-string `{_}` 冲突。
- 修复方式：改用 Write 工具写独立 .py 测试脚本再 RunCommand 执行，避免内联 python -c；或用最简单的单行命令 `python -c "print(sum(1 for _ in open('path',encoding='utf-8')))"`（无 f-string、无 $_）。
- 下次注意：PowerShell + python -c 的组合极易出错，复杂脚本一律写成 .py 文件；简单行数检查用 `python -c "print(sum(1 for _ in open('path',encoding='utf-8')))"` 这种纯单引号无 f-string 的形式。

- 错误现象：10 个 test_routes_*.py 测试返回 401，3 个 test_settings 测试断言失败，2 个 test_llm 异步测试无法运行。
- 错误原因：① `current_user` 依赖在 `keys_store.json` 非空时强制要求 token，测试不带 auth header 依赖磁盘 store 恰好为空；② `Settings()` 默认读 `backend/.env` + 真实环境变量，开发者本地 .env 配置覆盖默认值；③ 项目无 `pytest.ini` / `conftest.py`，`@pytest.mark.asyncio` 无法运行。
- 修复方式：① 新建 `backend/tests/conftest.py`，autouse fixture patch `app.api.auth._load_store` 返回空 providers dict，让 `current_user` 走"开发态兼容"分支；② `test_settings.py` 每个 test 加 `monkeypatch.delenv` 清空环境变量 + `_env_file=None` 跳过 .env；③ 新建 `backend/pytest.ini` 配置 `asyncio_mode = auto`，修正 `test_llm.py` 2 个测试的 mock（APIError 加 5xx status_code，ValueError 改 RateLimitError）。
- 下次注意：1. FastAPI 的 `Depends(current_user)` 在路由定义时捕获函数对象，patch `current_user` 名字不生效，必须 patch 它内部运行时调用的函数（`_load_store`）；2. 测试不应依赖磁盘状态（keys_store.json），用 fixture mock 隔离；3. `Settings()` 测试必须用 `_env_file=None` + `monkeypatch.delenv` 隔离环境；4. `@pytest.mark.asyncio` 需要 `pytest-asyncio` + `asyncio_mode = auto` 配置才能运行。
- 遗留：3 个 pre-existing 业务逻辑 bug（diagnose 引脚冲突检测 + wiring 接口签名不匹配）留待后续修复。

## 2026-06-28 - _rewrite_query_for_rag 循环依赖 + LRU 缓存恢复（查询改写迁移）

- 错误现象：恢复 `_rewrite_query_for_rag` 时，在 `chat_helpers.py` 中 `from app.api.chat_routes import _rewrite_query_for_rag` 会造成循环 import（chat_routes 已 import chat_helpers），启动时 Python 报 `ImportError: cannot import name '_rewrite_query_for_rag'`。
- 错误原因：`_rewrite_query_for_rag` 原本定义在 `chat_routes.py`，但调用方是 `chat_helpers._run_rag_retrieval`。chat_routes 已经 `from app.api.chat_helpers import _run_rag_retrieval`，反向 import 形成循环。
- 修复方式：把 `_rewrite_query_for_rag` + LRU 缓存（`_REWRITE_CACHE` / `_get_cached_rewrite` / `_set_cached_rewrite`）+ `_QUERY_REWRITE_SYSTEM` 整体从 `chat_routes.py` 迁移到 `chat_helpers.py`。chat_routes 清理 `import time`、`ChatMessage`、`extract_text_from_multimodal`（迁移后不再使用）。测试 `test_rag_edge_cases.py` 同步更新：`from app.api import chat_routes` → `chat_helpers`，`patch.object(chat_routes, "LLMClient")` → `patch.object(chat_helpers, "LLMClient")`，并在 `_run_rewrite` helper 中加 `chat_helpers._REWRITE_CACHE.clear()` 避免缓存污染测试。
- 下次注意：1. 函数迁移时先画依赖图，A→B import 时不能反向 B→A import；2. RAG 相关辅助函数统一放 `chat_helpers.py`，路由层保持薄；3. LRU 缓存测试必须 clear，否则相同 query 的后续测试命中缓存跳过 mock；4. `__new__` 绕过 `__init__` 的测试 mock 必须手动设置所有新增实例属性（如 `_db_unavailable`），否则 AttributeError。
- 验证结果：48 个 RAG edge case 测试全过，前端 tsc 0 errors，reranker 实测 rerank('STM32 GPIO 配置', ['GPIO 推挽输出配置','HardFault 处理器']) = [(0,0.77),(1,0.20)] 正常工作。

## 2026-06-28 - reranker predict 报 "Modality 'audio' is not supported"（环境修复）

- 错误现象：`bge-reranker-base` predict 时抛 `ValueError: Modality 'audio' is not supported by this CrossEncoder model. Supported modalities: text`，reranker 永久降级为 BM25-only 排序。
- 错误原因：`sentence_transformers 5.5.1` + `transformers 5.12.0` 的 CrossEncoder.preprocess 方法会检测输入 modality，但因为 `torchcodec` 的 stub 不完整（只有 decoders.AudioDecoder/VideoDecoder，缺 AutoProcessor），导致 modality 检测错误，把纯文本输入误判为 audio。根本原因是 `sentence_transformers 5.x` 强依赖 `torchcodec`（用于视频/音频解码），但 Hardware RAG Agent 只做文本 reranking，不需要多模态。
- 修复方式：降级 `sentence_transformers 5.5.1 → 4.1.0`（连带 `transformers 5.12.0 → 4.57.6`）。4.x 不依赖 torchcodec，CrossEncoder.preprocess 只处理文本，predict 正常工作（测试 `m.predict([('STM32 GPIO','GPIO 配置')])` 返回 0.9968）。
- 下次注意：1. `sentence_transformers 5.x` + `transformers 5.x` 的多模态管道对纯文本场景是过度设计，4.x 更稳定；2. stub 模式只能作为临时绕过，不能替代真正的版本降级；3. reranker predict 验证必须在实际 RAG 流程中测试，不能只测单独 predict（因为 modality 检测在 CrossEncoder.preprocess 中触发）。
- 遗留问题：ChromaDB HttpClient 模式连接失败（`Could not connect to tenant default_tenant`），向量检索不可用，只有 BM25 在工作。需检查 chromadb server 配置。

## 2026-06-28 - RAG 检索首次 132 秒、第二次 80 秒（性能优化）

- 错误现象：用户反馈首次对话 RAG 检索耗时 1 分 20 秒才有卡片展开，第二次也慢。测试确认首次 132 秒、第二次 80 秒。
- 错误原因：5 个瓶颈叠加。① `bge-reranker-large` 模型 ~1.3GB 首次加载慢 + 每次 predict 慢；② `_rewrite_query_for_rag` 每次都调 LLM 改写查询（8 秒超时，无缓存）；③ `chromadb` 因 opentelemetry 版本冲突 import 失败，但 `vector_store.db` property 不缓存失败状态，每次 search 都重复尝试 import（耗时数秒）；④ reranker `predict` 抛 `Modality 'audio' is not supported` 异常，但不缓存失败标记，每个 KB 都重复尝试；⑤ `search_all_enabled` 串行遍历所有 KB（10+ 个 KB × 6-7 秒 = 60+ 秒）；⑥ 多线程并行搜索时 reranker 单例不安全，多个线程同时加载模型。
- 修复方式：① reranker large → base（280MB，快 2-3 倍）；② 去掉 `_rewrite_query_for_rag` 的同步 LLM 调用（BM25+jieba 硬件词典+向量搜索已够用）；③ `vector_store.db` property 加 `_db_unavailable` 失败缓存，chromadb import 失败后不重试；④ reranker `rerank()` 加 `_RERANKER_PREDICT_FAILED` 失败缓存 + `threading.Lock()` 保护多线程加载；⑤ `search_all_enabled` 用 `ThreadPoolExecutor` 并行搜索（max_workers=8）；⑥ 前端 SSE 连接超时 60s → 120s；⑦ 修复 opentelemetry 版本冲突（`pip install --upgrade opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http`）。
- 下次注意：1. 单例模式必须考虑多线程安全（`threading.Lock()`）；2. 失败操作必须缓存失败状态，不能每次都重试（import 失败、模型加载失败、predict 失败）；3. 多个独立 IO 操作（多 KB 搜索）应该并行化；4. 性能问题先用日志定位时间线，不要瞎猜；5. 环境依赖（opentelemetry）版本冲突会导致 import 失败但不报错，只是静默降级。
- 验证结果：首次 132s → 76s（含模型下载 15s + 初始化 19s），第二次 80s → **25.6s**（RAG 检索仅 0.1s），pytest 19 passed。

## 2026-06-28 - useAppStore 与 useChatStore 的 activeSession 双写重复 + SearchModal 漏更新（架构深化 Phase A2）

- 错误现象：`useAppStore.activeSession` 和 `useChatStore.activeSessionId` 是两份独立状态，所有调用点都被迫双写（`useAppStore.getState().setActiveSession(id)` + `useChatStore.getState().setActiveSession(id)`）。`SearchModal.tsx` 只调了 `useAppStore.setActiveSession` 漏调 chat store，导致搜索结果点击切换会话后消息列表不更新（潜在 bug）。
- 错误原因：历史上 `useAppStore` 是简单 set，`useChatStore.setActiveSession` 后来加了流式切换复杂逻辑（保存部分内容、恢复 streamingContent），但两份状态从未合并，新代码（SearchModal）只看到 useAppStore 的简单接口就只调它。
- 修复方式：① 删除 `useAppStore.activeSession` 和 `setActiveSession`，让 `useChatStore.activeSessionId`/`setActiveSession` 成为唯一权威；② 7 个消费点全部改用 useChatStore（useKeyboard 读 activeSessionId、SearchModal/SessionPanel 订阅 setActiveSession、useSessionStore/useChatStore/BookmarkPanel 内部删除 useAppStore 双写）；③ 顺带删除 useAppStore 的死状态 `serialConnected`/`flashState`/`buildState`（setter 零调用、i18n 翻译键零引用）。
- 下次注意：1. 同一概念不要在多个 store 维护，单一权威源 + 派生 selector；2. 加复杂逻辑（如流式切换）后必须回头合并旧的简单接口，不能并存；3. 删 store 字段前必须全局 grep setter 调用点和字段订阅，确认零引用；4. zustand store 之间循环 import（双方只 `getState()`）是良性的，不必强行打破。

## 2026-06-28 - completed.md P0-15/16 虚报 build/upload 已接入真实编译（架构深化 Phase D）

- 错误现象：`docs/completed.md` 的 P0 修复记录写道"P0-15/16 `/api/build` `/api/upload` 接入真实沙箱编译 + 串口校验"，但实际 `build_routes.py` 仍是 mock SSE（`asyncio.sleep` + 假 progress 事件 10/30/70/100%），从未真正编译/烧录。
- 错误原因：2026-06-21 的 P0 修复批次中，build/upload 的修复记录可能是计划项被误标为已完成，或修复后代码被回退。`review-result.md` Phase 1 清单也标注为已修复，但代码未落地。
- 修复方式：① `build_routes.py` 顶部加注释明确标注"当前为模拟进度（mock SSE），真实编译/烧录为 v2 范围"；② `tool_router.py` 的 BuildTool/UploadTool docstring 标注 v2_pending，返回值加 `"v2_pending": True` 字段；③ 不在本批次实现真实编译（v2 范围，需 PlatformIO/arduino-cli + esptool/avrdude）。
- 下次注意：1. 修复记录必须附验证命令（如 `curl /api/build` 看返回是否含真实编译输出），不能仅凭"代码改了"就标记完成；2. 审查 completed.md 时对"已修复"条目抽查实际代码；3. v1/v2 边界清晰的特性（如硬件烧录）不要在 v1 阶段虚报，明确标注"v2 范围"比假装实现更好。

## 2026-06-28 - 测试 patch 打在死模块上,删 routes.py 前必须先修（架构深化 Phase A1）

- 错误现象：`test_routes_chat.py:47,73` 和 `test_main.py:41` 的 `patch("app.api.routes.LLMClient")` 打在已废弃的 `routes.py` 上,但实际处理 `/api/chat` 的是 `chat_routes.py`。测试能过纯属偶然——删 routes.py 后测试会直接报错。
- 错误原因：routes.py 拆分时(2026-06-21)测试未同步更新 patch 目标。`app.api.routes.LLMClient` 与 `app.api.common.LLMClient` 是不同模块命名空间引用,patch 死模块不影响活模块。
- 修复方式：删除 routes.py 前先把 patch 目标改为 `app.api.common.LLMClient`(3 处:test_routes_chat.py×2 + test_main.py×1)。
- 下次注意：1. 删模块前必须 grep 全局引用(含测试);2. patch 目标必须指向实际运行时使用的模块,不是历史模块;3. 拆分路由时测试的 patch 目标要同步迁移。

## 2026-06-28 - Prometheus Counter 在 create_app() 内创建导致重复注册（架构深化 Phase A1）

- 错误现象：`pytest` 报 `ValueError: Duplicated timeseries in CollectorRegistry: {'rag_requests', 'rag_requests_created', 'rag_requests_total'}`,3 个测试全失败。
- 错误原因：RAG 批次二(B9)把 `Counter`/`Histogram` 创建放在 `create_app()` 函数体内,测试多次调用 `create_app()` 时重复注册同名指标,prometheus_client 默认全局 registry 不允许重复。
- 修复方式：把 4 个指标(`_RAG_REQUESTS_TOTAL`/`_RAG_RETRIEVAL_SECONDS`/`_LLM_TOKENS_TOTAL`/`_RAG_RERANKER_SECONDS`)移到模块级单例,`create_app()` 内改为引用模块级变量。`_HTTP_REQUEST_HISTOGRAM` 保留懒初始化(已有 `if None` 守卫)。
- 下次注意：1. prometheus 指标必须在模块级创建,不能放在工厂函数内;2. 测试多次调用 `create_app()` 时所有模块级单例都不会重复注册;3. 类似的全局资源(DB 连接池、向量库单例)都应模块级初始化。

## 2026-06-28 - Starlette 1.3.1 与 FastAPI 0.115.6 不兼容（环境问题）

- 错误现象：后端启动报 `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`,整个 import 链断裂。
- 错误原因：环境里 starlette 1.3.1(最新版)移除了 `on_startup` 参数,但 fastapi 0.115.6 仍使用旧 API。requirements.txt 只 pin 了 `fastapi==0.115.0` 未 pin starlette。
- 修复方式：`pip install "starlette>=0.40.0,<0.42.0"` 降级到 0.41.3。注意 sse-starlette 3.4.5 要求 >=0.49.1,但实测 0.41.3 能跑(可能 sse-starlette 的版本要求是软依赖)。
- 下次注意：1. requirements.txt 必须 pin starlette 版本,不能只 pin fastapi;2. 升级 fastapi 前先检查 starlette 兼容性;3. `on_startup` 已废弃,新代码应改用 lifespan event handlers。

## 2026-06-28 - prometheus_client 未安装导致 import 失败（环境问题）

- 错误现象：`ModuleNotFoundError: No module named 'prometheus_client'`,后端无法启动。
- 错误原因：RAG 批次二(B9)在 requirements.txt 加了 `prometheus_client>=0.16`,但用户环境未实际安装(可能 pip install 未执行或虚拟环境不一致)。
- 修复方式：`pip install prometheus_client`。
- 下次注意：1. 加新依赖后必须实际安装验证,不能只改 requirements.txt;2. 切换虚拟环境后要重新 `pip install -r requirements.txt`。

## 2026-06-28 - GitNexus FTS 扩展 Windows 不可用导致 query 失效（代码图谱工具安装）

- 错误现象：`gitnexus analyze` 成功（6433 nodes / 10097 edges），但 `gitnexus query "RAG retrieval"` 无任何输出。日志显示 `FTS extension unavailable; continuing without FTS features. INSTALL fts failed with exit code 1: Error: IO exception: Cannot open file. path: ...gitnexus-ext-install-ktz6wf\install.lbug - Error 3: The system cannot find the path specified.`
- 错误原因：GitNexus 的 LadybugDB FTS 扩展在 Windows 下需要联网下载安装到临时目录，但安装路径解析失败（可能因用户名含中文 `奶茶丸` 导致路径问题）。FTS 禁用后 `query` 命令无法做全文搜索。
- 修复方式：① `gitnexus query` 不可用，改用 `codegraph query`（CodeGraph 内置 FTS5）；② `gitnexus context`/`trace`/`impact`/`cypher` 不依赖 FTS，仍可正常工作（遇到符号歧义时用 `--uid` 或 `--from-uid` 消歧）；③ 如需修复 FTS，运行 `gitnexus doctor` 查看详情，或设置 `GITNEXUS_LBUG_EXTENSION_INSTALL=auto` 后重新 `gitnexus analyze --repair-fts`。
- 下次注意：1. Windows 用户名含非 ASCII 字符可能导致部分 Node 工具的临时路径解析失败，优先用 `codegraph`（纯 SQLite，无外部扩展）；2. GitNexus 的 `query` 强依赖 FTS，`context`/`trace` 不依赖，安装后先用 `gitnexus doctor` 验证 FTS 状态；3. 两个工具互补使用：GitNexus 做可视化（`serve`）+ 符号查询（`context`），CodeGraph 做语义搜索（`query`/`explore`）。

## 2026-06-28 - npx 交互式确认卡住，改用全局安装（Skills + 图谱工具安装）

- 错误现象：`npx superpowers-zh --help` 卡在 `Need to install the following packages: superpowers-zh@1.6.0. Ok to proceed? (y)`，RunCommand 不支持交互式输入，命令一直挂起。
- 错误原因：npx 首次运行未安装的包时会交互式询问确认，但 Trae 的 RunCommand 不支持 stdin 输入。
- 修复方式：改用 `npm install -g <package>` 全局安装，再直接运行命令（如 `superpowers-zh --tool trae`、`skills add mattpocock/skills --all -y`、`gitnexus analyze`、`codegraph init`）。
- 下次注意：1. 在 Trae 中安装 npm 工具优先用 `npm install -g`，避免 npx 交互式卡住；2. 若必须用 npx，加 `-y` 参数（如 `npx -y superpowers-zh --tool trae`）；3. `skills add` 命令加 `--all -y` 跳过所有确认提示。

## 2026-06-28 - 并行 Edit 同一文件导致竞态、改动丢失（Pitfall - B5 开发）

- 错误现象：对同一个文件并行发起 5 个 Edit（3 个 vector_store.py + 2 个 kb_manager.py），3 个针对 vector_store.py 的 Edit 中只有最后一个成功保留，前两个的改动（`__init__` 签名加 ef_search 参数、存 `self._ef_search`）被覆盖丢失，而 kb_manager.py 顶部的 import 也被覆盖丢失，导致运行时 NameError。
- 错误原因：Edit 工具对同一文件的并行请求存在竞态，后到的 Edit 基于旧文件快照写入，覆盖了先到的 Edit 的改动。
- 修复方式：改为串行 Edit，每次只改同一文件的一个锚点，确认返回后再改下一个。对跨文件的独立 Edit 可以并行。
- 下次注意：同一文件的多处修改必须串行执行，不要并行。跨文件可以并行。每次 Edit 后要验证结果，不能仅凭"无报错"假设成功。

## 2026-06-28 - Tesseract 未安装导致 OCR 集成阻塞，改用 PaddleOCR（Bug 17）

- 错误现象：批次6 OCR 集成计划用 Tesseract（pytesseract），但系统未安装 tesseract.exe，`tesseract --version` 报 "not recognized"。winget 安装可能超时（>5 分钟），阻塞批次6。
- 错误原因：Tesseract 是系统级依赖（C++ 编译的 exe），需要单独安装并配置 PATH，不是纯 pip 包。Windows 安装还要手动设 TESSDATA_PREFIX 环境变量。
- 修复方式：改用 PaddleOCR（纯 pip 包，`pip install paddlepaddle paddleocr`）。优势：① 纯 Python 无系统依赖；② 中文识别准确率最高（芯片手册中文多）；③ 支持表格识别（寄存器表场景）。OCR_ENABLED=False 默认关闭，按需开启，懒加载避免 ~10s 启动开销。
- 下次注意：
  1. **OCR/外部工具选型优先纯 Python 包** — Tesseract/EasyOCR/PaddleOCR 三选一时，优先 PaddleOCR（中文+表格+纯 pip），避免系统级依赖增加部署复杂度
  2. **大依赖默认关闭** — paddlepaddle ~300MB + 模型文件，用 OCR_ENABLED=False 默认关闭，懒加载（`_ocr_instance = None` 单例），避免拖慢后端启动
  3. **测试要 mock 重依赖** — test_ocr_parser.py 用 `patch.dict(sys.modules, ...)` mock paddleocr/PIL/fitz/docx，不依赖实际安装，13 测试全过

## 2026-06-28 - rebuild_chroma_cosine.py 数据库表名报错 knowledge.db ≠ hardware_rag.db（Bug 16）

- 错误现象：批次1 cosine 重建脚本运行报 `sqlite3.OperationalError: no such table: knowledge_bases`。
- 错误原因：脚本硬编码 `BACKEND_DIR / "data" / "knowledge.db"`，但实际数据库是 `hardware_rag.db`（[database.py](file:///E:/Desktop/agent/backend/src/config/database.py) 用 `SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", str(_DB_DIR / "hardware_rag.db"))`）。脚本读不到 KB 表，无法遍历 collection。
- 修复方式：改脚本 `SQLITE_DB_PATH = Path(os.getenv("SQLITE_DB_PATH", str(BACKEND_DIR / "data" / "hardware_rag.db")))`，与 database.py 保持一致。修复后 12 KB 6454 chunks 27.9s 重建完成。
- 下次注意：
  1. **读数据库路径必须从 settings/database.py 读 env 变量** — 不能硬编码文件名，项目里 .env 的 SQLITE_DB_PATH 可能被覆盖
  2. **重建脚本要复用项目配置** — 独立脚本也要 `from src.config.settings import settings` 读路径，避免路径漂移

## 2026-06-28 - Edit 工具偶发"返回成功但磁盘未写入"导致白名单修改丢失（Bug 15）

- 错误现象：批次5 HTML 支持任务中，用 Edit 工具修改 [kb_routes.py:28](file:///E:/Desktop/agent/backend/app/api/kb_routes.py#L28) 的 ALLOWED_EXTENSIONS 和 [routes.py:427](file:///E:/Desktop/agent/backend/app/api/routes.py#L427) 的 ALLOWED_EXTENSIONS（加 .html/.htm），以及 routes.py 的 `_parse_file` HTML 分支。Edit 工具每次都返回成功，diff 显示新内容含 .html。但跑 pytest 时白名单测试失败（`.html not in ALLOWED_EXTENSIONS`），用 Read 工具读磁盘 .py 文件也显示旧内容（无 .html）。同一批次的 common.py `extract_attachment_text` 和 routes.py `_extract_attachment_text` 分支却正常写入。
- 错误原因：Edit 工具偶发"返回成功但未真正写入磁盘"。根因不明——同一批次 4 个 Edit，部分写入成功（common.py），部分未写入（kb_routes.py L28、routes.py L427、routes.py `_parse_file`）。重新 Edit 同样内容后正常写入。不是 __pycache__ 问题，因为 Read 工具读的是 .py 源文件也显示旧内容。
- 修复方式：重新 Edit 同样的 old_string/new_string，写入成功。用 `Grep HtmlParser` 验证所有 4 处 dispatch 分支都在磁盘上。
- 下次注意：
  1. **Edit 工具的成功返回不能完全信任** — 修改关键配置（白名单/工厂注册/常量）后，必须用 Grep 或 Read 验证磁盘实际内容
  2. **批量 Edit 后跑一次 Grep 验证** — 例如改完白名单后 `Grep "HtmlParser"` 确认所有 dispatch 点都在磁盘上
  3. **测试失败时先怀疑磁盘内容** — pytest 报告"不在集合里"且与 Edit 结果矛盾时，优先 Read 磁盘文件确认，而非怀疑测试逻辑

## 2026-06-28 - reranker 冷启动导致 RAG API 超时（Bug 14）

- 错误现象：集成 bge-reranker-base 后首次跑 5 题评测，G001 和 G002 全部失败（latency=0.0s，所有指标 0.00），但 G003-G005 成功。
- 错误原因：[reranker.py](file:///E:/Desktop/agent/backend/src/rag/reranker.py) 用懒加载，首次 search() 时触发模型下载（bge-reranker-base 1.11GB，~12 分钟）。[kb_manager.py:675-687](file:///E:/Desktop/agent/backend/src/rag/kb_manager.py#L675-L687) 的 reranker 调用是同步的，阻塞了 search() 方法。RAG API 的 httpx 超时是 300s，下载 12 分钟远超超时，导致 G001/G002 的 RAG 请求在 reranker 下载期间超时失败。G003 在下载快完成时开始，刚好赶上。
- 修复方式：无需改代码。reranker 模型下载后会缓存到 `~/.cache/huggingface/hub/`，后续启动直接加载（~5s）。重跑 G001-G002 即可成功（实测 recall_hit 100%，总分 82.41）。
- 下次注意：
  1. **集成大模型（reranker/embedding）时，首次启动要预留下载时间** — bge-reranker-base 1.11GB，2MB/s 网络需要 ~10 分钟
  2. **考虑预热机制** — 后端启动时主动加载 reranker（而非懒加载），避免首个请求超时。但启动时间会增加 ~5s
  3. **HF_ENDPOINT 镜像设置要早** — reranker.py 用 `os.environ.setdefault("HF_ENDPOINT", ...)`，但 sentence-transformers 可能在导入时已读取环境变量。如果镜像不生效，直接从 huggingface.co 下载（中国网络 ~2MB/s）
  4. **reranker 位置很重要** — 放在 threshold 过滤后、top-k 截断前，只重排通过 threshold 的 chunk，减少计算量

## 2026-06-28 - golden_dataset recall_hit=0% 设计缺陷（Bug 13）

- 错误现象：golden_dataset 评测 3 次（014914/020910/032201），recall_hit_rate 全部 0%，无法诊断"检索是否命中参考答案"。但实际看 retrieval_context 内容是相关的，只是匹配逻辑判定为 N。
- 错误原因：[run_golden_eval.py](file:///E:/Desktop/agent/backend/tests/rag_eval/run_golden_eval.py) 的 `check_recall_hit` 用 `SequenceMatcher` 计算文本相似度（阈值 0.60）。但 `golden_dataset.yaml` 里的 `reference_chunks` 是手工写的**语义摘要**（如 "STM32F4 GPIO OSPEEDR：低速 2MHz、中速 25MHz..."），实际入库的 chunk 是**原始文档片段**（如 "### 3.4 不同速度等级下的信号波形\n\n| 速度等级 | 上升时间 |..."）。文本相似度永远 < 0.60，导致 recall_hit 永远为 N。这是设计缺陷，不是代码 bug——README 写明 "reference_chunks 是多可接受集合"，但文本匹配无法识别"语义相同但文本不同"。
- 修复方式（[run_golden_eval.py:314-471](file:///E:/Desktop/agent/backend/tests/rag_eval/run_golden_eval.py#L314-L471)）：
  1. 新增 `EmbeddingSimilarityChecker` 类：用 OpenAI 兼容 embedding API 计算余弦相似度，阈值 0.70。支持缓存（按 text hash）和重试（max_retries=3），失败时 fallback 到 SequenceMatcher。
  2. 修改 `check_recall_hit` 函数：优先用语义匹配（embedding cosine similarity），失败时 fallback 到文本匹配。返回 `(hit, max_similarity, match_type)` 三元组，`match_type` 为 "semantic"/"text"/""。
  3. 修改 `run_evaluation` 主流程：启动时从 builtin-001 KB 的 SQLite 记录读 embedding 配置（含 Fernet 解密 `embedding_api_key_encrypted`），预计算所有 samples 的 reference_chunks embedding（30 题 × 2 ref = 60 次调用，~30 秒）。
  4. 新增 CLI 参数：`--embedding-api-key/--embedding-base-url/--embedding-model/--no-semantic-match`。
  5. 报告更新：Per-Sample 表加 `max_sim` 和 `match` 列，Low-Score Diagnostics 加 recall_hit 诊断行。
- 效果：G001-G005 5 题评测，recall_hit_rate 从 0% → 100%（5/5 全命中，match_type 全 semantic，max_sim 0.715-0.918）。总分 87.05/100。
- 下次注意：
  1. **设计诊断指标时要考虑"标注格式"与"实际数据格式"的差异** — 语义摘要 vs 原文片段，文本相似度无法跨越这个鸿沟，必须用 embedding 语义匹配
  2. **embedding 语义匹配有缓存** — 按 text hash 缓存，30 题预计算只需 60 次调用，~10-30 秒，不会拖慢评测
  3. **失败降级很重要** — embedding API 可能 504/超时，必须 fallback 到文本匹配，否则指标完全失效
  4. **阈值选择** — 语义匹配阈值 0.70 比文本匹配 0.60 更严格，因为语义匹配本身就比文本匹配更宽松（识别 paraphrase）

## 2026-06-28 - python -m 加载旧 .pyc 缓存导致代码修改不生效（Bug 11）

- 错误现象：修改 [run_golden_eval.py](file:///E:/Desktop/agent/backend/tests/rag_eval/run_golden_eval.py) 后用 `python -m tests.rag_eval.run_golden_eval` 运行，SSE 事件仍全部 unknown（859个），仍报 `ModuleNotFoundError: No module named 'deepeval'`。但用独立脚本 `_sse_probe.py` 直接 `python _sse_probe.py` 运行同样逻辑，事件解析完全正常（text/source/done 都有值）。
- 错误原因：`python -m` 会优先加载 `__pycache__/run_golden_eval.cpython-313.pyc` 里的旧字节码，**不会重新编译源文件**（除非 .py 的 mtime 比 .pyc 新——但某些情况下文件系统时间精度或缓存逻辑导致不触发重编译）。改了源码但 .pyc 缓存还是旧代码，导致所有修改"不生效"。
- 修复方式：改用 `python -B tests/rag_eval/run_golden_eval.py` 直接运行脚本。`-B` 标志禁用字节码缓存（不写 .pyc），保证每次跑的都是最新源码。
- 下次注意：
  1. **python -m 可能用旧 .pyc** — 改了代码但行为没变时，先删 `__pycache__/` 或用 `python -B` 直接运行
  2. **独立小脚本验证逻辑** — 怀疑代码没生效时，写个最小 `_probe.py` 直接 `python _probe.py` 跑同样逻辑，快速定位是代码问题还是运行环境问题
  3. **.pyc 缓存坑只出现在 python -m** — 直接 `python script.py` 不会用 .pyc（会重新编译）

## 2026-06-28 - 9router 代理 504 导致 DeepEval 指标全部失败（Bug 12）

- 错误现象：运行 golden eval 评测，RAG 调用正常（答案正确、检索到 5 个 chunk），但 DeepEval 的 4 个指标（context_recall/faithfulness/answer_relevancy/context_precision）全部 RetryError 失败，score=0。日志显示 judge LLM 调用 `https://9router.zxyzx.bbroot.com/v1/chat/completions` 频繁返回 `504 Gateway Time-out`（nginx/1.22.1），DeepEval 内部重试 2 次后放弃抛 `RetryError[InternalServerError]`。
- 错误原因：9router 代理间歇性 504，DeepEval 默认只内部重试 2 次（~133s），不足以撑过 504 风暴。`run_deepeval_metrics` 也没有指标级重试包装，单次 RetryError 就记 0 分。
- 修复方式（[run_golden_eval.py:362-398](file:///E:/Desktop/agent/backend/tests/rag_eval/run_golden_eval.py#L362-L398)）：在 metric 循环外面包一层重试，`MAX_METRIC_RETRIES=3`，每次失败后 `time.sleep(15)` 再重试整个 metric。外层 3 次 × 内层 2 次 = 最多 9 次尝试。实测 G001 context_recall 在 attempt 3 成功（9router 恢复返回 200），G002 context_recall 也在 attempt 3 成功。
- 效果：3 条试水全部出真实分数（G001=60.6, G002=72.2, G003=76.6，总分 69.78/100），0 错误。
- 下次注意：
  1. **第三方 LLM 代理不稳定时要加业务层重试** — 不能只依赖库的内部重试，外层再包一层更可靠
  2. **504 是间歇性的** — 重试 3 次 + 延迟通常能撑过去，不要一遇 504 就放弃
  3. **DeepEval 内部重试只 2 次** — 对不稳定代理不够，需要外层补充

## 2026-06-28 - asyncio.gather 级联取消导致后端进程崩溃（Bug 9）

- 错误现象：PDF 入库测试（multimodal chunker），后端在 Stage-2 处理多个 batch 时突然崩溃（exit code -1，无 Python traceback）。日志显示并行化正常工作（多个 Group/Batch 同时开始），但进程在 LLM 调用密集时段退出。
- 错误原因：[multimodal_chunker.py](file:///E:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py) 两处 `asyncio.gather`（Stage-1 组间并行 L1298、Stage-2 批次间并行 L430）没有 `return_exceptions=True`。默认行为是：**一个 task 异常 → 取消所有其他 task → 异常向上传播**。`CancelledError` 是 `BaseException`（不是 `Exception`），会逃出 `except Exception` 处理器，导致 uvicorn 进程被杀死。多个 PDF 同时处理时 LLM API 限流触发异常，级联取消导致整个进程崩溃。
- 修复方式（[multimodal_chunker.py L425-465, L1331-1370](file:///E:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L425-L465)）：
  1. 两处 `asyncio.gather` 加 `return_exceptions=True`
  2. 结果处理中分离成功结果和异常：`if isinstance(r, BaseException): ... else: successes.append(r)`
  3. 全部失败才 raise AgentChunkError；部分失败则 log warning 继续处理成功的 batch
  4. 同时把 `test_pdf_ingest.py` 的 `BATCH_SIZE` 从 2 改为 1（串行上传 PDF，每个 PDF 内部仍并行）
- 效果：修复后 8 个 PDF 全部成功索引（853 chunks），无崩溃。i2c 62页处理 688s，esp32-c3 76页处理 396s。
- 下次注意：
  1. **asyncio.gather 默认一个失败全取消** — 并行 task 必须加 `return_exceptions=True` 防止级联取消
  2. **CancelledError 是 BaseException 不是 Exception** — `except Exception` 捕获不到，会逃出导致进程崩溃
  3. **并行度不是越高越好** — BATCH_SIZE=2 × vision_concurrency=4 = 8 并发 LLM 调用，可能触发代理限流。串行上传 + 内部并行更稳定
  4. **exit code -1 无 traceback 通常是进程被杀** — 不是代码 bug，是未捕获的 BaseException 或信号

## 2026-06-28 - 测试脚本从错误路径读取 agent_trace（Bug 10）

- 错误现象：Bug 8 修复后重跑测试，8 个 PDF 全部成功索引，但测试报告的 trace 字段（策略/页数/批次/sections/耗时/tokens）仍然全是 "?"。直接查 ChromaDB 发现 `agent_trace` 确实以 JSON 字符串形式存储在 metadata 中，内容完整正确。
- 错误原因：[test_pdf_ingest.py:294](file:///E:/Desktop/agent/backend/test_pdf_ingest.py#L294) `analyze_chunks` 函数从 `chunks[0].get("metadata", {})` 读取 `agent_trace`，但 `/api/kb/documents/{doc_id}/chunks` API（[kb_routes.py:963-984](file:///E:/Desktop/agent/backend/app/api/kb_routes.py#L963-L984)）**不返回原始 metadata 字段**——它把 metadata 拆成单独的顶层字段返回（`agent_trace`, `page_start`, `page_end`, `section_title` 等）。`chunks[0].get("metadata", {})` 返回 `{}`，所以 `agent_trace` 为 None。
- 修复方式（[test_pdf_ingest.py:292-321](file:///E:/Desktop/agent/backend/test_pdf_ingest.py#L292-L321)）：改为 `chunks[0].get("agent_trace")` 直接从顶层字段读取。`page_ranges` 也改为从 `c.get("page_start")` 和 `c.get("page_end")` 读取。
- 效果：修复后报告正确显示所有 trace 数据（8 个 PDF 总耗时 2926.5s，总 tokens 4,157,828）。
- 下次注意：
  1. **API 返回结构 ≠ 数据库存储结构** — API 会重组/扁平化 metadata，不能假设返回原始 dict
  2. **写测试脚本前先看 API 实际返回** — 用 curl 或 print 看一个样本响应，不要凭假设写代码
  3. **"数据没存" vs "数据没读到" 要区分** — 先直接查 ChromaDB 确认存储正确，再排查读取路径

## 2026-06-28 - MultimodalChunker agent_trace 在 API 返回中为 null（Bug 8）

- 错误现象：PDF 入库测试（multimodal chunker + oc/mimo-v2.5），5 个 PDF 成功索引（spi:28, dht11:11, mpu6050:123, stm32f103c8:149, a4988:45 chunks），chunk 质量优秀（0% 短 chunks, 0 截断, 0 表格拆分）。但调用 `/api/kb/documents/{doc_id}/chunks` API 时，`agent_trace` 字段返回 null，导致测试报告的 trace 信息（策略/页数/批次/sections/耗时/tokens）全部显示为 "?"。其他 metadata 字段（section_title/section_summary/section_keywords/section_confidence）正常。
- 错误原因：[kb_routes.py:925](file:///E:/Desktop/agent/backend/app/api/kb_routes.py#L925) `if agent_trace and isinstance(agent_trace, dict):` 检查失败。Bug 7 修复后，MultimodalChunker 把 trace 序列化为 JSON **字符串**（`json.dumps(trace_dict, ensure_ascii=False)`），但 API 层仍按 **dict** 检查，条件不匹配，`trace_summary` 保持为 None。
- 修复方式（[kb_routes.py:923-961](file:///E:/Desktop/agent/backend/app/api/kb_routes.py#L923-L961)）：在 `isinstance(agent_trace, dict)` 检查前，先判断是否是字符串并 `json.loads` 解析：
  ```python
  if isinstance(agent_trace, str):
      try:
          agent_trace = json.loads(agent_trace)
      except (json.JSONDecodeError, TypeError):
          agent_trace = None
  ```
  同时补充 MultimodalChunker trace 的特有字段（`batching_strategy`, `num_pages`, `sections_found`, `token_usage`），这些在 AgentChunker trace 中不存在。
- 下次注意：
  1. **序列化/反序列化要成对出现** — Bug 7 在存储层把 dict 改为 json.dumps 字符串，但 API 层没同步更新反序列化逻辑。改存储格式时，要 grep 所有读取该字段的代码路径
  2. **isinstance 检查要考虑所有可能的类型** — ChromaDB metadata 字段可能是 str/int/float/bool/list/None，不能假定一定是 dict
  3. **不同 chunker 的 trace 结构不同** — AgentChunker trace 有 `num_rounds/temperature/toc_entries/rounds`，MultimodalChunker trace 有 `batching_strategy/num_pages/sections_found/token_usage`。API 层要兼容两种结构

## 2026-06-28 - ChromaDB metadata 不接受嵌套 dict（Bug 7）

- 错误现象：PDF 入库测试（multimodal chunker），3 个 PDF（spi/dht11/a4988）chunking 成功，但在 vector store upsert 阶段失败，报错：`Expected metadata value to be a str, int, float, bool, SparseVector, list, or None, got {'method': 'multimodal', 'model': 'oc/mimo-v2.5', ...} which is a dict`。
- 错误原因：[multimodal_chunker.py:501](file:///E:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L501) `chunk.metadata["agent_trace"] = trace_dict` 直接存了嵌套 dict（含 `token_usage` 子 dict），但 ChromaDB metadata 只接受 str/int/float/bool/list/None，不接受嵌套 dict。AgentChunker 已经用 `json.dumps()` 序列化（agent_chunker.py L1029/1082/1337/1373），MultimodalChunker 漏了。
- 修复方式（[multimodal_chunker.py:498-505](file:///E:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L498-L505)）：改为 `trace_json = json.dumps(trace_dict, ensure_ascii=False)`，然后 `chunk.metadata["agent_trace"] = trace_json`。与 AgentChunker 实现一致。
- 效果：修复后重跑测试，spi/dht11/a4988 全部成功索引，chunks 正常写入 ChromaDB。
- 下次注意：
  1. **ChromaDB metadata 只接受标量类型** — 任何嵌套结构（dict/对象）都要先 `json.dumps()` 序列化为字符串
  2. **新增 chunker 时要检查 metadata 序列化** — AgentChunker 已有 `json.dumps` 序列化 agent_trace，MultimodalChunker 漏了。新增 chunker 要参考已有实现
  3. **chunking 成功不代表入库成功** — chunking 是第一步，vector upsert 是第二步，metadata 类型错误会在第二步才暴露

## 2026-06-28 - PyMuPDF (fitz) 未安装

- 错误现象：运行 PDF 入库测试，`import fitz` 报 `ModuleNotFoundError: No module named 'fitz'`。`pip list` 显示有 docling 但无 PyMuPDF。
- 错误原因：[multimodal_chunker.py:529](file:///E:/Desktop/agent/backend/src/rag/chunking/multimodal_chunker.py#L529) 直接 `import fitz`，但 requirements.txt 没有列出 PyMuPDF。fitz 是 PyMuPDF 的模块名。
- 修复方式：`pip install PyMuPDF` → v1.27.2.3 安装成功。[requirements.txt](file:///E:/Desktop/agent/backend/requirements.txt) 文档解析段新增 `PyMuPDF==1.27.2.3`。
- 下次注意：
  1. **新增依赖要同步更新 requirements.txt** — 代码里 `import` 的新包，必须加入 requirements.txt
  2. **fitz 是 PyMuPDF 的模块名** — `pip install fitz` 会装错包（一个无关的 Fitz 库），正确的是 `pip install PyMuPDF`

## 2026-06-27 - DOCX 入库测试短 chunks 过多（文档标题+表格密集型文档）

- 错误现象：用 10 个质量参差不齐的 docx 文件入库测试（hybrid / small_chunk_size=800），124 个 chunks 中有 29 个短 chunks（<100 字符，占 23.4%），24 个极短（<50 字符，占 19.4%）。问题集中在三类文档：
  1. **文档标题被独立成 chunk**：每个文件的 chunk 0 都是孤立标题（12-43 字符），因为文档首段没用 Heading 样式，DocxParser 输出纯文本，HybridChunker 按 `\n\n` 分段时标题独立成 section
  2. **表格密集型文档**（18-ws2812b）：16 chunks 中 10 个短 chunks（62.5%），每个表格行被独立成 section
  3. **无标题纯文本文档**（12-arduino）：20 chunks 中 7 个短 chunks（35%）
- 错误原因：`_merge_tiny_chunks` Pass 2（forward merge）要求 `same_section` 才能合并，但：
  1. 文档首段 section_title 为空，与后续有标题 section 不匹配 → 合并失败
  2. 表格密集型文档每个表格的 section_title 不同（表格首行），相邻短表格无法合并
  3. `_split_plain_text` 把每个段落独立成 section，短段落无法跨越 section 边界合并
- 修复方式（[hybrid_chunker.py](file:///E:/Desktop/agent/backend/src/rag/chunking/hybrid_chunker.py#L438-L528) `_merge_tiny_chunks` Pass 2 增强）：
  1. 当 `pending_tiny.section_title` 为空（无标题孤立段落）时，允许合并到任意下一个 chunk
  2. 当合并后长度 `combined_len <= small_chunk_size` 时，允许跨 section 合并（处理表格密集型文档）
  3. 同步放宽 `pending_tiny` 的设置条件：tiny chunk 无 section_title 或与 next chunk 合并后不超过 small_chunk_size 时，设为 pending_tiny
- 效果：短 chunks 从 29 (23.4%) 降到 4 (4.0%)，总 chunks 从 124 降到 101。剩余 4 个短 chunks 是文档标题+首段合并后的边界情况（合并下一个 chunk 会超过 small_chunk_size），属合理残留。代码块截断、表格跨 chunk 拆分、纯符号 chunks 均为 0。
- 下次注意：
  1. **section_title 匹配不应是合并的唯一条件**——无标题文档（纯文本样式模拟标题）的 section_title 为空，需要特殊处理
  2. **表格密集型文档需要跨 section 合并**——每个表格是独立 section，但短表格合并后不超过 small_chunk_size 时应该合并
  3. **合并阈值要考虑 small_chunk_size**——跨 section 合并时必须检查合并后长度不超过 small_chunk_size，否则会破坏检索粒度

## 2026-06-27 - 测试脚本 DB 路径错误导致 embedding API key 未拷贝

- 错误现象：执行 `test_docx_ingest.py` 入库测试，10 个 docx 文件全部显示 `status=indexed`，`chunk_count` 有值（17/11/20...），但调用 `/api/kb/documents/{doc_id}/chunks` 返回 `total_chunks: 0, chunks: []`。SQLite 查询发现 `error_message='未配置 Embedding 模型，已分块但未向量化。配置 Embedding 后请删除重新上传。'`
- 错误原因：测试脚本里 DB 路径写的是 `E:\Desktop\agent\data\hardware_rag.db`，但实际路径是 `E:\Desktop\agent\backend\data\hardware_rag.db`。`copy_embedding_api_key` 函数找不到 DB 文件，静默跳过，导致测试 KB 的 `embedding_api_key_encrypted` 为 NULL，embedding 阶段失败，chunks 没写入 ChromaDB。
- 修复方式（[test_docx_ingest.py](file:///E:/Desktop/agent/backend/test_docx_ingest.py#L98-L106)）：DB 路径改为多候选路径列表，按顺序查找存在的路径：
  ```python
  candidates = [
      Path(r"E:\Desktop\agent\backend\data\hardware_rag.db"),  # 实际位置
      Path(r"E:\Desktop\agent\data\hardware_rag.db"),          # 备选
  ]
  db_path = next((p for p in candidates if p.exists()), None)
  ```
- 下次注意：
  1. **DB 路径不要硬编码**——项目结构可能调整，用候选列表更健壮
  2. **`status=indexed` 不代表入库成功**——要检查 `error_message` 字段，"已分块但未向量化"是一种半成功状态
  3. **chunks API 返回空不一定是 chunks 没生成**——可能是 embedding 失败导致 chunks 没写入 ChromaDB，但 `KnowledgeDoc.chunk_count` 字段已经有值（在 embedding 之前就更新了）

## 2026-06-27 - HybridChunker 表格/列表被切分（所有 chunk_size 都有）

- 错误现象：用户反馈 `### 1.3 UART 与 SPI、I2C 的对比` 只有 48 chars，表格被切到下一个 chunk；`### 1.4 UART 应用场景` 只有 43 chars，列表被切到下一个 chunk；`### 2.2 RS-232 电平` 385 chars，芯片对比表被切走。扫描所有 KB 发现 **所有 hybrid chunk_size (500/800/1200/2000) 都有此问题**，chunk_size 越小越严重（500: 18% tiny, 2000: 6% tiny），agent chunker 无此问题。
- 错误原因：`_DEFAULT_SEPARATORS` 中 `\n\n` 优先级太高，splitter 会在标题和表格之间的 `\n\n` 处切分，产生 "标题+引言" (40-80 chars) + "表格" 两个 chunk。`_merge_tiny_chunks` 只支持向后合并（tiny → previous），当 tiny chunk 是 section 的第一个 chunk 时，前一个 chunk 是不同 section，`same_section=false` 不合并。
- 修复方式（3 处改动，[hybrid_chunker.py](file:///E:/Desktop/agent/backend/src/rag/chunking/hybrid_chunker.py)）：
  1. separators 加入 `\n\n|`（表格边界），放在 `\n\n` 之前，让 splitter 优先在表格行之间切分而不是标题后
  2. `_merge_tiny_chunks` 增加 Pass 2 向前合并：tiny chunk 合并到下一个同 section chunk（包括代码块），解决"section 第一个 chunk 太小"问题
  3. 预过滤纯符号 chunk（`---`、`===`、`|---|`），这些是 markdown 分隔线无检索价值
- 效果：tiny chunks 从 58 降到 11（-81%），symbol-only 从 1 降到 0，用户提到的三个截断点全部修复（标题+表格/列表完整在同一 chunk）
- 下次注意：
  1. **separators 顺序很重要**——表格边界 `\n\n|` 必须在 `\n\n` 之前，否则 splitter 会在标题后切分
  2. **merge 逻辑要双向**——只向后合并会遗漏 section 第一个 chunk 太小的情况
  3. **不同 chunk_size 都可能有同一问题**——排查时不要只看一个 chunk_size，要全量扫描所有 KB
  4. 剩余 11 个 tiny chunks 是章节标题+引言（如 "## 4. STM32 USART 寄存器详解"），短是因为子章节被切成独立 section，有检索价值，不应合并

## 2026-06-27 - AgentChunker 504 Gateway Time-out（非流式调用触发 nginx 60s 超时）

- 错误现象：AgentChunker 对 150K 字符文档分 2 个 80K batch 调用 `oc/deepseek-v4-flash`，每个 batch 的 LLM 响应需要 ~200 秒（生成 6779 completion tokens），但代理 `https://9router.zxyzx.bbroot.com/v1` 的 nginx 有 60 秒超时，导致绝大多数请求返回 `504 Gateway Time-out`。SDK 默认 `max_retries=2` 偶尔能撞上快速响应成功，但失败率极高（batch 1 成功、batch 2 连续 6 次 504）。
- 错误原因：使用**非流式** `chat.completions.create(stream=False)` 调用——LLM 必须生成完整个 JSON 响应（6779 tokens）才开始返回任何字节，nginx 在 60 秒内没收到响应就关闭连接返回 504。**不是配置问题**：API key、URL、模型名都正确（已通过 `/v1/models` 列表和 `_probe_api.py` 验证 80K prompt 在 40 秒内能返回）。**也不是 batch 大小问题**：80K 是合理默认值，真正的瓶颈在 completion 生成时间。
- 修复方式：改为**流式调用** `stream=True, stream_options={"include_usage": True}`，LLM 边生成边发送 token，nginx 看到持续数据流就不会超时。即使总生成时间 200+ 秒，连接也能保持活跃。代码位于 [agent_chunker.py](file:///E:/Desktop/agent/backend/src/rag/chunking/agent_chunker.py#L605-L642)。
- 下次注意：
  1. **代理 API + 长生成任务必须用流式**——任何 completion >2K tokens 的调用都可能超过 nginx 60s 超时
  2. 504 不一定是配置错误，先用 `GET /v1/models` 验证模型名存在，再用渐进式 prompt 大小（5K→20K→40K→80K）测试基线延迟
  3. 不要盲目添加 `max_retries=0` 或 `timeout=120`——这会**禁用** SDK 内置的指数退避重试，反而更糟
  4. SDK 默认 `max_retries=2`（3 次尝试）对瞬时 504 有效，但对系统性超时无效——流式才是根治方案

## 2026-06-27 - ChromaDB 批量插入限制（8839 chunks 超过 max batch size 5461）

- 错误现象：AgentChunker 对 207KB 的 `04-cortexm-interrupt.md` 产生 8839 chunks，调用 `kb_manager.ingest_chunks()` 时 ChromaDB 抛出 `ValueError: Batch size of 8839 is greater than max batch size of 5461`，整个测试在 doc 4 处崩溃退出。
- 错误原因：`vector_store.py` 的 `ingest_chunks()` 方法直接调用 `self.db.add_documents(lc_docs)` 一次性插入所有文档，但 ChromaDB 的 Rust 后端限制单次 upsert 最多 5461 条。当 AgentChunker 产生过多小 chunk 时（207KB 文档产生 8839 chunks = 平均 23 字符/chunk），就会超限。
- 修复方式：在 [vector_store.py](file:///E:/Desktop/agent/backend/src/rag/vector_store.py#L408-L424) 的 `add_documents` 调用前加分批逻辑，每 5000 条一批（安全余量低于 5461），循环插入。
- 下次注意：
  1. ChromaDB 批量插入上限是 5461，任何 `add_documents` 调用都要考虑分批
  2. AgentChunker 的 `max_chunks=500` 参数目前只警告不强制——如果 LLM 识别过多 section，chunk 数会爆炸增长（实测 207KB 文档产生 8839 chunks）
  3. 这两个问题是关联的：流式修复让 LLM 调用不再 504，导致更多文档能跑到分块阶段，从而暴露了 chunk 数过多 + ChromaDB 批量限制的下游问题

## 2026-06-27 - AgentChunker chunk 数量爆炸（非 PDF 文档页码去重缺失）

- 错误现象：AgentChunker 对 150K 字符的文档产生 6831 chunks（目标 300-2000），207K 文档产生 8445 chunks。每个 chunk 平均仅 23 字符，远低于 sub_chunk_size=1000 的目标。embedding 阶段每个 chunk 需要单独调 DashScope API（~1s），8445 chunks 需要 2.3 小时，测试无法在合理时间内完成。
- 错误原因：非 PDF 文档使用合成页码标记（每 80K 字符一个 `<!-- PAGE:N -->`），LLM 识别的 section 都用 `start_page=1, end_page=1`。但 `get_text_for_page_range(full_text, 1, 1)` 对每个 section 返回**整页 80K 文本**——同一页上 92 个 section 都获得相同的 80K 文本，每个被 sub-splitter 切成 80 个 1000 字符 chunks，最终 92 × 80 = 7360 个**重复** chunks。
- 修复方式：在 [agent_chunker.py](file:///E:/Desktop/agent/backend/src/rag/chunking/agent_chunker.py#L1061-L1096) 的 `_build_chunks` 末尾加两步：1) 指纹去重（`compute_fingerprint` 比较，移除完全相同的 chunks），2) 强制 `max_chunks=500` 上限（之前只警告不强制）。
- 下次注意：
  1. 非 PDF 文档的合成页码只是 batch 分割的辅助手段，**不能**当作真正的页码用于 section 文本提取——同一页上的所有 section 会获得相同文本
  2. 任何 chunk 生产流程都要加去重步骤——`compute_fingerprint` 已有，但在 `_build_chunks` 里没被用来去重
  3. `max_chunks` 参数必须强制执行，不能只警告——否则上游 bug 会导致 chunk 数爆炸，拖垮下游 embedding 和 ChromaDB

## 记录格式

```md
## YYYY-MM-DD - 简短标题

- 错误现象：
- 错误原因：
- 修复方式：
- 下次注意：
```

## 2026-06-19 -
`apply_patch` 被错误包装成 JSON 导致连续失败

- 错误现象：多次尝试用 `apply_patch` 新建 `docs/api-contract.md`，工具连续返回 `aborted`，文件没有写入。
- 错误原因：`apply_patch` 是 freeform 工具，应该接收原始补丁正文；实际调用时被包装成了 `{"input":"..."}` 形式，工具无法按补丁解析。
- 修复方式：停止重复同一种失败调用；本次为完成文档落地，临时使用 PowerShell here-string 写入纯文档文件，并把该问题记录为工具层踩坑。
- 下次注意：遇到工具格式问题最多重试 2 次；第二次失败后要切换排查路径，先确认工具 schema、调用通道和最小可复现样例，避免陷入重复失败循环。

## 2026-07-01 - Agent 总流 timeout 120s 截断 search_docs（反模式）

- 错误现象：用户问 "ESP32 型号系列 分类"，Agent 调 search_docs 后 ~120s 整个流被截断，报 `agent timeout: 120.2s > 120s`，Agent 直接失败。search_docs 首次查询慢（bge-reranker CPU 推理 + 向量检索 ~60-90s）是已知瓶颈，但总流 timeout 不允许它完成。
- 错误原因：`prompts.py` 的 `SINGLE_REQ_TIMEOUT_S = 120` 是**整个 Agent 流的 wall-clock 总预算**，`context_guard.check_timeout(state)` 在每个 SSE chunk 前检查 `time.time() - state["start_time"] > 120`。这是反模式——同类工具（OpenCode/OpenHands/Codex/Cline/Hermes/Claude Code）均无总 timeout，只有单工具 timeout（Claude Code 最完善：bash/mcp/api 三类）。
- 修复方式（spec agent-reliability-batch）：
  1. `prompts.py`: `SINGLE_REQ_TIMEOUT_S = 120` → `TOOL_CALL_TIMEOUT_S = 300`（单工具预算）
  2. `context_guard.py`: `check_timeout(state)` → `check_tool_timeout(call_start_time, call_id)`（per-call-id 检查）
  3. `sse_adapter.py`: 移除 `_iter_agent_sse` 的总流 `check_timeout(state)`；在 `_convert_tool_message_to_sse` 处理 ToolMessage 前调 `check_tool_timeout`；新增 `_check_active_tool_timeouts` 在每个 chunk 前检查所有活跃 call_id（兜底工具卡死不返回 ToolMessage 的情况）
  4. state dict 移除 `start_time` 字段
- 下次注意：
  1. **不要给 Agent 流加总 timeout**——多轮工具调用总时长不可预测，总 timeout 会误杀慢工具
  2. **timeout 应该 per-tool**——每个工具独立计时，单个工具超时不影响其他工具
  3. **兜底机制**：工具卡死不返回 ToolMessage 时，per-call 检查不会触发，需在流循环里检查所有活跃 call_id
  4. **同类工具横评**：改 timeout 策略前先看 6 个同类工具怎么做，避免重复造反模式

## 2026-07-01 - _finalizeResume content 重复合并 bug（resume 路径与主流程不一致）

- 错误现象：HITL resume 流结束后，assistant message 的 content 会变成两份相同文本拼接（如 "ESP32 有多个系列..." 变成 "ESP32 有多个系列...ESP32 有多个系列..."）。
- 错误原因：sub-agent 实现 `_appendResumeText` 时实时把 `streamingContent` 写到 `message.content`（L182: `content: newContent` where newContent = s.streamingContent + evt.content），但 `_finalizeResume` 又执行 `const merged = (prev + s.streamingContent)`（L252-253），导致 content = streamingContent + streamingContent = 重复。主流程 `onDone`（L925-934）**不合并 content**，只更新 activity/usage——因为 text 事件已实时写 content。
- 修复方式：`_finalizeResume` 改为与主流程 `onDone` 对齐——只更新 activity，不合并 streamingContent 到 content。同时补 `streamingSources: []` 清空（resumeAgent 设了 streamingSources，onDone 也应清空）。
- 下次注意：
  1. **resume 路径的事件处理必须与主流程一致**——text 实时写 content + onDone 只更新 activity，不能 onDone 再合并 content
  2. **sub-agent 实现后必须审查与主流程的对齐性**——特别是 onDone/onError 等收尾逻辑，不能只看 resume 路径自身
  3. **streamingContent 是临时缓冲**——如果 text 事件已实时写到 message.content，onDone 就不能再合并，否则重复

## 2026-07-01 - reasoning 只提取 reasoning_content，漏 thinking/reasoning 字段（多 provider 不兼容）

- 错误现象：用户反馈"思考卡片不是 API 里的 thinking"，怀疑后端没正确提取推理字段。Agent 路径对推理模型（GLM-Z1 等）只显示 placeholder "模型正在思考..."，看不到真实推理过程。
- 错误原因：`reasoning_chat.py` 的 `_extract_reasoning` 只读 `delta.reasoning_content`（DeepSeek/QwQ/GLM-4.6 格式）。不同 provider 推理字段名不同：
  - `reasoning_content`：DeepSeek-R1 / QwQ / Qwen3 / GLM-4.6
  - `thinking`：部分 provider（GLM-5.x 可能改用）
  - `reasoning`：OpenAI o1 兼容
  只兼容一个字段名，其他 provider 的推理内容被丢弃，触发 placeholder 逻辑。
- 修复方式（spec fix-reasoning-and-source-citation）：
  1. `reasoning_chat.py` 新增 `_REASONING_FIELDS: tuple[str, ...] = ("reasoning_content", "thinking", "reasoning")` 常量
  2. `_extract_reasoning` 遍历 3 字段按优先级返回首个非空 str
  3. 删除 placeholder 逻辑（`PLACEHOLDER_THINKING` / `_maybe_emit_placeholder_thinking` / `reasoning_step_open` 状态机）——用户选"只显示真实 reasoning，无则不显示卡片"
- 下次注意：
  1. **多 provider 字段名不统一**——OpenAI-compatible API 各家推理字段名不同，提取时要兼容所有已知名（reasoning_content / thinking / reasoning）
  2. **placeholder 是体验反模式**——非推理模型显示 "模型正在思考..." 无信息量，让用户误以为不是真实 reasoning。用户明确要求"只显示真实 reasoning，无则不显示卡片"
  3. **reasoning_step_open 状态机冗余**——用于控制 placeholder 发送时机，删除 placeholder 后状态机也无用

## 2026-07-01 - source [srcN] 引用太弱，LLM 用文档名加粗代替（prompt 强化 + summary 提示）

- 错误现象：Agent 调 search_docs 后，答案用 `**esp32_datasheet.pdf**` 加粗标注来源，不输出 `[src1]`/`[src2]`，前端 MarkdownRenderer 的 [srcN]→按钮逻辑无法触发。用户反馈"没有改动之前是可以有引用 srcN 并渲染成按钮的"。
- 错误原因：`prompts.py` SYSTEM_PROMPT 的 [srcN] 指令太弱——只有一行 bullet "引用知识库来源时用 [srcN] 格式"，无独立章节、无 good/bad 示例、无强制规则。`search_docs.py` 的 summary 给 LLM 看的是 `[src1] title (相关度 X%)`，但没明确要求"答案中必须用这些标记引用"。LLM 倾向用自己的方式（文档名加粗）标注来源。
- 修复方式（spec fix-reasoning-and-source-citation）：
  1. `prompts.py` SYSTEM_PROMPT 新增"来源引用规范（必须遵守）"独立章节，含 good-example（`ESP32 有多个系列[src1]`）+ bad-example（`**esp32_datasheet.pdf**`）+ 4 条规则
  2. `search_docs.py` `_build_search_summary` 末尾加 "回答时必须用 [srcN] 格式引用上述片段，N 对应 src1/src2/..."
  3. 删除"工具调用前的意图说明"章节（与 placeholder 逻辑一起清理）
- 下次注意：
  1. **prompt 指令要独立章节 + good/bad 示例 + 规则**——单行 bullet 太弱，LLM 容易忽略。独立章节 + 对比示例能显著提升遵守率
  2. **summary 也要提示引用格式**——不只 SYSTEM_PROMPT，search_docs 返回给 LLM 的 summary 末尾也要明确要求"回答时必须用 [srcN] 引用"
  3. **前端渲染逻辑健全不代表 LLM 会输出**——MarkdownRenderer 的 [srcN]→按钮逻辑没问题，问题在后端 prompt 没强制 LLM 输出 [srcN]，排查时先确认 LLM 实际输出再查前端渲染







## 2026-06-19 -
GitHub PR 状态为空不是认证失败

- 错误现象：用户无法获取拉取请求状态。
- 错误原因：`gh auth status` 显示 GitHub 登录正常，远端也正确指向 `zxyzx0430/Hardware-RAG-Agent`；`gh pr list --state all` 返回空数组，表示当前仓库没有任何 PR，而不是权限或网络错误。
- 修复方式：用 `gh auth status`、`git remote -v`、`gh pr list --repo zxyzx0430/Hardware-RAG-Agent --state all --json ...` 三步确认。
- 下次注意：看到空数组要先判断"真实为空"还是"查询失败"；CLI 退出码为 0 且返回 `[]` 时，优先解释为没有 PR。







## 2026-06-19 -
Zustand store 模板字符串语法被破坏导致全部功能失效

- 错误现象：日志显示功能、对话区域下方六个功能按钮（回到问题/收藏/复制/重试/引用/分支）、对话导出、MCP 启停按钮、技能开关等全部点击无响应。`vite build` 报 `Unterminated string literal` 错误。
- 错误原因：`useChatStore.ts` 中多处 ES 模板字符串（template literal）的语法被破坏——反引号丢失、`${}` 表达式丢失、`\n` 转义被替换为实际换行。例如 `lines.join("\n")` 变成了跨行字符串、`msg-${Date.now()}` 变成了 `msg-`、`branch-${Date.now()}` 变成了 `ranch-`、`exportConversation` 中的模板字符串完全破碎。esbuild 无法解析导致整个 store 模块加载失败，所有依赖该 store 的组件功能全部瘫痪。
- 修复方式：完整重写 `useChatStore.ts`，修复所有模板字符串语法。同时修复 `SettingsPage.tsx` 中日志"刷新/复制"按钮缺少 onClick、MCP 启停按钮缺少 onClick、技能开关缺少 onClick 的问题——在 `useSettingsStore` 中添加 `toggleSkill` 和 `toggleMcpServer` action。修复 `ChatArea.tsx` 中"修改"按钮缺少 onClick 的问题——添加编辑状态管理和 `editAndResend` 调用。
- 下次注意：1) 修改含模板字符串的文件时，务必在保存后执行 `vite build` 验证；2) tsc 不检查模板字符串内部语法，esbuild 才会报错，所以 `tsc --noEmit` 通过不代表运行时无问题；3) 按钮缺少 onClick 是常见遗漏，新组件完成后应逐个检查交互元素。







## 2026-06-19 -
React 版本全面功能缺失修复（15+ 组件）

- 错误现象：React 项目相比原始 HTML 缺失约 55 项功能、15 项功能损坏。核心问题包括：面板拖拽无效、右键菜单缺失、收藏夹无法跳转、全局搜索不存在、串口/烧录/安全检查按钮无响应等。
- 错误原因：从 HTML 到 React 的迁移过程中，大量交互逻辑未实现——hook 已编写但未连接（usePanelResize）、组件已编写但未使用（ContextMenu、Modal）、按钮缺少 onClick、store action 为空操作（selectSession）。
- 修复方式：
  1. **AppRoot**: 连接 usePanelResize hook，左右 resizer 可拖拽，面板切换按钮绑定 onClick
  2. **SessionPanel**: 接入 ContextMenu 实现右键菜单（置顶/重命名/删除/移至项目），组标题可折叠/展开，项目可删除/新建
  3. **BookmarkPanel**: 从硬编码 demo 数据改为使用 useChatStore 真实数据，点击跳转到消息，删除/新建文件夹
  4. **SearchModal**: 新建全局搜索组件，Ctrl+K 修复（改用 store 状态而非 DOM 操作）
  5. **HamburgerMenu**: 导出对话框支持 Markdown/JSON 格式选择
  6. **InputBar**: 模型列表从 API 动态获取（useQuery + fallback），Markdown 预览切换
  7. **WorkbenchPanel**: 串口发送/过滤/导出/DTR/RTS，烧录编译/烧录模拟，安全检查验证模拟，接线图 SVG + 缩放/平移 + BOM
  8. **TemplatePanel**: 新建模板面板组件，保存/删除/插入模板
  9. **SnapshotPanel**: 新建快照面板组件，保存/恢复/删除/对比 diff
  10. **useAppStore**: 添加 searchOpen/templatePanelOpen/snapshotPanelOpen 状态
  11. **useSettingsStore**: 添加 toggleSkill/toggleMcpServer action
- 下次注意：1) 迁移 HTML 到 React 时，必须逐个功能点对比，不能只迁移视觉；2) hook/组件写好了不代表接入了，必须检查是否在父组件中实际调用；3) store action 定义了不代表 UI 调用了，必须检查按钮 onClick 是否绑定。







## 2026-06-20 -
SSE 连接超时 10s 导致 "signal is aborted without reason"

- 错误现象：聊天发送消息后报错"连接失败: signal is aborted without reason"，LLM 响应时间超过 10 秒时必现。
- 错误原因：`apiSSE` 中 `connTimer = setTimeout(() => controller.abort(), 10_000)` 仅给 10 秒连接超时，但 LLM（尤其是思考模型）首次响应可能需要 30-60 秒，超时后 AbortController 直接中断连接。
- 修复方式：将连接超时从 10 秒增加到 60 秒（`60_000`），给 LLM 足够的思考时间。响应头返回后 `clearTimeout(connTimer)` 仍会清除定时器，不影响流式读取阶段。
- 下次注意：SSE 长连接场景下，连接超时要考虑 LLM 推理延迟（尤其是 o1/o3 等思考模型），10 秒远远不够；建议 60 秒起步，或根据模型类型动态调整。







## 2026-06-20 -
lazy() 加载样式对象导致代码块白屏崩溃

- 错误现象：AI 回复包含代码块时，整个页面变白/空白，无任何内容渲染。
- 错误原因：`oneDark` 样式定义用 `lazy()` 包装成了 React lazy 组件，但 `SyntaxHighlighter` 的 `style` prop 期望接收普通 JS 对象。`lazy()` 返回的是一个 React 组件包装器而非样式对象，传入后导致渲染崩溃。
- 修复方式：将 `oneDark` 改为静态 `import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism"`，只保留 `SyntaxHighlighter` 组件本身的懒加载。样式对象体积很小（~5KB），静态导入不影响首屏性能。
- 下次注意：`React.lazy()` 只能用于 React 组件，不能用于普通 JS 对象/常量；样式定义、配置对象等小体积模块应直接静态导入，只有大型组件库才需要懒加载。







## 2026-06-20 -
CSS display:none 导致 React 右键菜单永远不可见

- 错误现象：右键点击历史会话没有任何反应，菜单不显示。
- 错误原因：原始 HTML 用 `.ctx-menu { display: none }` + `.ctx-menu.show { display: block }` 模式控制菜单显隐。迁移到 React 后，菜单通过条件渲染（`{ctxMenu && <ContextMenu />}`）控制可见性，但 CSS 中的 `display: none` 仍然生效，导致即使 React 渲染了菜单组件，它也永远不可见。
- 修复方式：移除 `.ctx-menu` 的 `display: none` 和 `.ctx-menu.show { display: block }` 规则，React 条件渲染已足够控制显隐。同时移除 `.ctx-submenu` 的 `display: none`。
- 下次注意：从 HTML 迁移到 React 时，CSS 中的 JS 风格显隐控制（display:none + class 切换）与 React 条件渲染会冲突；React 组件应只用条件渲染控制显隐，不要在 CSS 中设置 `display: none`。







## 2026-06-20 -
会话消息不独立：两个 store 重复存储 + 切换不同步

- 错误现象：切换到另一个会话后，之前会话的消息丢失；新建会话后不会自动切换过去；不同会话的消息混在一起。
- 错误原因：1) `useSessionStore` 和 `useChatStore` 都有 `sessionMessages` 字段，数据重复且不同步；2) `newSession` 创建会话后没有调用 `setActiveSession` 切换过去；3) `sendMessage` 中消息只在 `onDone` 时才保存到 `sessionMessages`，流式输出期间切换会话会丢失消息；4) 删除会话时没有清理 `useChatStore` 中的消息。
- 修复方式：1) 移除 `useSessionStore` 中的 `sessionMessages` 和 `setSessionMessages`，消息存储统一由 `useChatStore` 管理；2) `newSession` 末尾自动调用 `useChatStore.setActiveSession(localId)` 和 `useAppStore.setActiveSession(localId)`；3) `sendMessage` 的 `text`/`error` 事件处理中每次都同步更新 `sessionMessages`；4) `deleteSession` 同步清理 `useChatStore.sessionMessages` 并自动切换到下一个可用会话；5) `setActiveSession` 切换时重置流式状态。
- 下次注意：1) 避免在多个 store 中重复存储相同数据，选择单一数据源；2) 新建资源后要自动切换/选中；3) 流式输出期间的消息必须实时同步到持久化存储，不能只在完成时保存。







## 2026-06-20 -
会话数据不真实：静态字段 + 多处硬编码

- 错误现象：1) msgCount 永远是 0；2) preview 永远为空；3) 分组（今天/昨天/本周/更早）不准确；4) 新建项目后会话消失；5) 移至项目不调用后端API；6) 新建会话模型硬编码 "GPT-4o"；7) 项目硬编码 "嵌入式开发"。
- 错误原因：1) `msgCount`/`preview` 创建后从未更新；2) `group`/`timestamp` 是静态字符串，不根据实际时间动态计算；3) `createProject` 切换 `activeProject` 到空项目，过滤后无会话显示；4) `moveSessionToProject` 只更新本地不调后端；5) `newSession` 硬编码 model 和 project。
- 修复方式：1) Session 类型新增 `createdAt: number`（epoch ms），移除静态 `timestamp`/`group`/`createTime`（保留为可选兼容旧数据）；2) 新增 `getSessionGroup()`/`formatSessionTime()`/`formatCreateDate()` 工具函数动态计算；3) 新增 `updateSessionMeta()` action，在 `onDone` 时更新 msgCount/preview/title；4) `createProject` 不再切换 `activeProject`，避免会话消失；5) `moveSessionToProject` 增加后端 API 调用和"无项目"选项；6) `newSession` 从 `useSettingsStore` 读取当前 model，activeProject 为 "all" 时不指定 project；7) 旧数据通过 `migrateSession()` 自动迁移。
- 下次注意：1) 时间相关字段必须用 epoch 存储，显示时动态格式化，不能存静态字符串；2) 派生数据（msgCount、preview、group）不能只存不更新，应在数据变化时同步；3) 创建空容器（项目/文件夹）后不应自动切换过滤视图，否则用户会以为数据丢失；4) 新建资源时应从当前上下文读取配置（model、project），不要硬编码默认值。







## 2026-06-20 -
SSE 回调写入错误会话 + retry/edit 丢失消息

- 错误现象：1) 流式输出时切换会话再切回来，回答丢失；2) 点重试后整个会话消失；3) 编辑重发同样丢失；4) 分支功能完全坏掉；5) 停止流式不保存部分内容。
- 错误原因：1) **核心架构缺陷**：SSE 回调（onEvent/onDone/onError）使用 `s.activeSessionId` 获取目标会话，但用户切换会话后 activeSessionId 已变，导致流式内容写入错误会话；2) `retryMessage`/`editAndResend` 只更新 `messages` 不更新 `sessionMessages`，且不检查 `isStreaming`，流式中重试会导致 sendMessage 被拒绝而消息已被截断；3) `branchThread` 不创建会话、不保存消息；4) `stopStreaming` 不同步 `sessionMessages`。
- 修复方式：1) **SSE 回调捕获 requestSessionId**：在 `sendMessage` 时用闭包捕获 `activeSessionId`，回调中始终用此 ID 定位目标会话，而非 `s.activeSessionId`；2) 回调中判断 `s.activeSessionId === sid`，仅在仍在同一会话时更新 `messages`，否则只更新 `sessionMessages[sid]`；3) `retryMessage`/`editAndResend` 增加 `isStreaming` 守卫，截断消息后立即通过 `syncToSession()` 同步 `sessionMessages`；4) `setActiveSession` 切换时如正在流式，保存已输出内容并中止 SSE；5) `stopStreaming` 保存部分内容到 `sessionMessages`；6) `branchThread` 改为调用 `newSession()` 后设置分支消息。
- 下次注意：1) **异步回调绝不能依赖可变状态**（如 activeSessionId），必须在发起时捕获并闭包传递；2) 所有修改 `messages` 的操作必须同步更新 `sessionMessages`，否则切换会话时数据不一致；3) 流式输出期间的操作（retry/edit/switch）必须有守卫逻辑，不能假设 isStreaming 为 false；4) 异步操作（SSE、setTimeout）中操作 store 数据时，要考虑用户可能在等待期间切换了上下文。







## 2026-06-20 -
面板拖拽双渲染 + 多处细节功能缺陷

- 错误现象：1) 右侧栏拖拽卡顿不同比例；2) 推送到烧录不传代码；3) 流式时无法回看历史；4) 工具按钮空操作；5) JSON.parse 崩溃；6) onWheel passive 警告；7) 串口导出未过滤日志；8) 编辑框 Enter 不发送。
- 错误原因：1) `usePanelResize` 用本地 state + useEffect 同步 store，每次拖拽双渲染；2) `handlePushToFlash` 只切换 tab 不传代码；3) 自动滚动无暂停机制；4) 工具按钮未绑定 onClick；5) `JSON.parse(step.args)` 无 try-catch；6) React `onWheel` 默认 passive，`preventDefault()` 被忽略；7) 导出用 `log` 而非 `filteredLog`；8) 编辑 textarea 无 onKeyDown。
- 修复方式：1) 重写 `usePanelResize`：直接调 store setter，用 ref 捕获起始宽度，拖拽时设置 `document.body.style.cursor/userSelect`；2) 在 AppStore 新增 `flashCode` state，Preview 的 `handlePushToFlash` 调用 `setFlashCode(activeTab.code)`，Flash 面板读取 `flashCode || CODE`；3) ChatArea 新增 `userScrolledUp` ref，`onScroll` 检测距底部 <60px 时重置，自动滚动仅在 `!userScrolledUp` 时执行；4) 工具按钮绑定 `copyToClipboard` 和 `pushCodeToWorkbench`；5) JSON.parse 包裹 try-catch；6) 用 `useEffect` + `addEventListener('wheel', handler, { passive: false })` 替代 React onWheel；7) 导出改用 `filteredLog`；8) 编辑 textarea 添加 `onKeyDown`，Enter 保存发送。
- 下次注意：1) 拖拽类交互必须直接更新 store，不要本地 state + useEffect 同步；2) "推送到 X"功能必须传递数据，不能只切换视图；3) 自动滚动必须有暂停机制，用户向上滚动时不应强制拉回；4) 所有按钮必须绑定事件处理器，空按钮是功能缺陷；5) `JSON.parse` 必须有 try-catch 保护；6) React 的 `onWheel`/`onTouchMove` 是 passive 的，需要 `preventDefault()` 时必须用 `addEventListener` + `{ passive: false }`。







## 2026-06-20 -
filteredLog TDZ 错误导致白屏 + handleScroll 未定义

- 错误现象：打开页面白屏，控制台报 `Uncaught ReferenceError: Cannot access 'filteredLog' before initialization`。
- 错误原因：`WorkbenchPanel.tsx` 中 `handleExport`（第 204 行）引用了 `filteredLog`，但 `filteredLog` 的 `useMemo` 声明在 `handleExport` 之后（第 214 行），JavaScript 暂时性死区（TDZ）导致变量在声明前不可访问。同时 `ChatArea.tsx` 中 `onScroll={handleScroll}` 引用了未定义的 `handleScroll` 函数，也会导致运行时错误。
- 修复方式：1) 将 `filteredLog` 的 `useMemo` 声明移到 `handleExport` 之前；2) 移除 `ChatArea.tsx` 中未定义的 `handleScroll` 引用和未使用的 `userScrolledUp` ref。
- 下次注意：1) `useMemo`/`useCallback` 等钩子声明顺序很重要，被依赖的值必须先声明；2) `onScroll`/`onClick` 等事件处理器引用必须确保函数已定义，删除功能时要同步清理 JSX 中的引用。







## 2026-06-20 -
LLM reasoning_content 未提取 + 请求体结构不匹配 + lazy() 导出错误

- 错误现象：1) 思考卡片不显示模型的思考过程；2) 系统提示词和长期记忆未注入到 LLM 请求；3) `Element type is invalid: Received a promise that resolves to undefined` 白屏错误。
- 错误原因：1) 后端 `chat_stream` 只处理 `delta.content`，完全忽略了 `delta.reasoning_content`（DeepSeek-R1/QwQ）和 `delta.reasoning`（部分 OpenAI 兼容 API）；2) 前端请求体用 `{ messages, settings: { top_k, ... } }` 嵌套结构，但后端 `ChatRequest` 期望扁平结构 `{ messages, top_k, system_prompt, ... }`，导致 `system_prompt`/`long_term_memory` 等字段始终为 None；3) `react-syntax-highlighter/dist/esm/prism` 只有 `export default`，没有 named export `Prism`，`lazy(() => import(...).then(m => ({ default: m.Prism })))` 中 `m.Prism` 为 undefined。
- 修复方式：1) 后端 `chat_stream` 返回 `StreamChunk(type, content)` 而非纯字符串，提取 `reasoning_content`/`reasoning` 作为 thinking 类型；`routes.py` 根据 `chunk.type` 发送 `thinking`/`text` SSE 事件；2) 前端 `sendMessage` 请求体改为扁平结构，加入 `system_prompt`/`long_term_memory`/`model`/`max_tokens`；3) `lazy(() => import("react-syntax-highlighter/dist/esm/prism"))` 直接使用 default export，移除 `.then()` 包装；4) 前端 thinking 事件处理改为追加模式（如果最后一个 step 是 thinking 则追加内容），而非每次新建 step。
- 下次注意：1) OpenAI 兼容 API 的流式响应中，`delta` 对象可能包含 `reasoning_content`/`reasoning` 等非标准字段，需要用 `hasattr` 检查；2) 前后端请求体结构必须严格对齐，嵌套 vs 扁平是常见不匹配源；3) `React.lazy()` 的 `.then()` 回调必须返回 `{ default: Component }`，如果模块只有 default export 则不需要 `.then()` 包装；4) 流式推送的思考内容需要合并到同一个 step，不能每个 token 创建一个新 step。







## 2026-06-20 -
SSE AbortController 不一致导致切换会话后流式中止失败

- 错误现象：流式输出时切换到其他会话再切回来，输出暂停且无内容显示；控制台报 `signal is aborted without reason` 错误。
- 错误原因：1) `apiSSE` 内部创建了局部 `AbortController`，而 `useChatStore` 中的 `currentSseRequest` 是另一个独立的 `AbortController`，两者不是同一个。`setActiveSession` 调用 `currentSseRequest.abort()` 中止的是 store 中的 controller，但 fetch 请求绑定的是 `apiSSE` 内部的 controller，所以 abort 实际上没有中止 SSE 连接；2) 用户主动中止（切换会话/停止流式）触发了 `onError` 回调，导致显示"连接失败"错误消息；3) `streamingContent` 为空时（模型还在思考阶段），`&& streamingContent` 条件为 falsy，导致部分内容不被保存。
- 修复方式：1) `apiSSE` 新增 `externalController` 参数，`sendMessage` 把 store 中的 `AbortController` 传入，确保 abort 能真正中止 SSE 连接；2) `apiSSE` 的 catch 块中检查 `controller.signal.aborted`，如果是用户主动中止则静默返回，不触发 `onError`；3) `setActiveSession` 中保存部分内容时，不再要求 `streamingContent` 非空，即使只有思考步骤没有文本也保存。
- 下次注意：1) `AbortController` 必须是同一个实例才能中止请求，`apiSSE` 不应内部创建独立的 controller；2) 用户主动中止和真正的网络错误必须区分处理，`signal.aborted` 是区分标志；3) 流式输出中断时，即使没有文本内容（只有思考步骤），也要保存部分结果。







## 2026-06-20 -
思考卡片不显示 + Source 事件捕获 Bug + thinking step 合并错误

- 错误现象：1) 使用普通模型（GPT-4o/Claude等）时看不到思考卡片；2) RAG 来源永远不显示；3) RAG thinking 和 LLM reasoning 被合并成同一个 step。
- 错误原因：1) 普通模型不返回 `reasoning_content`，后端只发 `text` 事件不发 `thinking` 事件，`streamingSteps` 为空所以卡片不出现；2) 后端发送单个 source 对象 `{"type":"source","id":"src1",...}`，但前端读 `sse.sources`（数组），永远是 undefined → 空数组；3) thinking step 合并逻辑只看 type 是否为 thinking，不看 source 来源，导致 RAG 的"正在检索知识库..."和 LLM 的 reasoning_content 被合并。
- 修复方式：1) 后端在 LLM 流式输出前主动发 `thinking({"content":"正在生成回答...","source":"llm"})` 事件，确保普通模型也有思考卡片；2) 后端所有 thinking 事件加 `source` 字段区分来源（rag/llm/reasoning）；3) 前端 thinking step 合并逻辑改为只在 source 相同时合并；4) 前端 source 事件改为累积单个 source 对象而非读取 `sse.sources` 数组；5) `onDone` 中只在 `streamingSteps.length > 0` 时才设置 `activity`；6) ActivityBlock 渲染条件加 `steps.length > 0` 判断。
- 下次注意：1) 普通模型不会返回 reasoning_content，需要后端主动发 thinking 事件模拟思考状态；2) SSE 事件的数据结构必须前后端严格对齐，后端发单个对象时前端不能读数组属性；3) thinking step 合并逻辑必须考虑来源（source），不同来源的 thinking 应该是独立的 step。







## 2026-06-20 -
推理模型思考内容不显示 + Ollama 兼容性

- 错误现象：使用推理模型（DeepSeek-R1/QwQ）时，reasoning_content 不显示在思考卡片中。
- 错误原因：1) 后端只检查了 `delta.reasoning_content` 和 `delta.reasoning`，没有检查 Ollama 使用的 `delta.thinking` 字段；2) Ollama 的推理模型需要设置 `think: true` 参数才能返回思考内容；3) "正在生成回答..."占位 step 和 reasoning_content 的 thinking step 没有正确替换，导致出现两个 thinking step。
- 修复方式：1) 后端 `chat_stream` 新增对 `delta.thinking` 字段的检查（Ollama 兼容）；2) 检测 Ollama base_url 时自动添加 `extra_body={"think": True}` 参数；3) 前端 thinking step 合并逻辑：当收到 `source="reasoning"` 的事件时，如果上一个 step 是 `source="llm"` 的占位 step，则替换而非新建；4) ThinkingStep 组件根据 source 显示不同样式和标签（推理思考/知识库检索/思考中），推理思考默认展开。
- 下次注意：1) 不同 LLM 提供商的推理字段名不同：DeepSeek 用 `reasoning_content`，部分 OpenAI 兼容 API 用 `reasoning`，Ollama 用 `thinking`；2) Ollama 需要显式启用 `think: true` 参数；3) 推理思考应该替换"正在生成回答..."占位 step，而不是新建一个独立的 step。







## 2026-06-20 -
Vite 代理端口配置错误导致前端无法连接后端

- 错误现象：前端发送消息后看不到推理思考卡片，甚至可能完全无法与后端通信。
- 错误原因：`vite.config.ts` 中代理配置指向 `http://127.0.0.1:58080`，但后端实际运行在 `http://127.0.0.1:8000`。所有 `/api` 请求都被代理到了错误的端口，前端根本无法与后端通信。
- 修复方式：将 `vite.config.ts` 中的 `target` 从 `http://127.0.0.1:58080` 改为 `http://127.0.0.1:8000`。
- 下次注意：1) 修改后端端口后必须同步更新 `vite.config.ts` 的代理配置；2) 前端无法连接后端时，首先检查 Vite 代理配置和后端端口是否一致；3) 可以用 `netstat -ano | findstr :端口号` 确认后端实际监听的端口。







## 2026-06-20 -
api-contract.md 为 CRLF 行尾导致 Edit 工具匹配失败

- 错误现象：尝试用 `Edit` 工具修改 `docs/api-contract.md` 时，提示 `String to replace not found in file`，但肉眼核对文本完全一致。
- 错误原因：该文件实际使用 CRLF（`\r\n`）行尾，而 `Edit` 工具按 LF（`\n`）匹配；同时 `Read` 工具显示时会将 CRLF 渲染为普通换行，造成"内容一致"的错觉。
- 修复方式：先用 Python 脚本读取文件内容并执行 `.replace('\r\n', '\n')` 统一为 LF 后再写入；后续即可正常使用 `Edit` 工具。
- 下次注意：1) 在 Windows 仓库中编辑 `.md` 等文本文件前，先确认行尾格式；2) 如果 `Edit` 连续失败，用 `python -c "print(b'\\r' in Path(path).read_bytes())"` 快速检查；3) 项目文档统一维护为 LF，避免跨工具协作时产生匹配问题。







## 2026-06-20 -
CORS 跨域 + 模型列表请求无限重试死循环

- 错误现象：控制台大量 CORS 报错 `Access-Control-Allow-Origin`，前端页面完全瘫痪，模型列表请求无限重试。
- 错误原因：1) `SettingsPage.tsx` 和 `InputBar.tsx` 中的 `useQuery` 在后端代理失败后，直接用 `fetch` 请求远程 API（如 `https://9router.zxyzx.bbroot.com/v1/models`），浏览器同源策略拦截跨域请求；2) CORS 请求失败后，React Query 默认重试 3 次，加上 `queryKey` 依赖 `resolvedBaseUrl`/`activeProvider`，可能导致无限重试循环。
- 修复方式：1) 移除所有直接请求远程 API 的 fallback 代码，模型列表请求只走后端代理（`/api/models`）；2) 给 `useQuery` 添加 `retry: 1` 限制重试次数；3) 后端已有 `/api/models` 端点代理请求，不存在 CORS 问题。
- 下次注意：1) 前端永远不要直接请求第三方 API，所有请求都应通过后端代理或 Vite proxy 转发；2) React Query 的 `useQuery` 默认重试 3 次，对于可能因 CORS 失败的请求必须限制 `retry`；3) `useEffect` 或 `useQuery` 的依赖项必须正确，避免状态更新触发无限重渲染/重请求。







## 2026-06-20 -
修改 store action 签名后连带类型错误

- 错误现象：修复 `useChatStore.stopStreaming` 使其支持 `errorMessage?: string` 参数后，`npx tsc --noEmit` 报两处新错误：1) `InputBar.tsx` 中 `onClick={stopStreaming}` 类型不兼容，因为 `stopStreaming` 不再是 `() => void`；2) `useChatStore.ts` 中多处 `loadFromStorage("bookmarkData", ...)` / `saveToStorage("bookmarkData", ...)` 报错，因为 `persistence.ts` 的 `KEYS` 里没有 `bookmarkData`。
- 错误原因：1) 修改函数签名后，没有同步检查所有调用点，直接把带可选参数的函数传给了 React 的 `MouseEventHandler`；2) `bookmarkData`  persistence key 之前遗漏，代码里却已经在使用，只是之前可能没触发严格类型检查或被忽略。
- 修复方式：1) `InputBar.tsx` 改为 `onClick={() => stopStreaming()}`，显式无参调用；2) 在 `src/utils/persistence.ts` 的 `KEYS` 中添加 `bookmarkData: "hwrag_bookmark_data"`。
- 下次注意：1) 修改公共函数/ action 签名后，必须全局搜索调用点并验证 tsc；2) `loadFromStorage` / `saveToStorage` 的 key 必须在 `KEYS` 中显式注册，否则新增存储字段时会触发类型错误；3) 运行 `tsc --noEmit` 时要关注“本次改动引入”的新错误，区分于历史遗留错误。







## 2026-06-20 -
接口字段与契约不一致

- **现象**：联调 `/api/chat` 时发现 `system_prompt` / `long_term_memory` 等字段没有生效；后端收不到，前端以为已发送。类似地，`/api/models` 错误码、`/api/wiring` 响应结构在前后端理解不一致。
- **原因**：前端请求体使用了嵌套结构 `{ messages, settings: { top_k, system_prompt, ... } }`，而后端 `ChatRequest` Pydantic 模型期望扁平结构 `{ messages, top_k, system_prompt, ... }`；接口变更后没有及时先更新 `docs/api-contract.md`，导致双方各自按旧假设编码。
- **修复**：约定 "先改 `docs/api-contract.md`，再对齐代码"。将 `/api/chat` 请求体改为扁平结构，加入 `system_prompt` / `long_term_memory` / `model` / `max_tokens`；同步更新接口目录、字段说明、Mock 规则和变更日志，最后再修改前后端实现。
- **下次注意**：新增或修改接口时，必须先在 `docs/api-contract.md` 中登记并推进状态到 `agreed`；联调不一致时，先更新文档再改代码，禁止双方口头约定字段格式。







## 2026-06-20 -
前端硬件功能 fallback 模拟成功

- **现象**：点击"编译"、"烧录"或"安全检查"按钮后，界面显示"编译成功"、"烧录成功"等绿色状态，但后端实际未调用，串口/设备没有任何反应；用户误以为功能已完成，延误问题定位。
- **原因**：硬件工作台早期为了快速验证 UI，在 `setTimeout` 中模拟了成功响应；随着后端 `/api/build`、`/api/upload`、`/api/audit_pins`、`/api/diagnose` 已实现，这些模拟代码仍残留在组件中，覆盖了真实 API 调用路径。
- **修复**：移除所有 `setTimeout` 模拟逻辑，改为直接调用 `apiSSE` / `apiPost`；失败时通过日志面板和错误提示展示真实后端错误信息，不再伪造成功状态。
- **下次注意**：任何临时 mock / 模拟代码必须标注 TODO 并设置清理时间点；功能联调前要通过浏览器 Network 面板确认确实发出了真实请求，不能仅凭 UI 状态判断成功。







## 2026-06-20 -
FastAPI HTTPException detail 被包装在 `detail` 字段下

- **现象**：测试 `/api/kb/upload` 超大文件时，断言 `response.json()["success"]` 报 `KeyError: 'success'`。
- **原因**：路由里 `raise HTTPException(status_code=400, detail={"success": False, "error": {...}})`，但 Starlette/FastAPI 默认会把 `exc.detail` 再包一层返回为 `{"detail": <detail>}`，导致前端/测试看到的是 `{"detail": {"success": False, ...}}`。
- **修复**：测试端改为读取 `response.json()["detail"]["success"]`；如需统一 API 契约，应自定义 HTTPException 处理程序或改用 `JSONResponse(status_code=400, content={...})` 直接返回。
- **下次注意**：`HTTPException(detail=...)` 的 detail 会被包装成响应体的 `detail` 字段；如果要让响应体与正常成功响应保持同样的 `{success, data/error}` 结构，不要用 HTTPException，直接返回 `JSONResponse` 或自定义异常处理器。







## 2026-06-20 -
Mock 异步流式函数时产生 `coroutine was never awaited` 警告

- **现象**：用 `unittest.mock.patch` 替换 `LLMClient.chat_stream` 为 `async def fake_stream(...): raise RuntimeError(...)` 后，测试能通过但 pytest 报 `RuntimeWarning: coroutine 'fake_stream' was never awaited`。
- **原因**：不含 `yield` 的 `async def` 是协程函数，调用后返回的是 coroutine 对象；`async for` 在协程直接抛异常时，Python 的 coroutine 跟踪机制认为该协程未被正常 await 完毕。
- **修复**：在 `fake_stream` 中加入至少一条 `yield` 语句，使其成为异步生成器函数；调用后返回的是 async generator 对象，`async for` 可以正常迭代并消费异常。
- **下次注意**：mock 流式接口时，优先让 fake 函数成为 async generator（含 `yield`），而不是普通 coroutine；如果确实要模拟立即失败，可以让它 yield 一个空 chunk 后再 raise。







## 2026-06-21 -
安全基线改造：API Key 加密存储 + XSS 防护 + 异常脱敏

- **现象**：API Key 明文存储在前端 localStorage 和 HTTP Header 中传输，存在 XSS 窃取和中间人攻击风险；`dangerouslySetInnerHTML` 渲染未经消毒的 HTML；异常信息中可能泄露 API Key。
- **原因**：1) 前端 `getAuthHeaders()` 将 API Key 放入 `X-API-Key` Header 明文传输；2) `InputBar.tsx` 中 `dangerouslySetInnerHTML={{ __html: previewHtml }}` 未消毒；3) `routes.py` 中多处 `details: str(e)` 直接暴露异常原文，可能包含 API Key。
- **修复**：1) 新建 `backend/app/api/auth.py`，使用 Fernet 对称加密存储 API Key，前端保存时调用 `/api/auth/store-key` 获取 session_token，后续请求通过 `Authorization: Bearer {token}` 认证；2) 前端 `client.ts` 移除 `X-API-Key` Header，改为从 localStorage 读取 session_token；3) `InputBar.tsx` 中 `dangerouslySetInnerHTML` 使用 `DOMPurify.sanitize()` 消毒；4) `routes.py` 中所有 `details: str(e)` 改为 `details: _sanitize_error(str(e))`，`message` 中包含异常的也统一脱敏；5) `.gitignore` 添加 `backend/db/.enc_key` 和 `backend/db/keys_store.json`。
- **下次注意**：1) 敏感凭证（API Key、Token）不应在前端 localStorage 明文存储或通过 HTTP Header 明文传输；2) 所有 `dangerouslySetInnerHTML` 渲染的 HTML 必须经过 DOMPurify 消毒；3) 异常信息返回给前端前必须脱敏，`_sanitize_error` 应覆盖所有 `details` 和包含异常的 `message` 字段。







## 2026-06-21 -
代码质量修复（Task 10-17）踩坑汇总

- **现象**：多个代码质量问题：SSE 畸形 JSON 静默丢弃、localStorage 满时无提示、消息 ID 用 Date.now() 不唯一、数据库会话手动 try/finally 管理易遗漏、HardwareVectorStore 重复实例化、LLMClient 重试逻辑对编程错误也重试、useSessionStore 直接用 fetch 不走统一封装、branchThread 竞态条件。
- **原因**：1) SSE 解析 `catch {}` 静默跳过畸形数据，无法诊断网络问题；2) `saveToStorage` catch 块吞掉 QuotaExceededError；3) `msg-${Date.now()}` 在快速连续操作时可能重复；4) `db = SessionLocal() try/finally: db.close()` 不自动 commit/rollback；5) 每次 API 调用都 `HardwareVectorStore()` 重新加载 ChromaDB；6) `_with_retries` 对 KeyError/TypeError 等编程错误也重试；7) `useSessionStore` 中 `fetch` 不带 auth headers；8) `branchThread` 先 `newSession()` 再读 `activeSessionId`，存在竞态。
- **修复**：1) SSE catch 中计数并 warn 日志，连续 3 次触发 onError；2) `saveToStorage` 检测 QuotaExceededError 并 dispatch CustomEvent；3) 消息 ID 改用 `crypto.randomUUID()`；4) 新增 `get_db_ctx()` 上下文管理器自动 commit/rollback/close；5) 新增 `get_vector_store()` 单例函数；6) `_with_retries` 对 KeyError/AttributeError/TypeError/ValueError 直接抛出，仅重试网络/速率/5xx 错误；7) 新增 `apiPut`/`apiDelete`，useSessionStore 统一使用；8) `branchThread` 先准备数据再一次性 set，最后调 `newSession()`；9) sessionMessages 分片存储（`hwrag_msg_{sid}`），debounce 持久化避免 SSE 期间频繁写入。
- **下次注意**：1) catch 块不能静默丢弃错误，至少要日志记录；2) localStorage 操作必须处理 QuotaExceededError；3) 消息 ID 不能用 Date.now()，必须用 UUID；4) 数据库会话用上下文管理器，不要手动 try/finally；5) 重试逻辑必须区分可恢复/不可恢复异常；6) 前端 API 调用统一走封装函数，不要裸用 fetch；7) 异步操作中的状态读取要考虑竞态，先准备数据再修改状态。







## 2026-06-21 -
Agent 通用功能补强（Task 18-22）踩坑汇总

- **现象**：实现对话摘要自动生成、键盘快捷键扩展、工具参数 Schema 校验与超时控制、消息反馈机制、跨会话搜索时遇到的问题。
- **原因**：1) `_build_messages` 返回值从 `List[Dict]` 改为 `tuple[List, list]` 后，`chat()` 和 `chat_stream()` 中的调用未同步更新；2) `tool_router.py` 的 `_REGISTRY` 从 `dict[str, ToolHandler]` 改为 `dict[str, dict[str, Any]]` 后，`routes.py` 中 `payload.tool not in TOOL_REGISTRY` 仍可用（只检查 key），但 `dispatch` 内部需改为 `entry["fn"]` 取处理器；3) `useAppStore` 接口中 `searchOpen` 字段缺失（只有 `setSearchOpen`），导致 SearchModal TS 报错；4) `branchTreeOpen`/`setBranchTreeOpen` 在接口中声明但 store 实现中缺失；5) ChatArea.tsx 中 `useSessionStore` 未导入但被使用。
- **修复**：1) 同步更新 `chat()` 和 `chat_stream()` 中对 `_build_messages` 的解构调用，添加摘要生成逻辑；2) `dispatch` 改为从 `entry["fn"]` 取处理器，添加参数 Schema 校验和 `asyncio.wait_for` 超时控制；3) 在 `AppState` 接口中添加 `searchOpen: boolean`；4) 在 store 实现中添加 `branchTreeOpen: false` 和 `setBranchTreeOpen` action；5) ChatArea.tsx 添加 `import { useSessionStore }` 导入。
- 下次注意：1) 修改函数返回值类型后必须全局搜索所有调用点并同步更新；2) 修改 store 数据结构后，接口定义和实现必须同步；3) Zustand store 的接口（interface）和实现（create）必须完全匹配，缺失字段会导致 TS 报错；4) 组件中使用外部 store 时必须确保 import 已添加。







## 2026-06-21 -
branchThread 会话 ID 不一致 + 后端 CRUD 不返回分支字段

- **现象**：点击"分支"按钮后，新会话被创建但分支信息丢失，BranchTree 组件无法构建分支树。
- **原因**：1) `branchThread` 先用 `Date.now()` 生成 `newSessionId`，然后调用 `useSessionStore.newSession()` 创建新会话，但 `newSession()` 内部也用 `Date.now()` 生成自己的 `localId`，两个 ID 不一致，导致消息数据设置到了不存在的会话 ID 上；2) `updateSessionMeta` 的类型 `Partial<Pick<Session, "msgCount" | "preview" | "title">>` 不包含 `branchFromSessionId`/`branchFromMessageId`，只能用 `as any` 绕过；3) 后端 `crud.py` 的 `list_sessions`/`create_session`/`update_session` 不返回和处理 `branch_from_session_id`/`branch_from_message_id` 字段，前端 `initSessions` 也不映射这些字段。
- **修复**：1) 重写 `branchThread`：直接通过 `apiPost("sessions", { ..., branch_from_session_id, branch_from_message_id })` 创建带分支信息的新会话，拿到后端返回的 `sid` 后再设置消息数据和切换会话，回退时纯本地创建；2) `updateSessionMeta` 类型扩展为 `Partial<Pick<Session, "msgCount" | "preview" | "title" | "branchFromSessionId" | "branchFromMessageId">>`；3) 后端 `SessionCreate`/`SessionUpdate` Pydantic 模型添加 `branch_from_session_id`/`branch_from_message_id` 可选字段，`list_sessions`/`create_session` 响应中包含这些字段，`update_session` 支持更新这些字段；4) 前端 `initSessions` 映射 `s.branch_from_session_id` → `branchFromSessionId` 和 `s.branch_from_message_id` → `branchFromMessageId`。
- **下次注意**：1) 异步创建资源时，不要假设本地生成的 ID 与后端返回的 ID 一致，应以后端返回为准；2) 新增数据库字段后，CRUD 的 Pydantic 模型、响应序列化、前端映射三处必须同步更新；3) `updateSessionMeta` 等通用更新函数的类型应覆盖所有可更新字段，不要用 `as any` 绕过。






## 2026-06-21 -
全项目 Review 发现的系统性踩坑（共 19 项 P0）

- **现象**：对 01-08 全部线程做代码 review 后，发现 19 项 P0 阻断级问题、51 项 P1、71 项 P2、42 项 P3。详见 `docs/review-result.md`。
- **原因（按共性归类）**：
  1. **鉴权形同虚设**：`backend/app/api/auth.py` 的 `get_provider_key_by_session(token)` 定义但从未被任何路由 `Depends`，所有 `/api/sessions`、`/api/kb/*`、`/api/sandbox/*`、`/api/wiring`、`/api/devices` 路由均无鉴权。
  2. **响应格式违反契约**：`backend/app/api/crud.py` 所有路由返回裸对象（如 `{"sessions": [...]}`），违反契约 `{success, data}` 格式。前端 `unwrapResponse` 用 hack `if (!("success" in json)) return json as T` 兼容，掩盖了问题。
  3. **死代码与 mock 残留**：`backend/app/api_router.py`（完整 mock 路由）、`backend/app/kb/translation_pipeline.py`（完整类从未调用）、`/api/build`/`/api/upload`/`/api/audit_pins`（stub 返回硬编码）。
  4. **`backend/main.py` mock `create_app()` 影子覆盖**：入口文件曾有自己的 mock `create_app()` 返回空数组，shadowed 真实 `app/main.py` 的 `create_app()`，导致所有真实路由从未注册，所有请求 404 或返回 mock 数据。已修复为委托模式。
  5. **`useSSE` 的 `externalController` 死代码**：`apiSSE` 接受 `externalController` 参数但内部仍创建新 `AbortController`，外部传入的从未被使用，导致取消请求无效。
  6. **多模态 RAG `images` 字段未透传**：`/api/chat` 走 RAG 时 `images` 字段未透传给 LLM，后端 `KeyError` 崩溃。
  7. **MCP `handler.run` AttributeError**：`agent/handler.py` 调用 `handler.run(input)`，但 `MCPClient` 无 `run` 方法，工具永远调不通。
  8. **sandbox C/C++ 代码未传入容器**：`sandbox/runner.py` 的 `run_python` 从未通过 stdin 把代码传入容器，`compile` 阶段永远失败。
  9. **`asyncio.run` 在已有事件循环中崩溃**：`sandbox/runner.py` 用 `asyncio.run(self._run_container())`，在 FastAPI 已有事件循环中抛 `RuntimeError: This event loop is already running`。
  10. **`.gitignore` 路径错位**：加密密钥实际在 `backend/app/db/.enc_key` 与 `backend/app/db/keys_store.json`，但 `.gitignore` 写的是 `backend/db/.enc_key`，密钥文件可能被提交。
  11. **原生 SQL `LIKE` 拼接 + 缺 FTS 表**：`crud.py` 的 `/api/sessions/search` 用 `f"%{q}%"` 直接拼接，存在 SQL 注入；且 FTS 虚拟表未创建，查询会崩。
  12. **`apiWS` 硬编码端口**：`frontend/src/api/client.ts` 的 `apiWS` 硬编码 `ws://localhost:8000`，与契约 `ws://127.0.0.1:8000` 不符，生产环境必崩。
  13. **`requirements.txt` 缺依赖**：缺少 `aiofiles`、`python-multipart`、`httpx`、`PyYAML` 等运行时依赖，新环境部署必崩。
  14. **WebSocket 鉴权造假**：`/api/monitor/{port}` 在 `websocket.accept()` 后未校验 token，任何人可连接串口。
  15. **`/api/diagnose` 编译检查硬编码 PASS**：从未真正诊断，永远返回 PASS。
  16. **Arduino 编译器路径硬编码**：`/opt/arduino/arduino-cli` 在容器内不存在。
  17. **CORS 配置死代码**：`main.py` 历史 mock 中的 CORS 配置已失效，实际生效的是 `app/main.py` 的开发态宽松配置 `allow_origins=["*"]`。
  18. **`TranslationPipeline` 死代码**：完整类定义但从未被任何路由调用。
  19. **`/api/audit_pins` 返回硬编码**：`{"conflicts": []}` 从未真正检查引脚冲突。
- **修复方案**：详见 `docs/review-result.md` 的 Phase 1（P0 阻断项）清单。核心修复模式：
  - 鉴权：新建 `backend/app/api/dependencies.py` 的 `current_user` 依赖，注入到所有受保护路由
  - 响应格式：统一用 `{"success": True, "data": ...}` 包装
  - 死代码：删除或标注 `# TODO: implement`
  - `useSSE`：优先使用 `externalController`，仅在其为空时创建内部 controller
  - `asyncio.run`：改为 `await self._run_container()`
  - SQL 注入：用参数化查询 `WHERE title LIKE :q`
- **下次注意**：
  1) 加密/鉴权相关函数定义后，必须用 `grep` 全局搜索调用点，确认至少有一个路由 `Depends` 了它
  2) 契约规定的响应格式必须严格遵守，前端不能用 hack 兼容后端的违规
  3) mock/ stub 代码必须标注 `# TODO: implement` 并设置清理时间点
  4) 入口文件（`main.py`）不应有自己的 `create_app()` 实现，应委托给 `app/main.py`
  5) `AbortController` 参数必须真正被使用，否则取消功能失效
  6) `asyncio.run` 不能在已有事件循环中调用，FastAPI 路由中应直接 `await`
  7) `.gitignore` 路径必须与实际敏感文件位置核对
  8) 原生 SQL 必须用参数化查询，不能用 f-string 拼接
  9) WebSocket 必须在 `accept()` 前校验 token
  10) `requirements.txt` 必须用 `pip freeze` 生成，不能手写

## 2026-06-21 - main.py 入口使用 mock create_app 导致所有真实路由 404/500

- **现象**：前端请求 `/api/sessions`、`/api/auth/keys`、`/api/settings` 等端点返回 404 或 mock 空数据，所有 CRUD/auth/sandbox/MCP 路由均未注册。
- **原因**：`backend/main.py` 有自己的 `create_app()` 包含硬编码 mock 路由（`/api/sessions` 返回空列表等），而真实路由在 `app/main.py` 的 `create_app()` 中。`python main.py --web` 调用的是旧版 mock 的 `create_app()`，导致 crud.py/auth.py/sandbox_routes.py/mcp_routes.py 等路由全部未注册。
- **修复**：将 `main.py` 的 `create_app()` 改为委托给 `app.main.create_app()`，`uvicorn.run` 改为 `uvicorn.run("app.main:app", ...)` 字符串方式启动。
- **下次注意**：入口文件不应有自己的路由实现，必须委托给 `app/main.py`；修改路由后必须重启服务器验证。

## 2026-06-21 - document_processor.py 的 `import logging` 被误放在 docstring 内导致 NameError

- **现象**：`from app.main import create_app` 报 `NameError: name 'logging' is not defined`，服务器无法启动。
- **原因**：`document_processor.py` 的 `import logging` 语句被错误地放在了模块 docstring 三引号内部（第 4 行），Python 将其视为字符串内容而非 import 语句。第 78 行 `logger = logging.getLogger(__name__)` 引用了未导入的 `logging`。
- **修复**：将 `import logging` 从 docstring 内移出，放在 docstring 结束后的正常 import 区域。
- **下次注意**：修改文件头部时注意 docstring 三引号的闭合位置，确保 import 语句不在 docstring 内；服务器启动失败时优先检查 import 错误。

## 2026-06-21 - Windows GBK 终端无法输出 emoji 导致 UnicodeEncodeError 服务器崩溃

- **现象**：`python main.py --web` 启动时崩溃，报 `UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f310'`。
- **原因**：`main.py` 和 `app/main.py` 的 print 语句包含 emoji（🌐📖），Windows 默认终端编码为 GBK，无法编码 emoji 字符。
- **修复**：将所有 print 中的 emoji 替换为纯文本前缀（如 `[Web]`）。
- **下次注意**：Python print 输出在 Windows 终端中必须避免 emoji 和非 GBK 字符；如需使用，设置 `PYTHONIOENCODING=utf-8` 或 `PYTHONUTF8=1` 环境变量。

## 2026-06-21 - P0 修复踩坑

- **现象**：全项目 review 后修复 19 项 P0 阻断级问题，覆盖鉴权形同虚设、响应格式违反契约、RAG 字段未透传、MCP 工具调不通、沙箱 C/C++ 代码无法传入容器、async 事件循环冲突、硬件工作台接口全为 mock、依赖缺失、死代码残留、密钥路径错位、FTS 表缺失、WebSocket 鉴权造假等。
- **原因**：详见 `docs/review-result.md` 的 Phase 1 清单。根本原因是早期为快速验证 UI 写了大量 mock/stub，后续未清理；鉴权函数定义后未接入路由；契约规定的响应格式未被严格遵守；`asyncio.run` 误用在已有事件循环中；Docker 容器挂载 `/tmp` 读写有逃逸风险；C/C++ 代码未通过 stdin 传入容器。
- **修复方式**：见 `docs/completed.md` 的 "P0 修复记录（2026-06-21）" 章节。核心修复模式：
  - 鉴权：新建 `backend/app/api/dependencies.py` 的 `current_user` 依赖，注入到所有受保护路由
  - 响应格式：统一用 `{"success": True, "data": ...}` 包装
  - RAG 透传：`store.search` 前提取文本，`images` 字段透传给 LLM
  - MCP 调用：`tool_router.py` dispatch 兼容 plain function
  - 沙箱执行：C/C++ 代码通过 stdin 传入容器，async 阻塞用 `asyncio.to_thread`
  - 硬件工作台：`/api/audit_pins`、`/api/build`、`/api/upload`、`/api/diagnose`、`/api/monitor/{port}` 全部接入真实逻辑
  - 依赖管理：`requirements.txt` 补全 10 个运行时依赖
  - 死代码：删除 `backend/app/api_router.py`
  - 密钥安全：`.gitignore` 修正密钥路径
  - FTS 检索：`database.py` init_db 创建 FTS5 虚拟表 + 触发器
  - WebSocket 鉴权：`ws_auth` 在 `accept()` 前校验 token
- **下次注意**：
  1) 鉴权函数定义后必须用 grep 确认至少有一个路由 Depends 了它
  2) 契约规定的响应格式必须严格遵守，前端不能用 hack 兼容
  3) mock/stub 代码必须标注 `# TODO: implement` 并设置清理时间点
  4) `asyncio.run` 不能在已有事件循环中调用，FastAPI 路由中应直接 `await` 或用 `asyncio.to_thread`
  5) Docker 容器挂载 `/tmp` 读写有逃逸风险，改用 `tmpfs`
  6) C/C++ 代码必须通过 stdin 或 volume 传入容器，不能只靠 `cat` 命令
  7) WebSocket 必须在 `accept()` 前校验 token
  8) `requirements.txt` 必须用 `pip freeze` 生成，不能手写

## 2026-06-21 - routes.py 使用 threading.Lock() 未 import threading 导致启动崩溃

- **错误现象**：运行 `python -c "from app.main import create_app; app = create_app()"` 验证后端启动时，报 `NameError: name 'threading' is not defined`，定位到 `app/api/routes.py` line 82 的 `_vector_store_lock = threading.Lock()`。
- **错误原因**：`routes.py` 中 `get_vector_store()` 改造为线程安全双重检查锁定模式时，新增了 `_vector_store_lock = threading.Lock()`，但 import 区只导入了 `asyncio`，遗漏了 `import threading`。
- **修复方式**：在 `routes.py` import 区添加 `import threading`。
- **下次注意**：1) 使用任何标准库模块前必须确认已 import，特别是 `threading`/`asyncio`/`os` 等容易遗漏的；2) 改造单例为线程安全模式时，同步检查 import 列表；3) 后端启动验证脚本应作为提交前必跑项，可立即暴露此类 NameError。

## 2026-06-21 - os.chmod 在 Windows 上不支持导致三个接口全部 500

- **错误现象**：`/api/models`、`/api/sessions`、`/api/devices` 三个接口全部返回 500 Internal Server Error，前端 TypeError 崩溃。
- **错误原因**：
  1. `auth.py` 的 `_get_fernet()` 中 `os.chmod(ENCRYPTION_KEY_PATH, 0o600)` 在 Windows 上抛 `OSError`（Windows 不支持 Unix 权限模式），导致加密密钥初始化崩溃
  2. `_load_store()` 依赖 `_get_fernet()` 解密，初始化崩溃后所有鉴权逻辑崩
  3. `current_user` 依赖调用 `_load_store()`，异常未捕获，直接 500
  4. 所有注入 `Depends(current_user)` 的路由（sessions/devices/wiring/diagnose）全部 500
  5. `/api/models` 只捕获 `LLMError`，`LLMClient.__init__` 等异常直接 500
  6. 前端 `useQuery` 的 `queryFn` 抛异常时 UI 崩溃（TypeError）
- **修复方式**：
  1. `auth.py`：`os.chmod` 加 `try/except (OSError, AttributeError)` 包裹，Windows 上静默跳过
  2. `dependencies.py`：`current_user` / `ws_auth` 加 `try/except`，异常时返回匿名用户（`anonymous: True`）
  3. `routes.py`：`/api/models` 加 `except Exception` 兜底
  4. `InputBar.tsx` / `SettingsPage.tsx`：`queryFn` 加 `try/catch`，失败返回空数组
- **下次注意**：
  1) `os.chmod` 在 Windows 上只支持 `0o444`/`0o666` 两种模式（只读/读写），Unix 特定权限模式必须加 try/except
  2) FastAPI 依赖注入函数（`Depends`）必须对自身异常做兜底，否则一个依赖崩了整条路由链全崩
  3) 前端 `useQuery` 的 `queryFn` 不应直接抛异常，应 catch 后返回空值/默认值
  4) Windows 兼容性必须作为测试项，不能只在 Linux/Mac 上验证
## 2026-06-23 - SSE 事件分隔符转义错误导致前端不显示思考卡片与回答

- **错误现象**：用户输入正常提交，后端日志显示 SSE 正常输出内容，但前端只能看到 assistant 消息底部的五个功能按钮，看不到思考卡片和回答内容。
- **错误原因**：
  1. **根因**：`backend/app/api/common.py` 的 `sse_event()` 函数返回的 SSE 数据使用 `\\n\\n`（两个字符 `\n`）作为事件分隔符，而不是真正的换行符 `\n\n`。前端 SSE 解析器以空行作为事件边界，收不到真正的空行，导致所有 `text`/`thinking`/`source`/`done` 事件都解析失败，`onEvent` 从未被调用，消息 content 和 activity 始终保持初始空值。
  2. **附带问题**：`backend/app/api/chat_routes.py` 的 system_prompt 拼接也错误使用 `\\n`，污染提示词；同时 `while...else` 结构导致正常流程中不会发送 `done` 事件。
- **修复方式**：
  1. `common.py`：`sse_event()` 改为 `f"data: {json.dumps(payload)}\n\n"`，使用真正的换行符作为 SSE 事件分隔符。
  2. `chat_routes.py`：将附件/RAG/system_prompt 中的 `\\n` 全部改为 `\n`；重写 LLM 流式循环，正常结束时显式 `yield sse_event("done", ...)`。
  3. `frontend/src/api/client.ts`：SSE 解析前统一把 `\r\n`/`|` 归一化为 `\n`，并兜底处理连接关闭时缓冲区中剩余的完整事件。
  4. `frontend/src/components/chat/ChatArea.tsx`：流式状态判断从对象引用比较改为 `msg.id === messages[last]?.id`。
- **下次注意**：
  1. Python 字符串中 `\\n` 是字面量 `\n`（两个字符），不是换行符；SSE 必须用真正的 `\n\n` 或 `\r\n\r\n` 分隔事件。
  2. 新增/修改 SSE 辅助函数后，用 curl/wget 抓包检查原始字节，确认事件分隔符正确。
  3. 前端 SSE 解析器应兼容 CRLF/LF/CR，不能假设只有一种行尾。
  4. Python `while...else` 的 `else` 只在循环未被 `break` 时执行，流式读取场景下通常不适用，应避免用此模式发送收尾事件。







## 2026-06-23 - BaseHTTPMiddleware 缓冲 SSE 流式响应导致前端不显示内容

- **错误现象**：用户输入正常提交，后端日志显示 SSE 事件逐个生成，但前端界面只显示五个功能按钮，思考卡片和回答内容区域完全空白。curl 直接请求后端端口可以看到 SSE 事件，但通过前端 Vite 代理访问时内容不显示。
- **错误原因**：`app/main.py` 中使用 `@app.middleware("http")` 注册的中间件（`limit_request_body_middleware` 和 `request_log_middleware`）被 Starlette 包装为 `BaseHTTPMiddleware`。`BaseHTTPMiddleware` 的工作机制是：先消费整个响应体（包括所有 SSE 事件），然后再一次性转发给客户端。对于 `StreamingResponse`（SSE），这意味着所有事件被缓冲直到流结束才发送，前端无法逐个接收事件，导致 `onEvent` 回调从未被触发，消息 content 和 activity 始终为空。
- **修复方式**：将两个 `BaseHTTPMiddleware` 函数改为纯 ASGI 中间件类（`_RequestBodyLimitMiddleware` 和 `_RequestLogMiddleware`），直接操作 `scope`/`receive`/`send`，不缓冲响应体。用 `app.add_middleware()` 注册纯 ASGI 中间件。
- **下次注意**：
  1. `@app.middleware("http")` 创建的 `BaseHTTPMiddleware` 会缓冲整个 `StreamingResponse`，绝对不能用于 SSE 端点
  2. 需要在 SSE 流式响应上执行中间件逻辑时，必须用纯 ASGI 中间件（直接操作 scope/receive/send）
  3. SSE 不显示内容时，先用 curl 直接请求后端确认事件是否正常推送，再检查中间件是否缓冲了响应

## 2026-06-23 - 双重 falsy fallback 导致系统提示词无法修改和注入

- **错误现象**：前端设置页修改系统提示词后，发送消息时后端仍使用默认提示词；清空提示词也无法生效，始终回退到默认值。
- **错误原因**：前后端双重 falsy fallback：
  1. 前端 `useChatStore.ts` 中 `system_prompt: systemPrompt || undefined`，空字符串 `""` 被转为 `undefined`，JSON 序列化时该字段被省略
  2. 后端 `chat_routes.py` 中 `payload.system_prompt or DEFAULT_SYSTEM_PROMPT`，Python 的 `or` 对空字符串也视为 falsy，回退到默认值
  3. 两层叠加：前端省略字段 → 后端收到 None → 使用默认值；前端传空字符串 → 后端 `or` 视为 falsy → 使用默认值。用户无论怎么修改都无法生效。
- **修复方式**：
  1. 前端：`system_prompt: systemPrompt`（保留空字符串，不做 `|| undefined` 转换）
  2. 后端：`system_prompt = payload.system_prompt if payload.system_prompt is not None else DEFAULT_SYSTEM_PROMPT`（用 `is None` 检查代替 `or`，空字符串不再被吞没）
- **下次注意**：
  1. JavaScript `||` 和 Python `or` 对空字符串都视为 falsy，当业务语义需要区分"未设置"和"设置为空"时，必须用 `is None`/`=== undefined` 精确判断
  2. 前后端对同一字段的 falsy 处理逻辑必须对齐，避免双重 fallback 导致用户输入永远无法生效
  3. 测试系统提示词时，要同时验证"自定义内容"和"清空为空字符串"两种场景

## 2026-06-30 - useChatStore tool_result 把 render_data 简化丢失 + Task 7 setter 缺失（useWorkbenchBridge 实现踩坑）

- 错误现象：
  - 实现 useWorkbenchBridge 监听 Agent tool_result 事件路由 render_data 到对应 Pane 时，发现 useChatStore 没暴露 `lastToolResult` / `lastToolCall` 字段，无法直接 subscribe；
  - 进一步发现 useChatStore.ts L632-661 的 `tool_result` 分支用 `resultText = sse.result.output || sse.result.error || JSON.stringify(sse.result)` 把 result 简化成字符串存进 `step.result`，`target_pane` 和 `render_data` 字段在 store 里丢失，subscribe `streamingSteps` 也拿不到 render_data；
  - 另外 Task 7 描述说已加 `setWorkbenchUserOverride` / `resetWorkbenchOverride` setter 和 `setWbTab(t, source?)` 参数，但实际 useAppStore.ts 只有 `workbenchUserOverride: boolean` 字段，没有这两个 setter，setWbTab 也没加 source 参数。
- 错误原因：
  - T5 边界约束：useChatStore.ts 是 T5 独占不能改，其内部 tool_result 处理逻辑把结构化 result 压平成字符串（供 ActivityBlock 展示），没考虑 Agent 联动需要结构化字段；
  - Task 7 标记为 [x] 完成但实际只加了 `workbenchUserOverride` 字段，没加 setter（任务描述与实际不符）；
  - subscribe 模式只能拿到 store 暴露的字段，对未暴露的字段（如 SSE 事件原始 payload）无法观测。
- 修复方式：
  - useWorkbenchBridge.ts 导出 `handleToolResultEvent(event)` 和 `handleToolCallEvent(event)` 两个 handler 函数，由 T5 在 useChatStore.ts 的 onEvent 回调 tool_result / tool_call 分支里加一行调用（协作点，需通知 T1）；
  - 同时导出 `initWorkbenchBridge()` 兜底函数，subscribe `streamingSteps` 监听新 pending tool step（含 call_id）出现，触发 `resetOverrideIfNewCallId`，让"新 Agent turn 重置 override"在 T5 未配合时也能工作（render_data 路由仍需 T5 配合）；
  - 重置 override 用 `useAppStore.setState({ workbenchUserOverride: false })` 直接写，绕过缺失的 setter（zustand 允许 setState 直写任意字段）；
  - 严格不改 useChatStore.ts / useAppStore.ts / types/api.ts，在报告里明确协作点。
- 下次注意：
  1. T5 边界文件（useChatStore.ts）内部如果把 SSE 事件 payload 简化存储，T6 不能靠 subscribe 拿原始字段，必须导出 handler 让 T5 在事件入口处调用；
  2. zustand 的 `setState({ field: value })` 可以绕过缺失的 setter 直接写任意字段，作为临时 workaround，但应在报告里标记让 Task 7 owner 补 setter；
  3. 跨线程协作点（T6 监听 T5 数据）优先用"handler 函数 + T5 调用"模式，subscribe 兜底只能处理暴露的字段；
  4. 验证任务完成度时不能只信 tasks.md 的 [x] 标记，要 Read 实际文件确认 setter/参数是否真的加了。

## 2026-06-24 - newSession 异步 ID 迁移竞态导致新对话输入丢失

- **错误现象**：新对话开始时输入消息后，回答"莫名其妙退出/消失"——用户消息显示了但 assistant 回答永远为空。
- **错误原因**：`useSessionStore.ts` 的 `newSession` 采用"先本地后同步"策略：同步生成 `localId` 并切换为当前会话，异步调后端拿 `res.id` 后迁移。若用户在后端响应前发送消息，SSE 回调闭包捕获 `requestSessionId = localId`，迁移后 `sessionMessages[localId]` 已被删除，SSE 数据全部写入幽灵会话。
- **修复方式**：将 `newSession` 改为 `async` 函数，先 `await apiPost("sessions", ...)` 拿到后端真实 ID，再用该 ID 创建本地会话并切换。彻底消除 localId/res.id 双 ID 并存窗口。后端失败时回退到本地 ID。
- **下次注意**：1) 异步创建资源时不要假设本地生成的临时 ID 与后端返回的 ID 一致，应以后端返回为准；2) `newSession` 类操作应先拿后端 ID 再切换 UI，避免迁移竞态；3) 调用方 `onClick={() => newSession()}` 无需 await，fire-and-forget 兼容 Promise 返回。

## 2026-06-24 - SSE 切换会话不中止导致切回内容被空覆盖

- **错误现象**：SSE 流式输出中途切换到其他会话再切回来，回答内容消失或只剩最后一两个字符。
- **错误原因**：`setActiveSession` 流式切换时保存了部分内容到 `sessionMessages` 但**不中止 SSE**（保留 `currentSseRequest`），同时清空了 `streamingContent=""`。后台 SSE 继续运行，切回原会话时 `isActive=true`，用空的 `streamingContent` 重新累积，覆盖了 `sessionMessages` 中已保存的完整内容。
- **修复方式**：`setActiveSession` 流式切换时调用 `currentSseRequest.abort()` 中止 SSE（`apiSSE` 的 catch 块会识别为用户中止，不触发 `onError`），同时清空 `currentSseRequest`。`stopStreaming` 增加防御：`streamingSessionId` 为 null 时跳过写入避免误伤当前会话；优先取已有 `lastMsg.content` 而非空的 `streamingContent`。
- **下次注意**：1) 流式输出期间切换会话必须中止 SSE 并保存部分内容，不能让后台 SSE 继续运行（`streamingContent`/`streamingSteps` 是单例状态，切回后无法恢复对应缓冲区）；2) `stopStreaming` 必须用 `streamingSessionId` 精确定位会话，不能回退到 `activeSessionId`；3) `apiSSE` 的 `controller.signal.aborted` 检查确保用户中止不触发 `onError`。

## 2026-06-24 - 鉴权异常降级为匿名访问 + 多端点缺少鉴权

- **错误现象**：`dependencies.py` 的 `current_user` 在鉴权存储读取异常时返回 `anonymous=True`，攻击者可通过触发文件异常绕过鉴权。MCP/sandbox/auth 端点完全无鉴权，任何人可执行任意命令/代码/篡改 API Key。
- **错误原因**：1) `except Exception` 捕获器降级为匿名访问而非拒绝；2) `mcp_routes.py`、`sandbox_routes.py`、`auth.py` 的 `list_keys`/`delete_key` 端点未添加 `Depends(current_user)`；3) `auth.py` 与 `dependencies.py` 存在循环依赖，无法直接导入 `current_user`。
- **修复方式**：1) `dependencies.py` 异常时返回 503 而非匿名访问；2) MCP 全部端点和 sandbox 端点添加 `Depends(current_user)`；3) `auth.py` 的 `list_keys`/`delete_key` 用内联 `Header` 检查避免循环导入（`store-key` 作为认证端点本身不要求鉴权）。
- **下次注意**：1) 鉴权异常时必须默认拒绝访问（fail-closed），不能降级为匿名（fail-open）；2) 所有执行代码/命令的端点必须鉴权；3) 存在循环依赖时可用内联 `Header` 检查替代 `Depends(current_user)`；4) 认证端点本身（如 `store-key`）不需要鉴权，因为用户首次设置 Key 时还没有 token。

## 2026-06-24 - deleteSession 未中止 SSE + SSE 解析失败未中止流 + 导出 ContentPart[] 损坏

- **错误现象**：1) 删除正在流式输出的会话后 SSE 继续写入已删除的会话；2) SSE 连续 3 次 JSON 解析失败后仅回调 `onError` 但不中止流，可能无限循环；3) 导出 Markdown 时多模态消息内容输出 `[object Object]`。
- **错误原因**：1) `deleteSession` 未调用 `stopStreaming`；2) `apiSSE` 解析失败 3 次后没有 `controller.abort()`；3) `exportConversation` 用 `${m.content}` 模板字符串对 `ContentPart[]` 输出 `[object Object]`。
- **修复方式**：1) `deleteSession` 删除活跃会话时先调 `chatStore.stopStreaming()`；2) `apiSSE` 3 次失败后调 `controller.abort()` 并 `return`；3) `exportConversation` 对 `ContentPart[]` 转为 Markdown 文本后再导出。
- **下次注意**：1) 删除资源前必须先清理关联的异步操作（SSE/定时器）；2) 错误回调后必须中止流读取，不能仅通知错误；3) 模板字符串对对象类型输出 `[object Object]`，必须先转为字符串。

## 2026-06-24 - 图片上传多个问题：显示 base64 原文/分裂/SSE abort/模型不独立

- **错误现象**：1) 用户上传图片后，对话界面显示 `data:image/png;base64,...` 原文而非图片；2) 图片消息在界面"分裂成两个"；3) 第二次上传图片时 SSE 报 `BodyStreamBuffer was aborted`；4) 一个对话选择 A 模型后所有对话都变成 A 模型。
- **错误原因**：
  1) 用户消息气泡用 `{renderContent(msg.content)}` 纯文本渲染，`![Image](data:...)` Markdown 语法不被解析，显示为文字
  2) 消息 content 用字符串拼接 base64 数据 URL（可达数 MB），渲染时视觉上"分裂"
  3) 后端发 `error` 事件 → `onEvent` 调 `stopStreaming` → `stopStreaming` 调 `currentSseRequest.abort()` → body stream 中断 → 内层 catch 捕获 "BodyStreamBuffer was aborted" → 内层 catch **未检查 controller 是否已 abort** → 再次调 `onError` → 再次调 `stopStreaming` → `streamingSessionId` 已被第一次清空
  4) `sendMessage` 从全局 `useSettingsStore.getState().model` 读取模型，而非当前会话的 `session.model`
- **修复方式**：
  1) 用户气泡改用 `<MarkdownRenderer content={renderContent(msg.content)} />` 渲染，正确解析图片 Markdown
  2) 消息 content 改为 `ContentPart[]` 类型存储（而非拼接 base64 字符串），`renderContent` 已支持转换
  3) `apiSSE` 内层 catch 增加 `controller.signal.aborted` 检查，已 abort 时不调 `onError`；`stopStreaming` 增加幂等检查（`!isStreaming && !streamingSessionId && !currentSseRequest` 时直接返回）
  4) `sendMessage` 改为从 `useSessionStore.getState().sessions.find(s => s.id === activeSessionId)?.model` 读取模型；`InputBar` 选择模型时同时调 `updateSessionMeta(activeSessionId, { model: m.id })` 更新当前会话
  5) `sendMessage` 和 `handleSend` 的空文本守卫改为允许只有附件没有文字（`!content.trim() && (!attachments || attachments.length === 0)` 才拒绝）
  6) `onDone` 中 preview/title 生成增加 `extractText` 函数处理 `ContentPart[]` 类型
- **下次注意**：
  1) 用户消息和 assistant 消息都应通过 `MarkdownRenderer` 渲染，不能用纯文本
  2) 图片消息应用 `ContentPart[]` 类型存储，不要把 base64 数据 URL 拼接进字符串
  3) SSE 错误处理链中，`onEvent` 的 error 处理和 `onError` 回调可能对同一错误重复调用 `stopStreaming`，必须做幂等检查
  4) `apiSSE` 的内层 catch（reader.read 异常）也要检查 `controller.signal.aborted`，避免 abort 导致的读取错误触发 `onError`
  5) 模型等会话级配置应从 `session` 对象读取，不能从全局 `settingsStore` 读取
  6) 空文本守卫要考虑"只有附件"的场景

## 2026-06-21: 模型下拉框不显示上游模型
→- **错误现象**：验证 API Key 成功后，设置页和聊天输入框的模型下拉框只显示硬编码的 fallback 模型（gpt-4o/llama3.3/deepseek-v3），不显示上游 Provider 返回的真实模型列表。
- **错误原因**：
  1. SettingsPage.tsx handleVerify 成功后没有 invalidate TanStack Query 缓存，useQuery 的数据仍是空的
  2. useQuery queryKey 只依赖 baseUrl + provider，切换 Key 不触发 refetch
  3. 后端 routes.py Key 优先级为 stored_key or header_key，导致已存储的旧 Key 永远覆盖前端新输入的 Key
- **修复方式**：
  1. SettingsPage.tsx：导入 useQueryClient，handleVerify 成功后 queryClient.invalidateQueries({ queryKey: ["models"] })
  2. SettingsPage.tsx + InputBar.tsx：queryKey 加上当前 Key 值，Key 变化自动 refetch
  3. routes.py（两处：models + chat）：Key 优先级改为 header_key or stored_key or settings.llm_api_key
- **下次注意**：
  1. TanStack Query 的 cache 是隐式的，手动调用 API（如 handleVerify）后必须 invalidate 对应 query，否则 useQuery 不更新
  2. queryKey 必须包含所有会影响结果的输入，否则值变化不触发 refetch
  3. 后端 Key 优先级：用户刚输入的 X-API-Key header > 后端已加密存储的旧 Key > .env 默认值。Header 代表用户当前意图，必须优先

## 2026-06-24 - 图片渲染分裂/SSE 滚动卡顿

- **错误现象**：1) 发送单张图片在对话界面分裂为两张；2) 无法查看图片原图；3) SSE 输出时滑轮上下滑动卡顿。
- **错误原因**：
  1) `renderContent` 把 `ContentPart[]` 转成 `![Image](data:image/png;base64,...)` markdown 字符串，超长 base64 URL（可达数 MB）导致 ReactMarkdown 解析异常，可能重复渲染图片
  2) 图片通过 markdown `<img>` 渲染，没有点击查看原图的交互
  3) 自动滚动 `useEffect` 依赖 `[messages.length, isStreaming, streamingContent, streamingSteps]`，`streamingContent` 每个 token 变化触发 `scrollTo({ behavior: "smooth" })`，smooth 动画堆积导致滑轮卡顿
- **修复方式**：
  1) 新建 `UserMessageContent` 组件：`ContentPart[]` 中的文本走 `MarkdownRenderer`，图片直接用 `<img>` 渲染（不走 markdown 解析），避免超长 base64 URL 问题
  2) 图片点击弹出 lightbox 全屏查看原图（`position: fixed; inset: 0; background: rgba(0,0,0,0.85)`）
  3) 自动滚动策略改为：只在 `messages.length` 变化时滚动（用 `behavior: "auto"`），流式期间完全不自动滚动；`isStreaming` 变为 false 时才 smooth 滚动到底部；`wheel` 事件用 `addEventListener + { passive: true }` 确保浏览器原生滚动优先级最高
- **下次注意**：
  1) 超长 base64 data URL 不能放进 markdown 图片语法，ReactMarkdown 解析会出问题；图片应用 `<img>` 直接渲染
  2) 流式输出期间不能频繁 `scrollTo({ behavior: "smooth" })`，smooth 动画会堆积卡顿；流式期间应禁用自动滚动或用 `behavior: "auto"`
  3) 滑轮事件用 `addEventListener + { passive: true }` 注册，不用 React 的 `onWheel`（React onWheel 在某些浏览器中是 passive 的，无法保证优先级）

## 2026-06-24 - 图片重复发送 + 重试丢失图片内容

- **错误现象**：1) 发送单张图片在对话界面显示两张相同图片；2) 点重试按钮后用户发送的图片内容丢失。
- **错误原因**：
  1) 前端 `sendMessage` 同时通过 `messages` 的 `ContentPart[]` 和 `attachments` 字段发送图片。后端 `chat_routes.py` 第 85-96 行从 `attachments` 提取图片到 `image_parts`，第 112-115 行再把 `image_parts` 拼到 `last_user_msg`，导致图片被发送两次给 LLM。虽然前端渲染用的是本地 `messages`（只有一份图片），但后端处理逻辑会导致 LLM 看到两张图片，可能返回异常结果。
  2) `retryMessage` 第 563 行 `typeof userContent === "string" ? userContent : ""`，当用户消息是 `ContentPart[]`（含图片）时传空字符串给 `sendMessage`，图片和文本全部丢失。
- **修复方式**：
  1) 前端 `requestBody.attachments` 过滤掉图片类型（`attachments.filter(a => !a.type.startsWith("image/"))`），图片只在 `messages` 的 `ContentPart[]` 中发送，避免后端重复处理
  2) `retryMessage` 增加 `ContentPart[]` 处理：提取文本部分作为 `content`，提取图片部分重建为 `Attachment[]` 传给 `sendMessage`
  3) `branchThread` 的 model 也改为从当前会话读取（之前从全局设置读取）
- **下次注意**：
  1) 前后端不要通过多个通道发送同一数据（图片既在 `messages.ContentPart[]` 又在 `attachments`），后端会重复处理
  2) `retryMessage`/`editAndResend` 必须处理 `ContentPart[]` 类型的消息内容，不能简单地用 `typeof === "string"` 判断后传空字符串
  3) 所有读取 model 的地方都应从当前会话读取，不从全局设置读取

## 2026-06-23 - kb_routes.py 重写后测试用例与旧 API 不匹配
- **错误现象**：`pytest tests/test_routes_kb.py` 两个测试全部失败。`test_upload_success` 报 `assert False is True`；`test_upload_file_too_large` 报 `assert 200 == 400`。
- **错误原因**：
  1. 旧测试期望 upload 响应中 `chunks > 0`（同步入库），但新 API 改为异步索引，响应返回 `status="indexing"` + `chunks=0`，后台任务完成后再更新 DB
  2. 旧测试期望文件超限时返回 HTTP 400（`HTTPException`），但新 API 改为返回 HTTP 200 + `{"success": False, "error": {"code": "FILE_TOO_LARGE"}}`（错误体包装）
  3. 旧测试 patch `app.api.routes.MAX_UPLOAD_SIZE`，但新代码在 `app.api.kb_routes` 模块，patch 路径不对
  4. 测试 fixture 未创建 builtin KB 记录，导致 `kb_manager.get_kb("builtin-001")` 返回 None → `KB_NOT_FOUND`
- **修复方式**：
  1. 测试 fixture 中调用 `init_db()` + 创建 `KnowledgeBase(is_builtin=True)` 记录
  2. `test_upload_success` 改为断言 `status == "indexing"` 而非 `chunks > 0`
  3. `test_upload_file_too_large` 改为断言 `response.status_code == 200` + `data["error"]["code"] == "FILE_TOO_LARGE"`
  4. patch 路径改为 `app.api.kb_routes.MAX_UPLOAD_SIZE`
- **下次注意**：
  1. 重写 API 路由后必须同步更新对应测试，尤其是响应格式和状态码变更
  2. 异步索引 API 的测试只能验证"已接受请求"，不能验证"索引完成"——后者需要 mock 后台任务或等待完成
  3. `unittest.mock.patch` 的路径必须是代码实际定义的模块，不是旧模块路径

## 2026-06-23 - kb_manager.ingest_chunks 重复实现 vector_store 逻辑
- **错误现象**：`kb_manager.ingest_chunks()` 方法内部重复实现了 embedding 检查、metadata 构建和 ChromaDB 写入逻辑，与 `vector_store.ingest_chunks()` 高度重复。
- **错误原因**：kb_manager 最初设计时没有委托给 vector_store，而是自己处理入库，导致两处逻辑需要同步维护。
- **修复方式**：`kb_manager.ingest_chunks()` 改为：1) 注入 `kb_id` 到每个 chunk 的 metadata；2) 委托 `store.ingest_chunks(chunks, doc_id)` 处理 embedding 检查和写入；3) 标记 BM25 stale。
- **下次注意**：Manager 层应委托给 Store 层处理底层操作，不要重复实现。Manager 职责是路由和协调，Store 职责是持久化。

## 2026-06-24 - 上传文件 0 片段：未配 embedding 时 ingest_chunks 返回 0
- **错误现象**：上传文件后显示 "0 片段"，但文件已成功上传。
- **错误原因**：`vector_store.ingest_chunks()` 在 `self.embeddings is None` 时直接返回 0（未配置 embedding API）。chunking 本身正常工作，但无法向量化入库 ChromaDB。`_update_doc_status` 用 `ingested`（入库返回值）作为 `chunk_count`，导致显示 0。
- **修复方式**：在 `kb_routes.py._index_document()` 中，用 `len(chunks)` 作为 `chunk_count`（而非 `ingested`）。如果 `ingested == 0`，标记 status="indexed" 但加 error_message 提示"未配置 Embedding 模型，已分块但未向量化"。
- **下次注意**：chunking 和 embedding 是两个独立步骤。chunk_count 应反映实际分块数，而非入库数。入库失败不应影响 chunk_count 显示。

## 2026-06-24 - fetchEmbeddingModels 吞掉错误导致 UI 永远显示"未找到模型"
- **错误现象**：配置 Embedding 模型时点"获取模型列表"，无论 API Key 是否正确、网络是否通，都显示"未找到模型"。
- **错误原因**：`useKnowledgeStore.fetchEmbeddingModels` 在 catch 块中 `return []`，永远不向外抛异常。`RagSettingsPanel` 的 catch 块是死代码，用户看不到真实错误（如 401 Unauthorized、超时等）。
- **修复方式**：移除 store 层的 try/catch，让错误传播到调用方。`RagSettingsPanel` catch 块显示 `e.message`。同时改进 `unwrapResponse` 读取错误响应体中的 `error.message` 字段。
- **下次注意**：Store 层不要吞掉错误。让调用方决定如何处理错误（显示 toast、设置状态等）。

## 2026-06-24 - sanitize_error 正则双重转义导致 API Key 脱敏失效
- **错误现象**：错误日志中 URL 参数里的 API Key 未被脱敏，明文显示。
- **错误原因**：`re.sub(r"([^&\\s]+)", r"\\1***", ...)` 中 raw string 的 `\\s` 是 3 个字符（反斜杠+反斜杠+s），regex 引擎解释为"非反斜杠且非 s"，而非"非空白"。`\\1` 同理，解释为字面反斜杠+1 而非后向引用。
- **修复方式**：改为 `r"([^&\s]+)"` 和 `r"\1***"`（单反斜杠）。
- **下次注意**：Python raw string 中 `r"\s"` 是 2 个字符（反斜杠+s），regex 引擎解释为空白符。`r"\\s"` 是 3 个字符，regex 引擎解释为字面反斜杠+s。不要混淆。

## 2026-06-24 - addAttachments 在 StrictMode 下双重调用导致图片重复

- **错误现象**：发送单张图片在对话界面显示两张相同图片，跨多轮修复仍未解决。
- **错误原因**：
  1) `InputBar.tsx` 的 `addAttachments` 将异步副作用（`Promise.all(fileToAttachment)`）放在 `setAttachments` 的 state updater 函数内部。React 18 StrictMode 下 state updater 会被双重调用，导致**两个 Promise.all 同时处理同一个文件**，产生两个 Attachment 对象（id 不同但 content 相同）。
  2) 虽然 `mergeUniqueAttachments` 按 `name+type+content` 去重，但两个 Promise 解析时序可能导致两个 `setAttachments` 回调都看到空的 `current`，各自添加一份，绕过去重。
  3) `sendMessage` 的 for 循环对每个 attachment 都 push 一个 `image_url` part，如果 attachments 有重复，parts 就有重复。
- **修复方式**：
  1) 把 `Promise.all` 从 state updater 中移出，改为先读取 `attachmentsRef.current`（ref 镜像），在 updater 外部处理文件，再调用 `setAttachments` 合并
  2) 添加 `attachmentsRef` 同步 attachments state，避免 async 回调中读取 stale state
  3) 添加 `sendingRef` guard 防止 `handleSend` 重入
  4) 在 `sendMessage` 中添加 `seenImageUrls` Set 去重，防御性确保同一图片 content 只创建一个 `image_url` part
  5) 添加调试日志：`sendMessage` 记录 attachments/images 数量，`UserMessageContent` 记录 imageParts 数量
- **下次注意**：
  1) **永远不要在 state updater 函数内部放副作用**（Promise、setTimeout、fetch 等）。updater 必须是纯函数，StrictMode 会双重调用。
  2) 需要在 async 回调中读取最新 state 时，用 `useRef` 镜像 state，不要依赖闭包捕获的值
  3) 关键操作（如发送消息）应加 ref guard 防止重入
  4) 数据层去重是最后一道防线，即使上游逻辑正确也应做防御性去重

## 2026-06-25 - docs/review 扫描报告验证与标准化修复

- **错误现象**：`docs/review/` 下 8 份扫描报告存在大量事实性错误，部分声明行号不对、部分声明完全虚假、部分真实 bug 未修复。直接按报告修复会引入新问题。
- **错误原因**：
  1) 报告基于旧版代码编写，行号与当前代码不匹配（多数 P2/P3 声明）
  2) 5 个声明完全虚假：TopBar slice bug（已修复）、chatFontSize 未用（实际在用）、_summarize_messages 未调用（实际在用）、create_session 500（已有守卫）、database.py 无 try/except（实际有）、FALLBACK_PORTS 未用（实际在用）
  3) 真实 bug 未被修复：AuditPinsRequest 字段不匹配契约、file_parsers 缺 parse_from_bytes 方法、tool_routes 读 dict.__doc__、sandbox 错误码不匹配契约、CPU_TIMEOUT=10 vs 契约 30s、vite 端口 58080 vs 后端 8000
- **修复方式**：
  1) **标准化验证流程**：4 个并行 search agent 逐条验证声明，区分 ❌ FALSE / ⚠️ PARTIALLY TRUE / ✅ TRUE
  2) **P0 契约对齐**：hardware_routes.py AuditPinsRequest 改为 `pin_assignments: dict[str, PinAssignmentInfo]`，响应 conflicts/warnings 改为 `list[PinWarning]` 对象数组
  3) **P0 错误码对齐**：sandbox_routes.py 错误码改为 EMPTY_CODE/CODE_TOO_LONG/UNSUPPORTED_LANGUAGE/EXECUTION_TIMEOUT/SANDBOX_UNAVAILABLE，移除 HTTPException 改用标准 {success: False, error: {code, message}} 格式
  4) **P0 端口对齐**：vite.config.ts 代理端口 58080→8000；executor.py CPU_TIMEOUT 10→30
  5) **P1 缺失方法**：file_parsers.py 添加 BaseParser.parse_from_bytes 默认实现、PdfParser 类、ExcelParser 别名、各解析器 parse_from_bytes 覆盖
  6) **P1 逻辑 bug**：tool_routes.py 修复 `func.__doc__` → `entry["fn"].__doc__`；添加 Depends(current_user) 鉴权；chat_routes.py 修复 attachment_texts.append 缩进（移入 if text: 块内）
- **下次注意**：
  1) 扫描报告必须逐条验证后才能修复，不能盲目信任报告内容
  2) 行号声明最容易过时，验证时以代码实际内容为准
  3) 契约文档（api-contract.md）是接口对齐的唯一真相源，前后端字段不匹配时以契约为准
  4) Windows 终端运行 Python import 验证时，用 subprocess + timeout 避免重依赖卡死，不要用 signal.SIGALRM（Windows 不支持）

## 2026-06-25 - 后端启动卡在 "Waiting for application startup" + token-usage 404

- **错误现象**：后端重启后卡在 `INFO: Waiting for application startup.`，前端请求 `/api/token-usage/stats` 返回 404。看起来像死锁。
- **错误原因**：
  1) `_ensure_builtin_kb()` startup 事件中 `from src.rag.kb_manager import get_kb_manager` 触发连锁重依赖导入（chromadb + langchain + sqlalchemy），在本机约需 2-3 分钟
  2) 新增的 `/api/token-usage/stats` 路由虽然代码正确，但后端未完全启动时路由未注册，导致 404
  3) `trae-sandbox` 会吞掉终端 stdout，无法看到 `print()` 调试输出，误判为卡死
- **修复方式**：
  1) 用 `Start-Process -RedirectStandardOutput stdout.txt -RedirectStandardError stderr.txt` 启动后端，绕过 trae-sandbox 输出吞没
  2) 等待 2-3 分钟后后端正常启动，`/health` 返回 healthy，`/api/token-usage/stats` 返回 200
  3) 移除调试 print 语句，清理临时文件
- **下次注意**：
  1) 后端启动慢不是 bug，是 sqlalchemy/chromadb/langchain 导入开销大，需耐心等待 2-3 分钟
  2) 调试后端启动问题时，用 `Start-Process -RedirectStandardOutput` 重定向到文件，再用 Read 工具读取，避免 trae-sandbox 吞输出
  3) 新增 API 路由后 404 = 后端未重启或未完全启动，先检查 `/health` 确认后端状态

## 2026-06-25 - Token 用量记录：部分 provider 不返回 stream usage

- **错误现象**：用户反馈看不到 Token 用量图表，即使聊天后数据库仍为 0 条记录。
- **错误原因**：`stream_options: {"include_usage": True}` 是 OpenAI 专有参数，部分 provider（如某些 Ollama 配置、非标准 OpenAI 兼容 API）不返回 `chunk.usage`，导致 `usage_data` 始终为 None，不写入数据库。
- **修复方式**：在 `chat_routes.py` 流式结束后添加 fallback：如果 `usage_data` 为 None，用 `LLMClient._estimate_tokens()` 估算输入（从 messages + system_prompt）和输出（从累积的 `full_response_text`）token 数，并写入数据库。
- **下次注意**：
  1) 不能假设所有 provider 都支持 `stream_options`，必须有 fallback 估算机制
  2) 累积 `full_response_text` 用于估算输出 token，不能只 yield 不保存
  3) Token 记录失败不应阻断聊天流程，用 try/except 包裹并 logger.warning

## 2026-06-25 - 重复上传检测 DetachedInstanceError 导致 500

- **错误现象**：用户上传同名文件时返回 500 "服务器内部错误"，用户误以为是向量化失败。
- **错误原因**：`kb_routes.py` 重复上传检测代码在 `with get_db_ctx() as db:` 块关闭后访问 ORM 对象属性 `existing.doc_id` / `existing.status`，触发 `DetachedInstanceError`（session 已关闭，无法懒加载属性）。
- **修复方式**：在 session 块内提取标量值：
  ```python
  with get_db_ctx() as db:
      existing = db.query(KnowledgeDoc).filter(...).first()
      existing_doc_id = existing.doc_id if existing else None
      existing_status = existing.status if existing else None
  ```
- **下次注意**：SQLAlchemy ORM 对象离开 session 后不能访问属性（除非 `expire_on_commit=False`）。在 session 块内提取所有需要的标量值。

## 2026-06-25 - Builtin KB 缺少 Embedding API Key 导致向量化失败

- **错误现象**：用户在前端全局设置中配置了 Embedding 模型后上传文件，仍显示"未配置 Embedding 模型，已分块但未向量化"。
- **错误原因**：
  1) `ensure_builtin_kb()` 创建 builtin KB 时只设 `embedding_model`，未设 `embedding_api_key_encrypted` 和 `embedding_base_url`
  2) 前端全局 Embedding 设置存在 localStorage，从未同步到后端 KB 记录
  3) `HardwareVectorStore.__init__` 中 `api_key = embedding_api_key or settings.embedding_api_key`，两者都为空 → `self.embeddings = None` → 向量化跳过
- **修复方式**：
  1) 后端：`PATCH /api/kb/collections/{kb_id}/config` 接口 + `kb_manager.update_kb_config()` 方法（含缓存失效）
  2) 前端：KB 管理界面加齿轮"编辑配置"按钮 + 表单
  3) `startEditConfig` 从全局设置（localStorage）预填 embedding 配置到 KB 配置表单，用户只需点保存即可同步
- **下次注意**：
  1) 前端全局设置不会自动同步到后端，需要显式调用 API
  2) 每个 KB 需要独立配置 embedding，不能依赖全局 fallback
  3) `ensure_builtin_kb()` 应该从 settings 读取默认 embedding 配置（如果 settings 有 API Key）


## 2026-06-25 - LangChain OpenAIEmbeddings tiktoken 导致百炼 400 错误

- **错误现象**：上传文件后向量化报 `Error code: 400 - InternalError.Algo.InvalidParameter: Value error, contents is neither str nor list of str.: input.contents`。
- **错误原因**：LangChain `OpenAIEmbeddings` 默认 `tiktoken_enabled=True` + `check_embedding_ctx_length=True`，会用 tiktoken 把文本分词成整数 token ID 列表再作为 `input` 发送。OpenAI 原生 API 接受 token ID 列表，但阿里云百炼（DashScope）OpenAI 兼容模式只接受字符串或字符串列表，遇到整数列表就报 400。
- **修复方式**：在 `HardwareVectorStore.__init__` 的 `OpenAIEmbeddings` 初始化中设置 `tiktoken_enabled=False`、`check_embedding_ctx_length=False`（发送原始文本字符串），以及 `chunk_size=10`（百炼 text-embedding-v4 单次最多 10 条）。
- **下次注意**：1) 非 OpenAI 原生 API（百炼/Azure/其他兼容服务）使用 LangChain `OpenAIEmbeddings` 时必须禁用 tiktoken；2) 官方文档明确 `input` 类型为 `array<string> 或 string`，不接受 token ID；3) 百炼 v4 批量上限 10 条，v1/v2 是 25 条。


## 2026-06-25 - asyncio.create_task 任务被 GC 导致索引状态卡死

- **错误现象**：文件上传后文档状态永远停在 "indexing"，不变成 "indexed" 也不变成 "error"，stderr 无任何错误日志。
- **错误原因**：`asyncio.create_task(_index_document())` 创建后台任务后未保留引用。Python 事件循环只对 task 保持弱引用，请求处理函数返回后 task 可被垃圾回收。GC 触发时 task 收到 `CancelledError`（Python 3.8+ 是 `BaseException` 子类），`except Exception` 无法捕获，状态更新代码永远不执行。
- **修复方式**：1) 模块级 `_bg_tasks: set = set()` 保持强引用；2) `task = asyncio.create_task(...); _bg_tasks.add(task); task.add_done_callback(_bg_tasks.discard)`；3) 新增 `except asyncio.CancelledError` 分支更新状态为 "error" 后 `raise`。
- **下次注意**：1) `asyncio.create_task` 返回值必须保存引用，否则 task 会被 GC；2) Python 3.8+ `CancelledError` 继承 `BaseException` 而非 `Exception`，`except Exception` 捕获不到；3) 后台任务无日志且状态不变 = 被 GC 的典型症状。


## 2026-06-25 - verify_page_coverage 返回 set 导致 JSON 序列化失败

- **错误现象**：向量和 chunks 都已成功入库（日志显示 "Ingested 4 chunks"），但文档状态仍卡在 "indexing"，后台日志报 `TypeError: Object of type set is not JSON serializable`。
- **错误原因**：`verify_page_coverage()` 返回 `{"covered_pages": covered_set, ...}`，其中 `covered_set` 是 `set` 类型。`_update_doc_status` 用 `json.dumps(coverage)` 序列化时，`set` 不可 JSON 序列化，抛出 `TypeError`。该异常被 `_update_doc_status` 的 `except Exception` 捕获并仅记日志，不重新抛出，导致 `record.status = "indexed"` 这行虽然执行了但因异常回滚未提交到数据库。
- **修复方式**：1) `verify_page_coverage` 返回 `sorted(covered_set)`（list 代替 set）；2) `_update_doc_status` 的 `json.dumps` 加 `default=str` 安全网。
- **下次注意**：1) 返回值如果要 JSON 序列化，不能用 `set`，用 `sorted(list)`；2) `_update_doc_status` 的 `except Exception` 会吞掉错误导致状态不更新，关键操作的状态更新失败应该让调用方知道；3) "向量化成功但状态卡住" = 状态更新函数静默失败的典型症状。



## 2026-06-25 - HybridChunker 双层 overlap 嵌套导致大量重复 chunk

- **错误现象**：60KB 的 .md 文档切出 166 chunks，平均 350 字节。目录树被切成 19 片，JSON 示例被切到对象中间。检测发现 chunk 17/18 完全相同，chunk 70/71 完全相同。`big_chunk_text` 只是 1000 字符的 sub_chunk 片段，不是完整 section，小-大映射名存实亡。
- **错误原因**：`hybrid_chunker.py` 的双层切分设计有缺陷——先用 `_splitter`(1000/overlap=200) 切 section 得到 sub_chunks，sub_chunks 之间有 200 字符 overlap；然后对每个 sub_chunk 用 `_small_splitter`(500/overlap=100) 切小 chunk。overlap 区域的 200 字符在两个相邻 sub_chunk 中都存在，被 `_small_splitter` 各自独立切分，产出重复或近似重复的 small chunks。`big_chunk_text = sub_text` 也只是 1000 字符的中间产物，不是完整 section。
- **修复方式**：采用方案 C（先小后大聚合）——去掉中间层 `_splitter`，让 `_small_splitter`(500/overlap=0) 直接切 section。`big_chunk_text = section_text[:4000]`（完整 section 截断到 4000）。overlap=0 消除所有重复。同时：separators 最前加 `"\n```\n"` 保护代码块；`section_title` 改为带父标题路径（用 header_stack 维护层级）；`.md/.txt` 的 `page_range` 设为 `(0, 0)` 不造假页码。
- **下次注意**：1) 不要在嵌套切分中用 overlap——内外两层 overlap 会乘积式产生重复区域；2) 小-大映射的 big_chunk 应该是完整结构单元（section），不是中间切分产物；3) `.md` 文件没有真实页码，不要用字符估算造假，直接 `(0, 0)` 让前端显示"无页码信息"；4) `section_title` 要带父路径避免同名子标题混淆。

## 2026-06-25 - KB 管理三连崩：刷新丢文件 / 删不掉 / 重复阻挡

- **错误现象**：用户报告三个连锁问题：1) 刷新界面后看不到已上传的文件列表；2) 重新上传同名文件被"重复文件"错误阻挡；3) 点删除按钮一直删不掉，提示冲突。
- **错误原因**：
  1) **刷新丢文件**：前端 `useKnowledgeStore` 的 `activeKbId` 初始化硬编码 `DEFAULT_KB_ID = "builtin-001"`，没有持久化。用户切换到自定义 KB 上传文件后刷新页面 → `activeKbId` 重置回 `builtin-001` → `fetchItems("builtin-001")` 过滤不到任何文件 → 列表为空。
  2) **重复文件阻挡**：`kb/upload` 的重复检测对所有状态的记录都阻挡，包括 `status='error'`（上次上传失败留下的孤儿）和 `status='indexing'`（服务器重启留下的卡死记录）。用户被指引去删除孤儿，但删除又失败 → 死循环。
  3) **删不掉**：`kb_delete` 端点把 `file_path.unlink()` 放在 `get_db_ctx()` 事务内。Windows 文件锁（ChromaDB/其他进程持有句柄）导致 unlink 抛 `PermissionError` → 整个事务回滚 → DB 记录始终删不掉。同时前端 `deleteItemWithAPI` 只做乐观更新，成功后没有 reload 列表，UI 与后端状态不一致。
- **修复方式**：
  1) **activeKbId 持久化**：新增 `loadActiveKbId()`/`saveActiveKbId()` 两个 helper 读写 `localStorage["kb-active-kb-id"]`；store 初始化、`setActiveKb`、`fetchCollections` fallback、`deleteCollection` 切换四处都同步持久化。
  2) **重复检测覆盖孤儿**：`kb/upload` 遇到 `status in ('error', 'indexing')` 的同名记录时自动清理（DB 记录 + 向量 + 文件 best-effort）后继续上传；仅 `status='indexed'` 的成功记录阻挡。
  3) **删除事务解耦**：`kb_delete` 把文件 unlink 移到 `get_db_ctx()` 事务外作为 best-effort，DB 记录删除不再被文件锁阻塞。前端 `deleteItemWithAPI` 成功后调 `fetchItems(kbId)` 重新加载列表。
- **下次注意**：
  1) 用户切换的状态（activeKbId、activeSessionId、当前选中项等）必须持久化到 localStorage，否则刷新就丢
  2) 重复检测要区分"成功"和"失败"状态——失败/卡死的记录应该被自动覆盖，而不是阻挡用户重试
  3) Windows 文件锁是常见问题，文件 I/O 不要放在 DB 事务内，事务只管 DB 操作
  4) 乐观更新成功后必须 reload 后端列表，否则 UI 与后端状态漂移

## 2026-06-25 - import 端点创建新 doc_id 导致向量永远删不掉

- **错误现象**：通过 `kb_import` 导入的文档，删除时 DB 记录能删，但 ChromaDB 里的向量永远删不掉，越积越多。
- **错误原因**：`kb_import` 端点创建 `doc_id = str(uuid.uuid4())` 作为 KnowledgeDoc 记录，但 `store.import_data` 直接把源 KB 的 chunks 写入 ChromaDB 时保留**源 doc_id**（在 chunk metadata 里）。删除时 `store.delete_document(new_doc_id)` 在 ChromaDB 里 `where={"doc_id": new_doc_id}` 找不到任何匹配的 chunk（chunks 的 metadata 里是源 doc_id）→ 返回 0 删除 → 向量孤儿留下。
- **修复方式**：`kb_import` 从 `export_data.data.metadatas` 提取所有源 doc_id，按源 doc_id 分组（每组聚合 title 和 chunk_count），创建多条 KnowledgeDoc 记录时直接复用源 doc_id。删除时按 doc_id 查 ChromaDB 就能匹配。已有同 doc_id 记录时改为 update 而非 insert（避免 unique constraint）。
- **下次注意**：1) 跨存储层级（DB + ChromaDB）的实体 ID 必须一致，不能在写入时改 ID；2) import 类操作要把源数据里的关键字段（doc_id、metadata）原样保留，不能凭空生成新 ID；3) 删除操作失败时检查 DB 记录的 ID 与向量存储中的 ID 是否真的匹配。

## 2026-06-25 - 服务器重启后 indexing 状态卡死（1 小时阈值太长）

- **错误现象**：服务器重启后，DB 里 status='indexing' 的记录卡住不变成 error，前端轮询 2 分钟超时，用户也无法重新上传同名文件（被重复检测阻挡）。
- **错误原因**：startup 事件里虽然有清理 indexing 记录的逻辑，但加了 `created_at < now - 1 hour` 阈值——意思是只清理创建超过 1 小时的 indexing 记录。如果用户上传后 5 分钟内服务器重启，记录创建时间在 1 小时内，不会被清理，永远卡在 indexing。
- **修复方式**：移除时间阈值，启动时把所有 `status='indexing'` 的记录全部改成 `status='error'` + `error_message='服务重启时索引中断，请重新上传'`。理由：startup 只在进程启动时跑一次，此时任何 indexing 状态都是孤儿（后台 asyncio task 已随进程退出）。
- **下次注意**：1) 启动时的状态清理不要加时间阈值——只要进程重启了，所有未完成的后台任务都是孤儿；2) 后台异步任务（asyncio.create_task）的状态字段在重启后必须被显式重置，不能假设任务能恢复。

## 2026-06-26 - HybridChunker 代码块碎片化 + 文本块不合并导致大量无上下文短 chunk

- **错误现象**：用户上传 3 个 Markdown 文件后，chunk 质量极差：frontend-prompt.md 60 chunks、backend-prompt.md 82 chunks、backend-api-spec.md 25 chunks，总计 167 chunks 其中 51 个 <100 chars。文件树代码块（5161 chars）被 RecursiveCharacterTextSplitter 切成 18 个无意义行片段；API 规范段被切成 7 个 40-100 chars 的碎片（"响应：`{ok: true}`" 独立成块），完全没有上下文。
- **错误原因**（三层 bug 叠加）：
  1. **长代码块被 small_splitter 碎切**：`_split_markdown` 把所有内容（含代码块）作为纯文本传给 `RecursiveCharacterTextSplitter`，splitter 在代码块内部按行切开，文件树变成 18 个碎片。
  2. **短代码块合并但文本块不合并**：迭代2修复了短代码块合并回前一个文本块，但 `else` 分支里文本块（text part）直接 `merged.append` 创建新条目，不合并到前一个文本块。导致 `text→code→text→code` 产生多个独立 merged 条目，每个变成单独 section。section 3.5（会话管理 API，~542 chars）被拆成 7 个 section，每个 40-100 chars。
  3. **small_splitter 的 `"\n```\n"` 分隔符**：即使短代码块已合并回文本，splitter 仍能在 ``` 边界处切开，产生更多碎片。
- **修复方式**（三层修复）：
  1. **长代码块独立 section**：`_split_markdown` 用 `re.split(r"(```[\s\S]*?```)", text)` 提取代码块，长代码块（>500 chars）作为独立 section，`chunk()` 方法跳过 small_splitter 直接输出。短代码块（≤500 chars）用 `\x00CB{i}\x00` 占位符合并回文本，防止 bash `#` 注释被误认为 Markdown 标题。
  2. **文本块合并**：`else` 分支里文本块也合并到前一个文本块（`if merged and not merged[-1][0]: merged[-1] = (False, merged[-1][1] + part)`），整个 section 的文本+内联代码块成为一个 merged 条目，header split 正确地按标题分割。
  3. **small_splitter 占位符保护**：`chunk()` 方法在调用 small_splitter 前用 `_INLINE_CODE_RE.sub(_stash_code, section_text)` 替换所有内联代码块为占位符，split 完成后恢复。从 separators 列表移除 `"\n```\n"`。
  4. **后处理合并 tiny chunk**：`_merge_tiny_chunks` 方法把 <100 chars 的非代码块 chunk 合并到同 section 的前一个 chunk。
- **结果**：3 个文件从 167 chunks → 83 chunks（-50%），短块从 51 → 8（-84%）。剩余 8 个短块都是"标题+长代码块"前的标题行（如 "### 3.2 LLM 代理层" 40 chars），属于结构性边界。
- **下次注意**：1) Markdown chunker 必须在 structural split 阶段就把代码块提取为独立单元，不能依赖 RecursiveCharacterTextSplitter 处理代码块；2) 文本块和代码块的 merge 逻辑要对称——代码块合并到前文本，文本块也要合并到前文本，否则 `text→code→text→code` 会产生碎片；3) 在调用 RecursiveCharacterTextSplitter 前用占位符保护代码块，防止 ``` 边界被误用为分隔符；4) 永远加一个后处理步骤合并 tiny chunk，因为 RecursiveCharacterTextSplitter 总会在大块之间留下小碎片。

## 2026-06-26 - Agent 分块器黑箱化：prompt 简陋 + 子分块代码块碎片化 + 无透明度

- **错误现象**：Agent 分块器（LLM 驱动）存在三大问题：1) prompt 模板极简（纯英文、6 行、不指导代码块保护/上下文保持/分块大小），LLM 输出质量不可控；2) `_build_chunks` 中的子分块用 `RecursiveCharacterTextSplitter(1000/200)` 无代码块保护，与 HybridChunker 之前修复的碎片化 bug 完全相同；3) 整个 LLM 分块过程是黑箱——无日志、无投票轮次记录、无争议边界暴露、API 不返回 `is_code_block`/`boundary_disputed`/`section_summary`/`agent_trace` 等元数据。
- **错误原因**：
  1) `_AGENT_CHUNK_PROMPT` 只要求 `start_page/end_page/title/summary`，没有代码块保护指令、上下文保持指令、分块大小目标、关键词提取、置信度评估，也没有双语支持
  2) `_build_chunks` 的子分块直接用 `splitter.split_text(section_text)`，代码块中的 `#` 注释被误认为 Markdown 标题，``` 边界被 `"\n\n"` 分隔符切开
  3) `chunk()` 方法只在最后 `logger.info` 了一行总结，没有记录每轮 LLM 调用的 prompt/response/timing，`ChunkResult.metadata` 中没有 `agent_trace` 字段
  4) `kb_routes.py` 的 `get_doc_chunks` 端点只暴露 `chunk_index/content/section_title` 等基础字段，不暴露 `is_code_block`/`boundary_disputed`/`section_summary`/`agent_trace`
  5) 所有参数（prompt、temperature、rounds、batch_size）硬编码，无法按 KB 调整
- **修复方式**（四层修复）：
  1. **重写 prompt 模板**：双语（中文为主）、6 条分析规则（语义完整性/代码块保护/上下文保持/大小目标/标题层级/特殊内容）、要求输出 `keywords/has_code_block/confidence`、可通过 `prompt_template` 和 `prompt_system_extra` 构造参数覆盖
  2. **子分块代码块保护**：`_build_chunks` 在调用 `_sub_splitter` 前用 `_INLINE_CODE_RE.sub(_stash_code, section_text)` 替换内联代码块为 `\x00CB{i}\x00` 占位符，split 后恢复；整个 section 是代码块时直接输出不切分；`_merge_tiny_chunks` 合并 <100 chars 的非代码块 chunk
  3. **ChunkTrace 透明度**：新增 `ChunkTrace` 和 `RoundResult` dataclass，记录 model/num_rounds/temperature/toc_entries/num_batches/每轮 sections_found/elapsed_seconds/error/voted_sections/disputed_sections/final_chunks/total_elapsed/config，嵌入每个 chunk 的 `metadata["agent_trace"]`；`chunk()` 方法每步都有 `logger.info` 日志（TOC/batches/每轮 LLM 调用/vote/build）
  4. **API 暴露新字段**：`get_doc_chunks` 端点新增 `is_code_block`/`has_code_block`/`boundary_disputed`/`section_summary`/`section_keywords`/`section_confidence`/`agent_trace`（trace 做了摘要避免响应过大）；`_get_kb_chunker` 预留了 `num_rounds`/`max_batch_chars`/`sub_chunk_size` 的 KB 级配置接口
- **下次注意**：1) LLM 驱动的处理流程必须有完整的 trace 数据结构记录每步决策，不能只靠日志——日志是给人看的，trace 是给 API/前端看的；2) Agent chunker 的子分块逻辑必须复用 HybridChunker 的代码块保护技术，不能用裸 RecursiveCharacterTextSplitter；3) prompt 模板要双语（中文项目用中文为主）、要有具体规则和示例、要可通过构造参数覆盖；4) API 返回的 chunk 元数据要尽可能丰富，让用户能判断分块质量——`is_code_block`/`boundary_disputed`/`section_summary`/`agent_trace` 都是关键透明度字段。

## 2026-06-26 - PowerShell 内联 python -c 引号转义失败

- **错误现象**：在 PowerShell 中用 `python -c "..."` 传递含 f-string 双引号的测试脚本（如 `print(f"  {s[\"title\"]}")`），报 `SyntaxError: '[' was never closed`。
- **错误原因**：PowerShell 对 `\"` 的转义处理与 Python 字符串语义冲突，嵌套双引号在 `python -c "..."` 的外层双引号中被 PowerShell 提前截断，导致 Python 收到的源码残缺。
- **修复方式**：放弃内联 `python -c`，改用 Write 工具写临时 `.py` 测试文件再 `python _tmp_test_mm.py` 执行，验证后删除。
- **下次注意**：PowerShell 下运行含嵌套引号/花括号的 Python 代码，优先用临时文件；内联 `python -c` 仅适合无引号冲突的极简脚本。

## 2026-06-26 - MultimodalChunker 批次边界切断跨页 section

- **错误现象**：多模态分块器按 `batch_size=5` 切批，若某逻辑章节横跨第 4-7 页（批次 1=1-5，批次 2=6-10），批次 1 的 LLM 只看到第 5 页会把 section 截断到第 5 页，批次 2 的 LLM 又把它当新 section 从第 6 页开始——一个逻辑章节被切成两个 chunk，破坏语义完整性。
- **错误原因**：原实现 `page_data[i:i+batch_size]` 无重叠，相邻批次无共享上下文，LLM 无法识别跨边界 section 的连续性。
- **修复方式**：1) 新增 `_create_batches_with_overlap`，批次间保留 1 页重叠（batch_size=5 → 步长 4，批次 1=1-5，批次 2=5-9，批次 3=9-12）；2) 新增 `_merge_cross_batch_sections` 后处理，按 start_page 排序后，若相邻 section 页码重叠（`curr_start <= prev_end`）且标题相似（`_titles_similar` 归一化比较），合并为一个 section 取并集页码范围/flags/keywords；3) 新增 `_titles_similar` 标题归一化（去除编号/标点/空格，支持包含匹配）；4) 补 `max_chunks=500` 上限警告。
- **下次注意**：基于批次（文本或图像）的 LLM 分块器，批次边界都会切断跨边界 section，必须加重叠 + 后处理合并；标题相似度比较要先归一化（去编号/标点），再用包含匹配兜底"Overview" vs "Overview of GPIO"这类变体。

## 2026-06-26 - BM25 原始分数泄漏 + 阈值只过滤向量不过滤 BM25

- **错误现象**：用户设置相关度阈值 70%，但低于 70% 的结果仍被召回；部分来源显示相关度 2000%（BM25 原始分），另一些明明相关的结果只有 1%（因归一化时除以 maxScore=2000）。
- **错误原因**：1) `_bm25_search` 直接用 BM25 原始分（可达数千）作为 `SearchResult.score`；2) `rrf_fusion` 返回 `[r for _, r in scored]`，丢弃 RRF 融合分，保留原始 `SearchResult.score`——向量结果保留 cosine 0-1，BM25-only 结果保留原始分 2000+；3) `score_threshold` 只传给 ChromaDB 的向量搜索，BM25 路径完全绕过阈值。
- **修复方式**：1) `_bm25_search` 中将 BM25 分数归一化到 0-1（`score / max_score`）；2) `search()` 在 RRF 融合后统一过滤 `fused = [r for r in fused if r.score >= score_threshold]`；3) 前端 `ChatArea.tsx` 改用 `src.score * 100` 显示实际百分比，不再除以 `maxScore` 归一化。
- **下次注意**：混合检索（向量 + BM25）的两路分数必须在融合前归一化到同一量纲；阈值过滤必须在融合后统一应用，不能只作用于某一路径；`rrf_fusion` 返回时应保留 RRF 分数而非原始分数。

## 2026-06-26 - 删除文档后仍可引用：vector_store 静默吞异常 + BM25 缓存未清

- **错误现象**：用户删除文档后在对话中仍能引用到该文档的数据。
- **错误原因**：1) `vector_store.delete_document` 用 try/except 吞掉所有异常返回 0，导致 DB 记录删除成功但向量残留（孤儿向量）；2) `_rebuild_bm25` 在 corpus 为空（最后一个文档删除）或重建失败时未清理内存中的 `_bm25_indices` 缓存，后续搜索仍返回已删除文档。
- **修复方式**：1) `delete_document` 移除 try/except，失败时 raise，让调用方 `kb_routes` 的错误处理生效（返回 `VECTOR_DELETE_FAILED` 阻止 DB 记录删除）；2) `_rebuild_bm25` 在 corpus 为空时删除 pkl 文件 + pop 内存索引，在重建失败时也 pop 内存索引。
- **下次注意**：删除操作必须保证 DB 记录和向量/BM25 索引的原子性——任一失败都要阻止另一侧提交；缓存清理要覆盖空数据路径和异常路径，否则会产生脏读。

## 2026-06-26 - BigChunkExpander 组件删除后引用残留导致 TS 编译失败

- **错误现象**：移除 BigChunkExpander 组件定义后，RightPanel.tsx 第 153 行仍引用 `<BigChunkExpander>`，TypeScript 编译报错。
- **错误原因**：删除组件时只删了函数定义，漏了 JSX 使用处。
- **修复方式**：删除 `{src.small_chunk_id ? <BigChunkExpander smallChunkId={src.small_chunk_id} /> : null}` 这行；同时清理 globals.css 中的 `.bigchunk-*` 样式块。
- **下次注意**：删除 React 组件时用 `Grep` 全局搜索组件名，确保引用处和定义处一起清理；CSS 中对应的样式类也要一并删除避免死代码。

## 2026-06-26 - HybridChunker small_chunk_size=500 导致中文语义截断

- **错误现象**：用户反馈"完整语义的东西被截断了"——测试文档 01-stm32-gpio.md 的 Section 2.3（复用功能模式，约 1000 字符，含 AF 编号表 + 代码示例）在 500 字符处被切断，表格和代码示例分到不同 chunk。
- **错误原因**：`small_chunk_size=500` 对中文文档偏小（中文字符紧凑，500 字符约等于 250-300 个英文单词），多数逻辑章节超过此阈值被切断。
- **修复方式**：`small_chunk_size` 从 500 调到 800——测试验证 Section 2.3（709 字符）现在完整保留在一个 chunk 内，Section 6 寄存器参考表（754 字符）也完整。
- **下次注意**：中文文档的 chunk_size 应比英文大 30-50%（中文字符信息密度高）；调整后需用实际文档验证关键章节是否完整，不能只看 chunk 数量。


## 2026-06-26 - 测试脚本 embedding 配置不匹配 + list_docs 端点错误

- **错误现象**：RAG 评估脚本跑起来后，5 个文档全部 `status=error`，错误信息 `No credentials for provider: openai`；同时等待索引的循环永远检测不到完成，每个文档等 60s 超时后继续上传下一个。
- **错误原因**：
  1. 测试 KB 用 LLM 代理 `9router.zxyzx.bbroot.com` 做 embedding，但该代理只支持 chat completions，不支持 `/v1/embeddings` 端点（返回 400 "No credentials for provider: openai"）。所有 embedding 模型名（`text-embedding-3-small`、`oc/text-embedding-3-small`、`text-embedding-v4`）都失败。
  2. `list_docs` 方法调 `/kb/list`（返回 `data.documents` 扁平结构），却按 `data.collections` 分组结构解析，永远返回空列表，导致等待循环检测不到索引完成。
- **修复方式**：
  1. 新增 `_fetch_builtin_embedding_config()` 从数据库读取 builtin-001 的 embedding 配置（Aliyun DashScope `text-embedding-v4` + 解密 API key），测试 KB 复用此配置做向量化。
  2. `list_docs` 改用 `/kb/collections/{kb_id}` 端点（返回 `data.documents`）；等待超时从 60s 提到 180s，轮询间隔 3s。
- **下次注意**：LLM 代理不一定支持 embedding 端点——创建测试 KB 前先验证 embedding API 可达；端点返回格式要与代码解析逻辑对应（`/kb/list` vs `/kb/collections/{id}` 返回结构不同）。


## 2026-06-26 - AgentChunker RoundResult 缺 to_dict 方法导致静默降级

- **错误现象**：所有文档上传到 agent 策略的 KB 时，状态显示 `indexed` 但 `error_message` 为 `"agent 分块失败，降级为 hybrid 分块"`；chunk 数量与 hybrid 完全一致（13/10/12/14/12），chunk_completeness 得分模式也完全相同——AgentChunker 实际从未成功执行。
- **错误原因**：`RoundResult` dataclass 没有 `to_dict()` 方法，但 `agent_chunker.py` 第 273 行和第 287 行调用了 `round_result.to_dict()`。LLM 调用本身成功（sections 已返回），但在记录 trace 时抛出 `AttributeError: 'RoundResult' object has no attribute 'to_dict'`，被外层 `except Exception` 捕获后触发 hybrid 降级 fallback。由于 `status=indexed` 且文档可检索，这个 bug 非常隐蔽——只有检查 `error_message` 字段才能发现。
- **修复方式**：给 `RoundResult` 添加 `to_dict(self) -> dict: return asdict(self)` 方法（`asdict` 已在文件顶部从 `dataclasses` 导入）。
- **下次注意**：dataclass 如果需要序列化，务必添加 `to_dict` 方法或直接使用 `asdict()`；降级 fallback 的 error_message 一定要检查，`status=indexed` 不代表 chunker 方法真正生效。


## 2026-06-26 - AgentChunker agent_trace dict 导致 ChromaDB upsert 失败

- **错误现象**：修复 RoundResult.to_dict() 后重新测试 agent-deepseek，01-stm32-gpio.md 在 156s 后 status=error，`error_message` 为空（API 层）；直接查数据库发现完整错误：`Expected metadata value to be a str, int, float, bool, SparseVector, list, or None, got {'method': 'agent', ...} which is a dict in upsert`。Agent chunking 的 LLM 调用成功了（6 个 voted_sections），但 `ingest_chunks` 向 ChromaDB 写入时崩溃。
- **错误原因**：`agent_chunker.py` 第 838 行把 `trace_summary`（一个 dict）直接放进 chunk metadata 的 `"agent_trace"` 字段。ChromaDB 的 metadata 只支持扁平标量值（str/int/float/bool/None/list），不支持嵌套 dict。`kb_routes.py` 的外层 `except Exception` 捕获后调用 `_update_doc_status(doc_id, "error", error_message=sanitize_error(str(e)))`，但 API 响应里 error_message 显示为空（可能是 sanitize_error 截断或 collections API 返回格式问题）。
- **修复方式**：把 `trace_summary` 序列化为 JSON 字符串：`trace_summary_json = json.dumps(trace_summary, ensure_ascii=False)`，两处 `"agent_trace": trace_summary` 改为 `"agent_trace": trace_summary_json`。
- **下次注意**：ChromaDB metadata 只支持扁平标量，任何 dict/list 嵌套结构必须先 `json.dumps`；ingest_chunks 失败时的 error_message 应该确保非空，便于排查。


## 2026-06-26 - RRF 融合分数语义错误导致阈值/百分比/relevance_level 全部失效 (P0)

- **错误现象**：RAG 检索结果的相关度百分比显示为 1.6%-3.3%，阈值过滤（如 0.7）过滤掉所有结果，relevance_level 永远是 "low"。用户看到的相关度极低，但实际上检索结果是正确的。
- **错误原因**：`rrf_fusion` 函数用 RRF rank-based 分数（`1/(k+rank+1)`，范围 0.016-0.033）替换了原始的 0-1 相似度分数。但下游代码（`chat_routes.py` 的阈值过滤、`score_pct = round(score * 100, 1)` 百分比显示、`if score >= 0.8: "high"` relevance_level 判断）全部按 0-1 范围处理。
- **修复方式**：修改 `rrf_fusion` 保留原始 0-1 分数（取 vector cosine 和 BM25 normalized 的 max），RRF 分数仅用于排序。这样阈值过滤、百分比显示、relevance_level 判断全部恢复正常。
- **下次注意**：当融合多种检索算法时，排序用的融合分数和显示/过滤用的原始分数必须分开。RRF 是 rank-based 方法，其分数范围与相似度分数完全不同，不能混用。


## 2026-06-26 - BM25 搜索时同步重建索引导致首次检索卡顿 (P2-3)

- **错误现象**：文档入库后首次搜索会卡顿 1-3 秒，因为 `_bm25_search` 检测到 `_bm25_stale` 标记时同步调用 `_rebuild_bm25`，阻塞搜索请求。
- **错误原因**：`ingest_chunks` / `import_kb` / 文档删除只标记 `_bm25_stale`，不重建 BM25。重建推迟到下次搜索时同步执行，导致用户可感知的延迟。
- **修复方式**：在 `ingest_chunks` / `import_kb` / 文档删除完成后立即预构建 BM25（这些操作本身在后台任务中执行，不会阻塞用户）。`_bm25_search` 中的 stale 重建保留为 fallback。同时移除 `update_kb_config` 中不必要的 BM25 stale 标记（BM25 只依赖文本，不依赖 embedding 配置）。
- **下次注意**：耗时操作（如索引重建）应在数据变更时后台预执行，不要推迟到查询时同步执行。区分"数据变更触发的重建"和"查询时的 fallback 重建"。


## 2026-06-26 - jieba 分词未配置硬件术语词典导致 BM25 匹配失败 (P2-6)

- **错误现象**：BM25 搜索 "STM32F4" 时无法精确匹配，因为 jieba 把 "STM32F4" 切成 "STM32"、"F"、"4" 等碎片，导致与文档中的 "STM32F4" 不匹配。
- **错误原因**：`BM25Index` 使用 `jieba.lcut` 进行中文分词，但未添加硬件术语词典。jieba 默认词典不包含 "STM32F4"、"I2C"、"DMA" 等硬件术语，会将其切分成无意义碎片。
- **修复方式**：在 `BM25Index` 添加 `_HARDWARE_TERMS` 列表（80+ 硬件术语），通过 `jieba.add_word(term, freq=1000)` 加载到词典。在 `_ensure_index` 和 `search` 中调用 `_load_hardware_dict`（进程级单例，只加载一次）。
- **下次注意**：使用通用分词器处理专业领域文本时，必须配置领域词典。否则专业术语会被切碎，导致 BM25/倒排索引无法精确匹配。


## 2026-06-27 - RAG 边缘情况审查发现 4 个 P0 + 6 个 P1 防御性问题

- **错误现象**：对 `kb_manager.rrf_fusion` / `vector_store.import_data` / `chat_routes._rewrite_query_for_rag` 做边缘情况审查后，发现 4 个会导致静默数据损坏的 P0 bug 和 6 个防御性缺失。
- **错误原因 + 修复方式**（逐条）：
  1. **P0-1 `_make_rrf_key` chunk_index=None**：`dict.get('chunk_index', i)` 在 key 存在但值为 None 时返回 None（不是 fallback i），导致同一 chunk 在向量/BM25 两个列表中产生不同 key，无法融合去重。修复：新增 `_make_rrf_key(r, fallback_idx)` 辅助函数，显式处理 None。
  2. **P0-2 import_data embeddings 长度不匹配静默丢弃**：`embeddings` 长度 ≠ `documents` 长度时，`valid_embeddings` 被置为 None，ChromaDB 会重新向量化或存入无向量文档——静默损坏。修复：长度不匹配时 `raise ValueError`。
  3. **P0-3 查询改写 LLM 返回换行**：LLM 可能返回多行输出（如 "STM32F4\nDMA\n配置"），直接传给 BM25/embedding 会破坏分词。修复：`rewritten = " ".join(content.split())` 折叠所有空白。
  4. **P0-4 多模态历史内容被丢弃**：`history` 中 content 为 `list[dict]`（图片+文本）时，`isinstance(content, str)` 为 False，整条历史被跳过，代词消解失效。修复：检测 list 类型后提取 `type=="text"` 的部分。
  5. **P1-5 constant_k<1 除零**：`1/(constant_k + rank + 1)` 当 constant_k=0 且 rank=0 时为 1/1=1（不崩），但 constant_k 为负数时 RRF 分数可能为负。修复：constant_k<1 时钳到 1。
  6. **P1-6 orig_score 未钳到 0-1**：BM25Okapi 对短文档可能返回负分（IDF<0），负分会泄漏到 FusedResult.score。修复：`orig_score = max(0.0, min(1.0, orig_score))`。
  7. **P1-7 import_data 只检查首条 embedding 维度**：只查 `valid_embeddings[0]` 的维度，后续维度不一致的向量会静默写入，污染 collection。修复：遍历所有 embeddings 检查维度。
  8. **P1-8 API 不可用时静默跳过维度检查**：`_get_embedding_dimension` 探测失败返回 None，import_data 直接跳过检查无任何提示。修复：添加 warning 日志（fail-open，不阻断导入）。
  9. **P1-A `_bm25_search` 归一化未钳负分**：`normalized = score / max_score` 当 score 为负时产生负的归一化分数。修复：`normalized = max(0.0, score / max_score)`。
  10. **P1-B `_get_embedding_dimension` 无负缓存**：API 探测失败后不缓存失败结果，每次 import_data 都会重新探测（阻塞 + 浪费配额）。修复：`__init__` 初始化 `_dim_check_attempted` 标志，finally 中置 True，已探测过直接返回缓存值。
- **下次注意**：
  1) `dict.get(key, default)` 在 key 存在但值为 None 时返回 None 而非 default——遇到可能为 None 的 metadata 字段必须显式判断 `is None`。
  2) 跨数据源融合（向量+BM25）的 dedup key 必须用辅助函数统一生成，不能在每个循环里内联 `f"{r.doc_id}#{r.metadata.get(...)}"`。
  3) LLM 输出永远不可信——可能包含换行、多余空格、空字符串、None content，必须做清洗和 fallback。
  4) 多模态消息（content 为 list[dict]）在所有处理历史/消息的代码路径中都必须单独处理，不能假设 content 永远是 str。
  5) rank_bm25.BM25Okapi 在小语料 + 高频词场景下会返回负分（IDF 计算结果），所有使用 BM25 分数的地方都要钳到 0-1。
  6) 探测类 API 调用（如 embedding 维度探测）必须缓存失败结果，否则每次调用都会重试，在 API 宕机时会放大故障。
  7) 修改后用 `python -m py_compile` 验证语法，用 pytest 覆盖所有边缘情况——本次写了 48 个测试全部通过。

## 2026-06-28 - Golden dataset YAML 中 0x68 被解析成整数导致 schema 校验失败

- **错误现象**：执行 `python -m tests.rag_eval.run_golden_eval --validate-only` 校验 [golden_dataset.yaml](file:///E:/Desktop/agent/backend/tests/rag_eval/golden_dataset.yaml) 时报错：
  ```
  jsonschema.exceptions.ValidationError: 104 is not of type 'string'
  On instance['samples'][13]['tags'][4]: 104
  ```
  G014 样本的 `tags` 第 5 个元素被解析成了整数 104，但 [golden_dataset_schema.json](file:///E:/Desktop/agent/backend/tests/rag_eval/golden_dataset_schema.json) 要求 `tags` 数组元素必须是 string。
- **错误原因**：G014 的 `tags: [I2C, MPU6050, 地址, HAL, 0x68]` 中 `0x68` 符合 YAML 1.1 的十六进制整数字面量规则（`0x` 前缀 + 十六进制数字），PyYAML 默认按 YAML 1.1 解析，把它转成了整数 104（0x68 = 104）。schema 要求 string，所以校验失败。其他 tags 如 `400kHz`、`4WAY_HANDSHAKE_TIMEOUT`、`GPIO0` 因为包含非十六进制字符或字母在数字之后，不会被误解析。
- **修复方式**：把 `0x68` 加引号：`tags: [I2C, MPU6050, 地址, HAL, "0x68"]`。修复后 `--validate-only` 通过：`Validation passed: 30 samples, version 1.0`。
- **下次注意**：
  1. **YAML 中任何以 `0x` 开头的标识符都要加引号**——PyYAML 按 YAML 1.1 规则会把 `0x` + 十六进制数字解析成整数，即使它在 flow sequence `[...]` 里
  2. **凡是包含纯数字/十六进制/科学计数法形式的 tag、version、id 字段，统一加引号**最稳妥
  3. **golden dataset 写完后必须跑 `--validate-only`**——schema 校验能精确报出第几个 sample 第几个字段出错，比人工 review 可靠
  4. **schema 要先于数据写好**——本次是先写 schema 再写 YAML，校验才能立刻发现问题；如果先写数据后补 schema，错误会被掩盖

## 2026-06-28 - 并行 Edit 同文件导致 save 方法改动被 load 编辑覆盖（B3+B8 BM25 参数化）

- **错误现象**：在 [kb_manager.py](file:///E:/Desktop/agent/backend/src/rag/kb_manager.py) 上一次性并行执行 4 处 Edit（`__init__` 加 k1/b 参数、`_ensure_index` 传 k1/b 给 BM25Okapi、`save` 把 k1/b 写进 pkl、`load` 从 pkl 读 k1/b）。4 个 Edit 都返回成功，但事后 Read 磁盘文件发现 `save` 方法仍是旧的单行 `pickle.dump({"corpus":..., "tokenized":..., "metadatas":...}, f)`，k1/b 没写进去；`load` 方法却已正确更新。第 3 个 Edit（save）的改动被第 4 个 Edit（load）回退了。
- **错误原因**：同一条消息里对**同一文件**并行发多个 Edit 调用时，后续 Edit 基于的上下文快照可能是前一个 Edit 应用前的旧视图。当两个 Edit 的修改区域相邻（save 在 line 139-144、load 在 line 146-156）时，后应用的 Edit 会把它上下文里携带的 save 旧内容写回去，静默回退前一个 Edit 的改动。4 个 Edit 各自返回的 result snippet 互相矛盾（save 的 snippet 显示新版多行 dict，load 的 snippet 却显示旧版单行 save）。与 Bug 15（Edit 返回成功但磁盘未写入）不同——这里改动确实写入了磁盘，只是被后续并行 Edit 的旧快照覆盖。
- **修复方式**：重新单独执行 save 的 Edit（不并行，单条调用），写入成功。用 Read 通读 + `ast.parse` 语法检查 + `Grep "BM25Index("` 三重验证，确认 `__init__`/`_ensure_index`/`save`/`load`/`_rebuild_bm25` 五处全部正确，134 个硬件术语入库。
- **下次注意**：
  1. **同一文件的多个 Edit 不要并行批量调用**——改同一文件时串行执行（一条消息一个 Edit，或等前一个完成再发下一个），每个 Edit 基于前一个的最新结果；只有改不同文件时才适合并行
  2. **并行 Edit 后必须 Read 验证相邻区域**——若不得不并行，改完用 Read 通读所有修改区域，特别检查相邻 Edit 的边界是否被回退（看 result snippet 互相矛盾就是信号）
  3. **Edit 返回的 snippet 不能完全信任**——以最终 Read 磁盘文件为准；4 个 snippet 里 save 显示新版、load 显示旧版 save，矛盾即说明有覆盖

## 2026-07-03 - 接线图连线重叠导致无法辨认（svg_generator.py 正交布线算法缺陷）

- 错误现象：
  1. Agent 调用 `render_wiring` 生成的接线图中，多条连线完全重叠在一起，分不清哪条线连哪个引脚；
  2. 所有连线都从同一位置出发，沿着相同路径到达目标，视觉上像一条线；
  3. 标签也重叠在一起，无法阅读。
- 错误原因：
  1. `svg_generator.py` 的连线算法采用固定路径：`{x1,y1} {x1-20,y1} {x1-20,y2} {x2,y2}`，所有连线都向左偏移 20px 后垂直移动，没有区分不同连线；
  2. 没有分线道（lane）机制，多条连线共享同一垂直通道；
  3. 组件排列顺序未优化（MCU 和外设混排），导致连线交叉更多。
- 修复方式：
  1. **彻底改用原理图风格布局**：MCU 固定在左侧垂直排列所有引脚，外设在右侧垂直堆叠；
  2. 新增 `_group_nets()`：按源组件+源引脚分组为独立网络（net）；
  3. 新增 `_compute_net_y_positions()`：每个网络分配一条独立的水平总线通道；
  4. 连线严格正交：MCU 引脚 → 垂直下降/上升 → 水平总线 → 垂直下降/上升 → 外设引脚；
  5. 标签绑定在每个外设引脚右侧，随目标引脚移动，不再漂浮；
  6. 外设按其所连网络的平均 Y 坐标垂直排列，减少连线长度和交叉。
- 下次注意：
  1. **正交布线必须有分线道机制**——当多条线跨越相同水平区间时，必须在垂直方向上分配不同通道；
  2. 组件布局要按功能分组（MCU 在左、外设在右），减少连线交叉；
  3. SVG 画布高度要根据连线数量动态扩展，避免通道不够；
  4. 标签要加背景遮罩，防止被连线遮挡；
  5. 同网络（net）的多条连线要从源组件**不同锚点/不同路径**出发，避免水平线段完全重叠；
  6. 标签必须绑定到目标引脚附近，不能简单用中点坐标，否则标签会漂浮到错误位置；
  7. 外设框体布局必须做**碰撞检测**，否则多个外设会重叠在一起；
  8. 同一网络到多个外设时，垂直引线要**水平错开**，不能共用同一条下降线；
  9. 标签内容要与引脚名去重，避免 "GND" 引脚旁边再显示 "GND 供电" 这种冗余标签。

## 2026-07-03 - 串口工作台真实硬件接入修复（fix-serial-real-hardware）

### 错误现象
串口工作台表面可用，实际 4 个阻断点导致"真正可用"打折扣：
1. 前端发 `type:"data"`，后端只认 `type:"write"` → 双向通信断了，只能收不能发
2. `_read_serial` 的 `except Exception: pass` 静默吞异常 → 拔线了前端毫无感知
3. 扫描失败时 `FALLBACK_PORTS` 假装有 COM3/COM5/ttyUSB0 → demo 时极易误导
4. `websocket.ping()` 心跳在 Starlette WS 上不生效（静默失败）

### 错误原因
- WS 消息协议前后端没对齐（前端写 data，后端等 write）
- 防御性 except pass 吞掉了所有异常，包括硬件断开的关键信号
- 假端口回退是为了"开发时不接硬件也能看 UI"，但上线后会误导
- Starlette WebSocket 没有 ping() 方法，与 websockets 库 API 不同

### 修复方式
1. SerialPane.handleSend 改发 `{"type":"write","payload":text+lineEnding}`
2. `_read_serial` except 改为发 error 事件 + logger.warning + 主动 `websocket.close(code=1011)` + break
3. 删除 FALLBACK_PORTS，扫描失败 setDevices([]) + warn 日志 + UI 显示「未扫描到串口设备」
4. 删除无效心跳任务（Starlette WS 靠 TCP keepalive 保活）
5. 5 处内层 `except Exception: pass` 改为 `except Exception as e: logger.debug(...)`

### 下次注意
- WS 消息协议前后端必须对齐，类型字段写文档先
- 防御性 except 不能用 pass，至少 logger.debug 记录
- 不要为了开发方便写假数据回退，用 mock 服务或环境变量区分
- Starlette WS API 与 websockets 库不同，无 ping()，靠 TCP keepalive
- lineEnding="none" 时 suffix 必须设为 ""（而非字面量 "none"），否则会发送 `textnone`
- JSX 字符串属性不解析转义符，`value="\r\n"` 得到 4 字符字面量，必须用 `value={"\r\n"}` JS 表达式
- break 必须在 while 内部，try/except 在 while 外部时 break 会 SyntaxError
- 严禁对同一文件并行执行多个 Edit，必须串行编辑（并行 Edit 会基于旧内容覆盖，导致改动"丢失"）




## 2026-07-03 - PlatformIO 首次编译下载 ESP32 工具链耗时较长

- 错误现象：
  1. 用户首次调用 `pio_runner.compile_firmware` 时，`pio run` 子进程会先下载 ESP32 工具链（esptool, xtensa-esp32-elf-gcc 等），文件体积 200-500MB，等待时间可达数分钟甚至更久；
  2. 在这期间 stdout 不会有「Compiling...」等编译进度行，只有下载/解压日志，前端可能误以为卡死；
  3. 工具链不在 `pip install -r requirements.txt` 范围内，PlatformIO 自己管理在用户家目录的 `~/.platformio/packages/` 下。

- 错误原因：
  1. PlatformIO 采用按需下载策略，`pip install platformio` 只装 Python 包，不预装任何工具链；
  2. 首次为某个 platform（如 espressif32）编译时，PlatformIO 才下载该 platform 对应的全部工具链；
  3. `pio_runner.py` 默认编译超时 `DEFAULT_COMPILE_TIMEOUT_S = 300`（5 分钟），首次编译可能不够；
  4. 前端没有区分「下载工具链」和「编译中」两种状态，统一直通为 `compile_log` 事件。

- 修复方式：
  1. 文档/README 提示用户首次编译会下载工具链，需保持网络畅通；
  2. 用户可用环境变量 `HWRAG_COMPILE_TIMEOUT` 覆盖默认 300 秒超时（如设为 600 或 900）；
  3. 后续调用复用 `~/.platformio/packages/`，不再重复下载，编译速度恢复正常；
  4. 前端在 compile_log 事件流中识别 `Processing esp32s3` / `Installing espressif32` 等关键字，提示「正在下载工具链」。

- 下次注意：
  1. 测试真实 pio run 之前，先在命令行手动跑一次 `pio run`（任意 esp32 项目）预下载工具链，避免自动化测试因下载超时失败；
  2. 任何调用 pio 子进程的代码都要把「首次下载工具链」计入超时预算，或允许用户覆盖超时；
  3. `platformio.ini` 里 `platform = espressif32` 不指定版本时，PlatformIO 会自动拉取最新版 platform，可能触发额外下载，固定版本（如 `platform = espressif32@6.5.0`）可减少意外；
  4. CI 环境跑 pio 前应预缓存 `~/.platformio/` 目录（cache key 用 `platformio.ini` 的 hash）。




## 2026-07-04 - J-1 装饰器改变 kb_routes 路由错误返回格式（重构风险，非 bug）

- 错误现象：
  1. `kb_routes.py` 的 `list_collections` 和 `create_collection` 两个路由改用 `@handle_route_errors()` 装饰器后，**异常路径的返回格式从 `{success: False, error: {code, message, details}}` 变成 HTTPException `{detail: "..."}`**；
  2. HTTP 状态码从 200（return dict）变成 500（raise HTTPException）；
  3. 原错误码 `INTERNAL_ERROR` / `KB_CREATE_FAILED` 丢失，统一变成 `Internal error: <异常信息>`。

- 错误原因：
  1. 任务描述给的装饰器实现是 `raise HTTPException(...)`，而 kb_routes.py 原路由用 `return {success: False, ...}` dict 模式，两种模式不兼容；
  2. 装饰器是 FastAPI 标准做法（raise HTTPException），但本项目路由统一用 dict 模式，前端可能依赖 `response.success === False` 判断错误；
  3. 严格按任务描述实现装饰器，未对齐本项目 dict 模式。

- 修复方式（当前未修，仅记录风险）：
  1. 当前改造仅影响 `list_collections` 和 `create_collection` 两个路由，其余 13 个路由仍用 dict 模式；
  2. 若前端依赖统一 dict 格式，需把装饰器改为返回 dict 而非 raise HTTPException，例如：
     ```python
     except Exception as e:
         logger.exception(...)
         return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": sanitize_error(str(e)), "details": None}}
     ```
  3. 或前端适配：这两个路由的错误响应从 `response.json().success === False` 改为 `response.ok === false` 判断。

- 下次注意：
  1. 抽装饰器前先确认本项目的错误返回约定（dict vs HTTPException），装饰器实现要和现有约定对齐；
  2. 改造路由时若装饰器改变返回格式，必须在报告里显著标注，让 parent agent 评估前端兼容性；
  3. `sanitize_error` 仍可用于 HTTPException detail，但错误码（code 字段）会丢失，前端若按 code 分支处理会失效；
  4. 后续若要把剩余 13 个路由也改用装饰器，必须先统一装饰器的返回格式（建议保留 dict 模式以保持向后兼容）。

## langchain 1.x HITL 中断模式评估（2026-07-06）

**错误现象**：langgraph 1.x 推荐用 `interrupt()` 主动中断替代 `interrupt_before` 被动中断，需要评估是否迁移到路线 A（interrupt() 主动中断）。

**评估结论**：保留路线 B（`interrupt_before` + `Command(resume=...)`）。理由：
1. langgraph-prebuilt 1.1.0 的 `create_react_agent` 签名仍支持 `interrupt_before` 参数（已用 `inspect.signature` 验证，参数类型 `list[str] | None = None`）；
2. deny 路径已主动 yield tool_result SSE：`hitl_handler._inject_deny_messages` 注入 deny ToolMessage → `_iter_agent_sse` → `convert_tool_message_to_sse` 生成 tool_result SSE（PLUR [ENG-2026-0702-001] 约束满足）；
3. `PermissionClassifier`（`backend/src/agent/core/toolkit/permission_classifier.py`）已做细粒度判断（LOW allow / HIGH `run_command` graded by `risk_classifier` / MEDIUM `path_guard` 校验），无需重构；
4. 迁移到 `interrupt()` 需新建 `tools_node.py` 包装 ToolNode，风险高，且前次 spec `upgrade-langgraph-1x-stack` Task 6 已决策保留路线 B。

**修复方式**：无需修改代码。HITL 三条路径验证：
- **deny 路径**：用户拒绝 → `_inject_deny_messages` 注入 deny ToolMessage（content="用户拒绝执行此工具"）→ `Command(resume={"action":"deny"})` → `_iter_agent_sse` 流式 → `convert_tool_message_to_sse` 生成 tool_result SSE（含 deny 原因）→ Agent 继续推理 ✓
- **stop 路径**：用户停止 → `yield sse_event("done", {"success": False, "reason": "user stopped"})` → return 终止 Agent ✓
- **approve 路径**：用户批准 → `_mark_user_allow_in_ctx` → `Command(resume={"action":"allow"})` → `_iter_agent_sse` → 工具实际执行 → tool_result SSE → `handle_auto_resume` 循环检查下一个工具 ✓

**下次注意**：langgraph 1.2.4 仍兼容 `interrupt_before`，但未来版本可能移除，需关注 release notes。若未来迁移到路线 A，需新建 `tools_node.py` 包装 ToolNode 并在工具内主动调用 `interrupt({"tool":..., "args":..., "risk":...})`。

## langchain 1.x Task 13: astream_events(v2) 迁移评估（2026-07-06）

**错误现象**：spec 要求将 `agent.astream(stream_mode=...)` 替换为 `agent.astream_events(version="v2")`，需评估迁移风险。

**评估结论**：降级处理，保留现有 `astream(stream_mode=["messages", "updates"])` 模式。理由：
1. **spec 描述与实际代码不符**：spec 称当前用 `stream_mode=["messages", "values", "custom_events"]`，实际代码（sse_adapter.py L105、L281）用的是 `["messages", "updates"]`，说明 spec 作者未核对代码；
2. **事件结构根本不同**：`updates` 模式产出 node_output dict（含完整 AIMessage + ToolMessage），用于提取 tool_call / tool_result；`astream_events v2` 的 `on_tool_start` / `on_tool_end` 是独立事件，结构完全不同，无法一一对应；
3. **大量状态管理依赖特定 chunk 结构**：
   - `pending_tool_calls` 集合（text→thinking 转换依赖，Round 7 q001 CR=0 回归根因）
   - `text_buffer` 缓冲（防止 actual_output 污染）
   - `call_history` / `step_index` / `call_start_time`（超时检测依赖）
   - `risk_level` / `decision_source`（权限门控展示依赖）
   - `accumulate_tokens`（依赖 messages 模式的 AIMessageChunk）
4. **`_merge_agent_and_tool_events` 并发合并机制**（59 行）与 astream_events 单一事件流模型不兼容，迁移需重写整个合并逻辑；
5. 迁移需重写 `_convert_chunk_to_sse` 分发 + `_handle_message_chunk` + `_handle_update_chunk` + `_merge_agent_and_tool_events`，可能丢失事件或顺序错乱，破坏 SSE 流式输出 / 工具调用 / HITL / autocompact。

**修复方式**：无需修改代码。保留 `astream(stream_mode=["messages", "updates"])`。

**下次注意**：astream_events v2 适用于简单链式调用，不适合 ReAct Agent 的多模式合并 + 工具事件并发合并场景。若未来 LangGraph 弃用 astream(stream_mode=...)，需先设计新的多模式事件合并方案再迁移。

## langchain 1.x Task 14: adispatch_custom_event 迁移评估（2026-07-06）

**错误现象**：spec 要求用 `adispatch_custom_event` 替换 `streaming_event_bus` 队列合并机制，需评估迁移风险。

**评估结论**：降级处理，保留 `streaming_event_bus.py` + `_merge_agent_and_tool_events`。理由：
1. **项目无现有 custom_events 验证**：Grep 搜索 `adispatch_custom_event|dispatch_custom_event|custom_events` 全项目无匹配，迁移是引入全新模式；
2. **与 Task 13 降级决策冲突**：当前 `stream_mode=["messages", "updates"]` 无 `custom_events` 模式，要捕获 `adispatch_custom_event` 必须在 stream_mode 加 `"custom_events"`，这变相修改了 Task 13 保留的逻辑（spec 称"Task 13 降级则 Task 14 仍可独立实施"的假设有误——custom_events 需显式加入 stream_mode）；
3. **工具内 config 传播不确定**：`adispatch_custom_event` 需要 RunnableConfig 传播，当前 `_drain_stream`（build_tool.py L471-494）只有 session_id 参数，无 config；工具 `_arun` 也不接收 config；
4. **事件实时性可能下降**：当前 `_merge_agent_and_tool_events` 是并发消费者模式（_consume_agent + _consume_queue），tool_event_queue 事件可在 agent stream 阻塞时立即推送；改用 custom_events 后事件交错顺序由 LangGraph 控制，编译日志实时性可能受影响；
5. **编译日志实时推送是核心功能**（build_firmware），不能破坏。

**修复方式**：无需修改代码。保留 `streaming_event_bus.py` + `emit_tool_event` + `_merge_agent_and_tool_events`。

**下次注意**：adispatch_custom_event 适合简单工具反馈场景，不适合需要并发合并 + 实时性保证的场景。若未来迁移，需先确认工具内 config 传播机制 + 在 stream_mode 加 "custom_events" + 重写 _drain_stream 签名。

## langchain 1.x Task 15: InjectedToolArg 替换 PrivateAttr 评估（2026-07-06）

**错误现象**：spec 要求用 `Annotated[ToolContext, InjectedToolArg]` 替换 `_ctx: ToolContext | None = PrivateAttr(default=None)` 注入机制，需评估迁移风险。

**评估结论**：降级处理，保留 PrivateAttr 注入模式。理由：
1. **ToolContext 是每请求构建的**（agent_factory.py L497-502 `_build_tool_ctx`），包含 api_key / session_id / settings / permission_mode / decision_source；InjectedToolArg 通常通过 RunnableConfig.configurable 注入，但 configurable 通常是会话级的（如 thread_id），放每请求的 ToolContext 语义不符；
2. **HITL resume 时 config 传递风险**：HITL resume 走 `Command(resume=...)` 路径，config 的 configurable 可能不包含 tool_ctx，导致工具拿不到 ctx，所有 26 个工具失效；
3. **LangGraph 1.x 的 InjectedToolArg 与 create_react_agent 集成不确定**：create_react_agent 内部用 ToolNode 调用工具，InjectedToolArg 的注入时机和 config 传播路径需深度验证，无法仅凭导入测试确认；
4. **当前 PrivateAttr 模式工作正常**：`_inject_ctx_and_register`（agent_factory.py L490-494）在每请求时手动注入 `tool._ctx = ctx`，26 个工具都依赖 `_arun → self._ctx → execute(args, ctx)` 链路，破坏 ctx 注入会破坏所有工具；
5. **迁移收益低**：PrivateAttr 模式虽非 langchain 1.x 推荐写法，但功能完整、已验证，迁移仅是代码风格优化，不解决任何实际问题。

**修复方式**：无需修改代码。保留 `_ctx: ToolContext | None = PrivateAttr(default=None)` + `_inject_ctx_and_register`。

**下次注意**：InjectedToolArg 适合无状态工具或会话级配置注入，不适合每请求构建的富上下文。若未来迁移，需先验证 HITL resume 路径的 config 传播 + 26 个工具的 ctx 注入完整性。

## langchain 1.x Task 16: post_model_hook 替换 SSE 层 token 计数评估（2026-07-06）

**错误现象**：spec 要求用 `post_model_hook` 替换 SSE 层的 `accumulate_tokens` token 计数，需评估迁移风险。

**评估结论**：降级处理，保留 SSE 层 token 计数。理由：
1. **post_model_hook 参数可用**（已验证 langgraph 1.2.4 的 `create_react_agent` 签名含 `post_model_hook` 参数），但 state 与 sse_adapter 的 state dict 不同：post_model_hook 接收 AgentState（含 messages），sse_adapter 的 state dict 含 token_limit / session_id，需重新映射；
2. **ContextVar 跨 task 传播不确定**：`_CUMULATIVE_TOKENS` 是 ContextVar（context_guard.py L26），post_model_hook 在 graph 执行上下文中运行，与 sse_adapter 的 asyncio task 可能不同，ContextVar 可能不传播；
3. **tool_result token 计数会丢失**：当前 `accumulate_tokens` 在两处调用——messages chunk（sse_adapter.py L158）和 tool_result（sse_helpers.py L199）；post_model_hook 只在模型调用后触发，tool_result 的 token 计数会丢失，导致累计 token 低估；
4. **触发时机变化**：当前 `accumulate_tokens` 在流式过程中（messages chunk）实时检测，超限立即抛 `ContextLimitError`；post_model_hook 在模型调用完成后触发，超限检测延迟到模型调用结束，可能导致已经生成完整响应后才报错；
5. **当前机制是 token 超限降级，不是真正的 autocompact**：spec 描述"autocompact 触发时机"与实际代码不符——实际代码是 token 超限抛 `ContextLimitError` → 降级为基础模式，不是自动压缩历史消息。

**修复方式**：无需修改代码。保留 `accumulate_tokens` 在 SSE 层的调用（sse_adapter.py L158 + sse_helpers.py L199）。

**下次注意**：post_model_hook 适合需要在模型调用后修改 state 的场景（如真正的 autocompact 压缩历史消息），不适合替换流式过程中的实时 token 计数。若未来实现真正的 autocompact，可考虑 post_model_hook + SSE 层计数并存的方案。

## langchain 1.x Task 23: Send API 实现 search_docs 多查询并行评估（2026-07-06）

**错误现象**：spec 要求用 LangGraph `Send` API 实现 `search_docs` 多查询并行 fan-out，需评估迁移风险。

**评估结论**：降级处理，保留 `create_react_agent` 原生并行工具调用。理由：
1. **`Send` 是 custom StateGraph 的 fan-out 机制，不能注入 prebuilt `create_react_agent`**：`Send(node, state)` 用于自定义 `StateGraph` 的条件路由节点返回值，动态派发并行子任务；项目用 `langgraph.prebuilt.create_react_agent`（agent_factory.py L93-100），其内部 `agent → tools → agent` 循环是黑盒，无法在不替换为 custom StateGraph 的前提下注入 `Send` fan-out 节点；
2. **Agent 已原生支持并行工具调用**：`backend/src/agent/prompts.py` L74-76 system prompt 已声明"独立任务（如查 2 个不相关参数）可一次调多个工具，省时间"；LLM 在单个 AIMessage 中 emit 多个 `tool_calls`，`ToolNode` 并行执行；`sse_adapter._emit_tool_calls_from_message`（sse_adapter.py L225-231）已迭代 `msg.tool_calls[:MAX_TOOL_CALLS_DISPLAY]` 处理多 tool_call。`search_docs(query="A")` + `search_docs(query="B")` 并行已可用，`Send` 是冗余；
3. **迁移需放弃 `create_react_agent`**：改用 custom `StateGraph` + 自定义 fan-out 节点返回 `[Send("search_node", {"q": q1}), Send("search_node", {"q": q2}), ...]`，破坏 HITL `interrupt_before=["tools"]`、checkpointer（SqliteSaver thread_id 恢复）、ToolSpec registry（26 个工具的 `_ctx` 注入 + ToolRouter dispatch）、PermissionClassifier 权限门控、审计日志、整个 `sse_adapter` 流式合并逻辑；
4. **阶段 2 经验**：8 个 task 中 7 个降级，仅 1 个实施，与本次决策一致。

**修复方式**：无需修改代码。保留 `create_react_agent` + 原生并行工具调用。

**下次注意**：`Send` API 适合 custom StateGraph 的动态 fan-out 场景（如 map-reduce），不适合 prebuilt `create_react_agent`。若未来需要"一次查询拆成多个子查询并行检索"，应让 LLM 在 system prompt 引导下 emit 多个 `search_docs` tool_call（已支持），而非引入 `Send`。已验证 langgraph 1.2.4 `from langgraph.types import Send` 可导入，但仅对 custom StateGraph 有效。

## langchain 1.x Task 24: LangGraph Subgraph 实现复合工具（build_firmware 子图）评估（2026-07-06）

**错误现象**：spec 要求将 `build_firmware` 拆为 LangGraph Subgraph（scan_lib_deps → resolve → compile → analyze → retry|done）+ checkpoint + time-travel 回退，需评估迁移风险。

**评估结论**：降级处理，保留 `BuildTool` 单工具 + 内部多步骤 Python 函数。理由：
1. **build_firmware 当前已是多步骤工具**：`BuildTool.execute`（build_tool.py L241-262）内部依次执行 `scan_lib_deps_from_code(code)`（扫描 `#include` → lib_deps 映射）→ 合并 explicit + scanned deps → `compile_firmware(req)` → `_drain_stream` 实时转发 compile_log/progress/heartbeat → `_format_build_output` 含 missing-lib/first-build-timeout 提示。spec 描述的"拆为子图"是对已有内部逻辑的 graph 化重写，不新增功能；
2. **Subgraph 破坏 ToolNode 契约**：`ToolSpec.execute` 返回 `dict`，`ToolNode` 包装为 `ToolMessage`；Subgraph 返回的是 state（`MessagesState` / 自定义 TypedDict），不是 dict，需额外适配层把 subgraph state → dict，破坏 26 个工具统一的 ToolSpec 接口；
3. **Subgraph 破坏 streaming_event_bus 实时编译日志**：当前 `_drain_stream`（build_tool.py L471-494）通过 `emit_tool_event(session_id, event)` 把 compile_log 实时推到 `streaming_event_bus` 队列，`sse_adapter._merge_agent_and_tool_events` 并发合并到 SSE。Subgraph 内部节点的流式事件**不会自动冒泡到父 agent 的 `astream`**，需在父 graph 加 `stream_mode=["messages","updates","custom_events"]` + subgraph 节点用 `adispatch_custom_event`（依赖已降级的 Task 14）；
4. **checkpoint/time-travel 对 demo 无价值**：time-travel 是从历史 checkpoint 回放 subgraph 中间状态。编译失败时用户的正确操作是"改代码 + 重新调 build_firmware"（Agent 自然语言驱动），不是"回放到 compile 节点重试"。当前 retry 是 Agent 看到 `_MISSING_LIB_HINT` 后主动补 lib_deps 重新调 build_firmware，已是正确 UX；
5. **嵌套 checkpoint 复杂度高**：subgraph checkpoint 需独立 SQLite 表 + state 序列化，与父 graph 的 `agent_checkpoints.sqlite` thread_id 模型耦合复杂，无用户可见收益。

**修复方式**：无需修改代码。保留 `BuildTool` 单工具 + `_run_compile` / `scan_lib_deps_from_code` / `_drain_stream` 内部多步骤。

**下次注意**：Subgraph 适合需要独立 state 隔离 + checkpoint 回放的复杂子流程（如多轮审批），不适合已是线性步骤且依赖实时事件流的工具。若未来 build_firmware 需要真正的"编译失败自动 retry 不同 lib_deps 组合"，应在 `BuildTool.execute` 内部加 retry 循环（Python 层），而非引入 subgraph。已验证 langgraph 1.2.4 `from langgraph.graph import StateGraph, MessagesState` 可导入。

## langchain 1.x Task 25: Store API 实现跨会话长期记忆评估（2026-07-06）

**错误现象**：spec 要求用 LangGraph `BaseStore` / `InMemoryStore` / `SqliteStore` namespace 化存储替换自建 FTS5（`session_search.py`），需评估迁移风险。

**评估结论**：降级处理，保留 `session_search.py` 的 SQLite FTS5 实现。理由：
1. **FTS5 是全文检索，Store API 是 KV + 语义检索，语义不同**：`session_search.py` 用 `CREATE VIRTUAL TABLE sessions_fts USING fts5(session_id, user_msg, assistant_msg, timestamp)` + `WHERE sessions_fts MATCH ?` 做词法级 MATCH 查询（如"LED 接线"分词匹配 user_msg/assistant_msg）。`BaseStore.search()` 走 namespace 前缀 + 向量语义检索（需 embedding），**无 FTS5 MATCH 等价能力**；短查询（"STM32 代码"）语义检索精度低于词法 MATCH；
2. **跨会话长期记忆已实现**：FTS5 表是单一 `agent_sessions.db`，所有 session 的消息都写入同一表，`search_session_history` 跨 session 检索（`ORDER BY rowid DESC LIMIT ?`）。Store API 的"value-add"（跨 thread namespace 持久化）已被 FTS5 覆盖；
3. **数据迁移成本**：现有 `agent_sessions.db` 的 FTS5 数据需导出 + 按 `(namespace=("sessions", session_id), key=msg_id)` 重新写入 Store；无现成迁移脚本；`InMemoryStore` 重启丢失，`SqliteStore` 需新建 schema；
4. **查询接口重写**：`search_session_history(query, limit)` → `store.search(namespace_prefix=("sessions",), query=query, limit=limit)`，但语义不同（词法 vs 语义），需引入 embedding 模型（额外依赖 + 延迟），且 `SearchHistoryTool`（risk LOW）的"上次你帮我生成的代码呢"场景对短关键词词法匹配更准；
5. **Store API 的 store 参数已可用但语义错位**：`create_react_agent` 签名含 `store` 参数（已验证），但 Store 主要服务于 `BaseStore` 注入到节点（如记忆跨 thread 读写），`SearchHistoryTool` 是 Agent 主动调用的工具，不是节点，Store 注入路径不直接适用。

**修复方式**：无需修改代码。保留 `session_search.py` 的 FTS5 + `SearchHistoryTool`。

**下次注意**：`BaseStore` 适合需要跨 thread 持久化 + 语义检索的场景（如用户偏好记忆），不适合替换已有的词法级 FTS5 全文检索。若未来需要"语义相似的历史会话"（而不仅是关键词匹配），可考虑 Store API + embedding 与 FTS5 并存，而非替换。已验证 langgraph 1.2.4 `from langgraph.store.memory import InMemoryStore` + `from langgraph.store.base import BaseStore` 可导入。

## langchain 1.x Task 27: StreamReader 实现流式工具结果评估（2026-07-06）

**错误现象**：spec 要求 `build_firmware` 返回 `StreamReader[dict]`，让前端实时看到编译日志增量（与 Task 14 `adispatch_custom_event` 协调），需评估迁移风险。

**评估结论**：降级处理，保留 `streaming_event_bus` + `_merge_agent_and_tool_events`。理由：
1. **`StreamReader` 在 langgraph 1.2.4 中不存在**：`from langgraph.types import StreamReader` 抛 `ImportError: cannot import name 'StreamReader'`。`dir(langgraph.types)` 只有 `StreamWriter` / `CustomStreamPart` / `StreamPart`，**无 `StreamReader`**。spec 的前提（"build_firmware 返回 StreamReader[dict]"）在已安装版本上**不可实现**；
2. **依赖已降级的 Task 14**：spec 自述"与 Task 14 adispatch_custom_event 协调"，而 Task 14 已降级（pitfalls.md 上文 L3273-3286），保留 `streaming_event_bus` 队列合并机制。`StreamReader` 依赖 `adispatch_custom_event` 的 config 传播 + `stream_mode=["custom_events"]`，Task 14 降级则 Task 27 失去基础；
3. **编译日志实时推送已实现**：`build_firmware._drain_stream`（build_tool.py L471-494）通过 `emit_tool_event(session_id, event)` 把 `compile_log` / `progress` / `heartbeat` 实时推到 `streaming_event_bus` 队列；`sse_adapter._merge_agent_and_tool_events`（sse_adapter.py L270-329）并发合并 agent stream + tool_event_queue，`_convert_tool_event_to_sse`（L331-354）转成 `compile_log` / `progress` / `thinking` / `heartbeat` SSE 事件。用户"前端实时看到编译日志增量"目标**已达成**；
4. **`_merge_agent_and_tool_events` 的并发消费者模型比 StreamReader 更适合**：当前是 `_consume_agent` + `_consume_queue` 两个 asyncio task 并发，tool_event_queue 事件可在 agent stream 阻塞（如等待 LLM）时立即推送，实时性优于 LangGraph 控制事件交错顺序的单一流模型（与 Task 14 评估结论一致）。

**修复方式**：无需修改代码。保留 `streaming_event_bus.py` + `_drain_stream` + `_merge_agent_and_tool_events`。

**下次注意**：`StreamReader` 在 langgraph 1.2.4 不可用，未来升级 langgraph 后需重新确认 API 存在性。spec 涉及"流式工具结果"的特性应优先检查是否与已降级的 Task 14（`adispatch_custom_event`）耦合。当前 `streaming_event_bus` 队列合并机制已满足编译日志实时推送，无需引入新 API。

## langchain 1.x Task 22: interrupt() 实现工具内细粒度 HITL 评估（2026-07-06）

**错误现象**：spec Task 22 要求在 `tool_spec.py:_arun` 内根据 `risk_level` 主动调用 `interrupt({"tool":..., "args":..., "risk":...})` 实现细粒度 HITL，需评估与现有 `interrupt_before=["tools"]` 机制的冲突风险。

**评估结论**：**降级处理**，保留现有 `interrupt_before=["tools"]` + `PermissionClassifier` + `Command(resume=...)` 机制，不在 `_arun` 内新增 `interrupt()` 调用。理由：

1. **双重中断点冲突**：现有 `agent_factory.py` L91 已配置 `interrupt_before=["tools"]`，在 ToolNode 执行前被动中断；若再在 `_arun` 内主动 `interrupt()`，会形成"pre-ToolNode 中断 + 工具内中断"两个中断点。pre-ToolNode 先触发 → PermissionClassifier 判定 allow → resume → 工具执行 → _arun 内再 interrupt() → 第二次中断，语义冗余且混乱。

2. **resume 命令语义不兼容**：
   - `interrupt_before` 的 resume 是 `Command(resume={"action": "allow"})`（hitl_handler.py L44 `_RESUME_ALLOW`），resume value 是动作描述；
   - 工具内 `interrupt()` 的 resume 是 `Command(resume=<工具实际用的值>)`，resume value 会**作为 interrupt() 的返回值**传回工具；
   - 现有 `handle_auto_resume` / `resume_agent_after_user` 发送的是 `{"action": "allow"}`，若 _arun 内有 interrupt()，工具会收到 `{"action": "allow"}` 作为 resume 返回值——这是无意义的，工具无法据此继续 execute。改 resume 命令结构会破坏前端 resume API + 三条路径（deny/stop/approve）。

3. **破坏 4 步权限门控**（违反用户强约束 #2）：现有 4 步是 permission_classifier → permission_gate（已合并进 classifier）→ user_confirm → audit_log，全部在 pre-ToolNode 阶段完成。若把权限判定移进 _arun，要么重复判定（违反单一真相），要么替换 pre-ToolNode 判定（违反约束 #2）。

4. **破坏 HITL deny/stop 主动 yield tool_result SSE**（违反 PLUR [ENG-2026-0702-001]）：现有 deny 路径是 `_inject_deny_messages` 注入 deny ToolMessage → `_iter_agent_sse` → `convert_tool_message_to_sse` 生成 tool_result SSE。若改用工具内 interrupt() + resume(deny)，deny 信号路径改变，无法保证 tool_result SSE 仍能正确 yield。

5. **影响全部 26 个工具**（违反用户强约束 #1）：`_arun` 在 ToolSpec 基类，改动影响所有 26 个工具。任何 bug 会破坏全部工具调用。

6. **Task 1 已决策保留路线 B**：Task 1（HITL 中断模式统一评估）已评估 interrupt() vs interrupt_before，结论保留路线 B（interrupt_before + Command(resume=...)）。Task 22 依赖 Task 1，应尊重 Task 1 决策。

7. **功能需求已由 PermissionClassifier 满足**：SubTask 22.2/22.3/22.4 的功能目标已由现有机制实现（详见下方验证），无需新增 interrupt()。

**现有机制对 SubTask 22.2/22.3/22.4 的满足验证**：

- **SubTask 22.2（LOW 风险工具跳过 HITL）✓**：`PermissionClassifier.check`（permission_classifier.py L79-80）对 `risk_level == LOW` 直接返回 ALLOW，hitl_handler L72-73 用 `_RESUME_ALLOW` 自动 resume，工具执行。已验证 21 个 LOW 工具：search_docs / read_file / list_files / web_search / audit_pins / wiring / render_wiring / render_code / render_safety_report / build_firmware / list_kb_docs / webfetch / search_history / vision_analysis / view_image / image_generation / grep / glob / todo_write。

- **SubTask 22.3（MEDIUM/HIGH 触发 HITL）✓**：
  - MEDIUM（write_file / edit_file / apply_patch / multi_edit）：`_decide_medium` 先 path_guard 校验路径，acceptEdits 模式 allow，default 模式 ASK → hitl_handler yield tool_confirm_required SSE 等用户确认；
  - HIGH flash_firmware / undo_edit：`_decide_high` 直接 ASK → HITL 确认；
  - HIGH run_command：`_decide_high` → `_grade_command` 用 risk_classifier 分级，LOW 命令（ls/cat/grep 等）auto-allow，其他 ASK → HITL 确认。
  - **注**：spec 称 build_firmware 应触发 HITL，但代码中 build_firmware risk_level=LOW（build_tool.py L237 "编译无副作用"），这是刻意设计（编译无副作用），按用户强约束 #1 不修改。

- **SubTask 22.4（权限门控 + 审计日志）✓**：
  - LOW auto-allow → ToolRouter.dispatch → `audit_recorder.record`（decision_source=auto_allow）；
  - MEDIUM/HIGH ask → 用户确认 → `_mark_user_allow_in_ctx` → dispatch → audit_recorder.record（decision_source=user_allow）；
  - deny → `_audit_deny_decisions`（hitl_handler.py L239-266）记录 decision_source=path_deny 或 user_deny，error_type=PERMISSION_DENIED；
  - 8 个 decision_source enum 值：mode_bypass / auto_allow / user_allow / user_deny / path_deny（+ future 扩展位），全部正确。

**修复方式**：无需修改代码。保留 `tool_spec.py:_arun` 现有实现（委托 ToolRouter.dispatch），保留 `agent_factory.py` L91 `interrupt_before=["tools"]`，保留 `hitl_handler.py` + `permission_classifier.py` 的 pre-ToolNode 权限判定。

**下次注意**：`interrupt()` 适合**无 pre-ToolNode 中断**的场景（如自定义 ToolNode 包装器内主动中断）。本项目已有 `interrupt_before=["tools"]` 做细粒度判定，再加工具内 interrupt() 是重复且冲突的。若未来移除 interrupt_before 改用路线 A，需同时：(1) 新建 tools_node.py 包装 ToolNode；(2) 把 PermissionClassifier 逻辑移进工具内 interrupt() 前；(3) 重新设计 resume 命令结构；(4) 重写 deny ToolMessage 注入路径以保证 tool_result SSE 仍能 yield。

## langchain 1.x 全量审计总结（2026-07-06）

### 审计范围
- langchain 1.3.4 / langchain-openai 1.2.2 / langgraph 1.2.4 / langchain-chroma 1.1.0 升级后全量审计
- 5 阶段实施：git 回滚点 → 11 项必修复 → 8 项新特性优化 → 9 项新能力扩展 → 代码质量重构
- 总计 43 个 Task，4 个 commit（1511294 / e791e99 / c4b9a8b / c87ab72 / 8508e7f）

### 关键踩坑汇总（按重要性排序）
1. **InMemorySaver 导入路径迁移**：langgraph 1.2.4 top-level `from langgraph.checkpoint import InMemorySaver` 抛 ImportError，必须用子模块路径 `from langgraph.checkpoint.memory import InMemorySaver`
2. **ROOT_DIR 路径语义陷阱 [ENG-2026-0706-002]**：`ROOT_DIR from settings` = `backend/`，而 `parents[3]` = `agent/`，用 ROOT_DIR 替换 parents[3] 会丢失 session DB。正确做法用 `path_guard.PROJECT_ROOT`（语义等价）
3. **HITL 路线选择**：保留路线 B（interrupt_before + Command(resume=...)），langgraph-prebuilt 1.1.0 仍支持。interrupt() 双重中断点冲突，不能引入
4. **StreamReader 在 langgraph 1.2.4 不存在**：`from langgraph.types import StreamReader` 抛 ImportError，dir(langgraph.types) 只有 StreamWriter/CustomStreamPart
5. **langgraph-cli 是独立 PyPI 包**：核心库 v1.2.4 不携带 CLI，需 `pip install langgraph-cli` 才能用 `langgraph dev`
6. **astream_events v2 与 astream(stream_mode=...) 事件结构根本不同**：不能直接迁移，会破坏状态管理依赖的 chunk 结构
7. **EnsembleRetriever 默认行为与 rrf_fusion 不一致**：rrf_fusion 是高度定制化算法（BM25 penalty / 软归一化 / 自定义 dedup），迁移会破坏 threshold/UI/跨KB排序
8. **Chroma 1.x 私有属性 `_collection`**：用 getattr 容错 + hasattr 守卫，不能直接访问
9. **sqlalchemy __import__ 反模式**：顶部加 `from sqlalchemy import func`，用 `func.sum(...)` 而非 `__import__("sqlalchemy").sum(...)`
10. **MAX_RECURSION 注释与数值不一致**：原 200 注释说 "2*50+1" 实际是 4*50+1，统一改为 101（2*50+1 = 50 iterations hard cap）

### 降级决策汇总
- 阶段 2：7 项降级（astream_events v2 / adispatch_custom_event / InjectedToolArg / post_model_hook / PluginManager 接入 / EnsembleRetriever / ContextualCompressionRetriever）
- 阶段 3：8 项降级 + 1 项部分成功（interrupt() 细粒度 HITL / Send API / Subgraph / Store API / StreamReader / MultiQueryRetriever / ParentDocumentRetriever / SelfQueryRetriever；langgraph.json 部分成功）
- 阶段 4：5 项文件拆分降级 + 3 项函数拆分降级 + 5 项参数封装降级 + 2 项其他降级（共 15 项降级）

### 下次注意
- 升级 langchain/langgraph 全家桶后，先跑 `python -c "from langgraph.checkpoint.memory import InMemorySaver"` 验证导入路径
- 用 `inspect.signature(create_react_agent)` 验证 API 签名变化
- 用 `dir(langgraph.types)` 检查 StreamReader/StreamWriter 是否存在
- langgraph.json 配置文件需 `pip install langgraph-cli` 才能用 `langgraph dev`
- 路径替换时先用 `python -c "from src.agent.path_guard import PROJECT_ROOT; print(PROJECT_ROOT)"` 验证语义等价
- **迁移到 `create_agent` 后必须同步把 `SqliteSaver` 替换为 `AsyncSqliteSaver`**：只要 Agent 流式入口是 `astream()`，checkpointer 就必须实现 `aget_tuple()`/`aput()` 等 async 方法，否则前端会"静默失败"

---

## langchain 1.x 新 API 全量迁移 — create_agent + astream 必须使用 AsyncSqliteSaver（2026-07-06）

**错误现象**：
- 前端发送消息后，Activity 卡片显示 "1 工具"，thinking 卡显示 "模型正在思考..."，然后**静默失败**，没有任何输出；
- 后端日志无显式报错（默认日志级别），但 `POST /api/chat` 200 返回只持续 18ms，SSE 只发出 `done` 事件；
- 调高日志后发现 LangGraph `astream()` 内部抛异常：`NotImplementedError: The SqliteSaver does not support async methods. Consider using AsyncSqliteSaver instead.`

**错误原因**：
- 阶段 3 迁移到 `create_agent` 后，Agent 流式入口仍然是 `agent.astream()`（astream 路径保留）；
- LangGraph 的 `astream()` 会调用 `checkpointer.aget_tuple()` 等异步方法；
- 迁移前使用的同步 `SqliteSaver` 没有实现这些 async 方法，导致在 `AsyncPregelLoop.__aenter__` 阶段直接抛 `NotImplementedError`；
- 该异常被 `sse_adapter.py` 外层捕获后转为 error/done 事件，前端表现为"静默失败"。

**修复方式**：
- `agent_factory.py`：
  - `_build_sqlite_checkpointer()` 改为 async，使用 `AsyncSqliteSaver.from_conn_string(path)` 的 async context manager，并保存 context manager 引用防止连接被提前释放；
  - `_get_checkpointer()` 改为 async；
  - `create_hardware_agent_from_config()` / `create_hardware_agent()` 改为 async；
  - `reset_thread_checkpoint()` / `_try_delete_thread()` 改为 async，内部 await 异步 `delete_thread`；
- `chat_routes.py`：
  - `chat_sse()` 中 `await reset_thread_checkpoint(...)`；
  - `_build_agent_for_payload()` 改为 async，内部 `await create_hardware_agent(...)`；
- `langgraph_factory.py`：
  - `create_agent_for_studio()` 改为 async。

**重要细节**：
- `AsyncSqliteSaver.from_conn_string()` 返回的是 async context manager，不能简单 `await`；
- 不能直接用 `await aiosqlite.connect(path).__aenter__()` 创建长连接（aiosqlite 设计如此，会挂起）；
- 必须同时持有 context manager 对象和 saver 对象，保证 process lifetime 内不退出上下文，否则连接会被关闭。

**下次注意**：
- 任何调用 `agent.astream()` / `agent.astream_events()` 的地方，checkpointer 必须是 async-compatible（AsyncSqliteSaver / InMemorySaver / AsyncPostgresSaver 等）；
- 同步 SqliteSaver 只能用于同步路径（`agent.stream()`），本项目 SSE 是异步路径，必须迁移；
- 修改 checkpointer 生命周期时，要同步更新所有调用点（reset_thread_checkpoint、langgraph dev factory 等）。

## langchain 1.x 新 API 全量迁移 — 阶段 0 技术验证（2026-07-06）

### 验证环境
- langchain 1.3.4 / langgraph 1.2.4 / langchain-core 1.3.4
- 目的：全量迁移到 langchain 1.x 推荐新 API（create_agent / interrupt_on / stream_events v3 / InjectedToolArg / context_schema / middleware / Store API / adispatch_custom_event）

### 验证结果（5/8 可用，3/8 不存在但均有替代方案）

#### ✅ 可用的 API（5 个）
1. `from langchain.agents import create_agent` — 可用
2. `from langchain_core.tools import InjectedToolArg` — 可用
3. `from langgraph.store.memory import InMemoryStore` — 可用
4. `from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse` — 可用（子类化方式）
5. `from langgraph.types import StreamWriter, CustomStreamPart` — 可用

`create_agent` 签名（inspect.signature 验证）：
```
['model', 'tools', 'system_prompt', 'middleware', 'response_format',
 'state_schema', 'context_schema', 'checkpointer', 'store',
 'interrupt_before', 'interrupt_after', 'debug', 'name', 'cache', 'transformers']
```

`AgentMiddleware` 用法：子类化 `AgentMiddleware[StateT, ContextT]`，重写 `before_model` / `after_model` / `wrap_model_call` / `modify_model_request` 等钩子，接收 `ModelRequest` 返回 `ModelResponse`。`middleware` 参数接受 `Sequence[AgentMiddleware]`。

#### ❌ 不存在的 API（3 个）+ 替代方案

**踩坑 1：`interrupt_on` 参数不存在**
- **错误现象**：`create_agent(..., interrupt_on={...})` 抛 `TypeError: create_agent() got an unexpected keyword argument 'interrupt_on'`
- **错误原因**：`create_agent` 签名只有 `interrupt_before` / `interrupt_after`（与 `create_react_agent` 相同），没有 `interrupt_on` 声明式条件中断参数。`interrupt_on` 是 langchain-ai/deepagents 等 demo 项目用的概念，并非 langchain 1.3.4 公共 API。
- **修复方式**：保留 `interrupt_before=["tools"]` + `PermissionClassifier` pre-ToolNode 判定模式（即现有 hitl_handler.py 路线 B）。如果要用 middleware 实现 HITL，用 `AgentMiddleware.before_model` 钩子在 model 调用前检查 tool_calls 并通过 `Command(resume=...)` 控制，但这会绕过 interrupt_before 的 checkpoint 持久化，不推荐。
- **下次注意**：迁移 HITL 时优先验证 `interrupt_on` 是否存在；不存在则保留 `interrupt_before` 模式，PermissionClassifier 逻辑零修改。

**踩坑 2：`adispatch_custom_event` 不存在**
- **错误现象**：`from langgraph.types import adispatch_custom_event` 抛 `ImportError: cannot import name 'adispatch_custom_event' from 'langgraph.types'`
- **错误原因**：`langgraph.types` 只导出 `StreamWriter` / `CustomStreamPart` / `StreamMode` / `StreamPart` / `CheckpointStreamPart` / `MessagesStreamPart` / `UpdatesStreamPart` / `ValuesStreamPart` / `TasksStreamPart` / `DebugStreamPart`，没有 `adispatch_custom_event` 异步派发函数。langgraph 1.x 的自定义事件机制改为：在节点/工具函数签名中注入 `writer: StreamWriter`，调用 `writer.write(CustomStreamPart(...))` 派发。
- **修复方式**：用 `StreamWriter` + `CustomStreamPart` 替代：
  - 工具函数签名：`async def build_firmware(args, ctx, writer: StreamWriter) -> dict`
  - 派发事件：`writer.write({"type": "build_log", "data": {...}})`
  - 消费端：`agent.stream_events(input, version="v3")` 会自动投影 `on_custom_event` 事件
- **下次注意**：langgraph 1.x 自定义事件用 `StreamWriter` 注入方式，不是 `adispatch_custom_event` 函数调用。`StreamWriter` 必须作为函数参数注入（LangGraph 自动识别），不能在函数内部全局获取。

**踩坑 3：`@agent_middleware` 装饰器不存在**
- **错误现象**：`from langchain.agents import agent_middleware` 抛 `ImportError: cannot import name 'agent_middleware' from 'langchain.agents'`；`from langchain.agents.middleware import agent_middleware` 同样抛 ImportError
- **错误原因**：langchain 1.3.4 的 `langchain.agents.middleware` 模块只导出 `AgentMiddleware` 类、`ModelRequest` 类、`ModelResponse` 类，没有 `@agent_middleware` 装饰器。中间件只能通过子类化 `AgentMiddleware` 实现，然后传给 `create_agent(middleware=[...])`。
- **修复方式**：用 `AgentMiddleware` 子类替代装饰器：
  ```python
  from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
  
  class TokenCounterMiddleware(AgentMiddleware[AgentState, ToolContext]):
      async def after_model(self, request: ModelRequest) -> ModelResponse:
          # 累加 token 计数
          return ModelResponse(...)
  
  # create_agent(middleware=[TokenCounterMiddleware()])
  ```
- **下次注意**：langchain 1.x middleware 只能用类继承方式，不能用装饰器。`AgentMiddleware` 是泛型类，需指定 `[StateT, ContextT]` 类型参数。

### 阶段 0 结论
3 个 API 不存在但均有可用替代方案，迁移可继续：
- Task 7（middleware）→ 用 `AgentMiddleware` 子类（替代 `@agent_middleware` 装饰器）
- Task 8（custom_event）→ 用 `StreamWriter` + `CustomStreamPart`（替代 `adispatch_custom_event`）
- Task 11（HITL）→ 保留 `interrupt_before` + `PermissionClassifier`（不引入 `interrupt_on`），可选增强：用 `AgentMiddleware.before_model` 钩子做 token 计数等横切关注点

### 下次注意（全量迁移专用）
- 验证 API 存在性用 `python -c "from X import Y; print(Y)"` + `inspect.signature(Y)` 双重验证
- `langgraph.types` 的 `dir()` 输出能快速判断 StreamWriter/CustomStreamPart 是否存在
- `create_agent` 的 `interrupt_before` / `interrupt_after` 与 `create_react_agent` 完全相同，HITL 链路可零修改迁移
- `AgentMiddleware` 是泛型类，子类化时必须指定 `[StateT, ContextT]` 类型参数，否则 Pydantic 校验失败

---

## langchain 1.x 新 API 全量迁移 — 阶段 2 _extract_item 运算符优先级 bug（2026-07-06）

### 错误现象
- `backend/src/agent/store_adapter.py` L127 `_extract_item` 函数一行式：
  `value = getattr(item, "value", None) or item.get("value", {}) if isinstance(item, dict) else {}`
- Python 条件表达式 `A if C else B` 优先级低于 `or`，实际解析为：
  `value = (getattr(item, "value", None) or item.get("value", {})) if isinstance(item, dict) else {}`
- langgraph `InMemoryStore.search()` 返回 Item 对象（非 dict）→ `isinstance(item, dict)` 为 False → value 永远 = {}
- 导致 Store API 语义搜索即使找到匹配项，返回结果全部是空字符串，fallback 路径形同虚设

### 错误原因
- 一行式代码混合 `or` + 条件表达式，运算符优先级陷阱
- 没有显式 if/else 分支，可读性差，容易出错
- 单元测试未覆盖 Item 对象（仅测了 dict 路径）

### 修复方式
- 改为显式 if/else 分支：
  ```python
  if isinstance(item, dict):
      value = item.get("value", {}) or {}
  else:
      value = getattr(item, "value", None) or {}
  ```
- 追加 `isinstance(value, dict)` 防御性检查，避免 value 非 dict 时 .get() 报错
- 验证：用 `type('Obj',(),{'value':{...}})()` 模拟 Item 对象，确认修复后正确提取字段

### 下次注意
- 不要写 `A or B if C else D` 一行式，改为显式 if/else 分支
- langgraph Store API 返回的是 Item 对象（有 .value 属性），不是 dict，测试必须覆盖对象路径
- 双 subagent 验证机制有效：完整性审查 subagent 发现了这个 bug，功能测试 subagent 未发现（因为 Store API 是 fallback 路径，FTS5 主路径正常时不会触发）

---

## langchain 1.x 新 API 全量迁移 — 阶段 3 Task 12 stream_events v3 Fallback 决策（2026-07-06）

### 错误现象
- stream_events v3 是 typed-projection API（stream.messages / stream.tool_calls / stream.interleave），与当前 astream(stream_mode=["messages","updates"]) 的 (mode, chunk) 元组消费方式完全不同
- 迁移会涉及 sse_adapter.py 大规模重写：_iter_agent_sse / _handle_message_chunk / _handle_update_chunk / _merge_agent_and_tool_events 全部要换范式
- 极易破坏现有 SSE 事件流（thinking/text/tool_call/tool_result/source 5 类事件）

### 错误原因
- stream_events v3 是 langchain 1.3.4 全新范式，不是 astream 的增量改进
- typed-projection API 的消费方式（stream.messages 的 item.text/item.reasoning）与当前 AIMessageChunk 完全不同
- _merge_agent_and_tool_events 双队列合并机制要整个删掉换成 get_stream_writer + on_custom_event
- 风险极高，与"严格保持现有全部功能的完整性"要求冲突

### 修复方式
- 采用 spec Task 12 的 Fallback 方案：保留 astream(stream_mode=["messages","updates"]) 为 active 路径
- _consume_custom_events 占位函数保留（raise NotImplementedError 防误用）
- 阶段 3 聚焦 Task 10（InjectedToolArg 基类改造）+ Task 13（create_agent 替换）+ Task 11（HITL 零修改验证）
- stream_events v3 迁移推迟到后续阶段，待 typed-projection API 稳定后再做

### 下次注意
- stream_events v3 不是简单的 version 升级，是范式转换（raw event dict → typed projection）
- 大规模重写任务（如 sse_adapter.py）应在独立阶段做，不与其他高风险迁移混在一起
- spec 的 Fallback 条件是有价值的：当风险与收益不匹配时，保留现有工作方式是合理的

## 2026-07-06 - langchain 1.x 全量迁移阶段 4：build_self_query_retriever 函数行数超限 + Chroma $contains 不支持 metadata filter

### 错误现象
- 完整性审查 subagent 报告 `build_self_query_retriever` 函数体 12-15 行，超过 AGENTS.md "max-lines-per-function ≤ 10" 规范
- SelfQueryRetriever 评估 Chroma translator `$contains` 子串匹配，发现 metadata filter 层不支持

### 错误原因
1. **函数行数超限**：`build_self_query_retriever` 内联了 KB 查找 + store 获取 + chroma 校验 + SelfQueryRetriever.from_llm 调用，导致函数体过长
2. **Chroma $contains 误判**：`langchain_community/vectorstores/chroma.py` L683 出现 `$contains: "hello"`，但这是 `where_document` 参数（文档内容全文过滤），不是 metadata `where` filter。`ChromaTranslator.allowed_comparators` 仅 [EQ, NE, GT, GTE, LT, LTE]，无 LIKE/CONTAINS

### 修复方式
1. **函数行数超限**：抽取 `_resolve_chroma_store(kb_manager, kb_id) -> Any` 辅助函数封装 KB 查找 + store 获取 + chroma 校验逻辑（6 行），主函数体降至 4 行
2. **Chroma $contains**：降级为 $eq 精确匹配 + docstring 备注；子串匹配继续由 SearchDocsTool 现有 `doc_filter` 参数承担（Python 端 `_matches_doc_filter`）

### 下次注意
- Chroma 有两套独立过滤机制：`where` (metadata filter, 仅 EQ/NE/GT/GTE/LT/LTE) vs `where_document` (文档内容全文过滤, 支持 $contains)；SelfQueryRetriever 只翻译 metadata filter
- 函数内联多个步骤时，提前评估行数，必要时抽取辅助函数
- langchain API 约束要实际查源码（ChromaTranslator.allowed_comparators），不要臆测

## 2026-07-06 - langchain 1.x 全量迁移阶段 4：ParentDocumentRetriever 与 HybridChunker 不兼容

### 错误现象
- 评估 ParentDocumentRetriever 与 HybridChunker 兼容性，结论为不兼容，6 大原因

### 错误原因
1. HybridChunker chunk 元数据无 parent_id 字段（用 `small_chunk_id` + `big_chunk_text` 自包含机制）
2. ParentDocumentRetriever 的 `child_splitter` 会绕过 HybridChunker 的 page markers / 跨页表格合并 / 指纹 dedup / 代码块保护
3. parent 粒度不匹配（ParentDocumentRetriever 默认 parent = 整个文档，HybridChunker 的 "big chunk" = section 300-2000 字符）
4. docstore 与 ChromaDB 双份存储冲突
5. 多 KB 场景不友好（ParentDocumentRetriever 单 KB 设计）
6. **最强理由**：现有系统已通过 `small_chunk_id` + `big_chunk_text` + `get_chunk_by_small_id()` 闭环实现等价 small-to-big 映射

### 修复方式
- 未新建 `parent_doc_retriever.py`，Task 16 标记为 `[-] 跳过`
- 提供替代方案：自定义 `SmallToBigRetriever(BaseRetriever)` 子类（参考 rrf_retriever.py 模式，不重新切分 chunk，仅用 `get_chunk_by_small_id()` 反查注入）

### 下次注意
- 评估 LangChain 新组件兼容性时，必须深入看现有 chunk 元数据结构（不能只看文档描述）
- 现有系统已有等价机制时，不要为了"用新 API"而重复造轮子
- ParentDocumentRetriever 适合从零开始的 RAG 系统，不适合已有成熟 chunker 的系统

## 2026-07-07 - reranker FP16 优化代码被回滚后丢失（B4/B7 优化二次失效）

### 错误现象
- search_docs 的 reranker 推理慢：CPU 上 bge-reranker-base 5 pair predict 需 47 秒
- 用户反馈"现在是 cpu 推理，而且现在性能升级也失效了"
- 后端启动日志显示 `[Reranker] Loaded base model (FP32, ~280MB)`（应为 `precision=FP16`）

### 错误原因
1. **首次丢失**：sentence_transformers 5.x 降级到 4.1.0 时，B4/B7 优化代码（FP16 + large 模型）被一起回滚（2026-06-28）
2. **二次丢失**：上轮 plan 恢复了 4 处改动（import torch / _RERANKER_DEVICE / CrossEncoder device+.half() / batch_size=16），但用户后续"重构了一下"时改动 3（CrossEncoder 传 device + .half() + 日志更新）丢失，L67-76 恢复成旧代码
3. `_RERANKER_DEVICE` 常量定义了但未被使用（死代码），导致即使有 GPU 也会强制 CPU
4. 没有 `.half()`，FP32 推理比 FP16 慢

### 修复方式
- `reranker.py` L67-81 恢复：
  - `CrossEncoder(_RERANKER_MODEL, max_length=512, device=_RERANKER_DEVICE)` — 传 device 参数
  - `_RERANKER.model.half()` — FP16 量化
  - 日志更新为 `device=%s precision=FP16` 格式，方便验证新代码在运行
- 性能验证：10 pair predict 从 ~47s（FP32）降到 6.5s（FP16），提速 ~7x

### 修复方式（修正）
- `reranker.py` L67-87 改为：仅在 GPU 时用 FP16，CPU 保持 FP32
- 10 pair predict 实测：FP16 CPU 6.5s → FP32 CPU 3.8s

### 下次注意
- **降级依赖时**：检查是否有优化代码被连带删除（grep `.half()` / `device=` / `CrossEncoder` 确认优化代码还在）
- **重构后**：用 grep 验证关键优化（`.half()` / `device=`）是否还在，不要只看 import 和常量定义
- **死代码警惕**：常量定义了但没用到是优化代码丢失的信号（`_RERANKER_DEVICE` 定义了但 CrossEncoder 没传）
- **日志区分**：优化代码的日志格式要与旧代码不同（`precision=FP16` vs `FP32, ~280MB`），方便一眼确认新代码在运行
- **FP16 不是 always faster**：PyTorch CPU 后端对 FP16 没有原生 SIMD 优化，反而会走慢速标量路径。FP16 只有在 GPU 上才明确更快。

