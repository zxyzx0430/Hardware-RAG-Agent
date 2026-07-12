import { useState, useCallback, lazy, Suspense, useEffect, memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { copyToClipboard } from "../../utils/clipboard";
import { useI18n } from "../../i18n";
import { useAppStore } from "../../stores/useAppStore";
import type { SourceRef } from "../../types/session";

// 懒加载语法高亮组件，减小首屏 bundle
const SyntaxHighlighter = lazy(() =>
  import("react-syntax-highlighter/dist/esm/prism")
);
// 语法主题：深色 oneDark，浅色 oneLight
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";

interface MarkdownRendererProps {
  content: string;
  streaming?: boolean;
  enableSourceRef?: boolean;
  sources?: SourceRef[];
  onSourceClick?: (id: string) => void;
  onPushCodeToPreview?: (code: string, label: string, language: string) => void;
  onOpenInEditor?: (code: string, language: string) => void;
}

/**
 * 把正文中的来源引用统一转成 markdown 链接 [srcN](#src-N)。
 * 兼容 LLM 可能输出的多种格式：
 *   - [srcN]
 *   - [^N](srcN)
 *   - [^N]
 * 用 fenced code block 分段，只对非代码段替换，避免破坏代码块内的引用。
 */
function transformSourceRefs(content: string): string {
  const segments = content.split(/(```[\s\S]*?```)/g);
  return segments
    .map((seg, i) => {
      if (i % 2 === 1) return seg; // 代码段原样保留
      return seg
        .replace(/\[\^(\d+)\]\(src\1\)/g, '[src$1](#src-$1)')
        .replace(/\[\^(\d+)\]/g, '[src$1](#src-$1)')
        .replace(/\[src(\d+)\]/g, '[src$1](#src-$1)');
    })
    .join('');
}

/** 从 sources 里找对应 srcN 的 relevance_level，推导角标 class */
function srcRelevanceClass(n: number, sources?: SourceRef[]): string {
  if (!sources || sources.length === 0) return '';
  const src = sources.find((s) => s.id === `src${n}`);
  if (!src?.relevance_level) return '';
  return `rel-${src.relevance_level}`;
}

/** 语言显示名映射 */
const LANG_LABELS: Record<string, string> = {
  js: "JavaScript",
  javascript: "JavaScript",
  ts: "TypeScript",
  typescript: "TypeScript",
  py: "Python",
  python: "Python",
  c: "C",
  cpp: "C++",
  arduino: "Arduino",
  json: "JSON",
  bash: "Bash",
  sh: "Shell",
  sql: "SQL",
  html: "HTML",
  css: "CSS",
  yaml: "YAML",
  toml: "TOML",
  ini: "INI",
  xml: "XML",
  md: "Markdown",
  rust: "Rust",
  go: "Go",
  java: "Java",
};

/** 根据 app store 中的 themeMode 与 html.dark 类判断当前是否为深色模式 */
export function useAppliedDarkMode(): boolean {
  const themeMode = useAppStore((s) => s.themeMode);
  const [isDarkClass, setIsDarkClass] = useState(() =>
    document.documentElement.classList.contains("dark")
  );

  useEffect(() => {
    const root = document.documentElement;
    const update = () => setIsDarkClass(root.classList.contains("dark"));
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    update();
    return () => observer.disconnect();
  }, []);

  if (themeMode === "dark") return true;
  if (themeMode === "light") return false;
  return isDarkClass;
}

interface CodeBlockProps {
  language: string;
  code: string;
  streaming?: boolean;
  onPushCodeToPreview?: (code: string, label: string, language: string) => void;
  onOpenInEditor?: (code: string, language: string) => void;
}

const CodeBlock = memo(function CodeBlock({ language, code, streaming, onPushCodeToPreview, onOpenInEditor }: CodeBlockProps) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const isDark = useAppliedDarkMode();

  const handleCopy = useCallback(() => {
    copyToClipboard(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [code]);

  const handlePush = useCallback(() => {
    onPushCodeToPreview?.(code, `assistant-code-${language || 'text'}`, language || 'text');
  }, [code, language, onPushCodeToPreview]);

  const handleOpenInEditor = useCallback(() => {
    onOpenInEditor?.(code, language || 'text');
  }, [code, language, onOpenInEditor]);

  const langLabel = LANG_LABELS[language?.toLowerCase()] || language?.toUpperCase() || "Code";

  // 流式输出时降级为纯 <pre>，跳过 Prism 同步高亮，避免主线程阻塞
  if (streaming) {
    return (
      <div className="code-block-shell">
        <div className="code-block-header">
          <div className="code-block-dots">
            <span className="dot red" />
            <span className="dot yellow" />
            <span className="dot green" />
          </div>
          <span className="code-block-lang">{langLabel}</span>
          <div className="code-block-actions">
            {onPushCodeToPreview && (
              <button className="code-block-copy" onClick={handlePush} title={t('pushToPreviewShort')}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><polyline points="12 18 12 12"/><polyline points="9 15 12 12 15 15"/></svg>
                <span>{t('pushToPreviewShort')}</span>
              </button>
            )}
            {onOpenInEditor && (
              <button className="code-block-copy" onClick={handleOpenInEditor} title={t('openInEditor')}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                <span>{t('openInEditor')}</span>
              </button>
            )}
            <button className="code-block-copy" onClick={handleCopy}>
              {copied ? (
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              )}
              <span>{copied ? t("copied") : t("copy")}</span>
            </button>
          </div>
        </div>
        <pre style={{ margin: 0, padding: "12px 16px", background: "var(--code-block-bg)", color: "var(--fg)", fontSize: 13, lineHeight: 1.6, borderRadius: "0 0 8px 8px", overflow: "auto", fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
          <code>{code}</code>
        </pre>
      </div>
    );
  }

  return (
    <div className="code-block-shell">
      <div className="code-block-header">
        <div className="code-block-dots">
          <span className="dot red" />
          <span className="dot yellow" />
          <span className="dot green" />
        </div>
        <span className="code-block-lang">{langLabel}</span>
        <div className="code-block-actions">
          {onPushCodeToPreview && (
            <button className="code-block-copy" onClick={handlePush} title={t('pushToPreviewShort')}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><polyline points="12 18 12 12"/><polyline points="9 15 12 12 15 15"/></svg>
              <span>{t('pushToPreviewShort')}</span>
            </button>
          )}
          {onOpenInEditor && (
            <button className="code-block-copy" onClick={handleOpenInEditor} title={t('openInEditor')}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              <span>{t('openInEditor')}</span>
            </button>
          )}
          <button className="code-block-copy" onClick={handleCopy}>
            {copied ? (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
            ) : (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            )}
            <span>{copied ? t("copied") : t("copy")}</span>
          </button>
        </div>
      </div>
      <Suspense fallback={<pre style={{ margin: 0, padding: 12, background: "var(--code-block-bg)", color: "var(--fg)", fontSize: 13, borderRadius: "0 0 8px 8px" }}>{code}</pre>}>
        <SyntaxHighlighter
          language={language || "text"}
          style={isDark ? oneDark : oneLight}
          customStyle={{
            margin: 0,
            borderRadius: "0 0 8px 8px",
            fontSize: 13,
            lineHeight: 1.6,
            padding: "12px 16px",
          }}
          showLineNumbers={code.split("\n").length > 3}
          lineNumberStyle={{ opacity: 0.35, fontSize: 11 }}
        >
          {code}
        </SyntaxHighlighter>
      </Suspense>
    </div>
  );
});

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  streaming,
  enableSourceRef,
  sources,
  onSourceClick,
  onPushCodeToPreview,
  onOpenInEditor,
}: MarkdownRendererProps) {
  const processedContent = useMemo(
    () => (enableSourceRef ? transformSourceRefs(content) : content),
    [content, enableSourceRef]
  );

  // 缓存 components 对象，避免 ReactMarkdown 内部缓存失效
  const components = useMemo(
    () => ({
      code({ className, children, ...props }: any) {
        const match = /language-(\w+)/.exec(className || "");
        const codeStr = String(children).replace(/\n$/, "");
        // 判断是否为代码块（有语言标记或包含换行）
        if (match || codeStr.includes("\n")) {
          return (
            <CodeBlock
              language={match?.[1] || ""}
              code={codeStr}
              streaming={streaming}
              onPushCodeToPreview={onPushCodeToPreview}
              onOpenInEditor={onOpenInEditor}
            />
          );
        }
        // 行内代码
        return (
          <code className="inline-code" {...props}>
            {children}
          </code>
        );
      },
      // 表格样式
      table({ children }: any) {
        return (
          <div className="table-wrapper">
            <table>{children}</table>
          </div>
        );
      },
      // 链接处理：src 引用按钮 vs 外部链接
      a({ href, children }: any) {
        if (href && href.startsWith('#src-')) {
          const n = parseInt(href.slice(5), 10);
          const relClass = srcRelevanceClass(n, sources);
          return (
            <button
              type="button"
              className={`src-cite ${relClass}`}
              onClick={(e) => {
                e.preventDefault();
                onSourceClick?.(`src${n}`);
              }}
              title={sources?.find((s) => s.id === `src${n}`)?.title || `来源 src${n}`}
            >
              src{n}
            </button>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [streaming, sources, onSourceClick, onPushCodeToPreview, onOpenInEditor]
  );

  return (
    <div className={`markdown-body${streaming ? " streaming" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {processedContent}
      </ReactMarkdown>
      {streaming && <span className="streaming-cursor">▊</span>}
    </div>
  );
});
