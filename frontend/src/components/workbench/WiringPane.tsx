// WiringPane — 顶部 WiringEditor + 下方 SVG 渲染区（缩放/拖拽/BOM）
// 数据源：useWiringStore（svg/bom）。Agent 推送：useWorkbenchBridge.wiringRenderData
import { useState, useRef, useCallback, useEffect } from "react";
import DOMPurify from "dompurify";
import { useI18n } from "../../i18n";
import { useWiringStore, type BomEntry } from "../../stores/useWiringStore";
import { useWorkbenchBridge } from "../../stores/useWorkbenchBridge";
import { WiringEditor } from "./WiringEditor";
import { EmptyState } from "../shared/EmptyState";

const SVG_NODE_WARN_THRESHOLD = 100;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 3;
const ZOOM_IN_FACTOR = 1.2;
const ZOOM_OUT_FACTOR = 0.8;
const WHEEL_UP_FACTOR = 1.1;
const WHEEL_DOWN_FACTOR = 0.9;

interface DragState {
  active: boolean;
  startX: number;
  startY: number;
  panStartX: number;
  panStartY: number;
}

type DragHandlers = { onMove: (ev: MouseEvent) => void; onUp: () => void };
type Pan = { x: number; y: number };

const thLeft: React.CSSProperties = { textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 };
const thRight: React.CSSProperties = { textAlign: "center", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500, width: 50 };
const tdLeft: React.CSSProperties = { padding: "3px 8px", color: "var(--fg)" };
const tdCenter: React.CSSProperties = { padding: "3px 8px", textAlign: "center", color: "var(--fg)" };

/** Count SVG element nodes (approximate) by counting opening tags. */
function countSvgNodes(svg: string): number {
  const tagRe = /<(rect|circle|line|path|text|polygon|ellipse|polyline|g|use|image)\b/g;
  let count = 0;
  let m: RegExpExecArray | null;
  while ((m = tagRe.exec(svg)) !== null) count++;
  return count;
}

/** Recompute pan so the point under the cursor stays fixed after zoom. */
function computePan(prevPan: Pan, mx: number, my: number, ratio: number): Pan {
  return {
    x: mx - (mx - prevPan.x) * ratio,
    y: my - (my - prevPan.y) * ratio,
  };
}

/** Build a mousemove handler that pans the SVG by drag delta. */
function makeDragMoveHandler(
  dragRef: { current: DragState },
  setPan: React.Dispatch<React.SetStateAction<Pan>>,
): (ev: MouseEvent) => void {
  return (ev) => {
    if (!dragRef.current.active) return;
    setPan({
      x: dragRef.current.panStartX + ev.clientX - dragRef.current.startX,
      y: dragRef.current.panStartY + ev.clientY - dragRef.current.startY,
    });
  };
}

/** Tear down a drag: mark inactive, remove listeners, restore cursor. */
function endDrag(
  dragRef: { current: DragState },
  setDragging: React.Dispatch<React.SetStateAction<boolean>>,
  handlersRef: { current: DragHandlers | null },
  onMove: (ev: MouseEvent) => void,
  onUp: () => void,
): void {
  dragRef.current.active = false;
  setDragging(false);
  document.removeEventListener("mousemove", onMove);
  document.removeEventListener("mouseup", onUp);
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
  handlersRef.current = null;
}

