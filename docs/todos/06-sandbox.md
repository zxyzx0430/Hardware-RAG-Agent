# 06-sandbox TODO

> 新任务加到最前面（倒序排列），每次只读前 2 项。
> 每项完成时在 [x] 后面写完成说明。

- [ ] 验证后端启动：确认 sandbox 导入不报错（check_docker_available 依赖 docker-py 是否安装）
- [ ] 预拉取 Docker 镜像：python:3.11-slim / gcc:latest / node:20-slim / platformio/platformio-core:latest
- [ ] 创建前端 SandboxPanel 组件 + sandbox Store（api-contract 5.21-5.23）
- [ ] 更新 frontend/src/api/endpoints.ts 添加 sandbox 端点常量
- [ ] 编写 sandbox 测试（pytest: test_executor.py + test_sandbox_routes.py）
- [ ] 补全 requirements.txt：添加 docker（docker-py）
- [ ] 验证镜像拉取后的端到端执行流程

---

**已完成（2026-06-21 前）**

- [x] 核心执行器 executor.py — Docker SDK for Python，C/C++ stdin 传入，asyncio.to_thread 防阻塞
- [x] 模块导出 __init__.py — execute_code / check_docker_available / ExecutionResult
- [x] API 路由 sandbox_routes.py — execute + status，语言白名单 + 并发信号量 + 超时保护
- [x] 路由注册 main.py — sandbox_router 已 include
- [x] API 契约 api-contract.md — 5.21 execute / 5.22 status / 5.23 audit（draft）
- [x] 安全生产 P1/P2 — os.chmod Windows 兼容 / 全局异常处理

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**：全部 [x] 后通知 00-control 审查

