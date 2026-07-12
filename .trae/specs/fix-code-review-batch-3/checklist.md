# Checklist

## 安全 / 鉴权
- [ ] read_file.execute 开头调用了 validate_path(path, is_write=False)
- [ ] write_file.execute 开头调用了 validate_path(path, is_write=True)
- [ ] edit_file.execute 开头调用了 validate_path(path, is_write=True)
- [ ] bypassPermissions 模式读 .env 被拒
- [ ] auth.py store_key 有 _require_auth 调用
- [ ] auth.py _load_store/_save_store 有 threading.Lock 保护
- [ ] auth.py _save_store 用临时文件 + os.replace 原子替换
- [ ] auth.py 过期 session 被清理
- [ ] auth.py delete_key 清理关联 session

## RAG 检索正确性
- [ ] kb_manager.py search() 融合后不再用 score_threshold 二次过滤
- [ ] rrf_fusion docstring 改为 average
- [ ] search() BM25-only 注释更正
- [ ] import_kb 查询加了 kb_id 过滤
- [ ] reranker fallback 检测改为 any(s != 0.0)

## 后端功能 bug
- [ ] chat_routes.py:433 有 await _build_agent_for_payload
- [ ] pio_runner StreamContext 有 done_sent 标志
- [ ] _build_pio_done_event 检查 done_sent
- [ ] UploadRequest 有 framework / lib_deps 字段
- [ ] _ensure_upload_port 透传 framework / lib_deps
- [ ] _stream_pio_subprocess 有 try/finally + proc.wait()
- [ ] feedback_routes.py 裸 SQL 用 text() 包裹或改 ORM
- [ ] diagnose_code 初始化 found: list[str] = []
- [ ] create_session 复制到分支点后 break（不 continue 跳过）

## 前端 SSE / 状态管理
- [ ] useChatStore 有 backgroundSseRequests: Map state
- [ ] setActiveSession 切到非流式会话时移入 backgroundSseRequests
- [ ] setActiveSession 三元 no-op 已修复
- [ ] sendMessage 前检查并 abort 后台 SSE
- [ ] truncateAndResend 的 sendMessage 在 apiDelete.then() 里
- [ ] _lastToolCallId 按 session 隔离
- [ ] apiSSE onError 区分已收到 done 和中途断开
- [ ] FlashPane 有 useRef<AbortController>
- [ ] FlashPane 有停止按钮
- [ ] FlashPane 卸载时 abort

## 高优先级建议修改
- [ ] agent_factory build_tool_specs 改为 per-request 实例
- [ ] autocompact _call_summarizer 类型收敛
- [ ] sse_adapter finally 区分 task.cancelled()
- [ ] audit_recorder _redact_args 对 command value 脱敏
- [ ] run_command timeout_ms 有 ge=1000
- [ ] kb_manager list_kbs 用 GROUP BY
- [ ] document_processor _parse_pymupdf_per_page 有 try/finally

## 最终验证
- [ ] 后端所有修改文件 python -c 导入正常
- [ ] pytest tests/ 跑通
- [ ] 前端 tsc --noEmit 0 error
- [ ] 后端启动 + 59 路由正常
- [ ] docs/pitfalls.md 已更新
- [ ] git commit + push
- [ ] 全面审查报告已生成
