import { describe, expect, it } from "vitest";
import { getFlashReconnectTarget } from "./serialReconnect";

describe("getFlashReconnectTarget", () => {
  it("restores the same active port and baud rate", () => {
    expect(
      getFlashReconnectTarget(
        { connected: true, port: "COM4", baudRate: 57600 },
        "COM4",
      ),
    ).toEqual({ port: "COM4", baudRate: 57600 });
  });

  it("does not open monitoring if it was not connected before flashing", () => {
    expect(
      getFlashReconnectTarget(
        { connected: false, port: "COM4", baudRate: 115200 },
        "COM4",
      ),
    ).toBeNull();
  });

  it("does not reconnect a monitor on a different port", () => {
    expect(
      getFlashReconnectTarget(
        { connected: true, port: "COM3", baudRate: 115200 },
        "COM4",
      ),
    ).toBeNull();
  });
});
