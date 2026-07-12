import { useRef, useCallback } from "react";

/**
 * 面板拖拽调整 hook
 * 直接通过 setWidth 回调更新宽度（通常是 zustand store 的 setter），
 * 避免本地 state + useEffect 同步造成的双渲染和拖拽卡顿。
 *
 * unit:
 *   - "px"  (默认): minWidth/maxWidth/width 均为像素值
 *   - "pct":        width 为父容器宽度的百分比，拖动时把 delta px 换算为百分比
 *
 * getReservedWidth (optional, px mode only):
 *   返回容器内其他元素占用的宽度（如对话区最小宽度 + explorer 宽度 + strips）。
 *   拖拽时动态计算 effectiveMax = min(maxWidth, containerW - reserved)，
 *   防止面板拖到超过容器可用空间导致 flex 布局破裂。
 */
export function usePanelResize(
  currentWidth: number,
  direction: "left" | "right" = "left",
  minWidth = 180,
  maxWidth = 600,
  setWidth?: (w: number) => void,
  unit: "px" | "pct" = "px",
  getReservedWidth?: () => number
) {
  const startInfo = useRef({ x: 0, w: 0, containerW: 0 });
  // Keep latest width in a ref so onMouseDown (stable callback) reads fresh value
  const widthRef = useRef(currentWidth);
  widthRef.current = currentWidth;
  // Keep latest getReservedWidth in a ref so onMove reads fresh value
  const reservedRef = useRef(getReservedWidth);
  reservedRef.current = getReservedWidth;

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      // Always get parent container width (needed for both pct and px dynamic max)
      const containerW =
        (e.currentTarget.parentElement as HTMLElement | null)?.offsetWidth || 1;
      startInfo.current = { x: e.clientX, w: widthRef.current, containerW };
      const { x: startX, w: startW, containerW: cw } = startInfo.current;

      const onMove = (ev: MouseEvent) => {
        const delta = direction === "left" ? ev.clientX - startX : startX - ev.clientX;
        if (unit === "pct") {
          const deltaPct = (delta / cw) * 100;
          const newPct = Math.min(maxWidth, Math.max(minWidth, startW + deltaPct));
          if (setWidth) setWidth(newPct);
        } else {
          let effectiveMax = maxWidth;
          if (reservedRef.current) {
            const reserved = reservedRef.current();
            effectiveMax = Math.min(maxWidth, Math.max(minWidth, cw - reserved));
          }
          const newW = Math.min(effectiveMax, Math.max(minWidth, startW + delta));
          if (setWidth) setWidth(newW);
        }
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        // 强制 Monaco/SVG 等需要感知容器尺寸的子组件重新布局
        window.dispatchEvent(new Event('resize'));
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    // Only depend on direction and constraints; width is read via ref
    [direction, minWidth, maxWidth, setWidth, unit]
  );

  return { onMouseDown };
}
