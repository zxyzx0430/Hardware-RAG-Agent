import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OpenFileItem } from "../../types";
import { ImagePreview } from "./editorTabParts";

const { apiPostMock, toastMock } = vi.hoisted(() => ({
  apiPostMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({ apiPost: apiPostMock }));
vi.mock("../../stores/useToastStore", () => ({
  useToastStore: { getState: () => ({ showError: toastMock }) },
}));
vi.mock("../../i18n", () => ({
  useI18n: () => ({
    t: (_key: string, fallback?: string) => fallback ?? _key,
    lang: "zh",
  }),
}));

function binaryFile(overrides: Partial<OpenFileItem> = {}): OpenFileItem {
  return {
    id: "E:/project/firmware.bin",
    path: "E:/project/firmware.bin",
    name: "firmware.bin",
    is_text: false,
    size: 1536,
    ...overrides,
  };
}

describe("ImagePreview for unsupported binary files", () => {
  beforeEach(() => {
    apiPostMock.mockReset();
    toastMock.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows file size and mime type with a reveal entry", () => {
    render(<ImagePreview file={binaryFile()} />);

    expect(screen.getByText("此文件类型不支持预览")).toBeTruthy();
    expect(screen.getByText(/application\/octet-stream/)).toBeTruthy();
    expect(screen.getByText(/1\.5 KB/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "在文件管理器中显示" })).toBeTruthy();
  });

  it("reveals the file through the explorer API", async () => {
    apiPostMock.mockResolvedValue({});
    render(<ImagePreview file={binaryFile()} />);

    fireEvent.click(screen.getByRole("button", { name: "在文件管理器中显示" }));

    await waitFor(() =>
      expect(apiPostMock).toHaveBeenCalledWith("explorer/reveal", {
        path: "E:/project/firmware.bin",
      }),
    );
  });

  it("reports a reveal failure as a toast", async () => {
    apiPostMock.mockRejectedValue(new Error("文件不存在"));
    render(<ImagePreview file={binaryFile()} />);

    fireEvent.click(screen.getByRole("button", { name: "在文件管理器中显示" }));

    await waitFor(() => expect(toastMock).toHaveBeenCalledTimes(1));
    expect(String(toastMock.mock.calls[0][0])).toContain("文件不存在");
  });

  it("keeps the image preview unchanged when a data URL is available", () => {
    render(
      <ImagePreview
        file={binaryFile({
          name: "logo.png",
          data_url: "data:image/png;base64,AAAA",
          size: 2048,
        })}
      />,
    );

    expect(screen.getByAltText("logo.png")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("ImagePreview file size formatting", () => {
  afterEach(() => {
    cleanup();
  });

  it("formats byte, megabyte and gigabyte values", () => {
    const cases: Array<[number | undefined, string | null]> = [
      [undefined, null],
      [0, "0 B"],
      [2.5 * 1024 * 1024, "2.5 MB"],
      [3 * 1024 * 1024 * 1024, "3.0 GB"],
    ];
    for (const [size, expected] of cases) {
      const { unmount } = render(<ImagePreview file={binaryFile({ size })} />);
      if (expected) {
        expect(screen.getByText(new RegExp(`大小：${expected.replace(".", "\\.")}`))).toBeTruthy();
      } else {
        expect(screen.queryByText(/大小：/)).toBeNull();
      }
      unmount();
    }
  });
});
