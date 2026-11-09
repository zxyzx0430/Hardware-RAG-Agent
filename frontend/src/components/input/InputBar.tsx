import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { useChatStore } from "../../stores/useChatStore";
import { useLogStore } from "../../stores/useLogStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useAppStore } from "../../stores/useAppStore";
import { useToastStore } from "../../stores/useToastStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useSkillsStore } from "../../stores/useSkillsStore";
import { TemplatePanel } from "../shared/TemplatePanel";
import { useI18n } from "../../i18n";
import { buildModelGroups, filterModelGroups } from "../../utils/modelOptions";
import { reasoningModesFor, reasoningLabel, type ReasoningMode } from "../../types/reasoning";
import type { Attachment } from "../../types/api";

type PermissionChoice = "manual" | "auto" | "plan" | "yolo";

interface PermissionOption {
  id: PermissionChoice;
  label: string;
  description: { zh: string; en: string };
}

const PERMISSION_OPTIONS: PermissionOption[] = [
  {
    id: "manual",
    label: "manual",
    description: {
      zh: "每个中高风险操作都先问你",
      en: "Ask before every medium/high-risk action",
    },
  },
  {
    id: "auto",
    label: "auto",
    description: {
      zh: "文件修改自动放行，危险操作仍询问",
      en: "Auto-approve edits; still ask for dangerous actions",
    },
  },
  {
    id: "plan",
    label: "plan",
    description: {
      zh: "只读调研并给出计划，不改文件、不执行命令",
      en: "Read-only research and a plan; no writes or commands",
    },
  },
  {
    id: "yolo",
    label: "YOLO",
    description: {
      zh: "全部放行，不再询问（谨慎使用）",
      en: "Allow everything without asking (use with care)",
    },
  },
];

const MIN_INPUT_HEIGHT = 80;
const MAX_INPUT_HEIGHT = 320;
const DEFAULT_INPUT_HEIGHT = 120;
const INPUT_HEIGHT_KEY = "hwrag_input_height";

function loadInputHeight(): number {
  if (typeof window === "undefined") return DEFAULT_INPUT_HEIGHT;
  const raw = localStorage.getItem(INPUT_HEIGHT_KEY);
  const n = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(n) ? Math.max(MIN_INPUT_HEIGHT, Math.min(MAX_INPUT_HEIGHT, n)) : DEFAULT_INPUT_HEIGHT;
}

function attachmentSignature(attachment: Attachment): string {
  return `${attachment.name}\u0000${attachment.type}\u0000${attachment.content}`;
}

