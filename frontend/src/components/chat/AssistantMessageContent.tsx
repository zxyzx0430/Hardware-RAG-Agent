import { memo, useState, useCallback } from "react";
import type { ContentPart, SourceRef } from "../../types/session";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";
import { ImageLightbox } from "./ImageLightbox";

/** Image with loading / error states */
function StatefulImage({ src, alt, onClick, style }: { src: string; alt: string; onClick: () => void; style: React.CSSProperties }) {
  const [state, setState] = useState<"loading" | "loaded" | "error">("loading");
  const [retryKey, setRetryKey] = useState(0);

  const handleError = useCallback(() => setState("error"), []);
  const handleLoad = useCallback(() => setState("loaded"), []);
  const handleRetry = useCallback(() => { setState("loading"); setRetryKey((k) => k + 1); }, []);

  if (state === "error") {
    return (
      <div style={{ ...style, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, background: "var(--hover-bg)", cursor: "pointer" }} onClick={handleRetry}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
        <span style={{ fontSize: 12, color: "var(--muted-fg)" }}>图片加载失败，点击重试</span>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      {state === "loading" && (
        <div style={{ ...style, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--hover-bg)" }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--muted-fg)" strokeWidth="2" style={{ animation: "spin 1s linear infinite" }}>
            <line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/>
            <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/>
            <line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/>
            <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>
          </svg>
        </div>
      )}
      <img
        key={retryKey}
        src={src}
        alt={alt}
        onLoad={handleLoad}
        onError={handleError}
        onClick={onClick}
        style={{ ...style, display: state === "loaded" ? "block" : "none" }}
      />
    </div>
  );
}

/** 渲染 assistant 消息内容：文本走 MarkdownRenderer（保留 src 引用等能力），
 *  图片直接用 <img> 避免 1.2MB+ base64 data URI 走 markdown 解析时 src 丢失。
 *  纯字符串消息保持原 renderContent 路径不变。 */
export const AssistantMessageContent = memo(function AssistantMessageContent({
  content,
  streaming,
  sources,
  onSourceClick,
  onPushCodeToPreview,
  onOpenInEditor,
}: {
  content: string | ContentPart[];
  streaming: boolean;
  sources?: SourceRef[];
  onSourceClick?: (id: string) => void;
  onPushCodeToPreview?: (code: string, label: string, language: string) => void;
  onOpenInEditor?: (code: string, language: string) => void;
}) {
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null);
  if (typeof content === "string") {
    return (
      <MarkdownRenderer
        content={content}
        streaming={streaming}
        enableSourceRef
        sources={sources}
        onSourceClick={onSourceClick}
        onPushCodeToPreview={onPushCodeToPreview}
        onOpenInEditor={onOpenInEditor}
      />
    );
  }
  const textParts = content
    .filter((p) => p.type === "text")
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("\n\n");
  const imageParts = content.filter((p) => p.type === "image_url");
  return (
    <>
      {textParts.trim() && (
        <MarkdownRenderer
          content={textParts}
          streaming={streaming}
          enableSourceRef
          sources={sources}
          onSourceClick={onSourceClick}
          onPushCodeToPreview={onPushCodeToPreview}
          onOpenInEditor={onOpenInEditor}
        />
      )}
      {imageParts.map((p, idx) => {
        const imgPart = p as Extract<ContentPart, { type: "image_url" }>;
        return (
          <div
            key={idx}
            className="assistant-image-wrap"
            style={{ marginTop: 8, cursor: "zoom-in", display: "inline-block" }}
          >
            <StatefulImage
              src={imgPart.image_url.url}
              alt="generated"
              onClick={() => setLightboxSrc(imgPart.image_url.url)}
              style={{
                maxWidth: 420,
                maxHeight: 420,
                borderRadius: 8,
                border: "1px solid var(--border)",
                display: "block",
              }}
            />
          </div>
        );
      })}
      {lightboxSrc && <ImageLightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}
    </>
  );
});
