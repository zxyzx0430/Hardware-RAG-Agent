// ANSI escape code parsing for serial monitor output.

// ANSI escape code → HTML span color mapping
const ANSI_COLORS: Record<string, string> = {
  "31": "var(--danger)", // red
  "32": "var(--success)", // green
  "33": "var(--warn)", // yellow
  "34": "var(--primary)", // blue
  "35": "var(--purple)", // magenta
  "36": "#39c5cf", // cyan
};

/** Parse ANSI color escape codes and convert to HTML spans. Returns sanitized HTML string. */
export function parseAnsiToHtml(text: string): string {
  const parts: string[] = [];
  let remaining = text;
  let openSpan = false;
  const ansiRe = /\x1b\[(\d+)m/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = ansiRe.exec(remaining)) !== null) {
    if (match.index > lastIndex) {
      parts.push(remaining.slice(lastIndex, match.index));
    }
    const code = match[1];
    if (code === "0" || code === "39") {
      if (openSpan) {
        parts.push("</span>");
        openSpan = false;
      }
    } else if (ANSI_COLORS[code]) {
      if (openSpan) parts.push("</span>");
      parts.push(`<span style="color:${ANSI_COLORS[code]}">`);
      openSpan = true;
    }
    lastIndex = ansiRe.lastIndex;
  }
  if (lastIndex < remaining.length) {
    parts.push(remaining.slice(lastIndex));
  }
  if (openSpan) parts.push("</span>");
  const html = parts.join("");
  // Escape HTML entities except for our generated spans
  return html
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/&lt;span style="color:[^"]*"&gt;/g, (m) => m.replace(/&lt;/g, "<").replace(/&gt;/g, ">"))
    .replace(/&lt;\/span&gt;/g, "</span>");
}
