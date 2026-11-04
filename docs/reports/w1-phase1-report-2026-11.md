# W1 止血与基线 · 阶段 1 报告（只读诊断）

日期：2026-11-04
仓库：`E:\Desktop\agent`（master `eab62d2` + 71 文件 / +7,428 −536 未提交）
方法：在 `E:\DevCaches\Temp\tmp.wMFML86FVf` 的隔离副本中运行，未写 `backend/data`，未访问真实设备。

## 0. 基线实测（推翻了旧结论）

| 项 | 旧记录 | 本次实测 |
|---|---|---|
| 后端 | 20 failed / 108 passed（`--maxfail=1` 截断） | **815 收集；60 failed / 747 passed；另有 9 例挂起**（`tests/test_guarded_checkpoint_reset_transaction.py`，不返回，全量必卡死） |
| 前端 | 171 passed / 2 failed | 171 passed / 2 failed（确认） |
| `npm test` | 跑不起来 | 确认：`frontend/node_modules` 是 pnpm 布局，无 `.bin` |

干净跑命令（供阶段 2 复用）：

```
--basetemp 必须是 Windows 正斜杠路径；POSIX 路径会产生 178 个假 ERROR
```

## 1. 失败分类表（后端 60 + 挂起 9 + 前端 2）

| # | 归属 | 数量 | 类型 | 根因 | 最小修法 | 估时 |
|---|------|-----:|---|---|---|---:|
| 1 | `src/agent/compact/writer_lifecycle.py:970` | 11 | 测试过期 | 强制 `isinstance(native_control, langgraph.runtime.RunControl)`，测试传 double | 测试改用真实 `RunControl`，或放宽为三方法 duck-typing | 0.5h |
| 2 | `src/agent/context_guard.py:63` | 9 | 真 bug | `get_registered_context_window()` 返回 None 即 `ContextLimitError(0,0)`；显式 `context_window_override` 也无法绕过 | 先判 override 再查注册表 | 1h |
| 3 | `src/agent/sse_adapter.py:271` | 3 | 真 bug | 同 #2 的 SSE 复刻 | 同上 | — |
| 4 | `tests/test_tool_event_body_store.py:173,270` | 7 | 真 bug | `ToolMessage.content` 存成 str（`"{'private_result': ...}"`），读端按 dict | 存取走同一 JSON 编解码入口 | 2h |
| 5 | `tests/test_chat_resume_context_limit_sse.py:87` | 3 | 测试过期 | 期望 `['text','error','done']`，实得 `['error','done']` | 更新期望 | 0.5h |
| 6 | `src/agent/sse_adapter.py:320`、`src/agent/hitl_handler.py:159` | 3 | 真 bug | `KeyError: 'token_limit'`，budget 字段未覆盖所有构造点 | 补齐或改 dataclass | 1h |
| 7 | `tests/test_build_tool.py:180,198` | 2 | 真 bug（安全回归） | `risk_classifier` 改动使 HIGH→LOW、`ask`→`allow` | 回退规则或补用例 | 2h |
| 8 | `tests/test_rag_rank_merge.py:244` | 2 | 真 bug | `bm25-only` 返回空 | 查 BM25 分支 | 2h |
| 9 | `tests/test_autocompact_failure_preservation.py:61,85,129` | 4 | 测试过期 | 期望 42/3/20，实得 1/0/0 | 按新契约重写 | 2h |
| 10 | `app/api/chat_routes.py:872,1168,1621` | 3 | 真 bug | `SimpleNamespace` 无 `max_tokens`；回调缺 `session_delete_epoch`；HITL 快照 409 | 逐条修 | 3h |
| 11 | `context_guard.py:115` + 零散 | ~10 | 混合 | fail-closed 1>0、HITL 正则、SSE 终态断言 | 逐条定位 | 3h |
| 12 | `tests/test_guarded_checkpoint_reset_transaction.py` | 9 | 挂起/环境 | aiosqlite + langgraph，在 pytest-asyncio 1.3 下 `setup()`/reset 永不返回 | 先降级到 0.24 复测再判 | 4h |
| 13 | `frontend/src/stores/useChatStore.context-limit.test.ts:97,137` | 2 | 环境 | 默认 `testTimeout` 5000ms，实跑 8.8s（提升到 30s 后两例均通过） | `vite.config.ts` 加 `test: { testTimeout: 15000 }` | 0.25h |

合计 ≈ 26h。

## 2. 可复现性修复清单

| 问题 | 证据 | 修法 |
|---|---|---|
| npm/pnpm 混用 | `frontend/node_modules/.pnpm/` 存在、`.bin` 缺失；`package-lock.json` 是 npm 的 | 定 npm：删 `node_modules` → `npm ci`；`package.json` 加 `packageManager` + `engines.node>=22` |
| `npm test` 失败 | 同上 | `npm ci` 后自愈 |
| lockfile | 无 `pnpm-lock.yaml`；npm lock 已提交但与磁盘布局不符 | `npm ci` 重建后提交 |
| Python 版本 | `README.md:124` 写 3.10+；`session_search.py:55`、`store_adapter.py:98` 用 `datetime.UTC`（3.11+） | README 改 3.12+，CI 固定 3.12 |
| 依赖漂移 | 实装 fastapi 0.115.6 / SQLAlchemy 2.0.50 / pytest 9.0.3 / pytest-asyncio 1.3.0；`requirements.txt:1,25,26,34` pin 0.115.0 / 8.3.0 / 0.24.0 / 2.0.35 | `pip freeze` 出 `requirements.lock`，CI 用 lock |
| pytest wrapper | `scripts/verify_backend_isolated.ps1:61` 用 `--maxfail=1`；预导入 `pyarrow` 绕 Windows 原生崩溃 | 去掉 maxfail；pyarrow 预导入仅保留在 Windows job |
| 临时 DB 隔离 | `app/db/database.py:18` **import 期** `mkdir backend/data`，即使设了 `SQLITE_DB_PATH`；`tests/test_routes_kb.py:21`、`tests/test_manual_memory_integration_review.py` 直接用 `SessionLocal` | 目录懒创建；两文件改 `tmp_path` |
| 全量挂起 | 见 #12 | CI 接 `pytest-timeout --timeout=120` |

