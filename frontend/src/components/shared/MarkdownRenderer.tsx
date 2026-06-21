import { useState, useCallback, lazy, Suspense } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import DOMPurify from "dompurify";
import { copyToClipboard } from "../../utils/clipboard";
import { useI18n } from "../../i18n";

// 懒加载语法高亮组件，减小首屏 bundle
const SyntaxHighlighter = lazy(() =>
  import("react-syntax-highlighter/dist/esm/prism")
);
// oneDark 是普通 JS 对象（样式定义），不需要 lazy，直接静态导入
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

interface MarkdownRendererProps {
  content: string;
  streaming?: boolean;
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

function CodeBlock({
  language,
  code,
}: {
  language: string;
  code: string;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    copyToClipboard(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [code]);

  const langLabel = LANG_LABELS[language?.toLowerCase()] || language?.toUpperCase() || "Code";

  return (
    <div className="code-block-shell">
      <div className="code-block-header">
        <div className="code-block-dots">
          <span className="dot red" />
          <span className="dot yellow" />
          <span className="dot green" />
        </div>
        <span className="code-block-lang">{langLabel}</span>
        <button className="code-block-copy" onClick={handleCopy}>
          {copied ? (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
          ) : (
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          )}
          <span>{copied ? t("copied") : t("copy")}</span>
        </button>
      </div>
      <Suspense fallback={<pre style={{ margin: 0, padding: 12, background: "#1e1e2e", color: "#e6edf3", fontSize: 13, borderRadius: "0 0 8px 8px" }}>{code}</pre>}>
        <SyntaxHighlighter
          language={language || "text"}
          style={oneDark}
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
}

export function MarkdownRenderer({ content, streaming }: MarkdownRendererProps) {
  return (
    <div className={`markdown-body${streaming ? " streaming" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }) {
            const match = /language-(\w+)/.exec(className || "");
            const codeStr = String(children).replace(/\n$/, "");
            // 判断是否为代码块（有语言标记或包含换行）
            if (match || codeStr.includes("\n")) {
              return (
                <CodeBlock
                  language={match?.[1] || ""}
                  code={codeStr}
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
          table({ children }) {
            return (
              <div className="table-wrapper">
                <table>{children}</table>
              </div>
            );
          },
          // 链接在新窗口打开
          a({ href, children }) {
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
      {streaming && <span className="streaming-cursor">▊</span>}
    </div>
  );
}
