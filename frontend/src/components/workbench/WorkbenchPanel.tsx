import { useEffect, useMemo, useState, useRef, useCallback } from "react";
import DOMPurify from "dompurify";
import { useAppStore } from "../../stores/useAppStore";
import { useSerialStore } from "../../stores/useSerialStore";
import { useLogStore } from "../../stores/useLogStore";
import { useI18n } from "../../i18n";
import { copyToClipboard } from "../../utils/clipboard";
import { apiGet, apiPost, apiSSE, apiWS } from "../../api/client";
import type { BuildSSEEvent, DiagnoseItem, DiagnoseResponse, PinAuditResponse, PinWarning, WiringResponse } from "../../types/api";

const TAB_IDS = ["serial", "flash", "preview", "wiring", "safety"] as const;
type TabId = (typeof TAB_IDS)[number];

const CODE = `#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <ThingSpeak.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SENSOR_PIN 34
#define LED_PIN 2

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
WiFiClient client;

const char* ssid = "WiFi-2.4G";
const char* pwd = "password123";
unsigned long channelNumber = 123456;
const char* apiKey = "YOUR_API_KEY";

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(SENSOR_PIN, INPUT);
}`;

function nowHHMMSSmmm() {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const mmm = String(d.getMilliseconds()).padStart(3, "0");
  return `${hh}:${mm}:${ss}.${mmm}`;
}

const FALLBACK_PORTS = ["COM3 — USB Serial (CH340)", "COM5 — ST-Link Virtual COM", "/dev/ttyUSB0 — CP2102"];

// Baud rate options (extracted constant, avoids hardcoding in component)
const BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];

// Line type colors for wiring connections: red=power, green=signal, black=ground
const LINE_TYPE_COLORS: Record<string, string> = {
  power: "#f85149",
  signal: "#3fb950",
  ground: "#8b949e",
};

// ANSI escape code → HTML span color mapping
const ANSI_COLORS: Record<string, string> = {
  "31": "#f85149", // red
  "32": "#3fb950", // green
  "33": "#d29922", // yellow
  "34": "#58a6ff", // blue
  "35": "#bc8cff", // magenta
  "36": "#39c5cf", // cyan
};

/** Parse ANSI color escape codes and convert to HTML spans. Returns sanitized HTML string. */
function parseAnsiToHtml(text: string): string {
  const parts: string[] = [];
  let remaining = text;
  let openSpan = false;
  const ansiRe = /\x1b\[(\d+)m/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = ansiRe.exec(remaining)) !== null) {
    if (match.index > lastIndex) {
      parts.push(remaining.slice(lastIndex, match.index));
    }
    const code = match[1];
    if (code === "0" || code === "39") {
      if (openSpan) {
        parts.push("</span>");
        openSpan = false;
      }
    } else if (ANSI_COLORS[code]) {
      if (openSpan) parts.push("</span>");
      parts.push(`<span style="color:${ANSI_COLORS[code]}">`);
      openSpan = true;
    }
    lastIndex = ansiRe.lastIndex;
  }
  if (lastIndex < remaining.length) {
    parts.push(remaining.slice(lastIndex));
  }
  if (openSpan) parts.push("</span>");
  const html = parts.join("");
  // Escape HTML entities except for our generated spans
  return html
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/&lt;span style="color:[^"]*"&gt;/g, (m) => m.replace(/&lt;/g, "<").replace(/&gt;/g, ">"))
    .replace(/&lt;\/span&gt;/g, "</span>");
}

/** Count SVG element nodes (approximate) by counting opening tags of common SVG elements. */
function countSvgNodes(svg: string): number {
  const tagRe = /<(rect|circle|line|path|text|polygon|ellipse|polyline|g|use|image)\b/g;
  let count = 0;
  let m: RegExpExecArray | null;
  while ((m = tagRe.exec(svg)) !== null) count++;
  return count;
}

const SVG_NODE_WARN_THRESHOLD = 100;

export function WorkbenchPanel() {
  const { t } = useI18n();
  const { wbTab, setWbTab } = useAppStore();
  const tabLabels: Record<TabId, string> = {
    serial: t('serialMonitor'),
    flash: t('flash'),
    preview: t('codePreview'),
    wiring: t('wiringDiagram'),
    safety: t('safetyGuard'),
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="wb-tabbar workbench-tabs">
        {TAB_IDS.map((id) => (
          <button key={id} className={`wb-tab${wbTab === id ? " active" : ""}`} data-wbtab={id} onClick={() => setWbTab(id)}>
            {tabLabels[id]}
          </button>
        ))}
      </div>
      <div className="wb-content">
        {wbTab === "serial" && <SerialPane />}
        {wbTab === "flash" && <FlashPane />}
        {wbTab === "preview" && <PreviewPane />}
        {wbTab === "wiring" && <WiringPane />}
        {wbTab === "safety" && <SafetyPane />}
      </div>
    </div>
  );
}

