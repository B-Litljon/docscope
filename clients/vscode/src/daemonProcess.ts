import { ChildProcess, spawn } from "child_process";
import * as vscode from "vscode";

export type DaemonState = "starting" | "running" | "failed" | "not-managed" | "crashed";

const POLL_INTERVAL_MS = 300;
const HEALTH_TIMEOUT_MS = 1000;

/**
 * Spawns and supervises the docscope daemon (`uv run docscope serve`) so the
 * developer never has to start it by hand. If a daemon is already running
 * (started manually in a terminal), this manager leaves it alone entirely —
 * it only owns processes it spawned itself.
 */
export class DaemonProcessManager {
  private child: ChildProcess | undefined;
  private _state: DaemonState = "starting";

  onStateChange: (state: DaemonState) => void = () => {};

  constructor(
    private readonly healthUrl: string,
    private readonly daemonDir: string,
    private readonly output: vscode.OutputChannel
  ) {}

  get state(): DaemonState {
    return this._state;
  }

  private setState(state: DaemonState): void {
    this._state = state;
    this.onStateChange(state);
  }

  /** Health-check first; spawn only if nothing is listening and autoStart allows it. */
  async ensureRunning(autoStart: boolean): Promise<void> {
    if (await this.isHealthy()) {
      this.setState("not-managed");
      return;
    }
    if (!autoStart) {
      this.setState("failed");
      return;
    }
    this.setState("starting");
    this.spawnDaemon();
    const ready = await this.pollHealth(10_000);
    if (this.child) {
      // Only overwrite state if the process didn't already report crashed/failed.
      this.setState(ready ? "running" : "failed");
    }
  }

  async restart(): Promise<void> {
    this.killOwnedChild();
    await this.ensureRunning(true);
  }

  /** Kill the daemon only if this manager spawned it. Safe to call on deactivate. */
  dispose(): void {
    this.killOwnedChild();
  }

  private killOwnedChild(): void {
    if (this.child) {
      this.child.kill();
      this.child = undefined;
    }
  }

  private spawnDaemon(): void {
    this.output.appendLine(`Starting docscope daemon: uv run docscope serve (cwd ${this.daemonDir})`);
    const child = spawn("uv", ["run", "docscope", "serve"], {
      cwd: this.daemonDir,
      shell: process.platform === "win32",
    });
    this.child = child;

    child.stdout?.on("data", (data: Buffer) => this.output.append(data.toString()));
    child.stderr?.on("data", (data: Buffer) => this.output.append(data.toString()));

    child.on("error", (err) => {
      this.output.appendLine(`Failed to start docscope daemon: ${err.message}`);
      this.child = undefined;
      this.setState("failed");
    });

    child.on("exit", (code) => {
      this.output.appendLine(`docscope daemon exited (code ${code ?? "unknown"})`);
      this.child = undefined;
      // Only "crashed" if it had previously come up — otherwise ensureRunning's
      // own poll-timeout path already reports "failed".
      if (this._state === "running") {
        this.setState("crashed");
      }
    });
  }

  private async isHealthy(): Promise<boolean> {
    try {
      const res = await fetch(this.healthUrl, { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS) });
      return res.ok;
    } catch {
      return false;
    }
  }

  private async pollHealth(timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.isHealthy()) {
        return true;
      }
      if (!this.child) {
        // Process already died (see 'error'/'exit' handlers) — stop polling.
        return false;
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
    return false;
  }
}

/** Derive the daemon's HTTP health URL from its WebSocket URL setting. */
export function healthUrlFrom(daemonWsUrl: string): string {
  return daemonWsUrl.replace(/^ws/, "http").replace(/\/ws$/, "/health");
}
