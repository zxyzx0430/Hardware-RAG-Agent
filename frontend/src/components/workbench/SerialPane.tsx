import { useEffect, useMemo, useState, useRef, useCallback } from "react";
import DOMPurify from "dompurify";
import { useSerialStore } from "../../stores/useSerialStore";
import { useLogStore } from "../../stores/useLogStore";
import { useModalStore } from "../../stores/useModalStore";
import { useI18n } from "../../i18n";
import { apiGet, apiWS } from "../../api/client";
import { parseAnsiToHtml } from "./parseAnsiToHtml";
import { EmptyState } from "../shared/EmptyState";
import type { SerialDevice } from "../../types/serial";

// Baud rate options (extracted constant, avoids hardcoding in component)
const BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];

function nowHHMMSSmmm() {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const mmm = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${mmm}`;
}

export function SerialPane() {
  const { t } = useI18n();
  const { connected, port, baudRate, log, autoScroll, dtrActive, rtsActive, filter, lineEnding, autoConnectPort, setConnected, setPort, setBaudRate, addLog, clearLog, setAutoScroll, toggleDtr, toggleRts, setFilter, setLineEnding, setAutoConnectPort } = useSerialStore();
  const { confirmDialog } = useModalStore();
  const [sendText, setSendText] = useState("");
  const [devices, setDevices] = useState<SerialDevice[]>([]);
  const sendInputRef = useRef<HTMLInputElement>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // On mount, fetch real device list
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiGet<{ devices: SerialDevice[] }>("devices");
        if (!cancelled) {
          setDevices(data?.devices ?? []);
          if (data?.devices?.length) {
            useLogStore.getState().log("ok", "serial", `扫描到 ${data.devices.length} 个串口设备`);
          }
        }
      } catch {
        if (cancelled) return;
        setDevices([]);
        useLogStore.getState().log("warn", "serial", "未扫描到串口设备");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const seed = async () => {
    const ok = await confirmDialog({
      title: '注入演示数据',
      message: '将向串口日志注入演示数据（非真实串口数据），仅用于预览界面效果。是否继续？',
    });
    if (!ok) return;
    [
      '[DEMO] 14:32:02.157 AT',
      '[DEMO] 14:32:02.359 OK',
      '[DEMO] 14:32:03.112 AT+CWJAP="WiFi-2.4G","password123"',
      '[DEMO] 14:32:03.583 WIFI CONNECTING',
      '[DEMO] 14:32:06.902 WIFI CONNECTED',
      '[DEMO] 14:32:07.015 WIFI GOT IP',
      '[DEMO] 14:32:07.110 192.168.1.104',
      '[DEMO] 14:32:08.441 AT+CIPSTART="TCP","api.thingspeak.com",80',
      '[DEMO] 14:32:08.902 CONNECT',
    ].forEach(addLog);
  };

  const handleConnect = useCallback((portOverride?: string) => {
    if (connected) {
      // Disconnect
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
      useLogStore.getState().log("info", "serial", "断开串口连接");
      return;
    }
    // Try WebSocket connection; portOverride wins over store port
    const portName = portOverride || port || devices[0]?.port;
    if (!portName) {
      useLogStore.getState().log("warn", "serial", "请先选择串口设备");
      return;
    }
    useLogStore.getState().log("info", "serial", `连接串口: ${portName} @ ${baudRate}`);
    const ws = apiWS(`/api/monitor/${portName}?baud=${baudRate}`, {
      onOpen: () => {
        setConnected(true);
        ws.send(JSON.stringify({ type: "start" }));
      },
      onMessage: (data) => {
        try {
          const msg = JSON.parse(data);
          if (msg.type === "error") {
            useLogStore.getState().log("error", "serial", msg.message || "串口异常");
            setConnected(false);
            wsRef.current?.close();
            return;
          }
          if (msg.type === 'data') addLog(`${nowHHMMSSmmm()} ${msg.payload}`);
          else addLog(`${nowHHMMSSmmm()} ${data}`);
        } catch {
          addLog(`${nowHHMMSSmmm()} ${data}`);
        }
      },
      onClose: () => {
        setConnected(false);
        wsRef.current = null;
      },
      onError: () => {
        setConnected(false);
        wsRef.current = null;
        useLogStore.getState().log("error", "serial", `串口连接失败: ${portName} @ ${baudRate}`);
      },
    });
    wsRef.current = ws;
  }, [connected, port, devices, baudRate, setConnected, addLog]);

  // Auto-connect when another pane (e.g. FlashPane after flash success) sets autoConnectPort.
  // Clears the flag first to avoid re-trigger, syncs store port, then invokes handleConnect.
  useEffect(() => {
    if (!autoConnectPort) return;
    setAutoConnectPort(null);
    if (connected) return;
    setPort(autoConnectPort);
    handleConnect(autoConnectPort);
  }, [autoConnectPort, connected, handleConnect, setPort, setAutoConnectPort]);

  const handleSend = useCallback(() => {
    const text = sendText.trim();
    if (!text) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const suffix = lineEnding === "none" ? "" : lineEnding;
      const payload = text + suffix;
      wsRef.current.send(JSON.stringify({ type: "write", payload }));
      useLogStore.getState().log("debug", "serial", `发送: ${text.slice(0, 30)}`);
    } else {
      useLogStore.getState().log("error", "serial", "串口未连接，无法发送");
    }
    setSendText("");
    sendInputRef.current?.focus();
  }, [sendText, lineEnding]);

  const handleSendKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  // DTR/RTS 真实控制：先 toggle store（保留变色），再发 WS 消息给后端 set_dtr/set_rts
  const handleToggleDtr = useCallback((): void => {
    toggleDtr();
    const newDtr: boolean = !dtrActive;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_dtr", payload: newDtr }));
    } else {
      useLogStore.getState().log("error", "serial", "串口未连接，无法切换 DTR");
    }
  }, [dtrActive, toggleDtr]);

  const handleToggleRts = useCallback((): void => {
    toggleRts();
    const newRts: boolean = !rtsActive;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_rts", payload: newRts }));
    } else {
      useLogStore.getState().log("error", "serial", "串口未连接，无法切换 RTS");
    }
  }, [rtsActive, toggleRts]);

  const filteredLog = useMemo(() => {
    if (!filter) return log;
    const lower = filter.toLowerCase();
    return log.filter((line) => line.toLowerCase().includes(lower));
  }, [log, filter]);

  const handleExport = useCallback(() => {
    const content = filteredLog.join("\n");
    const blob = new Blob([content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `serial-log-${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }, [filteredLog]);

  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [filteredLog, autoScroll]);

  return (
    <div className="serial-monitor">
      <div className="serial-toolbar">
        <select id="serialPortSelect" title="后缀为串口设备制造商（驱动提供商），括号内为 VID/PID 标识" value={port || devices[0]?.port || ""} onChange={(e) => setPort(e.target.value)}>{devices.length === 0 ? <option value="" disabled>未扫描到串口设备</option> : devices.map((d) => {
          const label = d.manufacturer || d.description;
          const vidPid = d.vid != null && d.pid != null
            ? ` (VID:${d.vid.toString(16).toUpperCase().padStart(4, "0")} PID:${d.pid.toString(16).toUpperCase().padStart(4, "0")})`
            : "";
          return <option key={d.port} value={d.port} title={`端口：${d.port}｜制造商/驱动：${label}${vidPid ? `｜标识：${vidPid}` : ""}`}>{d.port} — {label}{vidPid}</option>;
        })}</select>
        <select id="serialBaudSelect" value={String(baudRate)} onChange={(e) => setBaudRate(parseInt(e.target.value))}>{BAUD_RATES.map((b) => <option key={b} value={b}>{b}</option>)}</select>
        <button className={`serial-connect-btn ${connected ? "on" : "off"}`} onClick={() => handleConnect()}>{connected ? t('disconnect') : t('connect')}</button>
        <button className="serial-ctrl-btn" onClick={handleToggleDtr} style={dtrActive ? { background: "var(--accent)", color: "var(--primary-fg)" } : undefined}>DTR</button>
        <button className="serial-ctrl-btn" onClick={handleToggleRts} style={rtsActive ? { background: "var(--accent)", color: "var(--primary-fg)" } : undefined}>RTS</button>
      </div>
      <div className="serial-log" id="serialLog" ref={logContainerRef}>
        {filteredLog.length ? filteredLog.map((line, idx) => {
          const timePart = line.split(' ')[0];
          const msgPart = line.slice(line.indexOf(' ') + 1);
          const hasAnsi = /\x1b\[\d+m/.test(msgPart);
          if (hasAnsi) {
            return (
              <div className="log-line log-recv" key={idx}>
                <span className="log-time">{timePart}</span>
                <span dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(parseAnsiToHtml(msgPart), { ALLOWED_TAGS: ['b','i','em','strong','code','pre','br','p','span','div','ul','ol','li','a','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td'], ALLOWED_ATTR: ['href','class','style','colspan','rowspan'] }) }} />
              </div>
            );
          }
          return (
            <div className="log-line log-recv" key={idx}>
              <span className="log-time">{timePart}</span>
              <span>{msgPart}</span>
            </div>
          );
        }) : (
          <EmptyState
            size="md"
            title={t('serialConnectHint')}
            icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 2v6M15 2v6M7 8h10v6a5 5 0 0 1-10 0z" /><path d="M12 19v3" strokeLinecap="round" /></svg>}
            action={!connected && (
              <button className="serial-clear" onClick={seed} aria-label="注入演示数据" title="注入演示数据（非真实串口数据）" style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                注入演示数据
              </button>
            )}
          />
        )}
      </div>
      <div className="serial-send-area">
        <input id="serialSendInput" ref={sendInputRef} placeholder={t('serialSendPlaceholder')} value={sendText} onChange={(e) => setSendText(e.target.value)} onKeyDown={handleSendKeyDown} />
        <select value={lineEnding} onChange={(e) => setLineEnding(e.target.value as "none" | "\n" | "\r\n")}>
          <option value={"\r\n"}>{"\\r\\n"}</option>
          <option value={"\n"}>{"\\n"}</option>
          <option value="none">不追加</option>
        </select>
        <button className="serial-send-btn" onClick={handleSend}>{t('sendBtn')}</button>
      </div>
      <div className="serial-bottom">
        <input className="serial-filter" id="serialFilter" placeholder={t('filterLog')} value={filter} onChange={(e) => setFilter(e.target.value)} />
        <label className="serial-autoscroll"><input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />{t('autoScroll')}</label>
        <button className="serial-clear" onClick={clearLog}>{t('clearLog')}</button>
        <button className="serial-clear" onClick={handleExport}>{t('exportLog')}</button>
      </div>
    </div>
  );
}
