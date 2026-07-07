import MarkdownIt from "markdown-it";
import * as vscode from "vscode";

import { CardsViewProvider, SidebarSettings } from "./cardsView";
import { DaemonClient, IncomingMessage, ConnectionState } from "./daemonClient";
import { DaemonProcessManager, DaemonState, healthUrlFrom } from "./daemonProcess";

const md = new MarkdownIt({ html: false, linkify: true, breaks: false });

// This is a personal tool tied to one repo checkout — no settings/prompt flow needed.
const DAEMON_DIR = "/mnt/storage/mystuf/development/docscope/daemon";

// vscode languageId -> docscope language tag
const LANGUAGE_MAP: Record<string, string> = {
  python: "python",
  rust: "rust",
  javascript: "javascript",
  javascriptreact: "javascript",
  typescript: "typescript",
  typescriptreact: "typescript",
};

let cardSeq = 0;

export function activate(context: vscode.ExtensionContext): void {
  const provider = new CardsViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(CardsViewProvider.viewType, provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.show();
  context.subscriptions.push(statusBar);

  const daemonOutput = vscode.window.createOutputChannel("docscope daemon");
  context.subscriptions.push(daemonOutput);

  const daemonUrl = configString("daemonUrl", "ws://127.0.0.1:7317/ws");
  const daemonManager = new DaemonProcessManager(healthUrlFrom(daemonUrl), DAEMON_DIR, daemonOutput);

  let daemonState: DaemonState = "starting";
  let clientState: ConnectionState = "connecting";
  const renderStatus = (): void => {
    if (daemonState === "starting") {
      statusBar.text = "$(sync~spin) docscope: starting daemon";
      statusBar.tooltip = "Starting the docscope daemon...";
      statusBar.command = undefined;
      return;
    }
    if (daemonState === "failed" || daemonState === "crashed") {
      statusBar.text = daemonState === "crashed" ? "$(book) docscope $(error) crashed" : "$(book) docscope $(error)";
      statusBar.tooltip =
        daemonState === "crashed"
          ? "docscope daemon crashed — click to view log and restart"
          : "docscope daemon failed to start — click to view log and retry";
      statusBar.command = "docscope.restartDaemon";
      return;
    }
    // "running" or "not-managed": defer to the websocket connection state.
    statusBar.command = undefined;
    statusBar.text = clientState === "connected" ? "$(book) docscope" : "$(book) docscope $(warning)";
    statusBar.tooltip = "docscope documentation navigator";
  };
  renderStatus();

  daemonManager.onStateChange = (state) => {
    daemonState = state;
    renderStatus();
  };

  // Transient ambient-miss / in-flight feedback (status bar only — never a card,
  // so the sidebar stays free of noise for lookups that found nothing).
  let statusRevertTimer: NodeJS.Timeout | undefined;
  const showTransientStatus = (message: string): void => {
    statusBar.text = `$(book) docscope: ${message}`;
    statusBar.tooltip = "docscope documentation navigator";
    if (statusRevertTimer) {
      clearTimeout(statusRevertTimer);
    }
    statusRevertTimer = setTimeout(() => {
      statusRevertTimer = undefined;
      renderStatus();
    }, 4000);
  };

  const client = new DaemonClient(daemonUrl);
  client.onState = (state) => {
    clientState = state;
    provider.setState(state);
    renderStatus();
  };
  client.onMessage = (msg) => {
    if (msg.type === "status") {
      showTransientStatus(msg.message);
    } else {
      handleMessage(msg, provider);
    }
  };

  void daemonManager.ensureRunning(configBoolean("autoStart", true)).then(() => client.connect());
  context.subscriptions.push({ dispose: () => client.dispose() });
  context.subscriptions.push({ dispose: () => daemonManager.dispose() });

  provider.setSettings(currentSettings());
  provider.onUpdateSetting = (key, value) => {
    void config().update(key, value, vscode.ConfigurationTarget.Global);
  };
  provider.onAddPackageOverride = (pkg, invUrl, versioned) => {
    void addPackageOverride(configString("daemonUrl", "ws://127.0.0.1:7317/ws"), pkg, invUrl, versioned);
  };

  context.subscriptions.push(
    vscode.commands.registerCommand("docscope.restartDaemon", async () => {
      daemonOutput.show(true);
      await daemonManager.restart();
      client.reconnect();
    })
  );

  // Client-side pre-debounce on cursor movement.
  let debounceTimer: NodeJS.Timeout | undefined;
  const onMove = (editor: vscode.TextEditor | undefined) => {
    if (!editor || !isEnabled(editor.document)) {
      return;
    }
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    const delay = configNumber("clientDebounceMs", 200);
    debounceTimer = setTimeout(() => {
      const ctx = buildContext(editor);
      if (ctx) {
        client.send({ type: "context", context: ctx });
      }
    }, delay);
  };

  context.subscriptions.push(
    vscode.window.onDidChangeTextEditorSelection((e) => onMove(e.textEditor))
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("docscope.forceLookup", () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        return;
      }
      const ctx = buildContext(editor, /*force*/ true);
      if (ctx) {
        client.send({ type: "lookup", context: ctx });
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("docscope.reconnect", () => client.reconnect())
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("docscope.daemonUrl")) {
        client.setUrl(configString("daemonUrl", "ws://127.0.0.1:7317/ws"));
      }
      if (e.affectsConfiguration("docscope")) {
        provider.setSettings(currentSettings());
      }
    })
  );
}

