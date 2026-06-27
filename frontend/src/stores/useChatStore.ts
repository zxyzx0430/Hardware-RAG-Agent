import { create } from "zustand";
import type { Message, SourceRef, ActivityStep, Session } from "../types/session";
import type { ChatSSEEvent, Attachment } from "../types/api";
import { loadFromStorage, saveToStorage, saveSessionMessages, loadSessionMessages, removeSessionMessages, migrateMessagesToShards } from "../utils/persistence";
import { useAppStore } from "./useAppStore";
import { useSessionStore } from "./useSessionStore";
import { apiSSE, apiPost } from "../api/client";
import { useSettingsStore } from "./useSettingsStore";
import { useLogStore } from "./useLogStore";

interface ChatState {
  messages: Message[];
  sessionMessages: Record<string, Message[]>;
  sources: SourceRef[];
  isStreaming: boolean;
  streamingContent: string;
  streamingSteps: ActivityStep[];
  streamingSources: SourceRef[];
  /** 当前流式请求所属的 sessionId，用于回调时定位正确的会话 */
  streamingSessionId: string | null;
  /** 流式请求开始时间，用于实时计时 */
  streamingStartTime: number | null;
  /** done 事件中携带的 usage 数据，onDone 时写入 Message */
  _pendingUsage: import("../types/session").TokenUsage | null;
  currentSseRequest: AbortController | null;
  activeSessionId: string;
  bookmarks: string[];
  bookmarkFolders: { id: string; name: string; createdAt: number }[];
  bookmarkData: Record<string, { folderId: string; bookmarkedAt: number; sessionId: string; sessionTitle: string; content: string; role: string }>;
  bookmarkTargetMsgId: string | null;
  statsOpen: boolean;
  /** 选中的知识库 ID 列表，空数组表示使用全部已启用知识库 */
  selectedKbIds: string[];
  setSelectedKbIds: (ids: string[]) => void;
  /** 切换某个知识库的选中状态（在数组中增删） */
  toggleKbSelection: (kbId: string) => void;

  sendMessage: (content: string, attachments?: Attachment[]) => void;
  stopStreaming: (errorMessage?: string) => void;
  retryMessage: (msgId: string) => void;
  editAndResend: (msgId: string, newContent: string) => void;
  branchThread: (msgId: string) => void;
  pushCodeToWorkbench: (code: string, name: string) => void;
  setMessages: (msgs: Message[]) => void;
  setSources: (srcs: SourceRef[]) => void;
  setActiveSession: (id: string) => void;
  quoteMessage: (msgId: string) => void;
  toggleBookmark: (msgId: string, folderId?: string) => void;
  removeBookmark: (msgId: string) => void;
  isBookmarked: (msgId: string) => boolean;
  addBookmarkFolder: (name: string) => void;
  deleteBookmarkFolder: (folderId: string) => void;
  setBookmarkTargetMsgId: (id: string | null) => void;
  addBookmarkToFolder: (msgId: string, folderId: string) => void;
  moveBookmarkToFolder: (bookmarkId: string, folderId: string) => void;
  renameBookmarkFolder: (folderId: string, name: string) => void;
  exportConversation: (format: "markdown" | "json") => void;
  showStats: () => void;
  hideStats: () => void;
}

const log = useLogStore.getState().log;

// Maximum number of messages retained in memory to avoid OOM on long conversations
const MAX_MESSAGES = 200;

/** Trim messages array to the last MAX_MESSAGES entries (rolling window) */
function trimMessages(msgs: Message[]): Message[] {
  if (msgs.length <= MAX_MESSAGES) return msgs;
  return msgs.slice(-MAX_MESSAGES);
}

/** 同步 messages 到 sessionMessages 并持久化 */
function syncToSession(
  state: { messages: Message[]; sessionMessages: Record<string, Message[]>; activeSessionId: string },
  overrideMessages?: Message[]
): { messages: Message[]; sessionMessages: Record<string, Message[]> } {
  const msgs = overrideMessages ?? state.messages;
  const updatedSM = { ...state.sessionMessages, [state.activeSessionId]: msgs };
  saveSessionMessages(state.activeSessionId, msgs);
  return { messages: msgs, sessionMessages: updatedSM };
}


