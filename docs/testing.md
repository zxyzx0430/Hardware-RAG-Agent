# 测试与测试数据

> 更新日期：2026-10-05。记录修复后的最终软件门禁，并单列修复前 606 项快照与 67 项失败轨迹；浏览器、硬件和 RAG 全量质量评测仍分别记录，不混作软件测试结论。

## 2026-10-05 最终修复后软件发布门禁

本节记录 2026-10-05 的最终隔离软件门禁。后端在全新的 `copy-full` 副本中完成；00 核对当前源码与运行副本的 247 项 manifest，均无哈希差异。前端最终测试使用隔离副本；前后 160 项文件哈希一致。提交与发布状态以 Git 记录为准。

| 范围 | 实际结果 | 输入快照与限制 |
| --- | --- | --- |
| 后端完整测试 | **608 passed / 0 failed / 0 skipped，193 warnings，191.87 秒，退出码 0**。日志：`04-backend-pytest-r1/logs/full-backend-pytest-fixed.log`。 | 全新 E 盘隔离副本 `copy-full`；当前源码和副本 247 项 SHA-256 均与 manifest 一致。数据库、向量目录、pytest 临时目录及缓存位于隔离副本；没有改 API、权限、KB 行为或依赖。 |
| 前端最终测试 | 2026-10-05 20:36:02 开始，35.64 秒；23 个测试文件、**145 项通过**。 | 独立副本 160 项源码/配置文件哈希一致，报告为 `05-frontend-release-gate/final-rerun-report.md`。不是浏览器验收。 |
| 前端静态检查与构建 | lint 0 errors/13 warnings，TypeScript 与构建退出码 0，827 模块。 | 这些结果来自同一源码的较早检查，不是 20:36 最终前端复跑的一部分；构建有大分块警告。 |

### 导入循环 P1：修复前失败、修复和复核

修复前诊断确认循环依赖：`agent_factory` 初始化经 `context_guard`、`app.api.sse`、`app.api` 回到 `chat_routes`；后者从未完成初始化的 `agent_factory` 导入 `_should_use_agent` 失败后走 fallback，未建立 `create_hardware_agent` 导出。原 03 测试曾报属性缺失；04 的首次窄复核 1 项和完整 `test_chat_source_registry_snapshot.py` 5 项通过，但同一 67 项组随后仍为 **66 passed、1 failed**。RED 证据及诊断值 `FACTORY_CREATE=True`、`ROUTE_AGENT_AVAILABLE=False`、`ROUTE_CREATE=False` 均保留。

04 在 `backend/src/agent/context_guard.py` 中将两个 SSE helper 的导入延迟到函数使用处，并新增两种 fresh-import 顺序回归；修复只涉及这一个源码文件和一条新测试文件，未调整权限、API、KB 语义或依赖。修后两种独立导入顺序 **2 passed**（38.77 秒，3 warnings）；原 67 项组 **67 passed**（61.38 秒，47 warnings）；来源快照文件 **5 passed**（11.69 秒，26 warnings）；最终完整后端套件 **608 passed**。导入顺序 P1 已由这些软件回归关闭。项目外证据：`04-backend-pytest-r1/logs/full-backend-pytest-fixed.log`、`source-copy-hashes-full.tsv` 及对应 fresh-import、67 项和来源快照测试报告。

修复前历史门禁：后端完整测试为 **606 passed / 0 failed / 0 skipped，193 warnings，244.65 秒，退出码 0**；前端 **145 项通过**（23 个测试文件）。该 606 项全量通过与当时独立 67 项组合的 **66 passed、1 failed** 均为实际记录；后者暴露导入顺序缺陷，不能被全量结果或随后单项/5 项窄复核覆盖。最初 RED（`1 failed / 1 passed`）也保留。完整轨迹见 `03-review/pytest-backend-focused.log`、`03-review/release-review.md` 和 `04-backend-pytest-r1/logs/same-67-focused-group.log`。

知识库检索范围沿用当前契约：省略或传空数组的 `kb_ids` 表示检索全部启用知识库，不能用空数组关闭 RAG（[API 契约 §5.1](api-contract.md)）。当前实现与此一致，本轮没有把“空知识库选择”登记为新缺陷，也没有更改其语义。

这些软件回归不代表真实浏览器、模型服务、硬件、全量 RAG 质量、其他新电脑安装或发布流程已完成验收。未在本轮重跑的 `lint`、TypeScript 和 Vite 构建仍只引用同源码的先前记录。

## 2026-10-04 F1 集成软件回归

F1 候选 `RAG-P1-20261004-A-F1` 是 2026-10-04 测试时的未提交工作区快照，HEAD 为 `32e82560d8c7723005b7247482910f1b5f01d579`；此处描述的是当时状态，后续发布状态以 Git 记录为准。运行前后核对冻结清单 422 个文件，缺失 0、哈希差异 0；后端隔离副本的 243 个源码/测试/输入文件及 `backend/pytest.ini` 也逐项匹配清单。副本在独立目录，未复制 `.env`、认证文件、数据库、索引或用户知识库；SQLite、审计、Chroma、上传、会话历史、检查点、临时目录及模型缓存均位于该运行目录，检查点使用内存模式，Hugging Face/Transformers 离线。

全套后端命令由项目外验证脚本执行：

```powershell
& '<external-acceptance-root>/20261004-rag-improvement-control/verify_backend_f1.ps1' `
  -IsolatedRoot '<external-acceptance-root>/F1-6dbff660011f49d186083bedb51d8f03/copy'
