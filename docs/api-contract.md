# Hardware RAG Agent API Contract

> 2026-10-26 上下文压缩候选的定向软件验收已完成：正常/resume 的 `CONTEXT_LIMIT` 与失败终态、完整 pending 暂停凭证及单 call 确认、真实 SSE 适配器驱动的原生 drain/join 和 SQLite 发布续跑均有证据。没有新增模型可授予权限，未扩大原始工具/Skill 正文归档资格；Skills 中普通 reader 仍注册，其现有运行时读取门禁保持。13 个专项 case 与五个指定回归文件为分组覆盖，非完整 HTTP/provider E2E 或全后端通过；主树产品未同步。详见[验收记录](reports/context-compaction-acceptance-20261026.md)，本段优先于下方历史状态。

> 2026-10-26 上下文压缩候选状态：协作 drain、同任务 join、精确保存配置及续跑属于宿主内部协议，不新增模型可授予权限。候选树七个冻结产品文件的无 spy 原生协议子集已有六项通过；正常/resume 的 `CONTEXT_LIMIT` 与失败终态、HITL 和完整保存恢复链仍在集中验证，不能据此声明完整 HTTP/SSE 已验收。主树产品未同步；下方历史“未接入/未测试”不代表此候选子集仍未运行，详见 [测试记录](testing.md)。

> 2026-10-24 当前 Skills 状态：标准包/API、Agent 与现有前端实现及接线完成；选定软件自动化验证为 13 个指定后端测试路径 186 passed / 26 warnings、完整 Vitest 24 files / 159 passed、`tsc -b` exit 0、Vite build exit 0（chunk-size advisory）。这不是完整后端或整项目验收；真实 GitHub/模型/浏览器、真实用户库/Skills 数据、完整 HTTP/SSE/resume E2E 和第三方 Codex/Claude/Pi/OpenCode 实际发现/运行未验证。本轮无提交，详见 [实施记录](skills-native-implementation.md)。

> 2026-10-23 Skills 接入状态（历史）：当时标准包、共享目录、原生工具处于实现中，集中验证尚未完成；本段的“待补齐”由上方 2026-10-24 结果更新。旧“只读/私有目录/默认停用”仍只属于历史阶段，不能作为当前协议。

> 2026-10-23 历史报告越权追加已撤回并精确恢复，不构成接口变更或授权。全部产品继续冻结；fresh只读worker没有control/caller/schema/reader/HTTP/SSE/权限变更权，无测试或功能接受。

> 2026-10-23 replacement51B9FCD4源码已交未验证：保持bool graph-applied/可选host receipt语义及初始协议，拒unready/foreign、owned事务与取消join是内部约束，不增HTTP/SSE/权限/归档来源。下一host writer/control设计无接线启用权，非全writer/globalCAS或跨轮实测承诺。

> 2026-10-23 旧replacement事务所有权窄修已分派、未交付验证：仅内部publisher拒unready/foreign及owned事务提交回滚，不增HTTP/SSE字段、权限或恢复来源；initial协议不改。两检索正文仍无自动归档资格，不将LOW/JSON/digest当授权。CRUD的epoch stage/同Session commit源码不等跨库或全writer fence/安全擦盘。

> 2026-10-23 内部恢复失败关闭源码已收、未验证：autocompact94144BB3失败抛现有AgentRequestBudgetError，不返回最后问题fallback；无新增caller/外部字段，不能因错误类型断言已跑通CONTEXT_LIMIT映射。原host scope/ref/tail/epoch与工具/Skill archive资格不变，全部产品冻结。下一存储接缝设计不授新schema/HTTP/model reader或正文归档许可。

> 2026-10-23 内部启动接线源码已交、未验证：route861531CD/adapterD2E7B2B已核源码和交接；首次None输入+真实seed配置与ongoing原request状态配置分开，初始scope/epoch在direct/merged真实消费任务启动前复核。仍无新增HTTP/SSE字段或授权，保存结果不明不重投输入，receipt UNKNOWN不当未登记或已完成。全产品冻结，下一仅安全观察/Skill恢复设计；puretextarchive拒raw tool/Skill/system、现读取工具白名单和删除/配额边界不因设计放宽。

> 2026-10-23 新内部启动consumer已窄分派、尚未交付/测试：初始私有模块factory7333D4AE/publisher691DDB9A现冻结。route/adapter下一仅在宿主严格资格与真实保存ack/读回后，首次以None输入及真实seed-ID config进入同原图；后续state/compaction/HITL使用原无seed-ID request config，不重复原input、不将旧parent携带追认为新发布。REFUSED/未知保存终止，receipt UNKNOWN不等确定未登记；epoch/完整有序来源/尾部/最终wire预算保持。无外部HTTP/SSE字段、权限或业务session变更，无全writer/跨库原子或运行成功承诺。

> 2026-10-23 内部接线源码已交、未验证：三consumer现为BB74BC8F/63A082C7/EAF39150；两publisher调用点可从当前verified纯文本scope尝试登记，normal入口只在稳定published head与完整有序原incoming匹配时保尾读入，取消不被吞成继续执行，epoch失效安全终止。初始输入保存仍未接caller；下一仅factory/publisher模块定义host-only原图证明和真实保存结果，图已应用但confirm无head只能UNCONFIRMED/UNKNOWN，不能说确定未登记；提交结果不明不得同图重投原input。无新增外部HTTP/SSE字段、权限或全writer/跨库原子保证，未声称生产head已实际推进或跨轮已通过验证。

> 2026-10-23 源码恢复已交后的窄接入契约（未测试）：normal和manual HITL请求队列按原宿主graph/业务session/具体queue配对，续跑只claim原owner/session/request及原图pending，外部session/SSE/call_id和ToolContext.session_id不变；当前四产品D7103B27/0B8AD034/6710186B/55F338EB已核。下一三文件任务显式接两publisher caller和新normal请求的稳定published-head读取，只有完整有序incoming匹配全部来源才替为checkpoint+原tail，旧producer不被新reader UUID替换。Skills/匿名/不支持或读取失败不产生恢复资格；失败保完整incoming或CONTEXT_LIMIT，epoch失效安全终止。HITL不load替图；图已applied但登记失败仍按原bool，不回滚或重放。初始pre-send未获save ack者仍不登记，v1/prose/orphan不追认，无新外部端点/字段或权限。源码实施不是验证结论。

> 2026-10-23 当前接入状态纠正：01路由/HITL/事件通道实现发生自回滚，已冻结并移交Mill四文件恢复；原交接报告不代表当前接入完成。chat_routes残留需逐块归因保护，其他三文件回任务入口。外部session/SSE/call_id及ToolContext.session_id契约不改；新宿主request图、原图pending匹配、原子claim与请求通道仍待完整恢复和最终统一验证。活preflight claim不得按TTL放手，取消/未启动响应须按拥有者生命周期清理，执行前须复核epoch。自动published-context恢复、publisher登记caller与预发送观察均未启用；不从事件ID/自然耗尽或同连接tuple存在推断commit。以下旧“正在接入”均为历史阶段，不是新支持声明。

> 2026-10-22 内部host连续性协议仍未测试/未启用恢复：新strict九字段路径才可产生私有origin资格；prepared保存完整有序source覆盖与未改tail证明，receipt/head只能由真实graph保存后exact-ID读回再登记。新分派要求prepared绑定宿主producer request图身份，与业务session/ToolContext分离；load按旧producer核ref和图绑定，新请求UUID不替换source UUID。新可选graph配置与publisher登记分支默认不影响旧调用，配置不是认证或授权。无新增HTTP/model读取端点，外部SSE/session字段不因内部graph UUID自动改变；HITL快照/续跑与SSE生产消费未接齐前不启用新路由恢复。配额/删除epoch/失败保原/CONTEXT_LIMIT/completed空均保持，不宣称全writer或跨库原子性。

> 2026-10-22 S06分派候选语义未测试：Agent list_kb_docs/新检索只把indexed且error_message为空的文档当可用来源，未知状态/缺记录/无法核实不放行；无可检索文档提示等待索引或说明无KB依据。缓存签名包含文档状态；检索期间原有效签名变更或发布前无法复核，沿用空结果语义且不自动扩大范围/重试。接口字段、TopK/阈值/文档过滤和None/[]全启用不变；/api/kb/list管理响应仍包含indexing/error及错误信息供查询重试。不是全局原子发布/撤回已返回来源，也没有统一验证结论。

> 2026-10-22 分派候选增量未测试：ToolRouter.output不再无条件截为8000字符；没有验证过完整正文可恢复来源时保留完整output，metadata.compacted不因长度被该helper设为true。返回封套字段不变；新metadata.hwrag_tool_event_ref仍仅定位body-free记录，不替代输出正文。后续provider预算无法容纳时应通过既有CONTEXT_LIMIT失败，不静默截断。此处是实施契约，不是统一验证结果。

> 2026-10-22 候选未测试增量：ToolRouter正常流水线返回metadata可含hwrag_tool_event_ref{batch_id,request_id,sequence,version:2,content_sha256}，仅真实元数据记录提交成功后宿主生成，不含身份或授权。新增registry LOW工具read_tool_event，仅上述locator及byte_budget1..4096（默认4096），extra字段拒绝；host verified scope+runtime session一致且Skills-off，核现存会话/epoch/同owner/请求/完整chain/head/version/hash。成功data.available:true、untrusted_history:true、completion_verified:false、coverage:committed_returns_only；失败available:false不带event。byte_budget覆盖该工具自身返回的完整JSON数据，不含ToolRouter外层封套；错误说明可独立返回，无历史正文。没有新HTTP端点，不允许执行旧动作/凭metadata升级completed或批准；disabled与Skills过滤复用。

> 同批kb_upload准入（未测试）：读取应用层文件内容最多MAX_UPLOAD_SIZE+1（50MiB+1），超限沿用FILE_TOO_LARGE；KB存在后先校验chunk_method/chunk_size/small_chunk_size，再处理同名失败上传清理或新写入。因此既有重复且新参数非法时优先INVALID_REQUEST，不发生旧记录清理。有效请求/同名重传/缺省配置/异步indexing形状不改；这不是HTTP传输层大小限制或并发重复上传CAS保证。

> 2026-10-22 候选未测试增量：Agent聊天及HITL续跑在provider发送前无法确认完整请求预算或超限时，使用既有SSE `error` 形状并返回 `code: "CONTEXT_LIMIT"`，随后 `done {success:false, completed:false}`。不自动删历史、丢工具schema或走基础模型重试。预算计算包含实际请求型号、system/messages、工具定义、结构化输出schema、JSON封装、256 framing及wire输出上限；无上限则预留本地登记最大值。未知型号、不可核成本的opaque模态/provider内建工具/远端隐含历史/自动truncation/extra_body覆写不允许绕过。此为保守本地文本预算，不是精确token或provider能力确认；按真人要求待全部实现到位后统一测试，尚未验收。

> 同轮内部工具返回元数据档案（未测试）不新增HTTP/model读取接口或工具许可字段；Scope仅宿主verified principal/session/request/delete-epoch提供，不来自payload。Skills活跃可记录固定元数据但仍不授原文archive权限。HITL续跑若该scope的principal与当前身份不符，沿用409/HITL_REQUEST_SNAPSHOT_UNAVAILABLE并在构建Agent前拒绝。内部读取只返回untrusted_history与completion_verified:false，returned不是完成/批准；原九字段completed空限制不变。私有Settings/配额/删除规则沿用现有context namespace，原GET context-events不返回这种不同schema的事件。

> 内部元数据链续接（2026-10-22，未测试）：工具返回ref使用独立schema/version2及请求内1..64序号，事务head绑定最终envelope摘要；缺head/缺序/错尾失败关闭。宿主整链读取包含ref+event的完整JSON响应≤32768字节、单条完整响应≤4096字节，不是只限制正文。旧同请求schema1不追认或迁移；既有puretext GET/reader version1不变，仍不提供该内部链的HTTP/model访问授权。

> 内部attempt事件（同日未测试）：schema2链可含tool_attempt_started/timeout/error/cancelled，attempt为1..16、decision固定unknown、skill_version为空；其locator另绑定phase+attempt，不改变tool_return旧locator。request64总上限计所有phase与return，记录配额不等于执行次数限制。取消或超时不代表外部动作撤销，无新SSE/HTTP/model工具字段与授权，pending/permission拒绝等前置事件仍未接齐。

这份文档是 `Hardware RAG Agent` 前后端联调时唯一认可的接口契约来源。

## 1. 工地铁律

- 任何新接口，先写文档，再写代码。
- 前端只要新增一个接口调用，就必须同步补全文档。
- 后端只要新增路由，或修改路径、请求体、响应体、字段名、状态码、错误码，就必须同步改文档。
- 没写进本文档的接口，默认视为不存在，不纳入联调与验收范围。
- 联调时如果前后端理解不一致，先更新本文档，再改代码。
- 每一次对本文档的修改，必须在变更日志中登记。

## 2. 全局约定

### 2.1 Base URL

- 开发环境统一使用 `/api` 作为前端请求前缀。
- 本地后端默认服务地址为 `http://127.0.0.1:58080`。
- WebSocket 地址为 `ws://127.0.0.1:58080/api`。
- 前端通过代理把 `/api` 转发到后端，避免在页面中写死多个地址。
- 本轮安全收口限定为单机本地运行：受支持的启动入口只允许环回监听地址；非环回地址应在启动前明确报错。它不是局域网或公网部署方案，也不等于已完成多用户认证或操作系统沙箱。

### 2.2 API 版本化

- V1 阶段所有接口路径以 `/api/` 开头，不显式标注版本号。
- V2 及以后新增或破坏性变更的接口必须以 `/api/v2/` 开头。
- 对已有 V1 接口的向后兼容修改（仅新增可选字段）可以不升版本，但必须在变更日志中登记。
- 删除或重命名字段、修改必填项、修改事件类型属于破坏性变更，必须升版本。

### 2.3 接口状态

每个接口必须标记当前状态，状态只允许使用以下几种：

- `draft`：需求已提出，契约未达成。
- `agreed`：前后端已确认字段与行为。
- `mocked`：前端已按契约接入 mock 数据。
- `implemented`：后端已完成实现。
- `verified`：软件接口的前后端联调通过；不代表真实开发板或串口已经完成实机验收。

### 2.4 HTTP 状态码

所有接口统一使用以下状态码：

| 场景 | 状态码 | 说明 |
| --- | --- | --- |
| 成功 | `200` | 请求正常处理完成 |
| 请求参数错误 | `400` | 路由自行检查参数后返回的业务错误 |
| 请求体校验失败 | `422` | FastAPI/Pydantic 拒绝缺失字段、错误类型或无效的接线端点等 |
| 认证失败 | `401` | API Key 无效或未提供 |
| 访问被拒绝 | `403` | Explorer 路径不在已授权项目根内 |
| 资源不存在 | `404` | 请求的资源不存在 |
| 请求冲突 | `409` | 资源已存在等 |
| 服务器内部错误 | `500` | 未预期的服务端错误 |

路由自行构造的错误响应遵循 2.6 节；FastAPI/Pydantic 自动生成的 `422` 响应使用其默认 `detail` 格式。

### 2.5 认证与鉴权

所有涉及 LLM 调用的接口通过 HTTP Header 传递认证和模型选择信息：

| Header | 必填 | 说明 | 示例 |
| --- | --- | --- | --- |
| `X-API-Key` | 是 | 用户配置的 API Key | `sk-proj-xxx` |
| `X-Model` | 是 | 当前选择的模型 ID | `gpt-4o` |
| `X-Provider` | 否 | Provider 标识，不传则默认为 `openai` | `openai` |

不需要 LLM 调用的接口（如 `/api/devices`、`/api/kb/list`）不需要传这些 Header。

所有 `/api/explorer/*` 路由另须传 `Authorization: Bearer <session_token>`，包括文件夹选择器；首次配置凭据的 `/api/auth/store-key` 不受此要求限制。缺失或无效的 session 返回 401（`AUTH_REQUIRED` / `AUTH_INVALID`）。

### 2.6 标准响应格式

除接口详情明确列出的旧版或专用响应（如 §5.9 `/api/tool`、§5.36 Explorer `open/read`）外，JSON 接口使用以下格式：

成功响应：

```json
{
  "success": true,
  "data": {}
}
```

`data` 的内容是各接口的业务数据，在接口详情中写明。

错误响应：

```json
{
  "success": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "请求参数不合法",
    "details": null
  }
}
```

- `code`：稳定错误码，给前端做分支判断用。全大写 + 下划线。
- `message`：给开发调试看的可读信息。
- `details`：必填，无额外信息时传 `null`。放字段级错误、原始报错摘要或补充信息。

此格式对所有 JSON 接口强制生效，不接受"接口直接返回业务对象"的例外。

### 2.7 SSE 约定

所有 SSE 接口统一使用：

- `Content-Type: text/event-stream`
- 每条事件格式：`data: {JSON}\n\n`
- 流结束时必须发送 `done` 事件。

事件对象统一包含：

- `type`：事件类型。
- `message`：可选的人类可读说明。
- 其他事件专属字段。

聊天、编译、烧录等流式接口，流结束时必须发送 `done` 事件。

### 2.8 字段命名

- JSON 字段统一使用 `snake_case`。
- 布尔值字段用 `true/false`，不要用 `0/1` 或 `"true"` 字符串。
- 时间字段统一使用 ISO 8601 字符串，除非接口明确约定 Unix 时间戳。
- 枚举字段必须在文档中列出允许值。
- 可空字段明确写 `null` 是否允许，不要让前后端各自猜测。

### 2.9 前端的 API 桥接函数

前端已封装四个桥接函数，后端只需返回标准格式即可：

| 函数 | 用途 | 说明 |
| --- | --- | --- |
| `apiGet(path)` | GET JSON 请求 | 自动拼接 `API_BASE + path`，无需补 `/api/` |
| `apiPost(path, body)` | POST JSON/multipart 请求 | 自动判断 `FormData`，JSON 自动 `Content-Type: application/json` |
| `apiDelete(path, body?, timeoutMs?)` | DELETE JSON 请求 | 自动拼接 `API_BASE + path`，body 可选 |
| `apiPatch(path, body?, timeoutMs?)` | PATCH JSON 请求 | 自动拼接 `API_BASE + path`，body 可选 |
| `apiSSE(path, body, callbacks)` | SSE 流式请求 | POST 请求，通过 `onEvent(evt)` 回调逐事件消费 |
| `apiWS(endpoint, handlers)` | WebSocket 连接 | 自动拼接 `WS_BASE + endpoint` |

调用示例：

- `apiPost('models', { base_url })` → `POST http://127.0.0.1:58080/api/models`
- `apiSSE('chat', body, { onEvent, onDone })` → `POST http://127.0.0.1:58080/api/chat`
- `apiWS('/api/monitor/COM3?baud=115200', { onMessage })` → `ws://127.0.0.1:58080/api/monitor/COM3?baud=115200`
- `apiGet('devices')` → `GET http://127.0.0.1:58080/api/devices`


### 2.10 分页

列表类接口（如 `/api/kb/list`）统一使用以下分页参数：

请求参数：
- `page`：页码，从 1 开始，默认 1。
- `page_size`：每页条数，默认 20，最大 100。

响应中 `data` 结构扩展为：

```json
{
  "success": true,
  "data": {
    "items": [],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 42,
      "total_pages": 3
    }
  }
}
```

- `items` 替代了无分页版本的直接数组名（如 `documents`）。
- 不分页的接口（如 `/api/devices`）继续返回原有格式。

### 2.11 日期时间格式

所有日期时间字段统一使用 ISO 8601 格式，包含时区信息：

- 推荐格式：`2026-06-19T10:00:00+08:00`
- 服务端统一使用 UTC 存储：`2026-06-19T02:00:00Z`
- 前端在展示时转换为本地时区。
- 仅日期不含时间时使用：`2026-06-19`
- 不使用 Unix 时间戳，除非接口详情中明确注明。

### 2.12 文件上传约束

| 约束 | 值 | 说明 |
| --- | --- | --- |
| 最大文件大小 | 50 MB | 超过时返回 `FILE_TOO_LARGE`（400） |
| 支持格式 | `pdf`、`md`、`txt` | 其他格式返回 `UNSUPPORTED_FORMAT`（400） |
| 编码 | UTF-8 | `md` 和 `txt` 文件必须为 UTF-8 编码 |
| 文件名字段 | `file` | 上传接口统一使用 `multipart/form-data`，字段名为 `file` |

### 2.13 空值与缺省值

| 场景 | 规则 | 说明 |
| --- | --- | --- |
| 字符串无值 | 传 `null` | 不传空字符串 `""` |
| 数组无值 | 传 `[]` | 不传 `null` |
| 对象无值 | 传 `null` | 按接口详情约定 |
| 布尔值 | 必填 | 不设默认值，不允许缺省 |
| 数字或字符串缺省 | 在接口详情中写明默认值 | 前端不传则后端使用默认值 |

### 2.14 错误码列表

所有接口共享以下错误码（`code` 字段）：