function SerialPane() {
  const { t } = useI18n();
  const { connected, port, baudRate, log, autoScroll, dtrActive, rtsActive, filter, setConnected, setPort, setBaudRate, addLog, clearLog, setAutoScroll, toggleDtr, toggleRts, setFilter } = useSerialStore();
  const [sendText, setSendText] = useState("");
  const [ports, setPorts] = useState<string[]>(FALLBACK_PORTS);
  const sendInputRef = useRef<HTMLInputElement>(null);
  const logContainerRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // On mount, fetch real device list
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiGet<{ devices: { port: string; description: string }[] }>("devices");
        if (!cancelled && data?.devices?.length) {
          setPorts(data.devices.map((d) => `${d.port} — ${d.description}`));
          useLogStore.getState().log("ok", "serial", `扫描到 ${data.devices.length} 个串口设备`);
        }
      } catch {
        // fallback to FALLBACK_PORTS already set
        if (!cancelled) {
          useLogStore.getState().log("warn", "serial", "串口设备扫描失败，使用默认列表");
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const seed = () => {
    [
      '14:32:02.157 AT',
      '14:32:02.359 OK',
      '14:32:03.112 AT+CWJAP="WiFi-2.4G","password123"',
      '14:32:03.583 WIFI CONNECTING',
      '14:32:06.902 WIFI CONNECTED',
      '14:32:07.015 WIFI GOT IP',
      '14:32:07.110 192.168.1.104',
      '14:32:08.441 AT+CIPSTART="TCP","api.thingspeak.com",80',
      '14:32:08.902 CONNECT',
    ].forEach(addLog);
  };

  const handleConnect = useCallback(() => {
    if (connected) {
      // Disconnect
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
      useLogStore.getState().log("info", "serial", "断开串口连接");
      return;
    }
    // Try WebSocket connection
    const portValue = port || ports[0];
    const portName = portValue.split(" — ")[0];
    useLogStore.getState().log("info", "serial", `连接串口: ${portName} @ ${baudRate}`);
    const ws = apiWS(`/api/monitor/${portName}?baud=${baudRate}`, {
      onOpen: () => {
        setConnected(true);
        wsRef.current = ws;
        ws.send(JSON.stringify({ type: "start" }));
      },
      onMessage: (data) => {
        try {
          const msg = JSON.parse(data);
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
  }, [connected, port, ports, baudRate, setConnected, addLog]);

  const handleSend = useCallback(() => {
    const text = sendText.trim();
    if (!text) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "data", payload: text }));
      useLogStore.getState().log("debug", "serial", `发送: ${text.slice(0, 30)}`);
    } else {
      useLogStore.getState().log("error", "serial", "串口未连接，无法发送");
    }
    setSendText("");
    sendInputRef.current?.focus();
  }, [sendText]);

  const handleSendKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

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

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, []);

  return (
    <div className="serial-monitor">
      <div className="serial-toolbar">
        <select id="serialPortSelect" value={port || ports[0]} onChange={(e) => setPort(e.target.value)}>{ports.map((p) => <option key={p} value={p}>{p}</option>)}</select>
        <select id="serialBaudSelect" value={String(baudRate)} onChange={(e) => setBaudRate(parseInt(e.target.value))}>{BAUD_RATES.map((b) => <option key={b} value={b}>{b}</option>)}</select>
        <button className={`serial-connect-btn ${connected ? "on" : "off"}`} onClick={handleConnect}>{connected ? t('disconnect') : t('connect')}</button>
        <button className="serial-ctrl-btn" onClick={toggleDtr} style={dtrActive ? { background: "var(--accent, #58a6ff)", color: "#fff" } : undefined}>DTR</button>
        <button className="serial-ctrl-btn" onClick={toggleRts} style={rtsActive ? { background: "var(--accent, #58a6ff)", color: "#fff" } : undefined}>RTS</button>
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
                <span dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(parseAnsiToHtml(msgPart)) }} />
              </div>
            );
          }
          return (
            <div className="log-line log-recv" key={idx}>
              <span className="log-time">{timePart}</span>
              <span>{msgPart}</span>
            </div>
          );
        }) : <div className="log-line log-sys"><span style={{ color: '#8b949e' }}>{t('serialConnectHint')}</span></div>}
      </div>
      <div className="serial-send-area">
        <input id="serialSendInput" ref={sendInputRef} placeholder={t('serialSendPlaceholder')} value={sendText} onChange={(e) => setSendText(e.target.value)} onKeyDown={handleSendKeyDown} />
        <button className="serial-send-btn" onClick={handleSend}>{t('sendBtn')}</button>
      </div>
      <div className="serial-bottom">
        <input className="serial-filter" id="serialFilter" placeholder={t('filterLog')} value={filter} onChange={(e) => setFilter(e.target.value)} />
        <label className="serial-autoscroll"><input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />{t('autoScroll')}</label>
        <button className="serial-clear" onClick={clearLog}>{t('clearLog')}</button>
        <button className="serial-clear" onClick={seed}>⟳</button>
        <button className="serial-clear" onClick={handleExport}>{t('exportLog')}</button>
      </div>
    </div>
  );
}

