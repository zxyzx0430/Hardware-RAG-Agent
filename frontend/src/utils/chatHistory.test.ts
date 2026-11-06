import { describe, expect, it } from "vitest";
import { shouldShowHistoryPagination } from "./chatHistory";

describe("history paging bar visibility", () => {
  it("hides the bar for a brand-new 0-message session", () => {
    expect(shouldShowHistoryPagination(0, undefined)).toBe(false);
    expect(shouldShowHistoryPagination(0, null)).toBe(false);
    expect(shouldShowHistoryPagination(0, {
      loading: false,
      pagesLoaded: 0,
      hasMore: null,
      error: null,
      persistenceError: null,
    })).toBe(false);
  });

  it("shows the bar once messages or paging state exist", () => {
    expect(shouldShowHistoryPagination(2, undefined)).toBe(true);
    expect(shouldShowHistoryPagination(0, {
      loading: true,
      pagesLoaded: 0,
      hasMore: null,
      error: null,
      persistenceError: null,
    })).toBe(true);
    expect(shouldShowHistoryPagination(0, {
      loading: false,
      pagesLoaded: 1,
      hasMore: false,
      error: null,
      persistenceError: null,
    })).toBe(true);
    expect(shouldShowHistoryPagination(0, {
      loading: false,
      pagesLoaded: 0,
      hasMore: null,
      error: "failed",
      persistenceError: null,
    })).toBe(true);
  });
});
