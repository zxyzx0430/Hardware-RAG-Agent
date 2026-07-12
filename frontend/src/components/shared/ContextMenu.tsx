import { useEffect, useRef, useState, useCallback } from "react";

export interface MenuItem {
  label: string;
  icon?: React.ReactNode;
  onClick?: () => void;
  children?: MenuItem[];
  danger?: boolean;
}

interface Props {
  items: MenuItem[];
  x: number;
  y: number;
  onClose: () => void;
}

function SubMenu({
  items,
  parentRect,
  onClose,
  onMouseEnter,
  onMouseLeave,
}: {
  items: MenuItem[];
  parentRect: DOMRect;
  onClose: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [activeSubIndex, setActiveSubIndex] = useState<number | null>(null);
  const [childRect, setChildRect] = useState<DOMRect | null>(null);

  const position = useCallback(() => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = parentRect.right;
    let top = parentRect.top;
    if (left + rect.width > vw) left = parentRect.left - rect.width;
    if (top + rect.height > vh) top = vh - rect.height - 4;
    ref.current.style.left = `${left}px`;
    ref.current.style.top = `${top}px`;
  }, [parentRect]);

  useEffect(() => {
    position();
  }, [position]);

  return (
    <div className="ctx-menu ctx-submenu" ref={ref} onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave} style={{ position: "fixed", left: 0, top: 0 }}>
      {items.map((item, i) =>
        item.label === "---" ? (
          <div key={i} className="ctx-sep" />
        ) : (
          <button
            key={i}
            className={`ctx-item${item.danger ? " destructive" : ""}`}
            onMouseEnter={() => {
              setActiveSubIndex(i);
              // capture rect for nested submenu positioning
              const el = ref.current?.children[i + (items.slice(0, i).filter((x) => x.label === "---").length)] as HTMLElement;
              if (el) setChildRect(el.getBoundingClientRect());
            }}
            onMouseLeave={() => setActiveSubIndex(null)}
            onClick={() => {
              if (item.onClick) item.onClick();
              onClose();
            }}
          >
            {item.icon}
            <span>{item.label}</span>
            {item.children && <span style={{ marginLeft: "auto", fontSize: 10 }}>▸</span>}
          </button>
        )
      )}
      {activeSubIndex !== null && items[activeSubIndex]?.children && childRect && (
        <SubMenu
          items={items[activeSubIndex].children!}
          parentRect={childRect}
          onClose={onClose}
        />
      )}
    </div>
  );
}

export function ContextMenu({ items, x, y, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [childRect, setChildRect] = useState<DOMRect | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [isSubmenuHovered, setIsSubmenuHovered] = useState(false);

  const clearCloseTimer = () => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  // adjust position to stay within viewport
  useEffect(() => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = x;
    let top = y;
    if (left + rect.width > vw) left = vw - rect.width - 4;
    if (top + rect.height > vh) top = vh - rect.height - 4;
    if (left < 0) left = 4;
    if (top < 0) top = 4;
    ref.current.style.left = `${left}px`;
    ref.current.style.top = `${top}px`;
  }, [x, y]);

  return (
    <div className="ctx-menu" ref={ref} style={{ position: "fixed", left: x, top: y }}>
      {items.map((item, i) =>
        item.label === "---" ? (
          <div key={i} className="ctx-sep" />
        ) : (
          <button
            key={i}
            className={`ctx-item${item.danger ? " destructive" : ""}`}
            onMouseEnter={() => {
              clearCloseTimer();
              setActiveIndex(i);
              const el = ref.current?.querySelector(`[data-idx="${i}"]`) as HTMLElement;
              if (el) setChildRect(el.getBoundingClientRect());
            }}
            onMouseLeave={() => {
              clearCloseTimer();
              closeTimerRef.current = setTimeout(() => {
                if (!isSubmenuHovered) {
                  setActiveIndex(null);
                  setChildRect(null);
                }
              }, 200);
            }}
            onClick={() => {
              if (item.children) return; // parent item with submenu doesn't close on click
              if (item.onClick) item.onClick();
              onClose();
            }}
            data-idx={i}
          >
            {item.icon}
            <span>{item.label}</span>
            {item.children && <span style={{ marginLeft: "auto", fontSize: 10 }}>▸</span>}
          </button>
        )
      )}
      {activeIndex !== null && items[activeIndex]?.children && childRect && (
        <SubMenu
          items={items[activeIndex].children!}
          parentRect={childRect}
          onClose={onClose}
          onMouseEnter={() => {
            clearCloseTimer();
            setIsSubmenuHovered(true);
          }}
          onMouseLeave={() => {
            setIsSubmenuHovered(false);
            clearCloseTimer();
            closeTimerRef.current = setTimeout(() => {
              setActiveIndex(null);
              setChildRect(null);
            }, 200);
          }}
        />
      )}
    </div>
  );
}