| code | 状态码 | 说明 |
| --- | --- | --- |
| `INVALID_REQUEST` | 400 | 请求参数不合法 |
| `AUTH_FAILED` | 401 | API Key 无效或未提供 |
| `MODEL_FETCH_FAILED` | 500 | 模型列表获取失败 |
| `UPLOAD_FAILED` | 500 | 文件上传或解析失败 |
| `FILE_TOO_LARGE` | 400 | 文件超过 50 MB |
| `UNSUPPORTED_FORMAT` | 400 | 文件格式不支持 |
| `TOOL_NOT_FOUND` | 400 | 请求的工具不存在 |
| `CHIP_NOT_SUPPORTED` | 400 | 芯片型号不支持 |
| `SERIAL_NOT_FOUND` | 404 | 串口设备不存在或已被占用 |
| `BUILD_FAILED` | 500 | 编译失败（具体错误在 SSE done 事件返回） |
| `FLASH_FAILED` | 500 | 烧录失败 |
| `INTERNAL_ERROR` | 500 | 未预期的服务端错误 |
| `DOC_NOT_FOUND` | 404 | 知识库文档不存在 |
| `KB_NOT_FOUND` | 404 | 知识库不存在 |
| `KB_NOT_DELETABLE` | 400 | 内置知识库不可删除 |
| `KB_CREATE_FAILED` | 500 | 创建知识库失败 |
| `KB_DELETE_FAILED` | 500 | 删除知识库失败 |
| `AGENT_CHUNK_FAILED` | 500 | Agent 分块失败（LLM 调用错误） |
| `AGENT_CHUNK_INCOMPLETE` | 500 | Agent 分块漏页，覆盖不完整 |
| `EMPTY_CODE` | 400 | 沙箱代码为空 |
| `CODE_TOO_LONG` | 400 | 沙箱代码超过 50000 字符 |
| `UNSUPPORTED_LANGUAGE` | 400 | 沙箱不支持的语言 |
| `SANDBOX_UNAVAILABLE` | 503 | 沙箱不可用（Docker 未安装/未运行） |
| `EXECUTION_TIMEOUT` | 408 | 沙箱执行超时（默认 30s） |
| `INVALID_POLICY` | 400 | Agent 权限模式 `permission_mode` 缺失或非法（`agent_sandbox_routes.py`） |
| `INVALID_SETTINGS_KEY` | 400 | 设置键不在白名单（`crud.py`，仅允许 `ALLOWED_SETTINGS_KEYS` 中的键） |
| `INVALID_RETRIEVAL_SETTING` | 422 | `topK` 或 `relevanceThreshold` 不是有限数，或超出允许范围；批量设置整批不变 |
| `NOT_FOUND` | 404 | 通用资源未找到（会话/消息等 CRUD 资源不存在，`crud.py`） |
| `AUTH_REQUIRED` | 401 | 未提供认证 token（`auth.py`，已配置 providers 但请求未带 Bearer token） |
| `AUTH_INVALID` | 401 | session_token 无效或已过期（`auth.py`，30 天有效期） |
| `PAYLOAD_TOO_LARGE` | 413 | 请求体超过 20MB 限制（`main.py` `_RequestBodyLimitMiddleware`，仅拦截 `/api/chat`） |
| `DEVICE_SCAN_FAILED` | 500 | 串口扫描失败（`hardware_routes.py` `/api/devices`） |
| `DIAGNOSE_FAILED` | 500 | 引脚诊断失败（`hardware_routes.py` `/api/diagnose`） |
| `AUDIT_FAILED` | 500 | 引脚审计失败（`hardware_routes.py` `/api/audit_pins`） |
| `TOOL_ERROR` | 200 | `/api/tool` 路由捕获未预期异常后返回的业务错误（HTTP 状态仍为 200） |
| `CONFLICT` | 409 | 消息 ID 已属于其他会话（`crud.py`） |
| `FORBIDDEN` | 403 | Explorer 路径不在已显式打开的授权根内 |
| `EXPLORER_ERROR` | 400/500 | Explorer 其他业务或内部错误，按具体 HTTP 状态判断 |
| `VERSION_CONFLICT` | 409 | Explorer 文件在读取后已变化，或新建路径已存在 |
| `TRASH_ERROR` | 400 | Explorer 移入回收区或恢复操作无法安全完成 |
| `RESTORE_CONFLICT` | 409 | Explorer 恢复目标已经存在 |
| `TRASH_ITEM_NOT_FOUND` | 404 | Explorer 回收区条目不存在 |

历史错误码 `MISSING_API_KEY`、`FETCH_FAILED` 已废弃，分别统一使用 `AUTH_FAILED`、`MODEL_FETCH_FAILED`。

新增错误码必须先登记到此表后再使用。

### 2.15 请求追踪 ID

后端在每个响应的 HTTP Header 中返回 `X-Request-Id`，值为 UUID v4。

- 前端无需关心此 Header，但可在调试时用于向开发者反馈问题。
- 后端在日志中记录每个请求的 `X-Request-Id`、方法、路径、耗时和状态码。

### 2.16 CORS 策略

开发阶段：

- 后端允许所有来源：`Access-Control-Allow-Origin: *`
- 允许所有标准方法和自定义 Header（`X-API-Key`、`X-Model`、`X-Provider`）
- 允许 `Content-Type: application/json` 和 `multipart/form-data`

生产部署建议：

- 将 `*` 替换为具体的前端域名。
- 通过环境变量 `CORS_ORIGINS` 配置。

### 2.17 SSE 连接管理

- Agent 聊天流在等待模型或工具期间每 15 秒发送一次 `heartbeat`；基础 LLM 回退流也每 15 秒发送心跳，并在 5 分钟无新数据后结束。
- 前端聊天流在 45 秒未收到任何数据时结束等待并报告连接超时。断线后不自动重放请求，由用户决定是否重试，避免重复执行 Agent 工具。
- 编译、烧录 SSE 的前端读超时保持 30 分钟；断开后由用户手动触发。
- 后端主动关闭前先发送 `type: "error"` 事件再断开。

### 2.18 WebSocket 重连策略

串口监视器 WebSocket 断开时：

- 前端自动重连，固定重试三次：1s → 2s → 3s。
- 三次均失败后显示"连接失败"，由用户手动重连。
- 断开期间前端保留已收到的数据，不清除历史。

## 3. 协作流程

### 3.1 前端新增接口时

前端在写 `apiGet`、`apiPost`、`apiSSE`、`apiWS` 调用之前，先补：

- 接口目录中的一行。
- 对应接口详情中的路径、用途、请求体、响应体、mock 规则。

### 3.2 后端实现接口时

后端开始写路由前，先核对：

- 路径是否已存在。
- 请求体是否齐全。
- 成功响应是否有明确示例。
- 错误响应和状态码是否写明。
- 是否需要 SSE 或 WebSocket。

### 3.3 联调验收时

每个接口最少检查：

- 路径、方法是否一致。
- 必填参数和 Header 是否一致。
- 成功响应字段是否一致。
- 错误响应格式是否一致。
- 空数据、异常数据、失败场景是否一致。
- 文档状态是否能推进到 `verified`。

### 3.4 跨功能线修改接口时

项目按 AGENTS.md 的六条端到端功能线分工。提出变更的线程先在交接中写明旧字段、新字段、兼容方式和受影响线程，由 00 主控协调同一份接口契约及共享代码的写入顺序。知识库提供检索结果和来源字段，聊天线负责 SSE 传输与展示，Agent 线负责工具调用意图，工具与安全线负责注册和授权，工作区与硬件线负责各自的实际动作。字段和事件格式确认后，相关线程再分别实现与验证；不能只更新一端就将接口标为 `verified`。

## 4. 接口目录

| 状态 | 方法 | 路径 | 用途 | 前端入口 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `agreed` | `POST` | `/api/chat` | RAG 流式聊天 | `apiSSE('chat', ...)` | SSE |
| `agreed` | `POST` | `/api/models` | 拉取模型列表 | `apiPost('models', ...)` | JSON |
| `agreed` | `POST` | `/api/kb/upload` | 上传知识库文件 | `apiPost('kb/upload', formData)` | multipart |
| `agreed` | `GET` | `/api/kb/list` | 列出已上传文档 | `apiGet('kb/list')` | JSON |
| `agreed` | `POST` | `/api/kb/delete` | 删除知识库文档 | `apiPost('kb/delete', { doc_id })` | JSON |
| `implemented` | `GET` | `/api/kb/collections` | 列出所有知识库 | `apiGet('kb/collections')` | JSON |
| `implemented` | `POST` | `/api/kb/collections` | 创建知识库 | `apiPost('kb/collections', ...)` | JSON |
| `implemented` | `GET` | `/api/kb/collections/{kb_id}` | 知识库详情（含文档） | `apiGet('kb/collections/{id}')` | JSON |
| `implemented` | `DELETE` | `/api/kb/collections/{kb_id}` | 删除知识库 | `apiDelete('kb/collections/{id}')` | JSON |
| `implemented` | `PATCH` | `/api/kb/collections/{kb_id}/toggle` | 切换 KB 搜索开关 | `apiPatch('kb/collections/{id}/toggle', { enabled })` | JSON |
| `implemented` | `POST` | `/api/kb/embedding-models` | 代理上游 embedding 模型列表 | `apiPost('kb/embedding-models', ...)` | JSON |
| `agreed` | `GET` | `/api/devices` | 扫描串口设备 | `apiGet('devices')` | JSON |
| `agreed` | `POST` | `/api/audit_pins` | 引脚冲突审计 | `apiPost('audit_pins', ...)` | JSON |
| `implemented` | `POST` | `/api/diagnose` | 代码与引脚诊断 | `apiPost('diagnose', ...)` | JSON |
| `agreed` | `POST` | `/api/build` | 编译固件 | `apiSSE('build', ...)` | SSE |
| `agreed` | `POST` | `/api/upload` | 烧录固件 | `apiSSE('upload', ...)` | SSE |
| `implemented` | `POST` | `/api/tool` | 直接调用 Agent 工具（含权限预检） | 专用客户端待接入 | JSON |
| `agreed` | `WS` | `/api/monitor/{port}?baud=` | 串口监视器数据流 | `apiWS(...)` | WebSocket |
| `implemented` | `POST` | `/api/explorer/open` | 授权并打开文件夹 | `apiPost('explorer/open', { path })` | JSON |
| `implemented` | `GET` | `/api/explorer/browse?path=` | 文件夹选择器 | `apiGet('explorer/browse?path=')` | JSON |
| `implemented` | `GET` | `/api/explorer/dir?path=` | 懒加载指定目录的子节点 | `apiGet('explorer/dir?path=')` | JSON |
| `implemented` | `GET` | `/api/explorer/read?path=` | 读取文件内容和版本 | `apiGet('explorer/read?path=')` | JSON |
| `implemented` | `POST` | `/api/explorer/write` | 按版本写入文件内容 | `apiPost('explorer/write', { path, content, expected_version })` | JSON |
| `implemented` | `POST` | `/api/explorer/create` | 新建文件/文件夹 | `apiPost('explorer/create', { path, type })` | JSON |
| `implemented` | `POST` | `/api/explorer/rename` | 重命名文件/文件夹 | `apiPost('explorer/rename', { path, new_name })` | JSON |
| `implemented` | `POST` | `/api/explorer/delete` | 移至应用回收区 | `apiPost('explorer/delete', { path })` | JSON |
| `implemented` | `GET` | `/api/explorer/trash?root_path=` | 列出可恢复条目 | `apiGet('explorer/trash?root_path=')` | JSON |
| `implemented` | `POST` | `/api/explorer/restore` | 恢复回收区条目 | `apiPost('explorer/restore', ...)` | JSON |
| `implemented` | `POST` | `/api/explorer/move` | 移动文件/文件夹 | `apiPost('explorer/move', { path, target_dir })` | JSON |
| `implemented` | `GET` | `/api/explorer/diff?path=` | 获取文件 diff 基准 | `apiGet('explorer/diff?path=')` | JSON |
| `implemented` | `GET` | `/api/explorer/watch?path=` | SSE 文件系统监听 | `apiSSE('explorer/watch?path=')` | SSE |


| `agreed` | `POST` | `/api/sessions` | 创建会话 | CRUD | JSON |
| `agreed` | `GET` | `/api/sessions` | 列出会话 | CRUD | JSON |
| `agreed` | `GET` | `/api/sessions/{id}` | 获取会话（含消息） | CRUD | JSON |
| `agreed` | `PUT` | `/api/sessions/{id}` | 更新会话 | CRUD | JSON |
| `agreed` | `DELETE` | `/api/sessions/{id}` | 删除会话 | CRUD | JSON |
| `implemented` | `POST` | `/api/sessions/{id}/messages` | 添加或按 ID 更新消息 | CRUD | JSON |
| `implemented` | `POST` | `/api/sessions/{id}/messages/append-v1` | 按稳定消息 ID 原子追加单条消息 | `apiPost('sessions/{id}/messages/append-v1', ...)` | JSON |
| `implemented` | `GET` | `/api/sessions/{id}/history-mode` | 查询历史存储模式；空 owner 会话可幂等初始化 V2 root/pointer | `apiGet('sessions/{id}/history-mode')` | JSON |
| `agreed` | `GET` | `/api/sessions/{id}/messages` | 列出消息 | CRUD | JSON |
| `agreed` | `DELETE` | `/api/sessions/{id}/messages?keep_count=N` | 截断会话消息（保留前 N 条） | `apiDelete('sessions/{id}/messages?keep_count=N')` | JSON |
| `agreed` | `GET` | `/api/settings` | 获取设置 | CRUD | JSON |
| `agreed` | `PUT` | `/api/settings` | 保存设置 | CRUD | JSON |
| `agreed` | `POST` | `/api/diagnose` | 代码诊断 | `apiPost('diagnose', ...)` | JSON |
| `implemented` | `GET` | `/api/agent-sandbox/policy` | 查询 Agent 权限模式 | `apiGet('agent-sandbox/policy')` | JSON |
| `implemented` | `POST` | `/api/agent-sandbox/policy` | 切换 Agent 权限模式 | `apiPost('agent-sandbox/policy', ...)` | JSON |
| `implemented` | `GET` | `/api/agent-sandbox/audit` | 查询工具审计日志 | `apiGet('agent-sandbox/audit', ...)` | JSON |
| `implemented` | `POST` | `/api/agent-sandbox/resume` | HITL 用户决策恢复 | `apiSSE('agent-sandbox/resume', ...)` | SSE |
| `implemented` | `GET` | `/api/tools` | 列出已注册工具 | `apiGet('tools')` | JSON |
| `implemented` | `GET` | `/api/token-usage/stats` | Token 用量统计（支持按会话过滤） | `apiGet('token-usage/stats?days=30&session_id=<sessionId>')` | JSON |
| `implemented` | `POST` | `/api/auth/store-key` | 加密存储 API Key | `apiPost('auth/store-key', ...)` | JSON |
| `implemented` | `GET` | `/api/auth/keys` | 列出已存储的 Provider | `apiGet('auth/keys')` | JSON |
| `implemented` | `DELETE` | `/api/auth/keys/{provider}` | 删除 Provider 的 API Key | `apiDelete('auth/keys/{provider}')` | JSON |
| `implemented` | `POST` | `/api/mcp/servers` | 添加 MCP Server 配置 | `apiPost('mcp/servers', ...)` | JSON |
| `implemented` | `GET` | `/api/mcp/servers` | 列出所有 MCP Server 及状态 | `apiGet('mcp/servers')` | JSON |
| `implemented` | `POST` | `/api/mcp/servers/{server_id}/start` | 启动 MCP Server | `apiPost('mcp/servers/{id}/start')` | JSON |
| `implemented` | `POST` | `/api/mcp/servers/{server_id}/stop` | 停止 MCP Server | `apiPost('mcp/servers/{id}/stop')` | JSON |
| `implemented` | `GET` | `/api/mcp/servers/{server_id}/tools` | 列出 MCP Server 提供的工具 | `apiGet('mcp/servers/{id}/tools')` | JSON |
| `implemented` | `DELETE` | `/api/mcp/servers/{server_id}` | 删除 MCP Server 配置 | `apiDelete('mcp/servers/{id}')` | JSON |
| `implemented` | `POST` | `/api/feedback` | 提交消息反馈（👍/👎） | `apiPost('feedback', ...)` | JSON |
| `implemented` | `GET` | `/api/feedback/{session_id}` | 查询会话的反馈记录 | `apiGet('feedback/{session_id}')` | JSON |
| `implemented` | `POST` | `/api/search` | 全文搜索历史消息 | `apiPost('search', ...)` | JSON |
| `implemented` | `PATCH` | `/api/kb/collections/{kb_id}/rename` | 重命名知识库 | `apiPatch('kb/collections/{id}/rename', ...)` | JSON |
| `implemented` | `PATCH` | `/api/kb/collections/{kb_id}/config` | 更新 KB 配置（embedding/分块策略） | `apiPatch('kb/collections/{id}/config', ...)` | JSON |
| `implemented` | `GET` | `/api/kb/documents/{doc_id}/chunks` | 查文档的 chunk 列表 | `apiGet('kb/documents/{doc_id}/chunks')` | JSON |
| `implemented` | `GET` | `/api/kb/chunks/{small_chunk_id}` | 查单个 chunk 详情（含 big_chunk_text） | `apiGet('kb/chunks/{small_chunk_id}')` | JSON |
| `implemented` | `POST` | `/api/kb/{kb_id}/export` | 导出 KB 数据为 JSON | `apiPost('kb/{id}/export')` | JSON |
| `implemented` | `POST` | `/api/kb/{kb_id}/import` | 导入 KB JSON 数据 | `apiPost('kb/{id}/import', formData)` | multipart |

## 5. 接口详情

### 5.1 `POST /api/chat`

- 状态：`agreed`
- 用途：聊天面板发起 RAG 流式问答。
- 持续滚动增量协议（2026-10-28 设计中，尚未实现或验收）：新客户端通过 `history_mode: "append-v1"`、会话 ID、服务端历史版本 `base_history_revision`、单轮稳定的 `turn_idempotency_key` 和新增消息提交请求。身份与删除 generation 由宿主确认，版本令牌及幂等键均不授予权限；版本过期或同一键对应不同内容应在执行模型或工具前拒绝。首次会话使用服务端确认的初始版本；旧客户端未提供 `history_mode` 时继续使用现有完整 `messages` 协议。需要断线恢复时读取服务端请求状态及已保存事件，不通过重复发起可能执行工具的聊天请求补救。接口字段、状态查询及前后端接线仍待实现与统一验证。
- 前端入口：`apiSSE('chat', body, { onEvent, onDone })`
- 请求 Header：

| Header | 值示例 |
| --- | --- |
| `X-API-Key` | `sk-proj-xxx` |
| `X-Model` | `gpt-4o` |
| `X-Provider` | `openai` |

- 请求体：

```json
{
  "messages": [
    { "role": "user", "content": "ESP32 I2C NACK 怎么排查？", "message_id": "msg-uuid" }
  ],
  "top_k": 5,
  "relevance_threshold": 0.5,
  "kb_ids": ["kb-001"],
  "temperature": 0.2,
  "max_tokens": 8192,
  "system_prompt": "你是一个嵌入式硬件助手",
  "long_term_memory": "",
  "model": "gpt-4o",
  "use_agent": true,
  "history_mode": "server",
  "permission_mode": "default",
  "tool_keys": { "tavily": "tvly-xxx", "tavily_base_url": "https://api.tavily.com" }
}
```

- `messages[]` 可选字段：`message_id` 是客户端提供的稳定消息 ID，缺省以兼容旧客户端。它只是定位提示，不授予访问或写入权限；来源绑定由服务端在当前 owner/session/request scope 下，通过 V3 owner reader 校验已持久化消息、source map、locator、快照和 generation fence。ID 作为独立 sidecar 与 `_build_agent_messages` 生成的 role/content 顺序逐项对应；它不进入 LangChain 消息字段、Agent/model prompt 或 SSE。当前请求最后一条 user 消息尚未写入 owner 历史，因此该槽位用 `null` 标记，且必须留在未归档的 recent 后缀；它不会参与 owner reader 绑定或 compaction archive。已发布 checkpoint 替换历史前缀时，checkpoint sidecar 为 `null`，经过对象身份核验的原尾消息继续携带原 ID，最后一条 user 仍为 `null`。唯一允许的 system 消息是紧邻当前 user 之前、无 ID 的纯文本引用；引用与当前 user 作为连续的 `null` transient 后缀留在 recent，不传给 owner reader 或 compaction archive。旧客户端缺少历史消息 ID，非尾部或带 ID 的 system 消息、tool/非纯文本消息、role/content/order 不匹配或历史 ID 洞、缺失或冲突的 owner/source 映射、current user 或 transient 后缀未留在 recent、head/fence 变化，仍使 V3 压缩 fail closed；原历史保持不变，不回退到不带来源绑定的旧自动压缩。若完整历史仍超出上下文预算，按现有路由预算错误返回。

- `history_mode`（可选，默认 `"client"`）：
  - `"client"`：保持上述由请求体提供完整历史的旧契约。
  - `"server"`：要求同时提供 `session_id`，且会话必须是当前认证 principal 的 owner-bound 会话。服务端按 owner/session 从持久化 Message 历史读取全部已保存 user/assistant 轮次，`messages` 只用于当前 user 轮和紧邻的 quote system 消息。客户端多发的旧历史不会进入模型上下文，也不能注入伪造的 assistant 历史。该模式使长对话的请求体不随历史增长，压缩、分页和刷新恢复以服务端历史与已发布 checkpoint 为准。会话不是 owner-bound、owner/incarnation/删除 epoch 无法确认、当前轮已存在 assistant 回答或内容冲突时，服务端保留原历史并返回 `HISTORY_UNAVAILABLE` 失败终态，不静默降级为只回答当前问题。

- SSE 事件类型：

| 事件类型 | 发生时机 | 前端消费字段 |
| --- | --- | --- |
| `thinking` | RAG 检索、Agent 思考或推理输出 | `content`、`source` |
| `text` | LLM 逐 token 输出 | `content` |
| `tool` | Agent 调用工具时（旧事件，向后兼容） | `name`、`icon`、`args`、`result` |
| `tool_call` | Agent 发起工具调用（args 为对外参数；Skills 读取工具有专用安全投影） | `tool`、`args`、`call_id`、`step_index` |
| `tool_result` | 工具执行完成（含 result + duration） | `call_id`、`tool`、`result`、`duration`、`success`、`step_index` |
| `tool_confirm_required` | 权限门控需要用户确认（HITL） | `call_id`、`tool`、`args`、`risk_level`、`reason` |
| `source` | RAG 检索到来源时 | `id`、`title`、`doc`、`page`、`score`、`excerpt` |
| `todo_update` | Agent 更新任务清单（todo_write 工具） | `todos`（含 `content`/`status`/`priority`） |
| `progress` | 长任务进度更新（编译/烧录/循环提示等） | `percent`、`message` |
| `heartbeat` | 长时间等待模型或工具时保持连接 | 无业务字段 |
| `done` | 流结束时 | `success`、`completed?`、`awaiting_confirmation?`、`usage?` |
| `error` | 出错时（含 `code` + `message`） | `code`、`message` |

