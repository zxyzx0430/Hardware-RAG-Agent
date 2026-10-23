# Agent Skills 原生支持

## 2026-10-24 最终实现与选定软件验证状态

原生 Skills 子任务的实现与接线，以及本次选定的软件自动化验证已完成；这不代表整个项目或完整后端验收完成。

- 隔离验收 RunName：`native-verification-20261024-030026-caa451a2daac444892dfff848dbcee6e`。
- 13 个指定后端测试路径：186 passed、26 warnings；这不是后端全量。
- 完整 Vitest：24 files、159 passed；`tsc -b` exit 0；Vite build exit 0，带 chunk-size advisory。
- 外置隔离验收产物保存在本机版本库外的验收目录，未纳入提交。
- 未验证真实 GitHub、真实模型、浏览器、真实用户库/Skills 数据、完整 HTTP/SSE/resume E2E，以及第三方 Codex/Claude/Pi/OpenCode 的实际发现或运行。提交与发布状态以 Git 记录为准。

## 本轮范围（2026-10-23）

以本轮用户明确要求为准，直接修改当前仓库，不复制旧候选工作树。Skills 是现有 Agent 的按需指令包，不是独立管理平台。旧文档中的“仅私有目录、导入默认停用、Skills 全局只读、禁止使用普通工具执行脚本”不再作为本轮实现约束；已有权限、确认、禁用工具、MCP 授权及路径边界继续有效。

标准依据：[Agent Skills specification](https://agentskills.io/specification)。Claude 目录适配依据：[Claude Code Skills](https://code.claude.com/docs/en/skills)。遵循标准包不代表已在各第三方 Agent 中完成运行验证。

## 分工与顺序

00 只负责接口、源码审查、正式文档和最后验收，不写产品代码。所有实施子任务显式指定 GPT-6 Luna / max，独立上下文，不派生后代。

1. 存储实施：`format.py`、`repository.py`、`github_import.py`、`skill_routes.py`。项目 `.agents/skills` / 用户 `~/.agents/skills`，旧私有目录兼容读取；新包保持原始内容，宿主管理信息放包外，显式可选 Claude 安装适配。
2. Agent 实施：`skill_runtime.py`、`tool_router.py`、`agent_factory.py`、`chat_routes.py` 和 Skills 提示词。预览、固定版本安装、当前请求新增快照及按需读取；解除 Skills 对普通工具/MCP 的额外过滤，保留原权限链。
3. 界面实施：现有 Skills store、设置组件和模式选择组件。解释安装范围、Claude 适配、导入后可用及正常权限，不新增独立平台。
4. 所有实现与接线完成后，00 审查交接，才进入集中验证。实现期间不运行测试、构建、服务或模型回放，不提交代码。

## 共享接口约定

- `SkillRepository(root=None, *, scope='project', staging_root=None)`：无显式 root 时发现项目、用户和旧私有目录；显式 root 保持隔离。项目同名优先，不覆盖较低优先级来源。
- HTTP GitHub 安装请求在原 `source`、`paths` 上增加可选 `scope: project | user`（默认 project）、`claude_adapter: boolean`（默认 false）。来源、版本固定和包限制保持。
- 导入成功的有效包立即启用；无效包不能加载。新增包快照只能追加，不得替换请求中已有 ID 的固定内容哈希。
- 初始自动目录只含标识、名称、描述和固定哈希；调用加载工具后读取完整 `SKILL.md`（含 frontmatter），再按需读取资源。安装不执行脚本，执行使用既有普通工具和权限链。
- `allowed-tools` 仅保留为实验性元数据，不授予权限。

## 原验证计划（历史检查清单）

以下条目记录本轮实现前的验证关注点，不表示每项都由最终选定门禁单独验收；实际运行范围仅以上方最终状态为准。

标准/旧格式、名称与目录匹配、原始字节保留、管理信息包外、范围与冲突、旧数据不迁移、受管理 Claude 适配与重复发现、导入默认可用、当前请求追加快照、旧哈希不可替换、资源路径/符号链接/大小限制、普通工具/MCP 不被 Skills 屏蔽及原确认继续生效、安装不执行脚本、前端请求和提示一致。

本页记录实施范围及选定软件自动化验证结果，不代表完整后端、真实环境或整个项目全面验收。

## 当前交接

- 前端实施已交回：现有管理页/选择器/store 与对应测试源完成。项目/用户范围及 Claude 适配字段已接入，没有新建独立平台；最终 Vitest 结果见本页顶部。
- Agent 实施已交回：四个请求级 Skills 工具、scope 安装、追加快照、off 下显式新安装后加载、正常工具/MCP/确认接线已写入。合成用例覆盖假导入器及真实 `GitHubSkillImporter` + 固定假传输经 ToolNode 的组合；不代表实网/真实模型验证。
- 存储实施已交回并停写：标准包、包外状态、兼容目录、Claude 适配生命周期与 ZIP 导出已接入。直接放置的有效共享标准包默认可用；安装竞争仅清理本次确实发布且未被修改的内容；有效标准 frontmatter 不降级修复。受管理 Claude 副本暂不支持原地编辑，明确返回需重新同步，不静默修改用户副本。
- 00 已核本轮开始前八个相关 dirty 文件的内容哈希仍一致；这是未覆盖原改动的保护核对，不是测试。
- 三项实现交接及审查修正完成后，00 已完成隔离软件验收；实际 RunName、通过数、warnings、产物位置及未验证边界见本页“2026-10-24 最终实现与选定软件验证状态”。
