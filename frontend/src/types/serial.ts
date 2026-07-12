export interface SerialDevice {
  port: string;
  description: string;
  vid?: number | null;
  pid?: number | null;
  manufacturer?: string | null;
  serial_number?: string | null;
}

export interface SerialState {
  connected: boolean;
  port: string;
  baudRate: number;
  log: string[];
}
