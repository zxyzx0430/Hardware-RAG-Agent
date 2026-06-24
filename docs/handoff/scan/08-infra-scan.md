## 任务：全面代码扫描（两轮，不修）

目标线程：08-infra（工程基础 / 构建 / 部署）

范围文件：
- backend/main.py
- backend/app/main.py
- backend/app/api/__init__.py
- frontend/vite.config.ts
- backend/requirements.txt
- frontend/package.json
- .gitignore
- scripts/dev.ps1
- backend/app/db/database.py
- AGENTS.md
- docs/workflow-trae-codex.md
- docs/plans/roadmap.md

方法：做两轮扫描，每轮把结果追加到 `docs/review/08-infra-scan.md`。
不要改代码、不要修 bug、不要动文件。只记录问题。

### Pass 1（广度扫描）

花 15-20 分钟通读全部范围文件，找以下问题：

1. 构建完整性
   - 前端 build 是否通过
   - requirements.txt 依赖是否完备且不过期
   - Vite 代理配置是否对齐后端端口

2. 配置问题
   - .gitignore 是否覆盖所有不应提交的文件
   - 环境变量 .env 模板是否完整
   - CORS 配置是否对齐开发环境

3. 代码异味
   - 重复的配置项
   - main.py 启动逻辑是否清晰
   - 日志级别配置是否正确

4. 文档完整性
   - AGENTS.md 和实际情况一致吗
   - README 是否已创建
   - api-contract.md 是否覆盖所有接口

### Pass 2（深度扫描）

基于 Pass 1 的结果，挑 2-3 个最可疑的路径做深度检查：

1. 部署准备
   - 从零安装到启动需要几步
   - 缺失的依赖是否会在运行时才炸
   - Python 版本兼容性

2. 异常处理
   - 后端启动时端口被占用的提示
   - 前端代理到后端时后端未启动的提示
   - 数据库迁移/初始化失败

3. 安全基线
   - .env 中的敏感信息是否通过 gitignore 保护
   - CORS 是否限制为开发环境而非 *

### 输出格式

每个问题按这个格式写：

```
## [P0/P1/P2] 简短标题

- 位置：文件路径:行号
- 现象：
- 影响评估（出故障时用户看到什么）：
- 建议修复方式（一句话）：
```

### 完成后

两轮都做完后，通知 00-control：「08-infra 扫描完成，结果在 docs/review/08-infra-scan.md」

注意：不要修，只记录。
