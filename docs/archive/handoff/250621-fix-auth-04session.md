# 交办单 250621-fix-auth-04session

## 归属
- 线程：04-session
- 优先级：P0（阻断核心功能）

## 问题
/api/auth/store-key 端点不响应，前端存 API Key 时调了这个接口但后端可能没注册。

## 修法
1. 确认 backend/app/main.py 里 app.include_router(auth_router) 已注册
2. 确认路由前缀 /api/auth 挂载正确
3. 测试 POST /api/auth/store-key 返回 200

## 验证
curl -X POST http://127.0.0.1:58080/api/auth/store-key -H "Content-Type: application/json" -d '{\"provider\":\"openai\",\"api_key\":\"test\"}' -> 返回 JSON

## 开工前必读
- AGENTS.md
- docs/completed.md
- docs/api-contract.md

## 修完后更新
docs/completed.md 对应线程状态
通知 00-control 验收
