# 08-infra 扫描报告

> 扫描时间：2026-06-24
> 扫描方法：Pass 1（广度）+ Pass 2（深度），两轮完成
> 扫描规则：只记录问题，不改代码

---

## Pass 1：广度扫描

### [P1] Vite 代理端口与后端实际端口不一致

- 位置：frontend/vite.config.ts:13
- 现象：proxy target 指向 58080，但 settings.py 默认端口为 8000，dev.ps1 也用 8000
- 影响评估：前端 /api 代理请求全部指向空端口，SSE 流式聊天、模型列表、知识库上传全部走不通
- 建议修复方式：统一端口，修改 vite.config.ts target 为 8000

### [P1] backend/main.py CLI 模式零日志

- 位置：backend/main.py
- 现象：整个文件用 print() 而非 logging，无 configure_logging()
- 建议修复方式：添加 logging.basicConfig，替换 print 为 logger.info

### [P2] 4 个 API 路由文件零日志

- 位置：feedback_routes.py / mcp_routes.py / sandbox_routes.py / search_routes.py
- 建议修复方式：加 import logging + logger，except 路径加 logger.warning/error

### [P2] .gitignore 未覆盖根目录修复脚本

- 位置：.gitignore
- 现象：根目录残留 fix_*.py / _fix*.js / _rewrite*.js / patch_*.py / log_check.py 等十几个文件
- 建议修复方式：追加 fix_*.py / _fix* / _rewrite* / _write_* / log_check.py / patch_*.py

### [P2] workflow-trae-codex.md 与 AGENTS.md 角色模糊

- 位置：docs/workflow-trae-codex.md §1.2
- 现象：workflow 说 Codex 不下场写功能代码（01-app~08-infra），但 08-infra 算不算功能代码未定义
- 建议修复方式：明确 08-infra 的归属

### [P2] requirements.txt 依赖版本过旧

- 位置：backend/requirements.txt
- 现象：chromadb==0.5.0，langchain==0.3.0，全部精确锁定无范围
- 建议修复方式：逐个验证后升级或用 >=

## Pass 2：深度扫描

### [P1] 从零安装到启动步骤不明确

- 位置：整体项目（无 README.md）
- 现象：clone 后需要用户自己摸索 venv / pip / npm / 两个入口 / 端口不一致
- 建议修复方式：创建 README.md 写清 5 步，或完善 dev.ps1 包含首次安装

### [P1] database.py 初始化失败无日志

- 位置：backend/app/db/database.py init_db()
- 现象：无 try/except，无日志。数据库不可用时静默失败
- 建议修复方式：加 try/except + logger.error

### [P2] 后端启动时端口被占用无友好提示

- 位置：backend/app/main.py main()
- 现象：uvicorn.run 抛 OSError，用户看到原始 Python traceback
- 建议修复方式：加 try/except 输出友好提示

### [P2] CORS 配置空列表时无警告

- 位置：backend/app/main.py:150-157
- 现象：生产环境 cors_origins 为空时所有跨域请求被拒绝，后端无日志
- 建议修复方式：空列表时记录 logger.warning

### [P2] dev.ps1 无首次安装流程

- 位置：scripts/dev.ps1
- 现象：假设依赖已安装，新机器直接运行会失败
- 建议修复方式：加入 pip install + npm install

---

## 关键发现汇总

| 严重度 | 数量 | 典型问题 |
|--------|------|---------|
| P1 | 4 | 端口不一致、CLI 零日志、无 README、DB 初始化静默失败 |
| P2 | 7 | 4 路由文件零日志、.gitignore 遗漏、依赖过旧、CORS 无警告、dev.ps1 无安装、角色模糊 |

---

*08-infra 扫描完成*