```

结果为 **606 passed / 0 failed / 0 errors / 0 skipped，193 warnings，152.06 秒，退出码 0**。日志与运行路径核验 JSON 保存在 `<external-acceptance-root>/F1-6dbff660011f49d186083bedb51d8f03/`。警告主要包括 FastAPI `on_event`、`datetime.utcnow()` 弃用、两项旧测试返回布尔值，以及 Transformers 缓存变量弃用；不影响本次退出码。

首次深层隔离路径运行得到 599 passed、7 个 Git 测试夹具初始化错误：Windows Git 无法创建 260 字符的模板 hook 文件路径（`Filename too long`）。保留原始日志；没有改全局 Git 配置或跳过测试。换用较短的唯一 E 盘根目录后完整重跑，606 项全部通过。前端独立快照另有 16 个文件的 99 项测试通过、lint 0 错误/13 警告、TypeScript 与 Vite 构建通过；源码清单 152 项哈希与聚合哈希均已核对。

以上是自动化软件门禁，不代表浏览器、真实本地 API/RAG 全链路、35 道质量评测、标准评分或真实硬件验收已完成。导入预检提示缺少 FFmpeg shared libraries，因此音视频解码仍未验收；没有下载权重或启动用户服务。

## 怎么运行

2026-10-04 发布检查点：`codex/day1-3-baseline` 的 `32e82560d8c7723005b7247482910f1b5f01d579` 当时已推送并核对远端 SHA，`master` 保持原提交。105 个文件包括源码、回归、公开样例与正式文档；凭证、数据库、索引、缓存、日志及评测产物未提交。提交前清除一个测试文件末尾的多余空行，没有改测试行为；其余后端源码/样例此前与 549 项全绿隔离副本逐文件一致。这是历史发布记录，不预判当前工作区后续状态。

GitNexus 缓存版 1.6.12 的全范围检查识别 105 文件、1,554 符号和 223 个受影响流程，风险 `critical`。全范围的符号列表触及 1,000 条硬上限；随后以两个项目外临时 Git index 分组重跑：已修改 48 文件/561 符号/213 流程，新增 57 文件/993 符号/10 流程，分别为 `critical`、`high`，均退出码 0且无 partial/truncated 提示。没有改工作树或原暂存范围来绕过检查。图谱陈旧和动态调用覆盖限制仍在，不将这些结果称为全量无风险认证。

可选 `tests/mcp_live_acceptance.py` 为本机原生导入顺序使用 PyArrow，项目未单独锁定这个直接依赖；已有主机可运行不等于该脚本在空白环境已验证。DeepEval 的独立判分环境不用于验证此硬件/Agent 验收脚本。

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

2026-10-04 推送后重新运行上述命令：30 道通用题 schema 校验通过，version 1.0、退出码 0、无模型调用；另有 5 道 PDF 专项题，因此历史合并 35 题不是该通用 YAML 的独立题数。

需要完整评测的参数和流程见 [黄金集评测说明](../backend/tests/rag_eval/GOLDEN_EVAL_README.md)。完整评测会访问运行中的本地服务并生成结果，不属于普通单元测试。

修复前的 2026-10-03～04 评测见 [RAG 质量基线第 1–7 节](rag-quality-baseline.md)：35 道正式题已采集，30 道完成来源引用链路；这不是答案正确率。30 个合格候选均尝试 v3 自评诊断，8 个有效、21 个评分超时、1 个 API 错误，8 个子集加权平均 85.31，不能代表全量质量分。题库/资料错误已在后续 P0 中校准，但没有据此重写旧答案或旧分数；修复后证据另见该报告第 8 节。当时 DeepEval 标准四指标完整样本为 0；后续部分完整分见第 10 节，仍无全量标准总分。修复前实际重排器未加载，结果属于降级路径。

### 2026-10-04 独立 DeepEval 环境

软件快照推送后，在项目外隔离目录建立独立评测环境。解释器为应用自带的 Python 3.12.14，不下载或替换全局 Python；DeepEval 固定 1.5.5。判分环境使用 LangChain 0.2.17 / core 0.2.43 / openai 0.1.25 和 Ragas 0.1.21 的旧依赖 API；不装后端的 Torch/Docling 等完整运行栈。安装退出码 0，`pip check` 返回 `No broken requirements found`，104 行依赖锁保存在项目外目录，SHA-256 为 `3141cc3cfd3dafbfe36272b3be54736118e648e9c593044b6697068275d125ec`。缓存、安装日志与临时文件均在该隔离目录；当时的系统 Python 3.13.13 / DeepEval 4.0.7 未改动。

`backend/requirements-eval.txt` 已改为仅安装判分依赖，不再包含 `-r requirements.txt`。安装成功不代表评分成功；Text 自定义适配和原生四指标的实际验证结果另记。此配置与本节结果文档是推送之后的本地改动，不冒充已包含在 `32e82560` 的远端文件。

在上述环境中，项目现有 `preflight_deepeval_runtime()` 实际返回 `python=3.12, deepeval=1.5.5`，四个原生指标均可导入；提前设置 `DEEPEVAL_TELEMETRY_OPT_OUT=YES`，仅在这次离线导入检查的上下文中阻断 DeepEval 的 PyPI 更新查询，退出后还原，没有改第三方包代码。原有 `test_metric_failures.py`、`test_report_denominators.py`、`test_eval_deadlines.py` 三文件另跑 **33 项通过、1 条警告、0.98 秒、退出码 0**，无模型请求。为避免导入后端认证 fixture 或插件副作用，使用 `--confcutdir=.../backend/tests/rag_eval` 和 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；警告是关闭插件后已有 `asyncio_mode` 配置不识别，不是跳过测试。结果保存为项目外 `protocol-tests.log`。这组纯评分协议测试不代替完整后端 549 项或真实原生判分。

Luna/max 实现项目外 `saved_deepeval.py` 与配套测试，采用实际 `Text` 身份的 `DeepEvalBaseLLM`，不伪装为 OpenAI 模型名；schema 返回实例、请求不重试、共享 40 次 HTTP 上限、单指标 90 秒、单响应 4,096 token。主控另跑 unittest：**7 项通过，0.354 秒、退出码 0**，四个原生 `a_measure` 都用假响应实际计算完成，另验非法格式不重试、请求封顶与取消。执行线程的同组 pytest 也为 7 项通过、5 条依赖弃用警告，不能合计成 14 个不同测试；首次自动加载插件曾触发非收费 PyPI 版本查询，随后禁用插件复测阻断查询，全程零 judge 请求。主控的评分 driver 另核对提交、题库/回答 hash 和成功检索的完整父上下文，dry preflight 有 3 个合格样本、0 模型调用。

首次真实原生判分运行 `grade-run-20261004T034214Z-353ef5ed`：G001/G023/G026 共 **3 planned / 3 eligible / 3 attempted / 0 scored / 3 failed**，四指标各 0/3 有效，全部为 `format_failure`，标准总分为 `null`，不得解释为 RAG 质量 0 分。实际 judge HTTP 尝试 **15 次**，没有重试或重新生成回答，脚本退出码 1；失败原文输入、结果分母与日志保留在项目外目录。评分器格式适配需继续核对，后续修复验证不覆盖这次失败成绩。

后续明确非流式请求与完整 JSON 代码块解码，适配器测试 9 项通过；两次格式诊断共 2 次 HTTP，不生成质量分。G001/G026 两题的第二轮实际 16 次 HTTP：召回及精确度均各 2/2 有效、分数 1.0，忠实性及回答相关性各 0/2 有效，两个完整总分均 `null`，退出码 1。再增加 SSE 与长度截断回归后外部适配器 **11 项通过、0.852 秒**；只针对 G001 的两个失败维度做流式探针，单指标 180 秒、单 HTTP 读取 90 秒、额度最多再 7 次。实际 4 次 HTTP，两个指标均失败、退出码 1：回包均 HTTP 200/SSE，各指标出现 `finish_reason=length`，在 4,096 token 预算下被判为不完整而拒绝计分。合计 **37 次 judge/诊断 HTTP 尝试**，不混同旧聊天或 Embedding 调用数。原生评分完整样本仍为 0；没有用跨版本好分数拼总分。用户中止时该探针已自然结束，确认无该评分进程继续运行，未再发请求。具体报告和未验证边界见 [质量报告第 9 节](rag-quality-baseline.md#9-2026-10-04-发布与独立-deepeval-验收)。

### 2026-10-04 四指标重评和提示词优化

产品只增加完整证据阅读、关键限制、逐句引用及检索内容不作为指令的规则，新增 `test_rag_answer_prompt.py`，没有改题库、指标公式、权重或工具权限。使用已有系统 Python 建立新的 E 盘源码/SQLite/索引隔离副本，实际命令：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-score-optimization/backend-regression `
  -TestPaths @('tests', '-vv', '-o', 'faulthandler_timeout=120')