function FlashPane() {
  const { t } = useI18n();
  const { flashCode, setFlashCode } = useAppStore();
  const [flashLog, setFlashLog] = useState<string[]>([t('idle')]);
  const [compiling, setCompiling] = useState(false);
  const [flashing, setFlashing] = useState(false);
  const [selectedEnv, setSelectedEnv] = useState("esp32-s3");
  const [selectedPort, setSelectedPort] = useState("");
  const [ports, setPorts] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiGet<{ devices: { port: string; description: string }[] }>("devices");
        if (!cancelled && data?.devices?.length) {
          const list = data.devices.map((d) => `${d.port} — ${d.description}`);
          setPorts(list);
          setSelectedPort(list[0]);
          useLogStore.getState().log("ok", "flash", `扫描到 ${data.devices.length} 个烧录端口`);
        }
      } catch {
        if (!cancelled) {
          useLogStore.getState().log("error", "flash", "烧录端口扫描失败");
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleCompile = useCallback(() => {
    if (compiling || flashing) return;
    setCompiling(true);
    setFlashLog([t('compilingStatus')]);
    useLogStore.getState().log("info", "flash", `开始编译: ${selectedEnv}`);

    apiSSE("build", { env: selectedEnv, project_dir: "/projects/hardware-rag" }, {
      onEvent: (event) => {
        const e = event as BuildSSEEvent;
        if (e.type === "progress") {
          useLogStore.getState().log("debug", "flash", `编译进度: ${e.message}`);
          setFlashLog((prev) => [...prev, e.message || `⏳ ${e.percent ?? 0}%`]);
        } else if (e.type === "done") {
          if (e.success) {
            useLogStore.getState().log("ok", "flash", "编译成功");
            setFlashLog((prev) => [...prev, `✓ ${t('compileSuccess')}`]);
          } else {
            const errMsg = e.errors?.join("; ") || t('compileFail');
            useLogStore.getState().log("error", "flash", `编译失败: ${errMsg}`);
            setFlashLog((prev) => [...prev, `✗ ${errMsg}`]);
          }
          setCompiling(false);
        }
      },
      onDone: () => {
        setCompiling(false);
      },
      onError: (err) => {
        useLogStore.getState().log("error", "flash", `编译失败: ${err.message}`);
        setFlashLog((prev) => [...prev, `✗ 编译失败: ${err.message}`]);
        setCompiling(false);
      },
    });
  }, [compiling, flashing, selectedEnv, t]);

  const handleFlash = useCallback(() => {
    if (compiling || flashing) return;
    setFlashing(true);
    setFlashLog([t('flashingStatus')]);

    const portName = selectedPort.split(" — ")[0];
    useLogStore.getState().log("info", "flash", `开始烧录: ${selectedEnv} → ${portName}`);
    apiSSE("upload", { env: selectedEnv, port: portName, project_dir: "/projects/hardware-rag" }, {
      onEvent: (event) => {
        const e = event as BuildSSEEvent;
        if (e.type === "progress") {
          setFlashLog((prev) => [...prev, e.message || `⏳ ${e.percent ?? 0}%`]);
        } else if (e.type === "done") {
          if (e.success) {
            useLogStore.getState().log("ok", "flash", "烧录完成");
            setFlashLog((prev) => [...prev, `✓ ${t('flashComplete')}`]);
          } else {
            const errMsg = e.errors?.join("; ") || t('flashFail');
            useLogStore.getState().log("error", "flash", `烧录失败: ${errMsg}`);
            setFlashLog((prev) => [...prev, `✗ ${errMsg}`]);
          }
          setFlashing(false);
        }
      },
      onDone: () => {
        setFlashing(false);
      },
      onError: (err) => {
        useLogStore.getState().log("error", "flash", `烧录失败: ${err.message}`);
        setFlashLog((prev) => [...prev, `✗ 烧录失败: ${err.message}`]);
        setFlashing(false);
      },
    });
  }, [compiling, flashing, selectedEnv, selectedPort, t]);

  const handleRefresh = useCallback(() => {
    setFlashLog([t('idle')]);
  }, []);

  return (
    <div className="flash-panel">
      <div className="flash-code-toggle">{t('codeToggle')}</div>
      <div className="flash-code-wrap"><textarea className="flash-code" readOnly={false} value={flashCode || CODE} onChange={(e) => setFlashCode(e.target.value)}></textarea></div>
      <div className="flash-steps">
        <div className="flash-step"><span className="flash-step-dot"></span>{t('compileLabel')}</div>
        <div className="flash-step"><span className="flash-step-dot"></span>{t('flashLabel')}</div>
        <div className="flash-step"><span className="flash-step-dot"></span>{t('verifyLabel')}</div>
      </div>
      <div className="flash-toolbar">
        <select value={selectedPort} onChange={(e) => setSelectedPort(e.target.value)}>
          {ports.length === 0 && <option value="" disabled>请选择端口</option>}
          {ports.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={selectedEnv} onChange={(e) => setSelectedEnv(e.target.value)}>
          <option value="esp32-s3">ESP32-S3</option>
          <option value="esp32-c3">ESP32-C3</option>
          <option value="esp32">ESP32</option>
          <option value="stm32f407">STM32F407</option>
        </select>
        <button className="flash-btn" onClick={handleRefresh}>⟳</button>
        <button className="flash-btn" onClick={handleCompile} disabled={compiling || flashing}>{compiling ? t('compilingStatus') : t('compileBtn')}</button>
        <button className="flash-btn" onClick={handleFlash} disabled={compiling || flashing}>{flashing ? t('flashingStatus') : t('flashBtn')}</button>
      </div>
      <div className="flash-log" id="flashLog">
        {flashLog.map((line, idx) => (
          <div key={idx} className={line.startsWith("✓") ? "fl-ok" : line.startsWith("✗") ? "fl-err" : "fl-info"}>{line}</div>
        ))}
      </div>
    </div>
  );
}

function PreviewPane() {
  const { t } = useI18n();
  const { previewTabs, activePreviewTabId, addPreviewTab, removePreviewTab, setActivePreviewTabId, updatePreviewTabCode, setWbTab, setFlashCode } = useAppStore();
  const [diagnostics, setDiagnostics] = useState<DiagnoseItem[] | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const lineNumbersRef = useRef<HTMLDivElement>(null);

  const fallbackTab = useMemo(
    () => ({ id: "default-preview", label: "main.cpp", code: CODE, language: "cpp" }),
    [],
  );

  const tabs = previewTabs.length ? previewTabs : [fallbackTab];
  const activeTab = tabs.find((tab) => tab.id === activePreviewTabId) || tabs.at(-1) || fallbackTab;
  const lines = activeTab.code.split("\n");

  const handleCodeChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      updatePreviewTabCode(activeTab.id, e.target.value);
    },
    [activeTab.id, updatePreviewTabCode],
  );

  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLTextAreaElement>) => {
      if (lineNumbersRef.current) {
        lineNumbersRef.current.scrollTop = e.currentTarget.scrollTop;
      }
    },
    [],
  );

  const handleCopyCode = useCallback(() => {
    copyToClipboard(activeTab.code);
  }, [activeTab.code]);

  const handlePushToFlash = useCallback(() => {
    // 将当前预览的代码传递到 Flash 面板
    setFlashCode(activeTab.code);
    setWbTab("flash");
  }, [activeTab.code, setWbTab]);

  const handleDiagnose = useCallback(async () => {
    if (diagnosing) return;
    setDiagnosing(true);
    setDiagnostics(null);
    try {
      const res = await apiPost<DiagnoseResponse>("diagnose", { code: activeTab.code, env: "esp32-s3" });
      setDiagnostics(res.results ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      useLogStore.getState().log("error", "preview", `诊断失败: ${msg}`);
    } finally {
      setDiagnosing(false);
    }
  }, [diagnosing, activeTab.code]);

  const statusColor = (status: DiagnoseItem["status"]) => {
    if (status === "PASS") return "#3fb950";
    if (status === "WARN") return "#d29922";
    return "#f85149";
  };

  return (
    <div className="code-preview-editor" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="code-preview-toolbar">
        {tabs.map((tab) => {
          const isGenerated = tab.id !== fallbackTab.id;
          return (
            <span
              key={tab.id}
              className={`code-preview-file-tab${activeTab.id === tab.id ? ' active' : ''}`}
              onClick={() => setActivePreviewTabId(tab.id)}
            >
              {tab.label}
              {isGenerated ? (
                <button
                  type="button"
                  className="tab-close"
                  onClick={(event) => {
                    event.stopPropagation();
                    removePreviewTab(tab.id);
                  }}
                >
                  ×
                </button>
              ) : null}
            </span>
          );
        })}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          <button
            type="button"
            className="flash-btn"
            onClick={handleCopyCode}
          >
            {t('copy')}
          </button>
          <button
            type="button"
            className="flash-btn"
            onClick={handlePushToFlash}
          >
            {t('pushToFlash')}
          </button>
          <button
            type="button"
            className="flash-btn"
            onClick={handleDiagnose}
            disabled={diagnosing}
          >
            {diagnosing ? t('diagnosing') : t('buildDiagnose')}
          </button>
        </span>
      </div>
      {diagnostics && (
        <div style={{
          borderBottom: "1px solid var(--border)",
          padding: "6px 12px",
          background: "var(--card)",
          fontSize: 11,
        }}>
          {diagnostics.map((d, idx) => (
            <div key={idx} style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 0" }}>
              <span style={{
                fontWeight: 600,
                color: statusColor(d.status),
                minWidth: 40,
              }}>
                {d.status}
              </span>
              <span style={{ color: "var(--fg)" }}>{d.name}</span>
              {d.detail && (
                <span style={{ color: statusColor(d.status), marginLeft: 4 }}>
                  — {d.detail}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="code-preview-editor">
        <div className="code-preview-numbers" ref={lineNumbersRef}>{lines.map((_, idx) => <span className="ln" key={idx}>{idx + 1}</span>)}</div>
        <textarea className="code-preview-textarea" value={activeTab.code} onChange={handleCodeChange} onScroll={handleScroll} />
      </div>
    </div>
  );
}

const BOM_DATA = [
  { component: "ESP32-S3", qty: 1 },
  { component: "SSD1306 OLED", qty: 1 },
  { component: "DHT22 Sensor", qty: 1 },
  { component: "LED", qty: 1 },
  { component: "4.7kΩ Resistor", qty: 2 },
  { component: "220Ω Resistor", qty: 1 },
];

const DEMO_SVG = `<svg width="100%" height="100%" viewBox="0 0 600 400" xmlns="http://www.w3.org/2000/svg" style="background: #0d1117">
  <defs>
    <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
      <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#1c2333" strokeWidth="0.5"/>
    </pattern>
  </defs>
  <rect width="600" height="400" fill="url(#grid)"/>
  <rect x="230" y="140" width="140" height="120" rx="6" fill="#1a2332" stroke="#58a6ff" strokeWidth="2"/>
  <text x="300" y="175" textAnchor="middle" fill="#58a6ff" fontSize="14" fontWeight="bold">ESP32-S3</text>
  <text x="300" y="195" textAnchor="middle" fill="#8b949e" fontSize="9">MCU</text>
  <text x="237" y="225" fill="#e6edf3" fontSize="8">GPIO2</text>
  <text x="237" y="240" fill="#e6edf3" fontSize="8">GPIO34</text>
  <text x="237" y="255" fill="#e6edf3" fontSize="8">SDA</text>
  <text x="237" y="270" fill="#e6edf3" fontSize="8">SCL</text>
  <text x="363" y="225" fill="#e6edf3" fontSize="8" textAnchor="end">3V3</text>
  <text x="363" y="240" fill="#e6edf3" fontSize="8" textAnchor="end">GND</text>
  <text x="363" y="255" fill="#e6edf3" fontSize="8" textAnchor="end">5V</text>
  <circle cx="230" cy="222" r="3" fill="#f0883e"/>
  <circle cx="230" cy="237" r="3" fill="#3fb950"/>
  <circle cx="230" cy="252" r="3" fill="#a371f7"/>
  <circle cx="230" cy="267" r="3" fill="#a371f7"/>
  <circle cx="370" cy="222" r="3" fill="#f85149"/>
  <circle cx="370" cy="237" r="3" fill="#8b949e"/>
  <circle cx="370" cy="252" r="3" fill="#f85149"/>
  <rect x="40" y="200" width="100" height="50" rx="4" fill="#1a2332" stroke="#f0883e" strokeWidth="1.5"/>
  <text x="90" y="220" textAnchor="middle" fill="#f0883e" fontSize="11" fontWeight="bold">LED</text>
  <text x="90" y="235" textAnchor="middle" fill="#8b949e" fontSize="8">GPIO2</text>
  <polygon points="75,240 85,245 75,250" fill="#f0883e" opacity="0.6"/>
  <line x1="85" y1="240" x2="85" y2="250" stroke="#f0883e" strokeWidth="1.5" opacity="0.6"/>
  <circle cx="140" cy="222" r="3" fill="#f0883e"/>
  <rect x="155" y="210" width="55" height="24" rx="3" fill="#1a2332" stroke="#8b949e" strokeWidth="1"/>
  <text x="182" y="226" textAnchor="middle" fill="#8b949e" fontSize="8">220Ω</text>
  <line x1="140" y1="222" x2="155" y2="222" stroke="#f0883e" strokeWidth="2"/>
  <line x1="210" y1="222" x2="230" y2="222" stroke="#f0883e" strokeWidth="2"/>
  <rect x="40" y="310" width="100" height="55" rx="4" fill="#1a2332" stroke="#3fb950" strokeWidth="1.5"/>
  <text x="90" y="332" textAnchor="middle" fill="#3fb950" fontSize="11" fontWeight="bold">DHT22</text>
  <text x="90" y="348" textAnchor="middle" fill="#8b949e" fontSize="8">GPIO34</text>
  <circle cx="140" cy="337" r="3" fill="#3fb950"/>
  <rect x="155" y="325" width="55" height="24" rx="3" fill="#1a2332" stroke="#8b949e" strokeWidth="1"/>
  <text x="182" y="341" textAnchor="middle" fill="#8b949e" fontSize="8">4.7kΩ</text>
  <line x1="140" y1="337" x2="155" y2="337" stroke="#3fb950" strokeWidth="2"/>
  <line x1="210" y1="337" x2="220" y2="337" stroke="#3fb950" strokeWidth="2"/>
  <line x1="220" y1="337" x2="220" y2="237" stroke="#3fb950" strokeWidth="2"/>
  <line x1="220" y1="237" x2="230" y2="237" stroke="#3fb950" strokeWidth="2"/>
  <line x1="182" y1="325" x2="182" y2="222" stroke="#f85149" strokeWidth="1.5" strokeDasharray="4,2"/>
  <line x1="182" y1="222" x2="370" y2="222" stroke="#f85149" strokeWidth="1.5" strokeDasharray="4,2"/>
  <rect x="430" y="80" width="120" height="70" rx="4" fill="#1a2332" stroke="#a371f7" strokeWidth="1.5"/>
  <text x="490" y="105" textAnchor="middle" fill="#a371f7" fontSize="11" fontWeight="bold">SSD1306</text>
  <text x="490" y="120" textAnchor="middle" fill="#8b949e" fontSize="8">OLED Display</text>
  <text x="490" y="135" textAnchor="middle" fill="#8b949e" fontSize="7">I2C (SDA/SCL)</text>
  <rect x="460" y="138" width="60" height="8" rx="1" fill="#0d1117" stroke="#a371f7" strokeWidth="0.5"/>
  <circle cx="430" cy="115" r="3" fill="#a371f7"/>
  <circle cx="430" cy="135" r="3" fill="#a371f7"/>
  <line x1="430" y1="115" x2="400" y2="115" stroke="#a371f7" strokeWidth="2"/>
  <line x1="400" y1="115" x2="400" y2="252" stroke="#a371f7" strokeWidth="2"/>
  <line x1="400" y1="252" x2="370" y2="252" stroke="#a371f7" strokeWidth="2"/>
  <line x1="430" y1="135" x2="410" y2="135" stroke="#a371f7" strokeWidth="2" strokeDasharray="6,3"/>
  <line x1="410" y1="135" x2="410" y2="267" stroke="#a371f7" strokeWidth="2" strokeDasharray="6,3"/>
  <line x1="410" y1="267" x2="370" y2="267" stroke="#a371f7" strokeWidth="2" strokeDasharray="6,3"/>
  <line x1="370" y1="222" x2="420" y2="222" stroke="#f85149" strokeWidth="2.5" opacity="0.4"/>
  <text x="425" y="226" fill="#f85149" fontSize="8" opacity="0.7">3V3</text>
  <line x1="370" y1="237" x2="420" y2="237" stroke="#8b949e" strokeWidth="2.5" opacity="0.4"/>
  <text x="425" y="241" fill="#8b949e" fontSize="8" opacity="0.7">GND</text>
  <line x1="490" y1="150" x2="490" y2="222" stroke="#f85149" strokeWidth="1" strokeDasharray="3,2" opacity="0.5"/>
  <line x1="490" y1="222" x2="420" y2="222" stroke="#f85149" strokeWidth="1" strokeDasharray="3,2" opacity="0.5"/>
  <rect x="440" y="155" width="45" height="20" rx="3" fill="#1a2332" stroke="#8b949e" strokeWidth="1"/>
  <text x="462" y="169" textAnchor="middle" fill="#8b949e" fontSize="7">4.7kΩ</text>
  <rect x="430" y="290" width="150" height="90" rx="4" fill="#0d1117" stroke="#1c2333" strokeWidth="1"/>
  <text x="505" y="308" textAnchor="middle" fill="#8b949e" fontSize="9" fontWeight="bold">图例</text>
  <line x1="440" y1="320" x2="460" y2="320" stroke="#f0883e" strokeWidth="2"/>
  <text x="465" y="323" fill="#8b949e" fontSize="8">GPIO 数字信号</text>
  <line x1="440" y1="337" x2="460" y2="337" stroke="#3fb950" strokeWidth="2"/>
  <text x="465" y="340" fill="#8b949e" fontSize="8">模拟输入</text>
  <line x1="440" y1="354" x2="460" y2="354" stroke="#a371f7" strokeWidth="2"/>
  <text x="465" y="357" fill="#8b949e" fontSize="8">I2C 总线</text>
  <line x1="440" y1="371" x2="460" y2="371" stroke="#f85149" strokeWidth="2"/>
  <text x="465" y="374" fill="#8b949e" fontSize="8">电源轨</text>
</svg>`;

function WiringPane() {
  const { t } = useI18n();
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [svgContent, setSvgContent] = useState("");
  const [bomData, setBomData] = useState<{ component: string; qty: number }[]>([]);
  const [generating, setGenerating] = useState(false);
  const [wiringError, setWiringError] = useState("");
  const [svgNodeWarning, setSvgNodeWarning] = useState("");
  const dragRef = useRef({ active: false, startX: 0, startY: 0, panStartX: 0, panStartY: 0 });
  const wrapRef = useRef<HTMLDivElement>(null);
  // Track active drag listeners so they can be removed on unmount
  const dragHandlersRef = useRef<{ onMove: (ev: MouseEvent) => void; onUp: () => void } | null>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    // 只响应左键
    if (e.button !== 0) return;
    e.preventDefault();
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      panStartX: pan.x,
      panStartY: pan.y,
    };
    setDragging(true);

    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current.active) return;
      const dx = ev.clientX - dragRef.current.startX;
      const dy = ev.clientY - dragRef.current.startY;
      setPan({
        x: dragRef.current.panStartX + dx,
        y: dragRef.current.panStartY + dy,
      });
    };
    const onUp = () => {
      dragRef.current.active = false;
      setDragging(false);
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      dragHandlersRef.current = null;
    };
    document.body.style.cursor = "grabbing";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    dragHandlersRef.current = { onMove, onUp };
  }, [pan]);

  // Remove drag listeners if the component unmounts mid-drag
  useEffect(() => {
    return () => {
      if (dragHandlersRef.current) {
        document.removeEventListener("mousemove", dragHandlersRef.current.onMove);
        document.removeEventListener("mouseup", dragHandlersRef.current.onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        dragHandlersRef.current = null;
      }
    };
  }, []);

  // 滚轮缩放：以鼠标位置为中心缩放（使用 addEventListener 避免 passive 限制）
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 0.9;
      setZoom((prevZoom) => {
        const newZoom = Math.min(3, Math.max(0.5, prevZoom * factor));
        const rect = el.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        setPan((prevPan) => ({
          x: mx - (mx - prevPan.x) * (newZoom / prevZoom),
          y: my - (my - prevPan.y) * (newZoom / prevZoom),
        }));
        return newZoom;
      });
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  const handleGenerate = useCallback(async () => {
    if (generating) return;
    setGenerating(true);
    setWiringError("");
    useLogStore.getState().log("info", "wiring", "生成接线图...");
    try {
      const res = await apiPost<WiringResponse>("wiring", {
        title: "Hardware RAG",
        connections: [
          { from: "ESP32-S3", pin: "GPIO2", to_component: "LED", to_pin: "ANODE", color: LINE_TYPE_COLORS.signal, label: "GPIO2→LED", line_type: "signal" },
          { from: "ESP32-S3", pin: "GPIO34", to_component: "DHT22", to_pin: "DATA", color: LINE_TYPE_COLORS.signal, label: "GPIO34→DHT22", line_type: "signal" },
          { from: "ESP32-S3", pin: "SDA", to_component: "SSD1306", to_pin: "SDA", color: LINE_TYPE_COLORS.signal, label: "I2C SDA", line_type: "signal" },
          { from: "ESP32-S3", pin: "SCL", to_component: "SSD1306", to_pin: "SCL", color: LINE_TYPE_COLORS.signal, label: "I2C SCL", line_type: "signal" },
          { from: "ESP32-S3", pin: "3V3", to_component: "LED", to_pin: "CATHODE", color: LINE_TYPE_COLORS.power, label: "3V3→LED", line_type: "power" },
          { from: "ESP32-S3", pin: "3V3", to_component: "DHT22", to_pin: "VCC", color: LINE_TYPE_COLORS.power, label: "3V3→DHT22", line_type: "power" },
          { from: "ESP32-S3", pin: "GND", to_component: "DHT22", to_pin: "GND", color: LINE_TYPE_COLORS.ground, label: "GND→DHT22", line_type: "ground" },
          { from: "ESP32-S3", pin: "GND", to_component: "SSD1306", to_pin: "GND", color: LINE_TYPE_COLORS.ground, label: "GND→SSD1306", line_type: "ground" },
        ],
        components: [
          { name: "ESP32-S3", type: "mcu", pins: ["GPIO2", "GPIO34", "SDA", "SCL", "3V3", "GND", "5V"] },
          { name: "LED", type: "led", pins: ["ANODE", "CATHODE"] },
          { name: "DHT22", type: "sensor", pins: ["DATA", "VCC", "GND"] },
          { name: "SSD1306", type: "display", pins: ["SDA", "SCL", "VCC", "GND"] },
        ],
      });
      if (res.svg) {
        setSvgContent(res.svg);
        const nodeCount = countSvgNodes(res.svg);
        if (nodeCount > SVG_NODE_WARN_THRESHOLD) {
          setSvgNodeWarning(`⚠ 节点数 ${nodeCount} 超过 ${SVG_NODE_WARN_THRESHOLD}，可能影响渲染性能`);
          useLogStore.getState().log("warn", "wiring", `SVG 节点数 ${nodeCount} 超过阈值 ${SVG_NODE_WARN_THRESHOLD}`);
        } else {
          setSvgNodeWarning("");
        }
      }
      if (res.bom) {
        setBomData(res.bom);
      }
      useLogStore.getState().log("ok", "wiring", "接线图生成完成");
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      setWiringError(errMsg);
      useLogStore.getState().log("error", "wiring", `接线图生成失败: ${errMsg}`);
    } finally {
      setGenerating(false);
    }
  }, [generating]);

  return (
    <div className="wiring-panel" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="wiring-toolbar" style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 11, color: "var(--muted-fg)", fontWeight: 500 }}>{t('wiringLabel')}</span>
        <button
          style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--card)", cursor: "pointer", color: "var(--fg)" }}
          onClick={handleGenerate}
          disabled={generating}
        >
          {generating ? "⏳..." : "生成接线图"}
        </button>
        {wiringError && <span style={{ fontSize: 10, color: "#f85149" }}>{wiringError}</span>}
        {svgNodeWarning && <span style={{ fontSize: 10, color: "#d29922" }}>{svgNodeWarning}</span>}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <button
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--card)", cursor: "pointer", color: "var(--fg)" }}
            onClick={() => setZoom(z => Math.min(3, Math.max(0.5, z * 1.2)))}
          >+</button>
          <button
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--card)", cursor: "pointer", color: "var(--fg)" }}
            onClick={() => setZoom(z => Math.min(3, Math.max(0.5, z * 0.8)))}
          >−</button>
          <button
            style={{ fontSize: 11, padding: "2px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--card)", cursor: "pointer", color: "var(--fg)" }}
            onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
          >{t('reset')}</button>
          <span style={{ fontSize: 10, color: "var(--muted-fg)", lineHeight: "22px", minWidth: 40, textAlign: "center" }}>{Math.round(zoom * 100)}%</span>
        </div>
      </div>
      <div
        className="wiring-svg-wrap"
        ref={wrapRef}
        style={{
          flex: 1,
          overflow: "hidden",
          cursor: dragging ? "grabbing" : "grab",
          position: "relative",
        }}
        onMouseDown={handleMouseDown}
      >
        {svgContent ? (
          <div
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: "0 0",
              width: "100%",
              height: "100%",
            }}
            dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(svgContent) }}
          />
        ) : (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: 'var(--muted-fg)', fontSize: 12 }}>
            {wiringError || t('wiringLabel') + ' — 点击"生成接线图"'}
          </div>
        )}
      </div>
      <div style={{ borderTop: "1px solid var(--border)", padding: "8px 12px", overflowY: "auto", maxHeight: 160 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--fg)", marginBottom: 6 }}>{t('bomTitle')}</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>{t('componentLabel')}</th>
              <th style={{ textAlign: "center", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500, width: 50 }}>{t('quantityLabel')}</th>
            </tr>
          </thead>
          <tbody>
            {bomData.map((item) => (
              <tr key={item.component} style={{ borderBottom: "1px solid var(--border, #1c2333)" }}>
                <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{item.component}</td>
                <td style={{ padding: "3px 8px", textAlign: "center", color: "var(--fg)" }}>×{item.qty}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface PinAllocation {
  pin: string;
  mode: string;
  source: string;
  status: "safe" | "warning" | "conflict";
}

interface StrappingConflict {
  pin: string;
  conflictType: string;
  description: string;
  suggestion: string;
}

function pinWarningToAllocation(w: PinWarning): PinAllocation {
  return {
    pin: w.pin,
    mode: "",
    source: "code",
    status: w.severity === "critical" ? "conflict" : "warning",
  };
}

function pinWarningToStrappingConflict(w: PinWarning): StrappingConflict {
  return {
    pin: w.pin,
    conflictType: w.severity === "critical" ? "严重冲突" : "警告",
    description: w.message,
    suggestion: w.suggestion,
  };
}

function SafetyPane() {
  const { t } = useI18n();
  const { previewTabs, activePreviewTabId, flashChip } = useAppStore();
  const [checking, setChecking] = useState(false);
  const [verified, setVerified] = useState(false);
  const [pinAllocations, setPinAllocations] = useState<PinAllocation[]>([]);
  const [strappingConflicts, setStrappingConflicts] = useState<StrappingConflict[]>([]);

  const handleVerify = useCallback(() => {
    if (checking) return;
    setChecking(true);
    setVerified(false);
    setPinAllocations([]);
    setStrappingConflicts([]);
    useLogStore.getState().log("info", "safety", "开始引脚安全审计");

    const activeTab = previewTabs.find((tab) => tab.id === activePreviewTabId);
    const code = activeTab?.code ?? "";

    const pinAssignments: Record<string, { function: string; config: string }> = {};
    const addPin = (pin: string, func: string, config: string) => {
      if (!pin) return;
      pinAssignments[pin] = { function: func, config };
    };

    // #define NAME [VALUE]
    const defineRe = /^\s*#\s*define\s+([A-Za-z_]\w*)\s*(\S+)?/gm;
    let m: RegExpExecArray | null;
    while ((m = defineRe.exec(code)) !== null) {
      addPin(m[1], "define", m[2] ?? "");
    }

    // pinMode(PIN, MODE)
    const pinModeRe = /pinMode\s*\(\s*([^,\s)]+)\s*,\s*([^)\s]+)\s*\)/g;
    while ((m = pinModeRe.exec(code)) !== null) {
      addPin(m[1], "pinMode", m[2]);
    }

    // digitalRead(PIN)
    const digitalReadRe = /digitalRead\s*\(\s*([^)\s]+)\s*\)/g;
    while ((m = digitalReadRe.exec(code)) !== null) {
      addPin(m[1], "digitalRead", "");
    }

    // digitalWrite(PIN)
    const digitalWriteRe = /digitalWrite\s*\(\s*([^)\s]+)\s*\)/g;
    while ((m = digitalWriteRe.exec(code)) !== null) {
      addPin(m[1], "digitalWrite", "");
    }

    if (Object.keys(pinAssignments).length === 0) {
      useLogStore.getState().log("warn", "safety", "未从代码中检测到引脚分配");
      setChecking(false);
      return;
    }

    apiPost<PinAuditResponse>("audit_pins", { chip: flashChip.toLowerCase(), pin_assignments: pinAssignments })
      .then((res) => {
        const allocations: PinAllocation[] = [
          ...Object.entries(res.pin_map || {}).map(([pin, info]: [string, any]) => ({
            pin,
            mode: info?.function || info?.mode || "",
            source: info?.source || "code",
            status: ("safe" as const),
          })),
          ...res.warnings.map(pinWarningToAllocation),
          ...res.conflicts.map(pinWarningToAllocation),
        ];
        // Deduplicate by pin
        const seen = new Map<string, PinAllocation>();
        for (const a of allocations) {
          const existing = seen.get(a.pin);
          if (!existing || existing.status === "safe") {
            seen.set(a.pin, a);
          }
        }
        setPinAllocations(Array.from(seen.values()));

        const conflicts: StrappingConflict[] = [
          ...res.conflicts.map(pinWarningToStrappingConflict),
          ...res.warnings.map(pinWarningToStrappingConflict),
        ];
        setStrappingConflicts(conflicts);
        setVerified(true);
        setChecking(false);
        useLogStore.getState().log("ok", "safety", `审计完成: ${conflicts.length} 冲突, ${res.warnings.length} 警告`);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        useLogStore.getState().log("error", "safety", `引脚审计失败: ${msg}`);
        setPinAllocations([]);
        setStrappingConflicts([]);
        setVerified(false);
        setChecking(false);
      });
  }, [checking, previewTabs, activePreviewTabId, flashChip]);

  const hasConflicts = strappingConflicts.length > 0;
  const hasWarnings = pinAllocations.some((p) => p.status === "warning" || p.status === "conflict");
  const allClear = !hasConflicts && !hasWarnings;

  const pinStatusColor = (status: PinAllocation["status"]) => {
    if (status === "safe") return "#3fb950";
    if (status === "warning") return "#d29922";
    return "#f85149";
  };

  const pinStatusLabel = (status: PinAllocation["status"]) => {
    if (status === "safe") return t('safe');
    if (status === "warning") return t('warning');
    return t('danger');
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--warn)" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
        <span style={{ fontSize: 12, fontWeight: 500 }}>{t('safetyTitle')}</span>
        <button style={{ marginLeft: 'auto', fontSize: 11, padding: '3px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--card)', cursor: 'pointer', color: 'var(--fg)' }} onClick={handleVerify} disabled={checking}>{checking ? t('safetyVerifying') : t('safetyVerify')}</button>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: '8px 12px' }}>
        {!verified && !checking && (
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100%', color: 'var(--muted-fg)', gap: 8 }}>
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            <div style={{ fontSize: 12 }}>{t('safetyDesc')}</div>
          </div>
        )}
        {checking && (
          <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: 'var(--muted-fg)', fontSize: 12 }}>
            {t('verifyingPin')}
          </div>
        )}
        {verified && allClear && (
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100%', gap: 8 }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#3fb950" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
              <path d="M9 12l2 2 4-4" stroke="#3fb950" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#3fb950" }}>{t('noSafetyIssues')}</div>
          </div>
        )}
        {verified && !allClear && (
          <>
            {/* Pin allocation table */}
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--fg)", marginBottom: 6 }}>{t('pinAllocation')}</div>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Pin</th>
                    <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Mode</th>
                    <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Source</th>
                    <th style={{ textAlign: "left", padding: "3px 8px", color: "var(--muted-fg)", fontWeight: 500 }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {pinAllocations.map((p) => (
                    <tr key={p.pin} style={{ borderBottom: "1px solid var(--border, #1c2333)" }}>
                      <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{p.pin}</td>
                      <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{p.mode}</td>
                      <td style={{ padding: "3px 8px", color: "var(--fg)" }}>{p.source}</td>
                      <td style={{ padding: "3px 8px", color: pinStatusColor(p.status), fontWeight: 500 }}>{pinStatusLabel(p.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Strapping pin conflict details */}
            {strappingConflicts.length > 0 && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: "#d29922", marginBottom: 6, display: "flex", alignItems: "center", gap: 4 }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#d29922" strokeWidth="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                  {t('strappingConflict')}
                </div>
                {strappingConflicts.map((c) => (
                  <div key={c.pin} style={{
                    background: "rgba(210, 153, 34, 0.08)",
                    border: "1px solid rgba(210, 153, 34, 0.25)",
                    borderRadius: 6,
                    padding: "8px 10px",
                    marginBottom: 6,
                    fontSize: 11,
                  }}>
                    <div style={{ fontWeight: 600, color: "var(--fg)", marginBottom: 4 }}>
                      {c.pin} — {c.conflictType}
                    </div>
                    <div style={{ color: "var(--muted-fg)", marginBottom: 4 }}>{c.description}</div>
                    <div style={{ color: "#3fb950" }}>
                      <span style={{ fontWeight: 500 }}>{t('suggestion')}：</span>{c.suggestion}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
