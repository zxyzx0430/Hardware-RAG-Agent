# 交办单 250621-fix-auth-02chat

## 归属
- 线程：02-chat
- 优先级：P0（阻断核心功能）

## 问题
前端 api/client.ts 的 getAuthHeaders() 在安全改造时去掉了 X-API-Key 请求头，后端收不到 API Key 导致验证失败。

## 修法
frontend/src/api/client.ts 的 getAuthHeaders() 函数：
1. 从 useSettingsStore 解构 providerKeys
2. 读取 providerKeys[activeProvider]
3. 加到 headers["X-API-Key"]

## 验证
打开设置页填 Key + URL，点验证 -> 绿色。返回聊天发消息 -> 正常流式返回。

## 开工前必读
- AGENTS.md
- docs/completed.md
- docs/api-contract.md

## 修完后更新
docs/completed.md 对应线程状态
通知 00-control 验收
