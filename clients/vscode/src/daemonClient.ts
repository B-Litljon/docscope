import WebSocket from "ws";

export type IncomingMessage =
  | { type: "card"; card: DocCardJson; markdown: string }
  | { type: "error"; error: { reason: string; detail?: string } }
  | { type: "status"; message: string }
  | { type: "pong" };

export interface DocCardJson {
  symbol: string;
  package: string;
  version: string;
  source_url: string;
  exact_version: boolean;
  cache_tier: string;
  elapsed_ms: number;
}

export type ConnectionState = "connecting" | "connected" | "disconnected";

/**
 * Thin WebSocket client for the docscope daemon with automatic reconnect.
 * The extension holds exactly one of these for the workspace.
 */
export class DaemonClient {
  private ws: WebSocket | undefined;
  private reconnectTimer: NodeJS.Timeout | undefined;
  private backoffMs = 500;
  private disposed = false;

  onMessage: (m: IncomingMessage) => void = () => {};
  onState: (s: ConnectionState) => void = () => {};

  constructor(private url: string) {}

  connect(): void {
    if (this.disposed) {
      return;
    }
    this.clearReconnect();
    this.onState("connecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.on("open", () => {
      this.backoffMs = 500;
      this.onState("connected");
    });
    ws.on("message", (data: WebSocket.RawData) => {
      try {
        this.onMessage(JSON.parse(data.toString()) as IncomingMessage);
      } catch {
        /* ignore malformed frames */
      }
    });
    ws.on("close", () => {
      this.onState("disconnected");
      this.scheduleReconnect();
    });
    ws.on("error", () => {
      // 'close' fires after 'error'; reconnect is handled there.
    });
  }

  private scheduleReconnect(): void {
    if (this.disposed || this.reconnectTimer) {
      return;
    }
    const delay = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, 10_000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      this.connect();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
    }
  }

  send(message: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  setUrl(url: string): void {
    if (url === this.url) {
      return;
    }
    this.url = url;
    this.reconnect();
  }

  reconnect(): void {
    this.backoffMs = 500;
    this.ws?.close();
    this.connect();
  }

  dispose(): void {
    this.disposed = true;
    this.clearReconnect();
    this.ws?.close();
  }
}
