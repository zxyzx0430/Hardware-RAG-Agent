# 06-sandbox 代码扫描报告

> 扫描时间：2026-06-25
> 范围文件：
> - backend/src/sandbox/executor.py
> - backend/src/sandbox/__init__.py
> - backend/app/api/sandbox_routes.py
> - docs/api-contract.md §5.21-5.23
> - backend/tests/（sandbox 相关）
> 规则：只记录不修

---

## Pass 1 — 广度扫描

### 1. 类型安全

#### [P2] `from typing import Optional` 未使用

- 位置：`backend/src/sandbox/executor.py:8`
- 现象：导入了 `Optional` 但未在任何类型注解中使用。
- 影响评估：无运行时影响，但属于死代码，lint 工具会报 warning。
- 建议修复方式：移除未使用的 import。

### 2. 功能完整性

#### [P1] 路由白名单与执行器镜像映射不一致

- 位置：`backend/app/api/sandbox_routes.py:18` vs `backend/src/sandbox/executor.py:24-29`
- 现象：routes.py 的 `ALLOWED_LANGUAGES` 包含 `"arduino"`，但 executor.py 的枚举表（5.21 契约）只列出了 `python/c/cpp/javascript`，没有列出 arduinor。三方不一致。
- 影响评估：前端看到 supported_languages 包含 arduino，契约未列出，后端实际支持，属于三方不同步。
- 建议修复方式：统一三个来源的 language 列表，任意一处增删须同步其余两处。

#### [P1] Docker 不可用时的行为与契约不符

- 位置：`backend/src/sandbox/executor.py:102-107` vs `docs/api-contract.md` §5.21 Mock 规则
- 现象：契约写「Mock 规则：后端无 Docker 时，返回模拟执行结果 `{ stdout: "(mock) hello sandbox", ... }`」，但实际代码返回的是错误信息「Docker 未安装或未启动。请安装 Docker Desktop...」，不返回 mock 结果。
- 影响评估：前端期望 Docker 不可用时仍可展示演示效果，实际会拿到错误提示。
- 建议修复方式：二选一：要么契约去掉 Mock 规则（安全优先），要么代码实现 mock 降级。

### 3. 代码异味

#### [P2] 硬编码的资源限制

- 位置：`backend/src/sandbox/executor.py:33-37`
- 现象：`CPU_TIMEOUT = 10`、`MEMORY_LIMIT = "256m"`、`NETWORK_DISABLED = True` 全部硬编码在模块级常量，不可配置。且 CPU_TIMEOUT=10 与契约写的"默认超时 30 秒"不一致（10 vs 30）。
- 影响评估：用户无法按需调整超时和内存限制；契约与实现数值不符。
- 建议修复方式：将配置项移到 `src/config/settings.py` 或环境变量；统一超时值。

#### [P2] `_run_container_sync` 的 else 兜底执行 shell 命令

- 位置：`backend/src/sandbox/executor.py:69-70`
- 现象：else 分支执行 `cmd = ["sh", "-c", code]`，虽然 routes.py 的白名单已阻止非法 language 传入，但 executor 自身也有 `LANGUAGE_IMAGES.get(language)` 检查，不匹配时返回错误。实际上不会进入此 else 分支。
- 影响评估：防御性代码，增加维护困惑。属于死代码路径。
- 建议修复方式：移除 else 兜底分支，或改用 `raise ValueError`。

#### [P3] docker 版本未固定

- 位置：`backend/requirements.txt`（需确认）
- 现象：executor.py 依赖 `import docker`（docker-py），如果 requirements.txt 未固定版本，新环境可能安装不兼容版本。
- 影响评估：依赖泄漏，新环境启动时缺少 docker-py 报 ImportError。
- 建议修复方式：在 requirements.txt 中添加 `docker>=7.0,<8.0`。

### 4. 资源泄漏

#### [P1] stdin attach socket 可能泄漏

