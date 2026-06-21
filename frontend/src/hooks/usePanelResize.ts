import { useRef, useCallback } from "react";

/**
 * 面板拖拽调整 hook
 * 直接通过 setWidth 回调更新宽度（通常是 zustand store 的 setter），
 * 避免本地 state + useEffect 同步造成的双渲染和拖拽卡顿。
 */
export function usePanelResize(
  currentWidth: number,
  direction: "left" | "right" = "left",
  minWidth = 180,
  maxWidth = 600,
  setWidth?: (w: number) => void
) {
  const startInfo = useRef({ x: 0, w: 0 });
  // Keep latest width in a ref so onMouseDown (stable callback) reads fresh value
  const widthRef = useRef(currentWidth);
  widthRef.current = currentWidth;

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      startInfo.current = { x: e.clientX, w: widthRef.current };
      const { x: startX, w: startW } = startInfo.current;

      const onMove = (ev: MouseEvent) => {
        const delta = direction === "left" ? ev.clientX - startX : startX - ev.clientX;
        const newW = Math.min(maxWidth, Math.max(minWidth, startW + delta));
        if (setWidth) {
          setWidth(newW);
        }
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    // Only depend on direction and constraints; width is read via ref
    [direction, minWidth, maxWidth, setWidth]
  );

  return { onMouseDown };
}
