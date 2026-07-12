# 修复串口工作台真实硬件接入 Spec

## Why

硬件工作台的串口扫描、连接、读取架构是真实的（基于 pyserial），但存在 1 个阻断双向通信的严重 bug 和若干静默吞异常/假数据回退问题，导致"看起来能用，实际接硬件会踩坑"。本 spec 修复这些阻断点并做最小增强，让工作台真正可双向收发、稳定可用，覆盖 95% 嵌入式 demo 场景（ESP32/STM32/AT 模块）。

## What Changes

### 修复项
- **BREAKING（WS 协议）**：前端发送消息类型从 `{"type":"data"}` 改为 `{"type":"write"}`，与后端现有 `write` 分支对齐
- 修复后端读取循环 `except Exception: pass` 静默吞异常：捕获异常时发 error 事件 + break + 关闭串口 + close WS + 记日志
- 删除前端 `FALLBACK_PORTS` 假端口回退：扫描失败时清空设备列表 + warn 日志 + UI 提示「未扫描到串口设备」
- 删除后端无效的 `websocket.ping()` 心跳任务（Starlette WS 无此方法，静默失败 30s 一次）

### 增强项
- 发送时追加换行：新增 `lineEnding` 状态（`"none" | "\n" | "\r\n"`，默认 `"\r\n"`）+ UI 下拉选择；`handleSend` 拼接后发送
- 扫描返回完整设备信息：`/api/devices` 返回 `vid` / `pid` / `manufacturer` / `serial_number`（pyserial 本就提供，之前丢弃了）
- 拔线/异常告警：前端收到 WS `error` 事件时，log error + UI 状态栏红色提示 + 自动断开 WS + `setConnected(false)`

## Impact

- Affected specs: workbench-agent-bridge（SerialPane 联动，不冲突，只改 SerialPane 内部）
- Affected code:
  - `backend/app/api/tool_routes.py` — WS `/api/monitor/{port}` 读取循环异常处理 + 删除心跳
  - `backend/app/api/hardware_routes.py` — `/api/devices` 返回字段扩展
  - `frontend/src/types/serial.ts` — `SerialDevice` 加 vid/pid/manufacturer/serial_number
  - `frontend/src/stores/useSerialStore.ts` — 加 `lineEnding` 状态 + setter
  - `frontend/src/components/workbench/SerialPane.tsx` — 修发送类型、删假端口、加换行下拉、加 error 处理、设备下拉显示 VID/PID

## ADDED Requirements

### Requirement: 发送换行追加

系统 SHALL 提供发送换行符选择能力，用户可在发送前选择追加 `\r\n`（默认）/ `\n` / 不追加，发送时按选择拼接 payload。

#### Scenario: AT 命令默认追加 \r\n
- **WHEN** 用户在输入框输入 `AT` 且 lineEnding 为默认 `\r\n`
- **THEN** 实际发送到串口的字节为 `AT\r\n`（0x41 0x54 0x0D 0x0A）

#### Scenario: 切换为不追加
- **WHEN** 用户将 lineEnding 切换为 `none` 并输入 `hello`
- **THEN** 实际发送到串口的字节为 `hello`（无换行符）

### Requirement: 完整设备信息扫描

系统 SHALL 在 `/api/devices` 返回每个串口设备的 port / description / vid / pid / manufacturer / serial_number 字段（值为 None 时返回 null），前端设备下拉显示 `COM3 — CH340 (VID:1A86 PID:7523)` 格式。

#### Scenario: 多设备区分
- **WHEN** 系统扫描到 COM3（CH340, VID:1A86 PID:7523）和 COM5（CP2102, VID:10C4 PID:EA60）
- **THEN** 设备下拉分别显示 `COM3 — CH340 (VID:1A86 PID:7523)` 和 `COM5 — CP2102 (VID:10C4 PID:EA60)`，用户可区分

#### Scenario: 无 VID/PID 的设备
- **WHEN** 扫描到某设备 vid/pid 为 None
- **THEN** 下拉显示 `COM3 — USB Serial Port`（只显示 description，不显示 VID/PID 部分）

### Requirement: 拔线/异常告警

系统 SHALL 在串口读取异常时（拔线、硬件错误、I/O 异常）向前端推送 error 事件，前端收到后 log error + UI 状态栏红色提示 + 自动断开 WS + setConnected(false)。

#### Scenario: 通信中拔线
- **WHEN** 串口连接建立后用户拔掉 USB 线
- **THEN** 后端捕获读取异常，发 `{"type":"error","message":"串口读取异常: ..."}` 给前端，关闭串口并 close WS
- **AND** 前端收到 error 事件后，log error + 状态栏显示红色「串口异常: ...」+ setConnected(false)

## MODIFIED Requirements

### Requirement: 串口双向通信

系统 SHALL 支持串口双向通信：前端发送的文本通过 WS `{"type":"write","payload":text}` 消息到达后端，后端调用 `ser.write(text.encode("utf-8"))` 写入串口；后端读取到的数据通过 `{"type":"data","payload":text}` 推送给前端。

#### Scenario: 发送 AT 命令并收到响应
- **WHEN** 用户连接串口后输入 `AT` 并点发送（lineEnding=`\r\n`）
- **THEN** 后端收到 `{"type":"write","payload":"AT\r\n"}` 并写入串口
- **AND** 硬件响应 `OK` 后，前端收到 `{"type":"data","payload":"OK"}` 并显示在日志区

### Requirement: 串口扫描失败处理

系统 SHALL 在串口扫描失败时（后端未起 / pyserial 报错 / 无设备）清空设备列表并显示「未扫描到串口设备」提示，不返回任何假端口数据。

#### Scenario: 无设备
- **WHEN** 系统无任何串口设备连接
- **THEN** `/api/devices` 返回 `{"devices":[]}`，前端设备下拉为空 + 显示「未扫描到串口设备」

## REMOVED Requirements

### Requirement: 假端口回退（FALLBACK_PORTS）

**Reason**: 扫描失败时静默回退到 COM3/COM5/ttyUSB0 假端口列表，UI 看起来像有设备但连接必然失败，demo 时极易误导用户。
**Migration**: 删除 `FALLBACK_PORTS` 常量及回退逻辑，改为清空列表 + 提示。

### Requirement: 应用层 WS 心跳（websocket.ping）

**Reason**: Starlette WebSocket 无 `ping()` 方法，该心跳任务每 30s 抛 AttributeError 并被静默吞掉，实际无保活效果，且违反"不静默吞异常"规范。Starlette WS 靠 TCP keepalive 保活在 demo 场景够用。
**Migration**: 直接删除心跳 asyncio 任务及相关 except 块。
