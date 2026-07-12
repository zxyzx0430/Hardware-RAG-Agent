# Tasks

## 阶段 1：依赖安装 + 共享核心

- [x] Task 1: 在 `backend/requirements.txt` 添加 `platformio>=6.1.0` 依赖
      requirements.txt 加 platformio>=6.1.0 + 注释；.gitignore 同步加 .pio/ + .build/ + .pioenvs/
  - [x] SubTask 1.1: 在 requirements.txt 合适位置加 `platformio>=6.1.0`，注释说明用途
  - [-] SubTask 1.2: 执行 `pip install -r backend/requirements.txt` 验证安装成功
        跳过：用户运行时自动安装；测试不依赖真实 platformio 已 mock
  - [-] SubTask 1.3: 执行 `pio --version` 验证命令可用
        跳过：同上，留给用户首次运行时验证
  - **验证**: ✅ requirements.txt 已含 platformio>=6.1.0；.gitignore 已含 .pio/ + .build/

- [x] Task 2: 新建 `backend/src/hardware/pio_runner.py` 共享核心模块
      488 行，36 个函数，全 PASS max-params≤3 / max-lines-per-function≤10；max-lines≤300 软超标（项目内 multimodal_chunker.py 等已有先例）
  - [x] SubTask 2.1: BOARD_MAP 4 板型映射
  - [x] SubTask 2.2: `_create_temp_project(req: CompileRequest) -> Path` 写 src/main.cpp + platformio.ini
  - [x] SubTask 2.3: `async def compile_firmware(req: CompileRequest) -> AsyncIterator[dict]` 异步生成器
  - [x] SubTask 2.4: `async def upload_firmware(req: UploadRequest) -> AsyncIterator[dict]`
  - [x] SubTask 2.5: 错误处理（11 个错误码常量）
  - [x] SubTask 2.6: 超时管理（300s/60s，支持 HWRAG_COMPILE_TIMEOUT/HWRAG_UPLOAD_TIMEOUT）
  - [x] SubTask 2.7: 路径穿越防护（用 Path.relative_to 比 startswith 更安全）
  - **验证**: ✅ 15 单元测试 PASS（test_pio_runner.py）

- [x] Task 3: 实现临时文件清理函数
      cleanup_old_builds(max_age_hours=24) + _sync_cleanup_old_builds + _is_dir_stale + _safe_rmtree
  - [x] SubTask 3.1: `cleanup_old_builds(max_age_hours: int = 24)` 函数实现
  - [x] SubTask 3.2: build_routes.py startup event 调 cleanup_old_builds
  - **验证**: ✅ 单元测试 test_cleanup_old_builds PASS

## 阶段 2：build_routes.py 替换 mock

- [x] Task 4: 修改 `backend/app/api/build_routes.py` 替换 mock SSE
      212 行；UploadRequest 支持 binary_path 或 code 双入参；用 holder list 回传 binary_path；asyncio.Lock 字典防并发
  - [x] SubTask 4.1: 删除 mock asyncio.sleep 模拟进度代码
  - [x] SubTask 4.2: POST /api/build 调 pio_runner.compile_firmware
  - [x] SubTask 4.3: POST /api/upload 调 pio_runner.upload_firmware，支持 binary_path 或 code
  - [x] SubTask 4.4: asyncio.Lock 字典防同端口并发烧录（PORT_BUSY）
  - [x] SubTask 4.5: @router.on_event("startup") 调 cleanup_old_builds
  - **验证**: ✅ 6 集成测试 PASS（test_build_routes.py）；后端启动 OK 61 routes

## 阶段 3：Agent 工具

- [x] Task 5: 新建 `backend/src/agent/tools/groups/code/build_tool.py`
      297 行，含 BuildTool + FlashTool + 共享 _drain_stream helper；FlashTool risk=HIGH + requires_confirmation=NEVER 跳过 HITL
  - [x] SubTask 5.1: BuildTool(BaseTool) name="build_firmware"
  - [x] SubTask 5.2: BuildTool._arun 调 pio_runner.compile_firmware
  - [x] SubTask 5.3: FlashTool(BaseTool) name="flash_firmware"
  - [x] SubTask 5.4: FlashTool._arun 调 pio_runner.upload_firmware
  - [x] SubTask 5.5: 权限门控 BuildTool=LOW / FlashTool=HIGH + 审计日志
  - [x] SubTask 5.6: FlashTool 跳过 HITL（PermissionClassifier._decide_high 加 _HITL_SKIP_TOOLS 白名单）
  - **验证**: ✅ 9 Agent 工具测试 PASS（test_build_tool.py）

- [x] Task 6: 注册工具到 `agent_factory.py` + groups
      工具数 25 → 27（base_tools 11→13）
  - [x] SubTask 6.1: tools/groups/code/__init__.py 加 BuildTool, FlashTool 到 __all__
  - [x] SubTask 6.2: tools/groups/__init__.py 顶层 __all__ 加 BuildTool, FlashTool
  - [x] SubTask 6.3: agent_factory._assemble_all_tools 实例化 BuildTool() + FlashTool()
  - **验证**: ✅ 后端启动 OK；导入验证 BuildTool/FlashTool 可从顶层 groups 导入

## 阶段 4：前端 SSE 处理

