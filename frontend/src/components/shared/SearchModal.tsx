import { useState, useEffect, useRef, useMemo } from "react";
import { useAppStore } from "../../stores/useAppStore";
import { useSessionStore } from "../../stores/useSessionStore";
import { useChatStore } from "../../stores/useChatStore";
import { useI18n } from "../../i18n";
import { apiPost } from "../../api/client";

function renderContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part: any) => {
      if (typeof part === "string") return part;
      if (part.type === "text") return part.text || "";
      if (part.type === "image_url") return "![Image](" + part.image_url.url + ")";
      return "";
    }).join("\n\n");
  }
  return String(content ?? "");
}

interface GlobalSearchResult {
  message_id: string;
  session_id: string;
  role: string;
  content: string;
  session_title: string;
}

export function SearchModal() {
  const { t } = useI18n();
  const searchOpen = useAppStore((s) => s.searchOpen);
  const setSearchOpen = useAppStore((s) => s.setSearchOpen);
  const setActiveNav = useAppStore((s) => s.setActiveNav);
  const setActiveSession = useAppStore((s) => s.setActiveSession);

  const sessions = useSessionStore((s) => s.sessions);
  const messages = useChatStore((s) => s.messages);

  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"local" | "global">("local");
  const [globalResults, setGlobalResults] = useState<GlobalSearchResult[]>([]);
  const [globalSearching, setGlobalSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searchOpen) {
      setQuery("");
      setGlobalResults([]);
      setTab("local");
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [searchOpen]);

  useEffect(() => {
    if (!searchOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setSearchOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [searchOpen, setSearchOpen]);

  // 全局搜索：当 tab=global 且 query 不为空时搜索
  useEffect(() => {
    if (tab !== "global" || !query.trim()) {
      setGlobalResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      setGlobalSearching(true);
      try {
        const data = await apiPost<{ results: GlobalSearchResult[]; total: number }>("search", { query: query.trim(), limit: 20 });
        setGlobalResults(data?.results ?? []);
      } catch {
        setGlobalResults([]);
      } finally {
        setGlobalSearching(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query, tab]);

  const filteredSessions = useMemo(() => {
    if (!query.trim()) return [];
    const q = query.toLowerCase();
    return sessions.filter((s) => s.title.toLowerCase().includes(q)).slice(0, 8);
  }, [query, sessions]);

  const filteredMessages = useMemo(() => {
    if (!query.trim()) return [];
    const q = query.toLowerCase();
    return messages
      .filter((m) => renderContent(m.content).toLowerCase().includes(q))
      .slice(0, 8);
  }, [query, messages]);

  const handleSelectSession = (sessionId: string) => {
    setSearchOpen(false);
    setActiveNav("chat");
    setActiveSession(sessionId);
  };

  const handleMessageClick = (msgId: string, sessionId: string) => {
    setSearchOpen(false);
    setActiveNav("chat");
    setActiveSession(sessionId);
    setTimeout(() => {
      document.getElementById("msg-" + msgId)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 100);
  };

  if (!searchOpen) return null;

  const hasLocalResults = filteredSessions.length > 0 || filteredMessages.length > 0;
  const showEmpty = query.trim() && tab === "local" && !hasLocalResults;

  return (
    <div className="modal-overlay" onClick={() => setSearchOpen(false)}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        {/* Tab 切换 */}
        <div style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--border)", marginBottom: 8 }}>
          <button
            style={{
              flex: 1, padding: "8px 0", border: "none", background: tab === "local" ? "var(--hover-bg)" : "transparent",
              color: tab === "local" ? "var(--accent)" : "var(--muted-fg)", cursor: "pointer", fontWeight: tab === "local" ? 600 : 400,
              fontSize: 12, borderBottom: tab === "local" ? "2px solid var(--accent)" : "2px solid transparent",
            }}
            onClick={() => setTab("local")}
          >
            {t("searchLocalTab", "当前会话")}
          </button>
          <button
            style={{
              flex: 1, padding: "8px 0", border: "none", background: tab === "global" ? "var(--hover-bg)" : "transparent",
              color: tab === "global" ? "var(--accent)" : "var(--muted-fg)", cursor: "pointer", fontWeight: tab === "global" ? 600 : 400,
              fontSize: 12, borderBottom: tab === "global" ? "2px solid var(--accent)" : "2px solid transparent",
            }}
            onClick={() => setTab("global")}
          >
            {t("searchGlobalTab", "全局搜索")}
          </button>
        </div>

        <input
          ref={inputRef}
          className="modal-search-input"
          placeholder={tab === "global" ? t("searchGlobalPlaceholder", "搜索所有会话的消息...") : t("searchPlaceholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        <div className="modal-scroll">
          {tab === "local" && (
            <>
              {filteredSessions.length > 0 && (
                <>
                  <div style={{ padding: "6px 12px", fontSize: 10, color: "var(--muted-fg)", fontWeight: 600, textTransform: "uppercase" }}>{t('searchSessionsLabel')}</div>
                  {filteredSessions.map((s) => (
                    <div key={s.id} className="modal-item" onClick={() => handleSelectSession(s.id)}>
                      <span className="modal-item-title">{s.title}</span>
                      <span className="modal-item-preview">{s.preview || s.model}</span>
                    </div>
                  ))}
                </>
              )}
              {filteredMessages.length > 0 && (
                <>
                  <div style={{ padding: "6px 12px", fontSize: 10, color: "var(--muted-fg)", fontWeight: 600, textTransform: "uppercase", marginTop: filteredSessions.length > 0 ? 4 : 0 }}>{t('searchMessagesLabel')}</div>
                  {filteredMessages.map((m) => (
                    <div key={m.id} className="modal-item" onClick={() => handleMessageClick(m.id, useChatStore.getState().activeSessionId)}>
                      <span className="modal-item-title">{m.role === "user" ? t('userLabel') : "Assistant"}</span>
                      <span className="modal-item-preview">{renderContent(m.content).slice(0, 120)}</span>
                    </div>
                  ))}
                </>
              )}
              {showEmpty && (
                <div className="modal-empty">{t('noMatchResult')}</div>
              )}
              {!query.trim() && (
                <div className="modal-empty">{t('searchHint')}</div>
              )}
            </>
          )}

          {tab === "global" && (
            <>
              {globalSearching && (
                <div className="modal-empty" style={{ opacity: 0.6 }}>搜索中...</div>
              )}
              {!globalSearching && globalResults.length > 0 && (
                <>
                  <div style={{ padding: "6px 12px", fontSize: 10, color: "var(--muted-fg)", fontWeight: 600, textTransform: "uppercase" }}>
                    {t("searchGlobalResults", "跨会话结果")} ({globalResults.length})
                  </div>
                  {globalResults.map((r) => (
                    <div key={r.message_id} className="modal-item" onClick={() => handleMessageClick(r.message_id, r.session_id)}>
                      <span className="modal-item-title" style={{ fontSize: 11, color: "var(--muted-fg)" }}>{r.session_title || r.session_id}</span>
                      <span className="modal-item-preview" style={{ display: "block" }}>
                        <span style={{ fontSize: 10, color: "var(--accent)", marginRight: 4 }}>{r.role === "user" ? "👤" : "🤖"}</span>
                        {r.content.slice(0, 120)}
                      </span>
                    </div>
                  ))}
                </>
              )}
              {!globalSearching && query.trim() && globalResults.length === 0 && (
                <div className="modal-empty">{t('noMatchResult')}</div>
              )}
              {!query.trim() && (
                <div className="modal-empty">{t('searchHint')}</div>
              )}
            </>
          )}
        </div>

        <div className="modal-footer">
          <span><kbd>Esc</kbd> {t('escClose')}</span>
          <span><kbd>Enter</kbd> {t('enterSelect')}</span>
        </div>
      </div>
    </div>
  );
}