- 字段说明：
  - `messages` 为历史消息数组，最后一条通常为当前用户消息；`content` 支持字符串或 OpenAI 格式的多模态数组 `[{type:"text",text:"..."},{type:"image_url",image_url:{url:"..."}}]`。
  - `top_k`、`temperature`、`max_tokens`、`system_prompt`、`long_term_memory`、`model` 均为可选参数，后端有默认值；`system_prompt` 为空时后端使用默认系统提示词。
  - 检索范围参数契约（2026-10-04）：`top_k` 限制每次 `search_docs` 检索的候选/结果数，不是整条回答可用来源数上限；`relevance_threshold` 为 `0.0`–`1.0`，前端设置页的 `relevanceThreshold`（`0`–`100`）发送时除以 100；缺省或空数组 `kb_ids` 表示检索全部启用的知识库，非空数组表示限定知识库，不能用空数组关闭 RAG。
  - 阈值只用于过滤向量检索候选；不据此过滤仅由关键词检索得到的候选，也不把阈值套到 RRF 内部排序分数或 reranker 分数上。来源事件的 `score` 是检索/展示分数，不是答案正确概率，也不是 RRF 内部排序分数。
  - 来源编号契约（2026-10-04）：`source.id` 使用 `srcN`，编号在一次用户请求内由 RAG 与 web 来源共同分配；同一请求中的相同证据复用编号，独立请求可重新从 `src1` 开始，确认续跑沿用该请求的服务器端来源状态。该状态不是客户端请求或 SSE 字段。
  - 实现状态边界（F1，2026-10-04）：Agent `ToolContext` 持有服务端来源注册器；新用户请求建立新状态，HITL 确认续跑从服务端请求快照恢复同一状态，客户端请求和 SSE 不携带该对象。冻结候选的全套后端自动化测试为 606 项通过；这不等同于真实 API、浏览器或质量评测验收。当前 `ChatRequest` 字段声明提供类型与默认值，但没有显式的 `top_k` / `relevance_threshold` 范围校验器；本契约约定不代表服务端已对越界聊天参数返回 `422`，也不标记端到端 `verified`。
  - `use_agent`（可选，默认 `false`）：是否走 LangGraph ReAct Agent 主路径；`true` 时后端优先用 Agent，失败降级为基础 LLM 流。
  - `permission_mode`（可选，默认 `"default"`）：Agent 工具调用门控模式，枚举 `"bypassPermissions"` / `"default"` / `"acceptEdits"`，按既有风险分类决定。`ALWAYS` 工具（如原生 Skill 安装）与 MCP 的确认不会因 bypass 或 Skills 模式而免除。
  - `tool_keys`（可选，默认 `{}`）：Agent 工具所需 API Key 注入，目前支持 `tavily`（联网搜索 API Key）和 `tavily_base_url`（可选，Tavily 兼容端点 Base URL，留空时使用官方 Tavily 服务）。
  - `skills_mode` 枚举 `off` / `auto` / `manual`，默认 `off`；`skill_ids` 为手动模式已启用技能 ID 列表，1～3 个且不重复。自动模式只提供名称、描述和标识/版本元数据，再按需加载正文和资料；手动模式明确加载所选技能。off 不自动发现旧包，但 Agent 收到明确安装请求后可安装并在同请求加载这次新增的包，不必再去设置启用。
  - Skills 不施加普通工具/MCP 全局只读交集，也不授予额外权限。普通工具、脚本命令和已配置 MCP 沿用各自禁用、风险、确认及路径规则；导入本身不运行脚本。`allowed-tools` 为实验性元数据而非权限授权。不支持工具调用的模型明确失败，不声称已使用技能；本轮源码接入与集中验证边界见 §5.37 和 testing.md。
  - 已实现记忆语义：`long_term_memory` 最多 4,000 字符，省略时读取已保存设置，显式 `""` 表示本次不使用；普通聊天、Agent 与确认续跑使用同一请求快照，注入一次。记忆是偏好背景，不是权限；验证边界见 testing.md。
  - `thinking` 的 `content` 为人类可读思考文本；`source` 枚举 `rag` / `llm` / `reasoning`，标识思考来源。
  - `source` 的 `id` 为文档片段唯一标识；`doc` 为所属文档 ID；`page` 为可选页码/位置；`score` 为相似度得分；`excerpt` 为命中片段摘要。
  - `tool` 的 `icon` 为前端展示图标名；`args` 为结构化调用参数；`result` 为工具执行结果字符串（聊天流中可空，完成后再填充）。
  - `tool_call` 的 `call_id` 为工具调用唯一标识，用于和 `tool_result` / `tool_confirm_required` 关联；`step_index` 为 Agent 执行步骤序号。
  - `tool_result` 的 `duration` 为工具执行耗时（毫秒）；`success` 为布尔值。
  - Skills 安全轨迹增量契约（2026-10-11，候选 A 步作者已实现、独立复核待完成，未同步主树或运行副本）：仅精确 `load_skill` / `read_skill_resource` 的 `tool_call.args` 和内部轨迹历史使用安全投影。只允许合法 `skill_id`，资源工具另可包含规范的 `references/` 或 `assets/` 相对 `path`；非法字段省略，未知字段不透传。真实执行参数、确认所需原始参数及授权语义不改变。
  - 上述两工具的 `tool_result.result` 显式重建 `{success, output, data, error, metadata}`，不透传未知顶层字段、技能或资源正文、任意输出/错误文案。`data` 仅允许经验证的 `skill_id`、64 位小写十六进制 `content_hash`、与工具匹配的 `stage`（`instructions` / `resource`）、资源相对 `path` 以及布尔 `success`；失败可缺少元数据或令 `data=null`。成功须具有完整合法追踪字段、外层 `success` 严格为 true 且内层 `data.success` 存在并严格为 true，同时无错误；缺失、错误类型或矛盾时失败关闭，事件外层 `success` 与投影一致。候选已用真实 converter 复现两个缺失内层成功字段的红灯并完成两文件作者窄修；作者完整集成 64 项、独立新增 9 项及来源兼容 5 项通过，00 接受后端 A 软件指定范围，未同步主树或运行副本，非完整 S02 验收。路径规范化沿既有仓库大小写兼容，不将目录强制转为小写或改变实际资源定位。
  - 安全结果使用固定文案：成功为 `Skill instructions loaded` / `Skill resource loaded`，失败为 `Skill operation failed`；失败 error 固定为 `SKILL_READ_FAILED` 及通用提示，不保留原始错误内容。metadata 仅保留可信工具名/调用关联和合法 duration、timestamp、compacted，不触发来自技能结果的 todo 事件。非 Skills 工具和 reasoning 契约不变。本步尚未覆盖前端展示、旧 activity 读回、CRUD 新持久化或 localStorage，不能据此声称整个历史链已脱敏；不迁移或删除旧数据库。
  - `webfetch` 失败沿用工具失败封套：HTTP 错误、被拒绝的重定向、连接异常和空正文返回 `success=false`、`data=null`、`error.error_type=EXEC_ERROR`；超时为 `TIMEOUT`。错误信息不含原始 URL、用户凭据或查询串。重定向不自动跟随；成功载荷仍为 `url/content/prompt/truncated`，不把普通工具数据中的 `error` 字段一律解释成失败。
  - WebFetch 请求期间的 HTTPX URL 日志另做请求范围脱敏，保留状态诊断；不改变上述载荷或授权语义，也不承诺所有工具、依赖或调用参数审计均已脱敏。
  - `tool_confirm_required` 触发时前端弹出 `ConfirmDialog`，用户决策后调 `POST /api/agent-sandbox/resume` 恢复；`risk_level` 枚举 `low` / `medium` / `high`。
  - `error` 的 `code` 枚举新增：`RECURSION_LIMIT`（Agent 轮次上限）、`CONTEXT_LIMIT`（累积 token 超限）、`HISTORY_UNAVAILABLE`（`history_mode=server` 时无法确认 owner-bound 服务端历史）、`AGENT_TIMEOUT`（执行超时）、`AGENT_FALLBACK`（Agent 失败降级）、`AGENT_ERROR`（Agent 运行异常）。
  - `done` 的 `usage` 可选，包含本次请求的 token 统计：`prompt_tokens`、`completion_tokens`、`total_tokens`。
  - `/api/chat` 终态区分传输结束与回答完成：正常回答为 `success=true, completed=true`；等待人工确认为 `success=true, completed=false, awaiting_confirmation=true`；失败为 `success=false, completed=false`。旧记录和普通 resume 成功 fallback 可能没有新字段，但显式 `completed=false` 不能解释为完整回答。人工确认等待必须保留待确认调用与请求快照，不计为成功 RAG 样本。
  - 模型流正常结束却只有推理或空白正文时，以及精确已知的上游零输出报文，使用 `MODEL_EMPTY_RESPONSE`，不是网络超时。Agent 连续 300 秒没有可见正文/推理或有效工具/状态进展时使用 `TIMEOUT`；自身心跳和空 chunk 不刷新进展计时，活动工具仍受原工具超时限制。通用异常仍可为 `INTERNAL_ERROR`。保留已输出正文，不自动重放整次 Agent 或已执行工具。

- SSE 示例：

```json
{ "type": "thinking", "content": "正在检索知识库...", "source": "rag" }
{ "type": "source", "id": "src1", "title": "ESP32 Technical Reference Manual", "doc": "doc-001", "page": 42, "score": 0.92, "excerpt": "I2C 总线需要 4.7kΩ 上拉电阻...", "kb_id": "builtin-001", "kb_name": "硬件手册库" }
{ "type": "tool", "name": "search_docs", "icon": "search", "args": { "query": "ESP32 I2C NACK" }, "result": "" }
{ "type": "thinking", "content": "已找到相关资料，正在生成回答。", "source": "reasoning" }
{ "type": "text", "content": "先检查上拉电阻和时钟配置 [src1]。" }
{ "type": "done", "success": true, "usage": { "prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200 } }
{ "type": "error", "message": "模型调用失败" }
```

- 约束：
  - `messages` 至少包含一条用户消息。
  - `type` 枚举只允许：`thinking`、`text`、`tool`、`source`、`progress`、`done`、`error`。
  - `done` 结束当前流。聊天失败可先发送一次 `error`，再发送一次失败 `done`；前端不得因后者或流关闭把错误覆盖成“回答完成”。等待人工确认后的流关闭也不能清除续跑所需信息。这些约束不改变编译、烧录接口各自的 `done` 载荷。
  - 除 `messages` 外，其余字段均非必填，后端有默认值。

- Mock 规则：前端先使用固定 `thinking → text → done` 三段流占位。

### 5.2 `POST /api/models`

- 状态：`agreed`
- 用途：根据用户配置的 API Key 和 Base URL 获取可用模型列表。
- 前端入口：`apiPost('models', { base_url })`
- 请求 Header：`X-API-Key`
- 请求体：

```json
{
  "base_url": "https://api.openai.com/v1"
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "models": ["gpt-4o", "gpt-4.1-mini"]
  }
}
```

- 错误响应：

| 场景 | code |
| --- | --- |
| 未提供 API Key 或 Key 无效 | `AUTH_FAILED` |
| 模型列表获取失败 | `MODEL_FETCH_FAILED` |

```json
{
  "success": false,
  "error": {
    "code": "MODEL_FETCH_FAILED",
    "message": "模型列表获取失败",
    "details": null
  }
}
```

### 5.3 知识库管理

知识库接口分为两层：
- **文档层**：`/api/kb/upload`、`/api/kb/list`、`/api/kb/delete` — 上传/列出/删除单个文档
- **Collection 层**：`/api/kb/collections/*` — 管理知识库本身（多 KB、不同 embedding、开关）

#### `POST /api/kb/upload`

- 状态：`implemented`
- 用途：上传知识库文档，自动解析、分块（hybrid/agent）、向量化入库到指定 KB。
- 前端入口：`apiPost('kb/upload', formData)`，需传 `FormData`，非 JSON。
- 请求体：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | File | 是 | 上传的文件，支持 `pdf`/`md`/`txt`/`xlsx`/`csv`/`json` |
| `kb_id` | string | 否 | 目标知识库 ID，默认 `builtin-001` |
| `chunk_method` | string | 否 | 覆盖 KB 默认分块方式：`hybrid` / `agent` |
| `chunk_size` | int | 否 | 覆盖默认 chunk_size（仅 hybrid 有效） |
| `small_chunk_size` | int | 否 | 覆盖 KB 默认 small_chunk_size（仅 hybrid 有效，可选 `500`/`800`/`1200`/`2000`）。未传时使用 KB 配置中的 `small_chunk_size` |

- 成功响应：

```json
{
  "success": true,
  "data": {
    "doc_id": "uuid-xxx",
    "kb_id": "builtin-001",
    "filename": "esp32_datasheet.pdf",
    "chunk_method_used": "hybrid",
    "chunks": 0,
    "status": "indexing"
  }
}
```

- 说明：上传后立即返回 `status: "indexing"`，后台异步入库。前端可通过 `GET /api/kb/list` 轮询文档状态（`indexing` → `indexed` / `error`）。
- 状态约束：`indexed` 只能表示本次入库成功且生成了有效向量；零有效分块、零向量或入库异常应进入 `error`，并提供非空 `error_message`。HTTP 上传成功仅表示已接受后台任务，不代表索引成功。
- 前端约束：只在 `indexing` 时持续轮询；`indexed` / `error` 都是终态。失败原因应可见，不得将失败文档展示为可检索成功。

- 错误响应：

```json
{
  "success": false,
  "error": {
    "code": "UPLOAD_FAILED",
    "message": "文件上传或解析失败",
    "details": null
  }
}
```

#### `GET /api/kb/list`

- 状态：`implemented`
- 用途：列出所有已上传的知识库文档，可按 `kb_id` 过滤。
- 前端入口：`apiGet('kb/list')` 或 `apiGet('kb/list?kb_id=xxx')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "documents": [
      {
        "doc_id": "uuid-xxx",
        "kb_id": "builtin-001",
        "title": "esp32_datasheet.pdf",
        "category": "user_upload",
        "file_type": "pdf",
        "file_size": 1048576,
        "chunk_count": 128,
        "chunk_method_used": "hybrid",
        "status": "indexed",
        "error_message": null,
        "created_at": "2026-06-19T10:00:00Z"
      }
    ]
  }
}
```

#### `POST /api/kb/delete`

- 状态：`implemented`
- 用途：删除知识库中的一篇文档及其向量数据。
- 前端入口：`apiPost('kb/delete', { doc_id: "uuid-xxx" })`
- 请求体：

```json
{
  "doc_id": "uuid-xxx"
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "doc_id": "uuid-xxx",
    "deleted_chunks": 128
  }
}
```

#### `GET /api/kb/collections`

- 状态：`implemented`
- 用途：列出所有知识库（含统计信息）。
- 前端入口：`apiGet('kb/collections')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "collections": [
      {
        "id": "builtin-001",
        "name": "硬件手册库",
        "description": "系统内置硬件手册知识库",
        "collection_name": "hardware-docs",
        "chunk_method": "hybrid",
        "small_chunk_size": 800,
        "embedding_model": "text-embedding-3-small",
        "agent_chunker_model": "gpt-4o-mini",
        "enabled": true,
        "is_builtin": true,
        "doc_count": 10,
        "chunk_count": 1280,
        "created_at": "2026-06-19T10:00:00Z"
      }
    ]
  }
}
```

#### `POST /api/kb/collections`

- 状态：`implemented`
- 用途：创建新知识库。
- 前端入口：`apiPost('kb/collections', body)`
- 请求体：

```json
{
  "name": "ESP32 笔记库",
  "description": "我整理的 ESP32 踩坑记录",
  "chunk_method": "hybrid",
  "small_chunk_size": 800,
  "embedding_model": "text-embedding-3-small",
  "embedding_base_url": "https://api.openai.com/v1",
  "embedding_api_key": "sk-...",
  "agent_chunker_model": "gpt-4o-mini",
  "agent_chunker_base_url": "https://api.openai.com/v1",
  "agent_chunker_api_key": "sk-...",
  "context_window": 256000
}
```

- 字段说明：
  - `chunk_method`：`hybrid`（默认）或 `agent`
  - `small_chunk_size`：HybridChunker 目标 chunk 大小，可选 `500`（高精度，易截断）/ `800`（平衡，默认）/ `1200`（中等文档）/ `2000`（超长手册）。仅 `hybrid` 有效，`agent` 模式忽略
  - `embedding_api_key` / `agent_chunker_api_key`：明文传入，后端用 Fernet 加密存储
  - `context_window`：Agent 分块用的上下文窗口，默认 256000

- 成功响应：

```json
{
  "success": true,
  "data": {
    "id": "kb-a1b2c3d4",
    "name": "ESP32 笔记库",
    "description": "我整理的 ESP32 踩坑记录",
    "collection_name": "kb_kb_a1b2c3d4",
    "chunk_method": "hybrid",
    "small_chunk_size": 800,
    "embedding_model": "text-embedding-3-small",
    "agent_chunker_model": "gpt-4o-mini",
    "enabled": true,
    "is_builtin": false,
    "created_at": "2026-06-19T10:00:00Z"
  }
}
```

#### `GET /api/kb/collections/{kb_id}`

- 状态：`implemented`
- 用途：获取单个知识库详情（含文档列表）。
- 前端入口：`apiGet('kb/collections/{kb_id}')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "id": "kb-a1b2c3d4",
    "name": "ESP32 笔记库",
    "description": "...",
    "collection_name": "kb_kb_a1b2c3d4",
    "chunk_method": "hybrid",
    "small_chunk_size": 800,
    "embedding_model": "text-embedding-3-small",
    "embedding_base_url": "https://api.openai.com/v1",
    "agent_chunker_model": "gpt-4o-mini",
    "agent_chunker_base_url": "https://api.openai.com/v1",
    "context_window": 256000,
    "enabled": true,
    "is_builtin": false,
    "created_at": "2026-06-19T10:00:00Z",
    "documents": [
      {
        "doc_id": "uuid-xxx",
        "title": "esp32_notes.md",
        "file_type": "md",
        "file_size": 2048,
        "chunk_count": 5,
        "chunk_method_used": "hybrid",
        "status": "indexed",
        "created_at": "2026-06-19T10:00:00Z"
      }
    ]
  }
}
```

#### `DELETE /api/kb/collections/{kb_id}`

- 状态：`implemented`
- 用途：删除知识库（内置 KB 不可删除）。同时删除 ChromaDB collection、BM25 索引、关联文档记录。
- 前端入口：`apiDelete('kb/collections/{kb_id}')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "kb_id": "kb-a1b2c3d4"
  }
}
```

- 错误响应：

| 场景 | code | 状态码 |
| --- | --- | --- |
| KB 不存在 | `KB_NOT_FOUND` | 404 |
| 内置 KB 不可删除 | `KB_NOT_DELETABLE` | 400 |

#### `PATCH /api/kb/collections/{kb_id}/toggle`

- 状态：`implemented`
- 用途：切换知识库搜索开关。关闭后该 KB 不参与 `search_all_enabled`。
- 前端入口：`apiPatch('kb/collections/{kb_id}/toggle', { enabled: true })`
- 请求体：

```json
{
  "enabled": true
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "kb_id": "kb-a1b2c3d4",
    "enabled": true
  }
}
```

#### `POST /api/kb/embedding-models`

- 状态：`implemented`
- 用途：代理上游 embedding 模型列表。用 POST 是因为需要传 API Key（避免走 URL/logs）。
- 前端入口：`apiPost('kb/embedding-models', { base_url, api_key })`
- 请求体：

```json
{
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-..."
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "models": ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"]
  }
}
```

- 说明：后端会过滤名称含 `embed` 的模型；若无匹配则返回全部模型。

### 5.4 `GET /api/devices`

- 状态：`agreed`
- 用途：扫描当前可用串口设备。
- 前端入口：`apiGet('devices')`
- 不需要认证 Header。
- 成功响应：

```json
{
  "success": true,
  "data": {
    "devices": [
      { "port": "COM3", "description": "USB Serial Device" }
    ]
  }
}
```

### 5.5 `POST /api/wiring`（已移除）

- 状态：`removed`（2026-11-08）
- 接线图工作台与 SVG 生成已下线；需要接线示意图时使用 Agent 的 `image_generation` 工具。

### 5.6 `POST /api/audit_pins`

- 状态：`agreed`
- 用途：检查引脚占用冲突、Strapping 风险或不安全分配。
- 前端入口：`apiPost('audit_pins', body)`
- 请求体：

```json
{
  "chip": "esp32-s3",
  "pin_assignments": {
    "GPIO0": { "function": "BUTTON", "config": "INPUT_PULLUP" },
    "GPIO2": { "function": "WS2812_DATA", "config": "OUTPUT" }
  }
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "conflicts": [],
    "warnings": [],
    "pin_map": {}
  }
}
```

- 约束：`pin_assignments` 为对象（key=引脚名，value=分配信息），非数组。`conflicts` / `warnings` 为空数组表示无问题。

### 5.7 `POST /api/build`

- 状态：`implemented`（基于 PlatformIO 真实编译，见 `backend/src/hardware/pio_runner.py`）
- 用途：触发固件编译。
- 前端入口：`apiSSE('build', body, { onEvent, onDone })`
- 请求体：

```json
{
  "code": "void setup() {...} void loop() {...}",
  "board": "esp32-s3",
  "options": {}
}
```

- 支持板型：`esp32-s3` / `esp32` / `esp32-c3` / `esp32-s2`（自动映射到 PlatformIO board id，定义在 `pio_runner.BOARD_MAP`）。
- SSE 事件：