## 3. CI 矩阵（`.github/workflows/ci.yml` 完整内容，未落盘）

```yaml
name: ci
on:
  push: { branches: [master] }
  pull_request:
  workflow_dispatch:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend:
    name: backend (${{ matrix.os }})
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    defaults:
      run: { working-directory: backend }
    env:
      SQLITE_DB_PATH: ${{ github.workspace }}/backend/data/ci-test.db
      AGENT_CHECKPOINTER_TYPE: memory
      PYTEST_DISABLE_PLUGIN_AUTOLOAD: "1"
      HF_HUB_OFFLINE: "1"
      TRANSFORMERS_OFFLINE: "1"
      ANONYMIZED_TELEMETRY: "False"
      PYTHONUTF8: "1"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/requirements.lock
      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.lock
          python -m pip install pytest-timeout==2.3.1
      - name: Warm native imports (Windows import-order crash)
        run: |
          python -c "import pyarrow, chromadb, transformers"
          python -c "from langgraph.runtime import RunControl"
      - name: pytest
        run: >
          python -m pytest tests -q --tb=short
          -p pytest_asyncio.plugin -p pytest_timeout
          --timeout=120 --timeout-method=thread
      - name: Upload failure log
        if: failure()
        uses: actions/upload-artifact@v4
        with: { name: pytest-${{ matrix.os }}, path: backend/.pytest_cache }

  frontend:
    name: frontend (${{ matrix.os }})
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    defaults:
      run: { working-directory: frontend }
    env:
      CI: "true"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npx tsc -b
      - run: npm test
      - run: npm run build
```

注意：
- `requirements.txt` 含 docling/sentence-transformers/platformio/torchcodec，CI 装机 3–5GB；建议另建 `requirements-ci.txt`。
- 首轮必红。先修环境类，再对已知失败加 `@pytest.mark.xfail(strict=True, reason=...)`，让 CI 变"有账可查"而非"全红"。
- 不要配 `--maxfail`，不要跑 wrapper 的子集。

## 4. 死代码 / 临时脚本

| 目标 | 证据 | 风险 |
|---|---|---|
| `src/agent/compact/microcompact.py` | 仅 `compact/__init__.py:17` 导出，无外部调用 | 低 |
| `src/agent/permission_matcher.py` | 全仓零外部 import | 低 |
| `src/agent/plugins/` | 全仓零外部 import | 中：`RedactPlugin` 是密钥脱敏，删前确认审计侧另有实现 |
| `src/agent/store_adapter.py` + `session_search.py` 旧路径 | 仅 `search_history.py` 引用，且 FTS 恒 0 行 | 中：先定 `search_history` 存废 |
| `app/api/tool_events.py`（575 行） | 未进 `app/main.py:336-349` 任何 router | 中：连带 `tests/test_tool_event_body_store.py` 7 例一起删 |
| `backend/_*.py` ×52 | 全部未跟踪；唯一外部引用 `docs/completed-history.local.md:1467` | 低；`_reindex_*`/`_verify_*` 属运维手册步骤，先 `git mv` 到 `scripts/legacy/` |
| 其余 11 个 `backend/test_*.py`/`check_*.py` 等 | `pytest.ini` 的 `testpaths=tests` 已挡住误收集 | 低 |

## 5. 需要主控批准

1. **删除** 6 个死模块 + 52 个 `backend/_*.py`（建议先移动到 `scripts/legacy/`，一轮后再真删）。
2. **改测试** #1/#5/#9（约 20 条断言）——改测试而非改产品。
3. **改产品** #2/#3/#4/#6/#7/#8/#10/#11（8 处真 bug）；`build_tool` 权限放开与 `context_guard` 属安全相关，建议最先修。
4. **提交策略**：修绿前不 commit 到 master；建议先开 `w1-stabilize` 分支给当前工作区落一次快照，再分小提交。
5. **回滚预案**：修不完则按 `docs/reports/agent-maturity-reset-2026-11.md:375` 回退 + cherry-pick 保留桶。

## 6. 阶段 2 待办（不自行开始）

1. #2/#3/#6/#10 context budget 簇（同一根因，4 处）
2. #7/#8 安全与检索回归
3. #4 序列化配对
4. #1/#5/#9 测试更新
5. #12 降级 pytest-asyncio 复测
6. npm 统一 + lock + README 版本 + data 目录懒创建
7. 落 CI、删死代码

## 附：证据文件

- 干净跑日志：`E:\DevCaches\Temp\tmp.wMFML86FVf\run5.log`（60 failed / 747 passed，4m30s）
- 隔离副本：`E:\DevCaches\Temp\tmp.wMFML86FVf\`（可直接复用，未污染 `backend/data`）
