import { useState, useEffect, useCallback } from "react";

interface Template {
  name: string;
  content: string;
}

const STORAGE_KEY = "hwrag_templates";

const DEFAULT_TEMPLATES: Template[] = [
  { name: '代码审查', content: '请审查以下代码，关注安全性、性能和最佳实践：\n\n```\n{{code}}\n```' },
  { name: '问题分析', content: '分析以下硬件问题的可能原因和解决方案：\n\n{{description}}' },
  { name: '知识检索', content: '在知识库中检索关于 {{topic}} 的相关文档和规范' },
  { name: '总结', content: '请总结以下内容的关键要点：\n\n{{content}}' },
];

function loadTemplates(): Template[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [...DEFAULT_TEMPLATES];
    const parsed: Template[] = JSON.parse(raw);
    return parsed.length > 0 ? parsed : [...DEFAULT_TEMPLATES];
  } catch {
    return [...DEFAULT_TEMPLATES];
  }
}

function saveTemplates(templates: Template[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(templates));
  } catch {
    // storage full or unavailable
  }
}

interface TemplatePanelProps {
  onInsert: (content: string) => void;
  currentText: string;
}

export function TemplatePanel({ onInsert, currentText }: TemplatePanelProps) {
  const [templates, setTemplates] = useState<Template[]>(loadTemplates);
  const [saving, setSaving] = useState(false);
  const [templateName, setTemplateName] = useState("");

  useEffect(() => {
    saveTemplates(templates);
  }, [templates]);

  const handleSave = useCallback(() => {
    if (!currentText.trim()) return;
    if (saving) {
      // confirm name and save
      if (!templateName.trim()) return;
      setTemplates((prev) => [...prev, { name: templateName.trim(), content: currentText.trim() }]);
      setTemplateName("");
      setSaving(false);
    } else {
      setSaving(true);
      setTemplateName(currentText.trim().slice(0, 12));
    }
  }, [currentText, saving, templateName]);

  const handleDelete = useCallback((idx: number) => {
    setTemplates((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const handleCancelSave = useCallback(() => {
    setSaving(false);
    setTemplateName("");
  }, []);

  if (templates.length === 0 && !saving) {
    return (
      <div className="template-panel">
        <span className="template-panel-empty">暂无模板</span>
        <button className="template-save-btn" onClick={handleSave} disabled={!currentText.trim()}>
          + 保存为模板
        </button>
      </div>
    );
  }

  return (
    <div className="template-panel">
      <div className="template-pills">
        {templates.map((t, i) => (
          <button
            key={i}
            className="template-pill"
            onClick={() => onInsert(t.content)}
            title={t.content}
          >
            <span className="template-pill-name">{t.name}</span>
            <span
              className="template-pill-delete"
              onClick={(e) => {
                e.stopPropagation();
                handleDelete(i);
              }}
            >
              ✕
            </span>
          </button>
        ))}
      </div>
      {saving ? (
        <div className="template-save-inline">
          <input
            className="template-name-input"
            value={templateName}
            onChange={(e) => setTemplateName(e.target.value)}
            placeholder="模板名称"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave();
              if (e.key === "Escape") handleCancelSave();
            }}
          />
          <button className="template-confirm-btn" onClick={handleSave}>保存</button>
          <button className="template-cancel-btn" onClick={handleCancelSave}>取消</button>
        </div>
      ) : (
        <button className="template-save-btn" onClick={handleSave} disabled={!currentText.trim()}>
          + 保存为模板
        </button>
      )}
    </div>
  );
}
