import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RightPanel } from "./RightPanel";
import { useAppStore } from "../../stores/useAppStore";
import { useChatStore } from "../../stores/useChatStore";
import type { Message, SourceRef } from "../../types/session";

const { fetchMock, getAuthHeadersMock } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  getAuthHeadersMock: vi.fn(() => ({ Authorization: "Bearer test-token" })),
}));

vi.mock("../../api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({}),
  apiPost: vi.fn().mockResolvedValue({}),
  apiPut: vi.fn().mockResolvedValue({}),
  apiPatch: vi.fn().mockResolvedValue({}),
  apiDelete: vi.fn().mockResolvedValue(undefined),
  apiSSE: vi.fn(),
  apiWS: vi.fn(),
  apiUploadWithProgress: vi.fn(),
  getAuthHeaders: getAuthHeadersMock,
  parseSseEventId: vi.fn(() => null),
  ApiError: class ApiError extends Error {},
  fetchBigChunk: vi.fn().mockResolvedValue({ text: "" }),
}));

vi.mock("../workbench/WorkbenchPanel", () => ({ WorkbenchPanel: () => null }));
vi.mock("../plan/PlanPanel", () => ({ PlanPanel: () => <div data-testid="plan-panel" /> }));
vi.mock("../shared/MarkdownRenderer", () => ({ useAppliedDarkMode: () => false }));
vi.mock("@monaco-editor/react", () => ({
  default: ({ value, language }: { value?: string; language?: string }) => (
    <div data-testid="monaco" data-language={language}>{value}</div>
  ),
}));

const PDF_EXCERPT =
  "The BME280 is a combined humidity, pressure and temperature sensor.\n\nPressure range: 300-1100 hPa.";
const TEXT_EXCERPT = "Pressure readings are logged every second.\nTemperature is compensated in firmware.";

const pdfSource: SourceRef = {
  id: "src1",
  title: "BME280 datasheet.pdf",
  doc: "bme280.pdf",
  doc_id: "doc-bme",
  page: 38,
  page_start: 39,
  page_end: 39,
  section_title: "Pressure sensor",
  score: 0.91,
  excerpt: PDF_EXCERPT,
  kb_name: "Datasheets",
  category: "sensor",
  messageId: "m1",
};

const textSource: SourceRef = {
  id: "src2",
  title: "bench-notes.txt",
  doc: "bench-notes.txt",
  page: 4,
  score: 0.84,
  excerpt: TEXT_EXCERPT,
  language: "text",
  messageId: "m1",
};

const codeSource: SourceRef = {
  id: "src3",
  title: "bme280_raw_test.cpp",
  doc: "bme280_raw_test.cpp",
  page: 12,
  score: 0.87,
  excerpt: "int main() { return 0; }",
  language: "cpp",
  messageId: "m1",
};

const unknownLanguageSource: SourceRef = {
  id: "src4",
  title: "platformio.toml",
  doc: "platformio.toml",
  page: 2,
  score: 0.7,
  excerpt: "[env:esp32dev]\nplatform = espressif32",
  language: "mystery-format",
  messageId: "m1",
};

const unknownCodeLanguageSource: SourceRef = {
  id: "src5",
  title: "sensor-firmware",
  doc: "sensor-firmware",
  page: 7,
  score: 0.75,
  excerpt: "const reading = bme280.readPressure();",
  language: "zig",
  messageId: "m1",
};

const pagedPdfSource: SourceRef = {
  ...pdfSource,
  id: "src6",
  title: "BME280 paged.pdf",
  doc_id: "doc-paged",
  page_start: 38,
  page_end: 40,
};

function imageResponse() {
  return {
    ok: true,
    status: 200,
    blob: async () => new Blob(["page"], { type: "image/png" }),
  };
}

