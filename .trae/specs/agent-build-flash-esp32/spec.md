# Agent 编译烧录真实 ESP32 Spec

## Why

当前 `build_routes.py` 的 `/api/build` 和 `/api/upload` 是 mock SSE（模拟进度），Agent 工具层也没有真实编译烧录工具。要让 Agent 能把 LLM 生成的代码真正烧录到 ESP32，需要：替换 mock 为真实 PlatformIO 编译 + 烧录，并新增 BuildTool/FlashTool 让 LLM 可决策调用。开源用户 `pip install -r requirements.txt` 后即可使用，无需额外安装 Arduino CLI 或其他可执行文件。

## What Changes

### 新增

- **共享核心模块** `backend/src/hardware/pio_runner.py`：封装 PlatformIO 编译 + 烧录逻辑，对外暴露两个 async generator 函数 `compile_firmware()` 和 `upload_firmware()`
- **Agent 工具文件** `backend/src/agent/tools/groups/code/build_tool.py`：含 BuildTool + FlashTool 两个 BaseTool 子类
- **SSE 事件** `compile_log`：透传 PlatformIO 原始输出（编译日志、警告、错误行），前端实时显示
- **临时目录清理**：启动时扫描 `.build/tmp/`，删除超过 24 小时的 session 目录
- **并发锁** `asyncio.Lock`：同端口不能并发烧录

### 修改

- **`backend/app/api/build_routes.py`**：替换 mock SSE，调 pio_runner 共享核心；加 asyncio.Lock 防同端口并发
- **`backend/src/agent/tools/groups/code/__init__.py`**：导出 BuildTool, FlashTool
- **`backend/src/agent/tools/groups/__init__.py`**：顶层 __all__ 加 BuildTool, FlashTool
- **`backend/src/agent/agent_factory.py`**：`_assemble_all_tools` 的 base_tools 列表加 BuildTool(), FlashTool()
- **`backend/requirements.txt`**：加 `platformio>=6.1.0`
- **`.gitignore`**：加 `.pio/`（PlatformIO 构建产物），确认 `.build/tmp/` 已忽略
- **`frontend/src/components/workbench/FlashPane.tsx`**：加 compile_log 事件处理 + 编译日志面板
- **`frontend/src/types/api.ts`**：加 BuildSSEEvent / UploadSSEEvent 联合类型，含 compile_log 事件

### 不改

- `chat_routes.py` L121+ — T2 独占 Agent 接入区，新工具注册在 agent_factory._assemble_all_tools，不碰这里
- `frontend/src/api/client.ts` — apiSSE 桥接函数已通用，新增事件类型不影响桥接

## Impact

- Affected specs:
  - `workbench-agent-bridge` — FlashPane 加 compile_log 面板，不破坏现有 wiring 联动
  - `fix-serial-real-hardware` — 复用 `/api/devices` 的 serial.tools.list_ports 扫描逻辑，不冲突
  - `industrial-tool-runtime` — BuildTool/FlashTool 继承 ToolSpec，遵循权限门控 + 审计日志规范
- Affected code:
  - `backend/src/hardware/pio_runner.py` — 新建共享核心
  - `backend/app/api/build_routes.py` — 替换 mock
  - `backend/src/agent/tools/groups/code/build_tool.py` — 新建 Agent 工具
  - `backend/src/agent/tools/groups/code/__init__.py` — 注册导出
  - `backend/src/agent/tools/groups/__init__.py` — 顶层导出
  - `backend/src/agent/agent_factory.py` — 工具实例化
  - `backend/requirements.txt` — 依赖
  - `.gitignore` — 忽略规则
  - `frontend/src/components/workbench/FlashPane.tsx` — SSE 事件处理
  - `frontend/src/types/api.ts` — 类型定义

## ADDED Requirements

### Requirement: PlatformIO 编译能力

系统 SHALL 提供基于 PlatformIO 的真实编译能力，接收用户代码 + 板型，写入临时目录生成 platformio.ini，调用 `pio run` 编译，通过 SSE 流式返回进度和原始日志。

