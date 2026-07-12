# Tasks

## 阶段 1：安全 / 鉴权（独立可并行）

- [x] Task 1: read_file / write_file / edit_file 补 path_guard
  - [x] SubTask 1.1: read_file.py execute 开头加 `validate_path(path, is_write=False)`
  - [x] SubTask 1.2: write_file.py execute 开头加 `validate_path(path, is_write=True)`
  - [x] SubTask 1.3: edit_file.py execute 开头加 `validate_path(path, is_write=True)`
  - [x] SubTask 1.4: 验证 bypassPermissions 模式读 .env 被拒

- [x] Task 2: auth.py store_key 鉴权 + 并发锁
  - [x] SubTask 2.1: store_key 函数体开头加 `_require_auth(authorization, store)`（首次无 Provider 时 _require_auth 直接 return）
  - [x] SubTask 2.2: 加 `_store_lock = threading.RLock()` 包裹 _load_store + _save_store
  - [x] SubTask 2.3: _save_store 改为写临时文件 + os.replace 原子替换
  - [x] SubTask 2.4: 过期 session 清理（get_provider_key_by_session 检测过期时删除）
  - [x] SubTask 2.5: delete_key 清理关联 session

## 阶段 2：RAG 检索正确性（独立可并行）

- [x] Task 3: score_threshold 语义修复
  - [x] SubTask 3.1: kb_manager.py search() 删除融合后的 `if score_threshold > 0: fused = [r for r in fused if r.score >= score_threshold]`
  - [x] SubTask 3.2: 确认 vector_store.search 已有 score_threshold 过滤（向量阶段过滤即可）
  - [x] SubTask 3.3: rrf_fusion docstring 改为 average
  - [x] SubTask 3.4: search() 注释 BM25-only 不受 threshold 影响更正为"受影响"

- [x] Task 4: import_kb 跨 KB 冲突 + reranker fallback
  - [x] SubTask 4.1: kb_routes.py import_kb 查询加 `KnowledgeDoc.kb_id == kb_id` 过滤
  - [x] SubTask 4.2: kb_manager.py:1016 reranker fallback 检测改 `any(s != 0.0 for _, s in reranked)`
  - [x] SubTask 4.3: 验证 reranker top-1=0.0 时不跳过

## 阶段 3：后端功能 bug（独立可并行）

- [x] Task 5: HITL resume 漏 await
  - [x] SubTask 5.1: chat_routes.py:433 `agent = _build_agent_for_payload(...)` 改为 `agent = await _build_agent_for_payload(...)`

- [x] Task 6: pio_runner 编译超时重复 done + 烧录丢 framework
  - [x] SubTask 6.1: StreamContext 加 `done_sent: bool = False` 标志
  - [x] SubTask 6.2: _build_pio_done_event 检查 done_sent，已发过则不再发
  - [x] SubTask 6.3: UploadRequest 加 framework / lib_deps 字段
  - [x] SubTask 6.4: _ensure_upload_port 透传 framework / lib_deps
  - [x] SubTask 6.5: build_routes.py _run_pio_upload 构造时填入 framework / lib_deps
  - [x] SubTask 6.6: _stream_pio_subprocess 加 try/finally，finally 中 `if proc.returncode is None: await _kill_process(proc); await proc.wait()`

- [x] Task 7: feedback_routes + diagnose_code + create_session
  - [x] SubTask 7.1: feedback_routes.py 裸 SQL 改 `from sqlalchemy import text` + `db.execute(text("INSERT ..."), {...})` 或用 ORM
  - [x] SubTask 7.2: hardware_routes.py diagnose_code 在 if/elif/else 之前初始化 `found: list[str] = []`
  - [x] SubTask 7.3: crud.py create_session 去掉 continue，改为复制到分支点后 break

## 阶段 4：前端 SSE / 状态管理（独立可并行）

- [x] Task 8: 后台 SSE 管理 + setActiveSession 修复
  - [x] SubTask 8.1: useChatStore 加 `backgroundSseRequests: Map<string, AbortController>` state
  - [x] SubTask 8.2: setActiveSession 切到非流式会话时，把当前 currentSseRequest 移入 backgroundSseRequests
  - [x] SubTask 8.3: setActiveSession 三元 no-op 修复（streamingSessionId / currentSseRequest 正确清理）
  - [x] SubTask 8.4: sendMessage 前检查 backgroundSseRequests 是否有该 session 的后台流，有则 abort
  - [x] SubTask 8.5: onDone / onError 时从 backgroundSseRequests 清理

- [x] Task 9: truncateAndResend + 模块级变量副作用
  - [x] SubTask 9.1: truncateAndResend 把 `setTimeout(() => sendMessage(...), 50)` 改为放进 `apiDelete.then()` 里
  - [x] SubTask 9.2: apiDelete.catch 中 toast 提示"重发失败，请手动重试"，不重发
  - [x] SubTask 9.3: `_lastToolCallId` 改为 `Map<sessionId, string>` 按 session 隔离（或移入 store state）
  - [x] SubTask 9.4: `_needsParagraphBreak` 移入 store state 或按 session 隔离

- [x] Task 10: apiSSE 断连 + FlashPane AbortController
  - [x] SubTask 10.1: client.ts apiSSE onError 中区分"已收到 done"（正常结束）和"中途断开"（连接错误）
  - [x] SubTask 10.2: 中途断开时调 onError 回调让上层提示用户"连接断开，点击重试"
  - [x] SubTask 10.3: FlashPane 用 `useRef<AbortController>` 持有当前编译 controller
  - [x] SubTask 10.4: FlashPane 加"停止"按钮调 `controller.abort()`
  - [x] SubTask 10.5: FlashPane useEffect 卸载时 abort controller

## 阶段 5：高优先级建议修改（独立可并行）

- [x] Task 11: agent_factory 并发 ctx + autocompact 类型
  - [x] SubTask 11.1: agent_factory build_tool_specs 改为 per-request 实例（不写全局 _TOOL_REGISTRY）
  - [x] SubTask 11.2: autocompact.py _call_summarizer 对 response.content 做类型收敛（isinstance str 检查）

- [x] Task 12: sse_adapter + audit_recorder + run_command
  - [x] SubTask 12.1: sse_adapter.py finally 块加 `if task.cancelled(): continue`
  - [x] SubTask 12.2: audit_recorder.py _redact_args 对 command/cmd key 的 value 额外正则脱敏
  - [x] SubTask 12.3: run_command.py RunCommandArgs.timeout_ms 加 `ge=1000`

- [x] Task 13: kb_manager N+1 + document_processor PDF 句柄
  - [x] SubTask 13.1: kb_manager.py list_kbs 改用 GROUP BY 一次查询
  - [x] SubTask 13.2: document_processor.py _parse_pymupdf_per_page 加 try/finally 关闭 doc

## 阶段 6：验证

- [x] Task 14: 全量验证
  - [x] SubTask 14.1: 后端 python -c 导入所有修改文件
  - [x] SubTask 14.2: pytest tests/ 跑通（180 passed, 1 预存失败）
  - [x] SubTask 14.3: 前端 npx tsc --noEmit 0 error
  - [x] SubTask 14.4: 附带修复 build_tool.py _emit_event NameError
  - [x] SubTask 14.5: docs/pitfalls.md 已更新
  - [-] SubTask 14.6: git commit + push（用户跳过）
  - [x] SubTask 14.7: 生成全面审查报告

# Task Dependencies

- Task 1-13 互相独立，可并行
- Task 14 依赖 Task 1-13 全部完成
