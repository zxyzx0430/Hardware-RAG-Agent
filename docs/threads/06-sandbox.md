# 06-sandbox 线程上下文

## 负责范围
- 做什么：运行隔离、权限控制、危险操作审计、工具调用安全策略
- 不做什么：不直接实现聊天业务、不处理 RAG 检索、不实现硬件烧录逻辑

## 当前状态
- 已完成：sandbox_routes.py 骨架已创建（POST /api/sandbox/execute + GET /api/sandbox/status）
- 已完成：sandbox_router 已在 main.py 中注册
- 已完成：AGENTS.md 定义了工具调用规则、文件编辑规则、命令风险等级
- 正在做：核心 src/sandbox/executor.py 尚未实现（sandbox_routes 引用不存在的函数）
- 阻塞：src/sandbox/__init__.py 为空，未导出 execute_code / check_docker_available
- 阻塞：Docker 执行环境尚未配置
- 阻塞：API 契约文档中无 sandbox 章节
- 阻塞：前端无 sandbox 组件/Store/端点

## 接口契约
- 涉及 docs/api-contract.md：需要新增 "6. 沙箱执行" 章节
- POST /api/sandbox/execute — 代码执行（状态：draft）
- GET /api/sandbox/status — Docker 可用性检查（状态：draft）
- 风险等级：低/中/高三档
- 高危操作审计记录格式待定

## 关键文件
- 前端：需新建 SandboxPanel / CodePlayground 组件（暂无）
- 前端端点：endpoints.ts 需添加 sandbox 入口
- 后端：backend/app/api/sandbox_routes.py（骨架已存在）
- 核心：src/sandbox/executor.py（不存在，需创建）
- 核心：src/sandbox/__init__.py（为空，需补导出）
- API 契约：docs/api-contract.md（需补充 sandbox 章节）
- 配置：AGENTS.md（命令风险等级、文件操作规则）
- 文档：docs/pitfalls.md（记录踩坑）
- 计划：docs/plans/*（V1/V2/V3 路线图）

## 决策记录
- 无

## 踩坑记录
- 关联 docs/pitfalls.md：
  1. `src/sandbox/__init__.py` 为空导致 `from src.sandbox import execute_code, check_docker_available` 运行时失败
  2. sandbox_routes.py 导入的模块（src.sandbox）在 backend 目录下无法直接导入，需 sys.path 处理
  3. api-contract.md 中完全没有 sandbox 相关章节，接口无契约保障

## 下次开工先看
1. 实现 src/sandbox/executor.py — 核心沙箱执行逻辑
2. 补全 src/sandbox/__init__.py — 导出 execute_code / check_docker_available
3. 更新 docs/api-contract.md — 新增 sandbox 章节（状态 draft）
4. 创建前端 sandbox Store 和组件
5. 更新 endpoints.ts 添加 sandbox 端点
