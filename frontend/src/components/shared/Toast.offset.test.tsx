import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useToastStore } from "../../stores/useToastStore";
import { ToastContainer } from "./Toast";

afterEach(() => {
  cleanup();
  useToastStore.setState({ toasts: [] });
});

describe("ToastContainer offset", () => {
  it("reserves the right edge through the Explorer avoidance variable", () => {
    useToastStore.setState({
      toasts: [{ id: "t1", type: "info", message: "hello", duration: 0 }],
    });

    render(<ToastContainer />);

    const container = screen.getByTestId("toast-container");
    expect(container.style.right).toContain("16px");
    expect(container.style.right).toContain("var(--toast-right-offset");
  });
});
