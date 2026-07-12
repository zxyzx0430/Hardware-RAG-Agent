# 07-hardware TODO

> 参考 AGENTS.md TODO 系统规则维护。

## 当前任务

- [x] 修复 monitor WebSocket 路径缺 /api/ 前缀（issue #7，P0）
      ✅ endpoints.ts monitor 常量改为 /api/monitor/，apiWS 去掉硬编码 /api 前缀拼接，
         WorkbenchPanel.tsx WS 调用同步改为 /api/monitor/，api-contract.md 文档对齐
- [x] stub 工具返回带入参信息（issue #4，P1）
      ✅ AuditPinsTool/WiringTool/BuildTool/UploadTool 的 output 改为 f-string 含入参字段；
         SearchDocsTool 已正确，无改动；self.args 死代码不存在

## 待办

- [x] 后端硬件 API 实现（serial.py / flash.py / wiring.py / safety.py / diagnose.py）— 部分完成（2026-06-29 核实）：
  - **已实现**（功能分散在多文件，非独立 serial.py/flash.py 等）：
    - serial 串口桥接 → [tool_routes.py](file:///E:/Desktop/agent/backend/app/api/tool_routes.py) 的 `_read_serial`/`_heartbeat` + websocket 路由
    - diagnose 引脚诊断 → [hardware_routes.py](file:///E:/Desktop/agent/backend/app/api/hardware_routes.py) L57 `/diagnose` + [app/hardware/gpio.py](file:///E:/Desktop/agent/backend/app/hardware/gpio.py)
    - wiring 接线图 → [hardware_routes.py](file:///E:/Desktop/agent/backend/app/api/hardware_routes.py) L181 `/wiring` + [src/hardware/svg_generator.py](file:///E:/Desktop/agent/backend/src/hardware/svg_generator.py)
    - audit_pins 引脚审计 → [hardware_routes.py](file:///E:/Desktop/agent/backend/app/api/hardware_routes.py) L224 + [app/hardware/audit.py](file:///E:/Desktop/agent/backend/app/hardware/audit.py)
    - flash 烧录 → 已实现（2026-07-03 落地）：[build_routes.py](file:///E:/Desktop/agent/backend/app/api/build_routes.py) 真实 SSE 调 [pio_runner.py](file:///E:/Desktop/agent/backend/src/hardware/pio_runner.py)（PlatformIO `pio run --target upload`）；BuildTool/FlashTool 注册到 Agent（工具数 25→27），见 docs/completed.md「Agent 编译烧录真实 ESP32」
  - **未实现（v2 范围）**：
    - safety 安全检查 → 无独立 safety.py
- [?] 前后端联调 monitor WebSocket — 需确认（2026-06-29 核实）：代码层面已实现（[tool_routes.py](file:///E:/Desktop/agent/backend/app/api/tool_routes.py) websocket 路由 + [endpoints.ts](file:///E:/Desktop/agent/frontend/src/api/endpoints.ts) monitor 路径），但需实际启动后端 + 连接串口设备做端到端联调验证，纯代码核实无法确认联调通过
- [x] 确认 pyserial / PlatformIO 是否安装 — 已确认（2026-06-29 核实）：[requirements.txt](file:///E:/Desktop/agent/backend/requirements.txt) L29 `pyserial==3.5` 已装；PlatformIO 未在 requirements.txt 中，但 [executor.py](file:///E:/Desktop/agent/backend/src/sandbox/executor.py) L33 用 `platformio/platformio-core:latest` Docker 镜像方式，L79 容器内用 `platformio ci` 命令，无需宿主机装 platformio pip 包