- 位置：`backend/src/sandbox/executor.py:79-85`
- 现象：`container.attach()` 返回的 socket 在 send 后调用了 `sock.close()`，但 attach 可能在异常时未正确关闭。且 attach 调用设置了 `stream=False` 返回低级 socket，若 send 抛异常则 socket 未关闭。
- 影响评估：极端情况下可能泄漏临时 TCP socket 连接。
- 建议修复方式：用 `try/finally` 包裹 attach/send/close，或用 `with` 语句管理 socket 生命周期。

---

## Pass 2 — 深度扫描

### 1. 契约对齐

#### [P0] 错误码与契约不匹配

- 位置：`backend/app/api/sandbox_routes.py:28-38` vs `docs/api-contract.md` §5.21 错误响应
- 现象：
  - 契约定义的错误码：`EMPTY_CODE` / `CODE_TOO_LONG` / `UNSUPPORTED_LANGUAGE` / `SANDBOX_UNAVAILABLE` / `EXECUTION_TIMEOUT` / `EXECUTION_FAILED`
  - 实际代码：
    - 代码为空：`raise HTTPException(400, detail="代码不能为空")` → 返回 `{"detail": "..."}`，不是标准错误格式
    - 代码超长：同上
    - 不支持的 language：返回 `{"success": false, "error": {"code": "INVALID_LANGUAGE", ...}}` → code 是 `INVALID_LANGUAGE` 而非 `UNSUPPORTED_LANGUAGE`
- 影响评估：前端按契约做的错误处理逻辑在代码为空和超长时无法工作；`UNSUPPORTED_LANGUAGE` 永远收不到。
- 建议修复方式：
  1. 代码为空/超长改为返回标准错误格式而非 HTTPException
  2. `INVALID_LANGUAGE` 统一为 `UNSUPPORTED_LANGUAGE`

#### [P1] 审计日志接口（5.23）只有契约没有实现

- 位置：`docs/api-contract.md` §5.23 vs `backend/app/api/sandbox_routes.py`
- 现象：契约定义了 `POST /api/sandbox/audit` 的完整请求/响应/错误码规格，但 sandbox_routes.py 中没有对应路由实现。
- 影响评估：前端按契约调用此端点会收到 404。
- 建议修复方式：实现 `POST /api/sandbox/audit` 路由，或将状态标记为 `draft` 并添加说明后端尚未实现。

#### [P2] 5.21 枚举表缺少 arduino

- 位置：`docs/api-contract.md` §5.21 枚举说明
- 现象：枚举表只列出 `python/c/cpp/javascript`，但 routes.py 的 `ALLOWED_LANGUAGES` 包含 `arduino`，executor.py 的 `LANGUAGE_IMAGES` 也包含 `arduino`。
- 影响评估：前端参考契约不会知道支持 arduino。
- 建议修复方式：契约补上 `arduino` 枚举项。

#### [P2] `GET /api/sandbox/status` 返回的 supported_languages 不一致

- 位置：`docs/api-contract.md` §5.22 vs `backend/app/api/sandbox_routes.py:59`
- 现象：契约 5.22 示例响应中 `supported_languages: ["python", "c", "cpp", "javascript"]` 缺了 `arduino`，但实际代码返回 `sorted(ALLOWED_LANGUAGES)` 包含 arduino。
- 影响评估：前端看到 arduino 但契约没写，属于文档未同步。
- 建议修复方式：契约补充 arduino。

### 2. 错误处理

#### [P1] 执行超时后 stderr 可能缺失完整错误信息

- 位置：`backend/src/sandbox/executor.py:91-97`
- 现象：容器 wait 超时时执行 `container.kill()`，然后读取 logs。但 kill 后立即读取 logs 可能 race condition——部分日志尚未 flush 到 Docker 存储，导致 stdout/stderr 截断或为空。
- 影响评估：超时的代码调式者看不到完整输出，难以判断卡在哪里。
- 建议修复方式：kill 后增加短暂等待（如 500ms）再读取 logs，或使用 `container.wait()` 的 timeout 异常中保留已有输出。

#### [P2] Docker 守护进程不响应时 check_docker_available 可能长时间阻塞

