import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useMCPStore, type MCPErrorCode, type MCPServer } from "../../stores/useMCPStore";

interface MCPManagerProps {
  lang: string;
}

interface DraftErrors {
  args: string | null;
  env: string | null;
}

function errorText(code: MCPErrorCode, isChinese: boolean): string {
  const messages: Record<MCPErrorCode, [string, string]> = {
    loadServers: ["无法读取 MCP 服务列表。", "Could not load MCP servers."],
    refreshServers: ["操作已由服务器确认，但列表刷新失败。", "The server confirmed the action, but the list could not be refreshed."],
    addServer: ["未确认添加结果；表单内容已保留，请刷新列表确认后再重试。", "The add result was not confirmed. Your form values were kept; refresh the list before retrying."],
    startServer: ["未确认启动结果；显示状态未更改，请刷新列表确认实际状态。", "The start result was not confirmed. The displayed status was left unchanged; refresh to check the actual status."],
    stopServer: ["未确认停止结果；显示状态未更改，请刷新列表确认实际状态。", "The stop result was not confirmed. The displayed status was left unchanged; refresh to check the actual status."],
    deleteServer: ["未确认删除结果；目标仍保留在当前列表和确认框中，请刷新列表确认。", "The delete result was not confirmed. The target remains in this list and dialog; refresh to check the actual state."],
    loadTools: ["无法读取工具列表。", "Could not load the tool list."],
  };
  return messages[code][isChinese ? 0 : 1];
}

function parseArgs(value: string): string[] | null {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) && parsed.every((item) => typeof item === "string") ? parsed : null;
  } catch {
    return null;
  }
}

function parseEnv(value: string): Record<string, string> | null {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const entries = Object.entries(parsed);
    return entries.every(([, item]) => typeof item === "string")
      ? Object.fromEntries(entries) as Record<string, string>
      : null;
  } catch {
    return null;
  }
}