function currentSettings(): SidebarSettings {
  return {
    enabledLanguages: configArray("enabledLanguages", Object.keys(LANGUAGE_MAP)),
    contextWindowLines: configNumber("contextWindowLines", 40),
    clientDebounceMs: configNumber("clientDebounceMs", 200),
    daemonUrl: configString("daemonUrl", "ws://127.0.0.1:7317/ws"),
  };
}

async function addPackageOverride(
  daemonUrl: string,
  pkg: string,
  invUrl: string,
  versioned: boolean
): Promise<void> {
  const base = daemonUrl.replace(/^ws/, "http").replace(/\/ws$/, "");
  try {
    const res = await fetch(`${base}/config/registry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package: pkg, inv_url: invUrl, versioned }),
    });
    if (res.ok) {
      void vscode.window.showInformationMessage(`docscope: added doc source for "${pkg}"`);
    } else {
      void vscode.window.showErrorMessage(`docscope: failed to add doc source (HTTP ${res.status})`);
    }
  } catch (err) {
    void vscode.window.showErrorMessage(
      `docscope: couldn't reach the daemon to add a doc source — ${(err as Error).message}`
    );
  }
}

export function deactivate(): void {
  /* subscriptions handle teardown */
}

function handleMessage(msg: IncomingMessage, provider: CardsViewProvider): void {
  if (msg.type === "card") {
    provider.pushCard({
      id: ++cardSeq,
      symbol: msg.card.symbol,
      version: `${msg.card.package} ${msg.card.version}`,
      exact: msg.card.exact_version,
      tier: msg.card.cache_tier,
      sourceUrl: msg.card.source_url,
      html: md.render(msg.markdown),
    });
  } else if (msg.type === "error") {
    provider.pushCard({
      id: ++cardSeq,
      symbol: msg.error.reason,
      version: "",
      exact: false,
      tier: "",
      sourceUrl: "",
      html: md.render(msg.error.detail ?? "No documentation found."),
      isError: true,
    });
  }
}

interface BufferContextPayload {
  file_path: string;
  language: string;
  text: string;
  cursor_line: number;
  cursor_col: number;
  workspace_root: string | null;
  window_start_line: number;
}

function buildContext(
  editor: vscode.TextEditor,
  force = false
): BufferContextPayload | undefined {
  const doc = editor.document;
  if (!force && !isEnabled(doc)) {
    return undefined;
  }
  const language = LANGUAGE_MAP[doc.languageId];
  if (!language) {
    return undefined;
  }
  const cursor = editor.selection.active;
  const window = configNumber("contextWindowLines", 40);
  const startLine = Math.max(0, cursor.line - window);
  const endLine = Math.min(doc.lineCount - 1, cursor.line + window);
  const range = new vscode.Range(
    new vscode.Position(startLine, 0),
    new vscode.Position(endLine, doc.lineAt(endLine).text.length)
  );
  const workspaceFolder = vscode.workspace.getWorkspaceFolder(doc.uri);
  return {
    file_path: doc.uri.fsPath,
    language,
    text: doc.getText(range),
    cursor_line: cursor.line,
    cursor_col: cursor.character,
    workspace_root: workspaceFolder ? workspaceFolder.uri.fsPath : null,
    window_start_line: startLine,
  };
}

function isEnabled(doc: vscode.TextDocument): boolean {
  const enabled = configArray("enabledLanguages", Object.keys(LANGUAGE_MAP));
  return enabled.includes(doc.languageId);
}

function config(): vscode.WorkspaceConfiguration {
  return vscode.workspace.getConfiguration("docscope");
}

function configString(key: string, fallback: string): string {
  return config().get<string>(key, fallback);
}

function configNumber(key: string, fallback: number): number {
  return config().get<number>(key, fallback);
}

function configBoolean(key: string, fallback: boolean): boolean {
  return config().get<boolean>(key, fallback);
}

function configArray(key: string, fallback: string[]): string[] {
  return config().get<string[]>(key, fallback);
}