- 位置：`backend/src/sandbox/executor.py:38-46`
- 现象：`docker.from_env().ping()` 在 Docker 守护进程挂起时可能阻塞超过默认 HTTP 超时（docker-py 默认 60s+）。虽然包装在 `asyncio.to_thread` 中不阻塞事件循环，但会长时间占用线程池资源。
- 影响评估：Docker 挂起时 sandbox/status 和 sandbox/execute 都变慢，用户感觉卡死。
- 建议修复方式：给 docker client 设置超时 `docker.from_env(timeout=5)`，或添加连接超时配置。

#### [P2] 容器内代码崩溃时 stderr 可能被 Docker 输出污染

- 位置：`backend/src/sandbox/executor.py:93-94`
- 现象：`container.logs(stdout=True, stderr=False)` 和 `container.logs(stdout=False, stderr=True)` 两次调用分别获取 stdout 和 stderr。Docker SDK 文档指出 `logs()` 返回的是合并流，不能保证分离。实际行为取决于容器引擎版本。
- 影响评估：stdout 和 stderr 可能互相污染，用户看到输出错位。
- 建议修复方式：使用 `container.logs(stdout=True, stderr=True, stream=False)` 一次获取合并输出，然后前端自行分离；或升级到 docker-py 支持流分离的版本。

### 3. 边界情况

#### [P2] 并发超过信号量限制时缺乏排队反馈

- 位置：`backend/app/api/sandbox_routes.py:47`
- 现象：`async with _sandbox_semaphore` 在并发满时会让请求等待，但不返回任何排队状态给前端。如果排队超过等待时间（如 FastAPI 超时），用户看到的是连接超时，不知道是因为排队。
- 影响评估：多个用户同时执行沙箱时，多出的请求静默等待无反馈。
- 建议修复方式：添加超时机制 `asyncio.wait_for(_sandbox_semaphore.acquire(), timeout=30)`，超时后返回 429 Too Many Requests。

#### [P2] `execute_code` 中 image 未找到时的错误信息不友好

- 位置：`backend/src/sandbox/executor.py:48-53`
- 现象：`LANGUAGE_IMAGES.get(language)` 返回 None 时返回错误。但 routes.py 的白名单已经检查过 language，正常情况下不会触发。然而如果未来 routes.py 白名单和 executor 不同步，执行器返回的错误只含文字说明，不含 `UNSUPPORTED_LANGUAGE` 错误码。
- 影响评估：防御性检查但错误格式与契约不一致。
- 建议修复方式：统一通过 `SANDBOX_UNAVAILABLE` 或 `EXECUTION_FAILED` 错误码返回。

#### [P3] 超大 stdout 未压缩/分段传输

- 位置：`backend/src/sandbox/executor.py:96-97`
- 现象：截断到 10000 字符，如果代码生成的内容刚好在边界截断，可能破坏 JSON/文本结构。前端无法知道输出是否被截断。
- 影响评估：用户看到截断的输出但没有"输出已被截断"的提示。
- 建议修复方式：在返回值中添加 `truncated: bool` 字段标识是否截断。

---

## 扫描总结

### 严重度分布

| 等级 | 数量 | 关键问题 |
|------|------|----------|
| P0 | 1 | 错误码与契约不匹配（EMPTY_CODE/CODE_TOO_LONG 格式错误 + INVALID_LANGUAGE） |
| P1 | 4 | Docker 不可用未 mock、错误码 INVALID_LANGUAGE 非 UNSUPPORTED_LANGUAGE、stdin socket 泄漏、超时后日志可能不完整 |
| P2 | 8 | 超时值 10 与契约 30 不一致、审计接口未实现、三方 language 列表不同步、捕获所有 Exception 过宽、并发排队无反馈、stdout/stderr 分离不可靠、Docker ping 超时未设置、兜底 shell 执行 |
| P3 | 2 | Optional 未使用、输出截断无标识 |

### 最严重问题（建议优先修）
1. 错误码对齐 — P0（阻断联调）
2. Docker 不可用行为 — P1（影响演示）
3. 审计接口缺失 — P2（契约有但无实现）
4. 超时值统一 — P2（10 vs 30）

