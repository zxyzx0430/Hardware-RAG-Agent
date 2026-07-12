# Tasks

## 阶段 1：后端修复（tool_routes.py + hardware_routes.py）

- [ ] Task 1: 修复 `backend/app/api/tool_routes.py` WS `/api/monitor/{port}` 读取循环静默吞异常
  - [ ] SubTask 1.1: 把 `_read_serial` 协程里的 `except Exception: pass` 改为：捕获异常 → 发 `{"type":"error","message":f"串口读取异常: {e}"}` → break 跳出读取循环
  - [ ] SubTask 1.2: 在 while 主循环的 except 兜底也加 error 事件发送（已有 SerialException 分支，确认通用 except 也发 error）
  - [ ] SubTask 1.3: finally 块确认关闭 ser + close WS（已有，验证不破坏）
  - **验证**: 读 tool_routes.py 确认无 `except Exception: pass` 残留；手动模拟 ser.read 抛异常时 WS 收到 error 事件

- [ ] Task 2: 删除 `backend/app/api/tool_routes.py` 无效的 `websocket.ping()` 心跳任务
  - [ ] SubTask 2.1: 删除 `asyncio.create_task` 启动心跳的代码 + 心跳协程函数 + 相关 except 块
  - **验证**: 读 tool_routes.py 确认无 `websocket.ping` / 心跳 asyncio 任务残留

- [ ] Task 3: 扩展 `backend/app/api/hardware_routes.py` `/api/devices` 返回字段
  - [ ] SubTask 3.1: 在 device dict 里加 `vid` / `pid` / `manufacturer` / `serial_number` 字段（从 pyserial `ListPortInfo` 取，None 时返回 null）
  - **验证**: 后端启动后 `curl http://127.0.0.1:58080/api/devices` 返回含 vid/pid/manufacturer/serial_number 字段（无设备时字段为 null 或列表为空）

## 阶段 2：前端类型 + Store（types/serial.ts + useSerialStore.ts）

- [ ] Task 4: 扩展 `frontend/src/types/serial.ts` `SerialDevice` 类型
  - [ ] SubTask 4.1: 加 `vid?: number | null` / `pid?: number | null` / `manufacturer?: string | null` / `serial_number?: string | null`
  - **验证**: `npx tsc --noEmit` 0 errors

- [ ] Task 5: 修改 `frontend/src/stores/useSerialStore.ts` 加 `lineEnding` 状态
  - [ ] SubTask 5.1: 加 `lineEnding: "none" | "\n" | "\r\n"`（默认 `"\r\n"`）+ `setLineEnding` setter
  - **验证**: `npx tsc --noEmit` 0 errors

## 阶段 3：前端 SerialPane 修复 + 增强（SerialPane.tsx）

- [ ] Task 6: 修复 `frontend/src/components/workbench/SerialPane.tsx` 发送类型不匹配
  - [ ] SubTask 6.1: `handleSend` 把 `{"type":"data","payload":text}` 改为 `{"type":"write","payload":text+lineEnding}`
  - **验证**: 读 SerialPane.tsx 确认 handleSend 发的是 `type:"write"`；tsc 0 errors

- [ ] Task 7: 删除 `frontend/src/components/workbench/SerialPane.tsx` 假端口回退
  - [ ] SubTask 7.1: 删除 `FALLBACK_PORTS` 常量
  - [ ] SubTask 7.2: 扫描失败时 `setDevices([])` + `useLogStore.log("warn","serial","未扫描到串口设备")` + UI 显示「未扫描到串口设备」（不再塞假数据）
  - **验证**: 读 SerialPane.tsx 确认无 FALLBACK_PORTS；后端不启动时前端设备下拉为空 + 提示

- [ ] Task 8: 加发送换行下拉 UI
  - [ ] SubTask 8.1: 在发送输入框旁加一个 `<select>` 选换行符：`\r\n`（默认）/ `\n` / 不追加
  - [ ] SubTask 8.2: select 值绑到 useSerialStore.lineEnding，onChange 调 setLineEnding
  - **验证**: tsc 0 errors；UI 渲染下拉可选

