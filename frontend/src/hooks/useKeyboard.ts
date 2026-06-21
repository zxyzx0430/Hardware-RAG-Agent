import { useEffect } from "react";
import { useAppStore } from "../stores/useAppStore";
import { useSessionStore } from "../stores/useSessionStore";
import type { ShortcutAction } from "../types";

export function useKeyboard(actions: ShortcutAction[]) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      for (const a of actions) {
        if (a.key.toLowerCase() !== e.key.toLowerCase()) continue;
        if (a.ctrl && !e.ctrlKey) continue;
        if (a.shift && !e.shiftKey) continue;
        if (a.meta && !e.metaKey) continue;
        e.preventDefault();
        a.handler();
        return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [actions]);
}

export function useDefaultKeyboardShortcuts() {
  const { setActiveNav } = useAppStore();

  return [
    {
      key: "k",
      ctrl: true,
      handler: () => {
        useAppStore.getState().setSearchOpen(true);
      },
      label: "搜索 Ctrl+K",
    },
    {
      key: ",",
      ctrl: true,
      handler: () => setActiveNav("settings"),
      label: "打开设置 Ctrl+,",
    },
    {
      key: "n",
      ctrl: true,
      handler: () => {
        useSessionStore.getState().newSession();
      },
      label: "新建对话 Ctrl+N",
    },
    {
      key: "ArrowUp",
      ctrl: true,
      handler: () => {
        const { sessions } = useSessionStore.getState();
        const activeSessionId = useAppStore.getState().activeSession;
        const idx = sessions.findIndex((s) => s.id === activeSessionId);
        if (idx > 0) {
          useSessionStore.getState().selectSession(sessions[idx - 1].id);
        }
      },
      label: "上一个会话 Ctrl+↑",
    },
    {
      key: "ArrowDown",
      ctrl: true,
      handler: () => {
        const { sessions } = useSessionStore.getState();
        const activeSessionId = useAppStore.getState().activeSession;
        const idx = sessions.findIndex((s) => s.id === activeSessionId);
        if (idx < sessions.length - 1) {
          useSessionStore.getState().selectSession(sessions[idx + 1].id);
        }
      },
      label: "下一个会话 Ctrl+↓",
    },
    {
      key: "Escape",
      handler: () => {
        const { searchOpen, setSearchOpen } = useAppStore.getState();
        if (searchOpen) {
          setSearchOpen(false);
          return;
        }
        const { activeNav, setActiveNav } = useAppStore.getState();
        if (activeNav === "settings") {
          setActiveNav("chat");
          return;
        }
      },
      label: "关闭弹窗 Escape",
    },
    {
      key: "/",
      ctrl: true,
      handler: () => {
        useAppStore.getState().setShortcutHelpOpen(true);
      },
      label: "快捷键帮助 Ctrl+/",
    },
  ] as ShortcutAction[];
}