#### Scenario: 编译 LED 闪烁代码到 ESP32-S3

- **WHEN** 前端发送 POST /api/build `{code: "void setup(){...} void loop(){...}", board: "esp32-s3"}`
- **THEN** 后端在 `.build/tmp/{session_id}/` 创建临时项目（src/main.cpp + platformio.ini）
- **AND** 调用 `pio run` 编译，SSE 流式推送 `thinking` → 多个 `progress` + `compile_log` → `done`
- **AND** 编译成功时 `done` 事件含 `binary_path`（指向 `.pio/build/esp32s3/firmware.bin`）

#### Scenario: 编译失败返回错误详情

- **WHEN** 用户代码有语法错误，PlatformIO 编译失败
- **THEN** SSE 推送 `compile_log` 透传 pio 错误输出，最后 `done` 事件 `success: false`
- **AND** `done.error.code = "COMPILE_FAILED"`，`details` 含 stderr 最后 50 行

#### Scenario: PlatformIO 未安装

- **WHEN** 系统检测到 `pio` 命令不可用
- **THEN** 立即返回 `done` 事件 `success: false`，`error.code = "PIO_NOT_INSTALLED"`
- **AND** error.message 提示用户执行 `pip install platformio`

### Requirement: PlatformIO 烧录能力

系统 SHALL 提供基于 PlatformIO 的真实烧录能力，接收 binary 路径（或代码）+ 板型 + 端口，调用 `pio run --target upload` 烧录到 ESP32，通过 SSE 流式返回烧录进度。

#### Scenario: 烧录已编译的 binary 到 ESP32-S3

- **WHEN** 前端发送 POST /api/upload `{binary_path: ".build/tmp/abc123/.pio/build/esp32s3/firmware.bin", board: "esp32-s3", port: "COM3"}`
- **THEN** 后端验证 binary_path 以 `.build/tmp/` 开头（防路径穿越）
- **AND** 在 platformio.ini 配置 `upload_port = COM3` + `upload_speed = 921600`
- **AND** 调用 `pio run --target upload` 烧录，SSE 流式推送进度
- **AND** 烧录成功时 ESP32 自动复位

#### Scenario: 未传 binary_path 时先编译再烧录

- **WHEN** 前端发送 POST /api/upload `{code: "...", board: "esp32-s3", port: "COM3"}`（无 binary_path）
- **THEN** 后端先调 `compile_firmware` 编译，编译成功后自动调 `upload_firmware` 烧录

#### Scenario: 同端口并发烧录被拒绝

- **WHEN** 端口 COM3 已有一个烧录任务在执行，另一个请求尝试烧录到 COM3
- **THEN** 第二个请求立即返回 `done` 事件 `success: false`，`error.code = "PORT_BUSY"`

#### Scenario: 端口不存在

- **WHEN** 用户传的 port 在 `serial.tools.list_ports` 扫描结果中找不到
- **THEN** 返回 `done` 事件 `success: false`，`error.code = "PORT_NOT_FOUND"`
- **AND** error.details 列出当前可用端口

### Requirement: Agent 编译烧录工具

系统 SHALL 提供 BuildTool 和 FlashTool 两个 Agent 工具，让 LLM 可决策调用真实编译烧录能力。BuildTool 风险等级 LOW 不需 HITL 确认；FlashTool 风险等级 HIGH 但跳过 HITL 直接执行（demo 顺畅优先），仍写审计日志。

#### Scenario: LLM 决策调 BuildTool 编译代码

- **WHEN** 用户在聊天里说"帮我编译这段 LED 闪烁代码到 ESP32-S3"
- **THEN** LLM 决策调用 BuildTool，参数 `{code: "...", board: "esp32-s3"}`
- **AND** BuildTool 内部调 `pio_runner.compile_firmware`，工具调用结果含 binary_path 或错误信息
- **AND** 工具调用写入 ToolAudit 表（spec §9.4），`decision_source = "mode_default"`，`risk_level = "low"`

