import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { useChatStore } from "../../stores/useChatStore";
import { useLogStore } from "../../stores/useLogStore";
import { useSettingsStore } from "../../stores/useSettingsStore";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { TemplatePanel } from "../shared/TemplatePanel";
import { useI18n } from "../../i18n";
import type { Attachment } from "../../types/api";

type PermissionMode = "bypassPermissions" | "default" | "acceptEdits";
const PERMISSION_MODE_ORDER: PermissionMode[] = ["bypassPermissions", "default", "acceptEdits"];

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
  const { t } = useI18n();
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [showPermissionDropdown, setShowPermissionDropdown] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);
  const permissionDropdownRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Ref mirror of attachments to read latest value inside async callbacks
  const attachmentsRef = useRef<Attachment[]>([]);
  // Guard against double send (e.g. rapid Enter + click)
  const sendingRef = useRef(false);

  const { sendMessage, stopStreaming, isStreaming } = useChatStore();
  const { providers, chatProviderId, chatModel, setChatModel, permissionMode, updateSetting } = useSettingsStore();
  const { sessions, updateSessionMeta } = useSessionStore();
  const { activeSessionId, drafts, setDraft, clearDraft } = useChatStore();
  // 输入框文本从 store 草稿派生，切换会话自动响应
  const text = drafts[activeSessionId] || "";
  // 模型优先从当前会话读取（各对话独立），回退到全局设置
  const currentSession = sessions.find((s) => s.id === activeSessionId);
  const model = currentSession?.model || chatModel;
  const { quotedMsg, setQuotedMsg, templatePanelOpen, setTemplatePanelOpen } = useAppStore();

  // Keep ref in sync with attachments state for reading inside async callbacks
  attachmentsRef.current = attachments;

  // 仅展示已验证的服务商，每个服务商的模型分组渲染
  const verifiedProviders = useMemo(
    () => providers.filter((p) => p.verified && p.models.length > 0),
    [providers],
  );

  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number } | null>(null);
  const [permissionDropdownPos, setPermissionDropdownPos] = useState<{ top: number; left: number } | null>(null);

  const permissionLabels: Record<PermissionMode, string> = {
    bypassPermissions: t('permissionBypass'),
    default: t('permissionAsk'),
    acceptEdits: t('permissionAuto'),
  };

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!dropdownRef.current?.contains(e.target as Node)) {
        setShowModelDropdown(false);
      }
      if (!permissionDropdownRef.current?.contains(e.target as Node)) {
        setShowPermissionDropdown(false);
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
    if (sendingRef.current) return;  // 防止重入
    if (!text.trim() && attachments.length === 0) return;
    sendingRef.current = true;
    const attachmentsCopy = attachments.length > 0 ? attachments : undefined;
    const quoted = useAppStore.getState().quotedMsg;  // 读取引用消息（用 getState 避免闭包陈旧值）
    sendMessage(text.trim() || "", attachmentsCopy, quoted ?? undefined);
    clearDraft(activeSessionId);
    setAttachments([]);
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

    // Process files outside of state updater — no side effects inside setAttachments
    try {
      const newAttachments = await Promise.all(toAdd.map(fileToAttachment));
      setAttachments((prev) => {
        const merged = mergeUniqueAttachments(prev, newAttachments);
        if (merged.length === prev.length) {
          setAttachError("已忽略重复文件");
        }
        return merged;
      });
    } catch (err) {
      setAttachError(err instanceof Error ? err.message : "文件处理失败");
      useLogStore.getState().log("error", "chat", `附件处理失败: ${err}`);
    }
  }, [fileToAttachment]);

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
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    setAttachError(null);
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
      setAttachments([]);
      setAttachError(null);
    }
  }, [isStreaming, stopStreaming, quotedMsg, setQuotedMsg, attachments, setAttachError]);

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

          <textarea
            ref={textareaRef}
            className="input-textarea"
            id="inputArea"
            placeholder={t('inputPlaceholderHardware')}
            rows={1}
            value={text}
            onChange={(e) => handleChange(e.target.value)}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 160) + "px";
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
                className={`input-btn${templatePanelOpen ? " active" : ""}`}
                title={t('templateBtn')}
                onClick={() => setTemplatePanelOpen(!templatePanelOpen)}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </button>
              <button className="input-btn" title={t('attachBtn')} onClick={() => fileInputRef.current?.click()}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>

              <div className="model-selector" id="modelSelector" ref={dropdownRef} onClick={() => setShowModelDropdown((v) => !v)}>
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
                className="permission-selector"
                id="permissionSelector"
                ref={permissionDropdownRef}
                onClick={() => setShowPermissionDropdown((v) => !v)}
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>
                <span className="permission-label" id="permissionLabel">{permissionLabels[permissionMode]}</span>
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
            maxHeight: Math.min(360, dropdownPos.top - 8),
            overflowY: "auto",
          }}
          ref={(el) => {
            if (el) {
              // 向上弹出：定位到 selector 上方
              const elRect = el.getBoundingClientRect();
              el.style.top = `${dropdownPos.top - elRect.height - 4}px`;
            }
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {verifiedProviders.length === 0 ? (
              <div
                className="model-empty-hint"
                style={{
                  padding: "12px 16px",
                  fontSize: 12,
                  color: "var(--text-muted, #999)",
                  textAlign: "center",
                }}
              >
                请先在设置中配置并验证服务商
              </div>
            ) : (
              verifiedProviders.map((provider) => (
                <div key={provider.id}>
                  <div className="model-group-header">{provider.name}</div>
                  {provider.models.map((modelId) => (
                    <button
                      key={`${provider.id}:${modelId}`}
                      className={`model-option${modelId === model && provider.id === chatProviderId ? " selected" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setChatModel(provider.id, modelId); // 更新全局默认
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
            {PERMISSION_MODE_ORDER.map((mode) => (
              <button
                key={mode}
                className={`permission-option${permissionMode === mode ? " selected" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  updateSetting("permissionMode", mode);
                  setShowPermissionDropdown(false);
                }}
              >
                {permissionLabels[mode]}
              </button>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
