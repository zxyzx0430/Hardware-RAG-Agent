import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

/** 共享的图片灯箱（用户上传图 + agent 生成图共用）。
 *  用 Portal 渲染到 document.body，脱离 .msg-row 的 contain:content（隐含 contain:layout），
 *  否则 position:fixed 会被 .msg-row 的 layout containment 钳制为相对 .msg-row 而非 viewport。 */
export function ImageLightbox({ src, onClose }: { src: string; onClose: () => void }) {
  const [loaded, setLoaded] = useState(false);

  // 全局 ESC 监听：div 即使 tabIndex={-1} 也需要点击才能聚焦，用 window 监听更可靠
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="图片预览"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 9999,
        background: "rgba(0,0,0,0.85)",
        display: "flex", alignItems: "center", justifyContent: "center",
        cursor: "zoom-out",
      }}
    >
      {!loaded && <div style={{ color: "#fff" }}>加载中...</div>}
      <img
        src={src}
        alt="original"
        onLoad={() => setLoaded(true)}
        style={{ maxWidth: "95vw", maxHeight: "95vh", objectFit: "contain", display: loaded ? "block" : "none" }}
      />
    </div>,
    document.body
  );
}
