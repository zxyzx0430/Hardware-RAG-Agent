import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiDeleteMock, apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPost: apiPostMock,
}));

async function loadStore() {
  const { useMCPStore } = await import("./useMCPStore");
  return useMCPStore;
}

const fixtureServer = {
  id: "fixture-mcp-server",
  name: "Fixture server",
  command: "C:\\fixture\\mcp-server.exe",
  status: "stopped",
  tools_count: 0,
};

describe("MCP API state", () => {
  beforeEach(() => {
    vi.resetModules();
    apiDeleteMock.mockReset();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
  });

  it("loads the server list from the API", async () => {
    apiGetMock.mockResolvedValueOnce({ servers: [fixtureServer] });
    const store = await loadStore();

    await expect(store.getState().fetchServers()).resolves.toBe(true);

    expect(apiGetMock).toHaveBeenCalledWith("mcp/servers");
    expect(store.getState().servers).toEqual([fixtureServer]);
    expect(store.getState().loading).toBe(false);
  });

  it("sends parsed arguments and environment only with the successful add request", async () => {
    const config = {
      id: "fixture-new-server",
      name: "Fixture new server",
      command: "C:\\Program Files\\Fixture\\server.exe",
      args: ["--config", "C:\\Fixture Data\\server.json"],
      env: { API_TOKEN: "synthetic-secret" },
    };
    apiPostMock.mockResolvedValueOnce({ id: config.id });
    apiGetMock.mockResolvedValueOnce({
      servers: [{ ...fixtureServer, id: config.id, name: config.name, command: config.command }],
    });
    const store = await loadStore();

    await expect(store.getState().addServer(config)).resolves.toBe(true);

    expect(apiPostMock).toHaveBeenCalledWith("mcp/servers", config);
    expect(apiGetMock).toHaveBeenCalledWith("mcp/servers");
    expect(store.getState().servers[0].status).toBe("stopped");
    expect(JSON.stringify(store.getState())).not.toContain("synthetic-secret");
  });

  it("keeps the list unchanged and stores no submitted secrets when adding fails", async () => {
    const store = await loadStore();
    store.setState({ servers: [fixtureServer] });
    apiPostMock.mockRejectedValueOnce(new Error("synthetic-secret was rejected"));

    await expect(store.getState().addServer({
      id: "fixture-failed-server",
      name: "Fixture failed server",
      command: "C:\\fixture\\server.exe",
      args: [],
      env: { TOKEN: "synthetic-secret" },
    })).resolves.toBe(false);

    expect(store.getState().servers).toEqual([fixtureServer]);
    expect(store.getState().actionErrors.add).toBe("addServer");
    expect(JSON.stringify(store.getState())).not.toContain("synthetic-secret");
    expect(store.getState().busyKey).toBeNull();
  });

  it("waits for server confirmation and uses the 70 second start timeout", async () => {
    const store = await loadStore();
    store.setState({ servers: [fixtureServer] });
    let resolveStart: (() => void) | undefined;
    apiPostMock.mockImplementationOnce(() => new Promise<void>((resolve) => {
      resolveStart = resolve;
    }));
    apiGetMock.mockResolvedValueOnce({ servers: [{ ...fixtureServer, status: "running" }] });

    const startRequest = store.getState().startServer(fixtureServer.id);
    await Promise.resolve();
    expect(store.getState().servers[0].status).toBe("stopped");
    expect(store.getState().busyKey).toBe(fixtureServer.id);

    resolveStart?.();
    await expect(startRequest).resolves.toBe(true);

    expect(apiPostMock).toHaveBeenCalledWith(
      "mcp/servers/fixture-mcp-server/start",
      undefined,
      70_000,
    );
    expect(store.getState().servers[0].status).toBe("running");
  });

  it("does not change the displayed status when a start request fails", async () => {
    const store = await loadStore();
    store.setState({ servers: [fixtureServer] });
    apiPostMock.mockRejectedValueOnce(new Error("fixture start failure"));

    await expect(store.getState().startServer(fixtureServer.id)).resolves.toBe(false);

    expect(store.getState().servers[0].status).toBe("stopped");
    expect(store.getState().actionErrors[fixtureServer.id]).toBe("startServer");
  });

  it("retains a running server when stopping fails", async () => {
    const runningServer = { ...fixtureServer, status: "running", tools_count: 2 };
    const store = await loadStore();
    store.setState({ servers: [runningServer] });
    apiPostMock.mockRejectedValueOnce(new Error("fixture stop failure"));

    await expect(store.getState().stopServer(runningServer.id)).resolves.toBe(false);

    expect(apiPostMock).toHaveBeenCalledWith("mcp/servers/fixture-mcp-server/stop");
    expect(store.getState().servers).toEqual([runningServer]);
    expect(store.getState().actionErrors[runningServer.id]).toBe("stopServer");
  });

  it("preserves the server when delete fails and removes it only after success", async () => {
    const store = await loadStore();
    store.setState({ servers: [fixtureServer] });
    apiDeleteMock.mockRejectedValueOnce(new Error("fixture delete failure"));

    await expect(store.getState().deleteServer(fixtureServer.id)).resolves.toBe(false);

    expect(store.getState().servers).toEqual([fixtureServer]);
    expect(store.getState().actionErrors[fixtureServer.id]).toBe("deleteServer");

    apiDeleteMock.mockResolvedValueOnce({ success: true });
    apiGetMock.mockResolvedValueOnce({ servers: [] });
    await expect(store.getState().deleteServer(fixtureServer.id)).resolves.toBe(true);

    expect(apiDeleteMock).toHaveBeenCalledWith("mcp/servers/fixture-mcp-server");
    expect(store.getState().servers).toEqual([]);
  });

  it("loads tool descriptions and reports tool-list failures without logging server data", async () => {
    const tool = {
      name: "fixture_echo",
      description: "Returns fixture input",
      input_schema: { type: "object", properties: { text: { type: "string" } } },
    };
    const store = await loadStore();
    apiGetMock.mockResolvedValueOnce({ tools: [tool] });

    await expect(store.getState().fetchTools(fixtureServer.id)).resolves.toBe(true);

    expect(apiGetMock).toHaveBeenCalledWith("mcp/servers/fixture-mcp-server/tools");
    expect(store.getState().toolsByServer[fixtureServer.id]).toEqual([tool]);
    expect(store.getState().toolsLoading[fixtureServer.id]).toBe(false);

    apiGetMock.mockRejectedValueOnce(new Error("fixture tool-list failure"));
    await expect(store.getState().fetchTools(fixtureServer.id)).resolves.toBe(false);
    expect(store.getState().toolsErrors[fixtureServer.id]).toBe("loadTools");
  });
});
