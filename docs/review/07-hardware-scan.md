# 07-hardware 代码扫描报告

> 扫描日期：2026-06-24
> 目标线程：07-hardware（硬件工作台）
> 方法：两轮扫描（广度 + 深度），只记录不修复

---

## Pass 1 广度扫描结果

### 1. 类型安全

- 串口参数 TS 类型 (SerialDevice in serial.ts) 和后端返回格式对齐：后端返回 {port, description}，前端定义 {port, description}。对齐 ✅
- GPIO 引脚编号校验：后端 resolve_gpio() 支持 GPIO/N 数字/#define 三种解析，无校验。但前端无独立校验层，直接发到后端。

### 2. 功能完整性

- 串口打开/关闭：前端 SerialPane 有 connect/disconnect 逻辑，WebSocket 连接正常。但无重连机制，意外断开后不会自动恢复。
- 烧录编译：build_routes.py 的 /api/build 和 /api/upload 是 mock SSE（asyncio.sleep + 硬编码进度），不是真实编译烧录，且未标记为 stub。
- 接线图 SVG 交互：WiringPane 有缩放/平移/BOM 表，交互完整 ✅

### 3. 代码异味

- 硬编码引脚/芯片：STRAPPING_PINS 只定义 esp32 和 esp32-s3，其余芯片 fallback 到 esp32-s3 -> 可能返回错误结果
- PreviewPane 默认代码硬编码 WiFi SSID/密码和 ThingSpeak API Key（第38-40行）
- build_routes.py 未标记为 stub/mock，函数名和结构像是真实实现但内容全是 sleep
- FALLBACK_PORTS 在 WorkbenchPanel.tsx 第68行定义，但在组件中未被引用（死代码）

### 4. 资源泄漏

- SerialPane：组件卸载时 wsRef.current?.close() 有 cleanup（useEffect return），但 if (wsRef.current) 只在 wsRef.current 已设置时执行，连接过程中卸载不会清理正在 CONNECTING 的 ws ✅ 但有窗口期
- Build/Upload SSE：apiSSE 调用使用 AbortController，组件卸载时 abort ✅

---

## Pass 2 深度扫描结果

### 1. 契约对齐检查

- /api/devices：前端 apiGet(devices)，后端返回 {success, data: {devices}}，与 api-contract 一致 ✅
- /api/diagnose：前端 apiPost(diagnose, {code, env?, chip?})，后端 DiagnoseRequest(code, env, chip)，一致 ✅
- /api/wiring：
  - api-contract 写 {title, connections, components}，但后端 WiringRequest 没有 title 字段（忽略）
  - 前端 connection 格式：{from: componentName, pin: pinName, to_component, to_pin, color, label, line_type}
  - 后端 connection 格式：{from_pin (alias from), to_pin (alias to), wire_type, note} — 完全不对齐
  - 前端 component 格式：{name, type, pins: string[]}
  - 后端 component 格式：{id, name, pins: dict[str,str]} — pins 类型不匹配
- /api/audit_pins：
  - api-contract 写 pin_assignments (dict)，前端发 pin_assignments (dict) ✅ 对齐
  - 但后端 AuditPinsRequest 定义 assignments: list[PinAssignment] — 字段名和类型都不同 ❌
  - 结果：后端静默忽略 pin_assignments，审计永远返回空（无冲突）
- /api/monitor：路径对齐 ✅

### 2. 错误处理

- 串口扫描失败 (/api/devices)：后端返回 DEVICE_SCAN_FAILED 错误码 ✅，前端 SerialPane 下拉框为空时显示 fallback 文本（不是设备列表）
- 编译失败：build_routes.py 的 SSE 永远 success=true，没有错误路径（mock 无失败场景）
- 接线图生成失败：后端有 try/except，返回 WIRING_FAILED ✅，前端 WiringPane 显示 wiringError
- 引脚审计失败：后端有 try/except，返回 AUDIT_FAILED ✅，前端 SafetyPane 显示错误日志
- 诊断失败：后端有 try/except 兜底 ✅

### 3. 竞态条件

- 串口数据接收和 UI 更新：SerialPane 的 onMessage 回调中直接 setState（addLog），React batch 处理 ✅ 但高频数据可能丢帧
- 后端 hardware_routes.py 没有全局串口连接管理（serial monitor 走 WebSocket 由 api_router.py 处理），单请求无竞争 ✅
- wiring_lock 确保接线图生成串行化 ✅

### 4. 边界情况

