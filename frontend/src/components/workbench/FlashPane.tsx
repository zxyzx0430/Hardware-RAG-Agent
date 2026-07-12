import { useEffect, useState, useCallback, useRef } from "react";
import MonacoEditor from "@monaco-editor/react";
import { useAppStore } from "../../stores/useAppStore";
import { useSerialStore } from "../../stores/useSerialStore";
import { useLogStore } from "../../stores/useLogStore";
import { useWorkbenchBridge } from "../../stores/useWorkbenchBridge";
import { useI18n } from "../../i18n";
import { apiGet, apiSSE } from "../../api/client";
import type { BuildSSEEvent, BuildDoneSSEEvent } from "../../types/api";
import type { SerialDevice } from "../../types/serial";
import { CODE } from "./workbenchConstants";
import { useAppliedDarkMode } from "../shared/MarkdownRenderer";

const THINKING_PREFIX = "› ";
const PROGRESS_PREFIX = "› ";

export function FlashPane() {
  const { t } = useI18n();
  const { flashCode, flashPlatform: selectedPlatform, flashBoard: selectedBoard, themeMode, setFlashPlatform: setSelectedPlatform, setFlashBoard: setSelectedBoard } = useAppStore();
  const isDark = useAppliedDarkMode();
  const editorTheme = themeMode === "dark" || (themeMode === "auto" && isDark) ? "vs-dark" : "vs-light";
  const flashRenderData = useWorkbenchBridge((s) => s.flashRenderData);
  const flashLiveLog = useWorkbenchBridge((s) => s.flashLiveLog);
  const flashProgress = useWorkbenchBridge((s) => s.flashProgress);
  const [flashLog, setFlashLog] = useState<string[]>([t('idle')]);
  const liveLogLenRef = useRef(0);
  const [compiling, setCompiling] = useState(false);
  const [flashing, setFlashing] = useState(false);
  const [selectedPort, setSelectedPort] = useState("");
  const [devices, setDevices] = useState<SerialDevice[]>([]);
  const [optionsText, setOptionsText] = useState("");
  const [progress, setProgress] = useState(0);
  const [showProgress, setShowProgress] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const compileControllerRef = useRef<AbortController | null>(null);
  const progressHideTimerRef = useRef<number | null>(null);

  // 组件卸载时中止正在进行的编译/烧录请求，避免后台资源浪费
  useEffect(() => {
    return () => {
      compileControllerRef.current?.abort();
      if (progressHideTimerRef.current !== null) window.clearTimeout(progressHideTimerRef.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiGet<{ devices: SerialDevice[] }>("devices");
        if (!cancelled) {
          setDevices(data?.devices ?? []);
          if (data?.devices?.length) {
            setSelectedPort(data.devices[0].port);
            useLogStore.getState().log("ok", "flash", `扫描到 ${data.devices.length} 个烧录端口`);
          }
        }
      } catch {
        if (!cancelled) {
          setDevices([]);
          useLogStore.getState().log("error", "flash", "烧录端口扫描失败");
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Auto-scroll unified log to bottom on new entries.
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [flashLog]);

  // Subscribe to Agent tool flash render_data (dispatched by useWorkbenchBridge).
  // Note: tab auto-switch is handled by dispatchFlashResult → autoSwitchPane in
  // the bridge, so we only fill compile log + progress here.
  useEffect(() => {
    if (!flashRenderData) return;
    const stageLabel = flashRenderData.stage === "compile" ? "编译" : "烧录";
    const resultLabel = flashRenderData.success ? "完成" : "失败";
    const portInfo = flashRenderData.port ? ` → ${flashRenderData.port}` : "";
    const binaryInfo = flashRenderData.binary_path ? ` → ${flashRenderData.binary_path}` : "";
    const detail = flashRenderData.stage === "flash" ? portInfo : binaryInfo;
    setFlashLog((prev) => [...prev, `[Agent] ${stageLabel}${resultLabel}${detail}`]);
    useLogStore.getState().log(
      flashRenderData.success ? "ok" : "error",
      "flash",
      `[Agent] ${stageLabel}${resultLabel}${detail}`,
    );
    if (flashRenderData.success) {
      setProgress(100);
      setFlashLog((prev) => [...prev, `[Agent] ${stageLabel}完成${detail}`]);
    } else {
      const errMsg = flashRenderData.error?.message || "未知错误";
      setFlashLog((prev) => [...prev, `[Agent] ${stageLabel}失败: ${errMsg}`]);
    }
  }, [flashRenderData]);

  // Append real-time compile_log / progress events from Agent tools to the log.
  useEffect(() => {
    const newLen = flashLiveLog.length;
    if (newLen > liveLogLenRef.current) {
      const newLines = flashLiveLog.slice(liveLogLenRef.current);
      setFlashLog((prev) => [...prev, ...newLines]);
      liveLogLenRef.current = newLen;
    } else if (newLen < liveLogLenRef.current) {
      // Live log was cleared by a new build/flash tool_call.
      liveLogLenRef.current = 0;
    }
  }, [flashLiveLog]);

  // Sync real-time progress from Agent tools into the progress bar.
  useEffect(() => {
    setProgress(flashProgress);
  }, [flashProgress]);

  const handleBuildDone = useCallback((e: BuildDoneSSEEvent, mode: "build" | "flash") => {
    if (e.success) {
      const msg = e.binary_path
        ? `✓ ${t('compileSuccess')} (${e.binary_path})`
        : `✓ ${t('flashComplete')}`;
      useLogStore.getState().log("ok", "flash", mode === "build" ? "编译成功" : "烧录完成");
      setFlashLog((prev) => [...prev, msg]);
      setProgress(100);
      // After a successful flash, switch to SerialPane and auto-connect same port
      if (mode === "flash" && selectedPort) {
        useAppStore.getState().setWbTab("serial");
        useSerialStore.getState().setPort(selectedPort);
        useSerialStore.getState().setAutoConnectPort(selectedPort);
      }
      // Keep the progress bar visible at 100% for 3 seconds after completion
      setShowProgress(true);
      if (progressHideTimerRef.current !== null) window.clearTimeout(progressHideTimerRef.current);
      progressHideTimerRef.current = window.setTimeout(() => {
        setShowProgress(false);
        progressHideTimerRef.current = null;
      }, 3000);
    } else {
      const fallback = mode === "build" ? t('compileFail') : t('flashFail');
      const errMsg = e.errors?.join("; ") || e.error?.message || fallback;
      const errCode = e.error?.code || "";
      useLogStore.getState().log("error", "flash", `失败 [${errCode}]: ${errMsg}`);
      setFlashLog((prev) => [...prev, `✗ [${errCode}] ${errMsg}`]);
    }
    setCompiling(false);
    setFlashing(false);
  }, [t, selectedPort]);

  const handleBuildEvent = useCallback((e: BuildSSEEvent, mode: "build" | "flash") => {
    switch (e.type) {
      case "thinking":
        useLogStore.getState().log("info", "flash", e.content);
        setFlashLog((prev) => [...prev, `${THINKING_PREFIX}${e.content}`]);
        setProgress(0);
        break;
      case "progress":
        useLogStore.getState().log("debug", "flash", `进度: ${e.message ?? ""}`);
        setFlashLog((prev) => [...prev, e.message || `${PROGRESS_PREFIX}${e.percent ?? 0}%`]);
        setProgress(e.percent ?? 0);
        break;
      case "compile_log":
        setFlashLog((prev) => [...prev, e.line]);
        break;
      case "heartbeat":
        break;
      case "done":
        handleBuildDone(e, mode);
        break;
    }
  }, [handleBuildDone]);

  const handleBuildError = useCallback((err: Error, mode: "build" | "flash") => {
    const label = mode === "build" ? "编译失败" : "烧录失败";
    useLogStore.getState().log("error", "flash", `${label}: ${err.message}`);
    setFlashLog((prev) => [...prev, `✗ ${label}: ${err.message}`]);
    setCompiling(false);
    setFlashing(false);
  }, []);

  const parseOptions = useCallback((text: string): Record<string, string> => {
    const opts: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) continue;
      const eqIdx = trimmed.indexOf("=");
      if (eqIdx === -1) continue;
      const key = trimmed.slice(0, eqIdx).trim();
      const value = trimmed.slice(eqIdx + 1).trim();
      if (key) opts[key] = value;
    }
    return opts;
  }, []);

  const handleCompile = useCallback(() => {
    if (compiling || flashing) return;
    setCompiling(true);
    setFlashLog([t('compilingStatus')]);
    setProgress(0);
    const board = selectedBoard || (selectedPlatform === "espressif32" ? "esp32-s3-devkitc-1" : "black_f407vg");
    useLogStore.getState().log("info", "flash", `开始编译: ${selectedPlatform} / ${selectedBoard || "(默认)"}`);
    const controller = new AbortController();
    compileControllerRef.current = controller;
    apiSSE("build", { board, platform: selectedPlatform, code: flashCode, options: parseOptions(optionsText) }, {
      onEvent: (event) => handleBuildEvent(event as BuildSSEEvent, "build"),
      onDone: () => setCompiling(false),
      onError: (err) => handleBuildError(err, "build"),
    }, controller);
  }, [compiling, flashing, selectedPlatform, selectedBoard, flashCode, optionsText, t, handleBuildEvent, handleBuildError, parseOptions]);

  const handleFlash = useCallback(() => {
    if (compiling || flashing) return;
    setFlashing(true);
    setFlashLog([t('flashingStatus')]);
    setProgress(0);
    const board = selectedBoard || (selectedPlatform === "espressif32" ? "esp32-s3-devkitc-1" : "black_f407vg");
    useLogStore.getState().log("info", "flash", `开始烧录: ${selectedBoard || "(默认)"} → ${selectedPort}`);
    const controller = new AbortController();
    compileControllerRef.current = controller;
    apiSSE("upload", { board, platform: selectedPlatform, port: selectedPort, code: flashCode, options: parseOptions(optionsText) }, {
      onEvent: (event) => handleBuildEvent(event as BuildSSEEvent, "flash"),
      onDone: () => setFlashing(false),
      onError: (err) => handleBuildError(err, "flash"),
    }, controller);
  }, [compiling, flashing, selectedPlatform, selectedBoard, selectedPort, flashCode, optionsText, t, handleBuildEvent, handleBuildError, parseOptions]);

  const handleStop = useCallback(() => {
    compileControllerRef.current?.abort();
    setCompiling(false);
    setFlashing(false);
    useLogStore.getState().log("info", "flash", "用户中止编译/烧录");
    setFlashLog((prev) => [...prev, "■ 已中止"]);
  }, []);

  const handleRefresh = useCallback(() => {
    setFlashLog([t('idle')]);
    setProgress(0);
  }, []);

  return (
    <div className="flash-panel">
      <div className="flash-toolbar">
        <select value={selectedPort} onChange={(e) => setSelectedPort(e.target.value)}>
          {devices.length === 0
            ? <option value="" disabled>未扫描到串口设备</option>
            : devices.map((d) => {
                const label = d.manufacturer || d.description;
                const vidPid = d.vid != null && d.pid != null
                  ? ` (VID:${d.vid.toString(16).toUpperCase().padStart(4, "0")} PID:${d.pid.toString(16).toUpperCase().padStart(4, "0")})`
                  : "";
                return <option key={d.port} value={d.port}>{d.port} — {label}{vidPid}</option>;
              })
          }
        </select>
        <select value={selectedPlatform} onChange={(e) => {
          setSelectedPlatform(e.target.value);
          setSelectedBoard("");
        }}>
          <option value="espressif32">ESP32 系列</option>
          <option value="ststm32">STM32 系列</option>
        </select>
        <input
          list="board-suggestions"
          value={selectedBoard}
          onChange={(e) => setSelectedBoard(e.target.value)}
          placeholder={selectedPlatform === "espressif32" ? "esp32-s3-devkitc-1" : "black_f407vg"}
        />
        <datalist id="board-suggestions">
          {selectedPlatform === "espressif32" ? (
            <>
              <option value="esp32-s3-devkitc-1">ESP32-S3 DevKitC-1 (N16R8/N8R2 通用)</option>
              <option value="esp32-devkitc-v4">ESP32 DevKitC V4</option>
              <option value="esp32-c3-devkitm-1">ESP32-C3 DevKitM-1</option>
              <option value="esp32-s2-saola-1">ESP32-S2 Saola-1</option>
              <option value="esp32-c6-devkitc-1">ESP32-C6 DevKitC-1</option>
              <option value="esp32-h2-devkitm-1">ESP32-H2 DevKitM-1</option>
            </>
          ) : (
            <>
              <option value="black_f407vg">Black F407VG</option>
              <option value="disco_f407vg">Discovery F407VG</option>
              <option value="nucleo_f407re">Nucleo F407RE</option>
              <option value="bluepill_f103c8">Blue Pill F103C8</option>
              <option value="black_f411ve">Black F411VE</option>
              <option value="nucleo_f411re">Nucleo F411RE</option>
              <option value="genericSTM32F103C8">Generic STM32F103C8</option>
            </>
          )}
        </datalist>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          <button className="flash-btn" onClick={handleRefresh} aria-label="刷新" title="刷新"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
          <button className="flash-btn" onClick={handleCompile} disabled={compiling || flashing}>{compiling ? t('compilingStatus') : t('compileBtn')}</button>
          <button className="flash-btn" onClick={handleFlash} disabled={compiling || flashing}>{flashing ? t('flashingStatus') : t('flashBtn')}</button>
          {(compiling || flashing) && (
            <button className="flash-btn" onClick={handleStop} aria-label="停止"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="6" y="6" width="12" height="12" rx="1"/></svg> 停止</button>
          )}
        </span>
      </div>
      <div className="flash-code-wrap" style={{ flex: 1 }}>
        <MonacoEditor
          language="cpp"
          value={flashCode || CODE}
          theme={editorTheme}
          height="100%"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 12,
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            wordWrap: "on",
            tabSize: 2,
            automaticLayout: true,
          }}
        />
      </div>
      <div className="flash-bottom">
        <textarea
          className="flash-options-input"
          value={optionsText}
          onChange={(e) => setOptionsText(e.target.value)}
          placeholder={"额外 platformio.ini 配置（每行一个 key = value）\n# 示例（ESP32-S3-N16R8）:\nboard_build.arduino.memory_type = qio_opi\nboard_build.partitions = default_16MB.csv\nboard_upload.flash_size = 16MB"}
          rows={2}
          title="额外 platformio.ini 配置项，每行一个 key = value"
        />
        {(compiling || flashing || showProgress) && (
          <div className="flash-progress-bar">
            <div className="flash-progress-fill" style={{ width: `${progress}%` }} />
            <span className="flash-progress-label">{progress < 100 ? `${compiling ? '编译中' : flashing ? '烧录中' : '处理中'}… ${progress}%` : "处理完成"}</span>
          </div>
        )}
        <div className="flash-log" id="flashLog" ref={logRef}>
          {flashLog.map((line, idx) => (
            <div key={idx} className={line.startsWith("✓") ? "fl-ok" : line.startsWith("✗") ? "fl-err" : "fl-info"}>{line}</div>
          ))}
        </div>
      </div>
    </div>
  );
}
