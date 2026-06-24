# 08-infra TODO

## 功能：README.md（等待 P0 完成后推进）

- [ ] 项目简介，快速开始，配置说明，项目结构，技术栈，MIT License
- [ ] 自验：照着 README 在空机器上跑一遍

## 功能：Docker 容器化（等待 P0 完成后推进）

- [ ] 后端 Dockerfile（Python 轻量镜像）
- [ ] docker-compose.yml（后端 + ChromaDB + 前端 build 产物）
- [ ] volume 挂载 data/ 目录

## 功能：CI 与代码质量（等待 P0 完成后推进）

- [ ] GitHub Actions: frontend typecheck + build
- [ ] GitHub Actions: backend pytest + ruff + mypy
- [ ] CONTRIBUTING.md + issue templates

## 功能：可观测性增强

- [ ] 后端日志：RAG 检索耗时、LLM token 用量、工具调用轨迹（结构化日志）
- [ ] 审计日志接口与 api-contract.md §5.23 对齐

## 技术债务

- [x] 后端日志基础设施：basicConfig + request-id 中间件 + LOG_LEVEL 配置
- [x] 全链路日志补全：9 个后端模块 + 3 个前端模块
- [x] scripts/dev.ps1：一键启动脚本，测试通过
- [x] 修复 main.py 日志输出到 stdout 而非 stderr
- [x] 知识点：chromadb 导入慢（~10s）；npx 需 cmd /c 包装；Start-Process 不能双重定向到同一文件

---

**规则**：[ ] 待做 → [x] 完成(写说明) → [-] 跳过(写理由) → [?] 需确认 → 新任务加到最前面
**完成**: 全部 [x] 后通知 00-control 审查
