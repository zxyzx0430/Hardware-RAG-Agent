import type { ContentPart } from "../types/session";

export type MessageContent = string | ContentPart[];

/**
 * 将消息内容转为可渲染字符串：纯文本原样返回，ContentPart[] 拼成 Markdown。
 * 兼容旧格式（part 为 string）和未知类型（fallback 到 String()）。
 */
export function renderMessageContent(content: MessageContent | unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part: ContentPart | string) => {
      if (typeof part === "string") return part;
      if (part.type === "text") return part.text || "";
      if (part.type === "image_url") return `![Image](${part.image_url.url})`;
      return "";
    }).join("\n\n");
  }
  return String(content ?? "");
}
