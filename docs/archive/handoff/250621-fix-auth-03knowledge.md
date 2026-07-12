# 交办单 250621-fix-auth-03knowledge

## 归属
- 线程：03-knowledge
- 优先级：P0（阻断核心功能）

## 问题
backend/app/api/routes.py 的 list_models 函数第 343 行写死了 get_provider_key("openai")，用户用其他供应商（DeepSeek、Ollama 等）验证时查不到加密存储的 Key。

## 修法
backend/app/api/routes.py：
1. 读取 X-Provider 请求头
2. 用动态 provider 代替硬编码的 "openai"
3. ModelsRequest 补上 provider 可选字段

## 验证
切换不同供应商点验证 -> 全部通过。

## 开工前必读
- AGENTS.md
- docs/completed.md
- docs/api-contract.md

## 修完后更新
docs/completed.md 对应线程状态
通知 00-control 验收
