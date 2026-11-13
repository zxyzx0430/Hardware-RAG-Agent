import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { BookmarkPanel } from "./BookmarkPanel";
import { useBookmarkStore } from "../../stores/useBookmarkStore";
import { useAppStore } from "../../stores/useAppStore";

type Entry = ReturnType<typeof useBookmarkStore.getState>["bookmarkData"][string];

const NOW = Date.UTC(2026, 10, 12, 8, 0, 0);

function bookmark(overrides: Partial<Entry>): Entry {
  return {
    folderId: "default",
    bookmarkedAt: NOW,
    sessionId: "s-1",
    sessionTitle: "未命名对话",
    content: "",
    role: "assistant",
    ...overrides,
  };
}

function cardTitles(): string[] {
  return Array.from(document.querySelectorAll(".bm-card-title")).map((el) => el.textContent ?? "");
}

function railButton(name: RegExp): HTMLElement {
  const rail = screen.getByRole("complementary", { name: "收藏夹" });
  const match = within(rail)
    .getAllByRole("button", { name })
    .find((button) => button.classList.contains("bm-rail-item"));
  if (!match) throw new Error(`rail button not found: ${name}`);
  return match;
}

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({ lang: "zh" });
  useBookmarkStore.setState({
    bookmarkFolders: [
      { id: "default", name: "默认收藏夹", createdAt: 1 },
      { id: "f-stm32", name: "STM32 调试", createdAt: 2 },
      { id: "f-esp32", name: "ESP32 笔记", createdAt: 3 },
    ],
    bookmarkData: {
      m1: bookmark({ folderId: "f-stm32", sessionTitle: "STM32 时钟树怎么配置", content: "APB1 最高 36 MHz，AHB 最高 72 MHz" }),
      m2: bookmark({ folderId: "f-esp32", sessionTitle: "ESP32-S3 GPIO 数量", content: "45 个可编程 GPIO" }),
      m3: bookmark({ folderId: "default", sessionTitle: "FreeRTOS 任务优先级", content: "抢占式调度与时间片轮转" }),
    },
    bookmarkTargetMsgId: null,
  });
});

afterEach(cleanup);

describe("BookmarkPanel", () => {
  it("filters by session title, preview content and folder name, case-insensitively", () => {
    render(<BookmarkPanel />);
    const search = screen.getByRole("textbox", { name: "搜索收藏内容…" });

    fireEvent.change(search, { target: { value: "时钟树" } });
    expect(cardTitles()).toEqual(["STM32 时钟树怎么配置"]);

    fireEvent.change(search, { target: { value: "抢占式" } });
    expect(cardTitles()).toEqual(["FreeRTOS 任务优先级"]);

    fireEvent.change(search, { target: { value: "gpio" } });
    expect(cardTitles()).toEqual(["ESP32-S3 GPIO 数量"]);

    fireEvent.change(search, { target: { value: "笔记" } });
    expect(cardTitles()).toEqual(["ESP32-S3 GPIO 数量"]);

    fireEvent.change(search, { target: { value: "不存在的关键词" } });
    expect(cardTitles()).toEqual([]);
    expect(screen.getByText("没有匹配的收藏")).toBeTruthy();
  });

  it("combines search with the selected folder in the rail", () => {
    render(<BookmarkPanel />);

    fireEvent.click(railButton(/STM32 调试/));
    expect(cardTitles()).toEqual(["STM32 时钟树怎么配置"]);

    fireEvent.change(screen.getByRole("textbox", { name: "搜索收藏内容…" }), { target: { value: "gpio" } });
    expect(cardTitles()).toEqual([]);
    expect(screen.getByText("没有匹配的收藏")).toBeTruthy();
  });

  it("filters cards by folder rail selection and restores all bookmarks", () => {
    render(<BookmarkPanel />);
    expect(cardTitles()).toHaveLength(3);

    fireEvent.click(railButton(/STM32 调试/));
    expect(cardTitles()).toEqual(["STM32 时钟树怎么配置"]);
    expect(railButton(/STM32 调试/).getAttribute("aria-current")).toBe("true");

    fireEvent.click(railButton(/全部收藏/));
    expect(cardTitles()).toHaveLength(3);
    expect(railButton(/全部收藏/).getAttribute("aria-current")).toBe("true");
  });

  it("creates a folder from the toolbar, selects it, and lets it receive a bookmark", () => {
    render(<BookmarkPanel />);
    const main = screen.getByRole("main");

    fireEvent.click(within(main).getByRole("button", { name: "新建收藏夹" }));
    fireEvent.change(screen.getByPlaceholderText("文件夹名称"), { target: { value: "电源设计" } });
    fireEvent.keyDown(screen.getByPlaceholderText("文件夹名称"), { key: "Enter" });

    const created = useBookmarkStore.getState().bookmarkFolders.find((f) => f.name === "电源设计");
    expect(created).toBeTruthy();
    expect(railButton(/电源设计/).getAttribute("aria-current")).toBe("true");
    expect(screen.getByRole("heading", { name: "电源设计" })).toBeTruthy();
    expect(screen.getByText("这个收藏夹还是空的")).toBeTruthy();

    act(() => useBookmarkStore.getState().setBookmarkTargetMsgId("msg-new"));
    const dialog = screen.getByRole("dialog", { name: "选择收藏夹" });
    fireEvent.click(within(dialog).getByRole("button", { name: "新建" }));
    const nameInput = within(dialog).getByPlaceholderText("文件夹名称");
    fireEvent.change(nameInput, { target: { value: "资料" } });
    fireEvent.keyDown(nameInput, { key: "Enter" });

    expect(screen.queryByRole("dialog")).toBeNull();
    const target = useBookmarkStore.getState().bookmarkFolders.find((f) => f.name === "资料");
    expect(target).toBeTruthy();
    expect(railButton(/资料/).getAttribute("aria-current")).toBe("true");
    expect(useBookmarkStore.getState().bookmarkData["msg-new"].folderId).toBe(target!.id);

    const card = document.querySelector(".bm-card") as HTMLElement;
    expect(card).toBeTruthy();
    expect(within(card).getByText("未命名对话")).toBeTruthy();
    expect(within(card).getByText("资料")).toBeTruthy();
  });

  it("moves a bookmark to another folder from the card action", () => {
    render(<BookmarkPanel />);
    const card = document.querySelector('[data-bookmark-id="m3"]') as HTMLElement;

    fireEvent.click(within(card).getByRole("button", { name: "移至" }));
    const dialog = screen.getByRole("dialog", { name: "移动到文件夹" });
    fireEvent.click(within(dialog).getByRole("button", { name: "ESP32 笔记" }));

    expect(useBookmarkStore.getState().bookmarkData.m3.folderId).toBe("f-esp32");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(cardTitles().sort()).toEqual(["ESP32-S3 GPIO 数量", "FreeRTOS 任务优先级"]);
  });
});
