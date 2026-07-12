// WiringEditor — WiringPane 顶部工具栏
// 仅保留：从代码提取 / 生成接线图 / 清空
import { apiPost } from "../../api/client";
import { useAppStore } from "../../stores/useAppStore";
import { useLogStore } from "../../stores/useLogStore";
import { useWiringStore, type WiringExtractData } from "../../stores/useWiringStore";
import type { WiringResponse } from "../../types/api";

const WIRING_TITLE = "Hardware RAG";

interface WiringEditorProps {
  zoom: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
}

const btnStyle: React.CSSProperties = {
  fontSize: 11, padding: "2px 8px", borderRadius: 4,
  border: "1px solid var(--border)", background: "var(--card)",
  cursor: "pointer", color: "var(--fg)",
};

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function getActiveCode(tabs: { id: string; code: string }[], activeId: string | null): string {
  return tabs.find((t) => t.id === activeId)?.code ?? "";
}

export function WiringEditor({ zoom, onZoomIn, onZoomOut, onReset }: WiringEditorProps) {
  const components = useWiringStore((s) => s.components);
  const connections = useWiringStore((s) => s.connections);
  const setSvg = useWiringStore((s) => s.setSvg);
  const setBom = useWiringStore((s) => s.setBom);
  const loadFromExtract = useWiringStore((s) => s.loadFromExtract);
  const clearAll = useWiringStore((s) => s.clearAll);
  const previewTabs = useAppStore((s) => s.previewTabs);
  const activePreviewTabId = useAppStore((s) => s.activePreviewTabId);

  const handleExtract = async () => {
    const code = getActiveCode(previewTabs, activePreviewTabId);
    if (!code.trim()) { alert("当前预览页没有代码，无法提取"); return; }
    try {
      const data = await apiPost<WiringExtractData>("wiring/extract", { code });
      loadFromExtract(data);
      useLogStore.getState().log("ok", "wiring",
        `提取完成：${data.components.length} 器件，${data.connections.length} 连线`);
    } catch (err) {
      alert("提取失败：" + errMsg(err));
      useLogStore.getState().log("error", "wiring", `提取失败: ${errMsg(err)}`);
    }
  };

  const handleGenerate = async () => {
    if (components.length === 0) { alert("请先添加器件"); return; }
    try {
      const res = await apiPost<WiringResponse>("wiring", {
        title: WIRING_TITLE, components, connections,
      });
      setSvg(res.svg ?? "");
      if (res.bom) setBom(res.bom);
      useLogStore.getState().log("ok", "wiring", "接线图生成完成");
    } catch (err) {
      alert("生成失败：" + errMsg(err));
      useLogStore.getState().log("error", "wiring", `生成失败: ${errMsg(err)}`);
    }
  };

  const handleClear = () => { if (confirm("确认清空所有器件和连线？")) clearAll(); };

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 8, padding: "6px 12px", borderBottom: "1px solid var(--border)",
      fontSize: 11,
    }}>
      <span style={{ fontWeight: 600, color: "var(--fg)" }}>接线图</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button style={btnStyle} onClick={handleExtract}>从代码提取</button>
        <button style={btnStyle} onClick={handleGenerate}>生成接线图</button>
        <button style={btnStyle} onClick={handleClear}>清空</button>
        <div style={{ width: 1, height: 14, background: "var(--border)", margin: "0 2px" }} />
        <button style={btnStyle} onClick={onZoomIn} aria-label="放大" title="放大">+</button>
        <button style={btnStyle} onClick={onZoomOut} aria-label="缩小" title="缩小">−</button>
        <button style={btnStyle} onClick={onReset} aria-label="重置缩放" title="重置缩放">重置</button>
        <span style={{ fontSize: 10, color: "var(--muted-fg)", lineHeight: "22px", minWidth: 40, textAlign: "center" }}>{Math.round(zoom * 100)}%</span>
      </div>
    </div>
  );
}