```

结果 **550 collected / 550 passed / 0 failed / 0 skipped，183 warnings，227.86 秒，退出码 0**。副本为该目录下 `hardware-rag-verify-434078a12a4744c88d777f3fc3b13855`；新提示词和测试文件与当前工作区 SHA-256 一致。本轮没有前端、浏览器、新电脑或硬件重验。

项目外 `collect_optimized.py` 复用只读真实问答流程；准备版本 `071038Z-72834b67` 在计费前累计台账核验失败，零模型请求，保留。正式新 `live-smoke/runs/20261004T071141Z-03fb7ee6`：三份 MD 共 457 片段入库；4 planned / 4 attempted / 4 completed，三题来源链路 3/3、严格内容检查 2/3、空库 1/1；退出码 1 来自内容检查而非传输错误。完整父上下文分别 5/5/10 段；自有服务 PID 2896 已退出，58087 空闲，cleanup verified。累计应用问题尝试为 13，内部模型与 Embedding 次数未计量。

Text 外部适配器和原生公式对照最终 **16 项离线 unittest 通过、1.223 秒、退出码 0**，包括输出截断拒绝、结束标记后连接不结束、取消和请求上限；无真实 judge 调用。项目外 driver 的默认行为只预检，显式 `--run-live` 才读取本机未跟踪凭证和调用模型。最终使用命令如下；这是付费实网操作，重跑会新建记录且不保证相同分数，不属于普通自动测试：

```powershell
$evalPython = '<external-acceptance-root>/20261004-release-deepeval/.venv-eval/Scripts/python.exe'
& $evalPython '<external-acceptance-root>/20261004-release-deepeval/grade_rag_run.py' `
  --answer-run '<external-acceptance-root>/20261004-rag-p0/live-smoke/runs/20261004T071141Z-03fb7ee6' `
  --max-output-tokens 32768 --judge-effort low --run-live
