# 07-hardware — 硬件工作台线程上下文

## 负责范围

### 做什么
- 串口扫描与设备列表 (GET /api/devices)
- 串口监视器 WebSocket 实时数据流 (WS /api/monitor/{port}?baud=)
- 固件编译触发 (POST /api/build SSE)
- 固件烧录触发 (POST /api/upload SSE)
- 接线图生成 SVG (POST /api/wiring)
- 引脚冲突审计 (POST /api/audit_pins)
- 代码诊断静态扫描 (POST /api/diagnose)
- 硬件工作台 WorkbenchPanel 全部 5 个 Tab (Serial/Flash/Preview/Wiring/Safety)
- 前后端联调、无硬件时 mock 可演示
- 依赖：pyserial (串口通信)、PlatformIO CLI (编译烧录)

### 不做什么
- 不负责基础聊天 (02-chat)
- 不负责知识库入库与检索 (03-knowledge)
- 不负责 Agent 工作流与 LangGraph 编排 (05-agent)
- 不负责会话持久化与书签 (04-session)
- 不负责 Docker/CI/日志基建 (08-infra)

## 当前状态

### 已完成
- 前端 WorkbenchPanel.tsx 完整 5 个 Tab 组件 (~46KB)
- 前端 useSerialStore 状态管理 (connected/port/baudRate/log/DTR/RTS/filter)
- 前端 types/serial.ts 定义了 SerialDevice 类型
- 前端 types/api.ts 定义了所有硬件相关类型
- 前端 api/endpoints.ts 定义了所有硬件 API 端点常量
- 前端 api/mock.ts 有硬件 mock 数据 (MOCK_DEVICES/MOCK_WIRING/MOCK_AUDIT)
- 后端 api_router.py 已有 /api/devices 路由 (当前返回空列表)
- api-contract.md 中硬件 6 个接口均已约定

### 正在做
- (首次初始化，暂无)

### 阻塞
- 无硬件时依赖 mock 数据演示
- pyserial/PlatformIO 未确认是否已安装

## 接口契约

涉及 docs/api-contract.md 第 5.4~5.10、5.20 节：

| 接口 | 方法 | 状态 | 前端入口 | 后端状态 |
| --- | --- | --- | --- | --- |
| GET /api/devices | GET | agreed | apiGet('devices') | api_router.py 已有 mock |
| POST /api/wiring | POST | agreed | apiPost('wiring', body) | 尚未实现 |
| POST /api/audit_pins | POST | agreed | apiPost('audit_pins', body) | 尚未实现 |
| POST /api/build | POST SSE | agreed | apiSSE('build', body, cb) | 尚未实现 |
| POST /api/upload | POST SSE | agreed | apiSSE('upload', body, cb) | 尚未实现 |
| WS /api/monitor/{port}?baud= | WS | agreed | apiWS(...) | 尚未实现 |
| POST /api/diagnose | POST | implemented | apiPost('diagnose', body) | 尚未实现 |

### 标准响应格式
所有 JSON 接口统一：{ success: true, data: {...} } 或 { success: false, error: { code, message, details } }

### SSE 约定
- Content-Type: text/event-stream
- 事件格式：data: {JSON}\\n\\n
- 事件类型：progress (percent/message)、done (success/message/errors)
- 流结束必须发送 done 事件

### WebSocket 约定
- 路径：/api/monitor/{port}?baud={baud}
- 前端连接后发 {"type":"start"}
- 后端推送 {"type":"data","payload":"串口文本"}

## 关键文件

### 前端
- frontend/src/components/workbench/WorkbenchPanel.tsx - 硬件工作台主组件 (~46KB)
- frontend/src/stores/useSerialStore.ts - 串口状态管理
- frontend/src/types/serial.ts - 串口设备类型定义
- frontend/src/types/api.ts - 全部 API 类型 (包含硬件相关类型)
- frontend/src/api/endpoints.ts - API 端点常量
- frontend/src/api/client.ts - API 桥接函数 (apiGet/apiPost/apiSSE/apiWS)
- frontend/src/api/mock.ts - Mock 数据层

### 后端
- backend/app/api_router.py - 路由注册 (已有 /api/devices mock)
- backend/app/api/serial.py - 串口 API (待创建)
- backend/app/api/flash.py - 编译烧录 API (待创建)
- backend/app/api/wiring.py - 接线图 API (待创建)
- backend/app/api/safety.py - 引脚安全审计 API (待创建)
- backend/app/api/diagnose.py - 代码诊断 API (待创建)

### 文档
- docs/thread-map.md - 线程拆分备忘录
- docs/api-contract.md - API 接口契约 (硬件相关：5.4-5.10, 5.20)
- docs/pitfalls.md - 踩坑记录

## 决策记录
- (暂无，首次初始化)

## 踩坑记录

关联 docs/pitfalls.md：
- 注意 apply_patch 有 bug，禁用；必须用 scripts/write_file.py 写入文件
- 注意 async 回调中不要依赖可变状态 (如 activeSessionId)，必须在闭包中捕获
- 注意 SSE/WebSocket 连接失败时应有正确的错误提示，不再 fallback 到 mock

## 下次开工先看
1. 先读 docs/thread-map.md 确认线程范围
2. 读 docs/api-contract.md 第 5.4~5.20 节确认接口契约
3. 读 docs/pitfalls.md 了解已知踩坑
