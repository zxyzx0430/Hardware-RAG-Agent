# Checklist

## 后端修复（tool_routes.py）
- [x] `_read_serial` 协程的 `except Exception: pass` 已改为发 error 事件 + break
- [x] while 主循环的通用 except 也发 error 事件（不只 SerialException 分支）
- [x] finally 块仍正确关闭 ser + close WS
- [x] `websocket.ping()` 心跳任务及相关 except 块已删除
- [x] 无 `except Exception: pass` 残留
> FIXED: 5 处内层 except Exception: pass 已改为 except Exception as e: logger.debug(...)（send error event failed / close websocket failed / send serial open error failed / send serial monitor error failed / close serial failed）

## 后端修复（hardware_routes.py）
- [x] `/api/devices` 返回的 device dict 含 vid / pid / manufacturer / serial_number 字段
- [x] 字段值为 None 时返回 null（不报错）

## 前端类型 + Store
- [x] `types/serial.ts` 的 `SerialDevice` 含 vid? / pid? / manufacturer? / serial_number? 字段
- [x] `useSerialStore.ts` 含 `lineEnding: "none" | "\n" | "\r\n"` 状态（默认 `"\r\n"`）+ `setLineEnding` setter
- [x] `npx tsc --noEmit` 0 errors

## 前端 SerialPane 修复
- [x] `handleSend` 发送的消息类型是 `{"type":"write","payload":text+lineEnding}`（不再是 `type:"data"`）
- [x] `FALLBACK_PORTS` 常量已删除
- [x] 扫描失败时 `setDevices([])` + warn 日志 + UI 显示「未扫描到串口设备」
- [x] 发送输入框旁有换行符下拉（`\r\n` / `\n` / 不追加），绑到 useSerialStore.lineEnding
- [x] onMessage 的 `type==="error"` 分支：log error + 状态栏红色提示 + setConnected(false) + 关闭 WS
- [x] 设备下拉 option 显示 `{port} — {manufacturer||description}{vid?(VID:.. PID:..):}`
- [x] `npx tsc --noEmit` 0 errors

## 集成验证
- [x] 后端启动不报错，`/api/devices` 路由可达
- [x] 前端 `npx tsc --noEmit` 0 errors
- [x] 现有 pytest 测试不回归
- [x] 端到端审查：发送链路 handleSend → WS type:"write" → 后端 write 分支 → ser.write 接得上
- [x] 端到端审查：异常链路 ser.read 抛异常 → 后端发 error → 前端 onMessage error → setConnected(false) 接得上
- [x] 端到端审查：扫描链路 /api/devices 完整字段 → SerialDevice 类型 → 下拉显示 VID/PID 接得上
- [x] 端到端审查：无 FALLBACK_PORTS，扫描失败清空列表

## 文档更新
- [x] `docs/pitfalls.md` 追加串口修复过程的坑
- [x] `docs/completed.md` 追加「串口工作台真实硬件接入修复」章节

## 不破坏现有功能
- [x] WS `/api/monitor/{port}` 现有 set_dtr/set_rts 分支仍可用
- [x] SerialPane 现有收发/过滤/导出/ANSI 渲染能力保留
- [x] DTR/RTS 按钮仍可用
- [x] 波特率下拉仍可选