export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  sessionMessages: (() => {
    // 优先从分片格式加载
    const sessions = loadFromStorage("sessions", [] as { id: string }[]);
    const sm: Record<string, Message[]> = {};
    for (const s of sessions) {
      const msgs = loadSessionMessages<Message[]>(s.id, []);
      if (msgs.length > 0) sm[s.id] = msgs;
    }
    // 兼容旧格式：如果分片为空但旧 key 存在，迁移
    const raw = loadFromStorage("messages", {} as Record<string, Message[]>);
    if (raw && typeof raw === "object" && !Array.isArray(raw) && Object.keys(sm).length === 0 && Object.keys(raw).length > 0) {
      migrateMessagesToShards(raw);
      return raw;
    }
    return sm;
  })(),
  sources: [],
  isStreaming: false,
  streamingContent: "",
  streamingSteps: [],
  streamingSources: [],
  streamingSessionId: null,
  streamingStartTime: null,
  _pendingUsage: null,
  currentSseRequest: null,
  activeSessionId: loadFromStorage("activeSession", "s1"),
  bookmarks: loadFromStorage("bookmarks", [] as string[]),
  bookmarkFolders: loadFromStorage("bookmarkFolders", [{ id: "default", name: "默认收藏夹", createdAt: Date.now() }]),
  bookmarkData: loadFromStorage("bookmarkData", {}),
  bookmarkTargetMsgId: null,
  statsOpen: false,
  selectedKbIds: [],

  setSelectedKbIds: (selectedKbIds) => set({ selectedKbIds }),

  toggleKbSelection: (kbId) => set((s) => ({
    selectedKbIds: s.selectedKbIds.includes(kbId)
      ? s.selectedKbIds.filter((id) => id !== kbId)
      : [...s.selectedKbIds, kbId],
  })),

  setMessages: (messages) => {
    const trimmed = trimMessages(messages);
    set((s) => syncToSession({ ...s, messages: trimmed }));
  },
  setSources: (sources) => set({ sources }),

  setActiveSession: (id) => {
    const { activeSessionId, messages, sessionMessages, isStreaming,
            streamingSessionId, currentSseRequest, streamingContent,
            streamingSteps, streamingStartTime } = get();
    if (id === activeSessionId) return;
    log("info", "chat", `切换会话: ${activeSessionId} → ${id}`);

    // 流式中切换会话：保存部分内容到 sessionMessages，但不中止 SSE。
    // 后台 SSE 继续运行，onEvent 回调中 isActive=false 时会写入 sessionMessages[sid]。
    let finalMessages = messages;
    if (isStreaming && streamingSessionId === activeSessionId) {
      // 当前活跃会话在流式中：把 streamingContent 同步到最后一条 assistant 消息
      finalMessages = messages.map((m, i) =>
        i === messages.length - 1 && m.role === "assistant"
          ? {
              ...m,
              content: streamingContent || m.content,
              ...(streamingSteps.length > 0
                ? { activity: { durationMs: 0, steps: streamingSteps, status: "running" as const } }
                : {}),
            }
          : m
      );
      log("info", "chat", `会话 ${streamingSessionId} 流式输出在后台继续（未中止）`);
    }

    // 保存当前会话的消息
    const updatedSessionMessages = {
      ...sessionMessages,
      [activeSessionId]: finalMessages,
    };

    // 加载目标会话的消息
    const loadedMessages = updatedSessionMessages[id] || [];

    // 如果目标会话正处于流式中（streamingSessionId === id），从 sessionMessages
    // 恢复 streamingContent，避免切回后空字符串覆盖已有内容。
    const isTargetStreaming = streamingSessionId === id && !!currentSseRequest;
    let restoredContent = "";
    let restoredSteps: ActivityStep[] = [];
    if (isTargetStreaming) {
      const lastMsg = loadedMessages[loadedMessages.length - 1];
      if (lastMsg?.role === "assistant") {
        restoredContent = typeof lastMsg.content === "string" ? lastMsg.content : "";
        restoredSteps = lastMsg.activity?.steps || [];
      }
      log("info", "chat", `切回流式会话 ${id}，恢复 content (${restoredContent.length} chars)`);
    }

    set({
      activeSessionId: id,
      messages: loadedMessages,
      sessionMessages: updatedSessionMessages,
      isStreaming: isTargetStreaming,
      streamingContent: restoredContent,
      streamingSteps: restoredSteps,
      streamingSources: [],
      // 保留 streamingSessionId 和 currentSseRequest — 后台 SSE 继续运行
      streamingSessionId: isTargetStreaming ? streamingSessionId : streamingSessionId,
      streamingStartTime: isTargetStreaming ? streamingStartTime : null,
      currentSseRequest: isTargetStreaming ? currentSseRequest : currentSseRequest,
    });

    saveSessionMessages(activeSessionId, finalMessages);
    saveToStorage("activeSession", id);
  },

  sendMessage: async (content, attachments) => {
    const { messages, isStreaming, activeSessionId } = get();
    // 允许只有附件没有文字（如只发图片）
    if (isStreaming || (!content.trim() && (!attachments || attachments.length === 0))) return;
    const imgCount = attachments?.filter((a) => a.type.startsWith("image/")).length ?? 0;
    log("info", "chat", `发送消息: ${content.slice(0, 50) || "(仅附件)"}... attachments=${attachments?.length ?? 0} images=${imgCount}`);

    try {

    // 编码图片附件为 base64 ContentPart
    // 本地消息用 ContentPart[] 存储（支持图片渲染），API 请求也用 ContentPart[]
    let messageContent: string | import("../types/session").ContentPart[] = content.trim();
    let apiContent: string | import("../types/session").ContentPart[] = content.trim();
    if (attachments && attachments.length > 0) {
      const parts: import("../types/session").ContentPart[] = [
        { type: "text", text: content.trim() || "请描述这张图片" }
      ];
      // Deduplicate image attachments by content to prevent duplicate rendering
      const seenImageUrls = new Set<string>();
      for (const attachment of attachments) {
        if (attachment.type.startsWith("image/")) {
          if (seenImageUrls.has(attachment.content)) {
            log("warn", "chat", `跳过重复图片附件: ${attachment.name}`);
            continue;
          }
          seenImageUrls.add(attachment.content);
          parts.push({
            type: "image_url",
            image_url: { url: attachment.content, detail: "auto" },
          });
        }
      }
      apiContent = parts;
      // 本地显示也用 ContentPart[]，让 MarkdownRenderer 正确渲染图片
      messageContent = parts;
    }
    const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: messageContent, timestamp: Date.now() };
    const assistantMsg: Message = { id: crypto.randomUUID(), role: "assistant", content: "", timestamp: Date.now() };
    const nextMessages = trimMessages([...messages, userMsg, assistantMsg]);

    // 立即同步到 sessionMessages
    const updatedSM = { ...get().sessionMessages, [activeSessionId]: nextMessages };
    saveSessionMessages(activeSessionId, nextMessages);

    set({
      messages: nextMessages,
      isStreaming: true,
      streamingContent: "",
      streamingSteps: [],
      streamingSources: [],
      streamingSessionId: activeSessionId,
      streamingStartTime: Date.now(),
      _pendingUsage: null,
      sessionMessages: updatedSM,
    });

    // 构建请求体：历史消息 + 设置（扁平结构，对齐后端 ChatRequest）
    // 模型优先从当前会话读取（各对话独立），回退到全局设置
    const { topK, temperature, systemPrompt, longTermMemory, maxTokens, relevanceThreshold } = useSettingsStore.getState();
    const { selectedKbIds } = get();
    const currentSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
    const model = currentSession?.model || useSettingsStore.getState().model;
    log("info", "chat", `model=${model} topK=${topK} maxTokens=${maxTokens} threshold=${relevanceThreshold} sessionId=${activeSessionId} systemPrompt="${systemPrompt?.slice(0, 50)}..."`);
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    const newMsg = { role: "user" as const, content: apiContent };
    const requestBody = {
      messages: [...history, newMsg],
      top_k: topK,
      relevance_threshold: relevanceThreshold > 0 ? relevanceThreshold / 100 : 0,
      temperature,
      max_tokens: maxTokens,
      system_prompt: systemPrompt,
      long_term_memory: longTermMemory || undefined,
      model: model || undefined,
      // 空数组表示搜索全部已启用知识库，非空时只搜选中的 KB
      kb_ids: selectedKbIds.length > 0 ? selectedKbIds : undefined,
      // 只发送非图片附件（图片已在 messages 的 ContentPart[] 中）
      // 后端会从 attachments 提取图片再拼到消息里，导致重复
      attachments: attachments && attachments.length > 0
        ? attachments.filter((a) => !a.type.startsWith("image/"))
        : undefined,
    };

    const startTime = Date.now();
    // ★ 关键：捕获发起请求时的 sessionId，回调中始终使用此 ID
    const requestSessionId = activeSessionId;

    const controller = new AbortController();
    set({ currentSseRequest: controller });

    apiSSE("chat", requestBody, {
      onEvent: (event) => {
        if (event.type === "error") {
          const errMsg = event.message ?? "未知错误";
          log("error", "chat", `SSE 错误: ${errMsg}`);
          get().stopStreaming(errMsg);
          return;
        }
        const sse = event;
        // ★ 始终写入发起请求时的会话，而非当前活跃会话
        set((s) => {
          const sid = requestSessionId;
          const isActive = s.activeSessionId === sid;
          const sessionMsgs = s.sessionMessages[sid] ?? [];
          const currentMsgs = isActive ? s.messages : sessionMsgs;

          switch (sse.type) {
            case "thinking": {
              const thinkContent = sse.content ?? "";
              const thinkSource = sse.source;
              log("debug", "chat", `[thinking] source=${thinkSource} content="${thinkContent.slice(0, 30)}"`);

              // 构建 step
              const steps = [...(isActive ? s.streamingSteps : (currentMsgs[currentMsgs.length - 1]?.activity?.steps || []))];
              const lastStep = steps[steps.length - 1];

              if (thinkSource === "reasoning") {
                if (lastStep && lastStep.type === "thinking" && lastStep.source === "llm") {
                  steps[steps.length - 1] = { ...lastStep, content: thinkContent, source: "reasoning" };
                } else if (lastStep && lastStep.type === "thinking" && lastStep.source === "reasoning") {
                  steps[steps.length - 1] = { ...lastStep, content: (lastStep.content || "") + thinkContent };
                } else {
                  // Source switched: close previous thinking step before creating a new one
                  if (lastStep && lastStep.type === "thinking") {
                    steps[steps.length - 1] = { ...lastStep, status: "done" };
                  }
                  steps.push({ type: "thinking", id: `h-${Date.now()}`, content: thinkContent, source: "reasoning" });
                }
              } else if (lastStep && lastStep.type === "thinking" && lastStep.source === thinkSource) {
                steps[steps.length - 1] = { ...lastStep, content: (lastStep.content || "") + thinkContent };
              } else {
                // Source switched: close previous thinking step before creating a new one
                if (lastStep && lastStep.type === "thinking") {
                  steps[steps.length - 1] = { ...lastStep, status: "done" };
                }
                steps.push({ type: "thinking", id: `h-${Date.now()}`, content: thinkContent, source: thinkSource });
              }

              if (isActive) {
                return { streamingSteps: steps };
              } else {
                // 后台：直接更新 sessionMessages 中的 assistant 消息的 activity
                const msgs = [...currentMsgs];
                const last = msgs[msgs.length - 1];
                if (last?.role === "assistant") {
                  msgs[msgs.length - 1] = { ...last, activity: { durationMs: 0, steps, status: "running" } };
                }
                const newSM = { ...s.sessionMessages, [sid]: msgs };
                saveSessionMessages(sid, msgs);
                return { sessionMessages: newSM };
              }
            }

            case "text": {
              const chunk = sse.content ?? "";
              const msgs = [...currentMsgs];
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant") {
                // last.content may be ContentPart[]; coerce to string to avoid array+string corruption
                const prevContent = isActive
                  ? s.streamingContent
                  : (typeof last.content === "string" ? last.content : "");
                const newContent = prevContent + chunk;

                // Close any open reasoning/llm thinking step when answer text begins.
                // Without this, the thinking card stays expanded while text streams below it.
                let updatedSteps = s.streamingSteps;
                if (isActive) {
                  const steps = [...s.streamingSteps];
                  const lastStep = steps[steps.length - 1];
                  if (lastStep && lastStep.type === "thinking" && lastStep.status !== "done") {
                    steps[steps.length - 1] = { ...lastStep, status: "done" };
                    updatedSteps = steps;
                  }
                  msgs[msgs.length - 1] = { ...last, content: newContent };
                } else if (last.activity?.steps?.length) {
                  const steps = [...last.activity.steps];
                  const lastStep = steps[steps.length - 1];
                  if (lastStep && lastStep.type === "thinking" && lastStep.status !== "done") {
                    steps[steps.length - 1] = { ...lastStep, status: "done" };
                  }
                  msgs[msgs.length - 1] = { ...last, content: newContent, activity: { ...last.activity, steps } };
                } else {
                  msgs[msgs.length - 1] = { ...last, content: newContent };
                }

                const newSM = { ...s.sessionMessages, [sid]: msgs };
                // NOTE: saveSessionMessages is intentionally NOT called here.
                // The store subscribe (line ~927) handles persistence via 500ms debounce
                // during streaming. Calling it per-token caused main-thread blocking.
                if (isActive) {
                  return { streamingContent: newContent, messages: msgs, sessionMessages: newSM, streamingSteps: updatedSteps };
                } else {
                  return { sessionMessages: newSM };
                }
              }
              return {};
            }

            case "tool": {
              const newStep = {
                type: "tool" as const,
                id: `t-${Date.now()}`,
                name: sse.name,
                args: typeof sse.args === "string" ? sse.args : JSON.stringify(sse.args ?? {}),
                result: sse.result,
              };

              if (isActive) {
                return { streamingSteps: [...s.streamingSteps, newStep] };
              } else {
                // 后台：追加 tool step 到 sessionMessages 中的 activity
                const msgs = [...currentMsgs];
                const last = msgs[msgs.length - 1];
                if (last?.role === "assistant") {
                  const existingSteps = last.activity?.steps || [];
                  msgs[msgs.length - 1] = { ...last, activity: { durationMs: 0, steps: [...existingSteps, newStep], status: "running" } };
                }
                const newSM = { ...s.sessionMessages, [sid]: msgs };
                saveSessionMessages(sid, msgs);
                return { sessionMessages: newSM };
              }
            }

            case "source": {
              const newSource: SourceRef = {
                id: sse.id ?? `src-${Date.now()}`,
                title: sse.title ?? "未知来源",
                doc: sse.doc ?? "",
                page: sse.page ?? 0,
                chunk_index: sse.chunk_index,
                page_start: sse.page_start,
                page_end: sse.page_end,
                section_title: sse.section_title,
                source_url: sse.source_url,
                category: sse.category,
                chunk_method: sse.chunk_method,
                score: sse.score ?? 0,
                excerpt: sse.excerpt ?? "",
                kb_id: sse.kb_id,
                kb_name: sse.kb_name,
                small_chunk_id: sse.small_chunk_id,
              };
              const msgs = [...currentMsgs];
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant") {
                const srcs = [...(last.sources || []), newSource];
                msgs[msgs.length - 1] = { ...last, sources: srcs };
              }
              const newSM = { ...s.sessionMessages, [sid]: msgs };
              saveSessionMessages(sid, msgs);
              if (isActive) {
                return { streamingSources: [...s.streamingSources, newSource], sources: [...s.streamingSources, newSource], messages: msgs, sessionMessages: newSM };
              } else {
                return { sessionMessages: newSM };
              }
            }

            case "done": {
              if (!("usage" in sse)) return {};
              const usage = sse.usage;
              if (usage) {
                log("ok", "chat", `Token usage: prompt=${usage.prompt_tokens} completion=${usage.completion_tokens} total=${usage.total_tokens}`);
                return { _pendingUsage: { promptTokens: usage.prompt_tokens || 0, completionTokens: usage.completion_tokens || 0, totalTokens: usage.total_tokens || 0 } };
              }
              return {};
            }

            case "progress":
              // build / upload 等进度事件，store 无需处理
              return {};

            default:
              return {};
          }
        });
      },
      onDone: () => {
        const durationMs = Date.now() - startTime;
        const usage = get()._pendingUsage;
        log("ok", "chat", `回答完成 (${(durationMs / 1000).toFixed(1)}s)${usage ? ` tokens=${usage.totalTokens}` : ''}`);
        set((s) => {
          const sid = requestSessionId;
          const isActive = s.activeSessionId === sid;
          const sessionMsgs = s.sessionMessages[sid] ?? [];
          const currentMsgs = isActive ? s.messages : sessionMsgs;

          // 活跃会话：用 streamingSteps 构建 activity
          // 非活跃会话：从 sessionMessages 中的已有 activity 补充 durationMs 和 usage
          const finalMessages = currentMsgs.map((m, i) =>
            i === currentMsgs.length - 1 && m.role === "assistant"
              ? {
                  ...m,
                  ...(isActive && s.streamingSteps.length > 0 ? { activity: { durationMs, steps: s.streamingSteps } } : {}),
                  ...(!isActive && m.activity ? { activity: { ...m.activity, durationMs, status: "done" as const } } : {}),
                  ...(s._pendingUsage ? { usage: s._pendingUsage } : {}),
                }
              : m
          );
          const updatedSM = { ...s.sessionMessages, [sid]: finalMessages };
          saveSessionMessages(sid, finalMessages);

          // 更新会话元数据
          const msgCount = finalMessages.filter((m) => m.role === "user" || (m.role === "assistant" && m.content)).length;
          const lastUserMsg = finalMessages.filter((m) => m.role === "user").pop();
          // 从 ContentPart[] 或 string 中提取文本用于 preview/title
          const extractText = (content: string | import("../types/session").ContentPart[]): string => {
            if (typeof content === "string") return content;
            return content.filter((p) => p.type === "text").map((p) => p.text).join(" ");
          };
          const userText = lastUserMsg ? extractText(lastUserMsg.content) : "";
          const preview = userText.slice(0, 60) || "(图片)";
          const sessionStore = useSessionStore.getState();
          const currentSession = sessionStore.sessions.find((x) => x.id === sid);
          const title = currentSession?.title === "新对话" && userText
            ? userText.slice(0, 30).replace(/\n/g, " ")
            : undefined;
          useSessionStore.getState().updateSessionMeta(sid, {
            msgCount,
            preview,
            ...(title ? { title } : {}),
          });

          // 只有当前活跃会话是请求会话时，才修改 isStreaming/currentSseRequest
          if (isActive) {
            return {
              isStreaming: false,
              streamingSessionId: null,
              streamingStartTime: null,
              currentSseRequest: null,
              _pendingUsage: null,
              // Reset streaming buffers so the next send starts clean
              streamingSteps: [],
              streamingContent: "",
              streamingSources: [],
              sessionMessages: updatedSM,
              messages: finalMessages,
            };
          } else {
            // 后台会话完成：只更新 sessionMessages，不清空全局流式状态
            // （streamingContent/streamingSessionId/currentSseRequest 属于活跃会话）
            return {
              sessionMessages: updatedSM,
            };
          }
        });
      },
      onError: (err) => {
        log("error", "chat", `SSE 连接错误: ${err.message}`);
        get().stopStreaming(err.message);
      },
    }, controller);
    } catch (err) {
      log("error", "chat", `sendMessage 异常: ${err instanceof Error ? err.message : String(err)}`);
      set({ isStreaming: false });
    }
  },

  stopStreaming: (errorMessage?: string) => {
    set((state) => {
      // 幂等检查：如果已经停止过（isStreaming=false 且无 SSE 请求），直接返回
      if (!state.isStreaming && !state.streamingSessionId && !state.currentSseRequest) {
        return {};
      }
      const { currentSseRequest, messages, streamingContent, streamingSteps, streamingSessionId, activeSessionId, sessionMessages } = state;
      log("warn", "chat", errorMessage ? "流式输出因错误停止" : "用户手动停止流式输出");
      if (currentSseRequest) currentSseRequest.abort();

      // 防御：streamingSessionId 已丢失说明状态已过期（如切换会话后被清空），
      // 跳过写入避免误伤当前会话
      if (!streamingSessionId) {
        log("warn", "chat", "streamingSessionId 已丢失，跳过 stopStreaming 写入");
        return {
          isStreaming: false,
          streamingContent: "",
          streamingSteps: [],
          streamingSources: [],
          streamingSessionId: null,
          streamingStartTime: null,
          currentSseRequest: null,
        };
      }

      const sid = streamingSessionId;
      const isActive = activeSessionId === sid;
      const targetMsgs = isActive ? messages : (sessionMessages[sid] ?? []);

      // 优先取已有内容，避免 streamingContent 为空时用纯错误消息覆盖
      const lastMsg = targetMsgs[targetMsgs.length - 1];
      const existingContent = isActive
        ? streamingContent
        : (typeof lastMsg?.content === "string" ? lastMsg.content : "");

      const finalContent = errorMessage
        ? (existingContent ? `${existingContent}\n\n❌ ${errorMessage}` : `❌ ${errorMessage}`)
        : (streamingContent || existingContent);

      const finalTargetMsgs = targetMsgs.map((m, i) =>
        i === targetMsgs.length - 1 && m.role === "assistant" && finalContent
          ? { ...m, content: finalContent, ...(streamingSteps.length > 0 ? { activity: { durationMs: 0, steps: streamingSteps, status: "done" as const } } : {}) }
          : m
      );
      const newSM = { ...sessionMessages, [sid]: finalTargetMsgs };
      saveSessionMessages(sid, finalTargetMsgs);

      return {
        messages: isActive ? finalTargetMsgs : messages,
        sessionMessages: newSM,
        isStreaming: false,
        streamingContent: "",
        streamingSteps: [],
        streamingSources: [],
        streamingSessionId: null,
        streamingStartTime: null,
        currentSseRequest: null,
      };
    });
  },

  retryMessage: (msgId) => {
    const { messages, isStreaming, activeSessionId } = get();
    if (isStreaming) return; // 流式输出中不允许重试
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    log("info", "chat", `重试消息: ${msgId}`);

    // 找到对应的用户消息
    let userIdx = -1;
    for (let i = idx; i >= 0; i--) {
      if (messages[i].role === "user") { userIdx = i; break; }
    }
    if (userIdx < 0) return;
    const userContent = messages[userIdx].content;
    const truncated = messages.slice(0, userIdx);

    // 同步更新 sessionMessages
    set((s) => syncToSession({ ...s, messages: truncated }));

    // 从 ContentPart[] 中提取文本和图片附件
    if (typeof userContent === "string") {
      setTimeout(() => get().sendMessage(userContent), 50);
    } else {
      // ContentPart[]: 提取文本和图片
      const text = userContent
        .filter((p): p is Extract<import("../types/session").ContentPart, { type: "text" }> => p.type === "text")
        .map((p) => p.text)
        .join("\n");
      const imageAttachments: import("../types/session").Attachment[] = userContent
        .filter((p): p is Extract<import("../types/session").ContentPart, { type: "image_url" }> => p.type === "image_url")
        .map((p) => ({
          id: `retry-img-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          name: "image.png",
          type: "image/png",
          content: p.image_url.url,
        }));
      setTimeout(() => get().sendMessage(text || "", imageAttachments.length > 0 ? imageAttachments : undefined), 50);
    }
  },

  editAndResend: (msgId, newContent) => {
    const { messages, isStreaming } = get();
    if (isStreaming || !newContent.trim()) return; // 流式输出中不允许编辑
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    log("info", "chat", `编辑重发: ${msgId}`);

    const truncated = messages.slice(0, idx);

    // 同步更新 sessionMessages
    set((s) => syncToSession({ ...s, messages: truncated }));
    setTimeout(() => get().sendMessage(newContent), 50);
  },

  branchThread: (msgId) => {
    const { activeSessionId, messages } = get();
    if (!activeSessionId) return;
    const idx = messages.findIndex((m) => m.id === msgId);
    if (idx < 0) return;
    log("info", "chat", `分支线程: ${msgId}`);

    const branchMsgs = messages.slice(0, idx + 1).map((m, i) =>
      i === idx ? { ...m, parentId: msgId } : { ...m }
    );

    // 通过后端 API 创建带分支信息的新会话
    const currentSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
    const model = currentSession?.model || useSettingsStore.getState().model;
    const project = useSessionStore.getState().activeProject === "all" ? "" : useSessionStore.getState().activeProject;

    apiPost<{ id: string }>("sessions", {
      title: "分支对话",
      model: model || "GPT-4o",
      project,
      branch_from_session_id: activeSessionId,
      branch_from_message_id: msgId,
    }).then((res) => {
      const sid = res?.id || `s${Date.now()}`;
      log("ok", "chat", `分支会话已创建: ${sid}`);

      // 创建本地 Session 对象
      const now = Date.now();
      const session: Session = {
        id: sid,
        title: "分支对话",
        preview: "",
        model: model || "GPT-4o",
        createdAt: now,
        project,
        pinned: false,
        msgCount: branchMsgs.length,
        branchFromSessionId: activeSessionId,
        branchFromMessageId: msgId,
      };

      // 更新 sessionStore
      useSessionStore.setState((s) => {
        const updated = { sessions: [session, ...s.sessions] };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });

      // 设置分支消息到新会话
      set((s) => ({
        sessionMessages: {
          ...s.sessionMessages,
          [sid]: branchMsgs,
        },
        messages: branchMsgs,
        activeSessionId: sid,
      }));
      saveSessionMessages(sid, branchMsgs);

      // 切换到新会话
      useAppStore.getState().setActiveSession(sid);
      saveToStorage("activeSession", sid);
    }).catch(() => {
      log("warn", "chat", "分支会话仅保存在本地");
      // 回退：纯本地创建
      const sid = `s${Date.now()}`;
      const now = Date.now();
      const session: Session = {
        id: sid,
        title: "分支对话",
        preview: "",
        model: model || "GPT-4o",
        createdAt: now,
        project,
        pinned: false,
        msgCount: branchMsgs.length,
        branchFromSessionId: activeSessionId,
        branchFromMessageId: msgId,
      };

      useSessionStore.setState((s) => {
        const updated = { sessions: [session, ...s.sessions] };
        saveToStorage("sessions", updated.sessions);
        return updated;
      });

      set((s) => ({
        sessionMessages: {
          ...s.sessionMessages,
          [sid]: branchMsgs,
        },
        messages: branchMsgs,
        activeSessionId: sid,
      }));
      saveSessionMessages(sid, branchMsgs);

      useAppStore.getState().setActiveSession(sid);
      saveToStorage("activeSession", sid);
    });
  },

  pushCodeToWorkbench: (code, name) => {
    useAppStore.getState().setRightPanelOpen(true);
    useAppStore.getState().setRightMode("workbench");
    useAppStore.getState().setWbTab("preview");
    useAppStore.getState().addPreviewTab({
      id: `preview-${Date.now()}`,
      label: `${(name || "code").slice(0, 18)}.cpp`,
      code,
      language: "cpp",
    });
  },

  quoteMessage: (msgId) => {
    const target = get().messages.find((msg) => msg.id === msgId) ?? null;
    useAppStore.getState().setQuotedMsg(target);
  },

  toggleBookmark: (msgId, folderId) => {
    const { bookmarkData, bookmarkFolders } = get();
    if (bookmarkData[msgId]) {
      const next = { ...bookmarkData };
      delete next[msgId];
      const nextBookmarks = Object.keys(next);
      saveToStorage("bookmarkData", next);
      saveToStorage("bookmarks", nextBookmarks);
      set({ bookmarkData: next, bookmarks: nextBookmarks });
    } else {
      const fid = folderId || bookmarkFolders[0]?.id || "default";
      const msg = get().messages.find((m) => m.id === msgId);
      const newEntry = {
        folderId: fid,
        bookmarkedAt: Date.now(),
        sessionId: get().activeSessionId,
        sessionTitle: "当前对话",
        content: msg ? (typeof msg.content === "string" ? msg.content.slice(0, 100) : "") : "",
        role: msg ? msg.role : "assistant",
      };
      const next = { ...bookmarkData, [msgId]: newEntry };
      const nextBookmarks = Object.keys(next);
      saveToStorage("bookmarkData", next);
      saveToStorage("bookmarks", nextBookmarks);
      set({ bookmarkData: next, bookmarks: nextBookmarks });
    }
  },

  removeBookmark: (msgId) => {
    const { bookmarkData } = get();
    const next = { ...bookmarkData };
    delete next[msgId];
    const nextBookmarks = Object.keys(next);
    saveToStorage("bookmarkData", next);
    saveToStorage("bookmarks", nextBookmarks);
    set({ bookmarkData: next, bookmarks: nextBookmarks });
  },

  isBookmarked: (msgId) => !!get().bookmarkData[msgId],

  addBookmarkFolder: (name) => {
    const id = `folder-${Date.now()}`;
    const folder = { id, name, createdAt: Date.now() };
    const next = [...get().bookmarkFolders, folder];
    saveToStorage("bookmarkFolders", next);
    set({ bookmarkFolders: next });
  },

  deleteBookmarkFolder: (folderId) => {
    if (folderId === "default") return;
    const next = get().bookmarkFolders.filter((f) => f.id !== folderId);
    saveToStorage("bookmarkFolders", next);
    set({ bookmarkFolders: next });
  },

  setBookmarkTargetMsgId: (id) => set({ bookmarkTargetMsgId: id }),

  addBookmarkToFolder: (msgId, folderId) => {
    const { bookmarkData, messages, activeSessionId } = get();
    const existing = bookmarkData[msgId];
    if (existing) {
      const next = { ...bookmarkData, [msgId]: { ...existing, folderId } };
      saveToStorage("bookmarkData", next);
      set({ bookmarkData: next });
    } else {
      const msg = messages.find((m) => m.id === msgId);
      const content = msg ? (typeof msg.content === "string" ? msg.content.slice(0, 100) : "") : "";
      const newEntry = {
        folderId,
        bookmarkedAt: Date.now(),
        sessionId: activeSessionId,
        sessionTitle: "当前对话",
        content,
        role: msg ? msg.role : "assistant",
      };
      const next = { ...bookmarkData, [msgId]: newEntry };
      const nextBookmarks = Object.keys(next);
      saveToStorage("bookmarkData", next);
      saveToStorage("bookmarks", nextBookmarks);
      set({ bookmarkData: next, bookmarks: nextBookmarks });
    }
  },

  moveBookmarkToFolder: (bookmarkId, folderId) => {
    const { bookmarkData } = get();
    const existing = bookmarkData[bookmarkId];
    if (!existing) return;
    const next = { ...bookmarkData, [bookmarkId]: { ...existing, folderId } };
    saveToStorage("bookmarkData", next);
    set({ bookmarkData: next });
  },

  renameBookmarkFolder: (folderId, name) => {
    const next = get().bookmarkFolders.map((f) => (f.id === folderId ? { ...f, name } : f));
    saveToStorage("bookmarkFolders", next);
    set({ bookmarkFolders: next });
  },

  exportConversation: (format) => {
    log("info", "chat", `导出对话 (${format})`);
    const { messages } = get();
    let content: string;
    let filename: string;
    let mimeType: string;

    if (format === "markdown") {
      content = messages.map((m) => {
        const role = m.role === "user" ? "**用户**" : "**Assistant**";
        // Handle ContentPart[] content (images/multimodal)
        const text = typeof m.content === "string"
          ? m.content
          : Array.isArray(m.content)
            ? m.content.map((p) => {
                if (p.type === "text") return p.text;
                if (p.type === "image_url") return `![Image](${p.image_url.url})`;
                return "";
              }).join("\n\n")
            : "";
        return `${role}\n\n${text}\n\n---`;
      }).join("\n\n");
      filename = `chat-export-${Date.now()}.md`;
      mimeType = "text/markdown";
    } else {
      content = JSON.stringify(messages, null, 2);
      filename = `chat-export-${Date.now()}.json`;
      mimeType = "application/json";
    }

    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },

  showStats: () => set({ statsOpen: true }),
  hideStats: () => set({ statsOpen: false }),
}));

// 自动持久化 sessionMessages（debounce，避免 SSE 期间频繁写入）
let _persistTimer: ReturnType<typeof setTimeout> | null = null;
let _pendingShards: Map<string, Message[]> = new Map();

function flushPendingShards() {
  for (const [sid, msgs] of _pendingShards) {
    saveSessionMessages(sid, msgs);
  }
  _pendingShards.clear();
  _persistTimer = null;
}

function scheduleShardSave(sessionId: string, msgs: Message[]) {
  _pendingShards.set(sessionId, msgs);
  if (_persistTimer) clearTimeout(_persistTimer);
  _persistTimer = setTimeout(flushPendingShards, 500);
}

useChatStore.subscribe((state, prevState) => {
  if (state.sessionMessages !== prevState.sessionMessages) {
    // 找出变化的 session
    const allKeys = new Set([...Object.keys(state.sessionMessages), ...Object.keys(prevState.sessionMessages)]);
    for (const sid of allKeys) {
      if (state.sessionMessages[sid] !== prevState.sessionMessages[sid]) {
        const msgs = state.sessionMessages[sid];
        if (msgs) {
          if (state.isStreaming) {
            scheduleShardSave(sid, msgs);
          } else {
            saveSessionMessages(sid, msgs);
          }
        }
      }
    }
  }
});

// ─── Mock 数据注入（控制台调用 window.__loadMockData() 测试 token 统计） ───
export function loadMockData() {
  const now = Date.now();
  const mockSessionId = "mock-test-session";

  const mockMessages: Message[] = [
    // 第 1 轮对话：简单问答
    {
      id: "mock-msg-1",
      role: "user",
      content: "STM32F103 的主频是多少？",
      timestamp: now - 3600_000,
    },
    {
      id: "mock-msg-2",
      role: "assistant",
      content: "STM32F103 的主频为 72MHz，基于 ARM Cortex-M3 内核，具有丰富的外设和较高的性价比。",
      timestamp: now - 3590_000,
      usage: { promptTokens: 28, completionTokens: 45, totalTokens: 73 },
      activity: {
        durationMs: 1200,
        steps: [
          { type: "thinking", id: "h-1", content: "正在检索知识库...", source: "rag" },
          { type: "thinking", id: "h-2", content: "STM32F103 主频 72MHz，Cortex-M3 内核", source: "llm" },
        ],
      },
      sources: [
        { id: "src-1", title: "STM32F103 数据手册", doc: "stm32f103-datasheet.pdf", page: 12, score: 0.95, excerpt: "STM32F103 主频 72MHz..." },
      ],
    },
    // 第 2 轮对话：推理模型
    {
      id: "mock-msg-3",
      role: "user",
      content: "帮我分析一下 I2C 通信失败的可能原因，从硬件和软件两个角度分析",
      timestamp: now - 1800_000,
    },
    {
      id: "mock-msg-4",
      role: "assistant",
      content: "## I2C 通信失败原因分析\n\n### 硬件原因\n1. 上拉电阻缺失或阻值不当\n2. 总线信号线短路或断路\n3. 供电电压不稳定\n\n### 软件原因\n1. I2C 地址配置错误\n2. 时序配置不匹配\n3. 中断优先级冲突导致超时",
      timestamp: now - 1780_000,
      usage: { promptTokens: 56, completionTokens: 128, totalTokens: 184 },
      activity: {
        durationMs: 8500,
        steps: [
          { type: "thinking", id: "h-3", content: "正在检索知识库...", source: "rag" },
          { type: "thinking", id: "h-4", content: "知识库中未找到匹配片段，将直接回答。", source: "rag" },
          { type: "thinking", id: "h-5", content: "正在生成回答...", source: "llm" },
          {
            type: "thinking",
            id: "h-6",
            content: "I2C通信失败需要从硬件和软件两方面分析。硬件方面最常见的是上拉电阻问题，标准模式要求4.7kΩ，快速模式要求1kΩ。软件方面地址配置错误是最高频的bug，7位地址需要左移1位...",
            source: "reasoning",
          },
        ],
      },
    },
    // 第 3 轮对话：工具调用
    {
      id: "mock-msg-5",
      role: "user",
      content: "搜索一下 ESP32-S3 的技术文档",
      timestamp: now - 600_000,
    },
    {
      id: "mock-msg-6",
      role: "assistant",
      content: "我为你找到了 ESP32-S3 的相关技术文档：\n\n1. **ESP32-S3 技术参考手册** - 涵盖 Wi-Fi、蓝牙 5.0、双核 Xtensa LX7 处理器等\n2. **ESP32-S3 数据手册** - 引脚定义、电气特性、封装信息",
      timestamp: now - 580_000,
      usage: { promptTokens: 42, completionTokens: 86, totalTokens: 128 },
      activity: {
        durationMs: 3200,
        steps: [
          { type: "thinking", id: "h-7", content: "正在检索知识库...", source: "rag" },
          {
            type: "tool",
            id: "t-1",
            name: "search_docs",
            args: '{"query": "ESP32-S3 技术文档"}',
            result: '{"count": 2, "titles": ["ESP32-S3 技术参考手册", "ESP32-S3 数据手册"]}',
          },
          { type: "thinking", id: "h-8", content: "正在生成回答...", source: "llm" },
        ],
      },
      sources: [
        { id: "src-2", title: "ESP32-S3 技术参考手册", doc: "esp32-s3-technical-reference.pdf", page: 1, score: 0.92, excerpt: "ESP32-S3 搭载 Xtensa LX7 双核处理器..." },
        { id: "src-3", title: "ESP32-S3 数据手册", doc: "esp32-s3-datasheet.pdf", page: 3, score: 0.88, excerpt: "引脚定义与电气特性..." },
      ],
    },
    // 第 4 轮：无 usage 的消息（测试估算 fallback）
    {
      id: "mock-msg-7",
      role: "user",
      content: "GPIO 的输出模式有哪些？",
      timestamp: now - 300_000,
    },
    {
      id: "mock-msg-8",
      role: "assistant",
      content: "GPIO 输出模式主要有：推挽输出、开漏输出、复用推挽输出、复用开漏输出。",
      timestamp: now - 295_000,
      // 故意不带 usage，测试估算逻辑
    },
    // 第 5 轮：今天的消息
    {
      id: "mock-msg-9",
      role: "user",
      content: "串口通信波特率怎么配置？",
      timestamp: now - 60_000,
    },
    {
      id: "mock-msg-10",
      role: "assistant",
      content: "串口波特率配置步骤：\n1. 确定所需的波特率（常用 9600、115200 等）\n2. 计算波特率寄存器值：BRR = fCK / (16 × BaudRate)\n3. 配置 USART_BRR 寄存器\n4. 使能发送/接收",
      timestamp: now - 50_000,
      usage: { promptTokens: 35, completionTokens: 92, totalTokens: 127 },
    },
  ];

  const updatedSM = { ...useChatStore.getState().sessionMessages, [mockSessionId]: mockMessages };
  saveSessionMessages(mockSessionId, mockMessages);

  // 确保会话存在
  const sessionStore = useSessionStore.getState();
  if (!sessionStore.sessions.find((s) => s.id === mockSessionId)) {
    const mockSession: import("../types/session").Session = {
      id: mockSessionId,
      title: "Mock 测试会话",
      preview: "串口通信波特率怎么配置？",
      model: "deepseek-v3",
      createdAt: now - 3600_000,
      project: "default",
      pinned: false,
      msgCount: 5,
    };
    useSessionStore.setState({ sessions: [mockSession, ...sessionStore.sessions] });
    saveToStorage("sessions", [mockSession, ...sessionStore.sessions]);
  }

  useChatStore.setState({
    messages: mockMessages,
    sessionMessages: updatedSM,
    activeSessionId: mockSessionId,
  });

  saveToStorage("activeSession", mockSessionId);
  log("ok", "chat", "Mock 数据已加载，切换到 Mock 测试会话");
}

// 仅在开发环境挂到 window 方便控制台调用
if (typeof window !== "undefined" && import.meta.env.DEV) {
  (window as any).__loadMockData = loadMockData;
  (window as any).__clearMockData = () => {
    const mockSessionId = "mock-test-session";
    const sm = { ...useChatStore.getState().sessionMessages };
    delete sm[mockSessionId];
    removeSessionMessages(mockSessionId);
    useChatStore.setState({ sessionMessages: sm, messages: [], activeSessionId: "s1" });
    saveToStorage("activeSession", "s1");
    log("ok", "chat", "Mock 数据已清除");
  };
}
