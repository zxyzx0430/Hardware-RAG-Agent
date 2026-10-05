import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { usePanelResize } from "./usePanelResize";

function ResizeHarness({
  initialWidth = 280,
  minWidth = 180,
  maxWidth = 600,
  reservedWidth = 0,
  direction = "left",
}: {
  initialWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  reservedWidth?: number;
  direction?: "left" | "right";
}) {
  const [width, setWidth] = useState(initialWidth);
  const resize = usePanelResize(
    width,
    direction,
    minWidth,
    maxWidth,
    setWidth,
    "px",
    () => reservedWidth,
  );

  return (
    <div data-testid="resize-container">
      <button type="button" onMouseDown={resize.onMouseDown}>Drag divider</button>
      <output aria-label="Panel width">{width}</output>
    </div>
  );
}

function setContainerWidth(width: number) {
  Object.defineProperty(screen.getByTestId("resize-container"), "offsetWidth", {
    configurable: true,
    value: width,
  });
}

afterEach(cleanup);

describe("usePanelResize viewport limits", () => {
  it("enforces both ends of the minimum and reserved-space maximum", () => {
    render(<ResizeHarness initialWidth={280} maxWidth={800} reservedWidth={600} />);
    setContainerWidth(1000);

    const divider = screen.getByRole("button", { name: "Drag divider" });
    fireEvent.mouseDown(divider, { clientX: 100 });
    fireEvent.mouseMove(document, { clientX: 1000 });
    expect(screen.getByLabelText("Panel width").textContent).toBe("400");
    fireEvent.mouseUp(document);

    fireEvent.mouseDown(divider, { clientX: 100 });
    fireEvent.mouseMove(document, { clientX: -1000 });
    expect(screen.getByLabelText("Panel width").textContent).toBe("180");
    fireEvent.mouseUp(document);
  });

  it("uses the container's current width during a drag after the window shrinks", () => {
    render(<ResizeHarness initialWidth={280} maxWidth={800} reservedWidth={600} />);
    setContainerWidth(1000);

    const divider = screen.getByRole("button", { name: "Drag divider" });
    fireEvent.mouseDown(divider, { clientX: 100 });
    setContainerWidth(900);
    fireEvent.mouseMove(document, { clientX: 1000 });

    expect(screen.getByLabelText("Panel width").textContent).toBe("300");
    fireEvent.mouseUp(document);
  });
});