```

四项仍用 DeepEval 1.5.5 原生类，30/25/25/20 是项目已有加权方案，不是 DeepEval 统一规定的默认权重。`include_reason=False` 仅省去额外总结请求，保留原生事实抽取、逐条 verdict 和公式；每指标 600 秒、单 HTTP 读取 90 秒、每轮最多 40 次尝试、不重试、无输入截断。客户端请求 low 不证明上游实际配置。

| 保存运行 | 答案版本 | planned / eligible / attempted / scored / failed | judge HTTP | 有效完整子集均值 |
| --- | --- | --- | --- | --- |
| `grade-run-20261004T064036Z-0f04ca97` | 旧 | 3 / 3 / 3 / 0 / 3 | 26 | 无 |
| `rag-grade-20261004T070429Z-b4c65c22` | 旧 | 3 / 3 / 3 / 2 / 1 | 20 | 100，仅 2/3 |
| `rag-grade-20261004T072004Z-7ce19b16` | 新 | 3 / 3 / 3 / 2 / 1 | 21 | 93.47，仅 2/3 |
| `rag-grade-20261004T074755Z-5a13c032` | 同一批新答案、请求 low | 3 / 3 / 3 / 1 / 2 | 19 | 100，仅 1/3 |
| `rag-grade-20261004T081323Z-5db5cac6` | 用户确认网络恢复后的同配置单轮重测 | 3 / 3 / 3 / 3 / 0 | 21 | 95.51，三题全部完整 |

恢复前的四轮均退出码 1，均不是三题完整或全量质量验收。另一次旧 G023 单项低推理开销探针用 3 次 HTTP、200.969 秒完成，忠实性 1.0，不参与均值。恢复前检查点合计 **89 次 judge/诊断尝试**，上一小节 37 次另计，该检查点独立环境累计 126；不等于实际计费请求数，也不包含应用/Embedding 次数。

恢复前统一配置轮明确出现三次 HTTP 503 与一次传输错误；失败保留为空值，不等于质量零分，不跨运行补分。用户确认短暂网络问题后，授权再用相同配置重测一次：最后一行实际退出码 **0**，四指标各 3/3 有效，21 次 HTTP 均为 200/stop。指标均值按百分制为召回 100、忠实性 97.62、回答相关性 100、检索精确度 80.56；每题先保留两位再平均，项目加权均分 **95.51**。这是三题开发子集数值达标，不是全量验收，UART 解释错误和 ESP32-S3 精确度 41.67 仍在。

新增 21 次后本节继续评分合计 **110 次 judge/诊断尝试**，与前一检查点的 37 次累计 **147 次**；应用提问没有重发，仍为 13 个历史累计问题尝试。适配器与公式对照重复复测仍为 16 项通过、1.538 秒，不将两轮同名测试相加。所有判分进程已自然结束，未继续循环刷分。完整报告和逐条审查在 `20261004-release-deepeval/` 各对应目录内 `report.json`。新问题、判分波动与总体置信度边界见[质量报告第 10 节](rag-quality-baseline.md#10-2026-10-04-四指标重评与证据边界优化)。

### 多模态模型接口测试

- 测试接口地址保存在本机未纳入版本控制的设置中；本文件不记录服务地址或密钥。
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
| 2026-10-03～04 | 本机项目外 `20261003-rag-quality/live_quality.py`：独立源码/数据库服务、真实上传索引、`collect`、`rubric_full`、`cache_probe`；参数和材料见 [质量基线](rag-quality-baseline.md) | 7 份固定输入均索引，共 1,228 片段；普通题已保存 30/30、引用链路 25/30，PDF 已保存 5/5、链路 5/5；另 2 个缺资料探针。2 次空输出错误、2 次本轮 300 秒截止、1 次无有效 KB 引用未算成功。v3 诊断 8/30 有效、21 次超时和 1 次 API 错误，标准总分不可用。删除合成 KB 后仍返回缓存内容已复现；没有修改产品代码、原知识库或硬件，非浏览器/启用重排器后的质量验收 |
| 2026-10-03 | `./scripts/verify_backend_isolated.ps1 -Python <python-executable> -TempRoot <external-acceptance-root>/20261003-rag-quality -TestPaths tests/rag_eval` | 18 项通过、30 条警告，34.32 秒，退出码 0；证据/评测报告逻辑回归，不是 18 个真实模型问答的质量通过 |
| 2026-10-03 | `./scripts/verify_backend_isolated.ps1 -Python <python-executable> -TempRoot <external-acceptance-root>/20261003`（本轮最终源码，包括 MCP 停止后的排队请求保护） | Git 范围正式后端测试：457 项通过、183 条警告，退出码 0，98.87 秒；隔离副本不含被忽略的本地探索测试，不是新电脑安装验收 |
| 2026-10-03 | `cd frontend; npm test`（本轮最终前端） | 16 个文件、79 项通过，退出码 0，24.95 秒；组件及状态/API 模拟测试，不是浏览器验收 |
| 2026-10-03 | `cd frontend; npm run lint`（本轮最终前端） | 0 错误、13 条已有 React Hooks 警告，退出码 0 |
| 2026-10-03 | `cd frontend; npm run build`（本轮最终前端） | 824 个模块构建成功，退出码 0，11.77 秒；仍有超过 500 kB 的分块提示 |
| 2026-10-03 | `cd frontend; npm run test -- --run`（Skills 删除确认和请求类型补齐后） | 14 个文件、62 项通过，退出码 0，8.72 秒；组件/API 模拟测试，不是浏览器验收 |
| 2026-10-03 | E 盘隔离副本：`tests/test_skill_runtime_review.py`、`tests/test_chat_skills_integration.py` | 26 项通过、25 条警告，15.25 秒；包括真实 SkillsRuntime 下伪造工具派发、MCP 来源标记、KB 范围、缺少运行时及模式不匹配均未执行 spy 工具；之后还有工具停用交集复核 |
| 2026-10-03 | E 盘隔离副本：记忆数据库集成、SkillsRuntime、技能平台及独立 HTTP/格式回归 | 64 项通过、28 条警告，17.59 秒；不是本轮最终全量 |
| 2026-10-03 | `./scripts/verify_backend_isolated.ps1 -Python D:\python\python.exe`（Skills/聊天记忆仍在实施时的源码快照） | Git 范围正式后端测试：317 项通过、116 条警告、退出码 0，104.87 秒；包含新增的 10 项记忆保存测试，不含被忽略的本地探索测试，不代表本轮新功能已验收 |
| 2026-10-03 | `cd frontend; npm run test -- --run`（记忆界面完成、Skills 界面仍在实施时） | 10 个文件、42 项通过、退出码 0；静态检查 0 错误、13 条已有警告。此检查点不作为最终构建/界面验收 |
| 2026-10-03 | 隔离源码副本中，按下方受控包装方式执行 `pytest tests -q --tb=short --maxfail=1 --disable-warnings` | 日期 1–3 历史工作区全量：331 项通过、104 条警告、退出码 0，98.84 秒；包含当时复制的 24 项未纳入 Git 的 HTML/OCR 本地测试，不能与现在的 Git 范围套件直接比较项数 |
| 2026-10-02 | 隔离源码副本中，以预先导入 PyArrow、禁用自动插件并显式加载 `pytest_asyncio.plugin` 的方式执行 `pytest tests -q --tb=short --maxfail=1 --disable-warnings` | 后端改动后全量：327 项通过、104 条警告、退出码 0；本次未再输出原生崩溃栈。具体包装命令与环境边界见下方 |
| 2026-10-02 | `cd frontend; npm run test -- --run` | 改动后：8 个测试文件、34 项通过；包含失败状态与停止轮询的新回归 |
| 2026-10-02 | `cd frontend; npm run lint` / `npm run build` | 改动后：0 错误、13 条已有 Hooks 警告；构建成功，仍有超过 500 kB 的分块提示 |
| 2026-10-02 | `python -m tests.rag_eval.run_golden_eval --validate-only` | 30 个样本格式校验通过；没有调用模型或产生质量分数 |
| 2026-10-02 | `tests/test_routes_tool.py` 与 `tests/test_tool_api_permission_gate.py` 定向回归，使用下方隔离包装方式 | 20 项通过、33 条警告；隔离测试间工具注册表状态，并验证高风险直接调用仍被确认门禁拦截 |
| 2026-10-02 | `python -m pytest tests/test_hitl_resume_fail_closed.py tests/test_local_binding.py tests/test_chat_routes_resume_terminal.py tests/test_tool_api_permission_gate.py -q --tb=short` | 04 线改动后回归：56 项通过、28 条警告；进程启动前设置临时 `SQLITE_DB_PATH` |
| 2026-10-02 | `python -m pytest tests/test_hitl_resume_fail_closed.py tests/test_local_binding.py tests/test_chat_routes_resume_terminal.py tests/test_tool_api_permission_gate.py tests/test_git_snapshot_undo_isolation.py tests/test_tool_router_request_spec.py tests/test_agent_factory_request_tool_config.py tests/test_kb_index_status_isolated.py -q --tb=short --maxfail=1 --disable-warnings` | 00 改动后复核：72 项通过、27 条警告；临时源码副本与临时 SQLite。系统 Python 另输出 PyArrow 原生 access violation 信息；虽退出码为 0，不能据此认定运行环境完全可靠 |
| 2026-10-02 | `cd backend; python -m pytest tests/test_tool_api_permission_gate.py -q --tb=short --maxfail=1` | 实施前基线：15 项通过，26 条警告；使用系统 Python 3.13.13，未运行新增续跑/监听地址测试 |
| 2026-10-02 | `cd frontend; npm run test -- --run` | 实施前基线：7 个测试文件、33 项通过 |
| 2026-10-02 | `cd frontend; npm run lint` | 实施前基线：0 错误、13 条已有 React Hooks 警告 |
| 2026-10-02 | `cd frontend; npm run build` | 实施前基线：构建成功；仍有 JS 分块超过 500 kB 的提示 |
| 2026-09-26 | `cd backend; python -m pytest tests -q --tb=short --maxfail=1 --disable-warnings` | 268 项通过，103 条警告；增加 Agent 心跳测试 |
| 2026-09-26 | `cd frontend; npm run test -- --run` | 7 个测试文件、33 项通过；新增聊天超时后结束生成并保留部分回答的测试 |
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

2026-10-02 本机环境检查发现根目录旧 `.venv` 指向另一 Windows 用户目录，启动报 `uv trampoline failed to spawn Python child process`；`backend/.venv` 当时不存在。本轮改动前检查使用可运行且已安装依赖的系统 Python，未覆盖或删除旧环境，也不将这一结果称为新电脑全新安装验收。上方安装步骤仍应在正确创建的项目隔离环境中执行。

本轮早期测试有未设置临时库的导入：`app.main` 模块级创建应用会初始化数据库并清理旧审计，HITL 状态初始化会按测试会话 ID 查询数据库。已停止这种测试方式，后续通过的回归采用临时库或完整临时源码副本。只读检查未发现原数据库文件大小或修改时间变化，但这不能证明没有行级影响；不将早期运行写成“完全未接触本机数据”。

### 2026-10-02～03 隔离回归的运行方式与限制

可用 `./scripts/verify_backend_isolated.ps1 -Python <可运行的 Python 路径>` 重现受控源码副本回归。脚本只复制 Git 已跟踪和未被忽略的后端源码、正式测试及 Markdown 夹具，不复制 `.env`、凭据、原数据库或本地探索测试；使用新建的临时数据目录。用 `-TestPaths tests/test_manual_memory_settings.py` 可执行定向测试。保留副本用于诊断，不覆盖旧虚拟环境；它不是新电脑安装验证，也不是网络/浏览器/硬件验收。

本轮后期 C 盘空间耗尽，测试改为 `-TempRoot <external-acceptance-root>/20261003`。每次创建独立源码副本、SQLite、`TEMP/TMP` 和 Pytest base；只搬移本轮自己创建的已完成临时副本到该目录保留诊断，未清理用户缓存或私有材料。不要并行启动会扩大系统缓存的 GitNexus `pnpm dlx` 调用。

2026-10-03 本轮记忆保存补丁及消息持久化定向回归：上述脚本运行 `tests/test_manual_memory_settings.py`、`tests/test_crud_message_idempotency.py`，13 项通过、42 条警告、退出码 0。包括严格类型/4,000 字符边界、替换/清空/读取、非法值整批不更新，以及原消息幂等行为；此时 Skills、Agent 记忆注入和界面仍在实施，不据此宣称整项验收。

测试目录是系统临时目录中的源码、正式测试和固定 Markdown 样例副本；没有复制项目 `.env`、真实数据库或用户手册。测试使用临时 `SQLITE_DB_PATH`，`AGENT_CHECKPOINTER_TYPE=memory`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，不下载模型或使用原知识库。

系统 Python 的默认插件/导入顺序曾触发 PyArrow 原生 access violation。最终全量回归在同一 Python 3.13.13 中禁用无关的全局自动插件，显式加载异步测试插件，并在 Pytest 前导入 PyArrow：

```powershell
# 在准备好的隔离源码副本 backend 目录内运行。
# SQLITE_DB_PATH 必须在 Python 启动前指向该副本的新临时数据库。
$env:SQLITE_DB_PATH = Join-Path (Get-Location).Path ('data/test-' + [guid]::NewGuid().ToString('N') + '.db')
$env:AGENT_CHECKPOINTER_TYPE = 'memory'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:ANONYMIZED_TELEMETRY = 'False'
python -c "import pyarrow,pytest; raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','-q','--tb=short','--maxfail=1','--disable-warnings']))"
```

这次进程通过且未再输出原生崩溃栈，但只是可靠完成了上述受控回归，不代表旧虚拟环境已修复、所有导入顺序都正常，或 README 在新电脑上的安装已重新验收。中途全量运行曾在 236 项通过后因共享工具注册表污染失败；修正测试夹具后重跑全量通过，没有跳过失败测试或放宽产品权限。

### 2026-10-02 浏览器与真实模型检查

- 隔离 API 绑定 `127.0.0.1:58080`，健康检查成功；前端绑定 `127.0.0.1:5173`。这些服务使用临时数据，不是原知识库。00 的内置浏览器初始化失败后，由 02 线使用可工作的浏览器完成下列检查。
- 索引成功的固定文档：实际打开片段预览，看到第 0 个片段、第 1 页及测试正文。
- 未配置 Embedding 的临时库：实际看到“索引失败”和“文档已分块但不可检索。配置后请重新上传”；3 秒后仍是稳定失败终态，没有持续索引加载。是否停止后台轮询另由前端自动化测试确认，不能只凭这 3 秒观察推断。
- 03 线用 `Text` 聊天模型和独立的 `text-embedding-v4` 向量模型，对临时合成文档运行 3 个固定问题。三问均记录到 `search_docs`、匹配该临时知识库/文档的 `src1` 来源和成功结束事件；两问完整回答分别确认 SDA=GPIO8 / SCL=GPIO9，以及第 9 个时钟 ACK 拉低 SDA / NACK 保持高电平。另一问的回答记录截断，只能确认部分内容，不能计为完整答案验收。
- 这不是完整黄金集或 DeepEval 质量评分，也没有验证引用的逐句语义忠实性。真实请求的凭证仅在本机临时服务配置中使用，不写入正式文档或 Git。
- 聊天界面的来源引用展示仍未验收：浏览器设置接口返回 401，本轮没有强制填写凭证、清空用户设置或扩大任务修复认证问题。

2026-10-03 补齐策略入库失败、聊天 HTTP 超时和连续失败的报告统计。新回归测试在旧版隔离副本上确实复现“两次失败只统计一次”，随后修正计数累加；不把已尝试和未尝试的题目混在一起。01 线完成补丁后因任务额度限制中断，00 接手复核和最终套件验证。

浏览器验收结束后，00 已关闭自己启动的两个临时进程，并确认 58080、5173 不再监听；未停止其他用户服务。测试资料保留在项目外临时目录，未删除真实知识库、旧环境或用户材料。

## 2026-10-04 RAG P0 修复检查

这些结果来自 `9bd7e292` 之后、版本提交前的隔离工作区测试；后续发布提交和远端分支以 Git 记录为准。执行任务显式使用 `gpt-6-luna` / `max`，主控按事先列出的验收条件复核；验收计划与产物保存在项目外目录。定向结果不能相加当作全量数量。

| 检查点 | 实际结果 | 边界 |
| --- | --- | --- |
| `test_webfetch_failure_envelope.py`，隔离源码与受控 HTTP 响应 | 8 项通过、25 条警告，12.46 秒，退出码 0 | 真实 ToolRouter 包装失败/超时，保留成功和普通字典语义；不是外网网页兼容性验收 |
| 四组事实校准后的 `tests/rag_eval` 检查点 | 22 项通过、30 条警告，退出码 0 | 包括 MODER/NVIC/UART/ESP32-S3 确定性检查，独立重算 65 行 UART 表；不是整个知识语料的认证 |
| 缓存新鲜度、索引响应、原索引状态和原缓存兼容联合 | 29 项通过、29 条警告，26.49 秒，退出码 0 | 单进程范围；受控阻塞期间实际 ASGI 健康/列表可响应，非真实大文档性能测试 |
| 评测协议最终 `tests/rag_eval` | 49 项通过、30 条警告，24.79 秒，退出码 0 | 受控评分超时、非法值、上下文、客户端期限与分母回归，没有真实模型评分调用 |
| 首轮/续跑终态、Agent 空流/无进展和 Skills/MCP/HITL 八文件联合 | 77 项通过、25 条警告，14.28 秒，退出码 0 | 此后全量揭示旧公开流式入口兼容问题；77 项不等于最终全量通过 |
| 最终前端 `npm test` | 16 个文件、86 项通过，8.82 秒，退出码 0 | store/组件/API 模拟测试，不是浏览器实际点击或断网验收 |
| 最终前端 `npm run lint` | 0 错误、13 条警告（7 个文件），退出码 0 | 既有 Hooks 警告保留 |
| 最终前端 `npm run build` | 824 个模块构建成功，构建阶段 5.55 秒，退出码 0 | 仍有超过 500 kB 的分块提示；不是新电脑安装 |
| 首次最终后端全量 | 97 项通过、1 项失败、63 条警告，30.01 秒，退出码 1 | `--maxfail=1` 后未运行的测试不能算通过；`test_chat_long_term_memory.py` 找不到模块公开 `stream_agent_to_sse` 属性，交回实现线程修复，不跳过旧测试 |
| 恢复公开流式入口后的七文件联合 | 111 项通过、25 条警告，48.80 秒，退出码 0 | 包含原记忆快照失败、正常两种导入顺序、模拟可选依赖缺失、wrapper 关闭/取消、流/HITL/MCP/Skills；真缺依赖仍走 fallback，不强制启用 Agent |
| 第二次后端全量 `integrated-final2` | 约 66% 后继续输出若干进度点，随后持续占用 CPU 且无终态；主控授权精确停止该轮 PID，进程回收退出码 1 | 记为挂起/人工中止，不捏造通过数量，不算完整通过。副本 `hardware-rag-verify-ca27e84d38db4951b91b7a4d0e0d8479` 保留；只停止经独有源码 GUID 核验的测试 PID 23652，未停止用户服务 |
| 第三次详细后端全量 `integrated-diagnostic`，额外参数 `-vv -o faulthandler_timeout=120` | 停在约 75% 的 `test_routes_kb.py::TestKbUpload::test_upload_success_returns_doc_id_and_status`；120 秒堆栈确认 AnyIO portal 正等待退出、后台停在 `_run_index_worker` 的 runner cancel-all 路径 | 已停止自有诊断进程；副本 `hardware-rag-verify-a70b7635c12c480ab2e4a7f8f7442326` 保留。不是全量通过；需要修正被取消 worker 的循环等待，并保留实体线程完成前不释放索引许可的约束 |
| 索引 runner 取消修复后的联合回归 | 35 项通过、10 条警告，26.71 秒，退出码 0 | A29 的四文件、新增 runner/上下文/异常回归及原 TestClient 上传用例；许可和 active 文档在实际线程结束前仍保留，不误报完成 |
| 索引取消补丁后的全量 `integrated-final3`，`-vv -o faulthandler_timeout=120` | 收集 545 项：537 项通过、1 项失败、183 条警告，132.50 秒，退出码 1；最后 7 项因 `--maxfail=1` 未执行 | 索引取消与原上传均通过；首个失败是 HTTP 404 的依赖日志脱敏断言，业务 `EXEC_ERROR` 正确，不能称全量通过。副本 `hardware-rag-verify-14c796aec9014b5d980de5c185877788` 保留 |
| WebFetch 请求范围 HTTPX INFO 脱敏 | 12 项通过、25 条警告，Pytest 11.77 秒，退出码 0 | 旧实现实测 1 项失败；原 8 项保留并强化 INFO 检查。双 WebFetch 重叠、真实 MockTransport 旁路请求、异常/取消清理有回归；不全局静音日志，不改变权限或成功结构 |
| 最终完整后端 `integrated-final4` | 549 项全部通过、183 条警告，Pytest 133.35 秒，退出码 0 | 自然结束，未跳过上述失败测试。主控逐文件比对源码/测试/公开 MD 共 236 文件 SHA-256，当前工作区与副本 0 差异；不是浏览器、真实模型或固定依赖空白安装验收 |

上述 404 回归使用受控 HTTP 响应，没有外网请求。工具自身错误消息和 warning 未泄漏 URL，但当时 HTTPX INFO 仍输出合成查询参数，不能把自身日志安全泛化为依赖日志安全；另有关闭日志 handler 的诊断，不能用它掩盖脱敏失败。已用 ContextVar 标记请求范围，锁/引用计数管理临时 HTTPX logger filter；A 结束后 B 的记录仍脱敏，父任务非 WebFetch HTTP 请求保留原日志，最后一条抓取退出才移除 filter。红灯日志 `webfetch-httpx-red-green/red-direct-8c1f79a771dd4893ba6befc579f66f24.log` 保留（1 项失败、25 条警告、11.99 秒），最终绿灯日志 `green-concurrent-11dbc42cebb549ab8f0b5ec2857feace.log` 和副本 `hardware-rag-verify-6b00fc42f9a54f04b23a72cbae491d8c` 保留。范围是当前 WebFetch 的 HTTPX URL 日志，不承诺所有第三方日志均已脱敏；修复后全量另记。

最终完整后端命令（在当前 PowerShell 直接调用，不再嵌套外层 shell）：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/integrated-final4 `
  -TestPaths @('tests', '-vv', '-o', 'faulthandler_timeout=120')