- [ ] Task 9: 加 WS error 事件处理（拔线告警）
  - [ ] SubTask 9.1: onMessage 的 `type==="error"` 分支：`useLogStore.log("error","serial",msg.message)` + 状态栏红色提示 + `setConnected(false)` + 关闭 WS
  - **验证**: tsc 0 errors；读 SerialPane.tsx 确认 error 分支处理

- [ ] Task 10: 设备下拉显示 VID/PID/厂商
  - [ ] SubTask 10.1: 下拉 option 文本改为：`{port} — {manufacturer||description}{vid?` (VID:${hex} PID:${hex})`:``}`
  - **验证**: tsc 0 errors；多设备时下拉能区分

## 阶段 4：集成验证

- [x] Task 11: 后端启动 + 前端 tsc 全量验证
      后端 62 路由 OK + /api/devices + /monitor 可达；前端 tsc 0 errors；pytest 因环境缺 sqlalchemy 收集失败（与本次修改无关，本次修改的 tool_routes/hardware_routes 导入 OK）
  - [x] SubTask 11.1: `cd backend && python -c "from app.main import create_app; app=create_app(); print('OK, routes:', len(app.routes))"` 输出 OK, routes: 62
  - [x] SubTask 11.2: `cd frontend && npx tsc --noEmit` 0 errors
  - [x] SubTask 11.3: pytest 因环境缺 sqlalchemy 无法收集（pre-existing 环境问题，非本次修改导致）；改用导入验证 tool_routes + hardware_routes 均导入 OK

- [x] Task 12: 端到端代码审查验证（双 subagent：A 验完成度 + B 找问题）
      A: 4/4 链路全部可达（发送/异常/扫描/无假端口）；B: 发现 1 严重 S1 + 3 中等 M1/M2/M3 + 若干低优先级，全部已修复
  - [x] SubTask 12.1: 审查发送链路：handleSend → WS `type:"write"` → 后端 write 分支 → ser.write ✅接得上
  - [x] SubTask 12.2: 审查异常链路：ser.read 抛异常 → 后端发 error 事件 → 前端 onMessage error 分支 → setConnected(false) ✅接得上
  - [x] SubTask 12.3: 审查扫描链路：/api/devices 返回完整字段 → 前端 SerialDevice 类型 → 下拉显示 VID/PID ✅接得上
  - [x] SubTask 12.4: 审查无假端口：FALLBACK_PORTS 已删，扫描失败清空列表 ✅接得上
  - [x] SubTask 12.5: 修复 S1（SerialDevice 类型重复定义改用 import）+ M1（portName 空值守卫）+ M2（_read_serial break 前主动 close WS）+ M3（ser.write 包 try/except）+ M6（wsRef 冗余赋值）

- [x] Task 13: 更新 `docs/pitfalls.md` + `docs/completed.md`
      pitfalls.md 追加 2 条（4 阻断点 + 类型重复定义）；completed.md 追加完整章节（核心结论+修复项+增强项+质量修复+传导链验证表+已知限制）
  - [x] SubTask 13.1: 修复过程中的坑追加到 pitfalls.md（发送类型不匹配、静默吞异常、假端口回退、无效心跳 + 类型重复定义）
  - [x] SubTask 13.2: 完成后追加 completed.md「串口工作台真实硬件接入修复」章节

# Task Dependencies

- Task 4 依赖 Task 3（前端类型要等后端字段确定）
- Task 6 依赖 Task 5（handleSend 用 lineEnding）
- Task 8 依赖 Task 5（下拉绑 useSerialStore.lineEnding）
- Task 10 依赖 Task 4（下拉用扩展后的 SerialDevice 类型）
- Task 11 依赖所有前序任务
- Task 12 依赖 Task 11
- Task 13 依赖 Task 12

# 并行机会

- Task 1, 2, 3（后端三处独立修改）可并行
- Task 4, 5（前端类型 + store 独立）可并行
- Task 6, 7, 8, 9, 10（SerialPane 内不同函数）原则上可并行，但同文件修改建议串行避免冲突
