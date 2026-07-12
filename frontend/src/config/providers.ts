/**
 * LLM Provider 配置。
 * 预设厂商列表已移除——用户在设置页自建服务商（name + baseUrl + apiKey）。
 * 保留 ProviderInfo 类型供旧代码兼容引用。
 */

export interface ProviderInfo {
  id: string;
  name: string;
  color: string;
}

/** 预设厂商列表为空：全部由用户自建 */
export const PROVIDERS: ProviderInfo[] = [];

/** provider id → 显示名映射（从自建 providers 派生，由调用方填充） */
export const PROVIDER_DISPLAY_NAMES: Record<string, string> = {};