function mergeUniqueAttachments(current: Attachment[], incoming: Attachment[]): Attachment[] {
  const seen = new Set(current.map(attachmentSignature));
  const merged = [...current];
  for (const attachment of incoming) {
    const signature = attachmentSignature(attachment);
    if (seen.has(signature)) continue;
    seen.add(signature);
    merged.push(attachment);
    if (merged.length >= 3) break;
  }
  return merged;
}
export function InputBar() {
  const { t, lang } = useI18n();
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [modelQuery, setModelQuery] = useState("");
  const [showPermissionDropdown, setShowPermissionDropdown] = useState(false);
  const [showReasoningDropdown, setShowReasoningDropdown] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [textareaHeight, setTextareaHeight] = useState(loadInputHeight());
  const textareaHeightRef = useRef(textareaHeight);
  useEffect(() => { textareaHeightRef.current = textareaHeight; }, [textareaHeight]);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);
  const permissionDropdownRef = useRef<HTMLDivElement | null>(null);
  const reasoningDropdownRef = useRef<HTMLDivElement | null>(null);
  const capabilityRefreshAttemptedRef = useRef<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Ref mirror of attachments to read latest value inside async callbacks
  const attachmentsRef = useRef<Attachment[]>([]);
  // Guard against double send (e.g. rapid Enter + click)
  const sendingRef = useRef(false);

  const { sendMessage, stopStreaming, isStreaming, activeSessionId, drafts, setDraft, clearDraft, sessionAttachments, setSessionAttachments, clearSessionAttachments, planMode, setPlanMode, leaveAgentPlanMode } = useChatStore();
  const { providers, chatProviderId, chatModel, setChatModel, permissionMode, updateSetting } = useSettingsStore();
  const fetchProviderModels = useSettingsStore((s) => s.fetchProviderModels);
  const { sessions, updateSessionMeta } = useSessionStore();
  // 输入框文本从 store 草稿派生，切换会话自动响应
  const text = drafts[activeSessionId] || "";
  // 附件按 session 隔离，切换会话时互不干扰
  const attachments = sessionAttachments[activeSessionId] || [];
  // 模型优先从当前会话读取（各对话独立），回退到全局设置
  const currentSession = sessions.find((s) => s.id === activeSessionId);
  const model = currentSession?.model || chatModel;
  const reasoningProvider = providers.find((p) => p.id === chatProviderId);
  const reasoningCapability = reasoningProvider?.modelCapabilities?.[model];
  const reasoningModes = reasoningModesFor(reasoningCapability);
  const requestedReasoningMode = currentSession?.reasoningMode ?? "auto";
  const selectedReasoningMode: ReasoningMode = reasoningModes.includes(requestedReasoningMode)
    ? requestedReasoningMode
    : "auto";
  const reasoningText = reasoningLabel(selectedReasoningMode, lang);
  const reasoningUnknown = reasoningCapability === undefined;
  const reasoningUnsupported = reasoningCapability?.reasoning === false;

  useEffect(() => {
    if (typeof fetchProviderModels !== "function" || !chatProviderId || !model) return;
    const provider = providers.find((item) => item.id === chatProviderId);
    if (!provider?.verified || provider.models.length === 0) return;
    if (Object.keys(provider.modelCapabilities || {}).length > 0) return;
    if (capabilityRefreshAttemptedRef.current.has(chatProviderId)) return;
    capabilityRefreshAttemptedRef.current.add(chatProviderId);
    void fetchProviderModels(chatProviderId);
  }, [chatProviderId, fetchProviderModels, model, providers]);
  const { quotedMsg, setQuotedMsg, templatePanelOpen, setTemplatePanelOpen } = useAppStore();

  // Keep ref in sync with attachments state for reading inside async callbacks
  attachmentsRef.current = attachments;

  // 仅展示已验证的服务商，每个服务商的模型分组渲染
  const verifiedProviders = useMemo(
    () => providers.filter((p) => p.verified && p.models.length > 0),
    [providers],
  );
  // 去重：多个本地路由可能暴露同一批模型，同一模型只显示一行。
  const modelGroups = useMemo(
    () => filterModelGroups(buildModelGroups(verifiedProviders, chatProviderId), modelQuery),
    [verifiedProviders, chatProviderId, modelQuery],
  );

  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number } | null>(null);
  const [permissionDropdownPos, setPermissionDropdownPos] = useState<{ top: number; left: number } | null>(null);
  const [reasoningDropdownPos, setReasoningDropdownPos] = useState<{ top: number; left: number } | null>(null);

  const selectedPermission: PermissionChoice = planMode
    ? "plan"
    : permissionMode === "bypassPermissions" || permissionMode === "yolo"
      ? "yolo"
      : permissionMode === "acceptEdits"
        ? "auto"
        : "manual";
  const permissionLabel = PERMISSION_OPTIONS.find(
    (option) => option.id === selectedPermission,
  )?.label ?? "manual";
  const reasoningTitle = reasoningUnknown
    ? (lang === "zh" ? "该模型未声明思考能力，选择显式模型后可调" : "Reasoning capability unknown; choose an explicit model to control it")
    : reasoningUnsupported
      ? (lang === "zh" ? "该模型不支持思考" : "This model does not support reasoning")
      : reasoningCapability?.effortSupported
        ? (lang === "zh" ? "切换本会话的思考强度" : "Change this session's reasoning effort")
        : (lang === "zh" ? "该模型只支持自动思考" : "This model only supports automatic reasoning");

  const chooseReasoning = (mode: ReasoningMode) => {
    if (activeSessionId) {
      updateSessionMeta(activeSessionId, { reasoningMode: mode });
    }
    setShowReasoningDropdown(false);
  };

  const choosePermission = (choice: PermissionChoice) => {
    if (choice === "plan") {
      // Plan mode is request-scoped; the persisted permission mode stays untouched.
      setPlanMode(true);
      return;
    }
    const applyHigherMode = () => updateSetting(
      "permissionMode",
      choice === "yolo" ? "yolo" : choice === "auto" ? "acceptEdits" : "default",
    );
    if (planMode) {
      // Leaving an Agent-initiated plan downgrade is the user's explicit
      // override: clear the server marker first, or the next request is clamped
      // back to plan. Keep the plan UI if clearing fails.
      void leaveAgentPlanMode().then((cleared) => {
        if (cleared) applyHigherMode();
      });
      return;
    }
    setPlanMode(false);
    applyHigherMode();
  };

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!dropdownRef.current?.contains(e.target as Node)) {
        setShowModelDropdown(false);
      }
      if (!permissionDropdownRef.current?.contains(e.target as Node)) {
        setShowPermissionDropdown(false);
      }
      if (!reasoningDropdownRef.current?.contains(e.target as Node)) {
        setShowReasoningDropdown(false);
      }
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, []);

  // 计算模型下拉菜单的 fixed 定位
  useLayoutEffect(() => {
    if (showModelDropdown && dropdownRef.current) {
      const rect = dropdownRef.current.getBoundingClientRect();
      setDropdownPos({
        top: rect.top - 4, // 向上弹出
        left: rect.left,
      });
    } else {
      setDropdownPos(null);
    }
  }, [showModelDropdown]);

  // 计算权限下拉菜单的 fixed 定位
  useLayoutEffect(() => {
    if (showPermissionDropdown && permissionDropdownRef.current) {
      const rect = permissionDropdownRef.current.getBoundingClientRect();
      setPermissionDropdownPos({
        top: rect.top - 4,
        left: rect.left,
      });
    } else {
      setPermissionDropdownPos(null);
    }
  }, [showPermissionDropdown]);

  // 计算思考强度下拉菜单的 fixed 定位
  useLayoutEffect(() => {
    if (showReasoningDropdown && reasoningDropdownRef.current) {
      const rect = reasoningDropdownRef.current.getBoundingClientRect();
      setReasoningDropdownPos({
        top: rect.top - 4,
        left: rect.left,
      });
    } else {
      setReasoningDropdownPos(null);
    }
  }, [showReasoningDropdown]);

  const handleInsertTemplate = useCallback((content: string) => {
    const prev = drafts[activeSessionId] || "";
    const trimmed = prev.startsWith("/") ? prev.slice(1) : prev;
    setDraft(activeSessionId, trimmed + content);
    setTemplatePanelOpen(false);
  }, [activeSessionId, drafts, setDraft, setTemplatePanelOpen]);

  const handleChange = useCallback((value: string) => {
    setDraft(activeSessionId, value);
    if (value.startsWith("/")) {
      setTemplatePanelOpen(true);
    } else if (!value.startsWith("/") && templatePanelOpen) {
      setTemplatePanelOpen(false);
    }
  }, [activeSessionId, setDraft, templatePanelOpen, setTemplatePanelOpen]);

  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

  const fileToAttachment = useCallback((file: File): Promise<Attachment> => {
    return new Promise((resolve, reject) => {
      if (file.size > MAX_FILE_SIZE) {
        reject(new Error(`文件 ${file.name} 超过 10MB 大小限制`));
        return;
      }
      const ALLOWED_EXTS = new Set(['.pdf','.md','.txt','.csv','.json','.xlsx','.xls','.py','.c','.h','.ino','.docx','.doc','.png','.jpg','.jpeg','.gif','.webp']);
      const ext = '.' + file.name.split('.').pop()?.toLowerCase();
      if (!ext || !ALLOWED_EXTS.has(ext)) {
        reject(new Error(`不支持的文件格式: ${ext}`));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        resolve({
          id: `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          name: file.name,
          type: file.type,
          content: reader.result as string,
        });
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }, []);

  const handleSend = () => {
    if (isStreaming) return;
    if (sendingRef.current) {
      useToastStore.getState().showWarning("正在发送中，请稍候");
      return;
    }  // 防止重入
    if (!text.trim() && attachments.length === 0) return;
    const skillsState = useSkillsStore.getState();
    const skillSelection = skillsState.chatSkillSelections[activeSessionId] ?? { mode: "off" as const, skillIds: [] };
    const enabledSkillIds = new Set(skillsState.skills.filter((skill) => skill.enabled).map((skill) => skill.id));
    const selectedSkillIds = skillSelection.mode === "manual"
      ? [...new Set(skillSelection.skillIds.filter((id) => enabledSkillIds.has(id)))].slice(0, 3)
      : [];
    if (skillSelection.mode === "manual" && selectedSkillIds.length === 0) {
      useToastStore.getState().showWarning(lang === "zh" ? "手动 Skills 模式至少需要一个仍处于启用状态的技能" : "Manual Skills mode needs at least one enabled skill");
      return;
    }
    sendingRef.current = true;
    const attachmentsCopy = attachments.length > 0 ? attachments : undefined;
    const quoted = useAppStore.getState().quotedMsg;  // 读取引用消息（用 getState 避免闭包陈旧值）
    sendMessage(text.trim() || "", attachmentsCopy, quoted ?? undefined, {
      mode: skillSelection.mode,
      skillIds: selectedSkillIds,
    });
    clearDraft(activeSessionId);
    clearSessionAttachments(activeSessionId);
    setAttachError(null);
    setTemplatePanelOpen(false);
    useAppStore.getState().setQuotedMsg(null);  // 发送后清除引用条
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    // Reset guard on next tick so subsequent sends work
    setTimeout(() => { sendingRef.current = false; }, 0);
  };

  const addAttachments = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    setAttachError(null);

    // Read current attachments from ref (avoids stale closure and StrictMode double-invoke issues)
    const current = attachmentsRef.current;
    const remainingSlots = 3 - current.length;
    if (remainingSlots <= 0) {
      setAttachError("最多 3 个文件");
      return;
    }
    const toAdd = files.slice(0, remainingSlots);
    if (files.length > remainingSlots) {
      setAttachError("最多 3 个文件");
    }

    // Process files outside of state updater — no side effects inside setState
    try {
      const newAttachments = await Promise.all(toAdd.map(fileToAttachment));
      const merged = mergeUniqueAttachments(current, newAttachments);
      if (merged.length === current.length) {
        setAttachError("已忽略重复文件");
      }
      setSessionAttachments(activeSessionId, merged);
    } catch (err) {
      setAttachError(err instanceof Error ? err.message : "文件处理失败");
      useLogStore.getState().log("error", "chat", `附件处理失败: ${err}`);
    }
  }, [fileToAttachment, activeSessionId, setSessionAttachments]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const file = item.getAsFile();
      if (file) files.push(file);
    }
    if (files.length > 0) {
      e.preventDefault();
      addAttachments(files);
    }
  }, [addAttachments]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = e.dataTransfer?.files;
    if (!files?.length) return;
    addAttachments(Array.from(files));
  }, [addAttachments]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.relatedTarget && e.currentTarget.contains(e.relatedTarget as Node)) return;
    setIsDragOver(false);
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length) return;
    addAttachments(Array.from(files));
    // 重置 input value 以便再次选择同一文件
    e.target.value = "";
  }, [addAttachments]);

  const removeAttachment = useCallback((id: string) => {
    setSessionAttachments(activeSessionId, attachments.filter((a) => a.id !== id));
    setAttachError(null);
  }, [activeSessionId, attachments, setSessionAttachments]);

  // 输入框高度拖拽
  const handleHeightResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    const startY = e.clientY;
    const startHeight = textareaHeightRef.current;
    const onMove = (ev: MouseEvent) => {
      const delta = startY - ev.clientY;
      const newHeight = Math.max(MIN_INPUT_HEIGHT, Math.min(MAX_INPUT_HEIGHT, startHeight + delta));
      setTextareaHeight(newHeight);
      if (textareaRef.current) {
        textareaRef.current.style.height = `${newHeight}px`;
      }
    };
    const onUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      localStorage.setItem(INPUT_HEIGHT_KEY, String(textareaHeightRef.current));
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, []);

  // ESC 键统一取消行为：优先级 流式 > 引用 > 附件
  const handleEscCancel = useCallback(() => {
    if (isStreaming) {
      stopStreaming();
      return;
    }
    if (quotedMsg) {
      setQuotedMsg(null);
      return;
    }
    if (attachments.length > 0) {
      clearSessionAttachments(activeSessionId);
      setAttachError(null);
    }
  }, [isStreaming, stopStreaming, quotedMsg, setQuotedMsg, attachments, setAttachError, clearSessionAttachments, activeSessionId]);

  return (
    <>
      {templatePanelOpen && (
        <TemplatePanel
          onInsert={handleInsertTemplate}
          currentText={text}
        />
      )}

      <div className={`quote-bar${quotedMsg ? "" : " hidden"}`} id="quoteBar">
        <span className="quote-bar-label">{t('quotedLabel')}</span>
        <span className="quote-bar-text" id="quoteBarText">{typeof quotedMsg?.content === 'string' ? quotedMsg.content.slice(0, 80) : ''}</span>
        <button className="quote-bar-close" title={t('cancelQuote')} onClick={() => setQuotedMsg(null)}>✕</button>
      </div>

      <div className="inputbar">
        <div
          className="input-shell"
          style={{ position: "relative", ...(isDragOver ? { borderColor: "var(--primary)" } : {}) }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDragOver && (
            <div style={{ position: "absolute", bottom: "100%", left: 0, right: 0, background: "var(--primary)", color: "#fff", padding: "4px 8px", borderRadius: "4px 4px 0 0", fontSize: "12px", pointerEvents: "none", textAlign: "center" }}>释放以上传文件</div>
          )}
          <input
            type="file"
            ref={fileInputRef}
            className="hidden"
            accept="image/*,.pdf,.txt,.md,.csv,.json,.xlsx,.xls,.py,.c,.h,.ino"
            multiple
            onChange={handleFileSelect}
          />

          {attachments.length > 0 && (
            <div className="attachment-chips">
              {attachments.map((file) => (
                <span className="attachment-chip" key={file.id}>
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  <span className="attachment-chip-name">{file.name}</span>
                  <button className="attachment-chip-remove" onClick={() => removeAttachment(file.id)}>×</button>
                </span>
              ))}
            </div>
          )}
          {attachError && (
            <div className="attachment-error">{attachError}</div>
          )}

          <div
            className="input-height-resizer"
            onMouseDown={handleHeightResizeStart}
            title="拖拽调整输入框高度"
          />

          <textarea
            ref={textareaRef}
            onPaste={handlePaste}
            className="input-textarea"
            id="inputArea"
            placeholder={t('inputPlaceholderHardware')}
            rows={1}
            value={text}
            style={{ maxHeight: textareaHeight }}
            onChange={(e) => handleChange(e.target.value)}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, textareaHeight) + "px";
            }}
            onKeyDown={(e) => {
              // Ignore Enter while IME is composing (e.g. Chinese input method)
              if (e.nativeEvent.isComposing) return;
              if (e.key === "Escape") {
                e.preventDefault();
                handleEscCancel();
                return;
              }
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
          />

          <div className="input-actions">
            <div className="input-left">
              <button
                className={`input-btn input-btn-labeled${templatePanelOpen ? " active" : ""}`}
                title={t('templateBtn')}
                onClick={() => setTemplatePanelOpen(!templatePanelOpen)}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                <span className="input-btn-label">{t('templateBtn')}</span>
              </button>
              <button className="input-btn input-btn-labeled" title={t('attachBtn')} onClick={() => fileInputRef.current?.click()}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
                <span className="input-btn-label">{t('attachBtn')}</span>
              </button>

              <div className="model-selector" id="modelSelector" ref={dropdownRef} onClick={() => {
                setModelQuery("");
                setShowModelDropdown((v) => !v);
              }}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="4" y="4" width="16" height="16" rx="2" />
                  <path d="M9 9h6v6H9z" />
                  <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
                </svg>
                <span className="model-label" id="modelLabel">{model || "未选择模型"}</span>
                <svg className="model-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </div>

              <div
                className={`reasoning-selector${reasoningUnknown || reasoningUnsupported ? " muted" : ""}`}
                id="reasoningSelector"
                ref={reasoningDropdownRef}
                title={reasoningTitle}
                aria-label={reasoningTitle}
                onClick={() => setShowReasoningDropdown((v) => !v)}
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
                <span className="reasoning-label">思考: {reasoningText}</span>
                <svg className="reasoning-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </div>

              <div
                className="permission-selector"
                id="permissionSelector"
                ref={permissionDropdownRef}
                onClick={() => setShowPermissionDropdown((v) => !v)}
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>
                <span className="permission-label" id="permissionLabel">{permissionLabel}</span>
                <svg className="permission-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </div>
            </div>

            <div className="input-right">
              <span className="enter-hint">{t('enterSend')}</span>
              <button className={`send-btn${!isStreaming && text.trim() ? " ready" : ""}${isStreaming ? " hidden" : ""}`} id="sendBtn" onClick={handleSend} disabled={!text.trim() || isStreaming}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
                {t('send')}
              </button>
              <button className={`stop-btn${isStreaming ? "" : " hidden"}`} id="stopBtn" onClick={() => stopStreaming()}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
                {t('stop')}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* 模型下拉菜单 — Portal 到 body，避免 overflow 裁剪 */}
      {showModelDropdown && dropdownPos && createPortal(
        <div
          className="model-dropdown"
          id="modelDropdown"
          style={{
            position: "fixed",
            bottom: "auto",
            top: "auto",
            left: dropdownPos.left,
            maxHeight: 340,
            overflowY: "auto",
          }}
          ref={(el) => {
            if (el) {
              // 向上弹出：定位到 selector 上方
              const elRect = el.getBoundingClientRect();
              el.style.top = `${Math.max(8, dropdownPos.top - elRect.height - 4)}px`;
            }
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="model-search-wrap">
              <input
                className="model-search-input"
                type="search"
                value={modelQuery}
                onChange={(e) => setModelQuery(e.target.value)}
                placeholder={t('modelSearchPlaceholder')}
                aria-label={t('modelSearchPlaceholder')}
                autoFocus
              />
            </div>
            {verifiedProviders.length === 0 ? (
              <div className="model-empty-hint model-search-empty">
                {t('modelSelectFirst')}
              </div>
            ) : modelGroups.length === 0 ? (
              <div className="model-search-empty">{t('modelNoMatch')}</div>
            ) : (
              modelGroups.map((group) => (
                <div key={group.providerId}>
                  <div className="model-group-header">{group.providerName}</div>
                  {group.models.map((modelId) => (
                    <button
                      key={`${group.providerId}:${modelId}`}
                      className={`model-option${modelId === model && group.providerId === chatProviderId ? " selected" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setChatModel(group.providerId, modelId); // 更新全局默认
                        if (activeSessionId) {
                          updateSessionMeta(activeSessionId, { model: modelId }); // 更新当前会话
                        }
                        setShowModelDropdown(false);
                      }}
                    >
                      {modelId}
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
        </div>,
        document.body,
      )}

      {/* 思考强度下拉菜单 — Portal 到 body，避免 overflow 裁剪 */}
      {showReasoningDropdown && reasoningDropdownPos && createPortal(
        <div
          className="reasoning-dropdown"
          id="reasoningDropdown"
          style={{
            position: "fixed",
            bottom: "auto",
            top: "auto",
            left: reasoningDropdownPos.left,
            maxHeight: Math.min(240, reasoningDropdownPos.top - 8),
            overflowY: "auto",
          }}
          ref={(el) => {
            if (el) {
              const elRect = el.getBoundingClientRect();
              el.style.top = `${reasoningDropdownPos.top - elRect.height - 4}px`;
            }
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {reasoningUnknown ? (
              <div className="reasoning-note">
                {lang === "zh"
                  ? "组合模型未声明思考能力。选择显式模型后可切换强度。"
                  : "This combo model does not declare reasoning capability. Choose an explicit model to control it."}
              </div>
            ) : reasoningUnsupported ? (
              <div className="reasoning-note">
                {lang === "zh" ? "该模型不支持思考。" : "This model does not support reasoning."}
              </div>
            ) : null}
            {reasoningModes.map((mode) => (
              <button
                key={mode}
                className={`reasoning-option${selectedReasoningMode === mode ? " selected" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  chooseReasoning(mode);
                }}
              >
                <span className="reasoning-option-label">{reasoningLabel(mode, lang)}</span>
                <span className="reasoning-option-desc">
                  {mode === "auto"
                    ? (lang === "zh" ? "由模型决定是否思考" : "Let the model decide")
                    : (lang === "zh" ? `请求${reasoningLabel(mode, lang)}思考强度` : `Request ${reasoningLabel(mode, lang)} reasoning effort`)}
                </span>
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}

      {/* 权限下拉菜单 — Portal 到 body，避免 overflow 裁剪 */}
      {showPermissionDropdown && permissionDropdownPos && createPortal(
        <div
          className="permission-dropdown"
          id="permissionDropdown"
          style={{
            position: "fixed",
            bottom: "auto",
            top: "auto",
            left: permissionDropdownPos.left,
            maxHeight: Math.min(240, permissionDropdownPos.top - 8),
            overflowY: "auto",
          }}
          ref={(el) => {
            if (el) {
              const elRect = el.getBoundingClientRect();
              el.style.top = `${permissionDropdownPos.top - elRect.height - 4}px`;
            }
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {PERMISSION_OPTIONS.map((option) => (
              <button
                key={option.id}
                className={`permission-option${selectedPermission === option.id ? " selected" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  choosePermission(option.id);
                  setShowPermissionDropdown(false);
                }}
              >
                <span className="permission-option-label">{option.label}</span>
                <span className="permission-option-desc">
                  {lang === "zh" ? option.description.zh : option.description.en}
                </span>
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
