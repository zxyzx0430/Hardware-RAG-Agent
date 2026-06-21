// Type declarations for react-syntax-highlighter ESM subpath imports
declare module "react-syntax-highlighter/dist/esm/prism" {
  import type { ComponentType } from "react";
  interface SyntaxHighlighterProps {
    language?: string;
    style?: Record<string, unknown>;
    customStyle?: Record<string, unknown>;
    codeTagProps?: Record<string, unknown>;
    showLineNumbers?: boolean;
    children?: string;
    [key: string]: unknown;
  }
  const SyntaxHighlighter: ComponentType<SyntaxHighlighterProps>;
  export default SyntaxHighlighter;
}

declare module "react-syntax-highlighter/dist/esm/styles/prism" {
  export const oneDark: Record<string, unknown>;
  export const oneLight: Record<string, unknown>;
  export const vscDarkPlus: Record<string, unknown>;
  export const atomDark: Record<string, unknown>;
  export const duotoneDark: Record<string, unknown>;
  export const duotoneLight: Record<string, unknown>;
  export const nightOwl: Record<string, unknown>;
  export const nightOwlLight: Record<string, unknown>;
  export const prism: Record<string, unknown>;
}
