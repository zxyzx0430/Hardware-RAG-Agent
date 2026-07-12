# Checklist

## 依赖 + 共享核心
- [x] `backend/requirements.txt` 含 `platformio>=6.1.0`
- [-] `pio --version` 命令可用（用户首次运行时验证，AI 阶段跳过）
- [x] `backend/src/hardware/pio_runner.py` 存在，含 `compile_firmware` + `upload_firmware` 两个 async generator 函数
- [x] BOARD_MAP 常量含 4 个板型映射（esp32-s3 / esp32 / esp32-c3 / esp32-s2）
- [x] `_create_temp_project` 函数在 `.build/tmp/{session_id}/` 创建 src/main.cpp + platformio.ini
- [x] platformio.ini 含 `[env:xxx]` + `platform = espressif32` + `board = ...` + `framework = arduino`
- [x] 编译超时 300 秒，烧录超时 60 秒（支持环境变量覆盖）
- [x] binary_path 必须以 `.build/tmp/` 开头，否则返回 INVALID_BINARY_PATH（实际用 Path.relative_to 更安全）
- [x] `cleanup_old_builds` 函数扫描 `.build/tmp/` 删除 24 小时前的目录

## build_routes.py 替换 mock
- [x] POST /api/build 不再含 `asyncio.sleep` mock 进度代码
- [x] POST /api/build 调 `pio_runner.compile_firmware`，流式转发 SSE 事件
- [x] POST /api/upload 调 `pio_runner.upload_firmware`，支持 binary_path 或 code 入参
- [x] asyncio.Lock 字典（key=port）防同端口并发烧录，违反时返回 PORT_BUSY
- [x] startup event 调 `cleanup_old_builds`
- [x] 错误码完整：PIO_NOT_INSTALLED / COMPILE_FAILED / COMPILE_TIMEOUT / PORT_NOT_FOUND / PORT_BUSY / UPLOAD_TIMEOUT / INVALID_BINARY_PATH

## Agent 工具
- [x] `backend/src/agent/tools/groups/code/build_tool.py` 存在，含 BuildTool + FlashTool 两个 class
- [x] BuildTool.name = "build_firmware"，description 含"当用户要求编译/烧录代码时调用"
- [x] FlashTool.name = "flash_firmware"，description 含"当需要烧录到 ESP32 设备时调用"
- [x] BuildTool._arun 调 `pio_runner.compile_firmware`，返回 binary_path 或 error
- [x] FlashTool._arun 调 `pio_runner.upload_firmware`，返回烧录状态
- [x] BuildTool risk_level = "low"，FlashTool risk_level = "high"
- [x] 两个工具都写 ToolAudit 表（spec §9.4），decision_source 用 spec 枚举值
- [x] FlashTool 跳过 HITL 确认（PermissionClassifier._HITL_SKIP_TOOLS 白名单），审计日志仍写 HIGH 风险
- [x] `tools/groups/code/__init__.py` 导出 BuildTool, FlashTool
- [x] `tools/groups/__init__.py` 顶层 __all__ 含 BuildTool, FlashTool
- [x] `agent_factory._assemble_all_tools` 的 base_tools 列表含 BuildTool(), FlashTool()
- [x] 后端启动日志验证工具数 27（base_tools 13 + local_tools 11 + workbench_tools 3）

## 前端 SSE 处理
- [x] `frontend/src/types/api.ts` 含 BuildSSEEvent + UploadSSEEvent 联合类型
- [x] 联合类型包含 thinking / progress / compile_log / done 四种事件
- [x] `FlashPane.tsx` 处理 `compile_log` 事件，把 line 追加到编译日志状态
- [x] FlashPane UI 含"编译日志"面板（只读，自动滚动到底部，可折叠）
- [x] `npx tsc --noEmit` 0 errors

## SSE 事件 schema
- [x] thinking 事件：`{content: str, source: "build"|"flash"}`
- [x] progress 事件：`{percent: int, message: str}`（兼容现有 mock schema）
- [x] compile_log 事件：`{line: str, stream: "stdout"|"stderr"}`（新增）
- [x] done 事件：`{success: bool, binary_path?: str, error?: {code, message, details}}`

## 测试
- [x] `backend/tests/test_pio_runner.py` 单元测试 PASS（15 测试 mock subprocess）
- [x] `backend/tests/test_build_routes.py` 集成测试 PASS（6 测试 mock pio_runner）
- [x] `backend/tests/test_build_tool.py` Agent 工具测试 PASS（9 测试含权限门控 + 审计日志验证）
- [-] 真实 ESP32-S3 端到端验证：LED 闪烁代码能编译成功并烧录到设备，LED 实际闪烁（需用户接真机）

## .gitignore + 文档
- [x] `.gitignore` 含 `.pio/` 规则（含 .build/ 和 .pioenvs/）
- [x] `.build/tmp/` 已被 `.build/` 规则覆盖
- [x] `docs/api-contract.md` §5.7/§5.8 更新 + §7 changelog + 新增 compile_log 事件 + 错误码表
- [x] `docs/pitfalls.md` 追加 4 条 PlatformIO 编译烧录相关踩坑
- [x] `docs/completed.md` 追加完成章节

## 不破坏现有功能
- [x] 前端 FlashPane 现有 progress/done 事件处理保留（switch 4 case 包含原 2 种）
- [x] /api/devices 现有 serial.tools.list_ports 扫描不受影响
- [x] Agent 现有 11 个工具不受影响（只新增不修改，工具数 25→27）
- [x] chat_routes.py L121+ Agent 接入区不碰
- [x] useChatStore.ts 不碰
- [x] 后端启动 OK，61 routes 可达
- [x] 前端 tsc 0 errors
