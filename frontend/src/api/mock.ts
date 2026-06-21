// Mock 数据层 — 前端不依赖后端时使用
// 按 docs/api-contract.md 的字段定义 mock

import type {
  ModelInfo,
  DevicesResponse,
  WiringResponse,
  PinAuditResponse,
  ToolResult,
} from "../types/api";
import type { Session } from "../types/session";
import type { KBDoc } from "../types/api";

export const MOCK_SESSIONS: Session[] = [
  { id: "s1", title: "STM32 I2C 通信问题排查", preview: "为什么我的 I2C 在 400kHz 时出现 NACK？", model: "GPT-4o", createdAt: Date.now() - 3600000, project: "嵌入式开发", pinned: true, msgCount: 12 },
  { id: "s2", title: "ESP32 FreeRTOS 任务调度", preview: "任务堆栈溢出的检测与处理方式", model: "Claude 3.5", createdAt: Date.now() - 7200000, project: "嵌入式开发", pinned: false, msgCount: 8 },
  { id: "s3", title: "Keil MDK 编译优化选项", preview: "O2 优化导致调试断点失效的原因", model: "GPT-4o", createdAt: Date.now() - 86400000, project: "嵌入式开发", pinned: false, msgCount: 15 },
  { id: "s4", title: "RTOS 与裸机选型对比", preview: "在资源受限 MCU 上使用 RTOS 的权衡", model: "Claude 3.5", createdAt: Date.now() - 90000000, project: "选型评估", pinned: false, msgCount: 6 },
  { id: "s5", title: "SPI DMA 传输配置", preview: "DMA 传输完成回调未触发的排查思路", model: "GPT-4o", createdAt: Date.now() - 3 * 86400000, project: "嵌入式开发", pinned: false, msgCount: 10 },
  { id: "s6", title: "ADC 采样精度问题", preview: "差分模式 vs 单端模式的噪声特性", model: "Claude 3.5", createdAt: Date.now() - 4 * 86400000, project: "选型评估", pinned: false, msgCount: 5 },
  { id: "s7", title: "CAN 总线波特率计算", preview: "不同晶振频率下的 BRP 和时序参数", model: "GPT-4o", createdAt: Date.now() - 12 * 86400000, project: "传感器调试", pinned: false, msgCount: 9 },
];

export const MOCK_KB_ITEMS: KBDoc[] = [
  { id: "kb1", name: "STM32F4 参考手册", size: "12.4 MB", chunks: 4821, status: "indexed", enabled: true, updatedAt: "2025-06-10", docType: "Reference Manual", tags: ["STM32", "Cortex-M4"] },
  { id: "kb2", name: "STM32 HAL 驱动手册", size: "5.8 MB", chunks: 2103, status: "indexed", enabled: true, updatedAt: "2025-06-10", docType: "User Manual", tags: ["HAL", "STM32"] },
  { id: "kb3", name: "ESP-IDF 编程指南 v5.2", size: "9.1 MB", chunks: 3567, status: "indexed", enabled: true, updatedAt: "2025-06-08", docType: "Programming Guide", tags: ["ESP32", "FreeRTOS"] },
  { id: "kb4", name: "ARM Cortex-M4 技术参考", size: "3.2 MB", chunks: 1289, status: "error", enabled: false, updatedAt: "2025-06-01", docType: "Technical Reference", tags: ["ARM", "Cortex-M4"], errorMessage: "文件格式不兼容，支持 PDF / MD / TXT" },
  { id: "kb5", name: "FreeRTOS 内核参考手册", size: "2.7 MB", chunks: 987, status: "indexing", enabled: true, updatedAt: "2025-06-17", docType: "Reference Manual", tags: ["FreeRTOS", "RTOS"] },
];

export const MOCK_MODELS: ModelInfo[] = [
  { id: "gpt-4o", label: "GPT-4o", provider: "OpenAI" },
  { id: "gpt-4o-mini", label: "GPT-4o Mini", provider: "OpenAI" },
  { id: "claude-3-5-sonnet", label: "Claude 3.5 Sonnet", provider: "Anthropic" },
  { id: "claude-3-haiku", label: "Claude 3 Haiku", provider: "Anthropic" },
  { id: "deepseek-v3", label: "DeepSeek V3", provider: "DeepSeek" },
];

export const MOCK_DEVICES: DevicesResponse = {
  devices: [
    { port: "COM3", description: "USB Serial Device (COM3)" },
    { port: "COM5", description: "CP2102 USB to UART Bridge (COM5)" },
  ],
};

export const MOCK_WIRING: WiringResponse = {
  svg: '<svg viewBox="0 0 640 420" xmlns="http://www.w3.org/2000/svg"><rect width="640" height="420" fill="#f0f1f5"/><text x="20" y="40" font-family="sans-serif" font-size="16">ESP32-S3 + BME280 接线图（Mock）</text></svg>',
  bom: [
    { component: "ESP32-S3 DevKitC-1", qty: 1 },
    { component: "BME280 Breakout", qty: 1 },
    { component: "跳线", qty: 4 },
  ],
};

export const MOCK_AUDIT: PinAuditResponse = {
  safe: false,
  warnings: [
    {
      pin: "GPIO0",
      severity: "critical",
      message: "GPIO0 是 Strapping 引脚，上拉决定启动模式。作为 BUTTON 使用时，按下会拉低进入下载模式。",
      suggestion: "改用 GPIO4 或 GPIO5 作为按钮输入",
    },
  ],
  conflicts: [],
  pin_map: {},
};

export const MOCK_TOOL_RESULT: ToolResult = {
  success: true,
  output: "工具调用返回结果（Mock）",
  duration_ms: 1240,
};
