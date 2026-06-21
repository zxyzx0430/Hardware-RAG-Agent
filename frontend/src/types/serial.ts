export interface SerialDevice {
  port: string;
  description: string;
}

export interface SerialState {
  connected: boolean;
  port: string;
  baudRate: number;
  log: string[];
}