| 事件类型 | 前端消费字段 | 说明 |
| --- | --- | --- |
| `thinking` | `content`, `source` | 思考中提示，`source="build"` |
| `progress` | `percent`（0-100 整数）, `message` | 进度更新 |
| `compile_log` | `line`, `stream` | PlatformIO 原始输出（编译日志/警告/错误），`stream="stdout"` |
| `done` | `success`（true/false）, `binary_path?`, `error?` | 编译结束终态事件；成功含 `binary_path`，失败含 `error.code` |

- SSE 错误码（`done.error.code`）：

| 错误码 | 含义 | 排查 |
| --- | --- | --- |
| `PIO_NOT_INSTALLED` | 未装 platformio | `pip install platformio` |
| `COMPILE_FAILED` | 编译失败 | `details` 含 stderr 最后 50 行（`LOG_TAIL_LINES = 50`） |
| `COMPILE_TIMEOUT` | 编译超时 | 默认 300s，可用环境变量 `HWRAG_COMPILE_TIMEOUT` 覆盖 |
| `UNSUPPORTED_BOARD` | `board` 不在 `BOARD_MAP` | 改用支持的板型 |
| `SPAWN_FAILED` | 子进程启动失败 | 检查 `pio` 可执行权限/PATH |

- SSE 示例：

```json
{ "type": "thinking", "content": "正在编译固件...", "source": "build" }
{ "type": "progress", "percent": 5, "message": "已创建临时项目" }
{ "type": "compile_log", "line": "Compiling .pio/libdeps/esp32s3/...", "stream": "stdout" }
{ "type": "done", "success": true, "binary_path": ".build/tmp/abc123/.pio/build/esp32s3/firmware.bin" }
{ "type": "done", "success": false, "error": { "code": "COMPILE_FAILED", "message": "编译失败", "details": "..." } }
```

- 约束：
  - 临时项目目录 `.build/tmp/{session_id}/`，启动时清理 24 小时前的目录（`cleanup_old_builds`，`DEFAULT_CLEANUP_AGE_HOURS = 24`）。
  - `binary_path` 永远以 `.build/tmp/` 为前缀（`ALLOWED_BINARY_PREFIX`），前端/Agent 不应改写路径，后端在 upload 阶段会校验。
  - 编译超时受 `HWRAG_COMPILE_TIMEOUT` 环境变量覆盖（默认 300s，`DEFAULT_COMPILE_TIMEOUT_S`）；首次编译会下载 ESP32 工具链 200-500MB，可能超时。

### 5.8 `POST /api/upload`

- 状态：`implemented`（基于 PlatformIO `pio run --target upload`，见 `pio_runner.upload_firmware`）
- 用途：编译并烧录固件到指定串口设备。
- 前端入口：`apiSSE('upload', body, { onEvent, onDone })`
- 请求体支持两种模式：

```json
// 模式 1：已有 binary_path（前序 /api/build 产出，推荐）
{
  "binary_path": ".build/tmp/abc123/.pio/build/esp32s3/firmware.bin",
  "port": "COM3",
  "board": "esp32-s3",
  "options": {}
}

// 模式 2：仅传 code，先编译再烧录（事件流 = compile 事件 + upload 事件）
{
  "code": "void setup() {...} void loop() {...}",
  "port": "COM3",
  "board": "esp32-s3",
  "options": {}
}
```

- `binary_path` 与 `code` 二选一，同时为空返回 `INVALID_ARGS`。
- SSE 事件：编译阶段沿用 §5.7 的 `thinking`/`progress`/`compile_log`。模式 2 编译成功时发送非终态 `thinking`（“编译成功，准备开始烧录...”），随后继续上传；只有编译失败或烧录结束才发送终态 `done`。

| 事件类型 | 前端消费字段 | 说明 |
| --- | --- | --- |
| `thinking` | `content`, `source` | `source="flash"` 表示进入烧录阶段 |
| `progress` | `percent`, `message` | 烧录进度（如 `Connecting....`） |
| `compile_log` | `line`, `stream` | esptool 原始输出 |
| `done` | `success`, `error?` | 烧录结束终态事件 |

- 烧录模式的 `done` 事件会在后端释放该端口的烧录锁后发送，避免前端过早重连。
- 若烧录前串口监视已连接到同一端口，前端在收到终态事件后会尝试用原端口和波特率恢复连接；之前未连接或连接的是其他端口时不会自动打开串口监视。

- SSE 错误码（`done.error.code`）：

| 错误码 | 含义 | 排查 |
| --- | --- | --- |
| `INVALID_ARGS` | `binary_path` 与 `code` 均为空 | 二选一传值 |
| `INVALID_BINARY_PATH` | `binary_path` 不以 `.build/tmp/` 开头 | 路径穿越防护，仅接受前序 build 产出的路径 |
| `BINARY_NOT_FOUND` | `binary_path` 对应文件不存在 | 重新调用 /api/build |
| `PORT_NOT_FOUND` | `port` 不在系统串口列表 | 检查设备连接/驱动 |
| `PORT_BUSY` | 该端口已被另一个烧录任务占用 | 等待前一个烧录完成 |
| `UPLOAD_TIMEOUT` | 烧录超时 | 默认 60s，可用 `HWRAG_UPLOAD_TIMEOUT` 覆盖 |
| `PIO_NOT_INSTALLED` | 未装 platformio | 同 §5.7 |
| `SPAWN_FAILED` | 子进程启动失败 | 同 §5.7 |
| `COMPILE_FAILED` / `COMPILE_TIMEOUT` / `UNSUPPORTED_BOARD` | 模式 2 编译阶段失败 | 同 §5.7 |

- 烧录参数：
  - `upload_speed = 921600`（`pio_runner.DEFAULT_UPLOAD_SPEED`，写死在生成的 `platformio.ini`）。
  - 同端口并发防护：模块级 `_upload_locks: dict[str, asyncio.Lock]`，`_check_upload_input` 用 `lock.locked()` 预检，`_stream_upload` 用 `async with lock` 获取。预检与获取非原子（TOCTOU），demo 场景够用。
- 约束：
  - `pio_runner.upload_firmware` **不自动触发编译**，`binary_path` 必须由前序 BuildTool/`/api/build` 产出。模式 2 的"先编译再烧录"编排由 `build_routes._run_upload_steps` 做，不在 pio_runner 层（避免参数不可见）。
  - 临时项目目录同 §5.7（`.build/tmp/{session_id}/`）。
  - 模式 2 会生成两个不同的 `session_id`（compile 一个、upload 一个），临时目录互不影响。

### 5.9 `POST /api/tool`

- 状态：`implemented`（权限门禁和后端测试已实现，前端直接调用尚未联调）
- 用途：**前端直接调用 Agent 工具**（如接线图面板上的"检查引脚"按钮）。注意与 SSE chat 中的 `type: "tool"` 事件的区别——后者是后端在聊天流中报告"Agent 内部调用了工具"，不经过此端点。
- 前端入口：暂无已接入的直接调用方。此端点返回原始 `ToolResultEnvelope`，前端接入时需适配其结构；通用 `apiPost` 的 `success/data` 解包会丢失顶层 `output` 和 `error.error_type`。
- 请求体：

```json
{
  "tool": "audit_pins",
  "args": {
    "chip": "esp32-s3"
  }
}
```

- 成功响应：

```json
{
  "success": true,
  "output": "工具返回结果文本",
  "data": null,
  "error": null,
  "metadata": {
    "tool_name": "audit_pins",
    "duration_ms": 1240,
    "call_id": "abc123",
    "timestamp": "2026-09-24T12:00:00+00:00",
    "kb_coverage_hint": null,
    "compacted": false
  }
}
```

- 约束：
  - `tool` 字段为工具名（字符串），非 `name`。
  - 工具调用先经过 `PermissionClassifier`。`allow` 才会派发；`ask` / `deny` 不执行工具。该端点没有交互确认与恢复流程。
  - `ConfirmationRule.ALWAYS` 工具即使处于 `bypassPermissions` 模式也要求聊天确认；直接 `/api/tool` 无法恢复确认流程，因此返回 `PERMISSION_CONFIRMATION_REQUIRED` 且不派发。`image_generation` 属于此类工具。
  - `run_command` 只有在命令可明确判断为只读时才可自动放行；未知命令、复合 shell 语法、输出文件选项或外部 diff/textconv 辅助选项至少要求确认。
  - `output` 为纯文本或 JSON 字符串，`duration_ms` 位于 `metadata`。
- 权限拒绝响应（HTTP 200，原始 `ToolResultEnvelope`）：

```json
{
  "success": false,
  "output": "",
  "data": null,
  "error": {
    "error_type": "PERMISSION_CONFIRMATION_REQUIRED",
    "error_message": "tool 'flash_firmware' requires interactive confirmation",
    "suggestion": "请在聊天流程中确认此操作；直接 API 调用不会执行需要确认的工具。",
    "retryable": false
  },
  "metadata": {
    "tool_name": "flash_firmware",
    "duration_ms": 0,
    "call_id": "abc123",
    "timestamp": "2026-09-24T12:00:00+00:00",
    "kb_coverage_hint": null,
    "compacted": false
  }
}
```

- `error.error_type`：`PERMISSION_CONFIRMATION_REQUIRED` 表示需要交互确认，`PERMISSION_DENIED` 表示权限策略拒绝。两者均不派发工具且不可通过本端点恢复；未找到工具沿用 `TOOL_NOT_FOUND`。这几个值不是 §2.14 的顶层 `error.code`。
- 路由内部未预期异常仍可能返回 `{ "success": false, "error": { "code": "TOOL_ERROR", "message": "...", "details": "..." } }`（HTTP 200）；这是该路由的历史响应格式，与权限拒绝的 `ToolResultEnvelope` 不同。

### 5.10 `WS /api/monitor/{port}?baud=`

- 状态：`agreed`
- 用途：推送串口实时数据到前端 Serial Monitor。
- 前端入口：`apiWS('/api/monitor/' + port + '?baud=' + baud, onMessage)`
- 路径参数：`port`，串口号，例如 `COM3`。
- 查询参数：`baud`，波特率，例如 `115200`。
- 连接后行为：前端连接成功后立即发送 `{ "type": "start" }`，后端持续推送串口数据。
- 数据帧格式：后端发送 JSON 对象 `{ "type": "data", "payload": "串口文本" }`。确需发送纯文本时前端兜底包装。
- Mock 规则：连接失败时前端显示错误日志，不再 fallback 到模拟数据。


### 5.11 `POST /api/sessions`

- 状态：`agreed`
- 用途：创建新对话会话。
- 请求体：

```json
{
  "title": "新对话",
  "model": "gpt-4o",
  "project": "嵌入式开发"
}
```

- 成功响应：

```json
{
  "id": "s3ed52d63",
  "title": "新对话",
  "model": "gpt-4o",
  "project": "嵌入式开发",
  "pinned": false,
  "msg_count": 0,
  "created_at": "2026-06-19T12:00:00"
}
```

### 5.12 `GET /api/sessions`

- 状态：`agreed`
- 用途：列出所有会话（不含消息体），按更新时间倒序。
- 成功响应：

```json
{
  "sessions": [
    {
      "id": "s3ed52d63",
      "title": "STM32 I2C 调试",
      "model": "agent",
      "project": "嵌入式开发",
      "pinned": false,
      "msg_count": 12,
      "created_at": "2026-06-19T12:00:00",
      "updated_at": "2026-06-19T14:30:00"
    }
  ]
}
```

### 5.13 `GET /api/sessions/{session_id}`

- 状态：`agreed`
- 用途：获取单个会话及其所有消息。
- 路径参数：`session_id` — 会话 ID。
- 成功响应：

```json
{
  "id": "s3ed52d63",
  "title": "STM32 I2C 调试",
  "model": "agent",
  "pinned": false,
  "msg_count": 2,
  "created_at": "2026-06-19T12:00:00",
  "messages": [
    {
      "id": "m1a2b3c4",
      "role": "user",
      "content": "I2C NACK 怎么排查？",
      "sources": [],
      "tool_calls": [],
      "created_at": "2026-06-19T12:00:05"
    },
    {
      "id": "m5e6f7g8",
      "role": "assistant",
      "content": "在 400kHz 下出现 NACK，最常见的原因是上拉电阻过大...",
      "sources": [{"id": "src1", "title": "I2C 协议规范"}],
      "tool_calls": [{"name": "search_docs", "args": "..."}],
      "created_at": "2026-06-19T12:00:10"
    }
  ]
}
```

### 5.14 `PUT /api/sessions/{session_id}`

- 状态：`agreed`
- 用途：更新会话属性（标题、模型、项目、固定状态）。
- 请求体（全部可选）：

```json
{
  "title": "新标题",
  "model": "gpt-4o",
  "project": "新项目",
  "pinned": true
}
```

- 成功响应：`{ "success": true }`

### 5.15 `DELETE /api/sessions/{session_id}`

- 状态：`agreed`
- 用途：删除会话及其所有消息。
- 成功响应：`{ "success": true, "data": { "id": "<session_id>", "deleted": true } }`（对齐原有实现，未新增响应字段）。
- 删除与暂停请求失效（2026-10-21 候选指定软件范围已独立接受）：成功删除在同一事务更新该会话的私有删除 generation；旧暂停请求之后读取或重存均失效，续跑使用现有 `409 / HITL_REQUEST_SNAPSHOT_UNAVAILABLE`。删除后重新创建同一 ID 不恢复旧快照，新请求可捕获新 generation 正常使用；删除失败不发布失效，其他会话不受影响。内部 marker 复用 Settings，不新增表/列，GET 隐藏其命名空间，PUT 白名单不允许写入；数据库不可读或 marker 损坏时失败关闭。此行为不追溯中止已执行流、撤回发送内容或声称物理清除所有 LangGraph checkpoint。
- S05a 内部事件接缝（2026-10-21，指定软件范围已独立接受）：同一删除事务还清除该会话 `_contextEvent:<session SHA256>:` 私有记录，失败回滚保留记录；其他会话不受影响。此接缝未接生产自动压缩，没有新增公共事件读取端点，不代表已经能恢复任意旧对话或安全擦除 SQLite 文件/日志。
- 持续滚动候选接缝（2026-10-28，已写源码、未测试或同步主树）：删除事务还按会话专属前缀清理滚动历史记录，与删除 generation 更新、原归档清理及会话删除一同提交；旧版不符合新历史存储 ID 约束的会话仍保留原删除路径。设置读取在数据库查询中排除私有历史键。此处不声明 SQLite 文件或日志已安全擦除。
- 错误响应（404）：`{ "success": false, "error": { "code": "NOT_FOUND", "message": "Session not found", "details": null } }`

### 5.16 `POST /api/sessions/{session_id}/messages`

- 状态：`implemented`
- 用途：在会话中添加消息，或按客户端提供的稳定 ID 更新已有消息。
- 请求体：

```json
{
  "id": "m1a2b3c4",
  "role": "user",
  "content": "I2C NACK 怎么排查？",
  "sources": [],
  "tool_calls": [],
  "activity": null
}
```

- 约束：`id` 可选；不传时后端生成。传入已有 ID 且属于本会话时，更新该行的消息内容和附属字段，不增加 `msg_count`；ID 属于其他会话时返回 HTTP 409、错误码 `CONFLICT`。前端重试保存时沿用同一 ID，不重新发起聊天或 Agent 恢复流。
- `role` 使用 `user` / `assistant`。`sources` / `tool_calls` / `activity` 可选。`activity` 为思考步骤/工具调用活动块 `{ durationMs, steps, status }`，仅 assistant 消息有。
- S02 B 可选字段契约（2026-10-11，已同意、尚未实现/验收）：仅精确 `load_skill` / `read_skill_resource` 的步骤可带 `skill_trace`，包含合法 `skill_id`、完整 64 位小写十六进制 `content_hash`、`stage`（`instructions` / `resource`）、严格布尔 `success`，资源阶段还需规范相对 `references/` 或 `assets/` 路径 `path`；保留既有路径大小写兼容。它只是说明信息，不是权限凭据。成功必须与 SSE 外层、内层严格 true 和完整元数据一致，失败可缺元数据；旧消息无此字段仍兼容，但不得回退展示或持久化原始 Skill 参数、正文、任意错误和未知字段。普通工具、reasoning、消息内容/来源/稳定 ID 及本地待保存机制不变。当前只授 B1a 前端数据投影，B1b 页面、B2 POST/GET 服务端 activity 投影另行授权；GET 只投影返回值、不重写旧数据库行，无新端点、SSE 事件、数据库列或迁移。
- 本轮范围保护更新：B1a turn 偏回旧 P0-3 后，00 已撤销继续实现权限、先收范围回执；上条字段为已同意设计，不表示当前仍有实现写权或已实现。无 API、类型、schema 或旧数据库变更验收。
- 最新授权更新（2026-10-11）：02 范围回执已收、写权不恢复。03 短 impact 登记由 00 核验并保留 HIGH/CRITICAL 风险告警后，仅给 03 候选 8 文件 B1a 专用数据投影增量权；上述字段契约仍为待实现/验收，B1b 页面与 B2 服务端 JSON 投影另行授权，无数据库迁移。
- 最新范围更新（2026-10-15）：候选 B1a 前端投影/名称绑定/缓存迁移经 04 独立三文件 100 项及初始化探针、类型检查后由 00 接受指定软件范围，不是主树发布或完整 S02。00 另授 05 仅 ActivityBlock.tsx 和新增测试的 B1b 安全展示：对 exact Skill 工具，只显示既有且有效 trace 的 id/完整 hash/stage/path 和固定状态；缺失/坏 trace 不从旧 rawargs/results 补造，不 JSON 回退显示正文/任意错误/未知字段，不改普通工具、reasoning 或权限。当前 B1b 未实现验收；B2 POST/GET 服务端 activity 白名单投影仍未授权，无端点、事件、列、旧行重写或数据库迁移。
- B1b 状态更新（2026-10-15）：候选组件窄实现已由 05 冻结交接，00 核作者 30 项组件测试与类型检查及两文件 hash；04 已接独立只读复核，尚未验收放行。不变更上述字段契约或通用工具/reasoning 行为，不将 UI trace 作为权限凭据；B2 服务端 POST/GET 投影、运行副本同步和真实数据库操作仍未授权。
- B1b 独立门禁更新（2026-10-15）：独立组件 30 项和类型检查通过，但资源 path 原型继承探针确认不符合自有字段来源要求，故暂不放行。资源 trace 的 path 必须是自有且有效的字段，不得将继承值显示为已验证来源；05 仅获原两 UI 文件增量收紧与持久回归权，后续另做非作者复核。无新字段、端点、迁移或权限变化；英文标签 P2 不扩本轮范围。
- B1b 恢复验证更新（2026-10-19）：own path 收紧后作者完整组件 31 项、原型路径探针及类型检查已通过，00 核当前十文件 hash；04 已接非作者独立 delta 验收，尚未放行。字段与兼容契约不变，所有产品文件冻结；B2 POST/GET、真实库和运行副本同步仍未授权。
- B1b 软件范围接受与 B2 调查（2026-10-19）：04 独立定向例、原型探针、完整 31 项和类型检查及十冻结 hash 由 00 核验后，候选 UI 指定软件范围接受；真实浏览器和完整 S02 未验。05 只读核查 B2 CRUD 入口/影响与最少文件，未授实现。B2 预定 POST 仅净化新入库 Skill 步骤，GET 只净化返回、不更新旧行；无数据库迁移，ordinary tools/reasoning/消息字段和稳定 ID 保存语义保持，待 00 审门禁另授权。
- B2 窄实现批准（2026-10-20，尚未交付/验收）：00 核固定 CLI 的三个入口 impact 均 UNKNOWN，并补核候选 FastAPI 装饰器、路由注册及前端实际保存/读取请求后，批准候选 CRUD 三入口与独立纯 helper、新回归文件的最小实现。POST 仅在新建/幂等更新写入前投影 `activity.steps`；消息列表 GET 和会话详情 GET 均只投影返回、不修改旧行。仅 exact 两 Skill 的工具步骤使用安全字段白名单，未知/raw args/result/content/error/label 不透传；已验证 trace 与调用 ID/stage/path/状态冲突时失败关闭，旧无 trace 不补造成功；普通工具、thinking/reasoning、消息正文/来源/ID/待保存标记保持。没有新端点、SSE 事件、DTO 字段、列或迁移，不授予任何工具权限。语法损坏的数据库 JSON 在 ORM 解码前失败的限制另记，不能伪称 projector 已解决。
- B2 指定软件范围接受（2026-10-21）：00 作者红绿及 01 独立 delta 80 项已核；精确 Skill 名但 `type` 缺失/非 `tool` 的畸形步骤直接丢弃，不伪造成成功或原样透传。正常 Skill 步骤安全投影、普通步骤不变、POST 写前及两 GET 只投影的既定契约已在候选实现。包含旧行不重写和幂等/409 验证；不是完整 Skills/S02、浏览器、真实旧库或发布验收。
- 成功响应：

```json
{
  "id": "m1a2b3c4",
  "role": "user",
  "content": "I2C NACK 怎么排查？",
  "created_at": "2026-06-19T12:00:05"
}
```

### 5.16.1 `POST /api/sessions/{session_id}/messages/append-v1`

- 状态：`implemented`
- 用途：按客户端提供的稳定消息 ID 追加一条用户或 assistant 消息，并在同一 owner 写事务中保存 Message 与对应 rolling-history 正文和索引。此接口每次只处理一条消息。
- 请求体：

```json
{
  "id": "m1a2b3c4",
  "expected_incarnation_id": "0123456789abcdef0123456789abcdef",
  "role": "user",
  "content": "I2C NACK 怎么排查？",
  "sources": [],
  "tool_calls": [],
  "activity": null
}
```

