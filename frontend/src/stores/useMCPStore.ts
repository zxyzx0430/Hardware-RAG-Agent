import { create } from "zustand";
import { apiDelete, apiGet, apiPost } from "../api/client";

const MCP_START_TIMEOUT_MS = 70_000;

export interface MCPServer {
  id: string;
  name: string;
  command: string;
  status: string;
  tools_count: number;
}

export interface MCPTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface MCPServerConfig {
  id: string;
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
}

export type MCPErrorCode =
  | "loadServers"
  | "refreshServers"
  | "addServer"
  | "startServer"
  | "stopServer"
  | "deleteServer"
  | "loadTools";

interface MCPStoreState {
  servers: MCPServer[];
  loading: boolean;
  busyKey: string | null;
  listError: MCPErrorCode | null;
  actionErrors: Record<string, MCPErrorCode>;
  toolsByServer: Record<string, MCPTool[]>;
  toolsLoading: Record<string, boolean>;
  toolsErrors: Record<string, MCPErrorCode>;
  fetchServers: () => Promise<boolean>;
  refreshServers: () => Promise<boolean>;
  addServer: (config: MCPServerConfig) => Promise<boolean>;
  startServer: (serverId: string) => Promise<boolean>;
  stopServer: (serverId: string) => Promise<boolean>;
  deleteServer: (serverId: string) => Promise<boolean>;
  fetchTools: (serverId: string) => Promise<boolean>;
}

async function requestServers(): Promise<MCPServer[]> {
  const response = await apiGet<{ servers?: MCPServer[] }>("mcp/servers");
  if (!Array.isArray(response?.servers)) {
    throw new Error("Invalid MCP server response");
  }
  return response.servers;
}

function serverPath(serverId: string, action?: "start" | "stop" | "tools"): string {
  const encodedId = encodeURIComponent(serverId);
  return action ? "mcp/servers/" + encodedId + "/" + action : "mcp/servers/" + encodedId;
}

export const useMCPStore = create<MCPStoreState>((set, get) => ({
  servers: [],
  loading: false,
  busyKey: null,
  listError: null,
  actionErrors: {},
  toolsByServer: {},
  toolsLoading: {},
  toolsErrors: {},

  fetchServers: async () => {
    set({ loading: true, listError: null });
    try {
      const servers = await requestServers();
      set({ servers, loading: false, listError: null });
      return true;
    } catch {
      set({ loading: false, listError: "loadServers" });
      return false;
    }
  },

  refreshServers: async () => {
    try {
      const servers = await requestServers();
      set({ servers, listError: null });
      return true;
    } catch {
      set({ listError: "refreshServers" });
      return false;
    }
  },

  addServer: async (config) => {
    set({ busyKey: "add" });
    set((state) => {
      const actionErrors = { ...state.actionErrors };
      delete actionErrors.add;
      return { actionErrors };
    });
    try {
      await apiPost("mcp/servers", config);
    } catch {
      set((state) => ({
        actionErrors: { ...state.actionErrors, add: "addServer" },
      }));
      return false;
    } finally {
      set({ busyKey: null });
    }

    // The POST has succeeded. This server is registered but has not been started.
    set((state) => ({
      servers: [
        { id: config.id, name: config.name, command: config.command, status: "stopped", tools_count: 0 },
        ...state.servers.filter((server) => server.id !== config.id),
      ],
    }));
    await get().refreshServers();
    return true;
  },

  startServer: async (serverId) => {
    set({ busyKey: serverId });
    set((state) => {
      const actionErrors = { ...state.actionErrors };
      delete actionErrors[serverId];
      return { actionErrors };
    });
    try {
      await apiPost(serverPath(serverId, "start"), undefined, MCP_START_TIMEOUT_MS);
    } catch {
      set((state) => ({
        actionErrors: { ...state.actionErrors, [serverId]: "startServer" },
      }));
      return false;
    } finally {
      set({ busyKey: null });
    }

    set((state) => ({
      servers: state.servers.map((server) =>
        server.id === serverId ? { ...server, status: "running" } : server,
      ),
    }));
    await get().refreshServers();
    return true;
  },

  stopServer: async (serverId) => {
    set({ busyKey: serverId });
    set((state) => {
      const actionErrors = { ...state.actionErrors };
      delete actionErrors[serverId];
      return { actionErrors };
    });
    try {
      await apiPost(serverPath(serverId, "stop"));
    } catch {
      set((state) => ({
        actionErrors: { ...state.actionErrors, [serverId]: "stopServer" },
      }));
      return false;
    } finally {
      set({ busyKey: null });
    }

    set((state) => {
      const toolsByServer = { ...state.toolsByServer, [serverId]: [] };
      const toolsErrors = { ...state.toolsErrors };
      delete toolsErrors[serverId];
      return {
        servers: state.servers.map((server) =>
          server.id === serverId ? { ...server, status: "stopped", tools_count: 0 } : server,
        ),
        toolsByServer,
        toolsErrors,
      };
    });
    await get().refreshServers();
    return true;
  },

  deleteServer: async (serverId) => {
    set({ busyKey: serverId });
    set((state) => {
      const actionErrors = { ...state.actionErrors };
      delete actionErrors[serverId];
      return { actionErrors };
    });
    try {
      await apiDelete(serverPath(serverId));
    } catch {
      set((state) => ({
        actionErrors: { ...state.actionErrors, [serverId]: "deleteServer" },
      }));
      return false;
    } finally {
      set({ busyKey: null });
    }

    set((state) => {
      const toolsByServer = { ...state.toolsByServer };
      const toolsLoading = { ...state.toolsLoading };
      const toolsErrors = { ...state.toolsErrors };
      const actionErrors = { ...state.actionErrors };
      delete toolsByServer[serverId];
      delete toolsLoading[serverId];
      delete toolsErrors[serverId];
      delete actionErrors[serverId];
      return {
        servers: state.servers.filter((server) => server.id !== serverId),
        toolsByServer,
        toolsLoading,
        toolsErrors,
        actionErrors,
      };
    });
    await get().refreshServers();
    return true;
  },

  fetchTools: async (serverId) => {
    set((state) => {
      const toolsErrors = { ...state.toolsErrors };
      delete toolsErrors[serverId];
      return {
        toolsLoading: { ...state.toolsLoading, [serverId]: true },
        toolsErrors,
      };
    });
    try {
      const response = await apiGet<{ tools?: MCPTool[] }>(serverPath(serverId, "tools"));
      if (!Array.isArray(response?.tools)) {
        throw new Error("Invalid MCP tools response");
      }
      set((state) => ({
        toolsByServer: { ...state.toolsByServer, [serverId]: response.tools ?? [] },
        toolsLoading: { ...state.toolsLoading, [serverId]: false },
      }));
      return true;
    } catch {
      set((state) => ({
        toolsLoading: { ...state.toolsLoading, [serverId]: false },
        toolsErrors: { ...state.toolsErrors, [serverId]: "loadTools" },
      }));
      return false;
    }
  },
}));