#### Scenario: LLM 决策调 FlashTool 烧录到设备

- **WHEN** 编译成功后 LLM 决策调用 FlashTool，参数 `{binary_path: "...", board: "esp32-s3", port: "COM3"}`
- **THEN** FlashTool 内部调 `pio_runner.upload_firmware`，工具调用结果含烧录状态
- **AND** 工具调用写入 ToolAudit 表，`decision_source = "mode_default"`（跳过 HITL），`risk_level = "high"`

#### Scenario: 工具描述引导 LLM 何时调用

- **WHEN** LLM 看到工具描述"BuildTool: 编译 Arduino 代码到 ESP32 固件。当用户要求编译/烧录代码时调用"
- **THEN** LLM 在用户说"编译这段代码"时主动调用 BuildTool

### Requirement: SSE 事件兼容 + compile_log 透传

系统 SHALL 兼容现有 mock SSE 事件 schema（thinking/progress/done），同时新增 `compile_log` 事件透传 PlatformIO 原始输出（编译日志、警告、错误行）。

#### Scenario: 前端兼容现有事件 + 处理新事件

- **WHEN** 后端推送 `thinking` / `progress` / `done` 事件
- **THEN** 前端 FlashPane 按现有逻辑处理（思考卡片/进度条/完成提示）
- **WHEN** 后端推送 `compile_log` 事件 `{line: "Compiling main.cpp...", stream: "stdout"}`
- **THEN** 前端在新增的"编译日志"面板实时显示该行

### Requirement: 临时文件清理

系统 SHALL 在启动时扫描 `.build/tmp/` 目录，删除超过 24 小时的 session 目录，避免磁盘空间累积。

#### Scenario: 启动时清理 24 小时前的临时文件

- **WHEN** 系统启动，扫描 `.build/tmp/` 发现 session_abc123 目录最后修改时间是 25 小时前
- **THEN** 删除该目录及其所有内容（含 .pio/build 子目录）
- **AND** 记录日志 `cleanup_old_builds deleted=1`

### Requirement: 超时管理

系统 SHALL 对编译和烧录分别设置超时（编译 300 秒 / 烧录 60 秒），超时时取消 subprocess 并返回错误。

#### Scenario: 编译超时

- **WHEN** PlatformIO 编译超过 300 秒未完成
- **THEN** 取消 subprocess，返回 `done` 事件 `success: false`，`error.code = "COMPILE_TIMEOUT"`
- **AND** error.message 提示"编译超时（5 分钟），可能因首次下载工具链或代码复杂"

#### Scenario: 烧录超时

- **WHEN** esptool 烧录超过 60 秒未完成
- **THEN** 取消 subprocess，返回 `done` 事件 `success: false`，`error.code = "UPLOAD_TIMEOUT"`

## MODIFIED Requirements

### Requirement: build_routes.py SSE 流

`POST /api/build` 和 `POST /api/upload` 从 mock 进度改为真实 PlatformIO 编译/烧录，事件流由 mock 的固定 4 个 progress 事件改为基于真实 subprocess 输出的动态事件流。SSE 事件类型从 `{thinking, progress, done}` 扩展为 `{thinking, progress, compile_log, done}`，前端需新增 compile_log 处理。

### Requirement: Agent 工具集

`_assemble_all_tools` 的 base_tools 列表新增 BuildTool() 和 FlashTool()，Agent 工具数从 25 增至 27（当前 base_tools 11 + local_tools 11 + workbench_tools 3 = 25）。

## REMOVED Requirements

### Requirement: build_routes.py mock SSE 模拟进度

**Reason**: 当前 mock SSE（固定 4 个 progress 事件 + asyncio.sleep）不能真实编译烧录，违反"让 Agent 烧录到真实 ESP32"的核心目标。
**Migration**: 替换为调 pio_runner 共享核心的真实 SSE 流，事件 schema 兼容（thinking/progress/done 保留，新增 compile_log）。