- 必填字段：`id`、`expected_incarnation_id`、`role`、`content`。`id` 必须匹配 `[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}`；`expected_incarnation_id` 必须是当前会话的活动 incarnation。
- 可选字段：`sources`、`tool_calls`（数组）和 `activity`（对象或 `null`）。`role` 仅接受 `user` / `assistant`。
- 容量：`content` 最多 1,048,576 UTF-8 字节；超过上限或不是有效 UTF-8 时返回 HTTP 422（`INVALID_REQUEST`），正文不会截断。metadata 最多 16,384 UTF-8 字节，按 canonical JSON 编码后计算，编码规则为键排序、紧凑分隔符、`ensure_ascii=false`、`allow_nan=false`。计算投影包含由 `role` 映射得到的 `event_type`（`user_text` / `assistant_text`）、规范化后的 `attachments`、`created_at` 和去掉 `content` 的 `message_event`；本接口当前不接收附件，故 `attachments` 为空数组。完整 `content` 仍参与正文存储、来源摘要和幂等校验。
- 幂等与冲突：首次写入返回 `status: "saved"`；同一 ID、内容完全相同且已有已验证 rolling locator 的重试返回 `status: "already_saved"`，不重复写消息或增加消息修订号。相同 ID 的内容冲突、跨会话 ID 冲突、缺少已验证 locator 的既有消息，或 rolling 历史尚未迁移到可追加状态时均 fail-closed；消息/历史冲突返回 HTTP 409（`MESSAGE_CONFLICT` 或 `MESSAGE_ID_IN_USE`）。对受 owner 绑定但当前用户无权访问的其他会话，返回 HTTP 403（`FORBIDDEN`）。过期 incarnation 返回 HTTP 409（`SESSION_INCARNATION_CONFLICT`）。
- 原子性与边界：单条 Message、会话计数/修订号更新及其 rolling-history 正文、locator、revision/head 写入使用同一 owner WRITE 事务；任何失败都会回滚该次事务。普通 `POST /api/sessions/{session_id}/messages` 是不同的旧 CRUD 端点，不属于本节的原子写入。append-v1 不代表既有消息的全量历史迁移，也不保证 user 与 assistant 两条消息组成的 turn pair 原子提交。
- 成功响应：标准 `{ "success": true, "data": ... }` 封套中的 `data` 包含 `id`、`status`（`saved` / `already_saved`）、当前 `message_revision` 和 ISO 8601 `created_at`。

```json
{
  "success": true,
  "data": {
    "id": "m1a2b3c4",
    "status": "saved",
    "message_revision": 1,
    "created_at": "2026-06-19T12:00:05"
  }
}
```

### 5.16.2 `GET /api/sessions/{session_id}/history-mode`

- 状态：`implemented`
- 用途：在聊天发送前确定当前会话应使用 owner-bound rolling-history V2 追加路径，还是兼容的 legacy Message CRUD 路径。
- owner-bound 会话先验证当前 principal、session incarnation 和删除 epoch，再通过公开 owner-bound rolling-history API 读取 active pointer。已确认 active 的 pointer 会复用；pointer 为 unset 且 `Message` 表为空时，后端以绑定该 owner 生命周期的稳定幂等身份创建空 V2 root，通过 expected-unset pointer 与 root bound head CAS 创建 active pointer，并独立读取确认后才返回 `owner_v1`。同一请求重试会复用该 root；损坏、冲突或无法确认的状态返回 HTTP 409（`MESSAGE_CONFLICT`），不会降级到 legacy。
- `mode: "legacy"` 除未绑定 owner 的旧共享会话外，也表示已鉴权 owner-bound 的历史会话仍有 `Message` 行但尚无 V2 active pointer。此时不创建空 root 覆盖旧历史；前端继续使用兼容 Message 接口，后端对已绑定会话执行 owner gate。已绑定 owner 的 legacy 历史额外返回 `server_history: true`：`POST /api/chat` 可使用 `history_mode: "server"` 由服务端读取完整持久历史并做有界图内压缩，保存仍走 legacy Message 接口；未绑定 owner 的 legacy 响应不带该字段，继续使用客户端历史与旧条数限制。该模式保留旧消息的读取和保存，但不等于已完成 V2 历史迁移。
- owner-bound 空会话的成功形状：

```json
{
  "success": true,
  "data": {
    "session_id": "session-123",
    "mode": "owner_v1",
    "incarnation_id": "0123456789abcdef0123456789abcdef"
  }
}
```

- legacy 成功形状保持 `{ "session_id": "session-123", "mode": "legacy" }`；owner-bound legacy 历史额外带 `"server_history": true`。未绑定会话仍使用无该字段的形状。legacy Message 的读取、添加/更新和截断路由对 owner-bound 会话继续验证当前 owner。

### 5.17 `GET /api/sessions/{session_id}/messages`

- 状态：`agreed`
- 用途：获取会话的所有消息，按创建时间正序。
- 持续滚动候选分页（2026-10-28，候选源码已改，未测试、未同步主树）：省略分页与版本参数时保持原全量响应。提供 `limit`（1–200）、`before_id`、`expected_revision` 或 `expected_incarnation_id` 时返回 `messages`、`has_more`、`next_before_id` 和非负 `message_revision`；默认每页 50 条，锚点必须属于当前会话。页面按 `(created_at, id)` 正序返回，较旧页以当前页最旧消息 ID 为游标。后续页传首屏的 `expected_revision`，不匹配时返回 HTTP 409 `SNAPSHOT_STALE`；若传 `expected_incarnation_id`，后端在同一读取快照中验证已认证 owner 与该会话当前 incarnation，失效或不匹配返回 HTTP 409 `SESSION_INCARNATION_CONFLICT`。不带 incarnation 的旧分页仍保留旧兼容路径；它不提供删除后重建同 ID 会话的身份保证。前端完整接线及统一验证尚未完成。
- 成功响应：

```json
{
  "messages": [
    {
      "id": "m1a2b3c4",
      "role": "user",
      "content": "I2C NACK 怎么排查？",
      "sources": [],
      "tool_calls": [],
      "activity": null,
      "created_at": "2026-06-19T12:00:05"
    }
  ]
}
```

### 5.17.1 持续滚动候选消息接口

以下接口已在候选工作树写入源码，尚未进行运行验证或同步主树；前端类型与调用方仍在接线中。

- `GET /api/sessions/{session_id}/history-mode`：在只读快照中分类当前会话。未绑定时返回 `{ "session_id": "...", "mode": "legacy" }`；已绑定时只有当前 owner 可获得 `{ "session_id": "...", "mode": "owner_v1", "incarnation_id": "..." }`。不存在返回 404，非 owner 返回 403。分类不认领会话，也不授权后续读写；错误不能被解释为 legacy。会话删除后同 ID 重建可能被分类为 legacy，客户端不能因此把旧生命周期的待保存内容发送到旧写接口。
- `GET /api/sessions/{session_id}/lifecycle`：只向已认证且绑定当前会话的 owner 返回 `{ "session_id": "...", "incarnation_id": "..." }`。标识仅用于识别会话生命周期，不授予访问权限。匿名、未绑定、非 owner 或已失效时返回 HTTP 409 `SESSION_INCARNATION_CONFLICT`；不会自动认领旧会话。
- `POST /api/sessions/{session_id}/messages/append-v1`：请求包含稳定消息 `id`、`expected_incarnation_id`、`role`、`content`，以及可选的 `sources`、`tool_calls`、`activity`。后端在同一写事务内验证 owner 与 incarnation；新消息返回 `status: "saved"`，同会话同 ID 且实际保存内容相同的重试返回 `status: "already_saved"`。回执还包含 `id`、`message_revision`、`created_at`。内容不同返回 HTTP 409 `MESSAGE_CONFLICT`；不会重放聊天或工具。
- `POST /api/sessions/{session_id}/messages/truncate-v1`：请求包含 `keep_through_id`、`expected_revision` 和 `expected_incarnation_id`。后端在同一写事务内验证 owner、生命周期、revision 与会话内锚点，再保留锚点及其之前的消息；返回 `kept`、`deleted` 和 `message_revision`。revision 过期返回 HTTP 409 `SNAPSHOT_STALE`，锚点不存在返回 HTTP 404 `ANCHOR_NOT_FOUND`。

这些接口的成功响应均使用现有 `{ "success": true, "data": ... }` 信封。本地待保存正文日志、旧会话迁移、分页恢复和聊天增量请求尚未完成；不能将这些候选接口当作完整滚动链路或已通过验收。

### 5.17.2 `DELETE /api/sessions/{session_id}/messages?keep_count=N`

- 状态：`agreed`
- 用途：截断会话消息，保留前 `keep_count` 条，删除其余。用于前端 retry/editAndResend 时同步删除后端旧消息，防止刷新后重复。`keep_count=0` 清空所有消息。
- 查询参数：`keep_count`（int，默认 0）
- 成功响应：

```json
{
  "kept": 2,
  "deleted": 3
}
```

### 5.18 `GET /api/settings`

- 状态：`implemented`
- 用途：获取所有设置键值对。
- 成功响应：

```json
{
  "success": true,
  "data": {
    "settings": {
      "themeMode": "dark",
      "lang": "zh",
      "longTermMemory": ""
    }
  }
}
```

- 约束：所有值均为字符串。前端自行做类型转换。
- 内部状态不属于设置返回契约：记忆清空 epoch、会话删除 generation 以及 `_contextEvent:` 事件档案 namespace 均不返回；PUT 原白名单不接受这些键。S05a 事件仅宿主内部限域读写，当前没有公共恢复字段/端点，不将隐藏键当作鉴权机制。

### 5.19 `PUT /api/settings`

检索设置补充（2026-10-04；当前 F1 候选已通过自动化前后端软件回归，真实服务与浏览器联调仍待验证）：

- `topK` 必须是 1–20 的整数；`relevanceThreshold` 必须是 0–100 的有限数，`0` 表示不按相关度阈值过滤。前端设置页使用 0–100 单位；聊天请求使用 `relevance_threshold` 的 0–1 单位。
- 任一值非法或超范围时，返回 HTTP `422`、错误码 `INVALID_RETRIEVAL_SETTING`；批量请求在写入前统一校验，失败时整批设置保持原值。
- 设置接口约束已在 `backend/app/api/crud.py` 中实现；后端全套自动化回归为 606 项通过，前端全套为 99 项通过、lint 无错误且构建通过。当前没有据此标记真实服务/浏览器端到端 `verified`。

手动记忆补充（2026-10-03，后端 `implemented`，界面联调待验证）：

- 沿用 `longTermMemory` 键；值必须是字符串，最多 4,000 个字符。`""` 表示清空，省略该键表示不修改。
- 错误类型（含 `null`）或超限返回 `422`，`detail.error.code` 为 `INVALID_LONG_TERM_MEMORY`；整批更新不改变此前保存值。
- 成功仍返回 `success: true, data: {updated: true}`；`GET /api/settings` 的 `data.settings.longTermMemory` 是服务器保存值，包含空字符串。
- 清空后的续跑规则（2026-10-21，候选指定软件范围已接受）：成功显式保存空串时，在同一设置事务中更新私有清空 epoch；事务失败不发布失效。该内部键不由 GET 返回，也不允许通过 PUT 白名单写入，无新表/列。携带清空前非空长期记忆的暂停请求在后续读取或重新保存续跑快照时失效，现有续跑接口返回 `409 / HITL_REQUEST_SNAPSHOT_UNAVAILABLE`，必须重新发送本轮请求；清空后再保存新值不恢复旧快照。非空值的普通编辑保留已暂停请求的原快照；无记忆请求不受影响。epoch 无法读取/损坏时对携带记忆的续跑失败关闭。此规则不能撤回已发送给模型的内容，也不声称中止已开始执行的续跑或删除历史消息。
- 前端明确保存并等待成功，保存失败保留编辑草稿，不以本地缓存更新冒称服务器保存成功。
- 记忆会随聊天发送给配置的模型；本接口不进行自动提取或聊天历史搜索，也不将记忆作为执行授权。

- 状态：`implemented`
- 用途：批量保存设置键值对。
- 请求体：

```json
{
  "themeMode": "dark",
  "lang": "zh",
  "longTermMemory": ""
}
```

- 成功响应：`{ "success": true, "data": { "updated": true } }`
### 5.20 `POST /api/diagnose`

- 状态：`implemented`
- 用途：对嵌入式代码做静态扫描，检查 GPIO 安全、引脚冲突、内存与 Flash 兼容性。
- 前端入口：`apiPost('diagnose', ...)`
- 请求体：

```json
{
  "code": "void setup() { pinMode(LED, OUTPUT); digitalWrite(LED, HIGH); }",
  "env": "esp32-s3",
  "chip": "esp32-s3"
}
```

- 字段说明：
  - `code`：待诊断的代码字符串，必填。
  - `env`：编译环境，默认 `esp32-s3`。
  - `chip`：目标芯片，默认 `esp32-s3`，影响 Strapping 引脚集合。

- 成功响应：

```json
{
  "success": true,
  "data": {
    "results": [
      { "name": "GPIO 安全检查", "status": "WARN", "detail": "GPIO0 为 Strapping 引脚，建议避免使用" },
      { "name": "编译预检", "status": "PASS", "detail": "语法预检通过" },
      { "name": "引脚冲突检测", "status": "PASS", "detail": "未发现同一引脚被同时配置为输入和输出" },
      { "name": "内存估算", "status": "PASS", "detail": "估算 SRAM 使用约 42%" },
      { "name": "Flash 兼容性", "status": "PASS", "detail": "识别到常见库: delay, Serial" }
    ]
  }
}
```

- 错误响应：

| 场景 | code |
| --- | --- |
| 后端诊断异常 | `DIAGNOSE_FAILED` |

```json
{
  "success": false,
  "error": {
    "code": "DIAGNOSE_FAILED",
    "message": "诊断失败: ...",
    "details": "..."
  }
}
```

- 约束：`status` 枚举值只允许 `PASS` / `WARN` / `FAIL`。
- 更新时间：2026-06-20

### 5.21 `POST /api/sandbox/execute` — DEPRECATED

> **状态：`deprecated`（2026-06-30）**
>
> Docker 沙箱代码已删除（调研 Aider/Cline/Continue/Cursor/OpenHands 5 个成熟 AI 编程项目，4 个不用 Docker；硬件 RAG Agent 是贴身副驾场景，不需要 Docker 隔离）。
>
> **替代方案**：Agent 的 `run_command` 工具会在本机执行命令，并使用权限门控和审计记录；以当前工具实现和本文契约为准。
>
> 以下内容仅作历史记录保留，接口已不可用。

### 5.22 `GET /api/sandbox/status` — DEPRECATED

> **状态：`deprecated`（2026-06-30）**
>
> 同 §5.21，Docker 沙箱已移除。前端不再调用此接口。
>
> 以下内容仅作历史记录保留，接口已不可用。

### 5.23 `POST /api/sandbox/audit` — DEPRECATED

> **状态：`deprecated`（2026-06-30）**
>
> 原 Docker 沙箱审计接口已废弃。Agent 时代改用 `audit_logger.py` 写入 SQLite `tool_audit` 表，前端通过 `AuditLogPanel` 组件查询，见 spec 第 9.4 节。
>
> 以下内容仅作历史记录保留，接口已不可用。

```json
{
  "action": "create_file",
  "level": "high",
  "target": "data/sensitive.txt",
  "detail": "创建文件，包含 API Key",
  "session_id": "s1a2b3c4"
}
```

- 字段说明：
  | 字段 | 类型 | 必填 | 说明 |
  | --- | --- | --- | --- |
  | `action` | string | 是 | 操作类型 |
  | `level` | string | 是 | 风险等级 |
  | `target` | string | 否 | 操作目标 |
  | `detail` | string | 否 | 操作详情 |
  | `session_id` | string | 否 | 所属对话 ID |

- 枚举说明：
  | level | 说明 |
  | --- | --- |
  | `low` | 读文件、列目录、状态检查 |
  | `medium` | 启动服务、安装依赖、格式化 |
  | `high` | 删除、重置、覆盖、网络下载、系统级命令 |

  | action | 说明 |
  | --- | --- |
  | `read_file` | 读文件 |
  | `write_file` | 写文件 |
  | `delete_file` | 删除文件 |
  | `execute_command` | 执行命令 |
  | `network_request` | 网络请求 |
  | `docker_operation` | Docker 操作 |
  | `system_config` | 系统配置变更 |

- 成功响应：`{ "success": true, "data": { "id": "audit-001" } }`
- 错误响应：

| 场景 | code |
| --- | --- |
| action 为空 | `INVALID_ACTION` |
| level 不支持 | `INVALID_LEVEL` |
| 审计存储写入失败 | `AUDIT_WRITE_FAILED` |

- 后端约束：审计日志至少保留最近 30 天，支持按 session_id 查询。
- 更新时间：2026-06-21

### 5.24 `GET /api/agent-sandbox/policy`

- 状态：`implemented`
- 用途：查询当前 Agent 工具调用权限模式（持久化在 settings 表，key = `agent_sandbox.policy`）。
- 前端入口：`apiGet('agent-sandbox/policy')`
- 请求 Header：无
- 成功响应：

```json
{ "success": true, "data": { "permission_mode": "default" } }
```

- 字段说明：
  - `permission_mode` 枚举 `"bypassPermissions"` / `"default"` / `"acceptEdits"`，详见 §5.1。
- 后端约束：settings 表无记录时返回默认值 `"default"`。
- 更新时间：2026-07-01

### 5.25 `POST /api/agent-sandbox/policy`

- 状态：`implemented`
- 用途：切换 Agent 权限模式（写 settings 表）。
- 前端入口：`apiPost('agent-sandbox/policy', { permission_mode })`
- 请求体：

```json
{ "permission_mode": "acceptEdits" }
```

- 成功响应：`{ "success": true, "data": { "permission_mode": "acceptEdits" } }`
- 错误响应：

| 场景 | code |
| --- | --- |
| `permission_mode` 缺失或非法 | `INVALID_POLICY` |

- 后端约束：`permission_mode` 必须在 3 个枚举值内；非法值返回 400。
- 更新时间：2026-07-01

### 5.26 `GET /api/agent-sandbox/audit`

- 状态：`implemented`
- 用途：查询 Agent 工具调用审计日志（SQLite `tool_audit` 表）。
- 前端入口：`apiGet('agent-sandbox/audit?session_id=xxx&limit=100')`
- 查询参数：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `session_id` | string | 否 | 按会话过滤 |
| `tool_name` | string | 否 | 按工具名过滤 |
| `decision` | string | 否 | 按决策过滤（`allow`/`ask`/`deny`/`executed`） |
| `risk_level` | string | 否 | 按风险等级过滤（`low`/`medium`/`high`） |
| `limit` | int | 否 | 返回条数上限，默认 100，最大 500 |

- 成功响应：

```json
{
  "success": true,
  "data": {
    "logs": [
      {
        "id": 1, "timestamp": "2026-07-01T10:00:00Z", "session_id": "sess-001",
        "tool_name": "run_command", "args_summary": "{\"command\":\"python --version\"}",
        "decision": "executed", "decision_source": "post_execution",
        "risk_level": "low", "exit_code": 0, "duration": 120, "error": null
      }
    ],
    "count": 1
  }
}
```

- 字段说明：
  - `args_summary` 为工具参数 JSON 字符串（截断到 500 字符）。
  - `decision` 枚举：`allow`（门控放行）/`ask`（门控询问）/`deny`（门控拒绝）/`executed`（执行后记录）。
  - `decision_source` 枚举：`bypass` / `risk_level` / `user` / `deny_pattern` / `post_execution`。
  - `duration` 字段名与聊天 SSE 一致，单位毫秒。
  - `error` 仅在执行失败或超时时非空。
- 后端约束：日志保留 30 天，启动时自动清理过期记录（`cleanup_old_logs()`）。
- 更新时间：2026-07-01

### 5.27 `POST /api/agent-sandbox/resume`

- 状态：`implemented`
- 用途：HITL（Human-In-The-Loop）场景下，用户在 `ConfirmDialog` 做出决策后恢复 Agent 执行。
- 前端入口：`apiSSE('agent-sandbox/resume', { payload, decision, call_id })`
- 请求体：

```json
{
  "payload": { "...": "原 ChatRequest 体（完整保留）" },
  "decision": "allow",
  "call_id": "具体待确认调用的 ID"
}
```

- 字段说明：
  - `payload` 为触发 `tool_confirm_required` 的原始 `ChatRequest`（前端缓存 `_lastAgentPayload`）。
  - `decision` 枚举 `"allow"`（允许本次）/`"deny"`（拒绝该工具调用）/`"stop"`（拒绝并停止 Agent）。不存在 MCP 永久授权。
  - `call_id` 可选严格字符串（1–256 字符），前端从当前确认卡读取。含 MCP 的请求对 allow/deny 必须提供当前 ID；缺失、重复或过期确认返回 409，不能批准下一张确认卡。旧内置工具客户端可省略。
