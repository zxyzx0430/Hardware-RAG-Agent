import { useSyncExternalStore } from 'react';
import zh from './zh';
import en from './en';
import { useAppStore } from '../stores/useAppStore';
import type { Lang } from '../types';

const DICT: Record<Lang, Record<string, string>> = { zh, en };

/** Translate a key using the current app language (reads from store). */
export function t(key: string, fallback?: string): string {
  const lang = useAppStore.getState().lang;
  return DICT[lang]?.[key] ?? fallback ?? key;
}

/** Translate a key for a specific language (no store dependency). */
export function tLang(key: string, lang: Lang): string {
  return DICT[lang]?.[key] ?? key;
}

/** Switch the app language. */
export function switchLang(lang: Lang): void {
  useAppStore.getState().setLang(lang);
}

/**
 * React hook that provides the `t` function and re-renders when language changes.
 * Usage: const { t } = useI18n();
 */
export function useI18n() {
  const lang = useSyncExternalStore(
    (callback) => useAppStore.subscribe(callback),
    () => useAppStore.getState().lang,
  );
  return {
    t: (key: string, fallback?: string) => DICT[lang]?.[key] ?? fallback ?? key,
    lang,
  };
}
