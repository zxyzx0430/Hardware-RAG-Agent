import { useState } from "react";
import { useI18n } from "../../i18n";
import { CHUNK_METHOD_INFO } from "../../constants/chunkMethodInfo";
import { formatFileSize } from "../../utils/format";

interface Props {
  files: File[];
  defaultMethod: string;
  onConfirm: (method: string) => void;
  onCancel: () => void;
}

export function UploadChunkMethodDialog({ files, defaultMethod, onConfirm, onCancel }: Props) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string>(defaultMethod || "hybrid");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleExpanded = (method: string) => {
    setExpanded((prev) => ({ ...prev, [method]: !prev[method] }));
  };

  const methods = ["hybrid", "agent", "multimodal"];

  return (
    <div
      className="modal-overlay"
      onClick={onCancel}
      style={{
        position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
        background: "rgba(0,0,0,0.5)", zIndex: 1000,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        className="modal-content"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg)", borderRadius: 8, padding: 0,
          width: "90%", maxWidth: 640, maxHeight: "85vh", overflow: "auto",
          border: "1px solid var(--border)",
          boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "16px 20px", borderBottom: "1px solid var(--border)",
          position: "sticky", top: 0, background: "var(--bg)", zIndex: 1,
        }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
            {t('selectChunkMethodForUpload')}
          </h2>
          <button
            onClick={onCancel}
            style={{
              fontSize: 18, color: "var(--fg)", cursor: "pointer",
              width: 32, height: 32, flexShrink: 0,
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              borderRadius: 6, border: "1px solid var(--border)", background: "var(--card)",
            }}
          >
            ✕
          </button>
        </div>

        <div style={{ padding: "16px 20px" }}>
          {/* File summary */}
          <div style={{
            marginBottom: 16, padding: 10, borderRadius: 6,
            background: "var(--thinking-bg)", border: "1px solid var(--border)",
            fontSize: 12, color: "var(--fg)",
          }}>
            <div style={{ fontWeight: 600, marginBottom: 6 }}>
              {t('uploadFileCount').replace("{n}", String(files.length))}
            </div>
            <div style={{
              display: "flex", flexDirection: "column", gap: 3,
              maxHeight: 96, overflowY: "auto",
            }}>
              {files.map((file, idx) => (
                <div key={idx} style={{ color: "var(--muted-fg)" }}>
                  {file.name} <span style={{ fontSize: 11 }}>({formatFileSize(file.size)})</span>
                </div>
              ))}
            </div>
          </div>

          {/* Method cards */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {methods.map((method) => {
              const info = CHUNK_METHOD_INFO[method];
              const isSelected = selected === method;
              const isExpanded = expanded[method];
              return (
                <div
                  key={method}
                  onClick={() => setSelected(method)}
                  style={{
                    borderRadius: 8,
                    border: `2px solid ${isSelected ? "var(--primary)" : "var(--border)"}`,
                    background: isSelected ? "var(--accent)" : "var(--card)",
                    padding: 12,
                    cursor: "pointer",
                    transition: "0.15s",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div style={{
                      width: 18, height: 18, borderRadius: "50%",
                      border: `2px solid ${isSelected ? "var(--primary)" : "var(--border)"}`,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      flexShrink: 0,
                    }}>
                      {isSelected && <div style={{
                        width: 10, height: 10, borderRadius: "50%", background: "var(--primary)",
                      }} />}
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 14 }}>
                        {method === "agent" ? t('agent') : method === "multimodal" ? t('multimodal') : t('hybrid')}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--muted-fg)", marginTop: 2 }}>
                        {info.useCase}
                      </div>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleExpanded(method); }}
                      style={{
                        fontSize: 11, color: "var(--primary)",
                        background: "transparent", border: "none", cursor: "pointer",
                      }}
                    >
                      {isExpanded ? t('collapse') : t('expand')}
                    </button>
                  </div>

                  {isExpanded && (
                    <div style={{
                      marginTop: 10, paddingTop: 10,
                      borderTop: "1px solid var(--border)",
                      fontSize: 12, lineHeight: 1.6,
                    }}>
                      <div style={{ marginBottom: 8 }}>
                        <span style={{ fontWeight: 600, color: "var(--primary)" }}>{t('principle')}：</span>
                        <span style={{ color: "var(--fg)" }}>{info.principle}</span>
                      </div>
                      <div style={{ marginBottom: 8 }}>
                        <div style={{ fontWeight: 600, color: "var(--success)" }}>✓ {t('pros')}</div>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 20, color: "var(--fg)" }}>
                          {info.pros.map((p, i) => <li key={i}>{p}</li>)}
                        </ul>
                      </div>
                      <div style={{ marginBottom: 8 }}>
                        <div style={{ fontWeight: 600, color: "var(--warn)" }}>✗ {t('cons')}</div>
                        <ul style={{ margin: "4px 0 0", paddingLeft: 20, color: "var(--fg)" }}>
                          {info.cons.map((c, i) => <li key={i}>{c}</li>)}
                        </ul>
                      </div>
                      <div>
                        <span style={{ fontWeight: 600, color: "var(--purple)" }}>{t('useCase')}：</span>
                        <span style={{ color: "var(--fg)" }}>{info.useCase}</span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Actions */}
          <div style={{ display: "flex", gap: 10, marginTop: 18, justifyContent: "flex-end" }}>
            <button className="kb-item-icon-btn" onClick={onCancel}>
              {t('cancel')}
            </button>
            <button className="btn-new" onClick={() => onConfirm(selected)}>
              {t('confirmUpload')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