export function WiringPane() {
  const { t } = useI18n();
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Pan>({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [svgNodeWarning, setSvgNodeWarning] = useState("");
  const dragRef = useRef<DragState>({ active: false, startX: 0, startY: 0, panStartX: 0, panStartY: 0 });
  const wrapRef = useRef<HTMLDivElement>(null);
  const dragHandlersRef = useRef<DragHandlers | null>(null);

  const svgContent = useWiringStore((s) => s.svg);
  const bomData = useWiringStore((s) => s.bom);
  const setSvg = useWiringStore((s) => s.setSvg);
  const setBom = useWiringStore((s) => s.setBom);
  const wiringRenderData = useWorkbenchBridge((s) => s.wiringRenderData);

  // Agent 推送路径：bridge 收到 render_wiring 结果时写入 store
  useEffect(() => {
    if (!wiringRenderData) return;
    setSvg(wiringRenderData.svg);
    if (wiringRenderData.bom) setBom(wiringRenderData.bom);
  }, [wiringRenderData, setSvg, setBom]);

  // SVG 节点数性能警告
  useEffect(() => {
    if (!svgContent) { setSvgNodeWarning(""); return; }
    const count = countSvgNodes(svgContent);
    setSvgNodeWarning(count > SVG_NODE_WARN_THRESHOLD
      ? `⚠ 节点数 ${count} 超过 ${SVG_NODE_WARN_THRESHOLD}，可能影响渲染性能`
      : "");
  }, [svgContent]);

  // 卸载时清理未完成的拖拽监听
  useEffect(() => {
    return () => {
      if (!dragHandlersRef.current) return;
      document.removeEventListener("mousemove", dragHandlersRef.current.onMove);
      document.removeEventListener("mouseup", dragHandlersRef.current.onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      dragHandlersRef.current = null;
    };
  }, []);

  const computeZoom = useCallback((prevZoom: number, el: HTMLElement, e: WheelEvent): number => {
    const factor = e.deltaY < 0 ? WHEEL_UP_FACTOR : WHEEL_DOWN_FACTOR;
    const newZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, prevZoom * factor));
    const rect = el.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    setPan((prevPan) => computePan(prevPan, mx, my, newZoom / prevZoom));
    return newZoom;
  }, []);

  const applyZoom = useCallback((el: HTMLElement, e: WheelEvent) => {
    e.preventDefault();
    setZoom((prevZoom) => computeZoom(prevZoom, el, e));
  }, [computeZoom]);

  // 滚轮缩放：以鼠标位置为中心
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => applyZoom(el, e);
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [applyZoom]);

  const beginDrag = useCallback((cx: number, cy: number) => {
    dragRef.current = { active: true, startX: cx, startY: cy, panStartX: pan.x, panStartY: pan.y };
    setDragging(true);
    document.body.style.cursor = "grabbing";
    document.body.style.userSelect = "none";
  }, [pan]);

  const attachDragHandlers = useCallback(() => {
    const onMove = makeDragMoveHandler(dragRef, setPan);
    const onUp = () => endDrag(dragRef, setDragging, dragHandlersRef, onMove, onUp);
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    dragHandlersRef.current = { onMove, onUp };
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    beginDrag(e.clientX, e.clientY);
    attachDragHandlers();
  }, [beginDrag, attachDragHandlers]);

  const clampZoom = (z: number) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  const zoomIn = () => setZoom((z) => clampZoom(z * ZOOM_IN_FACTOR));
  const zoomOut = () => setZoom((z) => clampZoom(z * ZOOM_OUT_FACTOR));
  const resetView = () => { setZoom(1); setPan({ x: 0, y: 0 }); };

  return (
    <div className="wiring-panel" style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%" }}>
      <WiringEditor
        zoom={zoom}
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onReset={resetView}
      />
      <div
        className="wiring-svg-wrap"
        ref={wrapRef}
        style={{ flex: 1, overflow: "hidden", cursor: dragging ? "grabbing" : "grab", position: "relative" }}
        onMouseDown={handleMouseDown}
      >
        {svgNodeWarning && (
          <div style={{ position: "absolute", top: 6, left: 8, zIndex: 2, fontSize: 10, color: "var(--warn)", background: "var(--card)", padding: "2px 6px", borderRadius: 4 }}>
            {svgNodeWarning}
          </div>
        )}
        {svgContent ? (
          <div
            style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: "0 0", width: "100%", height: "100%" }}
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(svgContent, { USE_PROFILES: { svg: true, svgFilters: true } }) }}
          />
        ) : (
          <EmptyState size="md" title="点击「从代码提取」或「生成接线图」开始" icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 3v18M18 3v18M6 12h12" strokeLinecap="round" /></svg>} />
        )}
      </div>
      <ComponentsList />
      <BomTable bom={bomData} />
    </div>
  );
}

// ComponentsList — 器件列表（Task 9 交叉联动）
// 渲染 useWiringStore.components；冲突引脚器件加红色 outline；点击设 selectedPin
function ComponentsList() {
  const [collapsed, setCollapsed] = useState(true);
  const components = useWiringStore((s) => s.components);
  const conflictPins = useWiringStore((s) => s.conflictPins);
  const setSelectedPin = useWiringStore((s) => s.setSelectedPin);

  if (components.length === 0) return null;

  return (
    <div style={{ borderTop: "1px solid var(--border)", fontSize: 11 }}>
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "6px 12px", background: "transparent", border: "none",
          color: "var(--fg)", cursor: "pointer", fontWeight: 600,
        }}
      >
        <span>器件列表 ({components.length}){conflictPins.size > 0 ? ` · ${conflictPins.size} 冲突引脚` : ""}</span>
        <span style={{ color: "var(--muted-fg)", fontSize: 10 }}>{collapsed ? "▸" : "▾"}</span>
      </button>
      {!collapsed && (
        <div style={{ padding: "0 12px 8px", overflowY: "auto", maxHeight: 160 }}>
          {components.map((c) => {
            const hasConflict = c.pins.some((p) => conflictPins.has(p));
            return (
              <div
                key={c.name}
                onClick={() => { if (c.pins[0]) setSelectedPin(c.pins[0]); }}
                style={{
                  padding: "4px 8px",
                  marginBottom: 4,
                  borderRadius: 4,
                  border: "1px solid var(--border)",
                  outline: hasConflict ? "2px solid var(--danger)" : "none",
                  cursor: "pointer",
                  color: "var(--fg)",
                }}
              >
                <div style={{ fontWeight: 500 }}>{c.name}</div>
                <div style={{ color: "var(--muted-fg)", fontSize: 10 }}>{c.pins.join(", ")}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function BomTable({ bom }: { bom: BomEntry[] }) {
  const { t } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  return (
    <div style={{ borderTop: "1px solid var(--border)", fontSize: 11 }}>
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        style={{
          width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "6px 12px", background: "transparent", border: "none",
          color: "var(--fg)", cursor: "pointer", fontWeight: 600,
        }}
      >
        <span>{t('bomTitle')} {bom.length > 0 ? `(${bom.length})` : "— 暂无"}</span>
        <span style={{ color: "var(--muted-fg)", fontSize: 10 }}>{collapsed ? "▸" : "▾"}</span>
      </button>
      {!collapsed && bom.length > 0 && (
        <div style={{ padding: "0 12px 8px", overflowY: "auto", maxHeight: 160 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={thLeft}>{t('componentLabel')}</th>
                <th style={thRight}>{t('quantityLabel')}</th>
              </tr>
            </thead>
            <tbody>
              {bom.map((item) => (
                <tr key={item.component} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={tdLeft}>{item.component}</td>
                  <td style={tdCenter}>×{item.qty}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
