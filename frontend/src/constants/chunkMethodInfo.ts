/**
 * Shared chunk method metadata used by KB creation, KB config, and upload dialog.
 */

export interface ChunkMethodInfo {
  principle: string;
  pros: string[];
  cons: string[];
  useCase: string;
}

export const CHUNK_METHOD_INFO: Record<string, ChunkMethodInfo> = {
  hybrid: {
    principle:
      "基于 RecursiveCharacterTextSplitter 的字符级切分，结合 markdown 标题层级识别。先按 # 标题分段，再按字符数（默认 800-1200）切分子段，内联代码块用占位符保护。",
    pros: [
      "速度快（无需 LLM 调用），本地即可完成",
      "稳定可预测，相同文档每次结果一致",
      "代码块边界完整（占位符保护机制）",
      "支持自定义 chunk_size 精调（500-2000）",
    ],
    cons: [
      "无法理解语义，仅按字符位置切分",
      "跨章节关联弱（相关内容可能分散在不同 chunk）",
      "对无标题文档退化为纯字符切分",
      "chunk 数量较多（比 Agent 多 2-3 倍）",
    ],
    useCase:
      "结构良好的技术文档（有 # 标题的 Markdown）、API 手册、教程文档、快速原型验证",
  },
  agent: {
    principle:
      "用 LLM 分析文档结构，识别语义章节边界，生成 section 摘要和关键词。支持多轮投票提高一致性。对无标题文档自动 fallback 到代码块感知切分（保持代码块完整）。",
    pros: [
      "语义完整性高（相关关键词在同一 chunk 共现）",
      "跨章节关联强（LLM 识别 section 关系）",
      "chunk 数量少 55%（更高效的 embedding）",
      "每个 chunk 带 section_title + summary 元数据",
      "无标题文档自动 fallback，不会完全失效",
    ],
    cons: [
      "需要 LLM API（耗时 2-5 分钟/文档 + API 成本）",
      "代码块边界偶尔截断（LLM 不总是尊重代码块边界）",
      "依赖 LLM 质量（模型差时 section 识别不准）",
      "无标题文档 fallback 后效果接近 HybridChunker",
    ],
    useCase:
      "结构复杂的芯片手册、跨主题技术文档、需要高质量检索的生产环境、对 embedding 成本敏感的场景",
  },
  multimodal: {
    principle:
      "用 Vision 模型（如 GPT-4o）分析 PDF 页面图像，先低分辨率分页定位章节边界，再高分辨率细化分析并生成 chunk。精度最高。",
    pros: [
      "精度最高，能识别视觉结构（表格/图表布局）",
      "适合扫描文档和复杂排版的 PDF",
      "能理解页面级语义（不仅文字）",
    ],
    cons: [
      "需要 Vision 模型（成本最高）",
      "速度最慢（每页多次 Vision 调用）",
      "仅支持 PDF（不支持纯文本/Markdown）",
      "API 依赖最强，网络波动影响大",
    ],
    useCase:
      "扫描版芯片手册、含大量表格/图表的 PDF、对精度要求极高的生产环境",
  },
};