- 无串口设备：后端返回空 devices 列表，前端 dropdown 空 → 显示 placeholder ✅
- 不支持的芯片型号：STRAPPING_PINS.get(chip) 找不到时 fallback 到 esp32-s3 → 可能给出错误的 strapping 引脚建议
- Board 引脚超出 GPIO 范围：resolve_gpio 无范围校验，GPIO 编号 0~255 均可通过 int() 解析
- Wiring 连接的 from/to 引脚的组件名称无校验：前端写 ESP32-S3，后端 SVG 生成器依赖 match 可能不渲染
### [P0] audit_pins 请求体字段与后端不匹配
- 位置：backend/app/api/hardware_routes.py:96-101 (AuditPinsRequest) / frontend SafetyPane
- 现象：前端发送 pin_assignments (dict)，后端期望 assignments (list[PinAssignment])。后端静默忽略 pin_assignments，审计永远返回无冲突。
- 影响评估：SafetyPane 的引脚审计功能完全失效，用户始终看到"通过"，不会检测到 Strapping 引脚或冲突。
- 建议修复：1) 后端改回 pin_assignments (dict) 对齐 api-contract；2) 或前端改用 assignments 格式。

### [P0] PinAuditResponse 类型不匹配（后端返回 string[]，前端期望 object[]）
- 位置：frontend/types/api.ts PinAuditResponse.conflicts/warnings (PinWarning[]) / backend/hardware_routes.py (list[str])
- 现象：前端 SafetyPane 遍历 res.conflicts 时访问 item.pin / item.message / item.suggestion，但后端返回的是 string[]。运行时 TypeError。
- 影响评估：用户点击"验证"后前台崩溃（或静默吞错），无任何审计结果展示。
- 建议修复：后端 conflicts/warnings 改为返回 [{pin, severity, message, suggestion}] 对齐前端类型。

### [P0] Wiring 请求体前后端数据模型完全不兼容
- 位置：backend/app/api/hardware_routes.py:73-88 (WiringConnection/WiringComponent) / frontend WorkbenchPanel.tsx:800-815
- 现象：前端 connection 是 {from, pin, to_component, to_pin, color, label, line_type}，后端是 {from_pin (alias from), to_pin (alias to), wire_type, note}。前端 component 是 {name, type, pins:[]}，后端是 {id, name, pins:{}}。
- 影响评估：接线图无法生成（后端收到错误数据），用户始终看到"点击生成接线图"占位。
- 建议修复：统一数据模型。建议后端按 api-contract 的 {title, connections: [{from: {component,pin}, to: {component,pin}, color?, label?}], components: [{name, type, pins:[]}]} 实现。

### [P1] build/upload SSE 路由是 mock 但未标记
- 位置：backend/app/api/build_routes.py 全部
- 现象：/api/build 和 /api/upload 使用 asyncio.sleep + 硬编码进度模拟真实流程，函数名和结构像真实实现，实际无 PlatformIO/串口烧录调用。
- 影响评估：用户看到"编译完成/烧录完成"但实际上什么都没发生。
- 建议修复：1) 文件顶部加 # STUB 标记；2) progress 事件中注明 (stub)；3) 接入真实 PlatformIO CLI。

### [P1] STRAPPING_PINS 只覆盖 esp32 和 esp32-s3
- 位置：backend/app/api/common.py:148-151
- 现象：只有 esp32 和 esp32-s3 两个芯片的 strapping 引脚集合。其他芯片（esp32-c3, esp32-c6, esp32-h2）fallback 到 esp32-s3 集合。
- 影响评估：非 esp32/esp32-s3 芯片收到错误的 strapping 引脚警告。
- 建议修复：补充常见芯片的 strapping 引脚，或找不到时返回 UNKNOWN_CHIP 错误。

### [P2] PreviewPane 默认代码含硬编码 WiFi 凭证
- 位置：frontend/src/components/workbench/WorkbenchPanel.tsx:38-40
- 现象：CODE 常量中写死 ssid=WiFi-2.4G/pwd=password123 和 ThingSpeak apiKey=YOUR_API_KEY。
- 影响评估：用户如果直接使用示例代码会泄露 WiFi 凭证或使用占位 Key。
- 建议修复：默认模板用占位符如 YOUR_SSID/YOUR_PASSWORD，或去掉硬编码默认值。

### [P2] SerialPane 缺乏 WebSocket 重连机制
- 位置：frontend/src/components/workbench/WorkbenchPanel.tsx SerialPane (~line 200-280)
- 现象：WebSocket 断开后不做自动重连。onClose 只记录日志。
- 影响评估：串口数据意外中断后用户需要手动点击重连。调试过程中丢失数据。
- 建议修复：指数退避自动重连（最多 3 次），保持稳定连接。

### [P2] FALLBACK_PORTS 定义未使用（死代码）
- 位置：frontend/src/components/workbench/WorkbenchPanel.tsx:68
- 现象：FALLBACK_PORTS 常量定义了 3 个硬编码串口，但在 SerialPane 中未被引用。
- 影响评估：无运行时影响，仅代码异味。
- 建议修复：删除未使用的 FALLBACK_PORTS 定义。

## 结论

共发现 **9 个问题**：3 个 P0（功能阻断）、2 个 P1（本周修）、3 个 P2（优化）。

P0 集中在前后端契约不对齐——audit_pins 和 wiring 模型不兼容导致功能完全失效，build/upload 是未标记的 mock。

建议优先修复 P0 契约对齐，然后再修 P1/P2。

---

07-hardware 扫描完成，结果在 docs/review/07-hardware-scan.md