```

完整日志 `integrated-final4/backend-full-43ccbd13dd064eebbc06258dd1f1149c.log`，副本 `hardware-rag-verify-2c997524dab04c68aab2e0063960f5b7`。549 项是全量实际执行数，不是相加定向结果。真实模型四问只在这一轮绿色及源码哈希比对后授权，结果另记。

缓存/索引检查使用下面的精确参数；多路径必须作为 PowerShell 数组传递，不能把一串逗号参数误当作全部执行：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/final-verified2 `
  -TestPaths @('tests/test_rag_cache_freshness.py', 'tests/test_kb_index_responsiveness.py',
    'tests/test_kb_index_status_isolated.py', 'tests/test_task4_parallel_cache.py')
```

上述 77 项聊天联合回归的路径为 `test_agent_stream_reliability.py`、`test_chat_stream_termination.py`、`test_chat_routes_resume_terminal.py`、`test_hitl_resume_fail_closed.py`、`test_chat_skills_integration.py`、`test_mcp_agent_integration.py`、`test_resume_snapshot_integration_review.py`、`test_sse_adapter_heartbeat.py`，均在 `backend/tests/`。导入兼容修复后使用新副本重新验收，不能沿用这个检查点作为最终源码证明。

公开入口兼容与显式关闭 wrapper 的最终联合命令为：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/stream-wrapper-final `
  -TestPaths @('tests/test_chat_stream_termination.py', 'tests/test_chat_long_term_memory.py',
    'tests/test_agent_stream_reliability.py', 'tests/test_hitl_resume_fail_closed.py',
    'tests/test_mcp_agent_integration.py', 'tests/test_chat_skills_integration.py',
    'tests/test_skill_runtime_review.py')
