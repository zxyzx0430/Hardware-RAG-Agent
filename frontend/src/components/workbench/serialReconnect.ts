export interface SerialReconnectTarget {
  port: string;
  baudRate: number;
}

export function getFlashReconnectTarget(
  serial: { connected: boolean; port: string; baudRate: number },
  uploadPort: string,
): SerialReconnectTarget | null {
  if (!serial.connected || !serial.port || serial.port !== uploadPort) return null;
  return { port: serial.port, baudRate: serial.baudRate };
}
