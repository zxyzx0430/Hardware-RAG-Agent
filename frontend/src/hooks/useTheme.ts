import { useEffect, useCallback } from "react";
import { useAppStore } from "../stores/useAppStore";
import type { Theme } from "../types";

export function useTheme() {
  const { themeMode, setThemeMode } = useAppStore();

  const applyTheme = useCallback((t: Theme) => {
    const root = document.documentElement;
    if (t === "dark" || (t === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
  }, []);

  useEffect(() => {
    applyTheme(themeMode);
    if (themeMode === "auto") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = () => applyTheme("auto");
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
  }, [themeMode, applyTheme]);

  const cycleTheme = useCallback(() => {
    const order: Theme[] = ["light", "dark", "auto"];
    const idx = order.indexOf(themeMode);
    setThemeMode(order[(idx + 1) % order.length]);
  }, [themeMode, setThemeMode]);

  return { theme: themeMode, setTheme: setThemeMode, cycleTheme };
}
