import { useCallback, useRef, useState } from "react";
import { Modal } from "../shared/Modal";
import { useI18n } from "../../i18n";
import { apiPost } from "../../api/client";
import { useToastStore } from "../../stores/useToastStore";
import type { OpenFileItem } from "../../types";

interface CloseConfirmDialogProps {
  fileName: string;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}

export function CloseConfirmDialog({
  fileName,
  onSave,
  onDiscard,
  onCancel,
}: CloseConfirmDialogProps) {
  const { t } = useI18n();
  const msg = t("unsavedChangesMessage", '"{name}" 有未保存的更改，关闭前是否保存？"').replace(
    "{name}",
    fileName,
  );
  return (
    <Modal onClose={onCancel} closeOnBackdrop>
      <div className="editor-close-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="editor-close-title">{t("unsavedChanges", "未保存的更改")}</div>
        <div className="editor-close-message">{msg}</div>
        <div className="editor-close-actions">
          <button className="editor-close-btn secondary" onClick={onCancel}>
            {t("cancel", "取消")}
          </button>
          <button className="editor-close-btn secondary" onClick={onDiscard}>
            {t("dontSave", "不保存")}
          </button>
          <button className="editor-close-btn primary" onClick={onSave}>
            {t("save", "保存")}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export interface EditorTabItemProps {
  file: OpenFileItem;
  isActive: boolean;
  isPinned: boolean;
  hasChanges: boolean;
  rootPath?: string;
  isDragging?: boolean;
  onClick: (id: string) => void;
  onContextMenu: (e: React.MouseEvent, id: string) => void;
  onClose: (id: string) => void;
  onTogglePin: (id: string) => void;
  onDiff: (id: string) => void;
  onDragStart: (id: string) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDrop: (id: string) => void;
}

function tabTooltip(file: OpenFileItem, rootPath?: string): string {
  const rel = rootPath ? file.path.replace(rootPath.replace(/\\/g, "/"), "").replace(/^\//, "") : "";
  const absolute = file.path;
  if (rel && rel !== absolute) return `${rel}\n${absolute}`;
  return absolute;
}

export function EditorTabItem({
  file, isActive, isPinned, hasChanges, rootPath, isDragging,
  onClick, onContextMenu, onClose, onTogglePin, onDiff,
  onDragStart, onDragOver, onDrop,
}: EditorTabItemProps) {
  const { t } = useI18n();
  const stop = (e: React.MouseEvent) => e.stopPropagation();
  return (
    <div
      className={`editor-tab${isActive ? " active" : ""}${isPinned ? " pinned" : ""}${isDragging ? " dragging" : ""}`}
      draggable
      onDragStart={() => onDragStart(file.id)}
      onDragOver={onDragOver}
      onDrop={() => onDrop(file.id)}
      onClick={() => onClick(file.id)}
      onContextMenu={(e) => onContextMenu(e, file.id)}
      title={tabTooltip(file, rootPath)}
    >
      <button
        className="editor-tab-pin"
        onClick={(e) => { stop(e); onTogglePin(file.id); }}
        title={isPinned ? t("unpin", "取消置顶") : t("pin", "置顶")}
      >
        {isPinned ? "📌" : "📍"}
      </button>
      <span className="editor-tab-name">{file.name}</span>
      {file.dirty && <span className="editor-tab-dirty">*</span>}
      {hasChanges && (
        <span
          className="editor-tab-changed"
          onClick={(e) => { stop(e); onDiff(file.id); }}
          title={t("fileChanged", "文件已变更")}
        />
      )}
      <button
        className="editor-tab-close"
        onClick={(e) => { stop(e); onClose(file.id); }}
        title={t("close", "关闭")}
      >
        ×
      </button>
    </div>
  );
}

const MIN_SCALE = 0.1;
const MAX_SCALE = 10;
const WHEEL_ZOOM_STEP = 0.15;

const BINARY_MIME_TYPES: Record<string, string> = {
  bin: "application/octet-stream",
  hex: "application/octet-stream",
  uf2: "application/octet-stream",
  img: "application/octet-stream",
  elf: "application/x-elf",
  so: "application/x-sharedlib",
  wasm: "application/wasm",
  pdf: "application/pdf",
  zip: "application/zip",
  gz: "application/gzip",
  tar: "application/x-tar",
  "7z": "application/x-7z-compressed",
  rar: "application/vnd.rar",
  exe: "application/vnd.microsoft.portable-executable",
  dll: "application/vnd.microsoft.portable-executable",
  wav: "audio/wav",
  mp3: "audio/mpeg",
  mp4: "video/mp4",
};

function fileMimeType(file: OpenFileItem): string {
  if (file.mime) return file.mime;
  const extension = file.name.includes(".")
    ? file.name.split(".").pop()?.toLowerCase() ?? ""
    : "";
  return BINARY_MIME_TYPES[extension] ?? "application/octet-stream";
}

function formatFileSize(bytes: number | undefined): string | null {
  if (bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  const kilobytes = bytes / 1024;
  if (kilobytes < 1024) return `${kilobytes.toFixed(1)} KB`;
  const megabytes = kilobytes / 1024;
  if (megabytes < 1024) return `${megabytes.toFixed(1)} MB`;
  return `${(megabytes / 1024).toFixed(1)} GB`;
}

export function ImagePreview({ file }: { file: OpenFileItem }) {
  const { t, lang } = useI18n();
  const [scale, setScale] = useState(1);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleReveal = useCallback(async () => {
    try {
      await apiPost("explorer/reveal", { path: file.path });
    } catch (err) {
      useToastStore.getState().showError(
        `${t("revealFailed", "在文件管理器中显示失败")}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }, [file.path, t]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setScale((prev) => {
      const delta = e.deltaY < 0 ? 1 + WHEEL_ZOOM_STEP : 1 - WHEEL_ZOOM_STEP;
      const next = Math.round(prev * delta * 100) / 100;
      return Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    });
  }, []);

  const handleClick = useCallback(() => {
    setScale((prev) => (prev === 1 ? 2 : 1));
  }, []);

  if (!file.data_url) {
    const mime = fileMimeType(file);
    const size = formatFileSize(file.size);
    const details = (lang === "en"
      ? [`Type: ${mime}`, size ? `Size: ${size}` : null]
      : [`类型：${mime}`, size ? `大小：${size}` : null]
    ).filter(Boolean).join(" · ");
    return (
      <div className="editor-empty">
        <div>{t("previewNotSupported", "此文件类型不支持预览")}</div>
        <div style={{ fontSize: 12, opacity: 0.75, lineHeight: 1.7 }}>
          <div>{file.name}</div>
          <div>{details}</div>
        </div>
        <button
          type="button"
          className="editor-close-btn secondary"
          onClick={() => void handleReveal()}
        >
          {t("reveal", "在文件管理器中显示")}
        </button>
      </div>
    );
  }
  return (
    <div
      ref={containerRef}
      className="editor-image-preview"
      onWheel={handleWheel}
      onClick={handleClick}
    >
      <img
        src={file.data_url}
        alt={file.name}
        style={{ transform: `scale(${scale})`, transformOrigin: "center center" }}
      />
      <span className="editor-image-zoom-label">{Math.round(scale * 100)}%</span>
    </div>
  );
}