```

这次副本为 `stream-wrapper-final/hardware-rag-verify-ba361243c99749b1b9db6c8b64c8b7f9`。恢复模块公开可 patch 的惰性入口，不改 `app/api/__init__.py`，正常结束/异常/调用者关闭或取消均显式关闭内层 adapter。此后的全量另用新副本；定向数量不替代全量。

全量回归进程启动前清空测试进程的 API 凭证环境变量，使用上方隔离脚本的新源码副本、新 SQLite/Chroma 和 E 盘临时目录；不复制 `.env`、加密 key、凭据 JSON 或原数据库，不使用用户手册。第一次失败副本保留于 `integrated-final/hardware-rag-verify-c9797f2793874ff7a11fbf453eaffd10`。没有安装/升级依赖、删除原材料、提交或推送。

评测线程的 49 项检查另用项目外隔离副本，运行 `pytest tests/rag_eval -q -p no:cacheprovider`，将 `config.TEST_DOCS_DIR`、`config.OUTPUT_DIR` 和黄金评测 `_OUTPUT_DIR` 指向该副本。复制阶段曾机械复制 `app/db/.enc_key`、`keys_store.json` 及备份，三份副本在 Pytest 启动前已精确删除；没有查看/打印/解析或用于认证，原文件不改动。不能将这一过程描述为“凭据从未被复制”。后续全量回归采用 Git 白名单复制，已核对这些凭据不属于复制范围。

标准质量评分在当前 Python 3.13.13 / DeepEval 4.0.7 环境预检失败；标准路径要求 Python 3.10～3.12 / DeepEval 1.5.5 和异步评分接口。本轮没有更换全局环境。协议回归通过与“取得完整标准质量总分”是两件事。

索引取消死循环修复只调整 `_run_index_worker` 及其回归：改用受 shield 保护、不会被 runner 当作 Task 一并取消的 executor Future，并显式复制 `contextvars`。实体线程结束前不归还索引名额；已完成 Future 的异常通过 `result()` 传播，避免线程自身 `CancelledError` 再被无限循环处理。旧 helper 的 AST 子进程红灯确实打印线程启动、runner 已取消及释放前快照后超时，父测试终止并回收自己的 PID 14440；不是依赖导入慢造成的假红灯。修复后的精确联合命令为：

```powershell
./scripts/verify_backend_isolated.ps1 -Python <python-executable> `
  -TempRoot <external-acceptance-root>/20261004-rag-p0/resumed-cancel-fix `
  -TestPaths @('tests/test_rag_cache_freshness.py', 'tests/test_kb_index_responsiveness.py',
    'tests/test_kb_index_status_isolated.py', 'tests/test_task4_parallel_cache.py',
    'tests/test_routes_kb.py::TestKbUpload::test_upload_success_returns_doc_id_and_status')
