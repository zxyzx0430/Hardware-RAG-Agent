import { beforeEach, describe, expect, it } from "vitest";
import { useAppStore } from "../useAppStore";
import {
  RIGHT_CONTENT_TAB_KEY,
  loadRightContentTabBySession,
} from "./persistence";

describe("per-session right content tab", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ rightContentTabBySession: {} });
  });

  it("remembers the selected tab per session and persists it for refresh", () => {
    const store = useAppStore.getState();
    store.setRightContentTab("session-a", "plan");
    store.setRightContentTab("session-b", "todos");

    expect(useAppStore.getState().rightContentTabBySession).toEqual({
      "session-a": "plan",
      "session-b": "todos",
    });
    expect(JSON.parse(localStorage.getItem(RIGHT_CONTENT_TAB_KEY)!)).toEqual({
      "session-a": "plan",
      "session-b": "todos",
    });
    // A fresh page load restores the same per-session choices.
    expect(loadRightContentTabBySession()).toEqual({
      "session-a": "plan",
      "session-b": "todos",
    });
    expect(useAppStore.getState().rightContentTabBySession["session-a"]).toBe("plan");
    expect(useAppStore.getState().rightContentTabBySession["session-c"]).toBeUndefined();
  });

  it("ignores unknown tab values when restoring", () => {
    localStorage.setItem(RIGHT_CONTENT_TAB_KEY, JSON.stringify({
      "session-a": "plan",
      "session-b": "not-a-tab",
      "session-c": 42,
    }));

    expect(loadRightContentTabBySession()).toEqual({ "session-a": "plan" });
  });

  it("ignores an empty session id", () => {
    useAppStore.getState().setRightContentTab("", "plan");
    expect(useAppStore.getState().rightContentTabBySession).toEqual({});
  });
});