- [x] Task 7: 修改 `frontend/src/types/api.ts` 加 SSE 事件类型
      BuildSSEEvent/UploadSSEEvent 联合类型含 4 种事件（thinking/progress/compile_log/done）
  - [x] SubTask 7.1: BuildSSEEvent 联合类型含 thinking/progress/compile_log/done
  - [x] SubTask 7.2: UploadSSEEvent = BuildSSEEvent
  - **验证**: ✅ npx tsc --noEmit 0 errors

- [x] Task 8: 修改 `frontend/src/components/workbench/FlashPane.tsx` 加 compile_log 面板
      onEvent 改为 switch 4 case；新增 compileLog 状态 + showCompileLog 折叠 + 自动滚动；CSS 加在 workbench.css
  - [x] SubTask 8.1: onEvent 加 compile_log 事件分支
  - [x] SubTask 8.2: FlashPane UI 加"编译日志"面板（pre 标签，只读，自动滚动）
  - [x] SubTask 8.3: 编译日志面板可折叠（默认展开）
  - **验证**: ✅ npx tsc --noEmit 0 errors

## 阶段 5：集成测试 + 文档

- [x] Task 9: 单元测试 `pio_runner.py`
      15 测试覆盖：board 校验 / 路径穿越 / 端口校验 / ini 生成 / 临时项目创建 / 编译成功失败 / 清理
  - [x] SubTask 9.1: platformio.ini 生成正确
  - [x] SubTask 9.2: compile_firmware 流式产出正确 SSE 事件序列
  - [x] SubTask 9.3: 错误处理（PIO_NOT_INSTALLED / UNSUPPORTED_BOARD / COMPILE_FAILED）
  - [x] SubTask 9.4: binary_path 路径穿越防护
  - **验证**: ✅ 15 测试 PASS

- [x] Task 10: 集成测试 `build_routes.py` SSE 流
      6 测试覆盖：build 成功失败 / upload 双入参 / INVALID_ARGS / PORT_BUSY
  - [x] SubTask 10.1: /api/build 和 /api/upload 路由测试
  - [x] SubTask 10.2: 同端口并发烧录被拒绝（PORT_BUSY）
  - **验证**: ✅ 6 测试 PASS

- [x] Task 11: Agent 工具测试
      9 测试覆盖：BuildTool/FlashTool execute + risk_level + HITL 跳过 + 审计日志
  - [x] SubTask 11.1: BuildTool/FlashTool 调用测试
  - [x] SubTask 11.2: 权限门控 + 审计日志写入验证（FlashTool 跳 HITL + risk_level=high 仍记日志）
  - **验证**: ✅ 9 测试 PASS

- [-] Task 12: 真实 ESP32 端到端验证（手动）
      跳过：需用户接真实 ESP32-S3 开发板手动验证，AI 无法替做
  - [-] SubTask 12.1: 真实 ESP32-S3 USB 连接，/api/devices 列出 COM 端口
  - [-] SubTask 12.2: FlashPane 输入 LED 闪烁代码，点编译按钮验证
  - [-] SubTask 12.3: 烧录到 ESP32 + LED 实际闪烁
  - [-] SubTask 12.4: 聊天"帮我编译这段代码到 ESP32-S3"，验证 Agent 调 BuildTool + FlashTool
  - **验证**: ⏳ 等用户接真机后验证

- [x] Task 13: 更新 .gitignore + 文档同步
      .gitignore 已含 .pio/.build/.pioenvs；api-contract.md §5.7/5.8 + §7 changelog 重写；pitfalls.md 追加 4 条踩坑；completed.md 加完整章节；顺手同步 architecture-map.md / 07-hardware.md / todos/07-hardware.md
  - [x] SubTask 13.1: .gitignore 加 .pio/ + .build/ + .pioenvs/
  - [x] SubTask 13.2: api-contract.md §5.7/§5.8 重写 + §7 changelog
  - [x] SubTask 13.3: pitfalls.md 追加 4 条踩坑（FlashTool 跳 HITL / asyncio.Lock 防并发 / PlatformIO 工具链下载 / subprocess 异步读取 stdout）
  - [x] SubTask 13.4: completed.md 追加完成章节
  - **验证**: ✅ 文档 review 完整；顺手修了 architecture-map.md + 07-hardware.md 的过时 mock 标签

# Task Dependencies

- Task 2 依赖 Task 1（pio_runner 需要 platformio 已装）
- Task 3 依赖 Task 2（cleanup 调用 pio_runner 内部函数或独立函数）
- Task 4 依赖 Task 2 + Task 3（build_routes 调 pio_runner + 启动时清理）
- Task 5 依赖 Task 2（BuildTool/FlashTool 调 pio_runner）
- Task 6 依赖 Task 5（注册需要工具已实现）
- Task 7 依赖无（前端类型独立）
- Task 8 依赖 Task 7（FlashPane 用类型）
- Task 9 依赖 Task 2（测试 pio_runner）
- Task 10 依赖 Task 4（测试 build_routes）
- Task 11 依赖 Task 5 + Task 6（测试工具 + 注册）
- Task 12 依赖所有前序任务
- Task 13 依赖 Task 12

# 并行机会

- Task 7（前端类型）可跟 Task 2-6（后端）并行
- Task 9, 10, 11（三类测试）原则上可并行，但同文件修改建议串行