```

副本 `resumed-cancel-fix/hardware-rag-verify-d319c566e8a140d28e2bfc094dd53555` 保留。这次原上传测试未修改，没有通过 mock、skip 或提前释放实体线程掩盖问题。线程重复取消、上下文、线程自身取消异常/普通异常、文档状态和 active 生命周期都有回归。导入流程共用该 helper，但没有单独做真实 `kb_import` 端到端验收；修复后的最终全量另记。

### 真实模型小样本与失败保留

项目外 `live-smoke/prepare_and_run.py --run-live` 只使用已授权 Text 凭据在独立源码、数据库、上传与向量目录执行，计划 G001/G023/G026 和 EMPTY1 四个固定样本，顺序请求，无评分模型。测试 fixture 仅开放 `search_docs` / `list_kb_docs` 并保留原禁用过滤；不开放硬件、文件、网络或 MCP 工具，不替换产品提示词。客户端总期限 300 秒、读超时 45 秒；测试进程的模型/Embedding 重试预算为零。它不是全部 Agent 体验、标准评分或 35 题完整重跑。

第一次真实运行 `live-smoke/runs/20261003T191921Z-564b6ec2` 索引三份公开 Markdown，共 457 个片段。仅 G001 实际发送，31.98 秒，正常结束、四种映射完整，但无 `search_docs`、知识库来源和引用，RAG 证据门槛未通过。汇总脚本处理三个未尝试样本的 `content_checks=None` 时又异常，已停止后续请求、保留记录并修诊断脚本，不重写为成功。修复后将使用不同源码的新快照另记四样本验收；旧一次失败保留，累计请求数量与各版本分母分别报告。

第二次真实运行 `live-smoke/runs/20261003T201359Z-c41385a3` 按正常 main-first 启动，实际同一服务 PID 的 Agent/Text 门控和两工具预检均通过。四个 API 问题均进入 client dispatch：G001 用时 40.709 秒，事实检查通过，有工具结果、5 条来源、引用和成功结束；G023 为 `ReadError`，G026/EMPTY1 为 `ConnectError`，后三题没有可复核的完整回答。本轮同时发现上述索引取消挂起，主控撤回源码冻结并精确停止服务 PID 9956，中止与这些错误有重叠，不能据此归因模型/代理网络故障。汇总记录是 4 planned / 4 attempted / 1 verified；`run_status=complete` 仅说明计划尝试已走完，不代表验收成功。原版本一次加该版本四次，累计 **5 个已尝试 API 问题**，不是 Agent 内部模型轮数；Embedding 索引请求次数未测量，不能说为零。cleanup 记录确认 PID 退出、58087 空闲，旧失败不修改。

索引取消和日志脱敏补丁完成、完整后端绿色及主控源码哈希核对后，运行最终四样本 `live-smoke/runs/20261004T023214Z-4d9f84ef`。三文档全部 indexed，共 457 片段；同一 PID 10168 的 Agent/两工具预检通过，不置真开关、不替换产品提示词。主控比对 manifest 的 228 个文件哈希，当前工作区零差异。离线重排器记录 `weights_unavailable_offline` 降级。

| 样本 | 请求耗时 | 传输/结束 | 来源与内容检查 |
| --- | --- | --- | --- |
| G001 | 52.074 秒 | 正常完成、无错误 | 实际成功检索、5 条来源、有效引用，四模式诊断与关键事实人工核对通过 |
| G023 | 86.474 秒 | 正常完成、无错误 | 实际成功检索、5 条来源、有效引用，USARTDIV/BRR/误差诊断与关键事实人工核对通过 |
| G026 | 65.758 秒 | 正常完成、无错误 | 来源链路通过；未明确说 GPIO46 不选择 VDD_SPI，预设严格内容诊断失败，保留，不误记为已回答错误电压角色 |
| EMPTY1 | 43.476 秒 | 正常完成、无错误 | 空库实际检索、0 来源、无伪造数字引用、明确缺资料，规则诊断与人工核对通过 |

汇总为 4 planned / 4 attempted / 4 completed；资料题来源链路 3/3，严格内容诊断 2/3，空库 1/1。`acceptance_passed=false`，smoke 脚本退出码 1 来自严格内容诊断，四问无传输错误；运行完成不等于严格验收通过。累计旧 5 + 新 4 = 9 个 API 问题尝试，grader 0，不是 LLM 内部调用数；Embedding 请求次数未计量。新旧结果和全部失败分别保留，没有额外重试或调整检查来改成绩。summary/完整答案/工具与 SSE 记录在同一新目录；最终 cleanup 确认 PID 10168 已退出、58087 空闲，未停止其他进程。

主控完整答案旁审还发现 G026 的 JTAG 说明附有引用但此次检索上下文不含该依据，以及 src1 的 `page=3` 与页范围/citation 的 p1 不一致；需要 P1 的逐句引用与页语义核对，未声称浏览器缺陷已复现。具体依据与后续顺序见[质量报告第 8 节](rag-quality-baseline.md#8-2026-10-04-p0-实施与新验收)。本次四问不是标准质量总分。

本轮没有真实浏览器点击验收、新电脑安装验收、烧录或烧录后自动重连实机验收；不能以构建或受控模型小样本替代。

## 2026-10-03 Skills、记忆与 MCP 检查

### 2026-10-03 Skills 与记忆实网检查点

- 从真实公共仓库 `anthropics/skills` 的 `skills/brand-guidelines` 目录预览并导入，固定提交 `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4`。实际 ASGI HTTP 路由预览/导入为 200；新包默认停用、诊断 `supported`；同名重复导入返回 409，启用为 200，编辑后自动停用。仅删除本次项目外临时目录里的测试导入，删除 200，随后详情 404。没有操作用户现有技能。
- 真实 Windows HTTP 读取曾复现 socket 提前关闭后的 `WinError 10038`，并非代理超时；修复底层 EOF 处理后重新预览和上述导入流程通过。单元回归另证明关闭 socket 后仍保留总 deadline 检查。
- 普通聊天调用真实 `Text` 模型两次：保存合成记忆后，非空回答含预置合成板子标签；清空后新请求仍有非空回答但不再含标签。两次均成功结束、无错误事件。使用临时 SQLite、只在进程内读取已授权测试凭证，没有保存凭证或记忆原文到本文件。早期 128 token 的两次请求只有成功结束、没有可核对的最终答案，不能算记忆生效或清空验收；最终重试使用 2,048 token 回复预算。
- 真实 `Text` 模型分别完成手动、自动技能两次请求：两次均实际产生成功的 `load_skill` 工具结果，内容摘要与请求包一致，非空答案命中仅在合成技能正文中的预置口令，且成功结束、无错误事件。自动模式初始提示只有元数据，手动模式通过后端加载；客户端工具轨迹均不含说明正文。这里只证明该合成技能闭环，不证明任意 GitHub 包、第三方脚本或多模态技能可运行。
- 当前主控和 02 线的内置浏览器均因 `failed to write kernel assets` 初始化失败；新技能管理、选择及记忆页面只完成组件自动化，未完成本轮实际浏览器点击验收，不沿用旧日期截图冒充新功能已验收。

### MCP 集成与真实模型

- `test_mcp_communication.py` 的真实安全 stdio fixture 完成初始化、发现、并发回显/求和、停止、重启与旧 spec 不能转用新进程。RPC ID/坏消息/超时/远端业务错误/通知与分页限制等异常用受控协议进程测试，分别注明真实子进程或模拟异常，不将 mock 当作第三方兼容验收。
- `test_mcp_agent_integration.py` 走实际 LangChain ToolNode/Agent 图、真实安全 stdio 子进程和 ASGI 续跑 HTTP：default 与 bypass 模式均逐次确认；一个批次先拒绝 echo，再单独确认 add；echo 零次远端调用、add 一次。停止没有执行工具；缺失、错误、重复 call_id 为 409；Router 对参数/实例篡改、伪造 user_allow、重复派发、超时和非法原始 schema 有回归。这些集成场景的模型是可控流式模型，不是实网模型。
- 最后安全复核补充“停止时已排队调用不得发送”、连接/发现整体 60 秒截止、长名称不超过 64 字符与歧义名称区分。两份 MCP 测试合计 37 项通过、25 条警告、13.33 秒；另重跑下方全量，不用定向数量代替总体验证。
- `mcp_live_acceptance.py` 为显式手动运行脚本，不被普通 pytest 自动执行。仅允许项目外 `hardware-rag-verify-*` 源码副本及该副本私有 SQLite；凭证通过显式 `--credentials` 文件只读载入内存，关闭文件/硬件工具，只开放安全 MCP fixture。
- 真实 `Text` 两次聊天、各续跑一次，单次回复预算 2,048 token：拒绝检查先暂停、远端调用为 0、最终回答非空；允许检查先暂停、远端调用为 1、成功工具结果和最终非空回答包含合成回显标签。都无错误或额外确认，汇总 `passed:true`，退出码 0。不保存密钥、正文或远端返回到报告；未测试多模态 MCP、第三方服务或浏览器点击。
- 重现方式：先用隔离验证脚本取得新副本路径，在副本 backend 目录设置 `SQLITE_DB_PATH` 为新的副本内 `data/mcp-live.db`、`AGENT_CHECKPOINTER_TYPE=memory` 和离线 RAG 环境变量，再运行 `python -m tests.mcp_live_acceptance --credentials <本机未跟踪测试配置的绝对路径>`。不要在原工作区运行或把凭证复制进夹具。
- 实测使用 Python 3.13.13、jsonschema 4.25.1；requirements 固定的 jsonschema 是 4.23.0，本轮未安装/升级依赖。这不是对完整固定依赖新环境的重新验收。前端 79 项通过，0 lint 错误/13 条既有 hooks 警告，构建成功；仍有大分块与 React act 环境提示。
- 主控再次尝试内置浏览器和 Computer Use，均在初始化时报 kernel assets / 系统找不到指定路径。实际新页面点击、布局与确认卡互动均未验收，不通过终端伪装浏览器操作绕过。
- GitNexus 已运行符号/文件 impact 与 detect-changes。公共入口有 HIGH/CRITICAL 风险，新符号 UNKNOWN 用源码调用与测试进一步核实；当前图谱仍有错配/陈旧调用和新未跟踪文件覆盖不足，变化报告不是“图谱全量无风险通过”。没有提交或推送。

## 硬件实机验证

2026-09-26 使用用户确认的 COM5 开发板，完成应用内串口连接、接收实际开发板日志和正常断开检查。未发送串口数据，也未编译或烧录固件。日志出现过 `PSRAM ID read error` 和 BME280 探测失败信息，尚未诊断其硬件或固件原因。固件烧录及烧录后的串口自动重连仍未做实机验证。

## 已知待修复的鲁棒性问题

受控测试中，在聊天回答已开始输出后断开后端连接，页面超过一分钟仍显示生成中，未及时提示连接中断。2026-09-26 增加 Agent 每 15 秒心跳和前端聊天流 45 秒无数据报错；定向和全量自动化测试通过，但尚未用同一浏览器场景复测，不能写成实测通过。另一次测试从首页直接开始的未命名会话刷新后未出现在会话列表中；正式创建的会话刷新后可以保留，前一种路径仍需专门复测。