function renderSource(source: SourceRef) {
  const message: Message = {
    id: "m1",
    role: "assistant",
    content: "Answer",
    timestamp: 1,
    sources: [source],
  };
  useAppStore.setState({
    lang: "zh",
    rightMode: "content",
    rightContentTabBySession: { s1: "sources" },
  });
  useChatStore.setState({
    activeSessionId: "s1",
    messages: [message],
    sessionMessages: { s1: [message] },
    isStreaming: false,
    streamingSessionId: null,
    streamingSources: [],
    streamingTodos: [],
    streamingTodosSeen: {},
    sessionTodos: { s1: [] },
    sessionTodosLoaded: { s1: true },
    sessionFileViewerSource: { s1: { messageId: "m1", sourceId: source.id } },
    sessionHighlightSourceId: {},
  });
  return render(<RightPanel />);
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(imageResponse());
  getAuthHeadersMock.mockClear();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(() => "blob:page-image"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RightPanel source viewer modes", () => {
  it("defaults PDF sources to the original page image and never mounts Monaco", async () => {
    renderSource(pdfSource);

    expect(screen.getByRole("tab", { name: "原文页" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "文字摘录" })).toBeTruthy();
    expect(screen.queryByTestId("monaco")).toBeNull();
    expect(screen.queryByText(/combined humidity/)).toBeNull();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/kb/documents/doc-bme/page-image?page=39&scale=1.5");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer test-token");

    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());
    expect(screen.getByRole("img").getAttribute("src")).toBe("blob:page-image");
  });

  it("shows a skeleton while the original page image loads", async () => {
    let resolveFetch: (value: ReturnType<typeof imageResponse>) => void = () => {};
    fetchMock.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));

    renderSource(pdfSource);

    expect(document.querySelector(".source-page-skeleton")?.textContent).toContain("正在加载原文页");
    resolveFetch(imageResponse());
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());
  });

  it("switches a PDF to the text excerpt as readable plain text", async () => {
    renderSource(pdfSource);
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    fireEvent.click(screen.getByRole("tab", { name: "文字摘录" }));

    const body = document.querySelector(".source-reading-body");
    expect(body?.textContent).toContain("combined humidity, pressure and temperature sensor");
    expect(body?.textContent).toContain("\n\n");
    expect(screen.queryByTestId("monaco")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("tab", { name: "文字摘录" }).getAttribute("aria-selected")).toBe("true");
  });

  it("renders plain text sources as body text without Monaco or view tabs", () => {
    renderSource(textSource);

    expect(screen.queryByTestId("monaco")).toBeNull();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    const body = document.querySelector(".source-reading-body");
    expect(body?.textContent).toContain("Pressure readings are logged every second.");
    expect(screen.getByText("bench-notes.txt")).toBeTruthy();
    expect(screen.getByText(/相关度|Relevance/)).toBeTruthy();
  });

  it("keeps code sources in Monaco and hides the original-page tabs", () => {
    renderSource(codeSource);

    const monaco = screen.getByTestId("monaco");
    expect(monaco.getAttribute("data-language")).toBe("cpp");
    expect(monaco.textContent).toContain("int main()");
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(document.querySelector(".source-reading-body")).toBeNull();
  });

  it("falls back to plaintext instead of cpp for an unknown language", () => {
    renderSource(unknownLanguageSource);

    const monaco = screen.getByTestId("monaco");
    expect(monaco.getAttribute("data-language")).toBe("plaintext");
    expect(monaco.getAttribute("data-language")).not.toBe("cpp");
  });

  it("keeps an unknown non-text language in Monaco even without a known extension", () => {
    renderSource(unknownCodeLanguageSource);

    const monaco = screen.getByTestId("monaco");
    expect(monaco.getAttribute("data-language")).toBe("plaintext");
    expect(monaco.textContent).toContain("bme280.readPressure");
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("offers the text excerpt when the original page image fails", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, blob: async () => new Blob([]) });

    renderSource(pdfSource);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/原文页不可用/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "查看文字摘录" }));

    const body = document.querySelector(".source-reading-body");
    expect(body?.textContent).toContain("combined humidity, pressure and temperature sensor");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("uses one compact header row with inline tabs and metadata", async () => {
    renderSource(pdfSource);
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    const header = document.querySelector(".source-fv-header");
    expect(header?.querySelector(".source-fv-back-icon")?.getAttribute("aria-label")).toBe("返回来源引用");
    expect(header?.querySelector(".source-fv-breadcrumb")?.textContent).toBe("来源引用");
    expect(header?.querySelector(".source-fv-title")?.textContent).toBe("BME280 datasheet.pdf");
    expect(header?.querySelector(".source-view-tabs")).toBeTruthy();

    const meta = document.querySelector(".source-fv-meta-compact");
    expect(meta?.textContent).toContain("Pressure sensor");
    expect(meta?.textContent).toContain("Datasheets");
    expect(meta?.textContent).toContain("第 39");
    expect(meta?.textContent).toContain("相关度 91%");
    expect(meta?.textContent).toContain("sensor");
  });

  it("zooms with the toolbar and requests a higher-resolution raster", async () => {
    renderSource(pdfSource);
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "放大" }));

    expect(screen.getByText("125%")).toBeTruthy();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.includes("page=39&scale=2"))).toBe(true);
    }, { timeout: 2000 });
  });

  it("zooms with the wheel and double-click toggles fit and last zoom", async () => {
    renderSource(pdfSource);
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    fireEvent.wheel(screen.getByRole("region"), { deltaY: -100, clientX: 10, clientY: 10 });
    expect(screen.getByText("116%")).toBeTruthy();

    fireEvent.doubleClick(screen.getByRole("img"));
    expect(screen.getByText("100%")).toBeTruthy();

    fireEvent.doubleClick(screen.getByRole("img"));
    expect(screen.getByText("116%")).toBeTruthy();
  });

  it("zooms with +/- keys and resets with the fit-width button", async () => {
    renderSource(pdfSource);
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    const region = screen.getByRole("region");
    fireEvent.keyDown(region, { key: "+" });
    expect(screen.getByText("125%")).toBeTruthy();

    fireEvent.keyDown(region, { key: "-" });
    expect(screen.getByText("100%")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByText("125%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "适应宽度" }));
    expect(screen.getByText("100%")).toBeTruthy();
  });

  it("navigates pages inside the source range and resets zoom", async () => {
    renderSource(pagedPdfSource);
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    expect(screen.getByText("38 / 40")).toBeTruthy();
    expect((screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByText("125%")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("39 / 40")).toBeTruthy();
    expect(screen.getByText("100%")).toBeTruthy();
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.includes("page=39&scale=1.5"))).toBe(true);
    });
    await waitFor(() => expect(screen.getByRole("img")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(screen.getByText("40 / 40")).toBeTruthy());
    expect((screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