- 请求校验：`decision` 必填且只接受上述三个精确值。非法字符串、空值或错误类型返回 HTTP `422`（默认 `detail` 格式），不恢复 Agent，也不执行待确认工具。
- SSE 事件类型：同 §5.1（`text` / `tool_call` / `tool_result` / `done` / `error`），复用 Agent 流式协议。
- 后端约束：
  - 请求快照在本机内存保存 30 分钟、最多 128 个；续跑使用原记忆、Skills、权限和工具连接，丢失后返回 `HITL_REQUEST_SNAPSHOT_UNAVAILABLE` 409，不接受客户端替代原快照。
  - 会话成功删除会使此前暂停请求失效，即便旧在途快照晚存回或同一 ID 重新创建也不能续跑；失败关闭沿用同一 409。删除 generation 由宿主捕获和核对，不是客户端授权字段。候选实现与验证边界见 §5.15。
  - S05a 请求绑定（2026-10-21，指定软件范围已独立接受）：绑定了 archive scope 的暂停请求还核对当前认证用户 principal，不匹配时在构建 Agent 前沿用 `409 / HITL_REQUEST_SNAPSHOT_UNAVAILABLE`，不接受客户端传来的 archive 身份。旧无 archive scope 请求保原有续跑语义，但无权自动归档/替换早期块。
  - MCP 批次逐个确认，全部决定完成后才恢复 ToolNode。授权绑定调用 ID、工具、参数及确切连接实例，只能消费一次；拒绝的调用在 Router 返回失败，不触及远端。停止撤销未消费授权，但不能撤回已执行的远端动作。
  - 普通内置工具保留原确认路径；MCP 不写入全局工具注册表，不由客户端的 decision_source 或 bypass 标记授权。
  - 等待下一次确认时，fallback `done` 为 `success=true, completed=false, awaiting_confirmation=true`；前端保留当前确认及续跑 payload。普通成功 fallback 仍兼容 `success=true, usage=null` 的旧形状。
  - 续跑异常发送 `error` 后接一次 `done(success=false, completed=false)`，前端保留错误及部分正文，不在流关闭后覆盖成完成。
  - `stop` → 保留 `done(success=false, reason="user stopped")` 结束流。只有当前用户决定为 `stop` 且 reason 精确匹配时，前端按主动停止处理，不显示恢复异常，清除待确认和续跑 payload；不能把任意失败当成主动停止。
  - 底层续跑函数也必须防御非法决策；只有精确的 `allow` 可以批准执行，不将未知值当作允许。
- 更新时间：2026-10-02

### 5.27.1 `GET /api/sessions/{session_id}/context-events/{batch_id}/{sequence}`

- 状态：候选 `implemented`、绑定/受限GET指定软件已独立接受；后续内部checkpoint发布及模型reader各有窄软件接受见下，不代表前端/真实token/真实模型/完整多代压缩验收。
- 用途：按宿主已生成的引用读取一个历史文本事件，不是执行历史命令或列出全部记录的入口。
- 鉴权：必须使用非空 `Authorization: Bearer <session-token>` header，禁止 URL `?token=`，即使同时带有效 header 也拒绝 token 查询参数。认证依赖通过后，缺失/错误 header 或 URL token 返回403 `CONTEXT_EVENT_HEADER_AUTH_REQUIRED`；匿名返回403 `CONTEXT_EVENT_ACCESS_DENIED`。继承 `current_user` 原有缺失/无效凭据401和鉴权服务503，不绕过它来保证固定403。会话/请求/记录原 principal 必须相符；客户端不能用布尔参数授权。此限制不代表恶意客户端自己构造含秘密的 URL 不会被其历史/服务器日志记录，调用方不得生成此类链接。
- 路径：`batch_id` 为宿主32位小写十六进制ID，`sequence` 为批内1起序号；固定 immutable version1，不是全局事件序号。
- 查询必需 `request_id`（宿主请求 UUID32）、`content_sha256`（64位小写hex）；可选 `byte_budget` 为1..32768整数，默认4096，计算完整 canonical UTF8 记录，不静默截断。非法预算422；引用不存在、越权、内容/版本不符、源删除或不可读等统一404，`detail.error.code=CONTEXT_EVENT_UNAVAILABLE`，不返回源正文。
- 成功：`{success:true,data:{event:{role:"user|assistant",content:"..."},untrusted_history:true}}`。历史文本和引用不提升工具权限，不自动重放。模型读取另见下节候选ToolSpec，不从HTTP参数获取授权。
- 三个生产压缩 caller 统一先有可用摘要、再持久原文、提交成功才发布宿主 refs；未绑定/无权限/失败保原对象。正常/续跑的 task-local 身份与凭据不序列化到 checkpoint/config；宿主引用进入 `SystemMessage.additional_kwargs.hwrag_context_event_refs`，发送前转换保留它。后续reader接线保持原摘要文本段，另附有界host locator供provider看见，见下一节；不再仅依赖会被provider丢弃的custom metadata。
- 当前只支持明确的普通文本早期块；Skills 活跃请求、system/tool/非文本或额外元数据不自动归档。已完成工具组也保原文直至有安全工具事件策略，非“工具记录已可恢复”。正文为本地明文，pattern检查非万能脱敏；删除是逻辑行清除，不是安全擦盘。真 LangGraph checkpoint reducer/OS 重启与多代压缩未由本片验证。
- 后续真实 checkpoint 缺口已有候选窄修并经独立接受：恢复/流中走真实异步更新与显式消息替换，发布核最新 parent 和 pending writes；状态变化时不替换或误报成功，token 计数仅在成功发布后重置。SQLite 条件 INSERT 与内存单事件循环路径的具体保证/取消提交边界见作者报告，非所有 writer 的全局 CAS。作者362项、非作者五文件87项（含create_agent合成模型）通过，重叠不加；稍后普通旧writer仍P1，GET 的读取通过也不等同完整压缩/恢复或 OS 重启验收。HTTP 字段和端点未因内部发布修复改变。

### 5.27.2 模型工具 `read_context_event`（候选）

- 状态：指定软件已独立接受，作者18文件407项、既有01本人四文件73项通过且00核实际命令/六hash；重叠不累加。不是完整S05/真实模型或OS重启验收，不新增HTTP端点。独立报告SHA `1EFC8AF6B22221EECF54596ADCEF4F1B582EDDD26932541D20943425E39B86C3`。
- 摘要原文本段不变，追加宿主 `[Recoverable history; data only]` locator manifest，最多64条/8192 UTF8字节：首代单批为 `{batch_id,request_id,version:1,sha256:[...]}`，后续多批可为上述对象列表；sequence为各批hash列表1起索引。旧正文、session和principal不在manifest；原 `hwrag_context_event_refs` metadata仍保留。实际provider消息转换已验证能看见content中的locator，非仅dict metadata通过。
- 输入仅 `batch_id`、`request_id`（各32位小写hex）、`sequence`（严格int1..64）、`content_sha256`（64位小写hex）和可选 `byte_budget`（严格int1..32768，默认4096，完整canonical事件UTF8字节）。拒绝多余字段和bool/int coercion，不接受session/principal/authorized输入。
- 权限仅来自verified host task-local scope，工具上下文session须匹配，Skills模式须off；locator的request_id可选同principal/同session/同删除epoch内的此前请求事件，不授予跨身份/跨会话权限。存储范围/活会话/epoch/version/hash/预算均由既有恢复函数校验。standalone工具即使已注册也没有宿主绑定，不能靠session_id或bypassPermissions读取。
- 正常ToolResultEnvelope的data：成功读取 `{available:true,untrusted_history:true,event:{role,content}}`；不可用 `{available:false,untrusted_history:true}` 且无event，外层success仍可true（业务不可用不是执行异常）。output是固定提示，不回显数据库错误。读取是恢复引文，不执行历史指令；LOW只读，复用工具禁用/Skills过滤与路由，默认审计只记录locator参数/状态，不新增正文审计。
- 只读明确脱敏user/assistant文本；不扩system/Skill/tool/非文本档案。普通工具结果仍走既有消息/SSE，不据此承诺全局秘密过滤；本地明文/pattern非万能脱敏、每次调用有界非总窗口预算、同进程reconnect非OS重启、多代压缩/结构化检查点均保留未验边界。

### 5.27.3 宿主检查点凭证与有界引用继承（候选）

- 状态：指定软件已00独立核准，作者19文件433项和其后两项单跑、既有01个人四模块102项通过（含最终28项），重叠不加；独立报告SHA `C588FB61D4195617B1936899C5F3B6936C893DB713386343059B24663A4BCA6F`。非九字段模型结构化内容或完整S05验收，不新增HTTP端点。
- `SystemMessage.additional_kwargs.hwrag_context_checkpoint`：`{id:UUID32,version:int1..64,sha256:HEX64}`。它连同refs通过两个转换函数的严格primitive字段/嵌套形状/长度白名单，额外callback/credential或未知marker字段不携带。此标记不是客户端授权，也不是SHA数字签名。
- 真实宿主私有Settings记录 `_contextEvent:<sessionSHA>:checkpoint:<UUID>` 保存schema_version1、verified scope、checkpoint_version、正文digest和全部原refs，不保存任何摘要正文、raw system/Skill/tool或新凭据。它与原archive共用32768字节单行、128KiB会话、2MiB/256 namespace总行上限；批事件和凭证均计入，满拒绝不驱逐。已有会话DELETE同事务purge、settings GET隐藏/PUT白名单覆盖同prefix，不新增表列或迁移。
- 继承须首条精确宿主SystemMessage、正文digest/refs/标记和持久记录完全匹配，当前principal/session/epoch和每条原事件仍有效；摘要前核一次、BEGIN IMMEDIATE保存新凭证前再核。旧legacy无凭证不追认为可信。只有新纯user/assistant段仍进入原事件archive，原system/Skills/tool/nontext策略未放宽。
- 每代保留全部旧refs，最多64；派生摘要上限8192 UTF8字节，manifest另限8192，超限/源失效/无权限/提交失败保同一原对象。凭证commit后才交原真实graph发布器；发布器仍核最新parent/pending，新tail不被覆盖。发布拒绝前已提交的batch/seal可计quota，不新增清理；局部限制不等全provider窗口预算/全局writer CAS。
- 这里只提供host schema与引用来源链，模型摘要仍不可信、不证明“已完成/已批准”，不授权/重放命令。真实恶意历史模型响应、九字段质量、三个合成长会话/真实模型/OS重启等验收另列。

### 5.27.4 结构化摘要内容与宿主来源绑定（候选）

- 状态：00 指定软件独立接受，作者20模块485项与既有01本人四模块112项（新模块最终50项）通过，重叠不加；报告SHA `F30851F2F886F4F02F998EE233ABF9EAAD93448FA7052992F75036577309DB1B`。不称完整 S05/真实 provider 验收；工具历史、语义质量、全窗口预算仍未完成。不新增 HTTP 字段或端点，不改变 §5.27.1 GET 的 header 身份验证、权限与预算契约。
- 模型输出必须恰为九字段 JSON：`goal`、`constraints`、`completed`、`current_state`、`decisions`、`failed_attempts`、`open_questions`、`artifacts_and_evidence`、`next_action`。每项为最多8条 `{text,source}` 数组；text 是不超过1024字符的来源原文摘录，source 仅本次宿主分配的 `s1..s64`。goal/current_state/artifacts_and_evidence/next_action 必须非空；当前纯 user/assistant 来源无法证明 terminal 工具执行，completed 必须为空。九个空字段、额外/重复字段、虚构摘录/槽位、模型自给持久 ID、坏 JSON、free text 均拒绝保原，不自动修补。
- 校验后宿主将 source 替换为已持久化并按当前 scope/生命周期验证的 `{batch_id,request_id,sequence,version,content_sha256}`；模型不提供身份/权限字段。原事件原文/散列再核及原 seal 事务 parent/epoch/源引用校验仍适用；schema/散列不是授权或数字签名。
- 后继输入只由前代已验证的选中 quotes 加新完整纯文本事件构成，不每代向模型重载全部旧 raw。保留全部旧 refs 与可恢复 manifest；有 seal 的旧 narrative 也不隐性追认或迁移。最多64输入槽位/保留refs、模型与绑定正文各8192 UTF-8、manifest另8192，原会话/全局配额不变，超限保原不驱逐。原事件先提交后 seal/graph 拒绝可留下计配额的未引用批，不新增清理。
- 原 system/tool/Skills/nontext/额外元数据仍不自动归档，现会在摘要调用前拒绝。quote grounding 不证明真伪、字段语义质量或“已批准”，历史仅数据不授权执行。局部上限不等完整 system/tools/output 总窗口预算；普通聊天另500字问题、工具安全事件档案、长回放/真实模型仍后续。

### 5.27.5 普通聊天客户端输入预算（候选）

- 状态：00作者实现冻结，25模块567项（新57项）通过，既有01本人短独立复核中。无新HTTP字段/端点或SSE枚举，沿现有LLM错误流程返回上下文预算/摘要不可用信息；不以省略占位符继续回答，不写回原历史或长期记忆。
- 普通chat/stream按实际request model而非client默认型号选择预算，仅精确本地登记名称或精确provider前缀后缀匹配；未知/模糊窗口或无效output配置拒绝。最终wire `max_tokens`按该型号cap；既有OpenCode stream省略该字段时预留登记最大output，不假定请求的小cap已被provider执行。
- system+完整消息JSON的UTF-8字节+256 framing+output reserve作保守本地上限。它可能拒绝精确tokenizer本可放下的输入，不是provider能力确认或通用精确token计数。opaque图像/未知内容成本不能仅凭URL或固定85估算证明，因此当前普通客户端明确拒绝；纯text blocks保持原格式，不隐性去图或丢文本。不称完整多模态预算。
- 超限时仅把完整的较早user/assistant prefix送同一runtime client/model做摘要，包含620字后原文而非逐条截到500字；摘要请求连固定提示/封装/输出reserve最多32KiB。新派生摘要最多8192 UTF-8字节，替换后重新核总预算。旧消息角色/重复条目/最近suffix顺序保留；prefix过大/不支持/summary失败或取消/空/过大/final仍超限则终止请求，原输入历史不改。
- 普通摘要仍为untrusted narrative，不是§5.27.4的宿主持久seal、工具终态证据或新授权。没有合并普通/Agent archive能力；Agent tool schema/system/output全窗口、真实provider和OS重启另验。

### 5.28 `GET /api/tools`

- 状态：`implemented`
- 用途：列出当前已注册的 Agent 工具，供前端展示可用工具清单或调试。
- 前端入口：`apiGet('tools')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "tools": [
      { "name": "search_docs", "description": "检索知识库" },
      { "name": "audit_pins", "description": "引脚冲突审计" }
    ]
  }
}
```

- 字段说明：`name` 为工具注册名（与 `POST /api/tool` 的 `tool` 字段一致）；`description` 取自 handler 的 `__doc__`，可能为空字符串。
- 后端约束：依赖 `current_user`；工具来源为 `src/agent/tool_router.py` 的 `_REGISTRY`。
- 更新时间：2026-07-01

### 5.29 `GET /api/token-usage/stats`

- 状态：`implemented`
- 用途：返回近 N 天的 Token 用量统计，用于前端用量面板；支持按会话过滤以展示当前对话的真实用量。
- 前端入口：`apiGet('token-usage/stats?days=30&session_id=<sessionId>')`
- 查询参数：
  - `days`（int，默认 30，范围 1-365，超出自动 clamp）
  - `session_id`（string，可选）— 若提供，则只统计该会话的 `token_usage` 记录
- 成功响应：

```json
{
  "success": true,
  "data": {
    "daily": [
      { "date": "2026-07-01", "input": 1200, "output": 800, "total": 2000 }
    ],
    "by_model": [
      { "model": "gpt-4o", "input": 1200, "output": 800, "total": 2000, "calls": 5 }
    ],
    "summary": {
      "total_input": 1200,
      "total_output": 800,
      "total_tokens": 2000,
      "days": 30
    }
  }
}
```

- 字段说明：`daily` 按天聚合（日期为 UTC 日期字符串）；`by_model` 按模型分组并按总 token 倒序；`summary` 汇总近 N 天总量。
- 后端约束：数据来自 SQLite `token_usage` 表（`chat_helpers._record_token_usage` 写入）。无 `current_user` 依赖（统计接口）。
- 更新时间：2026-07-01

### 5.30 `POST /api/wiring/extract`（已移除）

- 状态：`removed`（2026-11-08）
- 接线图工作台与代码提取逻辑已下线，不再提供前端入口。

### 5.31 `POST /api/auth/store-key`

- 状态：`implemented`
- 用途：加密存储 Provider 的 API Key（Fernet 对称加密），返回 `session_token` 供后续 Bearer 鉴权使用。
- 前端入口：`apiPost('auth/store-key', { provider, api_key, base_url })`
- 请求体：

```json
{
  "provider": "openai",
  "api_key": "sk-...",
  "base_url": "https://api.openai.com/v1"
}
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "session_token": "64位hex",
    "provider": "openai"
  }
}
```

- 错误响应：`api_key` 为空时返回 400（`API Key 不能为空`）。
- 后端约束：加密密钥存于 `backend/app/db/.enc_key`（权限 0o600）；`session_token` 有效期 30 天，存于 `keys_store.json`。
- 更新时间：2026-07-01

#### `GET /api/auth/keys`

- 状态：`implemented`
- 用途：列出已存储的 Provider（不含明文 Key）。
- 前端入口：`apiGet('auth/keys')`
- 请求 Header：`Authorization: Bearer <session_token>`（已配置 providers 时必填）。
- 成功响应：

```json
{
  "success": true,
  "data": {
    "providers": [
      { "provider": "openai", "base_url": "https://api.openai.com/v1", "updated_at": "2026-07-01T10:00:00", "has_key": true }
    ]
  }
}
```

- 错误响应：未带/无效 token 返回 401（`AUTH_REQUIRED` / `AUTH_INVALID`）。首次配置（无 providers）时跳过鉴权。
- 更新时间：2026-07-01

#### `DELETE /api/auth/keys/{provider}`

- 状态：`implemented`
- 用途：删除指定 Provider 的 API Key。
- 前端入口：`apiDelete('auth/keys/{provider}')`
- 请求 Header：`Authorization: Bearer <session_token>`（同上）。
- 成功响应：`{ "success": true }`（provider 不存在时也返回成功，幂等）。
- 更新时间：2026-07-01

### 5.32 `/api/mcp/servers/*` — MCP Server 管理

