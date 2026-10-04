import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MCPManager } from "./MCPManager";
import { useMCPStore } from "../../stores/useMCPStore";

const { apiDeleteMock, apiGetMock, apiPostMock } = vi.hoisted(() => ({
  apiDeleteMock: vi.fn(),
  apiGetMock: vi.fn(),
  apiPostMock: vi.fn(),
}));

vi.hoisted(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
});

vi.mock("../../api/client", () => ({
  apiDelete: apiDeleteMock,
  apiGet: apiGetMock,
  apiPost: apiPostMock,
}));

afterEach(cleanup);

beforeEach(() => {
  apiDeleteMock.mockReset();
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  useMCPStore.setState({
    servers: [],
    loading: false,
    busyKey: null,
    listError: null,
    actionErrors: {},
    toolsByServer: {},
    toolsLoading: {},
    toolsErrors: {},
  });
});

const fixtureServer = {
  id: "fixture-mcp-server",
  name: "Fixture MCP",
  command: "C:\\Fixture\\mcp-server.exe",
  status: "stopped",
  tools_count: 0,
};

describe("MCPManager", () => {
  it("sends the executable path, JSON arguments, and environment mapping as separate fields", async () => {
    const savedServer = { ...fixtureServer, id: "fixture-added-server", name: "Fixture Local" };
    apiGetMock
      .mockResolvedValueOnce({ servers: [] })
      .mockResolvedValueOnce({ servers: [savedServer] });
    apiPostMock.mockResolvedValueOnce({ id: savedServer.id });
    render(<MCPManager lang="zh" />);

    fireEvent.click(screen.getByRole("button", { name: /添加 MCP 服务/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "Fixture Local" } });
    fireEvent.change(screen.getByRole("textbox", { name: "可执行文件路径" }), {
      target: { value: "C:\\Program Files\\Fixture\\mcp-server.exe" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "参数（JSON 字符串数组）" }), {
      target: { value: "[\"--config\", \"C:\\\\Fixture Data\\\\server.json\"]" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "环境变量（JSON 字符串映射）" }), {
      target: { value: "{\"API_TOKEN\":\"synthetic-secret\"}" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /添加 MCP 服务/ }));
      await waitFor(() => expect(useMCPStore.getState().busyKey).toBeNull());
    });

    await screen.findByText("Fixture Local");
    expect(apiPostMock).toHaveBeenCalledWith("mcp/servers", expect.objectContaining({
      name: "Fixture Local",
      command: "C:\\Program Files\\Fixture\\mcp-server.exe",
      args: ["--config", "C:\\Fixture Data\\server.json"],
      env: { API_TOKEN: "synthetic-secret" },
    }));
    expect(apiGetMock).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(useMCPStore.getState())).not.toContain("synthetic-secret");
    expect(screen.queryByRole("textbox", { name: "环境变量（JSON 字符串映射）" })).toBeNull();
  });

  it("shows JSON validation beside the fields and does not send invalid values", async () => {
    apiGetMock.mockResolvedValueOnce({ servers: [] });
    render(<MCPManager lang="zh" />);

    fireEvent.click(screen.getByRole("button", { name: /添加 MCP 服务/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "Fixture Invalid" } });
    fireEvent.change(screen.getByRole("textbox", { name: "可执行文件路径" }), { target: { value: "C:\\fixture.exe" } });
    fireEvent.change(screen.getByRole("textbox", { name: "参数（JSON 字符串数组）" }), { target: { value: "[\"--ok\", 3]" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /添加 MCP 服务/ }));
      await waitFor(() => expect(useMCPStore.getState().busyKey).toBeNull());
    });

    expect(screen.getByRole("alert").textContent).toContain("请输入 JSON 字符串数组");
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("keeps the full form draft when the server rejects an add request", async () => {
    apiGetMock.mockResolvedValueOnce({ servers: [] });
    apiPostMock.mockRejectedValueOnce(new Error("fixture add failed"));
    render(<MCPManager lang="zh" />);

    fireEvent.click(screen.getByRole("button", { name: /添加 MCP 服务/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "名称" }), { target: { value: "Fixture Retry" } });
    fireEvent.change(screen.getByRole("textbox", { name: "可执行文件路径" }), { target: { value: "C:\\fixture.exe" } });
    fireEvent.change(screen.getByRole("textbox", { name: "环境变量（JSON 字符串映射）" }), {
      target: { value: "{\"TOKEN\":\"synthetic-secret\"}" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /添加 MCP 服务/ }));
      await waitFor(() => expect(useMCPStore.getState().busyKey).toBeNull());
    });

    await screen.findByText("未确认添加结果；表单内容已保留，请刷新列表确认后再重试。");
    expect((screen.getByRole("textbox", { name: "名称" }) as HTMLInputElement).value).toBe("Fixture Retry");
    expect((screen.getByRole("textbox", { name: "可执行文件路径" }) as HTMLInputElement).value).toBe("C:\\fixture.exe");
    expect((screen.getByRole("textbox", { name: "环境变量（JSON 字符串映射）" }) as HTMLTextAreaElement).value).toContain("synthetic-secret");
    expect(useMCPStore.getState().servers).toEqual([]);
    expect(JSON.stringify(useMCPStore.getState())).not.toContain("synthetic-secret");

    const firstAttemptId = (apiPostMock.mock.calls[0][1] as { id: string }).id;
    const savedServer = { ...fixtureServer, id: firstAttemptId, name: "Fixture Retry" };
    apiPostMock.mockResolvedValueOnce({ id: firstAttemptId });
    apiGetMock.mockResolvedValueOnce({ servers: [savedServer] });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "添加 MCP 服务" }));
      await waitFor(() => expect(useMCPStore.getState().busyKey).toBeNull());
    });

    await screen.findByText("Fixture Retry");
    expect((apiPostMock.mock.calls[1][1] as { id: string }).id).toBe(firstAttemptId);
  });

  it("keeps a failed start in the stopped state and shows the error beside that server", async () => {
    apiGetMock.mockResolvedValueOnce({ servers: [fixtureServer] });
    apiPostMock.mockRejectedValueOnce(new Error("fixture start failed"));
    render(<MCPManager lang="zh" />);

    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "启动" }));
      await waitFor(() => expect(useMCPStore.getState().busyKey).toBeNull());
    });

    await screen.findByText("未确认启动结果；显示状态未更改，请刷新列表确认实际状态。");
    expect(useMCPStore.getState().servers[0].status).toBe("stopped");
    expect(apiPostMock).toHaveBeenCalledWith(
      "mcp/servers/fixture-mcp-server/start",
      undefined,
      70_000,
    );
  });

  it("binds the alert dialog to the selected server and cancellation makes no DELETE request", async () => {
    apiGetMock.mockResolvedValueOnce({ servers: [fixtureServer] });
    render(<MCPManager lang="zh" />);

    fireEvent.click(await screen.findByRole("button", { name: "删除 MCP 服务：Fixture MCP" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toContain("Fixture MCP");
    expect(dialog.textContent).toContain("fixture-mcp-server");
    fireEvent.click(screen.getByRole("button", { name: "取消删除" }));

    expect(apiDeleteMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "删除 MCP 服务：Fixture MCP" })).toBeTruthy();
  });

  it("keeps the target and its error visible when DELETE fails", async () => {
    apiGetMock.mockResolvedValueOnce({ servers: [fixtureServer] });
    apiDeleteMock.mockRejectedValueOnce(new Error("fixture delete failed"));
    render(<MCPManager lang="zh" />);

    fireEvent.click(await screen.findByRole("button", { name: "删除 MCP 服务：Fixture MCP" }));
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
      await waitFor(() => expect(useMCPStore.getState().actionErrors[fixtureServer.id]).toBe("deleteServer"));
    });

    const dialogAfterFailure = screen.getByRole("alertdialog");
    expect(dialogAfterFailure.textContent).toContain("未确认删除结果；目标仍保留在当前列表和确认框中，请刷新列表确认。");
    expect(screen.getByRole("alertdialog").textContent).toContain("fixture-mcp-server");
    expect(useMCPStore.getState().servers).toEqual([fixtureServer]);
    expect(apiDeleteMock).toHaveBeenCalledWith("mcp/servers/fixture-mcp-server");
  });

  it("loads a server's tools and renders names, descriptions, and schemas", async () => {
    const tool = {
      name: "fixture_echo",
      description: "Returns fixture input",
      input_schema: { type: "object", properties: { text: { type: "string" } } },
    };
    apiGetMock
      .mockResolvedValueOnce({ servers: [{ ...fixtureServer, tools_count: 1 }] })
      .mockResolvedValueOnce({ tools: [tool] });
    render(<MCPManager lang="zh" />);

    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "查看工具" }));
      await waitFor(() => expect(useMCPStore.getState().toolsLoading[fixtureServer.id]).toBe(false));
    });

    expect(await screen.findByText("Returns fixture input")).toBeTruthy();
    expect(screen.getByText("fixture_echo")).toBeTruthy();
    expect(screen.getByText(/"properties"/)).toBeTruthy();
    expect(apiGetMock).toHaveBeenLastCalledWith("mcp/servers/fixture-mcp-server/tools");
  });

  it("shows an inline retry when fetching a tool list fails", async () => {
    apiGetMock
      .mockResolvedValueOnce({ servers: [fixtureServer] })
      .mockRejectedValueOnce(new Error("fixture tools failed"));
    render(<MCPManager lang="zh" />);

    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "查看工具" }));
      await waitFor(() => expect(useMCPStore.getState().toolsLoading[fixtureServer.id]).toBe(false));
    });

    await screen.findByText("无法读取工具列表。");
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
    await waitFor(() => expect(useMCPStore.getState().toolsLoading[fixtureServer.id]).toBe(false));
  });
});
