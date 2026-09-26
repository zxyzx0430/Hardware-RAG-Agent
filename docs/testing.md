# 测试与测试数据

> 更新日期：2026-09-26。已重新运行后端和前端测试、前端静态检查及构建。

## 怎么运行

### 后端

Windows PowerShell：

    cd backend
    .\.venv\Scripts\python.exe -m pytest tests

macOS / Linux：

    cd backend
    python -m pytest tests

### 前端

    cd frontend
    npm ci
    npm run test -- --run
    npm run lint
    npm run build

### RAG 黄金集评测

先用 `cd backend`，再运行 `python -m tests.rag_eval.run_golden_eval --validate-only`，只检查黄金集格式。

需要完整评测的参数和流程见 [黄金集评测说明](../backend/tests/rag_eval/GOLDEN_EVAL_README.md)。完整评测会访问运行中的本地服务并生成结果，不属于普通单元测试。

### 多模态模型接口测试

- 测试接口：`https://9router.zxyzx.bbroot.com/v1`
- 模型名称：`Text`；支持多模态，可用于后续聊天及多模态接口联调。
- API key 属于凭证，不保存在本文件或 Git 中；请只在本机应用设置或其他未纳入版本控制的安全配置中填写。
- 记录接口和模型信息不代表模型连通性或具体功能已经通过测试；运行后需单独记录实际结果。

## 哪些测试数据必须保留

- `backend/tests/rag_eval/golden_dataset.yaml`：人工维护的通用黄金问答集。
- `backend/tests/rag_eval/golden_dataset_pdf.yaml`：PDF 专项黄金问答集。
- `backend/tests/rag_eval/golden_dataset_schema.json`：黄金集格式规则。
- `data/test_docs/*.md`：RAG 评测使用的输入文档；这些 Markdown 文件已纳入 Git，应继续保留。

不要把上述黄金集或 Markdown 输入当成运行结果删除。新增可复用样例时，放在测试目录并一并更新说明。

## 哪些是运行后生成的文件

- `backend/tests/rag_eval/run_golden_eval.py` 在 `data/test_results/` 生成带日期的 JSON 和 Markdown 结果，以及调试日志。
- `backend/tests/rag_eval/run_eval.py` 在 `data/test_results/` 生成规则评测结果。
- `scripts/run_baseline_deepeval.py` 和 `scripts/run_baseline_deepeval_v2.py` 在 `data/benchmark/` 生成评测报告、日志和续跑检查点。
- 页面预览、PDF 图像抽取、分块快照等是脚本产物，不是默认自动化测试的输入。

重新运行会产生一份新的结果，不保证还原旧报告里的分数。旧日期的单次评测报告和日志已清理。`.gitignore` 只忽略这些具体结果名称或输出目录，不会忽略整个 `data/` 或 `backend/tests/`。

## 本机保留但不提交的材料

- `data/test_docs/*.docx` 是本机的文档解析样例，保留在电脑上并按现有规则忽略。
- `data/test_results/pdf_golden_dataset.json`、分析快照、页面预览和旧 embedding 备份用途不完全明确，暂时保留在本机并精确忽略；当前自动评测使用上方的 YAML 黄金集。
- `backend/tests/test_html_ingest.py`、`backend/tests/test_ocr_parser.py` 和 `backend/tests/rag_eval/_sse_probe.py` 是被精确规则忽略的本地测试文件。本次未改动或删除它们，也不把它们算作 Git 中的正式测试套件。
- 本地知识库、数据库和用户导入手册属于运行数据，不是源码或测试夹具，不应提交。

## 最近一次有记录的测试结果

| 运行日期 | 命令 | 结果 |
| --- | --- | --- |
| 2026-09-26 | `cd backend; python -m pytest tests -q --tb=short --maxfail=1` | 267 项通过，103 条警告；pytest 缓存写入被 Windows 拒绝，不影响本次测试结果 |
| 2026-09-26 | `cd frontend; npm run test -- --run` | 7 个测试文件、30 项通过 |
| 2026-09-26 | `cd frontend; npm run lint` | 0 错误，13 条 React Hooks 警告 |
| 2026-09-26 | `cd frontend; npm run build` | 构建成功；有 JS 分块超过 500 kB 的提示 |
| 2026-09-24 | `cd frontend; npm run test -- --run` | 6 个测试文件、28 项通过 |
| 2026-09-24 | `cd frontend; npm run build` | 构建成功；有单个 JS 分块超过 500 kB 的提示 |
| 2026-09-24 | `cd frontend; npx eslint src/components/layout/AppRoot.tsx src/components/explorer/ExplorerPanel.layout.test.tsx` | 通过，无 lint 错误或警告 |
| 2026-09-16 | `cd backend; python -m pytest tests -q --disable-warnings --maxfail=1` | 后端 197 项通过；报告注明使用 Python 3.13，非空白数据库环境 |
| 2026-09-16 | `cd frontend; npm run test -- --run` | 前端 7 项通过 |
| 2026-09-16 | `cd frontend; npm run build` | 构建成功；报告记录有大包提示 |
| 2026-09-16 | `cd frontend; npm run lint` | 0 个错误、13 条 Hook 警告 |
| 2026-09-23 | `npm ci` | 隔离环境安装 428 个包成功 |

2026-09-24 的内置浏览器检查：在 1280×720 视口把资源管理器拖至下限后，实际面板宽度保持 220px，四个当前显示的头部按钮各为 32px，按钮行没有溢出；折叠条为 24px，重新展开后仍为 220px。该浏览器检查时没有打开项目根目录，因此项目打开后额外出现的“已删除”按钮由前端组件测试确认存在，并按 5 个图标按钮计算所需宽度；未在浏览器里单独点击该按钮。

2026-09-23 的收口记录另外摘要称后端 206 项、前端 10 项通过，构建和静态检查通过；但没有记下这些检查各自的实际命令，因此这里只保留为历史报告摘要，不冒充可复现记录。本次整理没有重跑这些测试。

## 硬件实机验证

2026-09-26 使用用户确认的 COM5 开发板，完成应用内串口连接、接收实际开发板日志和正常断开检查。未发送串口数据，也未编译或烧录固件。日志出现过 `PSRAM ID read error` 和 BME280 探测失败信息，尚未诊断其硬件或固件原因。固件烧录及烧录后的串口自动重连仍未做实机验证。

## 已知待修复的鲁棒性问题

受控测试中，在聊天回答已开始输出后断开后端连接，页面超过一分钟仍显示生成中，未及时提示连接中断。不能把本次自动化测试通过视为聊天断线恢复通过。另一次测试从首页直接开始的未命名会话刷新后未出现在会话列表中；正式创建的会话刷新后可以保留，前一种路径仍需专门复测。