function createServerId(): string {
  const randomId = globalThis.crypto?.randomUUID?.();
  return randomId ? "mcp-" + randomId : "mcp-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

export function MCPManager({ lang }: MCPManagerProps) {
  const isChinese = lang === "zh";
  const servers = useMCPStore((state) => state.servers);
  const loading = useMCPStore((state) => state.loading);
  const busyKey = useMCPStore((state) => state.busyKey);
  const listError = useMCPStore((state) => state.listError);
  const actionErrors = useMCPStore((state) => state.actionErrors);
  const toolsByServer = useMCPStore((state) => state.toolsByServer);
  const toolsLoading = useMCPStore((state) => state.toolsLoading);
  const toolsErrors = useMCPStore((state) => state.toolsErrors);
  const fetchServers = useMCPStore((state) => state.fetchServers);
  const addServer = useMCPStore((state) => state.addServer);
  const startServer = useMCPStore((state) => state.startServer);
  const stopServer = useMCPStore((state) => state.stopServer);
  const deleteServer = useMCPStore((state) => state.deleteServer);
  const fetchTools = useMCPStore((state) => state.fetchTools);

  const [formOpen, setFormOpen] = useState(false);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [serverName, setServerName] = useState("");
  const [command, setCommand] = useState("");
  const [argsJson, setArgsJson] = useState("[]");
  const [envJson, setEnvJson] = useState("{}");
  const [draftErrors, setDraftErrors] = useState<DraftErrors>({ args: null, env: null });
  const [deleteTarget, setDeleteTarget] = useState<MCPServer | null>(null);
  const [expandedServerId, setExpandedServerId] = useState<string | null>(null);

  useEffect(() => {
    void fetchServers();
  }, [fetchServers]);

  const resetDraft = () => {
    setDraftId(null);
    setServerName("");
    setCommand("");
    setArgsJson("[]");
    setEnvJson("{}");
    setDraftErrors({ args: null, env: null });
  };

  const handleAddServer = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const args = parseArgs(argsJson);
    const env = parseEnv(envJson);
    setDraftErrors({
      args: args === null ? (isChinese ? "请输入 JSON 字符串数组，例如 [\"--port\", \"3000\"]。" : "Enter a JSON array of strings, for example [\"--port\", \"3000\"].") : null,
      env: env === null ? (isChinese ? "请输入 JSON 字符串映射，例如 {\"API_TOKEN\":\"...\"}。" : "Enter a JSON string mapping, for example {\"API_TOKEN\":\"...\"}.") : null,
    });
    if (args === null || env === null || !serverName.trim() || !command.trim()) return;

    const id = draftId ?? createServerId();
    if (draftId === null) setDraftId(id);
    const saved = await addServer({
      id,
      name: serverName.trim(),
      command: command.trim(),
      args,
      env,
    });
    if (!saved) return;
    resetDraft();
    setFormOpen(false);
  };

  const handleServerAction = async (server: MCPServer) => {
    const succeeded = server.status === "running"
      ? await stopServer(server.id)
      : await startServer(server.id);
    if (succeeded && expandedServerId === server.id && server.status !== "running") {
      void fetchTools(server.id);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    const deleted = await deleteServer(deleteTarget.id);
    if (deleted) {
      if (expandedServerId === deleteTarget.id) setExpandedServerId(null);
      setDeleteTarget(null);
    }
  };

  return (
    <div className="settings-section">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <h3 style={{ marginBottom: 8 }}>{isChinese ? "MCP 服务" : "MCP servers"}</h3>
        <button type="button" className="verify-btn" onClick={() => void fetchServers()} disabled={loading || busyKey !== null}>
          {isChinese ? "刷新列表" : "Refresh"}
        </button>
      </div>
      <p style={{ fontSize: 13, color: "var(--muted-fg)", marginBottom: 12 }}>
        {isChinese
          ? "添加并管理本机 stdio MCP 服务。配置保存在后端进程内存中；刷新页面可重新读取，后端重启后需要重新添加。"
          : "Add and manage local stdio MCP servers. Configuration lives in backend process memory, so it can be reloaded after a page refresh and must be added again after a backend restart."}
      </p>
      <div role="note" style={{ border: "1px solid var(--border)", borderLeft: "3px solid var(--primary)", borderRadius: "var(--radius-md)", padding: "10px 12px", marginBottom: 16, color: "var(--muted-fg)", fontSize: 12 }}>
        {isChinese
          ? "启动服务会在本机创建进程，不受 sandbox 隔离。MCP 工具每次调用仍需用户确认，即使启用 bypassPermissions 也不会跳过；只读 Skills 不会开放外部 MCP 工具。"
          : "Starting a server creates a process on this computer and is not sandboxed. Every MCP tool call still requires user confirmation, including when bypassPermissions is enabled. Read-only Skills do not expose external MCP tools."}
      </div>

      {listError && (
        <p role="alert" style={{ color: "var(--danger)", fontSize: 12, marginBottom: 12 }}>
          {errorText(listError, isChinese)}
        </p>
      )}

      <div aria-busy={loading}>
        {loading ? (
          <p role="status" style={{ color: "var(--muted-fg)", fontSize: 13 }}>
            {isChinese ? "正在读取 MCP 服务…" : "Loading MCP servers…"}
          </p>
        ) : servers.length === 0 ? (
          <p style={{ color: "var(--muted-fg)", fontSize: 13, margin: "12px 0" }}>
            {isChinese ? "尚未添加 MCP 服务。添加本机已存在的可执行程序路径即可开始。" : "No MCP servers yet. Add the path to an executable that is already on this computer."}
          </p>
        ) : (
          servers.map((server) => {
            const running = server.status === "running";
            const expanded = expandedServerId === server.id;
            const serverBusy = busyKey === server.id;
            const serverError = actionErrors[server.id];
            const tools = toolsByServer[server.id] ?? [];
            const toolsRegionId = "mcp-tools-" + encodeURIComponent(server.id);
            return (
              <div className="mcp-card" key={server.id}>
                <div className="mcp-row">
                  <div className="mcp-left" style={{ minWidth: 0, flexWrap: "wrap" }}>
                    <span className={"mcp-dot " + (running ? "running" : "stopped")} aria-hidden="true"></span>
                    <span className="mcp-name">{server.name}</span>
                    <span className="mcp-badge" style={{ fontVariantNumeric: "tabular-nums" }}>
                      {server.tools_count} {isChinese ? "个工具" : "tools"}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                    <button type="button" className={"verify-btn " + (running ? "danger" : "primary")} onClick={() => void handleServerAction(server)} disabled={busyKey !== null}>
                      {serverBusy
                        ? (isChinese ? "处理中…" : "Working…")
                        : running
                          ? (isChinese ? "停止" : "Stop")
                          : (isChinese ? "启动" : "Start")}
                    </button>
                    <button type="button" className="verify-btn" onClick={() => {
                      if (expanded) {
                        setExpandedServerId(null);
                      } else {
                        setExpandedServerId(server.id);
                        void fetchTools(server.id);
                      }
                    }} aria-expanded={expanded} aria-controls={toolsRegionId}>
                      {expanded
                        ? (isChinese ? "隐藏工具" : "Hide tools")
                        : (isChinese ? "查看工具" : "View tools")}
                    </button>
                    <button type="button" className="verify-btn danger" onClick={() => setDeleteTarget(server)} aria-label={(isChinese ? "删除 MCP 服务：" : "Delete MCP server: ") + server.name} disabled={busyKey !== null}>
                      {isChinese ? "删除" : "Delete"}
                    </button>
                  </div>
                </div>
                <div className="mcp-command" title={server.command}>{server.command}</div>
                {serverError && (
                  <p role="alert" style={{ color: "var(--danger)", fontSize: 12, margin: 0 }}>
                    {errorText(serverError, isChinese)}
                  </p>
                )}
                <div id={toolsRegionId} role="region" aria-label={(isChinese ? "MCP 工具：" : "MCP tools: ") + server.name} hidden={!expanded} style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                    {toolsLoading[server.id] ? (
                      <p role="status" style={{ color: "var(--muted-fg)", fontSize: 12 }}>
                        {isChinese ? "正在读取工具…" : "Loading tools…"}
                      </p>
                    ) : toolsErrors[server.id] ? (
                      <div>
                        <p role="alert" style={{ color: "var(--danger)", fontSize: 12 }}>
                          {errorText(toolsErrors[server.id], isChinese)}
                        </p>
                        <button type="button" className="verify-btn" onClick={() => void fetchTools(server.id)}>
                          {isChinese ? "重试" : "Retry"}
                        </button>
                      </div>
                    ) : tools.length === 0 ? (
                      <p style={{ color: "var(--muted-fg)", fontSize: 12 }}>
                        {isChinese ? "当前没有可用工具。" : "No tools are currently available."}
                      </p>
                    ) : (
                      tools.map((tool) => (
                        <div key={tool.name} style={{ padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                          <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600 }}>{tool.name}</div>
                          {tool.description && <p style={{ color: "var(--muted-fg)", fontSize: 12, margin: "4px 0" }}>{tool.description}</p>}
                          <details style={{ color: "var(--muted-fg)", fontSize: 11 }}>
                            <summary>{isChinese ? "输入参数结构" : "Input schema"}</summary>
                            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontFamily: "var(--font-mono)" }}>{JSON.stringify(tool.input_schema, null, 2)}</pre>
                          </details>
                        </div>
                      ))
                    )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {formOpen ? (
        <form onSubmit={(event) => void handleAddServer(event)} style={{ marginTop: 12, padding: 16, border: "1px solid var(--border)", borderRadius: "var(--radius-md)", background: "var(--thinking-bg)" }}>
          <label className="field-label" htmlFor="mcp-server-name">{isChinese ? "名称" : "Name"}</label>
          <input id="mcp-server-name" className="form-input" value={serverName} onChange={(event) => setServerName(event.target.value)} required autoComplete="off" />

          <label className="field-label" htmlFor="mcp-server-command" style={{ marginTop: 10 }}>
            {isChinese ? "可执行文件路径" : "Executable path"}
          </label>
          <input id="mcp-server-command" className="form-input" value={command} onChange={(event) => setCommand(event.target.value)} placeholder={isChinese ? "例如：C:\\tools\\mcp-server.exe" : "e.g. C:\\tools\\mcp-server.exe"} required autoComplete="off" spellCheck={false} />
          <p id="mcp-command-help" style={{ color: "var(--muted-fg)", fontSize: 11, margin: "4px 0 10px" }}>
            {isChinese ? "填写本机已存在的程序路径；这里不会按 shell 字符串切分，也不会自动安装程序。" : "Use an executable that already exists locally. The command is not shell-split and no program is installed automatically."}
          </p>

          <label className="field-label" htmlFor="mcp-server-args">{isChinese ? "参数（JSON 字符串数组）" : "Arguments (JSON string array)"}</label>
          <textarea id="mcp-server-args" className="settings-textarea" rows={3} value={argsJson} onChange={(event) => { setArgsJson(event.target.value); setDraftErrors((current) => ({ ...current, args: null })); }} aria-invalid={Boolean(draftErrors.args)} aria-describedby={draftErrors.args ? "mcp-args-error" : "mcp-args-help"} spellCheck={false} />
          {draftErrors.args ? (
            <p id="mcp-args-error" role="alert" style={{ color: "var(--danger)", fontSize: 11, marginTop: 4 }}>{draftErrors.args}</p>
          ) : (
            <p id="mcp-args-help" style={{ color: "var(--muted-fg)", fontSize: 11, marginTop: 4 }}>{isChinese ? "默认 []。每项必须是字符串，不会对参数执行 shell 分词。" : "Defaults to []. Every item must be a string; arguments are not shell-split."}</p>
          )}

          <label className="field-label" htmlFor="mcp-server-env" style={{ marginTop: 10 }}>{isChinese ? "环境变量（JSON 字符串映射）" : "Environment (JSON string mapping)"}</label>
          <textarea id="mcp-server-env" className="settings-textarea" rows={4} value={envJson} onChange={(event) => { setEnvJson(event.target.value); setDraftErrors((current) => ({ ...current, env: null })); }} aria-invalid={Boolean(draftErrors.env)} aria-describedby={draftErrors.env ? "mcp-env-error" : "mcp-env-help"} spellCheck={false} />
          {draftErrors.env ? (
            <p id="mcp-env-error" role="alert" style={{ color: "var(--danger)", fontSize: 11, marginTop: 4 }}>{draftErrors.env}</p>
          ) : (
            <p id="mcp-env-help" style={{ color: "var(--muted-fg)", fontSize: 11, marginTop: 4 }}>{isChinese ? "默认 {}。值必须是字符串；环境变量仅在当前表单内存中暂存，不写入前端持久化存储或日志。" : "Defaults to {}. Values must be strings. Environment values stay in this form's memory and are not persisted by the frontend or written to its logs."}</p>
          )}

          {actionErrors.add && <p role="alert" style={{ color: "var(--danger)", fontSize: 12 }}>{errorText(actionErrors.add, isChinese)}</p>}
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <button type="submit" className="verify-btn primary" disabled={busyKey !== null || !serverName.trim() || !command.trim()}>
              {busyKey === "add" ? (isChinese ? "正在添加…" : "Adding…") : (isChinese ? "添加 MCP 服务" : "Add MCP server")}
            </button>
            <button type="button" className="verify-btn" onClick={() => { setFormOpen(false); resetDraft(); }} disabled={busyKey !== null}>
              {isChinese ? "取消" : "Cancel"}
            </button>
          </div>
        </form>
      ) : (
        <button type="button" className="mcp-add" onClick={() => setFormOpen(true)}>
          + {isChinese ? "添加 MCP 服务" : "Add MCP server"}
        </button>
      )}

      {deleteTarget && (
        <div role="alertdialog" aria-modal="true" aria-labelledby="mcp-delete-title" aria-describedby="mcp-delete-description" style={{ position: "fixed", inset: 0, zIndex: 1100, background: "rgba(0,0,0,.45)", display: "grid", placeItems: "center", padding: 20 }}>
          <div style={{ width: "min(440px, 100%)", padding: 18, borderRadius: 10, border: "1px solid var(--border)", background: "var(--bg)" }}>
            <h3 id="mcp-delete-title" style={{ marginBottom: 8 }}>{isChinese ? "确认删除 MCP 服务" : "Delete this MCP server?"}</h3>
            <p id="mcp-delete-description" style={{ fontSize: 13 }}>
              {isChinese ? "将停止并从本机配置中删除：" : "This will stop and remove the local configuration for "}
              <strong>{deleteTarget.name}</strong>
            </p>
            <p style={{ fontSize: 11, color: "var(--muted-fg)", overflowWrap: "anywhere" }}>
              {isChinese ? "服务 ID：" : "Server ID: "}<code>{deleteTarget.id}</code>
            </p>
            {actionErrors[deleteTarget.id] && (
              <p role="alert" style={{ color: "var(--danger)", fontSize: 12 }}>
                {errorText(actionErrors[deleteTarget.id], isChinese)}
              </p>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
              <button type="button" className="verify-btn" onClick={() => setDeleteTarget(null)} disabled={busyKey === deleteTarget.id}>
                {isChinese ? "取消删除" : "Cancel"}
              </button>
              <button type="button" className="verify-btn danger" onClick={() => void handleDelete()} disabled={busyKey !== null}>
                {busyKey === deleteTarget.id
                  ? (isChinese ? "正在删除…" : "Deleting…")
                  : (isChinese ? "确认删除" : "Confirm delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