- 状态：`implemented`
- 用途：管理 MCP (Model Context Protocol) Server 配置与生命周期，让 Agent 可调用外部 MCP 工具。
- 前端入口：`apiPost('mcp/servers', ...)` / `apiGet('mcp/servers')` 等。
- 依赖：列表/工具读取为 `current_user_optional`，变更为 `current_user`，沿用单机认证语义。
- 端点清单：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/mcp/servers` | 添加 MCP Server 配置（id/name/command/args/env） |
| `GET` | `/api/mcp/servers` | 列出所有 MCP Server 及运行状态 |
| `POST` | `/api/mcp/servers/{server_id}/start` | 启动指定 Server，失败返回 502 |
| `POST` | `/api/mcp/servers/{server_id}/stop` | 停止指定 Server |
| `GET` | `/api/mcp/servers/{server_id}/tools` | 列出该 Server 提供的工具（name/description/input_schema） |
| `DELETE` | `/api/mcp/servers/{server_id}` | 删除配置（先 stop 再 remove） |

- `POST /servers` 请求体：

```json
{
  "id": "fixture",
  "name": "Safe test fixture",
  "command": "C:/path/to/python.exe",
  "args": ["C:/path/to/agent/backend/tests/fixtures/mcp_stdio_server.py"],
  "env": {}
}
```

- `GET /servers/{id}/tools` 成功响应：

```json
{
  "success": true,
  "data": {
    "tools": [
      { "name": "read_file", "description": "读取文件", "input_schema": { "type": "object" } }
    ]
  }
}
```

- 成功体为 `{success:true,data:...}`；业务错误为 `{success:false,data:{error:{code,message}}}`。重复配置 409；非法配置 422；未知服务 404；启动失败 502。不返回原始进程错误、env 或密钥。
- 配置只驻留后端内存，不写入设置/localStorage；后端重启需重加。command 必须是现有程序的绝对路径，Windows 接受 `.exe/.com`；args 为字符串数组，env 为字符串映射，不拆命令、不自动安装。启动本机进程不受系统沙箱隔离。
- 已知服务未启动时 tools 返回空数组。连接/初始化/分页发现合计 60 秒限时，前端启动请求 70 秒；单次 RPC 30 秒，通知最多 16 条、列表最多 32 页/128 工具、消息最多 64 KiB。受限 JSON Schema 不支持的关键字、远端引用或非法参数明确失败，不降级为宽松校验。
- 已连接工具加入 Agent 的请求快照，HIGH、每次确认且不自动重试；Skills 模式不再屏蔽 MCP，仍要求精确的请求级授权。停止/重启不会把旧快照转到新进程。长名称或歧义名称映射为不超过 64 字符的带摘要名称，保留 server/tool 来源字段。
- 子进程只继承必要环境变量及用户显式填写的 env；服务错误、断连和超时是真正失败，外部调用审计省略参数原文。结果仍会发给当前模型服务，用户应自行判断数据敏感性。
- 更新时间：2026-10-03

### 5.33 `/api/feedback/*` — 消息反馈

- 状态：`implemented`
- 用途：用户对 assistant 消息点赞/点踩，用于后续评估与微调数据收集。
- 端点清单：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/feedback` | 提交一条反馈 |
| `GET` | `/api/feedback/{session_id}` | 查询某会话下的所有反馈 |

- `POST /api/feedback` 请求体：

```json
{
  "message_id": "m1a2b3c4",
  "session_id": "s3ed52d63",
  "rating": 1
}
```

- 字段说明：`rating` 取 `1`（👍）或 `-1`（👎）。
- 成功响应：`{ "success": true }`
- `GET /api/feedback/{session_id}` 成功响应：

```json
{
  "success": true,
  "data": [
    { "message_id": "m1a2b3c4", "rating": 1, "created_at": "2026-07-01T10:00:00" }
  ]
}
```

- 后端约束：数据存 SQLite `feedback` 表，无 `current_user` 依赖。
- 更新时间：2026-07-01

### 5.34 `POST /api/search`

- 状态：`implemented`
- 用途：全文搜索历史消息内容，返回命中片段（content 截断到 200 字符）。
- 前端入口：`apiPost('search', { query, limit })`
- 请求体：

```json
{ "query": "I2C NACK", "limit": 20 }
```

- 成功响应：

```json
{
  "success": true,
  "data": {
    "results": [
      {
        "message_id": "m1a2b3c4",
        "session_id": "s3ed52d63",
        "role": "user",
        "content": "I2C NACK 怎么排查？",
        "seq": 1,
        "created_at": "2026-11-04T02:00:00Z",
        "session_title": "STM32 I2C 调试"
      }
    ],
    "total": 1
  }
}
```

- 后端约束：只使用 `messages_fts`，按 `messages_fts.rowid = messages.rowid` 关联并按 `rank` 排序；FTS5 不可用或返回空结果时 fallback 到 `LIKE`。Agent 的 `search_history` 工具复用同一查询。无 `current_user` 依赖。
- 更新时间：2026-11-04

### 5.35 KB 补充端点（rename / config / chunks / export / import）

以下 6 个端点补充 §5.3 的 Collection 管理与文档检索能力，状态均为 `implemented`。

#### `PATCH /api/kb/collections/{kb_id}/rename`

- 用途：重命名知识库。
- 前端入口：`apiPatch('kb/collections/{id}/rename', { name })`
- 请求体：`{ "name": "新名称" }`（空字符串返回 `INVALID_REQUEST`）
- 成功响应：`{ "success": true, "data": { "kb_id": "...", "old_name": "...", "new_name": "..." } }`
- 错误响应：KB 不存在返回 `KB_NOT_FOUND`。
- 更新时间：2026-07-01

#### `PATCH /api/kb/collections/{kb_id}/config`

- 用途：更新知识库配置（embedding 模型/base_url/api_key、agent chunker、chunk_method、small_chunk_size、context_window、description）。更新后 Store 缓存失效，下次检索重新初始化。
- 前端入口：`apiPatch('kb/collections/{id}/config', body)`
- 请求体（所有字段可选，`api_key` 字段 `null`=保留 / `""`=清除 / `"xxx"`=设置）：

```json
{
  "embedding_model": "text-embedding-3-large",
  "embedding_base_url": "https://api.openai.com/v1",
  "embedding_api_key": "sk-new",
  "agent_chunker_model": "gpt-4o",
  "chunk_method": "agent",
  "small_chunk_size": 1200,
  "context_window": 200000,
  "description": "更新后的描述"
}
```

- 成功响应：`{ "success": true, "data": { "kb_id": "...", "message": "配置已更新，Store 缓存已失效" } }`
- 错误响应：`small_chunk_size` 非 500/800/1200/2000 返回 `INVALID_REQUEST`；KB 不存在返回 `KB_NOT_FOUND`。
- 更新时间：2026-07-01

#### `GET /api/kb/documents/{doc_id}/chunks`

- 用途：获取某文档入库后的所有 chunk（内容 + 元数据 + agent_trace 摘要），用于前端 chunk 浏览/调试。
- 前端入口：`apiGet('kb/documents/{doc_id}/chunks')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "doc_id": "uuid-xxx",
    "kb_id": "builtin-001",
    "total_chunks": 12,
    "chunks": [
      {
        "id": "chunk-id",
        "chunk_index": 0,
        "content": "...",
        "content_length": 800,
        "page_start": 1,
        "page_end": 2,
        "section_title": "Overview",
        "chunk_method": "hybrid",
        "chunk_size": 800,
        "title": "esp32_datasheet.pdf",
        "small_chunk_id": "uuid-xxx#s0",
        "big_chunk_text": "...",
        "fingerprint": "sha256...",
        "is_code_block": false,
        "has_code_block": false,
        "boundary_disputed": false,
        "section_summary": "",
        "section_keywords": [],
        "section_confidence": null,
        "agent_trace": null
      }
    ]
  }
}
```

- 错误响应：文档不存在返回 `DOC_NOT_FOUND`。
- 后端约束：`agent_trace` 字段会从字符串解析回 dict 并提取摘要字段（省略完整 rounds 详情以控制响应大小）。
- 更新时间：2026-07-01

#### `GET /api/kb/chunks/{small_chunk_id}`

- 用途：按 `small_chunk_id` 查询单个 chunk 的完整信息（含 `big_chunk_text` 上下文）。前端点击"查看完整上下文"时调用。
- 路径参数：`small_chunk_id` 格式为 `{doc_id}#s{chunk_index}`，例如 `uuid-xxx#s0`。
- 前端入口：`apiGet('kb/chunks/{small_chunk_id}')`
- 成功响应：

```json
{
  "success": true,
  "data": {
    "id": "chunk-id",
    "small_chunk_id": "uuid-xxx#s0",
    "content": "...",
    "content_length": 800,
    "big_chunk_text": "...完整上下文...",
    "section_title": "Overview",
    "chunk_index": 0,
    "chunk_size": 800,
    "chunk_method": "hybrid",
    "page_start": 1,
    "page_end": 2,
    "title": "esp32_datasheet.pdf",
    "doc_id": "uuid-xxx",
    "kb_id": "builtin-001"
  }
}
```

- 错误响应：`small_chunk_id` 缺少 `#` 返回 `INVALID_ID`；doc 不存在返回 `DOC_NOT_FOUND`；chunk 不存在返回 `CHUNK_NOT_FOUND`。
- 更新时间：2026-07-01

#### `POST /api/kb/{kb_id}/export`

- 用途：导出 KB 的 ChromaDB 数据（documents + embeddings + metadatas）为 JSON，可用于跨实例迁移。
- 前端入口：`apiPost('kb/{id}/export')`
- 成功响应：`{ "success": true, "data": { ...export_data, "embedding_model": "...", "name": "...", "chunk_method": "..." } }`
- 错误响应：KB 不存在返回 `KB_NOT_FOUND`；导出异常返回 `EXPORT_FAILED`。
- 后端约束：目标 KB 与导入端应使用相同 embedding 模型。
- 更新时间：2026-07-01

#### `POST /api/kb/{kb_id}/import`

- 用途：将 `export` 导出的 JSON 文件导入到已存在的 KB（直接写 ChromaDB，不重新 embedding）。
- 前端入口：`apiPost('kb/{id}/import', formData)`（`multipart/form-data`，字段名 `file`）
- 请求体：`multipart/form-data`，文件为 `export` 接口的 JSON 输出，大小上限 50MB。
- 成功响应：

```json
{
  "success": true,
  "data": {
    "imported_chunks": 1280,
    "source_kb": "硬件手册库",
    "source_model": "text-embedding-3-small"
  }
}
```

- 错误响应：

| 场景 | code |
| --- | --- |
| KB 不存在 | `KB_NOT_FOUND` |
| 未提供文件名 | `INVALID_REQUEST` |
| 文件超 50MB | `FILE_TOO_LARGE` |
| JSON 缺少 `data` 字段或非有效 JSON | `INVALID_FORMAT` |
| Embedding 维度不匹配 | `EMBEDDING_DIM_MISMATCH` |
| 其他导入异常 | `IMPORT_FAILED` |

- 后端约束：导入时按源 doc_id 创建/更新 `KnowledgeDoc` 记录（保证删除时能正确清理向量）；embedding 模型不一致时仅 warning 不阻断。
- 更新时间：2026-07-01

### 5.36 Explorer 文件资源管理器接口

- 状态：`implemented`（本轮认证、版本保存及回收区尚待完整前后端联调验收）
- 用途：第四栏文件资源管理器的后端支撑，包括文件夹授权、文件树浏览、带版本的文件读写、新建/重命名/移动、可恢复删除、diff 基准获取以及 SSE 实时监听。
- 前端入口：`apiPost('explorer/...', ...)` / `apiGet('explorer/...?path=')` / `apiSSE('explorer/watch?path=', ...)`
- 认证：所有 Explorer 路由（包括 `browse`、`open`、`watch`）都要求有效 Bearer session。`browse` 只用于登录后的文件夹选择，可浏览根目录授权范围外的路径以选择项目，但不读取文件内容，也不授予根目录访问。只有该 session 显式调用 `open` 才会授权项目根。授权根保存在进程内存中，并以 session token 的 SHA-256 值隔离；其他有效 session 不能访问，除非自己显式打开同一目录。后端重启后授权清空，前端必须让用户重新打开，不得由读取、搜索或监听静默授权。
- 响应：`open` 和 `read` 保留旧版直接返回业务字段的格式；其余 JSON 路由返回 §2.6 的 `success/data` 包装。`watch` 为 SSE。请求体字段使用 `snake_case`。

#### `POST /api/explorer/open`

- 用途：授权并打开文件夹，返回根节点文件树（大目录可懒加载）。
- 请求体：`{ "path": "/absolute/path" }`
- 成功响应（旧版无 `success/data` 包装）：

```json
{
  "tree": [
    {
      "name": "project",
      "path": "/absolute/path/project",
      "type": "directory",
      "children": []
    }
  ]
}
```

- 字段说明：
  - `tree` 为 `FileNode` 数组；`type` 枚举 `file` / `directory`；`children` 仅在 `directory` 时出现，递归结构。
  - `FileNode` 新增 `lazy?: boolean`：为 `true` 时表示该目录的子节点尚未加载，前端展开时应调用 `GET /api/explorer/dir?path=` 按需获取。
- 后端约束：后端记录该路径为已授权根目录，后续 Explorer 文件操作必须落在该根目录内，禁止越界访问。

#### `GET /api/explorer/browse?path=`

- 用途：文件夹选择器（Folder Picker）浏览目录，返回可进入的子目录和文件名，不返回文件内容。
- 查询参数：`path`（可选，绝对路径；为空时返回驱动器/根目录列表）
- 成功响应：

```json
{
  "success": true,
  "data": {
    "current": "E:\\Desktop\\agent",
    "parent": "E:\\Desktop",
    "directories": [
      { "name": "backend", "path": "E:\\Desktop\\agent\\backend" },
      { "name": "frontend", "path": "E:\\Desktop\\agent\\frontend" }
    ],
    "files": [
      { "name": "README.md", "path": "E:\\Desktop\\agent\\README.md" }
    ]
  }
}
```

- 安全说明：本端点要求有效 Bearer session，但不把浏览到的路径加入已授权根，也不读取文件内容；用户仍需显式调用 `open`。它允许浏览已授权根外的目录以选择新项目，部署时需限制服务的网络暴露范围。

#### `GET /api/explorer/dir?path=`

- 用途：懒加载指定目录的直接子节点，用于超大目录（如 `.platformio`、`node_modules`）的按需展开。
- 查询参数：`path`（绝对路径，必须是已授权根目录内的 directory）
- 成功响应：

```json
{
  "success": true,
  "data": {
    "children": [
      { "name": "packages", "path": "/absolute/path/.platformio/packages", "type": "directory", "lazy": true },
      { "name": "platformio.ini", "path": "/absolute/path/.platformio/platformio.ini", "type": "file" }
    ]
  }
}
```

- 字段说明：
  - `children` 为 `FileNode` 数组，结构与 `/api/explorer/open` 中的 `tree` 一致。
  - 返回的 directory 节点可再次带有 `lazy: true`，支持递归按需加载。
- 后端约束：
  - 路径必须在已授权根目录内；越界返回 403（`FORBIDDEN`）。
  - 目录无权限或不存在时当前实现返回 500（`EXPLORER_ERROR`），前端展示重试按钮。
  - 后端进程重启后会清空内存中的已授权根集合；前端不能自动调用 `open` 重授权，须由用户重新显式打开项目。

#### `GET /api/explorer/read?path=`

- 用途：读取单个文件内容；二进制文件返回标记，不返回原始字节内容。
- 查询参数：`path`（绝对路径）
- 成功响应（旧版无 `success/data` 包装）：

```json
{
  "name": "main.py",
  "path": "/absolute/path/main.py",
  "content": "print('hello')",
  "is_text": true,
  "version": "<SHA-256 hex>"
}
```

- 字段说明：可编辑文本的 `version` 是文件原始字节的 SHA-256 十六进制摘要。`is_text` 为 `false` 时可能没有 `content` 和 `version`，并可返回 `size` / `data_url` 供二进制预览；前端不能把二进制文件当文本保存。

#### `POST /api/explorer/write`

- 用途：在版本一致时原子保存 UTF-8 文本，避免覆盖外部程序修改的内容。
- 请求体：`{ "path": "/absolute/path/main.py", "content": "...", "expected_version": "<上次读取的 SHA-256 hex>" }`；新建文件必须显式传 `"expected_version": null`。
- 成功响应：`{ "success": true, "data": { "path": "/absolute/path/main.py", "version": "<新 SHA-256 hex>" } }`
- 冲突：磁盘版本已变化，或新建目标已存在时，返回 HTTP 409、`VERSION_CONFLICT`，不覆盖已有内容。缺少 `expected_version` 时由 Pydantic 返回 422。
- 后端约束：路径必须在已授权根目录内；失败时前端保留未保存的修改。

#### `POST /api/explorer/create`

- 用途：新建文件或文件夹。
- 请求体：`{ "path": "/absolute/path/new.txt", "type": "file" }` 或 `{ "path": "/absolute/path/new_dir", "type": "directory" }`
- 成功响应：`{ "success": true, "data": { "path": "/absolute/path/new.txt", "type": "file" } }`
- 字段说明：`type` 枚举 `file` / `directory`。

#### `POST /api/explorer/rename`

- 用途：重命名文件或文件夹。
- 请求体：`{ "path": "/absolute/path/old.txt", "new_name": "new.txt" }`
- 成功响应：`{ "success": true, "data": { "path": "/absolute/path/new.txt" } }`
- 字段说明：`new_name` 仅含新名称，不含目录路径。

#### `POST /api/explorer/delete`

- 用途：把文件或文件夹移入应用回收区，以便恢复。
- 请求体：`{ "path": "/absolute/path/to_delete" }`
- 成功响应：`{ "success": true, "data": { "path": "/absolute/path/to_delete", "trash_id": "<item_id>" } }`
- 回收区位于 Windows 的 `%LOCALAPPDATA%/HardwareRAGAgent/explorer-trash/<project-root-sha256>/<item-id>`（项目根经规范化后计算 SHA-256；隔离测试用 `HWRAG_APP_DATA_DIR` 指向临时目录）。不同项目根分开存储；界面提供已删除列表和恢复，不提供自动清空或永久删除接口。

#### `GET /api/explorer/trash?root_path=`

- 用途：列出已显式打开的项目根下可恢复的条目。
- 查询参数：`root_path`（已授权项目根的绝对路径）。
- 成功响应：`{ "success": true, "data": { "items": [{ "item_id": "...", "root_path": "...", "original_path": "...", "name": "main.py", "kind": "file", "deleted_at": "<ISO datetime>" }] } }`。`kind` 为 `file` 或 `directory`。

#### `POST /api/explorer/restore`

- 用途：恢复回收区条目；不覆盖现有文件或目录。
- 请求体：`{ "root_path": "/absolute/path/project", "item_id": "<item_id>", "target_path": "/absolute/path/project/main.py" }`；`target_path` 可省略，默认恢复到原路径。
- 成功响应：`{ "success": true, "data": { "path": "/absolute/path/project/main.py", "item_id": "<item_id>" } }`。
- 目标已存在时返回 HTTP 409、`RESTORE_CONFLICT`，回收区条目保留；目标越出授权根时返回 403、`FORBIDDEN`。

#### `POST /api/explorer/move`

- 用途：移动文件或文件夹到目标目录。
- 请求体：`{ "path": "/absolute/path/a.txt", "target_dir": "/absolute/path/target" }`
- 成功响应：`{ "success": true, "data": { "path": "/absolute/path/target/a.txt" } }`
- 后端约束：`target_dir` 必须在已授权根目录内。

#### `GET /api/explorer/diff?path=`

- 用途：获取文件 diff 基准内容。
- 查询参数：`path`（绝对路径）
- 成功响应：

```json
{
  "success": true,
  "data": {
    "base": "git",
    "has_changes": true,
    "base_content": "original code",
    "current_content": "modified code"
  }
}
```

- 字段说明：
  - `base`：基准来源，枚举 `git` / `null`。优先使用 Git HEAD；无 Git 时返回 `null`，前端回退到打开时的内存快照作为基准。
  - `has_changes`：有 Git 基准时为布尔值；无 Git 基准时为 `null`。
  - `base_content`：有 Git 基准时为基准内容；无 Git 基准时为 `null`。
  - `current_content`：磁盘当前内容。

#### `GET /api/explorer/watch?path=`

- 用途：SSE 实时监听已打开文件夹的文件系统变化。
- 查询参数：`path`（已授权根目录路径）
- SSE 事件类型：

| 事件类型 | 发生时机 | 事件字段 |
| --- | --- | --- |
| `change` | 文件内容被修改 | `path`, `is_directory?` |
| `create` | 新建文件/文件夹 | `path`, `is_directory?` |
| `delete` | 删除文件/文件夹 | `path`, `is_directory?` |
| `rename` | 文件/文件夹重命名或移动 | `src_path`, `dest_path`, `is_directory?` |
| `heartbeat` | 监听保活 | 无业务字段 |
| `done` | 监听结束 | — |

- 字段说明：
  - `path`：发生变化的文件/文件夹绝对路径。
  - `src_path` / `dest_path`：`rename` 事件的原路径和目标路径。
  - `is_directory`：布尔值，表示变化对象是否为文件夹。
- 后端约束：文件夹关闭或客户端断开时停止监听；监听失败时降级为轮询或返回 `error` 事件后断开。

- 更新时间：2026-09-24

### 5.37 `/api/skills/*` — 标准 Agent Skills 与 GitHub 导入

- 状态：标准包/共享目录、Agent API 与现有前端接线完成；本轮选定软件自动化验证完成。结果为 13 个指定后端测试路径 186 passed / 26 warnings、完整 Vitest 24 files / 159 passed、`tsc -b` exit 0、Vite build exit 0（chunk-size advisory）。不代表完整后端或整项目验收；真实 GitHub/模型/浏览器、真实用户库/Skills 数据、完整 HTTP/SSE/resume E2E 与第三方 Agent 实际发现/运行未验证。
- `/api/tools` 仍管理内置工具开关；本节管理独立的 Markdown 技能包，不互相冒称。
- 列表和详情沿用 `current_user_optional`，写入、编辑、删除、开关及 GitHub 操作沿用 `current_user`；凭据为空的本地开发兼容行为不因本轮变化。
- JSON 成功体沿用 `success/data`；业务失败直接使用 `{success:false,error:{code,message}}`，没有 detail 外壳。422 自动校验保持 FastAPI 格式；聊天端创建技能快照的错误仍使用 HTTPException 的 detail。

| 方法 | 路径 | 请求 / 响应 `data` |
| --- | --- | --- |
| GET | `/api/skills` | `{skills: SkillInfo[]}`；旧 `content` 字段保留 |
| POST | `/api/skills` | `{content:string}`；新建标准 `SKILL.md` 包；无 frontmatter 的旧纯 Markdown 创建入口可包装为标准包，已有 legacy 包继续可读 |
| GET | `/api/skills/{id}` | `SkillInfo`，包括说明和资源清单 |
| PATCH | `/api/skills/{id}` | `{content?:string,enabled?:boolean}`，至少一个；返回更新后的详情 |
| PATCH | `/api/skills/{id}/toggle` | 保留旧开关接口；返回 `{id,enabled}` |
| DELETE | `/api/skills/{id}` | 删除已校验的单个技能及其可证明由本应用管理的状态/适配；不删除未知或被修改的适配；旧成功格式保留 |
| GET | `/api/skills/{id}/export` | 可选 query `scope=project|user|legacy`；返回标准 ZIP 包（含 `<name>/SKILL.md` 和原始资源字节，不含宿主管理状态）；legacy 导出生成标准说明但不修改源包；错误仍为 JSON 封套 |
| POST | `/api/skills/github/preview` | `{url:string,ref?:string|null,path?:string|null}` → `{source,candidates}` |
| POST | `/api/skills/github/import` | `{source,paths:string[],scope?:"project"|"user",claude_adapter?:boolean}` → `{skills:SkillInfo[]}`；默认 project/false，有效新包立即启用 |

`SkillInfo` 保留 `id/name/description/content/enabled`，增加 `format`、`compatibility_status`、`issues`、`content_hash`、`source`、`locally_modified`、`metadata` 和 `resources`。`content` 是含 frontmatter 的原始说明；Agent 按需加载完整 `SKILL.md`，不预先把列表里的所有说明注入模型。

本轮新增 `scope`（`project` / `user` / `legacy`）、`install_path`、`claude_adapter` 和 `scope_conflicts`。默认聚合发现按项目、用户、旧私有目录排序；新安装同名冲突拒绝，不覆盖或隐式迁移。启用/来源/管理信息保存在包外，标准包原始文件不被改成私有格式。Claude 适配优先同内容链接，必要时使用校验的受管理副本；不把适配重复列为另一个 Skill，也不覆盖已有 `.claude/skills` 包。

- `format`：`standard` / `legacy` / `invalid`。
- `compatibility_status`：`supported`（基础说明能力支持）、`partial`（部分支持，须查看缺项）、`review_required`（错误或不安全包，不可启用）。诊断不能证明自然语言描述的所有步骤都可执行。
- `issues`：`[{code,severity,message}]`；`severity` 为 `warning` / `error`。
- 开发候选版本新增警告 `RESOURCE_DIRECTORY_UNSUPPORTED`：包包含 `examples/` 等当前资源读取边界之外的目录时，兼容状态标为 `partial` 并说明目录。文件保留但不能读取；不扩大读取白名单或执行权限。`issues` 的字段结构不变，客户端应保留未识别警告的 `message`，不能将未知警告当成完全支持。
- `resources`：`[{path,size,supported}]`。专用读取接口支持 `references/`、`assets/` 下白名单 UTF-8 文本；其他资源和脚本仍按原始文件保留，需要时由既有普通工具在其权限边界内读取/执行，导入本身不执行脚本。
- GitHub `source`：预览/导入用 `{owner,repo,commit_sha,path}`，其中 SHA 为完整 40 位提交；安装后的来源额外含 provider、导入时间及摘要。本地编辑标为 `locally_modified`，外部包编辑自动停用，需单独确认启用，不能同一请求边改边启用。
- `candidates` 包含仓库相对目录 `path`、技能名称、描述、兼容状态及 issues；`paths` 必须选该固定提交下的实际技能目录。树截断、恶意路径、链接、重复包和超限明确失败，不覆盖本机已有包。
- 初版只读公共 GitHub 仓库。逐文件读取固定 SHA，树条目最多 5,000，累计下载最多 20 MiB、总时限 60 秒；每个包最多 128 个文件/10 MiB、单文件最多 2 MiB；不 clone、不解压 ZIP、不安装依赖。
- 典型业务错误：`SKILL_NOT_FOUND` 404、`SKILL_EXISTS` 409、`SKILL_DISABLED` / `SKILL_REVIEW_REQUIRED` / `SKILL_VERSION_CHANGED` 409、格式/路径错误 400、大小超限 413、GitHub 下载失败 502；精确错误码随已实现测试核对。

聊天运行字段见 §5.1。`skills_mode` 只控制发现/加载，不过滤普通工具或 MCP。Agent 工具 `preview_skill_import` 复用原来源校验，`import_skill` 使用固定提交和正常人工确认，安装后只追加本请求的新快照；既有 ID/hash 不被重新绑定。`load_skill` 随后读取完整 `SKILL.md`（含 frontmatter）和资源路径，`read_skill_resource` 按需读取资料，前后复核版本。off 模式不自动发现旧包，但允许用户明确导入的本请求新包继续加载。真实模型/浏览器/第三方 Agent 行为不由源码接线或导入成功代替验证。

## 6. 新增接口模板

```md
### X.X `METHOD /api/xxx`

- 状态：`draft`
- 用途：
- 前端入口：
- 请求 Header：
- 路径参数：
- 查询参数：
- 请求体：
- 成功响应：
- 错误响应：
- 字段说明：
- 枚举说明：
- 前端约束：
- 后端约束：
- Mock 规则：
- 更新时间：
```

## 7. 变更日志

| 日期 | 变更内容 | 变更人 |
| --- | --- | --- |
| 2026-11-08 | 移除 `POST /api/wiring` 与 `POST /api/wiring/extract`（工作台接线图功能下线，含前端 tab、`render_wiring`/`wiring` 工具与 SVG 生成器）；`render_safety_report` 保留但不再推送右侧面板，工作台安全护栏 tab 移除 | 00 主控与集成 |
| 2026-11-04 | §5.1 新增 `history_mode: "client" \| "server"`：server 模式由服务端按 owner/session 读取完整持久历史，请求体只提供当前 user 轮与 quote system 消息；新增 `HISTORY_UNAVAILABLE` 失败终态，长对话不再受客户端消息条数上限约束；§5.16.2 owner-bound legacy 历史返回 `server_history: true`，可走 server 历史 + 图内压缩、保存仍走 legacy Message 接口。定向隔离测试见 [testing.md](testing.md)；完整后端套件仍包含候选存量失败，不能据此宣称全量发布门禁通过 | 00 主控与集成 |
| 2026-10-31 | §5.1 `/api/chat` 可选 `message_id` 作为独立、按序 sidecar 接入 V3 owner source-binding compaction；最后一条尚未持久化的 user 槽位为 `null` 并留在 recent 后缀，旧客户端及 owner/source 不匹配场景 fail closed，ID 不进入 Agent/model/SSE，禁止回退到无绑定压缩 | 02 聊天与会话 |
| 2026-10-31 | §4 登记 `POST /api/sessions/{session_id}/messages/append-v1`；新增 §5.16.1，说明稳定 ID 幂等、UTF-8 正文/metadata 容量、同 owner 事务及 fail-closed 迁移边界；保留普通 `/messages` POST 的旧端点语义 | 02 聊天与会话 |
| 2026-10-31 | 新增 §5.16.2，说明 history-mode 对空 owner 会话执行 owner-scoped V2 root/pointer bootstrap；有 legacy Message 的 owner 会话维持 owner-gated legacy 保存且暂不支持 V2 compaction，状态不确定时 fail closed | 02 聊天与会话 |

| 2026-10-24 | §5.37 同步原生 Skills 当前接口与选定自动化结果；明确非全后端/全项目验收及真实环境、第三方 Agent 未验证边界 | 00 主控 |
| 2026-10-22 | 按真人要求分派实施：无完整恢复来源时保留全tool output；Agent来源限indexed且无error，检索期间签名变更拒旧结果，管理状态列表不变；全部未测试 | 00 主控协调，01/工具worker实施 |
| 2026-10-22 | 候选可选tool metadata locator及LOW read_tool_event，宿主范围/删除/chain/hash/预算读取且无授权重放；KB上传有界读与配置校验前移防无效请求触发旧清理；全部未测试 | 00 主控 |
| 2026-10-22 | 候选 Agent 最终请求总预算超限/不可核时，聊天和续跑发送 CONTEXT_LIMIT 并失败结束，保原历史且不自动降级；按最新真人约束，全部落实后统一测试，当前未验收 | 00 主控 |
| 2026-10-22 | 内部无正文工具返回档案接宿主请求scope，Skills仍不授原文archive、续跑身份不匹配构建前409；无新公开读取接口，未测试/未接受 | 00 主控 |
| 2026-10-11 | §5.16 登记 02 写权撤销后由 03 接 B1a 窄数据投影，保留 HIGH/CRITICAL 告警与 UNKNOWN 源码补核边界，字段兼容设计未变，尚无实现/测试验收 | 00 主控 |
| 2026-10-11 | §5.16 登记 S02 B 可选 skill_trace、严格安全投影和兼容边界；B1a 前端先影响门禁后实现，页面/B2 CRUD 尚未授权，无数据库迁移，不作已实现验收 | 00 主控 |
| 2026-10-08 | §5.36 修正 Explorer 根目录授权状态：所有路由要求 Bearer session，只有显式 open 授权且按 session 哈希隔离；browse 不授权。补充项目根 SHA-256 回收区分桶路径、已删除列表与恢复说明，暂不提供永久删除 | 00 主控 |
| 2026-10-04 | §5.1 登记 F1 的服务端请求来源状态与 HITL 续跑快照边界；§5.19 更新检索设置自动化回归状态。完整门禁为后端 606 项、前端 99 项通过；真实 API/浏览器/质量评测仍未验收，详见 testing.md | 04 工具扩展与安全执行 |
| 2026-10-04 | §2.14、§5.1、§5.19 补充检索设置范围与原子拒绝、每次检索 top-k、阈值单位/作用范围、空 KB 选择语义及请求内 `srcN` 生命周期；登记当前实现与待接线/待验收边界，不标记端到端 `verified` | 04 工具扩展与安全执行 |
| 2026-10-04 | §5.1 对齐 webfetch 失败封套、空回答/idle 超时、完成/等待字段；§5.27 对齐续跑错误、等待和主动停止。定向自动化结果见 testing.md，不代表真实网络或全部 RAG 质量验收 | 00 主控 |
| 2026-10-03 | Skills 管理与记忆字段已实现；MCP 改为 RAM 配置和严格 stdio，确认逐调用绑定 call_id、参数与连接实例，失败不重放；验证边界见 testing.md | 00 主控 |
| 2026-10-03 | §5.37 约定 Markdown Skills 管理、固定 SHA 的 GitHub 预览/导入及适配诊断；§5.1 登记只读技能模式和记忆快照字段，正在实施，未宣称联调通过 | 00 主控 |
| 2026-10-03 | §5.19 手动记忆保存严格校验：字符串最多 4,000 字符，空串清空，非法值 422 且整批不写入；界面联调状态另行记录 | 00 主控 |
| 2026-10-02 | 日期 1–3 实施契约：§2.1 明确受支持启动入口仅本机环回监听；§5.3 明确零向量/入库失败进入 error、终态停止轮询；§5.27 明确非法 decision 返回 422 且不执行工具。实际验证记录另见 testing.md，不因本行登记宣称验收通过 | 00 主控 |
| 2026-09-24 | §5.5/§5.30 对齐接线图端到端软件实现：规范连接改为嵌套 `from/to` 端点，保留两种旧扁平请求格式；补充 MCU/LED 引脚、端点校验 422、提取结果可直接送入生成接口及验证边界 | 00 主控 |
| 2026-09-24 | §5.16 消息保存增加可选稳定 ID、同会话更新与跨会话 409；§5.36 Explorer 对齐 Bearer session、文件版本冲突和可恢复删除/恢复接口，并修正旧版 `open/read` 响应包装说明 | 00 主控 |
| 2026-09-24 | §5.9 对齐 `/api/tool` 的原始 ToolResultEnvelope 与权限门禁：补充 `PERMISSION_CONFIRMATION_REQUIRED` / `PERMISSION_DENIED`，确认 `ask`/`deny` 时 HTTP 200 且不执行工具；记录通用前端客户端尚未适配 | 00 主控 |
| 2026-06-19 | 初始版本 | Codex |
| 2026-06-19 | 补充 9 项开发约定：分页约定（2.10）、日期时间格式（2.11）、文件上传约束（2.12）、空值与缺省值（2.13）、错误码列表（2.14）、请求追踪 ID（2.15）、CORS 策略（2.16）、SSE 连接管理（2.17）、WebSocket 重连策略（2.18） | Codex |
| 2026-06-19 | 新增 CRUD 接口文档（5.11–5.19）：sessions CRUD、messages CRUD、settings 读写 | Codex |
| 2026-06-19 | 新增 HTTP 状态码约定（2.4）；新增认证 Header 约定（2.5）；收严标准响应格式（2.6）；新增 API 版本化约定（2.2）；KB 扩展为多个端点（5.3）；WS 路径从 `/monitor` 改为 `/api/monitor`（5.10）；`/api/tool` 与 SSE `tool` 事件边界说明（5.9）；新增变更日志（7） | Codex |
| 2026-06-20 | 新增 `/api/diagnose` 接口（5.20），实现 GPIO 安全、编译预检、引脚冲突、内存估算、Flash 兼容性五类诊断；`/api/wiring` 状态推进为 `implemented` 并补充请求/响应字段说明；错误码表新增 `DIAGNOSE_FAILED` | Trae |
| 2026-06-20 | 对齐 SSE 事件字段：`thinking` 改 `content` 并加 `source`、`source` 改 `id` 并扩展字段、`tool` 加 `icon`/`result`、done 加 `usage`、新增 `progress` 事件（5.1/5.7/5.8）；标准化接口响应：`/api/kb/upload` 增加错误响应、`/api/wiring` 增加 `bom`、`/api/models` 错误码区分 `AUTH_FAILED`/`MODEL_FETCH_FAILED`；错误响应 `details` 统一必填可 null（2.6）；废弃 `MISSING_API_KEY`、`FETCH_FAILED`（2.14） | Codex |
| 2026-06-21 | 新增沙箱执行接口（5.21）和沙箱状态接口（5.22）、审计日志接口（5.23） | Codex |
| 2026-06-20 | 前端真实对接：SerialPane WebSocket 去 fallback、FlashPane build/upload 去模拟、SafetyPane 动态提取引脚、PreviewPane 接入 `/api/diagnose`；`/api/diagnose` 补入接口目录；移除 5.7/5.8/5.10 前端 mock 规则 | Trae |
| 2026-06-23 | 修复 `/api/chat` 请求体：将嵌套 `settings` 结构改为扁平结构，新增 `max_tokens`、`long_term_memory`、`model` 字段；补充 `messages.content` 多模态支持说明 | Trae |
| 2026-06-23 | RAG 全量化实现：新增 6 个 KB collection 管理接口（5.3）：`GET/POST /api/kb/collections`、`GET/DELETE /api/kb/collections/{kb_id}`、`PATCH /api/kb/collections/{kb_id}/toggle`、`POST /api/kb/embedding-models`；`/api/kb/upload` 改造支持 `kb_id`+`chunk_method`+`chunk_size` 参数；`/api/kb/list` 加 `kb_id` 过滤；新增错误码 `KB_NOT_FOUND`/`KB_NOT_DELETABLE`/`KB_CREATE_FAILED`/`KB_DELETE_FAILED`/`AGENT_CHUNK_FAILED`/`AGENT_CHUNK_INCOMPLETE`；KB 接口状态推进为 `implemented` | Trae |
| 2026-06-23 | `/api/chat` SSE `source` 事件新增 `kb_id`+`kb_name` 字段，RAG 搜索改为走 `KnowledgeBaseManager.search_all_enabled()` 多 KB 融合检索 | Trae |
| 2026-06-29 | 文档对齐代码核查：§4 sessions/settings 表格修复 BEL 损坏的反引号；§2.14 错误码表补 5 个 sandbox 错误码（`EMPTY_CODE`/`CODE_TOO_LONG`/`UNSUPPORTED_LANGUAGE`/`SANDBOX_UNAVAILABLE`/`EXECUTION_TIMEOUT`）；§5.7/§5.8 状态 `agreed`→`mocked`（对齐 build_routes.py mock SSE 现状）；§5.21/§5.22 状态 `draft`→`implemented`（对齐 sandbox_routes.py 真实 Docker 沙箱实现） | Trae |
| 2026-07-01 | v3 Agent 接入：§5.1 请求体新增 `use_agent`/`permission_mode`/`tool_keys` 字段；SSE 事件表新增 `tool_call`/`tool_result`/`tool_confirm_required` 3 个事件 + `error.code` 枚举扩展（`RECURSION_LIMIT`/`CONTEXT_LIMIT`/`AGENT_TIMEOUT`/`AGENT_FALLBACK`/`AGENT_ERROR`）；§4 接口目录新增 4 个 `/api/agent-sandbox/*` 端点；§5 新增 §5.24-5.27 agent-sandbox 接口详情（policy 查询/切换、audit 查询、resume HITL 恢复） | Trae |
| 2026-07-01 | 文档补全对齐 main.py 注册的全部 router：§2.14 错误码表补 12 个错误码（`INVALID_POLICY`/`INVALID_SETTINGS_KEY`/`NOT_FOUND`/`AUTH_REQUIRED`/`AUTH_INVALID`/`PAYLOAD_TOO_LARGE`/`DEVICE_SCAN_FAILED`/`DIAGNOSE_FAILED`/`WIRING_FAILED`/`AUDIT_FAILED`/`EXTRACT_FAILED`/`TOOL_ERROR`）；§4 接口目录补 22 条缺失路由（`/api/tools`、`/api/token-usage/stats`、`/api/wiring/extract`、`/api/auth/store-key`+`/keys`+`/keys/{provider}`、`/api/mcp/servers/*` 6 条、`/api/feedback/*` 2 条、`/api/search`、KB 补充 6 条：rename/config/documents/chunks/chunks/{id}/export/import、`DELETE /api/sessions/{id}/messages?keep_count=N`）；§5 新增 §5.28-5.35 详情段（tools/token-usage/wiring-extract/auth/mcp/feedback/search/KB 补充端点） | Trae T1 文档修复子代理 |
| 2026-07-03 | §5.7 `/api/build` + §5.8 `/api/upload` 从 `mocked` 推进为 `implemented`（基于 PlatformIO 真实编译烧录，见 `backend/src/hardware/pio_runner.py`）；请求体从 `env`+`project_dir` 改为 `code`+`board`+`options`（build）和 `binary_path`/`code` 二选一+`port`+`board`+`options`（upload）；SSE 事件表新增 `thinking`/`compile_log` 事件，`done` 事件改为含 `binary_path?`/`error.code`；新增 SSE 错误码 `PIO_NOT_INSTALLED`/`COMPILE_FAILED`/`COMPILE_TIMEOUT`/`UNSUPPORTED_BOARD`/`SPAWN_FAILED`/`INVALID_ARGS`/`INVALID_BINARY_PATH`/`BINARY_NOT_FOUND`/`PORT_NOT_FOUND`/`PORT_BUSY`/`UPLOAD_TIMEOUT`；§2.14 错误码表暂未补，待后续修订 | Trae T2 文档同步子代理 |
| 2026-07-07 | 新增 Explorer 文件资源管理器接口 §4/§5.36：`/api/explorer/{open,read,write,create,rename,delete,move,diff,watch}`，覆盖第四栏文件树、编辑器保存、diff 视图、SSE 实时监听 | Trae |
| 2026-07-08 | Explorer 接口补充：`GET /api/explorer/dir?path=` 支持目录懒加载；`FileNode` 新增 `lazy` 字段说明 | Trae |
| 2026-09-23 | `/api/upload`：模式 2 编译成功改为非终态提示；终态 `done` 延至端口锁释放后发送，串口监视按烧录前的连接状态/端口/波特率自动恢复；明确 `verified` 状态不等于实机硬件验收 | Codex |
| 2026-09-23 | §3.4 补充六条功能线的跨接口交接和单一写入规则；未改变 API 字段或状态 | Codex |
| 2026-09-26 | §2.17 对齐聊天心跳、45 秒无数据报错和手动重试行为；§5.1 补充 `heartbeat` 事件 | Codex |

## 8. 包工头检查清单

每次前后端准备开工或联调前，至少检查以下问题：

- 这个接口是否已经写进本文档？
- 状态是 `draft` 还是 `agreed`？
- 前端调用名、路径、方法是否一致？
- 后端返回字段名是否和文档完全一致？
- 错误格式是否统一？
- SSE / WebSocket 的事件结构是否写清？
- 认证 Header 是否需要传递？
- 有没有 mock 占位规则，避免一方空等另一方？

如果以上任一问题答案是否定的，这个接口就不应直接进入联调。

## 9. 可观测字段（W5，2026-11-05）

- HTTP 响应头同时返回 `x-request-id` 与 `x-trace-id`，二者值相同；请求可带
  `x-trace-id`/`x-request-id` 透传上游 trace。
- SSE 帧在请求 trace 上下文中增加 `id: <trace_id>:<seq>`，data JSON 增加
  `trace_id` 与 `seq`；无 trace 上下文的直接调用保持旧格式。
- `token_usage` 新增 `trace_id`/`run_id`/`call_id`/`cached_tokens`/`cost_usd`/
  `latency_ms`/`status`/`source`；旧字段不变。
- `tool_audit` 新增 `trace_id`/`result_hash`；`call_id`/`success`/`error_type`
  现在由 `log_tool_call` 接收并落库。
- Agent 路径的 `done.usage` 仍未填充；归属 `chat_routes`/`sse_adapter`，
  由 W5 后续批次或对应线程处理。

## 10. Memory v2（2026-11-06）

Memory v2 保留旧 `/api/memory` 路径，新增 `tier/status/project_id/evidence`
字段和审核/工作记忆接口。所有响应沿用 `{success, data}` envelope；默认读
`status=active`，审核路径显式读 `proposed`。单机个人用户，`principal_id`
默认 `local`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/memory` | 过滤 `scope/scope_key/tier/status/project_id/kind/q/limit`；默认只返回 active |
| POST | `/api/memory` | 显式创建；`tier` 可取 `working/project/user/episodic`，`tier` 优先于旧 `scope` |
| PATCH | `/api/memory/{id}` | 更新 content/structured/confidence/expires_at |
| DELETE | `/api/memory/{id}` | 软删除，并从 Markdown 镜像移除 |
| POST | `/api/memory/search` | 关键词检索；默认 active，可按 tier/project 过滤 |
| GET | `/api/memory/proposals` | 列出 proposed 候选 |
| POST | `/api/memory/proposals/{id}/approve` | proposed → active |
| POST | `/api/memory/proposals/{id}/reject` | proposed → rejected |
| GET | `/api/memory/working/{session_id}` | 当前 session 的最新有效九字段 checkpoint；clear/delete 后返回 `null` |
| GET | `/api/memory/episodes?session_id=` | checkpoint 历史摘要，标记 `valid` |
| GET | `/api/memory/status?session_id=` | schema/表/列/计数/epoch/working snapshot/镜像路径 |
| POST | `/api/memory/clear` | 推进 memory-clear epoch，并清理指定 session 的 working 条目和 snapshots；不动 user/project |

设置键 `autoMemoryEnabled` 接受布尔值或 `"true"/"false"`，后端统一存成小写
字符串；默认关闭。Memory 内容始终按明文参考数据注入，不当作指令；硬件档案
仍走 `hardware-profile` Skill，不进入通用记忆。

## 11. Todo 与 Plan（2026-11-06）

Todo 使用已有 `todos` 表作为服务端权威快照；Plan 使用新 `plans` 表按会话保存
版本。两者都沿用 `{success, data}` envelope 和会话 owner 规则；未绑定 owner 的
旧会话保持可访问。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sessions/{sid}/todos` | 返回 `{todos:[{content,status,priority}], updated_at}`；空清单 `updated_at=null` |
| DELETE | `/api/sessions/{sid}/todos` | 清空并返回 `{todos:[], cleared:true}` |
| GET | `/api/sessions/{sid}/plans/latest` | 最新计划完整 JSON，或 `{plan:null}` |
| GET | `/api/sessions/{sid}/plans?limit=20` | 版本元数据列表；不返回 steps |
| POST | `/api/sessions/{sid}/plans/{plan_id}/approve` | 只接受最新 `pending` 版本，返回 `{plan}` |
| POST | `/api/sessions/{sid}/plans/{plan_id}/reject` | 只接受最新 `pending` 版本，返回 `{plan}` |

Plan JSON 字段：`id, session_id, version, status, title, summary, steps[],
risks[], open_questions[], run_id, executed_run_id, created_at, decided_at`。
`status` 为 `pending/approved/rejected/superseded`；新版本会把旧的未执行
`pending/approved` 版本改为 `superseded`，已开始执行的计划不被覆盖。

`ChatRequest` 新增 `approved_plan_id?: string`，`permission_mode` 新增请求级
`"plan"`（不写入设置，刷新后回到原三态）。带 `approved_plan_id` 时后端校验
计划属于该会话、已批准且仍是最新版本；执行请求不能同时使用 `permission_mode
="plan"`。批准只提供任务上下文，不预授权任何工具调用；写文件、命令、MCP、构建
和烧录仍按原权限策略确认。

SSE 新增：

- `plan_update {plan}`：`submit_plan` 成功后推送；执行开始时也会推送带
  `executed_run_id` 的版本，前端据此隐藏“待执行”提示。
- `todo_update {todos:[]}`：合法的清空事件，前端必须用空数组覆盖旧展示。
- 空 session 的 Todo 只走内存回退；不影响聊天完成。
