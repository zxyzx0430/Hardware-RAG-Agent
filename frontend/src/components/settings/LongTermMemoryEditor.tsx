import { useEffect, useRef, useState } from "react";

const MAX_MEMORY_CHARS = 4000;

interface LongTermMemoryEditorProps {
  value: string;
  lang: string;
  label: string;
  description: string;
  onSave: (value: string) => Promise<boolean>;
}

type SaveFeedback =
  | { type: "saving" }
  | { type: "saved" }
  | { type: "error"; message: string }
  | null;

export function LongTermMemoryEditor({ value, lang, label, description, onSave }: LongTermMemoryEditorProps) {
  const isChinese = lang === "zh";
  const [draft, setDraft] = useState(value);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<SaveFeedback>(null);
  const lastObservedValue = useRef(value);

  useEffect(() => {
    if (value === lastObservedValue.current) return;
    lastObservedValue.current = value;
    if (!dirty) setDraft(value);
  }, [value, dirty]);

  const save = async (submittedValue: string) => {
    if (saving) return;
    setSaving(true);
    setFeedback({ type: "saving" });
    try {
      const saved = await onSave(submittedValue);
      if (saved) {
        setDraft(submittedValue);
        setDirty(false);
        setFeedback({ type: "saved" });
      } else {
        setFeedback({
          type: "error",
          message: isChinese
            ? "保存失败。编辑草稿已保留，上次服务器保存值未更改。"
            : "Save failed. Your draft is still here; the last server value was not changed.",
        });
      }
    } catch {
      setFeedback({
        type: "error",
        message: isChinese
          ? "保存失败。编辑草稿已保留，上次服务器保存值未更改。"
          : "Save failed. Your draft is still here; the last server value was not changed.",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (nextValue: string) => {
    if (Array.from(nextValue).length > MAX_MEMORY_CHARS) {
      setFeedback({
        type: "error",
        message: isChinese ? "长期记忆最多 4,000 个字符。" : "Long-term memory is limited to 4,000 characters.",
      });
      return;
    }
    setDraft(nextValue);
    setDirty(nextValue !== value);
    setFeedback(null);
  };

  const handleClearAndSave = () => {
    setDraft("");
    setDirty(true);
    void save("");
  };

  return (
    <div style={{ marginBottom: 24 }}>
      <label htmlFor="longTermMemory" className="field-label">{label}</label>
      <p id="longTermMemoryDescription" style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 8 }}>
        {description}
      </p>
      <p style={{ fontSize: 12, color: "var(--muted-fg)", marginBottom: 8 }}>
        {isChinese
          ? "记忆会随聊天发送给所选模型服务；请勿保存密钥或敏感凭证。"
          : "Memory is sent with chats to your selected model service. Do not store API keys or sensitive credentials here."}
      </p>
      <textarea
        id="longTermMemory"
        className="settings-textarea"
        rows={5}
        maxLength={MAX_MEMORY_CHARS * 2}
        value={draft}
        disabled={saving}
        aria-describedby="longTermMemoryDescription"
        onChange={(event) => handleChange(event.target.value)}
      />
      <div className="char-count">{Array.from(draft).length} / {MAX_MEMORY_CHARS}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button
          type="button"
          className="verify-btn primary"
          onClick={() => void save(draft)}
          disabled={!dirty || saving}
        >
          {isChinese ? "保存记忆" : "Save memory"}
        </button>
        <button
          type="button"
          className="verify-btn"
          onClick={handleClearAndSave}
          disabled={saving || (!draft && !value)}
        >
          {isChinese ? "清空并保存" : "Clear and save"}
        </button>
      </div>
      {feedback?.type === "saving" && (
        <span className="saved-feedback" role="status" aria-live="polite">
          {isChinese ? "正在保存…" : "Saving…"}
        </span>
      )}
      {feedback?.type === "saved" && (
        <span className="saved-feedback" role="status" aria-live="polite">
          {isChinese ? "✓ 已保存" : "✓ Saved"}
        </span>
      )}
      {feedback?.type === "error" && (
        <span className="saved-feedback" role="alert" aria-live="assertive">
          {feedback.message}
        </span>
      )}
    </div>
  );
}
