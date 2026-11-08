import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useAppStore } from "../../stores/useAppStore";
import { HamburgerMenu } from "./HamburgerMenu";

function themeButton(): HTMLElement {
  return screen.getByRole("button", { name: /切换深色模式|切换浅色模式|跟随系统/ });
}

describe("HamburgerMenu theme cycle", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ themeMode: "light", lang: "zh" });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("cycles light → dark → auto and labels each click with its destination", () => {
    render(<HamburgerMenu onClose={() => {}} />);

    expect(themeButton().textContent).toContain("切换深色模式");
    fireEvent.click(themeButton());
    expect(useAppStore.getState().themeMode).toBe("dark");

    expect(themeButton().textContent).toContain("跟随系统");
    fireEvent.click(themeButton());
    expect(useAppStore.getState().themeMode).toBe("auto");

    expect(themeButton().textContent).toContain("切换浅色模式");
    fireEvent.click(themeButton());
    expect(useAppStore.getState().themeMode).toBe("light");
  });
});
