import { useCallback, useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { apiPost } from "../../api/client";
import { useChatStore } from "../../stores/useChatStore";
import { useLogStore } from "../../stores/useLogStore";
import { useSettingsStore, baseUrlByProvider } from "../../stores/useSettingsStore";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { TemplatePanel } from "../shared/TemplatePanel";
import { mdToHtml } from "../../utils/markdown";
import DOMPurify from "dompurify";
import { useI18n } from "../../i18n";
import type { Attachment } from "../../types/api";

const FALLBACK_MODELS = [
  { id: "gpt-4o", label: "GPT-4o", provider: "OpenAI" },
  { id: "claude-3-5-sonnet", label: "Claude 3.5 Sonnet", provider: "Anthropic" },
  { id: "deepseek-v3", label: "DeepSeek V3", provider: "DeepSeek" },
];

type ModelItem = { id: string; label: string; provider: string };

const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  deepseek: 'DeepSeek',
  gemini: 'Google Gemini',
  xai: 'xAI (Grok)',
  mistral: 'Mistral AI',
  groq: 'Groq',
  together: 'Together AI',
  perplexity: 'Perplexity',
  fireworks: 'Fireworks AI',
  cohere: 'Cohere',
  glm: 'Zhipu GLM',
  kimi: 'Kimi (Moonshot)',
  qwen: 'Alibaba Qwen',
  baichuan: 'Baichuan AI',
  step: 'StepFun Step',
  siliconflow: 'SiliconFlow',
  doubao: 'Doubao (ByteDance)',
  azure: 'Azure OpenAI',
  ollama: 'Ollama (Local)',
};

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
  const [text, setText] = useState("");
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // Ref mirror of attachments to read latest value inside async callbacks
  const attachmentsRef = useRef<Attachment[]>([]);
  // Guard against double send (e.g. rapid Enter + click)
  const sendingRef = useRef(false);

  // Model cache per provider
  const [modelCache, setModelCache] = useState<Record<string, ModelItem[]>>({});

  const { sendMessage, stopStreaming, isStreaming } = useChatStore();
  const { setModel, activeProvider, baseUrls, providerKeys } = useSettingsStore();
  const { sessions, updateSessionMeta } = useSessionStore();
  const { activeSessionId } = useChatStore();
  // 模型优先从当前会话读取（各对话独立），回退到全局设置
  const currentSession = sessions.find((s) => s.id === activeSessionId);
  const model = currentSession?.model || useSettingsStore.getState().model;
  const { quotedMsg, setQuotedMsg, templatePanelOpen, setTemplatePanelOpen } = useAppStore();

  const resolvedBaseUrl = baseUrls[activeProvider] || baseUrlByProvider(activeProvider);

  // Keep ref in sync with attachments state for reading inside async callbacks
  attachmentsRef.current = attachments;

  // Fetch models for the current provider（只走后端代理，避免 CORS）
  const { data: fetchedModels } = useQuery<ModelItem[]>({
    queryKey: ["models", resolvedBaseUrl, activeProvider, providerKeys[activeProvider]],
    queryFn: async () => {
      try {
        const res = await apiPost<{ models: string[] }>("models", { base_url: resolvedBaseUrl });
        if (res.models?.length) {
          return res.models.map((modelId) => ({ id: modelId, label: modelId, provider: activeProvider }));
        }
        return [];
      } catch {
        return [];  // Graceful fallback: API failure returns empty array instead of throwing
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: 1,
    enabled: !!providerKeys[activeProvider]?.trim(),
  });

  // Update model cache when models are fetched
  useEffect(() => {
    if (fetchedModels && fetchedModels.length > 0) {
      setModelCache((prev) => ({
        ...prev,
        [activeProvider]: fetchedModels,
      }));
    }
  }, [fetchedModels, activeProvider]);

  // Merge cached models: current provider first, then others
  const models = useMemo(() => {
    const cached = modelCache;
    const allProviders = Object.keys(cached);
    // Current provider first
    const ordered = [activeProvider, ...allProviders.filter((p) => p !== activeProvider)];
    const result: ModelItem[] = [];
    for (const provider of ordered) {
      if (cached[provider]) {
        result.push(...cached[provider]);
      }
    }
    return result.length > 0 ? result : FALLBACK_MODELS;
  }, [modelCache, activeProvider]);

  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!dropdownRef.current?.contains(e.target as Node)) {
        setShowModelDropdown(false);
      }
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, []);

  // 计算下拉菜单的 fixed 定位
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

  const handleInsertTemplate = useCallback((content: string) => {
    setText((prev) => {
      const trimmed = prev.startsWith("/") ? prev.slice(1) : prev;
      return trimmed + content;
    });
    setTemplatePanelOpen(false);
  }, [setTemplatePanelOpen]);

  const handleChange = useCallback((value: string) => {
    setText(value);
    if (value.startsWith("/")) {
      setTemplatePanelOpen(true);
    } else if (!value.startsWith("/") && templatePanelOpen) {
      setTemplatePanelOpen(false);
    }
  }, [templatePanelOpen, setTemplatePanelOpen]);

  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

  const fileToAttachment = useCallback((file: File): Promise<Attachment> => {
    return new Promise((resolve, reject) => {
      if (file.size > MAX_FILE_SIZE) {
        reject(new Error(`文件 ${file.name} 超过 10MB 大小限制`));
        return;
      }
      const ALLOWED_EXTS = new Set(['.pdf','.md','.txt','.csv','.json','.xlsx','.xls','.py','.c','.h','.ino','.png','.jpg','.jpeg','.gif','.webp']);
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
    sendMessage(text.trim() || "", attachmentsCopy);
    setText("");
    setAttachments([]);
    setAttachError(null);
    setTemplatePanelOpen(false);
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
    const files = e.dataTransfer?.files;
    if (!files?.length) return;
    addAttachments(Array.from(files));
  }, [addAttachments]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
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

  const previewHtml = useMemo(() => mdToHtml(text), [text]);

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
        <div className="input-shell">
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
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
          />

          {showPreview && (
            <div
              className="input-preview"
              style={{
                maxHeight: 200,
                overflowY: "auto",
                borderTop: "1px solid var(--border, #e0e0e0)",
                padding: "8px 12px",
                fontSize: "var(--chat-fs, 14px)",
                lineHeight: 1.6,
              }}
            >
              {text.trim() ? (
                <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(previewHtml) }} />
              ) : (
                <span style={{ color: "var(--text-muted, #999)" }}>{t('mdPreviewHint')}</span>
              )}
            </div>
          )}

          <div className="input-actions">
            <div className="input-left">
              <button
                className={`input-btn${templatePanelOpen ? " active" : ""}`}
                title={t('templateBtn')}
                onClick={() => setTemplatePanelOpen(!templatePanelOpen)}
              >
                📋
              </button>
              <button className="input-btn" title={t('attachBtn')} onClick={() => fileInputRef.current?.click()}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>

              <button
                className={`input-btn${showPreview ? " active" : ""}`}
                title={t('mdPreviewBtn')}
                onClick={() => setShowPreview((v) => !v)}
                style={showPreview ? { backgroundColor: "var(--accent-bg, rgba(0,0,0,0.08))" } : undefined}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              </button>

              <div className="model-selector" id="modelSelector" ref={dropdownRef} onClick={() => setShowModelDropdown((v) => !v)}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="4" y="4" width="16" height="16" rx="2" />
                  <path d="M9 9h6v6H9z" />
                  <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
                </svg>
                <span className="model-label" id="modelLabel">{model}</span>
                <svg className="model-chevron" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
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
          {Array.from(new Set(models.map((m) => m.provider))).map((provider) => (
            <div key={provider}>
              <div className="model-group-header">{PROVIDER_DISPLAY_NAMES[provider] || provider}</div>
              {models.filter((m) => m.provider === provider).map((m) => (
                <button
                  key={m.id}
                  className={`model-option${m.id === model ? " selected" : ""}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setModel(m.id); // 更新全局默认
                    if (activeSessionId) {
                      updateSessionMeta(activeSessionId, { model: m.id }); // 更新当前会话
                    }
                    setShowModelDropdown(false);
                  }}
                >
                  {m.label}
                </button>
              ))}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}
