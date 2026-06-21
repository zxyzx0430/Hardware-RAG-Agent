export interface ProviderInfo {
  id: string;
  name: string;
  baseUrl: string;
  color: string;
  models: string[];
}

export interface Skill {
  name: string;
  desc: string;
  enabled: boolean;
}

export interface MCPServer {
  id: string;
  name: string;
  command: string;
  status: string;
  tools: number;
}
